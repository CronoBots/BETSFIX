#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tableau de bord de MONITORING (user 2026-08-29) — le catalyseur d'optimisation : on ne peut optimiser
que ce qu'on mesure. 3 panneaux, 0 risque (lecture seule) :

  A. PRODUITS DÉPLOYÉS (réel) : Confiance / Value figés (stat_bet) — n, réussite, ROI, cote, série — vs
     l'attente du backtest. Détecte tôt une divergence réel↔backtest.
  B. MATURITÉ / PROMOTION par marché : chaque marché passé au filtre CONFIANCE (curaté) et VALUE (curaté),
     avec drapeau « promouvable » (borne basse de Wilson > seuil de rentabilité, n≥25). Signal : « tel marché
     est prêt à devenir un pari joué ».
  C. CALIBRATION BRUTE marché × bande de cote : accumulation des fantômes (surtout les marchés rares :
     cartons, corners, tirs cadrés…). Montre qui mûrit vers n≥25.

Usage : python tools/monitor.py [--html data/monitor.html]   (sinon rapport texte stdout)
"""
import argparse
import collections
import glob
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import analyses as A                       # noqa: E402
import tools.backtest_confidence as B               # noqa: E402

Z = 1.96


def wilson_lb(w, n):
    if n == 0:
        return 0.0
    p = w / n
    return (p + Z * Z / (2 * n) - Z * math.sqrt((p * (1 - p) + Z * Z / (4 * n)) / n)) / (1 + Z * Z / n)


def _metrics(picks):
    """picks = liste de (result, cote). -> dict n/wins/winrate/roi/avg_cote/wilson_lb/breakeven."""
    n = len(picks)
    if not n:
        return {"n": 0}
    w = sum(1 for r, _ in picks if r == "won")
    losses = sum(1 for r, _ in picks if r == "lost")
    ret = sum((c - 1) if r == "won" else (-1 if r == "lost" else 0) for r, c in picks)
    avgc = sum(c for _, c in picks) / n
    dec = w + losses
    return {"n": n, "wins": w, "losses": losses,
            "winrate": round(100 * w / dec, 1) if dec else 0.0,
            "roi": round(100 * ret / n, 2), "avg_cote": round(avgc, 2),
            "wlb": round(100 * wilson_lb(w, dec), 1) if dec else 0.0,
            "be": round(100 / avgc, 1)}


# --------------------------------------------------------------------------- Panel A : produits déployés
def panel_deployed():
    # clé = `stat_bet.kind` RÉEL (anglais : "confidence"/"value"), pas le libellé d'affichage français.
    tiers = {"confidence": [], "value": []}
    _mids = A._montante_mids() if A.MONTANTE_ROI_ON else frozenset()   # montante OFF -> ne rien exclure
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sb = d.get("stat_bet")
        if not (isinstance(sb, dict) and sb.get("result") in ("won", "lost")):
            continue
        if d.get("roi_void") or str(d.get("id")) in _mids:
            continue
        k = sb.get("kind")
        if k in tiers:
            tiers[k].append((sb["result"], sb.get("cote") or 0))
    exp = {"confidence": "backtest 93% · cote ~1.14 · ROI ~+8%",
           "value": "backtest profil B · cote ~1.6 · ROI ~+11 à +18%"}
    lbl = {"confidence": "Confiance", "value": "Value"}
    return [{"tier": lbl[t], **_metrics(v), "attente": exp[t]} for t, v in tiers.items()]


# --------------------------------------------------------------------------- Panel B : maturité / promotion
_MARKETS_B = ["Double chance", "Handicap", "Total Under", "Total Over", "Total équipe",
              "Vainqueur", "Tirs cadrés", "Cartons", "Corners"]


def _curated(ds, market, prob_min, cote_lo, cote_hi, ev_floor, tiebreak):
    """1 pari/match : meilleur candidat de CE marché sous ces règles. -> liste (result, cote)."""
    picks = []
    for m in ds:
        c = B.select_fast(m, prob_min, cote_lo, cote_hi, frozenset([market]), ev_floor, tiebreak)
        if c and c["result"] in ("won", "lost"):
            picks.append((c["result"], c["cote"]))
    return picks


def panel_promotion(ds):
    rows = []
    for mk in _MARKETS_B:
        # règles CONFIANCE (conf>=80, cote courte, le plus sûr) et VALUE (conf>=58, cote grasse, EV>=+5%, cote haute)
        conf = _metrics(_curated(ds, mk, 80, 1.05, 1.50, -0.15, "prob"))
        val = _metrics(_curated(ds, mk, 58, 1.40, 2.30, 0.05, "cote_hi"))
        def _promo(x):
            return "OUI" if (x.get("n", 0) >= 25 and x.get("wlb", 0) > x.get("be", 100)) else \
                   ("proche" if x.get("n", 0) >= 10 and x.get("wlb", 0) > x.get("be", 100) else "non")
        rows.append({"market": mk, "conf": conf, "conf_promo": _promo(conf),
                     "val": val, "val_promo": _promo(val)})
    return rows


# --------------------------------------------------------------------------- Panel C : calibration brute
_BANDS = [("1.05–1.30", 1.05, 1.30), ("1.30–1.60", 1.30, 1.60), ("1.60–2.30", 1.60, 2.30), ("2.30+", 2.30, 99)]


def panel_calibration():
    data = collections.defaultdict(lambda: collections.defaultdict(list))
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for s in (d.get("shadow") or []):
            if not isinstance(s, dict) or s.get("result") not in ("won", "lost"):
                continue
            co = s.get("cote") or 0
            mk = A.market_of(s.get("code") or "")
            for name, lo, hi in _BANDS:
                if lo <= co <= hi:
                    data[mk][name].append((s["result"], co))
                    break
    out = []
    for mk in sorted(data, key=lambda k: -sum(len(v) for v in data[k].values())):
        bands = {name: _metrics(data[mk][name]) for name, _, _ in _BANDS if data[mk][name]}
        out.append({"market": mk, "bands": bands,
                    "total": sum(len(data[mk][b]) for b in data[mk])})
    return out


# --------------------------------------------------------------------------- rendu
def render_text():
    print("=" * 78)
    print("TABLEAU DE BORD MONITORING — BETSFIX")
    print("=" * 78)
    print("\n[A] PRODUITS DÉPLOYÉS (réel figé)")
    for r in panel_deployed():
        print("  %-9s : n=%-3s réussite=%-5s%% ROI=%+6s%% cote=%-4s | attendu: %s"
              % (r["tier"].upper(), r.get("n"), r.get("winrate"), r.get("roi"), r.get("avg_cote"), r["attente"]))
    ds = B.load_dataset()
    print("\n[B] MATURITÉ / PROMOTION par marché (curaté = 1 meilleur pari/match)")
    print("  %-14s | %-28s | %-28s" % ("MARCHÉ", "sous règles CONFIANCE", "sous règles VALUE"))
    for r in panel_promotion(ds):
        c, v = r["conf"], r["val"]
        print("  %-14s | n=%-3s %-5s%% ROI%+6s%% [%-6s] | n=%-3s %-5s%% ROI%+6s%% [%-6s]"
              % (r["market"], c.get("n", 0), c.get("winrate", 0), c.get("roi", 0), r["conf_promo"],
                 v.get("n", 0), v.get("winrate", 0), v.get("roi", 0), r["val_promo"]))
    print("\n[C] CALIBRATION BRUTE marché × bande de cote (accumulation fantômes)")
    for r in panel_calibration():
        parts = ["%s:n%d/%.0f%%" % (b, m["n"], m["winrate"]) for b, m in r["bands"].items()]
        print("  %-14s (%4d) : %s" % (r["market"], r["total"], "  ".join(parts)))


def _pill(v, ok, warn):
    c = "#22c55e" if v >= ok else ("#f59e0b" if v >= warn else "#ef4444")
    return f'<span style="color:{c};font-weight:700">{v:+.1f}%</span>'


def render_html(path):
    open(path, "w", encoding="utf-8").write(build_html())
    return path


def build_html() -> str:
    """HTML autonome du tableau de bord (string) — sert au fichier ET à la route /monitor de l'app."""
    ds = B.load_dataset()
    dep, promo, calib = panel_deployed(), panel_promotion(ds), panel_calibration()
    h = ['<h1>📊 Monitoring BETSFIX</h1>',
         '<p class="sub">Lecture seule · confiance/value déployées, maturité des marchés, calibration brute.</p>']
    # A
    h.append('<h2>🎯 Produits déployés (réel)</h2><table><tr><th>Tier</th><th>n</th><th>Réussite</th>'
             '<th>ROI</th><th>Cote</th><th>Attendu (backtest)</th></tr>')
    for r in dep:
        h.append(f'<tr><td><b>{r["tier"].upper()}</b></td><td>{r.get("n","–")}</td>'
                 f'<td>{r.get("winrate","–")}%</td><td>{_pill(r.get("roi",0),3,0)}</td>'
                 f'<td>{r.get("avg_cote","–")}</td><td class="muted">{r["attente"]}</td></tr>')
    h.append('</table>')
    # B
    h.append('<h2>🌱 Maturité / promotion par marché</h2>'
             '<p class="sub">« Curaté » = 1 meilleur pari/match sous les règles du tier. Promouvable = borne '
             'basse de Wilson &gt; seuil de rentabilité, n≥25.</p>'
             '<table><tr><th>Marché</th><th colspan=3>Règles CONFIANCE</th><th colspan=3>Règles VALUE</th></tr>'
             '<tr><th></th><th>n</th><th>réuss/ROI</th><th>promo</th><th>n</th><th>réuss/ROI</th><th>promo</th></tr>')
    for r in promo:
        c, v = r["conf"], r["val"]
        def _pr(x):
            return {"OUI": '<b style="color:#22c55e">✅ OUI</b>', "proche": '<span style="color:#f59e0b">~ proche</span>'}.get(x, '<span class="muted">non</span>')
        h.append(f'<tr><td><b>{r["market"]}</b></td>'
                 f'<td>{c.get("n",0)}</td><td>{c.get("winrate",0)}% / {_pill(c.get("roi",0),3,0)}</td><td>{_pr(r["conf_promo"])}</td>'
                 f'<td>{v.get("n",0)}</td><td>{v.get("winrate",0)}% / {_pill(v.get("roi",0),3,0)}</td><td>{_pr(r["val_promo"])}</td></tr>')
    h.append('</table>')
    # C
    h.append('<h2>👻 Calibration brute (fantômes) marché × cote</h2>'
             '<p class="sub">Accumulation par bande de cote. Les marchés rares (cartons, corners…) mûrissent '
             'vers n≥25 grâce à la couverture obligatoire.</p><table><tr><th>Marché</th><th>Total</th>'
             + "".join(f"<th>{b}</th>" for b, _, _ in _BANDS) + "</tr>")
    for r in calib:
        cells = ""
        for b, _, _ in _BANDS:
            m = r["bands"].get(b)
            cells += f'<td>{"–" if not m else f"""n{m["n"]}·{m["winrate"]}%"""}</td>'
        h.append(f'<tr><td><b>{r["market"]}</b></td><td>{r["total"]}</td>{cells}</tr>')
    h.append('</table>')
    css = ("<style>body{font-family:system-ui,-apple-system,sans-serif;background:#0b0f14;color:#e6edf3;"
           "margin:0;padding:16px;max-width:900px}h1{font-size:22px;margin:0 0 2px}h2{font-size:16px;"
           "margin:22px 0 6px;color:#9fb6cf}.sub{color:#7f8794;font-size:12px;margin:0 0 8px}"
           ".muted{color:#7f8794;font-size:12px}table{width:100%;border-collapse:collapse;font-size:13px;"
           "overflow-x:auto;display:block}th,td{padding:6px 8px;text-align:center;border-bottom:1px solid #1c2733}"
           "th{color:#9fb6cf;font-weight:600;font-size:11px;text-transform:uppercase}"
           "td:first-child,th:first-child{text-align:left}tr:hover td{background:#0f1620}</style>")
    return css + "".join(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=None)
    a = ap.parse_args()
    if a.html:
        print("HTML écrit :", render_html(a.html))
    else:
        render_text()


if __name__ == "__main__":
    main()
