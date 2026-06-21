"""RECON Bet Builder Unibet : capture le trafic réseau d'une page de match pour découvrir
l'endpoint qui calcule la cote d'un combiné même-match (créa-combiné).

But (phase 1) : voir l'état de la page (cookie wall / géo / login), lister tous les hôtes/endpoints
appelés, et repérer comment atteindre le « Créa-combiné ». On NE place JAMAIS de pari (lecture seule).

Usage :
    python tools/betbuilder_probe.py <event_id_unibet> [--visible] [--wait 6] [--click "Créa"]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
OUTDIR = os.path.join("data", "_bb_probe")


def _chrome() -> str:
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if not found:
        raise SystemExit("Chrome introuvable")
    return found


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class CDP:
    """Client CDP minimal avec gestion des ÉVÉNEMENTS (le harnais screenshot ne lit que les réponses)."""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._futs: dict = {}
        self.events: list = []          # (method, params)
        self._reader = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if "id" in m and m["id"] in self._futs:
                    self._futs.pop(m["id"]).set_result(m)
                elif "method" in m:
                    self.events.append((m["method"], m.get("params", {})))
        except Exception:
            pass

    async def cmd(self, method, params=None):
        self._id += 1
        fut = asyncio.get_event_loop().create_future()
        self._futs[self._id] = fut
        await self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=30)


async def probe(event_id: str, visible: bool, wait: float, click: str | None,
                grab: str | None = None, bbflow: bool = False) -> None:
    import websockets

    os.makedirs(OUTDIR, exist_ok=True)
    url = f"https://fr.unibetsports.be/betting/sports/event/{event_id}"
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="bb_cdp_")
    flags = [_chrome(), "--disable-gpu", f"--remote-debugging-port={port}",
             f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-blink-features=AutomationControlled",
             "--lang=fr-FR", "--window-size=430,920"]
    if not visible:
        flags.insert(1, "--headless=new")
    proc = subprocess.Popen(flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(50):
            try:
                data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
                for t in data:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws_url = t["webSocketDebuggerUrl"]; break
            except Exception:
                pass
            if ws_url:
                break
            await asyncio.sleep(0.3)
        if not ws_url:
            raise SystemExit("CDP : aucun onglet exposé")

        async with websockets.connect(ws_url, max_size=120_000_000) as ws:
            c = CDP(ws)
            await c.cmd("Network.enable")
            await c.cmd("Page.enable")
            await c.cmd("Runtime.enable")
            await c.cmd("Emulation.setDeviceMetricsOverride",
                        {"width": 430, "height": 920, "deviceScaleFactor": 2, "mobile": True})
            await c.cmd("Emulation.setTouchEmulationEnabled", {"enabled": True})
            await c.cmd("Page.navigate", {"url": url})
            await asyncio.sleep(wait)

            # Accepte les cookies (OneTrust) — sinon le contenu du match ne se rend pas
            js_cookie = (
                "(()=>{const b=[...document.querySelectorAll('button,a')]"
                ".find(e=>/autoriser tous|accepter tous|tout accepter|accept all/i.test(e.textContent||''));"
                "if(b){b.click();return 'cookies acceptés';}return 'pas de bandeau cookies';})()"
            )
            r = await c.cmd("Runtime.evaluate", {"expression": js_cookie, "returnByValue": True})
            print("  cookies:", r.get("result", {}).get("result", {}).get("value"))
            await asyncio.sleep(wait)   # laisse les marchés se charger après consentement

            # Clique éventuel (ex. onglet « Créa-combiné ») par texte
            if click:
                js = (
                    "(()=>{const t=%r.toLowerCase();"
                    "const els=[...document.querySelectorAll('button,a,div,span,li')];"
                    "const hit=els.find(e=>e.textContent&&e.textContent.trim().toLowerCase().includes(t)"
                    "&&e.offsetParent!==null);"
                    "if(hit){hit.click();return 'CLICK '+hit.tagName+' :: '+hit.textContent.trim().slice(0,60);}"
                    "return 'NON TROUVÉ: '+t;})()" % click
                )
                r = await c.cmd("Runtime.evaluate", {"expression": js, "returnByValue": True})
                print("  clic:", r.get("result", {}).get("result", {}).get("value"))
                await asyncio.sleep(wait)

            # Flux Bet Builder : ouvrir l'onglet Créa-combiné, cliquer 2 issues -> déclenche le POST de prix
            if bbflow:
                js_open = (
                    "(()=>{const t=/cr[ée]a|bet builder|combin[ée]|construis/i;"
                    "const els=[...document.querySelectorAll('button,a,[role=tab],div,span,li')]"
                    ".filter(e=>e.offsetParent&&t.test(e.textContent||'')&&(e.textContent||'').length<30);"
                    "if(els[0]){els[0].click();return 'ouvert: '+els[0].textContent.trim();}return 'BB tab introuvable';})()"
                )
                r = await c.cmd("Runtime.evaluate", {"expression": js_open, "returnByValue": True})
                print("  BB open:", r.get("result", {}).get("result", {}).get("value"))
                await asyncio.sleep(wait)
                for k in range(2):   # clique 2 boutons d'issue (texte = une cote décimale style 1.50)
                    js_pick = (
                        "(()=>{const od=/^\\s*\\d+[.,]\\d{2}\\s*$/;"
                        "const b=[...document.querySelectorAll('button,[role=button],div')]"
                        ".filter(e=>e.offsetParent&&od.test(e.textContent||'')&&!e.dataset._bbk);"
                        "if(b[%d]){b[%d].dataset._bbk=1;b[%d].click();return 'clic issue: '+b[%d].textContent.trim();}"
                        "return 'issue introuvable';})()" % (0, 0, 0, 0)
                    )
                    r = await c.cmd("Runtime.evaluate", {"expression": js_pick, "returnByValue": True})
                    print(f"  BB pick {k+1}:", r.get("result", {}).get("result", {}).get("value"))
                    await asyncio.sleep(2.5)

            # Inventaire des onglets/sections visibles (pour repérer le créa-combiné)
            js_tabs = (
                "[...document.querySelectorAll('button,a,[role=tab],li')]"
                ".map(e=>e.textContent.trim()).filter(t=>t&&t.length<40)"
                ".filter((v,i,a)=>a.indexOf(v)===i).slice(0,80)"
            )
            r = await c.cmd("Runtime.evaluate", {"expression": js_tabs, "returnByValue": True})
            tabs = r.get("result", {}).get("result", {}).get("value", [])

            # Screenshot d'état
            shot = await c.cmd("Page.captureScreenshot", {"format": "png"})
            png = base64.b64decode(shot["result"]["data"])
            shot_path = os.path.join(OUTDIR, f"state_{event_id}.png")
            with open(shot_path, "wb") as f:
                f.write(png)

            # Récolte des requêtes réseau
            reqs = {}
            for method, p in c.events:
                if method == "Network.requestWillBeSent":
                    rid = p.get("requestId")
                    rq = p.get("request", {})
                    reqs.setdefault(rid, {})["url"] = rq.get("url")
                    reqs[rid]["method"] = rq.get("method")
                    if rq.get("postData"):
                        reqs[rid]["postData"] = rq["postData"][:2000]
                elif method == "Network.responseReceived":
                    rid = p.get("requestId")
                    resp = p.get("response", {})
                    reqs.setdefault(rid, {})["status"] = resp.get("status")
                    reqs[rid]["mime"] = resp.get("mimeType")

            # Corps de réponse pour les hôtes ciblés (ShapeGames / Kambi bet builder)
            if grab:
                grabbed = {}
                for rid, info in reqs.items():
                    u = info.get("url", "")
                    if grab in u:
                        try:
                            r = await c.cmd("Network.getResponseBody", {"requestId": rid})
                            body = r.get("result", {}).get("body", "")
                            grabbed[u] = {"method": info.get("method"), "status": info.get("status"),
                                          "postData": info.get("postData"), "body": body[:4000]}
                        except Exception:
                            pass
                gp = os.path.join(OUTDIR, f"grab_{event_id}.json")
                with open(gp, "w", encoding="utf-8") as f:
                    json.dump(grabbed, f, ensure_ascii=False, indent=2)
                print(f"  corps capturés ({grab}) : {len(grabbed)} -> {gp}")
                for u in list(grabbed)[:20]:
                    print(f"    [{grabbed[u]['method']} {grabbed[u]['status']}] {u[:120]}")

            # Hôtes uniques + endpoints "intéressants"
            from urllib.parse import urlparse
            hosts = {}
            interesting = []
            # mots-clés sur le CHEMIN (pas le domaine 'unibet' qui pollue) + POST systématiquement retenus
            KW = ("odds", "combo", "build", "pulse", "rgm", "sgp", "coupon", "outcome",
                  "price", "betoffer", "betslip", "offering", "cust", "/bet")
            for rid, info in reqs.items():
                u = info.get("url", "")
                if not u or u.startswith("data:"):
                    continue
                pr = urlparse(u)
                h = pr.netloc
                hosts[h] = hosts.get(h, 0) + 1
                path_low = pr.path.lower()
                is_post = (info.get("method") == "POST")
                if is_post or any(k in path_low for k in KW):
                    interesting.append({"url": u, "method": info.get("method"),
                                        "status": info.get("status"), "mime": info.get("mime"),
                                        "postData": info.get("postData")})

            out = {"event_id": event_id, "url": url, "n_requests": len(reqs),
                   "hosts": dict(sorted(hosts.items(), key=lambda x: -x[1])),
                   "tabs_visibles": tabs, "interesting": interesting}
            log_path = os.path.join(OUTDIR, f"net_{event_id}.json")
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)

            print(f"  screenshot -> {shot_path}")
            print(f"  réseau ({len(reqs)} req) -> {log_path}")
            print(f"  hôtes principaux : {list(out['hosts'].items())[:8]}")
            print(f"  onglets visibles ({len(tabs)}) : {tabs[:25]}")
            print(f"  endpoints intéressants : {len(interesting)}")
            for it in interesting[:15]:
                print(f"    [{it['method']} {it.get('status')}] {it['url'][:110]}")
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("event_id")
    ap.add_argument("--visible", action="store_true", help="navigateur visible (anti-bot)")
    ap.add_argument("--wait", type=float, default=6.0)
    ap.add_argument("--click", default=None, help="texte d'onglet/bouton à cliquer (ex. 'Créa')")
    ap.add_argument("--grab", default=None, help="sous-chaîne d'URL : capturer les corps de réponse (ex. 'shapegames')")
    ap.add_argument("--bbflow", action="store_true", help="ouvrir le Créa-combiné + cliquer 2 issues (capture le POST de prix)")
    a = ap.parse_args()
    asyncio.run(probe(a.event_id, a.visible, a.wait, a.click, a.grab, a.bbflow))
