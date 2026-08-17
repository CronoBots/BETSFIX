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
html,body{margin:0;padding:0;background:transparent}
.card{width:920px;padding:46px 50px 40px;background:linear-gradient(160deg,#101b29 0%,#0a0f17 60%,#080c13 100%);
  border:2px solid rgba(34,184,255,.55);border-radius:30px;color:#e9f1fb;position:relative;overflow:hidden;
  box-shadow:inset 0 0 0 1px rgba(34,184,255,.28),inset 0 0 80px rgba(34,184,255,.07)}
.glow{position:absolute;top:-140px;right:-120px;width:380px;height:380px;border-radius:50%;
  background:radial-gradient(circle,rgba(34,184,255,.20),transparent 70%)}
.hero{margin:-6px 0 22px;text-align:left;position:relative}
.hero img{height:46px;width:auto;display:block;filter:drop-shadow(0 4px 14px rgba(34,184,255,.35))}   /* wordmark = LOGO (pas bannière pleine largeur) */
.top{font-size:30px;font-weight:900;letter-spacing:.05em;color:#5fd0ff;text-transform:uppercase;line-height:1.25}
.top .ico{width:30px;height:30px;vertical-align:-5px;margin-right:8px}
.topcomp{font-size:20px;font-weight:700;letter-spacing:.02em;color:#93b7db;text-transform:none;margin-left:5px}
.match{font-size:48px;font-weight:900;margin-top:20px;line-height:1.08;position:relative}
.meta{font-size:23px;color:#90a4be;margin-top:12px;font-weight:600;position:relative}
.sep{height:1px;background:rgba(255,255,255,.09);margin:30px 0 26px}
.beth{font-size:19px;font-weight:800;letter-spacing:.10em;color:#9fe7c0;text-transform:uppercase}
.leg{display:flex;justify-content:space-between;align-items:center;gap:20px;font-size:29px;font-weight:700;
  margin-top:20px;line-height:1.2}
.leg .o{flex:none;background:rgba(25,196,106,.15);color:#7ff0b6;border-radius:12px;padding:5px 18px;font-weight:900}
.legsel{display:flex;flex-direction:column;gap:5px;min-width:0}
.legsel .mkt{font-size:30px;font-weight:700;color:#eef4fb;line-height:1.2}
.legsel .pk{font-size:30px;font-weight:800;color:#eef4fb;line-height:1.15}
.legwhy{font-size:21px;font-weight:500;color:#a7bcd6;line-height:1.34;margin:9px 0 6px 2px;
  padding-left:18px;border-left:3px solid rgba(63,184,255,.38)}
.synth{font-size:22px;font-weight:600;color:#d0dfef;line-height:1.36;margin:2px 0 20px;
  background:rgba(34,184,255,.07);border:1px solid rgba(34,184,255,.16);border-radius:14px;padding:16px 20px}
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
    if mk in ("push", "void"):                 # void (remboursé) = même pastille grise que push -> jamais blanc
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
    """Logo BETSFIX (wordmark, IDENTIQUE au logo en haut du site — user 2026-08-17 : plus la bannière
    « BETSFIX FOOTBALL » du sport) en data-URI base64. '' si absent."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    for name in ("wordmark.png", "logo.png"):
        uri = _img_uri(os.path.join(root, name))
        if uri:
            return uri
    return ""


def _selh(e, market: str, pick: str) -> str:
    """Libellé de sélection : marché discret « … : » PUIS la sélection en avant, sur DEUX lignes, si un
    marché est fourni ; sinon la sélection seule sur une ligne (libellés déjà courts)."""
    if market:
        return (f'<span class="legsel"><span class="mkt">{e(market)} :</span>'
                f'<span class="pk">{e(pick)}</span></span>')
    return f'<span>{e(pick)}</span>'


_CSS_SIMPLE = """
.scard{padding:44px 48px 48px}
.shero{text-align:center;margin:2px 0 4px}
.swm{height:100px;width:auto;max-width:82%;margin:0 auto;display:block;filter:drop-shadow(0 6px 22px rgba(34,184,255,.42))}
/* TYPE de pari écrit SOUS le logo comme une signature (user 2026-08-17 : plus en badge) — tagline centrée,
   colorée par type, grand interlettrage pour l'effet « fait partie du logo ». */
.stag{text-align:center;font-size:46px;font-weight:900;letter-spacing:.26em;text-transform:uppercase;
  margin:-2px 0 40px;padding-left:.26em}
.stag.st-confiance{color:#34d27b}
.stag.st-value{color:#22b8ff}
.stag.rb-w{color:#34d27b}
.stag.rb-l{color:#ff6b6b}
.stag.rb-n{color:#9fb6cf}
.slg{text-align:center;font-size:25px;font-weight:800;color:#eef4fb;letter-spacing:.05em;margin-bottom:30px;line-height:1.2}
.stms{display:flex;align-items:center;justify-content:center;gap:30px;margin-bottom:34px}   /* espace équipes -> cadre pari (user 2026-08-17) */
/* CADRE « partie Paris » comme sur le site (.vm) : fond teinté, bordure, coins arrondis (user 2026-08-17). */
.sbet{background:rgba(255,255,255,.05);border:1.5px solid rgba(255,255,255,.14);border-radius:22px;
  padding:30px 32px 32px;box-shadow:0 3px 18px -8px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.05)}
.stm{flex:1;display:flex;flex-direction:column;align-items:center;gap:15px;min-width:0}
.stn{font-size:31px;font-weight:900;color:#eef4fb;text-align:center;line-height:1.14;letter-spacing:-.01em}
.stc{flex:0 0 auto;font-size:44px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.tlwrap{position:relative;width:94px;height:94px;display:block}
.tlogo{position:absolute;inset:0;width:94px;height:94px}
span.tlogo.mono{border-radius:50%;display:grid;place-items:center;font-size:35px;font-weight:900;color:#fff;
  box-shadow:0 5px 15px rgba(0,0,0,.4),inset 0 0 0 1px rgba(255,255,255,.12)}
img.tlogo{object-fit:contain;filter:drop-shadow(0 3px 8px rgba(0,0,0,.5))}
/* Pari + glose aux PROPORTIONS du site (user 2026-08-17) : le pari (28px) est PLUS PETIT que les équipes
   (.stn 31px), la glose (23px) encore un cran en dessous — comme .mc-pick(13)/.mc-gloss(12.5) vs .mc-teams(15). */
.spk{text-align:center;font-size:32px;font-weight:800;color:#eef4fb;margin-bottom:0;line-height:1.2}
.sgl{text-align:center;font-size:25px;font-weight:600;color:#8fa2b8;line-height:1.32;margin-top:9px}
.vbar{position:relative;height:16px;border-radius:99px;overflow:hidden;margin:22px 0 2px;
  background:linear-gradient(180deg,#191b22,#212430);box-shadow:inset 0 1px 2px rgba(0,0,0,.55)}
.vbar>i{position:absolute;left:0;top:0;bottom:0;border-radius:99px;box-shadow:inset 0 1px 0 rgba(255,255,255,.35)}
.ve{position:absolute;top:0;bottom:0}
.vepos{background:rgba(255,255,255,.36)}
.veneg{background:repeating-linear-gradient(45deg,rgba(255,255,255,.13) 0 5px,transparent 5px 11px)}
.vmk{position:absolute;top:0;bottom:0;width:3px;margin-left:-1.5px;background:rgba(244,248,255,.55);border-radius:2px}
.vgrid{display:flex;width:100%;border-top:2px solid rgba(255,255,255,.10);margin-top:28px;padding-top:24px}
.vc{flex:1;display:flex;flex-direction:column;align-items:center;gap:9px}
.vc + .vc{border-left:2px solid rgba(255,255,255,.07)}
.vl{font-size:19px;font-weight:800;color:#90a4be;text-transform:uppercase;letter-spacing:.08em}
.vv{font-size:37px;font-weight:900;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.vconf{color:#64cd8d}
.vcote{color:#fff}
.vpos{color:#34d27b}
.vmid{color:#f6c54a}
.vneg{color:#ff6b6b}
/* ANALYSE en PUCES comme le site (« Pourquoi ce choix », user 2026-08-17) : une phrase = une puce à point
   gris, texte léger. Pas de barre verticale. */
.swhy{font-size:23px;font-weight:500;color:#a7bcd6;line-height:1.44;margin-top:34px;padding:0;list-style:none}
.swhy li{position:relative;padding-left:36px;margin-bottom:18px}
.swhy li:last-child{margin-bottom:0}
.swhy li:before{content:"";position:absolute;left:9px;top:13px;width:11px;height:11px;border-radius:50%;background:#5f7a97}
/* Carte RÉSULTAT façon site : signature Gagné/Perdu sous le logo + SCORE au centre + « Terminé ». */
.rsc{display:flex;flex-direction:column;align-items:center;gap:3px}
.rsc b{font-size:58px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.rfin{font-size:20px;font-weight:800;color:#90a4be;text-transform:uppercase;letter-spacing:.06em}
"""


def _team_logo_html(name, url, e) -> str:
    """Logo d'équipe (URL FotMob) SUR un monogramme coloré de repli — comme le site (crest)."""
    words = [w for w in re.split(r"[\s.\-]+", str(name or "")) if w]
    ini = ("".join(w[0] for w in words[:2]) or (str(name or "?")[:2])).upper()
    hue = sum(ord(c) for c in str(name or "")) % 360
    mono = (f'<span class="tlogo mono" style="background:linear-gradient(150deg,'
            f'hsl({hue},48%,46%),hsl({(hue + 24) % 360},52%,34%))">{e(ini)}</span>')
    if url:
        return (f'<span class="tlwrap">{mono}'
                f'<img class="tlogo" src="{_html.escape(str(url))}" '
                f'onerror="this.remove()"></span>')
    return f'<span class="tlwrap">{mono}</span>'


def _simple_card_html(d: dict) -> str:
    """Carte de PARI SIMPLE façon SITE (user 2026-08-17) : logo BETSFIX + badge TYPE (Confiance/Value),
    ligue + pays centrés, logos + heure au centre, le pari, barre de confiance (zone edge), grille verdict
    Confiance/Edge/Value/Cote, et le « pourquoi » affiché en entier."""
    def e(x):
        return _html.escape(re.sub(r"\s*\(F\)", "", str(x)))
    _wm = _banner_uri(d.get("emoji", ""))
    _tier = str(d.get("tier") or "confiance")
    _tlabel = "VALUE" if _tier == "value" else "CONFIANCE"
    home, away = str(d.get("home") or ""), str(d.get("away") or "")
    _cat = str(d.get("cat", ""))                        # « Football · <comp> »
    _comp = _cat.split(" · ", 1)[1] if " · " in _cat else _cat
    _lg = " • ".join(x for x in (str(d.get("country") or ""), _comp) if x).upper()
    _hh = str(d.get("meta", "")).split("·")[-1].strip() if d.get("meta") else ""
    conf, edge, val, cote = d.get("conf"), d.get("edge"), d.get("value"), d.get("cote")
    cells = []
    if conf is not None:
        cells.append(f'<div class="vc"><span class="vl">Confiance</span><span class="vv vconf">{e(conf)}%</span></div>')
    if edge is not None:
        cells.append(f'<div class="vc"><span class="vl">Edge</span>'
                     f'<span class="vv {"vpos" if edge >= 2 else "vmid" if edge >= 0 else "vneg"}">'
                     f'{"+" if edge >= 0 else ""}{e(edge)} pts</span></div>')
    if val is not None:
        cells.append(f'<div class="vc"><span class="vl">Value</span>'
                     f'<span class="vv {"vpos" if val >= 3 else "vmid" if val >= 1 else "vneg"}">'
                     f'{"+" if val >= 0 else ""}{e(val)}%</span></div>')
    if cote:
        cells.append(f'<div class="vc"><span class="vl">Cote</span><span class="vv vcote">{e(cote)}</span></div>')
    _bar = ""
    try:
        cf, be = int(round(float(conf))), round(100.0 / float(cote))
        _col = "#64cd8d" if cf >= 68 else ("#f6c54a" if cf >= 55 else "#ff6b6b")
        _ov = (f'<i class="ve vepos" style="left:{be}%;width:{cf - be}%"></i>' if cf >= be
               else f'<i class="ve veneg" style="left:{cf}%;width:{be - cf}%"></i>')
        _mk = f'<b class="vmk" style="left:{min(be, 100)}%"></b>' if 0 < be < 100 else ""
        _bar = f'<div class="vbar"><i style="width:{min(cf, 100)}%;background:{_col}"></i>{_ov}{_mk}</div>'
    except (TypeError, ValueError):
        _bar = ""
    # ANALYSE en PUCES (comme le pli « Pourquoi ce choix » du site) : découpe en phrases, regroupe les
    # fragments trop courts (< 25 car), une puce par phrase (user 2026-08-17 : l'analyse était tronquée).
    _why_txt = str(d.get("why") or "").strip()
    _why_html = ""
    if _why_txt:
        _raw = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", _why_txt) if s.strip()]
        _pts: list = []
        for _s in _raw:
            if _pts and len(_pts[-1]) < 25:
                _pts[-1] = f"{_pts[-1]} {_s}"
            else:
                _pts.append(_s)
        _why_html = '<ul class="swhy">' + "".join(f"<li>{e(p)}</li>" for p in _pts) + "</ul>"
    inner = (
        f'<div class="glow"></div>'
        f'<div class="shero">' + (f'<img class="swm" src="{_wm}">' if _wm else '') + '</div>'
        f'<div class="stag st-{_tier}">{_tlabel}</div>'   # TYPE écrit sous le logo comme une signature (user 2026-08-17)
        f'<div class="slg">{e(_lg)}</div>'
        f'<div class="stms">'
        f'<div class="stm">{_team_logo_html(home, d.get("home_logo"), e)}<span class="stn">{e(home)}</span></div>'
        f'<div class="stc">{e(_hh)}</div>'
        f'<div class="stm">{_team_logo_html(away, d.get("away_logo"), e)}<span class="stn">{e(away)}</span></div>'
        f'</div>'
        f'<div class="sbet">'                             # CADRE « partie Paris » (comme le site, user 2026-08-17)
        f'<div class="spk">{e(d.get("pick", ""))}</div>'
        + (f'<div class="sgl">{e(d.get("gloss"))}</div>' if d.get("gloss") else "")
        + f'<div class="vgrid">{"".join(cells)}</div>'   # GRILLE d'abord
        + f'{_bar}'                                       # BARRE SOUS les stats (comme le site, user 2026-08-17)
        + '</div>'
        + _why_html)                                      # ANALYSE COMPLÈTE en puces, sous le cadre
    return (f"<!doctype html><html><head><meta charset=utf-8><style>{_CSS}{_CSS_SIMPLE}</style></head>"
            f'<body><div class="card scard">{inner}</div></body></html>')


def _result_simple_card_html(d: dict) -> str:
    """Carte RÉSULTAT d'un pari SIMPLE façon SITE (user 2026-08-17) : logo BETSFIX + badge Gagné/Perdu,
    ligue+pays, logos + SCORE au centre + « Terminé », le pari + sa glose."""
    def e(x):
        return _html.escape(re.sub(r"\s*\(F\)", "", str(x)))
    _wm = _banner_uri(d.get("emoji", ""))
    sp = d.get("simple") or {}
    mark = sp.get("mark") or ""
    _rlabel, _rcls = {"won": ("GAGNÉ", "rb-w"), "lost": ("PERDU", "rb-l"),
                      "push": ("REMBOURSÉ", "rb-n"), "void": ("REMBOURSÉ", "rb-n")}.get(mark, ("", "rb-n"))
    home, away = str(d.get("home") or ""), str(d.get("away") or "")
    _cat = str(d.get("cat", ""))
    _comp = _cat.split(" · ", 1)[1] if " · " in _cat else _cat
    _lg = " • ".join(x for x in (str(d.get("country") or ""), _comp) if x).upper()
    _score = str(d.get("score") or "").strip()
    _center = (f'<span class="rsc"><b>{e(_score)}</b><span class="rfin">Terminé</span></span>'
               if _score else '<span class="rfin">Terminé</span>')
    _gl = f'<div class="sgl">{e(sp.get("gloss"))}</div>' if sp.get("gloss") else ""
    _ccls = "won" if mark == "won" else ("lost" if mark == "lost" else "push" if mark in ("push", "void") else "")
    inner = (
        f'<div class="glow"></div>'
        + f'<div class="shero">' + (f'<img class="swm" src="{_wm}">' if _wm else '') + '</div>'
        + (f'<div class="stag {_rcls}">{_rlabel}</div>' if _rlabel else "")   # résultat écrit sous le logo (signature)
        + f'<div class="slg">{e(_lg)}</div>'
        f'<div class="stms">'
        f'<div class="stm">{_team_logo_html(home, d.get("home_logo"), e)}<span class="stn">{e(home)}</span></div>'
        f'<div class="stc">{_center}</div>'
        f'<div class="stm">{_team_logo_html(away, d.get("away_logo"), e)}<span class="stn">{e(away)}</span></div>'
        f'</div>'
        f'<div class="spk">{e(sp.get("label", ""))}</div>{_gl}')
    return (f"<!doctype html><html><head><meta charset=utf-8><style>{_CSS}{_CSS_SIMPLE}</style></head>"
            f'<body><div class="card scard {_ccls}">{inner}</div></body></html>')


def _card_html(d: dict) -> str:
    if d.get("type") == "simple":                       # PARI SIMPLE : design SITE dédié (user 2026-08-17)
        return _simple_card_html(d)
    if d.get("type") == "result" and d.get("simple") and not d.get("combo"):   # RÉSULTAT simple : design site
        return _result_simple_card_html(d)
    # Échappe + retire le suffixe « (F) » des équipes féminines (WNBA) — affichage seulement.
    def e(x):
        return _html.escape(re.sub(r"\s*\(F\)", "", str(x)))
    _wm = _banner_uri(d.get("emoji", ""))
    _wm_img = f'<img class="wm" src="{_wm}">' if _wm else ''
    _wm_hero = f'<div class="hero">{_wm_img}</div>' if _wm_img else ''
    _icon = _sport_icon(d.get("emoji", ""))
    _cardcls = ""
    # Titre du sport SUR UNE SEULE LIGNE : SPORT (gros, en avant) puis COMPÉTITION en PLUS PETIT à la
    # suite (casse normale) -> pas de retour à la ligne, tout tient sur une ligne. cat = « Sport · Compét. ».
    _cat = str(d.get("cat", ""))
    _sp, _cp = _cat.split(" · ", 1) if " · " in _cat else (_cat, "")
    inner = (f'<div class="glow"></div>'
             f'{_wm_hero}'
             f'<div class="top">{_icon}<span class="topsport">{e(_sp)}</span>'
             + (f'<span class="topcomp">· {e(_cp)}</span>' if _cp else "")
             + '</div>'
             + f'<div class="match">{e(d.get("match",""))}</div>'
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
        if _verdict == "void":                         # remboursé (void) = MÊME rendu visuel que push :
            _verdict = "push"                          # bannière « ➖ Remboursé » + accent gris (sinon
        #                                                aucune bannière ni CSS .card.void -> carte muette).
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
        if d.get("synth"):                             # synthèse du combiné (corrélation) — pro, en tête
            inner += f'<div class="synth">{e(d["synth"])}</div>'
        inner += f'<div class="beth">Combiné · {len(d.get("legs",[]))} sélections</div>'
        for leg in d.get("legs", []):                  # legs = (marché, pick, cote[, why])
            mkt, pk, cote = leg[0], leg[1], leg[2]
            why = leg[3] if len(leg) > 3 else ""
            inner += (f'<div class="leg">{_selh(e, mkt, pk)}'
                      f'<span class="o">{e(str(cote))}</span></div>')
            if why:                                    # ANALYSE de la jambe (comme l'app)
                inner += f'<div class="legwhy">{e(why)}</div>'
        inner += (f'<div class="cote"><span class="l">Cote combinée</span>'
                  f'<span class="v">{e(str(d.get("cote","")))}</span></div>')
    else:
        inner += f'<div class="leg">{_selh(e, d.get("market",""), d.get("pick",""))}</div>'
        if d.get("why"):                               # ANALYSE du pari simple (comme l'app)
            inner += f'<div class="legwhy">{e(d["why"])}</div>'
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
        raise RuntimeError("Chrome introuvable")   # PAS SystemExit (BaseException échappe à except Exception -> tuerait le scan/règlement)
    return found


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


_CARD_BG = (8, 12, 20)          # fond bleu-noir (coins arrondis + marges de normalisation)
_CARD_RATIO = 1.3               # hauteur/largeur VISÉ pour TOUTES les cartes -> même largeur sur Telegram


def _normalize_card(png: str) -> None:
    """Uniformise l'affichage Telegram : (1) APLATIT l'alpha sur un fond BLEU-NOIR — les coins arrondis
    (transparents) deviennent sombres au lieu de BLANCS (Telegram compose l'alpha sur blanc) ; (2) normalise
    l'image à un RATIO FIXE en ajoutant du fond bleu-noir (padding vertical si la carte est plus courte,
    horizontal si plus haute) -> toutes les cartes ont le MÊME ratio donc la MÊME largeur d'affichage, seule
    la hauteur du CONTENU change. No-op si PIL absent / erreur (jamais bloquant pour le scan/règlement)."""
    try:
        from PIL import Image
    except Exception:
        return
    try:
        card = Image.open(png).convert("RGBA")
        w, h = card.size
        if h / w <= _CARD_RATIO:
            cw, ch = w, round(w * _CARD_RATIO)          # compléter en HAUTEUR (carte pleine largeur)
        else:
            cw, ch = round(h / _CARD_RATIO), h          # compléter en LARGEUR (carte plus haute que le ratio)
        canvas = Image.new("RGBA", (cw, ch), _CARD_BG + (255,))
        canvas.alpha_composite(card, ((cw - w) // 2, (ch - h) // 2))
        canvas.convert("RGB").save(png)                 # RGB (sans alpha) -> Telegram n'ajoute pas de blanc
    except Exception:
        pass


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
            raise RuntimeError("CDP : aucun onglet")   # repli texte au lieu de tuer le process appelant
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
            # Boîte EXACTE de la carte -> capture CLIPPÉE dessus : le PNG épouse la carte, AUCUN bord noir.
            # La carte a une largeur FIXE (920px) -> tous les tickets font la MÊME largeur (peu importe le
            # sport / la longueur des textes) ; seule la HAUTEUR varie avec le contenu.
            r = await cmd("Runtime.evaluate", {"expression":
                "(function(){var c=document.querySelector('.card');var b=c.getBoundingClientRect();"
                "return JSON.stringify({x:b.left,y:b.top,w:b.width,h:b.height});})()",
                "returnByValue": True})
            box = json.loads(r["result"]["result"]["value"])
            vw = int(box["x"] + box["w"]) + 8          # viewport assez grand pour rendre la carte entière
            vh = int(box["y"] + box["h"]) + 8          # (position incluse) avant de la clipper au pixel près
            await cmd("Emulation.setDeviceMetricsOverride",
                      {"width": vw, "height": vh, "deviceScaleFactor": 2, "mobile": False})
            # Fond TRANSPARENT (alpha 0) : sinon Chrome comble les coins ARRONDIS (hors carte) en BLANC.
            # Avec l'alpha, les coins deviennent transparents et se fondent dans le fond du chat Telegram.
            await cmd("Emulation.setDefaultBackgroundColorOverride",
                      {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
            await asyncio.sleep(0.2)
            # clip.scale=1 : la haute résolution vient DÉJÀ du deviceScaleFactor=2 (sinon on double l'échelle)
            shot = await cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                "clip": {"x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"], "scale": 1}})
            os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
            with open(out_png, "wb") as f:
                f.write(base64.b64decode(shot["result"]["data"]))
        _normalize_card(out_png)       # fond bleu-noir (coins) + ratio fixe (même largeur pour tous)
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
