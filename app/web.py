"""Plateforme de visionnage (front-end HTML rendu côté serveur).

Pages mobiles cohérentes au-dessus de l'API : accueil, liste des matchs,
détail/analyse d'un match. Thème sombre, nav commune. Aucun JS requis.
"""

from __future__ import annotations

import html
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Brussels")
except Exception:  # pragma: no cover
    LOCAL_TZ = None


def to_local(value):
    """Convertit un datetime/ISO en datetime local belge (ou None)."""
    if value is None:
        return None
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    if LOCAL_TZ is not None and getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(LOCAL_TZ)
    return dt


def day_label(d, today) -> str:
    """Libellé d'un jour : Aujourd'hui / Demain / jour de semaine + date."""
    delta = (d - today).days
    if delta == 0:
        return f"Aujourd'hui — {d.strftime('%d/%m')}"
    if delta == 1:
        return f"Demain — {d.strftime('%d/%m')}"
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{jours[d.weekday()].capitalize()} {d.strftime('%d/%m')}"


def fmt_local(value, with_date: bool = True) -> str:
    """Formate un datetime/ISO en heure locale belge. '' si absent."""
    if value is None:
        return ""
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value[11:16] if len(value) >= 16 else value
    if LOCAL_TZ is not None and getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(LOCAL_TZ)
    return dt.strftime("%d/%m %H:%M" if with_date else "%H:%M")

