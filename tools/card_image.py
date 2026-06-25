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
import re
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
.hero{margin:-40px -50px 18px;text-align:center;position:relative}
.hero img{width:100%;height:auto;display:block;filter:drop-shadow(0 6px 20px rgba(34,184,255,.30))}
.top{font-size:21px;font-weight:800;letter-spacing:.14em;color:#5fd0ff;text-transform:uppercase}
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
.leg.headl{font-weight:900;font-size:26px;color:#9fe7c0;margin-top:0}
.leg.sub{font-size:26px;color:#cdd9e8;margin-top:16px}
.mk{flex:none;border-radius:12px;padding:6px 18px;font-weight:900;font-size:26px;line-height:1.2}
.mk.won{background:rgba(25,196,106,.22);color:#8df3c0}
.mk.lost{background:rgba(255,80,90,.18);color:#ff9aa1}
.mk.push{background:rgba(150,165,185,.18);color:#c0cbdb}
.verdict{display:flex;align-items:center;justify-content:center;gap:16px;margin:26px 0 2px;
  padding:20px 26px;border-radius:20px;font-size:36px;font-weight:900;letter-spacing:.05em;
  text-transform:uppercase}
.verdict.won{color:#8df3c0;border:1px solid rgba(25,196,106,.55);
  background:linear-gradient(180deg,rgba(25,196,106,.30),rgba(25,196,106,.10))}
.verdict.lost{color:#ff9aa1;border:1px solid rgba(255,80,90,.48);
  background:linear-gradient(180deg,rgba(255,80,90,.24),rgba(255,80,90,.09))}
.verdict.push{color:#c7d2e0;border:1px solid rgba(150,165,185,.42);background:rgba(150,165,185,.14)}
.leg.win span:first-child{color:#bff6d8}
.leg.lose span:first-child{color:#ffc2c6}
.rgt{flex:none;display:flex;align-items:center;gap:16px}
.oc{background:rgba(255,255,255,.13);color:#f2f7fc;border:1px solid rgba(255,255,255,.22);border-radius:11px;
  padding:6px 17px;font-size:26px;font-weight:900;min-width:74px;text-align:center}
.combohd{font-size:28px;font-weight:900;color:#d3edff;letter-spacing:.02em;text-transform:uppercase;
  background:rgba(34,184,255,.13);border-left:6px solid #3fb8ff;border-radius:12px;
  padding:16px 22px;margin:6px 0 10px}
.mark{display:block;flex:none}
.cchero{display:flex;justify-content:space-between;align-items:center;margin-top:24px;
  border-top:1px solid rgba(255,255,255,.07);padding-top:20px}
.cchero .l{font-size:20px;color:#90a4be;font-weight:800;text-transform:uppercase;letter-spacing:.10em}
.cchero .v2{font-size:48px;font-weight:900;color:#6fe3ff;line-height:1;
  text-shadow:0 3px 16px rgba(34,184,255,.4)}
.ico{display:inline-block;vertical-align:-5px;margin-right:6px}
/* accent verdict sur TOUTE la carte (résultats) — inset pour ne pas être rogné */
.card.won{border-color:rgba(25,196,106,.55);box-shadow:inset 0 0 0 2px rgba(25,196,106,.30),inset 0 0 140px rgba(25,196,106,.12)}
.card.won .glow{background:radial-gradient(circle,rgba(25,196,106,.22),transparent 70%)}
.card.lost{border-color:rgba(255,80,90,.50);box-shadow:inset 0 0 0 2px rgba(255,80,90,.26),inset 0 0 140px rgba(255,80,90,.10)}
.card.lost .glow{background:radial-gradient(circle,rgba(255,80,90,.18),transparent 70%)}
.card.push{border-color:rgba(150,165,185,.42);box-shadow:inset 0 0 0 2px rgba(150,165,185,.22)}
.brand{position:absolute;bottom:30px;right:50px;font-size:21px;font-weight:900;letter-spacing:.22em;
  color:rgba(255,255,255,.22)}
"""

_MK = {"won": "✅", "lost": "❌", "push": "➖"}

# Icônes sport en COULEUR (SVG inline) — l'emoji ⚽/🎾/🏀 sort en N&B sous Chrome headless.
_SVG = {
    "⚽": ('<svg class="ico" width="28" height="28" viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" '
          'fill="#f2f6fa" stroke="#0b1118" stroke-width="1"/><path d="M12 6.2l3.4 2.5-1.3 4h-4.2l-1.3-4z" '
          'fill="#10202f"/><path d="M12 6.2V3.5M15.4 8.7l2.6-1M14.1 12.7l1.7 2.2M9.9 12.7l-1.7 2.2'
          'M8.6 8.7l-2.6-1" stroke="#10202f" stroke-width="1.1" fill="none"/></svg>'),
    "🎾": ('<svg class="ico" width="28" height="28" viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" '
          'fill="#d4ff52"/><path d="M4.2 6.5c4.5 2.8 4.5 8.2 0 11M19.8 6.5c-4.5 2.8-4.5 8.2 0 11" '
          'fill="none" stroke="#ffffff" stroke-width="1.7"/></svg>'),
    "🏀": ('<svg class="ico" width="28" height="28" viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" '
          'fill="#ff8a33"/><path d="M1.3 12h21.4M12 1v22M4.3 4.2c4.3 4.3 4.3 11.3 0 15.6M19.7 4.2'
          'c-4.3 4.3-4.3 11.3 0 15.6" fill="none" stroke="#7a3a12" stroke-width="1.2"/></svg>'),
}


def _sport_icon(emoji: str) -> str:
    return _SVG.get(emoji, _html.escape(emoji or ""))


def _mark(mk: str, size: int = 38) -> str:
    """Coche/croix RONDE « maison » (SVG) — cohérente avec les icônes sport, plus premium que l'emoji."""
    if mk == "won":
        return (f'<svg class="mark" width="{size}" height="{size}" viewBox="0 0 36 36"><circle cx="18" cy="18" '
                'r="17" fill="#16b863"/><circle cx="18" cy="18" r="16.4" fill="none" stroke="#9ff5c4" '
                'stroke-opacity=".55" stroke-width="1.1"/><path d="M10 18.6l5 5 11-11.2" fill="none" '
                'stroke="#fff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    if mk == "lost":
        return (f'<svg class="mark" width="{size}" height="{size}" viewBox="0 0 36 36"><circle cx="18" cy="18" '
                'r="17" fill="#e23b46"/><circle cx="18" cy="18" r="16.4" fill="none" stroke="#ffb0b5" '
                'stroke-opacity=".55" stroke-width="1.1"/><path d="M12 12l12 12M24 12L12 24" fill="none" '
                'stroke="#fff" stroke-width="3.5" stroke-linecap="round"/></svg>')
    if mk == "push":
        return (f'<svg class="mark" width="{size}" height="{size}" viewBox="0 0 36 36"><circle cx="18" cy="18" '
                'r="17" fill="#8595a8"/><path d="M11 18h14" fill="none" stroke="#fff" stroke-width="3.5" '
                'stroke-linecap="round"/></svg>')
    return ""


_SPORT_OF = {"⚽": "foot", "🎾": "tennis", "🏀": "basket"}


def _img_uri(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return ""


def _banner_uri(emoji: str) -> str:
    """Bannière BETSFIX du SPORT (en-tête de carte) en data-URI base64. Repli sur le wordmark
    générique si la bannière du sport est absente -> carte autonome (rendu Chrome sans serveur)."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    sport = _SPORT_OF.get(emoji or "")
    for name in ([f"banner_{sport}.png"] if sport else []) + ["wordmark.png"]:
        uri = _img_uri(os.path.join(root, name))
        if uri:
            return uri
    return ""


def _card_html(d: dict) -> str:
    # Échappe + retire le suffixe « (F) » des équipes féminines (WNBA) — affichage seulement.
    def e(x):
        return _html.escape(re.sub(r"\s*\(F\)", "", str(x)))
    _wm = _banner_uri(d.get("emoji", ""))
    _wm_img = f'<img class="wm" src="{_wm}">' if _wm else ''
    _wm_hero = f'<div class="hero">{_wm_img}</div>' if _wm_img else ''
    _icon = _sport_icon(d.get("emoji", ""))
    _cardcls = ""
    inner = (f'<div class="glow"></div>'
             f'{_wm_hero}'
             f'<div class="top">{_icon}{e(d.get("cat",""))}</div>'
             f'<div class="match">{e(d.get("match",""))}</div>'
             f'<div class="meta">{e(d.get("meta",""))}</div>'
             f'<div class="sep"></div>')
    if d.get("type") == "result":
        sp, cb = d.get("simple"), d.get("combo")
        # JAMAIS 2 fois le même prono : si le simple est DÉJÀ une jambe du combiné, on ne l'affiche pas.
        if sp and cb:
            _n = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()
            _sl = _n(sp.get("label", ""))
            if any(_n(l[0]) == _sl for l in cb.get("legs", [])):
                sp = None
        _verdict = (cb or {}).get("mark") or (sp or {}).get("mark") or ""
        _cardcls = _verdict                            # accent (bordure + halo) sur TOUTE la carte
        if sp:
            mk = sp.get("mark", "")
            _wl = "win" if mk == "won" else ("lose" if mk == "lost" else "")
            _oc = f'<span class="oc">{e(str(sp["cote"]))}</span>' if sp.get("cote") else ""
            inner += (f'<div class="leg {_wl}"><span>{e(str(sp.get("label","")))}</span>'
                      f'<span class="rgt">{_oc}{_mark(mk)}</span></div>')
        if cb:
            # ligne « Combiné » = bandeau qui RESSORT, SANS marque à droite
            inner += f'<div class="combohd">Combiné · {len(cb.get("legs",[]))} sélections</div>'
            for leg in cb.get("legs", []):
                lbl, lm = leg[0], leg[1]
                lc = leg[2] if len(leg) > 2 else ""
                _wl = "win" if lm == "won" else ("lose" if lm == "lost" else "")
                _oc = f'<span class="oc">{e(str(lc))}</span>' if lc else ""
                inner += (f'<div class="leg sub {_wl}"><span>{e(str(lbl))}</span>'
                          f'<span class="rgt">{_oc}{_mark(lm)}</span></div>')
            if cb.get("cote"):                         # cote combinée = HÉROS (gros chiffre cyan)
                inner += (f'<div class="cchero"><span class="l">Cote combinée</span>'
                          f'<span class="v2">{e(str(cb["cote"]))}</span></div>')
        # --- BAS de carte : SCORE d'abord, puis le cadre VERDICT tout en bas ---
        inner += '<div class="sep"></div>'
        inner += (f'<div class="cote"><span class="l">Score final</span>'
                  f'<span class="v">{e(str(d.get("score","")))}</span></div>')
        _vtxt = {"won": "Pari gagné", "lost": "Pari perdu", "push": "Remboursé"}.get(_verdict, "")
        if _vtxt:
            inner += f'<div class="verdict {_verdict}">{_mark(_verdict, 34)}{e(_vtxt)}</div>'
    elif d.get("type") == "combo":
        inner += f'<div class="beth">Combiné · {len(d.get("legs",[]))} sélections</div>'
        for sel, cote in d.get("legs", []):
            inner += f'<div class="leg"><span>{e(sel)}</span><span class="o">{e(str(cote))}</span></div>'
        inner += (f'<div class="cote"><span class="l">Cote combinée</span>'
                  f'<span class="v">{e(str(d.get("cote","")))}</span></div>')
    else:
        inner += f'<div class="leg"><span>{e(d.get("pick",""))}</span></div>'
        if d.get("conf"):                              # confiance AU-DESSUS de la cote
            inner += f'<div class="conf">Confiance <b>{e(str(d["conf"]))}%</b></div>'
        inner += (f'<div class="cote"><span class="l">Cote</span>'
                  f'<span class="v">{e(str(d.get("cote","")))}</span></div>')
    _cc = f"card {_cardcls}".strip()
    return (f"<!doctype html><html><head><meta charset=utf-8><style>{_CSS}</style></head>"
            f'<body><div class="{_cc}">{inner}</div></body></html>')


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
             "meta": "sam. 21 juin · 17:00", "type": "combo", "cote": "1.64",
             "legs": [("Double chance 1X", "1.07"), ("Plus de 2.5 buts", "1.86"),
                      ("Argentine marque en 1re MT", "1.23")]}
    simple = {"emoji": "🎾", "cat": "Tennis · Roland-Garros", "match": "Pegula — Noskova",
              "meta": "sam. 21 juin · 14:00", "type": "simple",
              "pick": "Pegula remporte au moins un set", "cote": "1.21", "conf": 85}
    res_combo = {"emoji": "⚽", "cat": "Football · Coupe du Monde", "match": "Argentine — Autriche",
                 "meta": "terminé · sam. 21 juin · 17:00", "type": "result", "score": "3 – 1",
                 "combo": {"cote": "1.64", "mark": "won",
                           "legs": [("Double chance 1X", "won", "1.07"),
                                    ("Plus de 2.5 buts", "won", "1.86"),
                                    ("Argentine marque en 1re MT", "won", "1.23")]}}
    res_simple = {"emoji": "🎾", "cat": "Tennis · Roland-Garros", "match": "Pegula — Noskova",
                  "meta": "terminé · sam. 21 juin · 14:00", "type": "result", "score": "2 – 0 (sets)",
                  "simple": {"label": "Pegula remporte au moins un set", "cote": "1.21", "mark": "won"}}
    os.makedirs("data/_cards", exist_ok=True)
    render_card_sync(combo, "data/_cards/combo.png")
    render_card_sync(simple, "data/_cards/simple.png")
    render_card_sync(res_combo, "data/_cards/res_combo.png")
    render_card_sync(res_simple, "data/_cards/res_simple.png")
    print("OK -> data/_cards/combo.png, simple.png, res_combo.png, res_simple.png")
