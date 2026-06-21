"""Capture la VRAIE requête de cote bet builder (coupon/validate.json) depuis une session
Unibet CONNECTÉE par l'utilisateur lui-même.

Sécurité : l'utilisateur se connecte À LA MAIN dans la fenêtre. Le script NE capture QUE le
trafic réseau vers l'endpoint de validation de coupon (jamais le mot de passe / les frappes).
Profil PERSISTANT (data/_bb_session, gitignore) -> la session reste connectée pour les runs suivants.

Usage :
    python tools/betbuilder_capture.py [--event 1025862147] [--seconds 240]
Pendant la fenêtre : se connecter, ouvrir un match, onglet « Créa-combiné », ajouter 2-3 jambes.
Sortie : data/_bb_probe/validate_capture.json (URL, méthode, headers, body, réponse).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.betbuilder_probe import CDP, _chrome, OUTDIR   # réutilise le client CDP

PROFILE = os.path.join("data", "_bb_session")   # persistant (gitignore via data/)
MATCH = ("coupon/validate", "coupon%2Fvalidate", "cf-mt-auth", "/coupon/")


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _auto_event() -> tuple[str, str]:
    """Choisit un match À VENIR avec Bet Builder (prePacks dispo), foot d'abord puis basket."""
    from app import unibet
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for sp in ("foot", "basket"):
        for m in (unibet.matches(sp) or []):
            st = m.get("start", "")
            try:
                if datetime.fromisoformat(st.replace("Z", "+00:00")) <= now:
                    continue
            except ValueError:
                continue
            if unibet.prepack_combos(str(m["id"])):
                return str(m["id"]), f"{m.get('home')} - {m.get('away')} ({st[:16]})"
    return "", ""


async def capture(event_id: str, seconds: int) -> None:
    import websockets
    os.makedirs(OUTDIR, exist_ok=True)
    os.makedirs(PROFILE, exist_ok=True)
    if event_id in ("", "auto", None):
        event_id, label = _auto_event()
        if not event_id:
            raise SystemExit("Aucun match à venir avec Bet Builder trouvé maintenant. Réessaie plus tard.")
        print(f">>> Match auto-sélectionné : {label}  (event {event_id})")
    url = f"https://fr.unibetsports.be/betting/sports/event/{event_id}"
    port = _free_port()
    proc = subprocess.Popen(
        [_chrome(), "--disable-gpu", f"--remote-debugging-port={port}",
         f"--user-data-dir={os.path.abspath(PROFILE)}", "--no-first-run", "--no-default-browser-check",
         "--disable-blink-features=AutomationControlled", "--lang=fr-FR", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            print(f"\n>>> Fenêtre ouverte {seconds}s. CONNECTE-TOI, ouvre un match, Créa-combiné, "
                  f"ajoute 2-3 jambes. Je capture (HTTP + WebSocket)…\n")
            # boucle de capture : HTTP coupon/validate ET trames WebSocket (la cote arrive souvent par WS)
            seen = set()
            BBKW = ("coupon", "validate", "betbuilder", "bet_builder", "outcomeids", "betBuilder")
            for _ in range(seconds):
                await asyncio.sleep(1)
                for method, p in list(c.events):
                    rid = p.get("requestId")
                    if method == "Network.requestWillBeSent":
                        u = p.get("request", {}).get("url", "")
                        if any(m in u for m in MATCH) and ("http", rid) not in seen:
                            seen.add(("http", rid))
                            print(f"  ⚡HTTP {p['request'].get('method')} {u[:85]}")
                    elif method == "Network.webSocketCreated":
                        if ("wsc", rid) not in seen:
                            seen.add(("wsc", rid))
                            print(f"  🔌WS  {p.get('url','')[:85]}")
                    elif method in ("Network.webSocketFrameSent", "Network.webSocketFrameReceived"):
                        pd = (p.get("response") or {}).get("payloadData", "")
                        if any(k in pd.lower() for k in BBKW) and ("wsf", rid, pd[:40]) not in seen:
                            seen.add(("wsf", rid, pd[:40]))
                            tag = "↑" if method.endswith("Sent") else "↓"
                            print(f"  🔌{tag} frame: {pd[:120]}")
            # collecte finale : requêtes HTTP validate + leurs réponses + trames WS bet builder
            reqs = {}
            ws_urls = {}
            ws_frames = []
            BBKW = ("coupon", "validate", "betbuilder", "bet_builder", "outcomeids", "betbuilder")
            for method, p in c.events:
                rid = p.get("requestId")
                if method == "Network.requestWillBeSent":
                    rq = p.get("request", {})
                    if any(m in rq.get("url", "") for m in MATCH):
                        reqs.setdefault(rid, {}).update(
                            url=rq.get("url"), method=rq.get("method"),
                            headers=rq.get("headers"), postData=rq.get("postData"))
                elif method == "Network.responseReceived":
                    if rid in reqs:
                        reqs[rid]["status"] = p.get("response", {}).get("status")
                elif method == "Network.webSocketCreated":
                    ws_urls[rid] = p.get("url")
                elif method in ("Network.webSocketFrameSent", "Network.webSocketFrameReceived"):
                    pd = (p.get("response") or {}).get("payloadData", "")
                    low = pd.lower()
                    # garde : tout ce qui touche NOTRE match, le bet builder, ou une trame ENVOYÉE (souscription)
                    keep = (event_id in pd or "betbuilder" in low or "prepack" in low
                            or "outcomeids" in low or method.endswith("Sent"))
                    if keep:
                        ws_frames.append({"ws_url": ws_urls.get(rid), "rid": rid,
                                          "dir": "sent" if method.endswith("Sent") else "received",
                                          "payload": pd[:6000]})
            for rid in list(reqs):
                if reqs[rid].get("method") in ("POST", "GET"):
                    try:
                        r = await c.cmd("Network.getResponseBody", {"requestId": rid})
                        reqs[rid]["responseBody"] = r.get("result", {}).get("body", "")[:6000]
                    except Exception:
                        pass
            out = os.path.join(OUTDIR, "validate_capture.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"http": list(reqs.values()), "ws_urls": ws_urls,
                           "ws_frames": ws_frames}, f, ensure_ascii=False, indent=2)
            print(f"\n>>> HTTP coupon: {len(reqs)} | trames WS bet builder: {len(ws_frames)} -> {out}")
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="auto", help="event_id Unibet, ou 'auto' (match à venir avec Bet Builder)")
    ap.add_argument("--seconds", type=int, default=300)
    a = ap.parse_args()
    asyncio.run(capture(a.event, a.seconds))