CSS = """
  :root{
    --bg:#080a0f;--bg2:#0c0f16;--surface:#13161f;--surface2:#1a1e2a;
    --border:#252a37;--border2:#2f3545;--text:#eef1f7;--muted:#9099a8;--dim:#646c7c;
    --accent:#2ee27f;--accent2:#19c46a;--accent-ink:#04130a;
    --gold:#f6c54a;--gold-bg:#231d09;--gold-bd:#4a3c0c;
    --red:#f25d6e;--green:#34d27b;
    --radius:16px;--shadow:0 6px 22px rgba(0,0,0,.40);--shadow-sm:0 2px 8px rgba(0,0,0,.30);
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;color:var(--text);font-size:15px;line-height:1.45;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
       background:
         radial-gradient(900px 500px at 100% -10%,rgba(46,226,127,.07),transparent 60%),
         radial-gradient(700px 400px at -10% 0%,rgba(60,120,255,.05),transparent 55%),
         var(--bg);}
  a{color:inherit;text-decoration:none;-webkit-tap-highlight-color:transparent}
  .wrap{max-width:720px;margin:0 auto;padding:16px 16px 56px}
  /* Header sticky premium */
  .hdr{position:sticky;top:0;z-index:50;
       background:linear-gradient(180deg,rgba(12,15,22,.92),rgba(12,15,22,.78));
       backdrop-filter:saturate(160%) blur(14px);-webkit-backdrop-filter:saturate(160%) blur(14px);
       border-bottom:1px solid var(--border)}
  .hdr-in{max-width:720px;margin:0 auto;padding:12px 16px 10px}
  .brand{display:flex;align-items:center;gap:9px;font-size:20px;font-weight:800;
         letter-spacing:-.02em}
  .brand .logo{font-size:22px;filter:drop-shadow(0 2px 6px rgba(46,226,127,.4))}
  .brand b{color:var(--accent)}
  .brand .tag{margin-left:auto;font-size:10px;font-weight:700;letter-spacing:.12em;
              text-transform:uppercase;color:var(--dim);border:1px solid var(--border2);
              padding:3px 8px;border-radius:20px}
  .nav{display:flex;gap:7px;margin-top:11px}
  .nav a{flex:1;text-align:center;padding:10px 8px;border-radius:12px;font-size:13px;
         font-weight:700;background:var(--surface);color:var(--muted);
         border:1px solid var(--border);transition:.16s}
  .nav a:active{transform:scale(.97)}
  .nav a.on{color:var(--accent-ink);border-color:transparent;
            background:linear-gradient(180deg,var(--accent),var(--accent2));
            box-shadow:0 4px 16px rgba(46,226,127,.32)}
  h2{font-size:13px;font-weight:700;margin:26px 0 11px;color:var(--muted);
     text-transform:uppercase;letter-spacing:.07em;display:flex;align-items:center;gap:8px}
  h2:before{content:"";width:3px;height:14px;border-radius:3px;
            background:linear-gradient(var(--accent),var(--accent2))}
  /* KPI grid */
  .grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:11px;margin:14px 0}
  .card{position:relative;background:linear-gradient(180deg,var(--surface2),var(--surface));
        border:1px solid var(--border);border-radius:var(--radius);padding:14px 10px;
        text-align:center;box-shadow:var(--shadow-sm);overflow:hidden}
  .card:before{content:"";position:absolute;inset:0 0 auto 0;height:2px;
               background:linear-gradient(90deg,transparent,var(--border2),transparent)}
  .lbl{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;font-weight:700}
  .val{font-size:25px;font-weight:800;margin:5px 0;letter-spacing:-.02em;
       font-variant-numeric:tabular-nums}
  .sub{font-size:11px;color:var(--muted)}
  /* Rows / list cards */
  .row{display:block;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border-radius:var(--radius);padding:14px 15px;margin:10px 0;border:1px solid var(--border);
       box-shadow:var(--shadow-sm);transition:.16s}
  .row:active{transform:scale(.99);border-color:var(--border2)}
  .row.pick{border-color:rgba(46,226,127,.45);
            background:linear-gradient(180deg,rgba(46,226,127,.10),rgba(46,226,127,.03));
            box-shadow:0 4px 18px rgba(46,226,127,.14)}
  .live{color:var(--red);font-weight:800;letter-spacing:.02em}
  .rowtop{display:flex;justify-content:space-between;align-items:center;font-size:11px;
          color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
  .players{font-size:16px;font-weight:700;margin:7px 0 3px;letter-spacing:-.01em}
  .badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:800;
         letter-spacing:.02em}
  .b-val{background:rgba(46,226,127,.14);color:var(--accent);border:1px solid rgba(46,226,127,.25)}
  .b-dim{background:var(--surface);color:var(--muted);border:1px solid var(--border)}
  .bar{height:9px;border-radius:99px;background:rgba(242,93,110,.22);overflow:hidden;margin:8px 0}
  .bar > span{display:block;height:100%;border-radius:99px;
              background:linear-gradient(90deg,var(--accent2),var(--accent))}
  /* Tables */
  table{width:100%;border-collapse:collapse;font-size:13px;margin:4px 0;
        background:var(--surface);border:1px solid var(--border);border-radius:14px;
        overflow:hidden;box-shadow:var(--shadow-sm)}
  td{padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:top}
  tr:last-child td{border-bottom:none}
  tr:first-child td{background:rgba(255,255,255,.02);font-size:11px;text-transform:uppercase;
                    letter-spacing:.05em}
  .dim{color:var(--muted);font-size:12px}
  .pos{color:var(--green);font-weight:700}.neg{color:var(--red);font-weight:700}
  /* Banners — info discret par défaut, ambre seulement pour les vraies alertes (.warn) */
  .banner{background:var(--surface);border:1px solid var(--border);
          border-left:3px solid var(--border2);color:var(--muted);border-radius:12px;
          padding:11px 14px;font-size:12.5px;line-height:1.55;margin:11px 0}
  .banner b{color:var(--text)}
  .banner.warn{background:linear-gradient(180deg,var(--gold-bg),rgba(35,29,9,.45));
          border:1px solid var(--gold-bd);border-left:3px solid var(--gold);color:var(--gold)}
  .banner.warn b{color:#ffd877}
  /* CTA cards */
  .big{display:block;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border-radius:var(--radius);padding:18px 18px;margin:11px 0;border:1px solid var(--border);
       font-size:16px;font-weight:700;box-shadow:var(--shadow);transition:.16s}
  .big:active{transform:scale(.99)}
  .big .d{font-size:12.5px;color:var(--muted);font-weight:400;margin-top:5px;line-height:1.5}
  .foot{color:var(--dim);font-size:11px;margin-top:30px;text-align:center;line-height:1.7;
        border-top:1px solid var(--border);padding-top:18px}
  .src{font-size:12px;font-weight:600;padding:9px 13px;border-radius:12px;margin:4px 0 2px;
       border:1px solid var(--border)}
  .src.ok{background:rgba(46,226,127,.10);color:var(--accent);border-color:rgba(46,226,127,.22)}
  .src.ko{background:var(--gold-bg);color:var(--gold);border-color:var(--gold-bd)}
"""


