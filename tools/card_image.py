"""Génère une CARTE graphique (PNG) d'un prono pour Telegram (sendPhoto) — rendu premium via Chrome.

Une carte = un HTML soigné (dégradé sombre, accent cyan, cotes en pastilles) rendu à taille fixe
par Chrome (CDP) puis capturé en PNG. Réutilise le harnais CDP du projet.

Usage (démo) : python tools/card_image.py
API : render_card_sync(data, out_png)  où data = {emoji, cat, match, meta, type, cote, legs|pick}
"""
from __future__ import annotations

import asyncio
import base64
import html as _html
import json
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.request

CHROME = (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
          os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"))

_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#05080d;font-family:'Segoe UI',Roboto,Arial,sans-serif;-webkit-font-smoothing:antialiased}
.card{width:920px;padding:46px 50px 40px;background:linear-gradient(160deg,#101b29 0%,#0a0f17 60%,#080c13 100%);
  border:1px solid rgba(34,184,255,.22);border-radius:30px;color:#e9f1fb;position:relative;overflow:hidden}
.glow{position:absolute;top:-140px;right:-120px;width:380px;height:380px;border-radius:50%;
  background:radial-gradient(circle,rgba(34,184,255,.20),transparent 70%)}
.top{font-size:21px;font-weight:800;letter-spacing:.14em;color:#5fd0ff;text-transform:uppercase;position:relative}
.match{font-size:48px;font-weight:900;margin-top:12px;line-height:1.08;position:relative}
.meta{font-size:23px;color:#90a4be;margin-top:12px;font-weight:600;position:relative}
.sep{height:1px;background:rgba(255,255,255,.09);margin:30px 0 26px}
.beth{font-size:19px;font-weight:800;letter-spacing:.10em;color:#9fe7c0;text-transform:uppercase}
.leg{display:flex;justify-content:space-between;align-items:center;gap:20px;font-size:29px;font-weight:700;
  margin-top:20px;line-height:1.2}
.leg .o{flex:none;background:rgba(25,196,106,.15);color:#7ff0b6;border-radius:12px;padding:5px 18px;font-weight:900}
.cote{display:flex;justify-content:space-between;align-items:flex-end;margin-top:34px}
.cote .l{font-size:19px;color:#90a4be;font-weight:700;text-transform:uppercase;letter-spacing:.10em}
.cote .v{font-size:58px;font-weight:900;color:#fff;line-height:1}
.conf{font-size:23px;color:#90a4be;font-weight:600;margin-top:10px}
.conf b{color:#e9f1fb}
.brand{position:absolute;bottom:30px;right:50px;font-size:21px;font-weight:900;letter-spacing:.22em;
  color:rgba(255,255,255,.22)}
"""


def _card_html(d: dict) -> str:
    e = _html.escape
    inner = (f'<div class="glow"></div>'
             f'<div class="top">{e(d.get("emoji",""))} {e(d.get("cat",""))}</div>'
             f'<div class="match">{e(d.get("match",""))}</div>'
             f'<div class="meta">{e(d.get("meta",""))}</div>'
             f'<div class="sep"></div>')
    if d.get("type") == "combo":
        inner += f'<div class="beth">Combiné · {len(d.get("legs",[]))} sélections</div>'
        for sel, cote in d.get("legs", []):
            inner += f'<div class="leg"><span>{e(sel)}</span><span class="o">{e(str(cote))}</span></div>'
        inner += (f'<div class="cote"><span class="l">Cote combinée</span>'
                  f'<span class="v">{e(str(d.get("cote","")))}</span></div>')
    else:
        inner += f'<div class="leg"><span>{e(d.get("pick",""))}</span></div>'
        inner += (f'<div class="cote"><span class="l">Cote</span>'
                  f'<span class="v">{e(str(d.get("cote","")))}</span></div>')
        if d.get("conf"):
            inner += f'<div class="conf">Confiance <b>{e(str(d["conf"]))}%</b></div>'
    inner += '<div class="brand">BETSFIX</div>'
    return f"<!doctype html><html><head><meta charset=utf-8><style>{_CSS}</style></head><body><div class=card>{inner}</div></body></html>"


def _chrome() -> str:
    for c in CHROME:
        if os.path.exists(c):
            return c
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if not found:
        raise SystemExit("Chrome introuvable")
    return found


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


async def render_card(d: dict, out_png: str) -> str:
    """Rend la carte du prono `d` en PNG (out_png). Renvoie le chemin."""
    import websockets
    tmp = tempfile.mkdtemp(prefix="card_")
    htmlf = os.path.join(tmp, "c.html")
    with open(htmlf, "w", encoding="utf-8") as f:
        f.write(_card_html(d))
    port = _free_port()
    proc = subprocess.Popen([_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={port}", f"--user-data-dir={tmp}\\prof",
                             "--no-first-run", "--force-device-scale-factor=2"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = None
        for _ in range(50):
            try:
                data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
                for t in data:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws = t["webSocketDebuggerUrl"]; break
            except Exception:
                pass
            if ws:
                break
            await asyncio.sleep(0.3)
        if not ws:
            raise SystemExit("CDP : aucun onglet")
        async with websockets.connect(ws, max_size=80_000_000) as sock:
            mid = 0

            async def cmd(method, params=None):
                nonlocal mid
                mid += 1
                await sock.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                while True:
                    m = json.loads(await sock.recv())
                    if m.get("id") == mid:
                        return m

            await cmd("Page.enable")
            await cmd("Page.navigate", {"url": "file:///" + htmlf.replace("\\", "/")})
            await asyncio.sleep(1.0)
            # hauteur réelle de la carte -> viewport ajusté (largeur 920 + marges)
            r = await cmd("Runtime.evaluate", {"expression":
                "(function(){var c=document.querySelector('.card');var b=c.getBoundingClientRect();"
                "return JSON.stringify({w:Math.ceil(b.width)+40,h:Math.ceil(b.height)+40});})()",
                "returnByValue": True})
            dims = json.loads(r["result"]["result"]["value"])
            await cmd("Emulation.setDeviceMetricsOverride",
                      {"width": dims["w"], "height": dims["h"], "deviceScaleFactor": 2, "mobile": False})
            await asyncio.sleep(0.2)
            shot = await cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
            with open(out_png, "wb") as f:
                f.write(base64.b64decode(shot["result"]["data"]))
        return out_png
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def render_card_sync(d: dict, out_png: str) -> str:
    return asyncio.run(render_card(d, out_png))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    combo = {"emoji": "⚽", "cat": "Football · Coupe du Monde", "match": "Argentine — Autriche",
             "meta": "aujourd'hui · 17:00", "type": "combo", "cote": "1.64",
             "legs": [("Double chance 1X", "1.07"), ("Plus de 2.5 buts", "1.86"),
                      ("Argentine marque en 1re MT", "1.23")]}
    simple = {"emoji": "🎾", "cat": "Tennis · Roland-Garros", "match": "Pegula — Noskova",
              "meta": "aujourd'hui · 14:00", "type": "simple",
              "pick": "Pegula remporte au moins un set", "cote": "1.21", "conf": 85}
    os.makedirs("data/_cards", exist_ok=True)
    render_card_sync(combo, "data/_cards/combo.png")
    render_card_sync(simple, "data/_cards/simple.png")
    print("OK -> data/_cards/combo.png, simple.png")
