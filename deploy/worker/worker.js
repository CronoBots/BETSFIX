/**
 * BETSFIX — Worker de SECOURS (failover), 2026-08-14.
 *
 * Rôle : se placer DEVANT le hostname public. Comportement :
 *   1. Essaie l'ORIGINE (le tunnel, via un 2e hostname `ORIGIN_BASE` pour ne pas boucler sur soi-même).
 *   2. Si l'origine répond (2xx/3xx/4xx) -> proxy TRANSPARENT (le site normal, PC allumé).
 *   3. Si l'origine échoue (timeout / erreur réseau / 5xx = PC éteint/reboot) -> sert le dernier
 *      SNAPSHOT depuis KV (binding SNAP), avec un bandeau « hors ligne — données de HH:MM ».
 *
 * Purement de l'AFFICHAGE de secours. N'écrit jamais rien. Les snapshots sont poussés par le PC
 * (deploy/snapshot_to_kv.py). CSS/JS étant inline dans les pages, le secours est stylé et navigable ;
 * seules les images /static manquent (dégradation propre).
 */

const PAGE_KEYS = {
  "/": "home",
  "/accueil": "accueil",
  "/stats": "stats",
  "/directs": "directs",
  "/montante": "montante",
  "/calendrier": "calendrier",
};

// PC ÉTEINT -> l'origine échoue vite (connexion refusée / 5xx), on bascule tout de suite. Ce timeout ne
// mord QUE si l'origine est UP mais lente (page lourde) : 15 s évite de servir le snapshot à tort.
const ORIGIN_TIMEOUT_MS = 15000;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1) Tenter l'origine (tunnel) avec un timeout dur.
    const originBase = (env.ORIGIN_BASE || "").replace(/\/$/, "");
    if (originBase) {
      const originUrl = originBase + url.pathname + url.search;
      try {
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), ORIGIN_TIMEOUT_MS);
        const resp = await fetch(originUrl, {
          method: request.method,
          headers: request.headers,
          body: (request.method === "GET" || request.method === "HEAD") ? undefined : request.body,
          redirect: "manual",
          signal: ctl.signal,
        });
        clearTimeout(t);
        // L'origine vit (même une 404 applicative = le PC répond) -> proxy transparent.
        if (resp.status < 500) return resp;
      } catch (e) {
        // timeout / DNS / réseau -> on bascule sur le secours KV
      }
    }

    // 2) SECOURS depuis KV (uniquement en lecture ; jamais d'écriture).
    return serveFallback(url, request, env);
  },
};

async function serveFallback(url, request, env) {
  const isFrag = url.searchParams.get("frag") === "1";
  const name = PAGE_KEYS[url.pathname];

  // Chemin non snapshoté (image /static, manifest, route inconnue) : on n'a pas de secours -> 503 léger.
  if (!name) {
    return new Response("", { status: 503, headers: { "x-betsfix-fallback": "miss" } });
  }

  const key = (isFrag ? "frag:" : "page:") + name;
  let html = await env.SNAP.get(key);
  if (html === null && isFrag) html = await env.SNAP.get("page:" + name); // repli page complète
  if (html === null) {
    return new Response(offlineStub(), {
      status: 503,
      headers: { "content-type": "text/html; charset=utf-8", "x-betsfix-fallback": "empty" },
    });
  }

  const updated = await env.SNAP.get("_meta:updated");
  html = injectBanner(html, updated);

  return new Response(html, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "x-betsfix-fallback": "kv",
      "cache-control": "no-store",
    },
  });
}

function injectBanner(html, updatedIso) {
  let when = "";
  if (updatedIso) {
    // Affiche l'heure UTC de la dernière copie (le client la lit telle quelle ; simple et fiable).
    const hhmm = String(updatedIso).slice(11, 16);
    when = hhmm ? ` — données de ${hhmm} UTC` : "";
  }
  const banner =
    '<div style="position:sticky;top:0;z-index:99999;background:#7a1f1f;color:#fff;' +
    'font:600 13px/1.4 system-ui,sans-serif;padding:8px 12px;text-align:center">' +
    "⚠️ Mode hors ligne — le serveur est momentanément indisponible" + when +
    "</div>";
  // Injecte juste après <body ...>
  return html.replace(/(<body[^>]*>)/i, "$1" + banner);
}

function offlineStub() {
  return (
    '<!doctype html><html lang="fr"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    "<title>BETSFIX — hors ligne</title></head>" +
    '<body style="background:#070708;color:#eee;font:16px/1.5 system-ui,sans-serif;' +
    'display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center;margin:0">' +
    "<div><h1 style=\"font-size:20px\">BETSFIX est momentanément indisponible</h1>" +
    "<p style=\"opacity:.7\">Le serveur redémarre. Réessaie dans un instant.</p></div>" +
    "</body></html>"
  );
}