def layout(title: str, active: str, body: str, refresh: bool = False) -> str:
    e = html.escape
    nav_items = [("home", "/", "🏠 Accueil"), ("matches", "/app", "🎾 Matchs"),
                 ("perf", "/tracking/dashboard", "📊 Perf")]
    nav = '<nav class="nav">' + "".join(
        f'<a class="{"on" if active==k else ""}" href="{href}">{e(lbl)}</a>'
        for k, href, lbl in nav_items) + "</nav>"
    meta_refresh = '<meta http-equiv="refresh" content="180">' if refresh else ""
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#080a0f">
{meta_refresh}<title>{e(title)} · BetsFix</title><style>{CSS}</style></head><body>
<header class="hdr"><div class="hdr-in">
<div class="brand"><span class="logo">🎾</span> Bets<b>Fix</b><span class="tag">Tennis</span></div>
{nav}</div></header><div class="wrap">{body}
<div class="foot">Données SofaScore + Unibet BE · informatif, sans garantie · jouez responsable</div>
</div></body></html>"""


def render_home(rep: dict, source: dict | None = None,
                picks: list[dict] | None = None) -> str:
    e = html.escape
    prec = rep.get("precision_modele")
    prec_txt = "—" if prec is None else f"{round(prec*100)}%"
    if source and source.get("ok"):
        src = '<div class="src ok">🟢 Source : SofaScore OK</div>'
    elif source:
        src = (f'<div class="src ko">🟠 SofaScore en pause '
               f'({source.get("paused_seconds", 0)}s) — LiveScore prend le relais</div>')
    else:
        src = ""

    # 💰 Paris à venir : les value détectées, classées par edge
    picks = picks or []
    if picks:
        rows = "".join(
            f'<a class="row pick" href="/app/match/{v["id"]}?tour={v["tour"]}">'
            f'<div class="rowtop"><span>{e(v["tour"].upper())} · {e(v.get("time") or "")}</span>'
            f'<span class="badge b-val">+{round((v.get("edge") or 0)*100, 1)} pts</span></div>'
            f'<div class="players">{e(v.get("player") or "")} '
            f'<span class="dim">@{v.get("odds") or "—"}</span></div>'
            f'<div class="dim">{e(v["home"])} vs {e(v["away"])} · mise '
            f'{v.get("stake") if v.get("stake") is not None else "—"}%</div></a>'
            for v in picks)
        picks_html = (f'<h2>💰 Paris à venir ({len(picks)})</h2>'
                      '<div class="banner">Les "value" du modèle vs Unibet, classées par edge. '
                      'À recouper — un pari n\'est jamais garanti.</div>' + rows)
    else:
        picks_html = ('<h2>💰 Paris à venir</h2>'
                      '<div class="dim">Aucune value détectée pour le moment.</div>')

    body = f"""{src}{picks_html}"""
    body += f"""
<a class="big" href="/app">🎾 Matchs & analyses
  <div class="d">Matchs du jour : favori du modèle, stats, forme, h2h et cotes Unibet</div></a>
<a class="big" href="/tracking/dashboard">📊 Fiabilité du modèle
  <div class="d">Précision : {e(prec_txt)} sur {rep.get('predictions_evaluees',0)} matchs réglés</div></a>
<a class="big" href="/docs">🛠️ API & documentation
  <div class="d">Tous les endpoints (matchs, stats, joueurs, cotes…)</div></a>
<div class="banner warn">Outil d'<b>aide à la décision</b> : il t'aide à analyser, il ne prédit pas
 de paris gagnants. Un modèle simple ne bat pas un bookmaker sérieux — sers-t'en pour
 t'informer, décide toi-même, et joue responsable.</div>"""
    return layout("Accueil", "home", body, refresh=True)


def _bar(pct: float | None) -> str:
    p = round((pct or 0) * 100)
    return f'<div class="bar"><span style="width:{p}%"></span></div>'


def fmt_score(home_score, away_score) -> str:
    """Score set par set d'un match en cours/terminé : '6-4 3-2'. '' si aucun."""
    hs = getattr(home_score, "sets", None) or []
    as_ = getattr(away_score, "sets", None) or []
    parts = []
    for h, a in zip(hs, as_):
        if h is None and a is None:
            continue
        parts.append(f'{h if h is not None else 0}-{a if a is not None else 0}')
    return " ".join(parts)


def _match_row(m: dict) -> str:
    """Ligne standard d'un match (à venir ou en direct). Cliquable -> détail."""
    e = html.escape
    if m["status"] == "inprogress":
        sc = f' <span class="dim">{e(m["score"])}</span>' if m.get("score") else ""
        status = f'<span class="live">🔴 EN DIRECT</span>{sc}'
    else:
        status = e(m.get("time") or "")
    inner = (
        f'<div class="rowtop"><span>{e(m["tour"].upper())} · {status}</span></div>'
        f'<div class="players">{e(m["home"])} <span class="dim">vs</span> {e(m["away"])}</div>'
        f'<div class="dim">favori modèle : {e(m.get("fav") or "—")} {e(m.get("favp") or "")}'
        f' · confiance {e(m.get("confidence") or "—")}</div>')
    if m.get("clickable", True):
        return f'<a class="row" href="/app/match/{m["id"]}?tour={m["tour"]}">{inner}</a>'
    return f'<div class="row">{inner}</div>'


def render_matches(groups: list[tuple[str, list[dict]]], live: list[dict] | None = None,
                   finished: list[dict] | None = None,
                   value_picks: list[dict] | None = None, fallback: bool = False) -> str:
    """Page Matchs en sections : paris de confiance, en direct, à venir, terminés.

    groups : [(titre_jour, [match])] pour les matchs à venir. live/finished/value_picks :
    listes de dicts (cf. routeur). match_dict à venir/live : id,tour,home,away,time,status,
    fav,favp,confidence,clickable.
    """
    e = html.escape
    live, finished, value_picks = live or [], finished or [], value_picks or []
    out = []
    if fallback:
        out.append('<div class="banner warn">⚠️ SofaScore momentanément indisponible — scores '
                   'affichés via LiveScore (repli). L\'analyse détaillée revient dès que '
                   'SofaScore répond.</div>')

    # 💎 Paris de confiance (value détectées) — tout en haut
    if value_picks:
        out.append(f'<h2>💎 Paris de confiance ({len(value_picks)})</h2>')
        out.append('<div class="banner">Matchs où le modèle voit une <b>value</b> vs Unibet. '
                   'Avis du modèle, à confirmer — un désaccord n\'est pas un pari gagnant.</div>')
        for v in value_picks:
            edge = round((v.get("edge") or 0) * 100, 1)
            badge = f'<span class="badge b-val">VALUE +{edge} pts</span>'
            inner = (
                f'<div class="rowtop"><span>{e(v["tour"].upper())} · {e(v.get("time") or "")}</span>'
                f'{badge}</div>'
                f'<div class="players">{e(v["home"])} <span class="dim">vs</span> {e(v["away"])}</div>'
                f'<div class="dim">pari : <b class="pos">{e(v.get("player") or "")}</b> '
                f'@{v.get("odds") or "—"} · mise {v.get("stake") if v.get("stake") is not None else "—"}% '
                f'· confiance {e(v.get("confidence") or "—")}</div>')
            out.append(f'<a class="row pick" href="/app/match/{v["id"]}?tour={v["tour"]}">{inner}</a>')

    # 🔴 En direct
    if live:
        out.append(f'<h2>🔴 En direct ({len(live)})</h2>')
        out.extend(_match_row(m) for m in live)

    # À venir (groupés par jour)
    total_up = sum(len(ms) for _, ms in groups)
    if total_up:
        out.append('<div class="banner">Touchez un match pour son analyse détaillée '
                   '(favori, stats, cotes). Heures en fuseau belge.</div>')
        for title, ms in groups:
            if not ms:
                continue
            out.append(f"<h2>{e(title)} ({len(ms)})</h2>")
            out.extend(_match_row(m) for m in ms)
    elif not live and not value_picks:
        out.append('<div class="dim">Aucun match à venir pour le moment.</div>')

    # ✅ Récemment terminés (vs favori du modèle)
    if finished:
        out.append(f'<h2>✅ Récemment terminés ({len(finished)})</h2>')
        for f in finished:
            mark = ('<span class="pos">✓ modèle ok</span>' if f.get("ok")
                    else '<span class="neg">✗ raté</span>')
            inner = (
                f'<div class="rowtop"><span>{e(f["tour"].upper())} · terminé</span>{mark}</div>'
                f'<div class="players">{e(f["home"])} <span class="dim">vs</span> {e(f["away"])}</div>'
                f'<div class="dim">favori modèle : {e(f.get("fav") or "—")} {e(f.get("favp") or "")} '
                f'· vainqueur : <b>{e(f.get("winner_name") or "")}</b></div>')
            out.append(f'<a class="row" href="/app/match/{f["id"]}?tour={f["tour"]}">{inner}</a>')

    return layout("Matchs", "matches", "".join(out), refresh=True)


def render_match_detail(a, winner_odds: tuple[float | None, float | None],
                        aces: dict | None = None, tour: str = "atp",
                        home_form: list[dict] | None = None,
                        away_form: list[dict] | None = None,
                        h2h: dict | None = None, score: str = "") -> str:
    """a = MatchAnalysis ; winner_odds = (cote_home, cote_away) Unibet ;
    aces = récap tendance d'aces ; home_form/away_form = derniers résultats (V/D) ;
    h2h = {'home': n, 'away': n} bilan des confrontations ; score = score en cours."""
    e = html.escape
    hp = a.model_home_probability
    ap = a.model_away_probability
    live = (f' · <span class="live">🔴 {e(score)}</span>'
            if a.status == "inprogress" and score else
            (f' · {e(score)}' if score else ""))
    head = (f'<a class="dim" href="/app">← Retour aux matchs</a>'
            f'<div class="players" style="font-size:18px;margin-top:10px">'
            f'{e(a.home.name)} <span class="dim">vs</span> {e(a.away.name)}</div>'
            f'<div class="dim">{e(a.ground_type or "")} · statut {e(a.status or "")}'
            f'{live} · confiance {e(a.confidence or "—")}</div>')

    # 💰 LE PARI À JOUER — la recommandation nette du modèle pour ce match
    pick = next((v for v in a.value_bets if v.is_value), None)
    if pick:
        pari_html = (
            f'<div class="big" style="border-color:#1b5e20;background:#13251a">'
            f'💰 Pari à jouer : <b class="pos">{e(pick.player)}</b> @ {pick.odds}'
            f'<div class="d">Mise conseillée {pick.recommended_stake_pct}% du capital · '
            f'edge +{round((pick.edge or 0)*100, 1)} pts vs Unibet. Value du modèle, '
            f'à recouper — un pari n\'est jamais garanti.</div></div>')
    elif a.unibet_matched:
        pari_html = (
            '<div class="big">🚫 Aucun pari conseillé'
            '<div class="d">Le modèle ne détecte pas de value vs les cotes Unibet '
            'sur ce match. Mieux vaut s\'abstenir.</div></div>')
    else:
        pari_html = ""

    # Forme récente (V/D, du plus récent au plus ancien)
    def _form_row(name, form):
        if not form:
            return f'<tr><td>{e(name)}</td><td class="dim">historique indisponible</td></tr>'
        badges = " ".join('<span class="pos">V</span>' if f["win"]
                          else '<span class="neg">D</span>' for f in form)
        last_opp = form[0].get("opp", "")
        return (f'<tr><td>{e(name)}</td><td>{badges} '
                f'<span class="dim">· dernier : {"✓" if form[0]["win"] else "✗"} '
                f'{e(last_opp.split()[-1] if last_opp else "")}</span></td></tr>')

    form_html = ""
    if home_form or away_form:
        form_html = ('<h2>Forme récente <span class="dim">(récent → ancien)</span></h2>'
                     '<table>' + _form_row(a.home.name, home_form or [])
                     + _form_row(a.away.name, away_form or []) + '</table>')

    # Face-à-face
    h2h_html = ""
    if h2h:
        hh, aw = h2h.get("home") or 0, h2h.get("away") or 0
        if hh + aw > 0:
            lead = a.home.name if hh > aw else (a.away.name if aw > hh else None)
            tag = f'avantage {e(lead.split()[-1])}' if lead else "à égalité"
            h2h_html = (f'<h2>Face-à-face</h2><div class="row"><b>{e(a.home.name)} '
                        f'{hh} – {aw} {e(a.away.name)}</b><br>'
                        f'<span class="dim">{hh + aw} confrontation'
                        f'{"s" if hh + aw > 1 else ""} · {tag}</span></div>')

    probs = ""
    if hp is not None:
        probs = (f'<h2>Probabilités du modèle</h2>'
                 f'<div class="dim">{e(a.home.name)} {round(hp*100)}% · '
                 f'{e(a.away.name)} {round(ap*100)}%</div>{_bar(hp)}')

    frows = "".join(
        f'<tr><td>{e(f.name)}</td><td>{round((f.home or 0)*100)}%</td>'
        f'<td>{round((f.away or 0)*100)}%</td><td class="dim">{e(f.detail or "")}</td></tr>'
        for f in a.factors)
    factors = (f'<h2>Facteurs</h2><table><tr><td class="dim">facteur</td>'
               f'<td class="dim">{e(a.home.name.split()[-1])}</td>'
               f'<td class="dim">{e(a.away.name.split()[-1])}</td><td class="dim">détail</td></tr>'
               f'{frows}</table>') if a.factors else ""

    # Lecture du modèle (favori) — neutre, pas de pari conseillé
    fav = a.home.name if (hp or 0) >= 0.5 else a.away.name
    favp = round(max(hp or 0, ap or 0) * 100)
    verdict = (f'<div class="big">🎾 Favori du modèle : {e(fav)} ({favp}%)'
               f'<div class="d">Confiance {e(a.confidence or "—")}. Lecture statistique, '
               f'à recouper avec ton jugement — ce n\'est pas un conseil de pari.</div></div>')

    # Cotes Unibet + comparaison au marché (informatif)
    oh, oa = winner_odds
    odds_html = ""
    if a.unibet_matched and (oh or oa):
        def cmp_row(name, model_p, odds):
            imp = round(100 / odds) if odds else None
            mp = round((model_p or 0) * 100)
            note = ""
            if imp is not None:
                if mp - imp >= 6:
                    note = '<span class="pos">modèle plus optimiste</span>'
                elif imp - mp >= 6:
                    note = '<span class="neg">modèle plus prudent</span>'
                else:
                    note = '<span class="dim">en accord</span>'
            return (f'<tr><td>{e(name)}</td><td><b>{odds or "—"}</b></td>'
                    f'<td>{mp}% / {imp if imp is not None else "—"}%</td><td>{note}</td></tr>')
        odds_html = (
            '<h2>Cotes Unibet vs modèle</h2>'
            '<table><tr><td class="dim">joueur</td><td class="dim">cote</td>'
            '<td class="dim">modèle / implicite</td><td class="dim"></td></tr>'
            + cmp_row(a.home.name, hp, oh) + cmp_row(a.away.name, ap, oa) + '</table>')
    elif not a.unibet_matched:
        odds_html = ('<div class="banner">Cotes Unibet indisponibles (match pas encore '
                     'à l\'affiche du book).</div>')

    # Tendance d'aces : fourchette (durée), ajustement adversaire, P(plus de la ligne)
    aces_html = ""
    if aces:
        def arow(name, p):
            if p.get("rate") is None:
                return (f'<tr><td>{e(name)}</td><td class="dim">—</td>'
                        f'<td class="dim">tendance inconnue</td></tr>')
            lo, hi = round(p["exp_low"]), round(p["exp_high"])
            adj = ""
            if p["factor"] <= 0.97:
                adj = ' <span class="dim">(− retour adverse)</span>'
            elif p["factor"] >= 1.03:
                adj = ' <span class="dim">(+ retour faible)</span>'
            # vs ligne Unibet
            if p.get("line") is not None and p.get("p_over_low") is not None:
                pl, ph = round(p["p_over_low"] * 100), round(p["p_over_high"] * 100)
                lo_p, hi_p = min(pl, ph), max(pl, ph)
                if hi_p < 48:
                    verdict = '<span class="neg">Moins de</span> plus probable'
                elif lo_p > 55:
                    verdict = '<span class="pos">Plus de</span> plausible (si match long)'
                else:
                    verdict = 'incertain — dépend de la durée'
                cmp = (f'Plus de {p["line"]} : <b>{lo_p}–{hi_p}%</b><br>'
                       f'<span class="dim">{verdict}</span>')
            else:
                cmp = '<span class="dim">pas de ligne Unibet</span>'
            return (f'<tr><td>{e(name)}<br><span class="dim">{p["adj_rate"]:.2f}/jeu</span></td>'
                    f'<td><b>~{lo}–{hi}</b> aces{adj}</td><td>{cmp}</td></tr>')
        aces_html = (
            '<h2>Service — aces attendus</h2>'
            '<div class="banner">Fourchette selon la <b>durée du match</b> '
            f'(court ~{round(aces["sg_short"])} jeux de service → long ~{round(aces["sg_long"])}), '
            'ajustée par la <b>force de retour</b> de l\'adversaire. '
            '<b>P(Plus de la ligne)</b> = notre proba vs le pari Unibet. '
            '⚠️ Le book intègre déjà tout ça : à lire, pas un signal de value.</div>'
            '<table><tr><td class="dim">joueur</td><td class="dim">aces attendus</td>'
            '<td class="dim">vs ligne Unibet</td></tr>'
            + arow(aces["home_name"], aces["home"])
            + arow(aces["away_name"], aces["away"]) + '</table>')

    # Accès à l'outil "Tous les paris" (modèle vs book sur tous les marchés Unibet)
    paris_link = ""
    if a.unibet_matched:
        paris_link = (f'<a class="big" href="/app/match/{a.match_id}/paris?tour={e(tour)}">'
                      f'🎯 Tous les paris (modèle vs Unibet)'
                      f'<div class="d">Vainqueur, aces, jeux, sets, breaks… proba du modèle '
                      f'vs cote du book, marché par marché.</div></a>')

    body = (head + pari_html + verdict + form_html + h2h_html + paris_link
            + probs + factors + aces_html + odds_html)
    return layout(f"{a.home.name} vs {a.away.name}", "matches", body)


def _market_rows(rows: list[dict]) -> str:
    """Lignes d'un tableau de marché : sélection | cote | modèle/book | écart."""
    e = html.escape
    trs = []
    for r in rows:
        mp, ip = r.get("model_p"), r.get("implied_p")
        edge = r.get("edge")
        mp_s = f"{round(mp * 100)}%" if mp is not None else "—"
        ip_s = f"{round(ip * 100)}%" if ip is not None else "—"
        if edge is None:
            edge_s = "—"
        else:
            cls = "pos" if edge > 0 else ("neg" if edge < 0 else "dim")
            edge_s = f'<span class="{cls}">{"+" if edge >= 0 else ""}{round(edge * 100, 1)}</span>'
        flag = ' <span class="badge b-val">écart</span>' if r.get("value") else ""
        trs.append(
            f'<tr><td>{e(r.get("market") or "")}<br>'
            f'<span class="dim">{e(r.get("selection") or "")}'
            f'{(" · ligne " + str(r["line"])) if r.get("line") is not None else ""}</span>{flag}</td>'
            f'<td><b>{r.get("odds") or "—"}</b></td>'
            f'<td>{mp_s} / {ip_s}</td><td>{edge_s}</td></tr>')
    return "".join(trs)


def render_markets(match, winner_rows: list[dict], ace_rows: list[dict],
                   sim_rows: list[dict], odds_matched: bool, tour: str = "atp",
                   set_rows: list[dict] | None = None) -> str:
    """Page "Tous les paris" : modèle vs book, par marché, regroupé par fiabilité."""
    e = html.escape
    set_rows = set_rows or []
    back = (f'<a class="dim" href="/app/match/{match.id}?tour={e(tour)}">← Retour à l\'analyse</a>'
            f'<div class="players" style="font-size:18px;margin-top:10px">'
            f'{e(match.home.name)} <span class="dim">vs</span> {e(match.away.name)}</div>')
    if not odds_matched:
        body = back + '<div class="banner">Cotes Unibet indisponibles pour ce match.</div>'
        return layout("Tous les paris", "matches", body)

    # 🎯 Meilleur pari du match : on ne retient QUE les marchés fiables/calibrés
    # (vainqueur, sets). Les aces sont exclus du titre (edges souvent artefacts non
    # validés) — ils restent visibles, en info, dans leur section.
    def _best(rows):
        cand = [r for r in rows if (r.get("edge") or 0) > 0]
        return max(cand, key=lambda r: r["edge"]) if cand else None

    options = [(_best(winner_rows), "Vainqueur", "marché le plus fiable")]
    options = [(r, lbl, note) for r, lbl, note in options if r]
    if options:
        best, blbl, bnote = max(options, key=lambda x: x[0]["edge"])
        be = round((best["edge"] or 0) * 100, 1)
        if (best["edge"] or 0) >= 0.04:
            line = f' (ligne {best["line"]})' if best.get("line") is not None else ""
            best_html = (
                f'<div class="big" style="border-color:#1b5e20;background:#13251a">'
                f'🎯 Meilleur pari : <b class="pos">{e(best.get("selection") or "")}</b>{line} '
                f'@ {best.get("odds") or "—"} <span class="dim">[{blbl}]</span>'
                f'<div class="d">{e(best.get("market") or "")} · modèle '
                f'{round((best.get("model_p") or 0)*100)}% vs book '
                f'{round((best.get("implied_p") or 0)*100)}% · edge +{be} pts. '
                f'{bnote} — jamais garanti.</div></div>')
        else:
            best_html = ('<div class="big">🎯 Aucun pari à valeur nette'
                         '<div class="d">Les cotes du book collent à nos estimations sur '
                         'ce match. Mieux vaut s\'abstenir ou jouer petit.</div></div>')
    else:
        best_html = ""

    # Légende : comment lire le tableau (la demande "mieux expliqué")
    intro = (
        '<div class="banner"><b>Comment lire ?</b> Chaque ligne = un pari Unibet.<br>'
        '• <b>modèle</b> = la proba qu\'on estime · <b>book</b> = la proba derrière la cote.<br>'
        '• <b>écart</b> = modèle − book. <span class="pos">Vert (+)</span> = on te donne '
        'PLUS de chances que le book ⇒ potentiellement intéressant. '
        '<span class="neg">Rouge (−)</span> = à éviter.<br>'
        '⚠️ Un écart positif <b>n\'est pas</b> un gain garanti — le book est souvent très '
        'juste, surtout sur les petits marchés.</div>')

    def section(title, sub, rows, sub_class="banner"):
        if not rows:
            return ""
        return (f'<h2>{e(title)}</h2><div class="{sub_class}">{sub}</div>'
                '<table><tr><td class="dim">marché / sélection</td><td class="dim">cote</td>'
                '<td class="dim">modèle / book</td><td class="dim">écart</td></tr>'
                f'{_market_rows(rows)}</table>')

    sections = (
        section("🏆 Vainqueur du match",
                "Le marché le <b>mieux modélisé</b> (Elo, classement, forme, surface, h2h). "
                "C\'est ici que nos estimations sont les plus fiables.", winner_rows)
        + section("🛡️ Paris « sûrs » — sets (au moins un set, handicap ±2.5, total sets)",
                  "Faible cote, haute probabilité (comme tes paris gagnants). "
                  "<b>Validé sur 4250 matchs : ces marchés sont bien cotés, sans edge "
                  "systématique.</b> On affiche notre estimation (approximative) à titre "
                  "<b>indicatif</b> — pas de pari conseillé ici.", set_rows)
        + section("🎾 Aces (exploratoire)",
                  "Signal réel sur la tendance d\'aces, mais total ancré sur le book : "
                  "à confirmer par le suivi avant d\'en faire un pari.", ace_rows)
        + section("🧪 Jeux · breaks (simulateur — expérimental)",
                  "⚠️ Simulation du déroulé, <b>peu fiable</b> sur ces marchés. "
                  "À ne PAS suivre pour parier en l\'état.", sim_rows, sub_class="banner warn"))
    if not (winner_rows or set_rows or ace_rows or sim_rows):
        sections = '<div class="dim">Aucun marché évaluable pour ce match.</div>'
    return layout("Tous les paris", "matches", back + best_html + intro + sections)
