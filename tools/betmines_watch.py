"""Suivi « info seule » du DOUBLE quotidien de Betmines — demande user 2026-07-23.

But : MESURER leur taux de réussite réel par nous-mêmes (« leur taux de réussite est pas mal et
j'aimerais m'y intéresser ») avant d'envisager de s'en inspirer. On ne copie RIEN dans nos pronos :
on capture leur combiné « sûr » (le Double, ~2 jambes over buts, cote ~1.9) et on le règle.

TOTALEMENT ISOLÉ (même politique que provisional/combo_daily) : écrit UNIQUEMENT
`data/betmines_track.json` — jamais sidecars / stat_bet / ROI / calibration.

Règlement : nos sources d'abord (Flashscore puis LiveScore) ; REPLI = le score affiché par la page
Betmines elle-même (leurs ligues sont souvent obscures — D2 islandaise, réserves MLS — absentes de
nos sources ; le score de la row est alors accepté, tracé `score_src: "betmines"`).

Usage : python tools/betmines_watch.py   (capture le Double du jour + règle les entrées passées).
Appelé 1×/jour en fin de scan_daily (best-effort : ne casse JAMAIS le scan).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACK = os.path.join(_ROOT, "data", "betmines_track.json")
URL = "https://betmines.com/fr/paris-du-jour-football"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_MOIS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
         "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
         "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
         "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12}


def _load() -> dict:
    try:
        with open(TRACK, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    tmp = TRACK + ".tmp"
    os.makedirs(os.path.dirname(TRACK), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TRACK)


def _fetch() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": _UA,
                                               "Accept-Language": "fr-FR,fr;q=0.9"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "ignore")


def _parse_double(html: str) -> dict | None:
    """Extrait la section « Double » (le combiné SÛR — pas le « Risque ») : date + jambes + cote totale.
    Une jambe = {comp, home, away, market, line, cote, score(éventuel, si la page l'affiche déjà)}."""
    # Découpe : section Double = entre « >Double< » et la 1re « Cote totale » qui suit.
    msec = re.search(r">\s*Double\s*<(.*?)Cote\s+totale\s*:?\s*(?:</?[^>]*>\s*)*([\d.,]+)",
                     html, re.S | re.I)
    if not msec:
        return None
    sec, total = msec.group(1), msec.group(2)
    # Date du bloc (« 23 de julio de 2026 » ou format FR) — repli : aujourd'hui UTC.
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mdt = re.search(r"(\d{1,2})\s+de\s+([a-zéû]+)\s+de\s+(\d{4})|(\d{1,2})\s+([a-zéû]+)\s+(\d{4})",
                    sec, re.I)
    if mdt:
        g = mdt.groups()
        dd, mois, yy = (g[0], g[1], g[2]) if g[0] else (g[3], g[4], g[5])
        mn = _MOIS.get((mois or "").lower())
        if mn:
            day = f"{yy}-{mn:02d}-{int(dd):02d}"
    legs, comp = [], ""
    # Parse par SEGMENTS (robuste aux tags/icônes) : chaque chunk de row -> liste de textes non vides, puis
    # interprétation séquentielle [home, away, (s1, s2)?, marché…, ±X.5, cote]. La COMPÉTITION de la row
    # SUIVANTE apparaît en QUEUE du chunk courant (structure Nuxt) -> balayage séquentiel.
    parts = re.split(r'<div[^>]*class="daily-bet-fixture-row[^"]*"[^>]*>', sec)
    for i, chunk in enumerate(parts):
        txt = re.sub(r"<[^>]+>", "|", chunk)
        segs = [t.strip() for t in re.sub(r"\s+", " ", txt).split("|") if t.strip()]
        if i > 0 and segs:
            # ligne éventuelle : Home Away [s1 s2] <mots du marché> ±X.5 cote [comp suivante…]
            nums = [(j, s) for j, s in enumerate(segs) if re.fullmatch(r"[+\-]\d+(?:[.,]\d+)?", s)]
            if nums and len(segs) >= 4:
                jl, line_s = nums[0]
                cote_s = segs[jl + 1] if jl + 1 < len(segs) else ""
                if re.fullmatch(r"\d+(?:[.,]\d+)?", cote_s):
                    head = segs[:jl]                   # [home, away, (s1, s2)?, marché…]
                    score = None
                    digits = [(k, s) for k, s in enumerate(head) if re.fullmatch(r"\d{1,2}", s)]
                    if len(digits) >= 2:               # score déjà affiché (match joué)
                        score = f"{digits[0][1]}-{digits[1][1]}"
                        market = " ".join(head[digits[1][0] + 1:]).strip()
                        home_away = head[:digits[0][0]]
                    else:
                        market = head[-1] if head else ""
                        home_away = head[:-1]
                    if len(home_away) >= 2:
                        legs.append({"comp": comp, "home": home_away[0], "away": home_away[1],
                                     "score": score, "market": market or "Nombre de buts",
                                     "line": float(line_s.replace(",", ".")),
                                     "cote": float(cote_s.replace(",", ".")), "result": None})
        # compétition annonçant la row suivante = dernier segment « Xxx - Yyy » sans chiffre
        for seg in reversed(segs):
            if re.fullmatch(r"[^\d]{3,60}", seg) and (" - " in seg or "League" in seg):
                comp = seg
                break
    if not legs:
        return None
    try:
        tot = float(total.replace(",", "."))
    except ValueError:
        tot = None
    return {"date": day, "legs": legs, "total_odds": tot, "result": None,
            "captured": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _settle_leg(leg: dict) -> None:
    """Règle une jambe « Nombre de buts ±X.5 » : total buts vs ligne. Sources : Flashscore -> LiveScore ->
    REPLI score de la page Betmines (leurs ligues obscures manquent souvent chez nous)."""
    if leg.get("result") in ("won", "lost", "push"):
        return
    total = None
    q = {"home": leg.get("home", ""), "away": leg.get("away", ""), "sofa_id": ""}
    try:
        from app import flashscore, livescore
        sc = flashscore.final_score("foot", q) or livescore.final_score("foot", q)
        if sc and sc.get("home") is not None and sc.get("away") is not None:
            total = int(sc["home"]) + int(sc["away"])
            leg["score"] = f'{sc["home"]}-{sc["away"]}'
            leg["score_src"] = "sources"
    except Exception:
        pass
    if total is None and leg.get("score"):            # repli : score déjà affiché par la page Betmines
        try:
            h, a = leg["score"].split("-")
            total = int(h) + int(a)
            leg.setdefault("score_src", "betmines")
        except (ValueError, AttributeError):
            total = None
    if total is None:
        return
    ln = leg.get("line") or 0
    if ln > 0:                                        # « +X.5 » = over
        leg["result"] = "won" if total > ln else "lost"
    else:                                             # « -X.5 » = under
        leg["result"] = "won" if total < abs(ln) else "lost"


def run(force: bool = False) -> None:
    d = _load()
    # THROTTLE 6 h (hors --force) : appelé aussi par la boucle reconcile (10 min) pour rattraper un Double
    # publié APRÈS le scan de 09 h — sans marteler leur site (max ~4 captures/jour).
    _meta = d.get("_meta") or {}
    if not force:
        try:
            last = datetime.fromisoformat(_meta.get("last_run", "2000-01-01T00:00:00+00:00"))
            if (datetime.now(timezone.utc) - last).total_seconds() < 6 * 3600:
                return
        except ValueError:
            pass
    _meta["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    d["_meta"] = _meta
    # 1) CAPTURE du Double du jour (idempotent : une entrée par date, jamais réécrite si déjà réglée).
    try:
        dbl = _parse_double(_fetch())
    except Exception as exc:                          # réseau/anti-bot : on garde le suivi existant
        print(f"betmines: capture impossible ({exc}) — règlement seul")
        dbl = None
    if dbl and (dbl["date"] not in d or d[dbl["date"]].get("result") is None):
        prev = d.get(dbl["date"]) or {}
        # préserve les résultats déjà réglés d'une capture antérieure du même jour
        for old in (prev.get("legs") or []):
            for leg in dbl["legs"]:
                if (leg["home"], leg["away"]) == (old.get("home"), old.get("away")) and old.get("result"):
                    leg.update({k: old[k] for k in ("result", "score", "score_src") if k in old})
        d[dbl["date"]] = dbl
        print(f"betmines: Double {dbl['date']} capturé ({len(dbl['legs'])} jambes @ {dbl['total_odds']})")
    # 2) RÈGLEMENT des jambes en attente + verdict du combiné (toutes gagnées = won ; ≥1 perdue = lost).
    for day, cb in d.items():
        if not isinstance(cb, dict) or cb.get("result") in ("won", "lost"):
            continue
        for leg in cb.get("legs") or []:
            _settle_leg(leg)
        res = [leg.get("result") for leg in cb.get("legs") or []]
        if res and all(r == "won" for r in res):
            cb["result"] = "won"
        elif any(r == "lost" for r in res):
            cb["result"] = "lost"
    _save(d)
    # 3) BILAN courant (leur taux de réussite MESURÉ par nous).
    done = [c for c in d.values() if isinstance(c, dict) and c.get("result") in ("won", "lost")]
    if done:
        w = sum(1 for c in done if c["result"] == "won")
        pnl = sum((c.get("total_odds") or 0) - 1 if c["result"] == "won" else -1 for c in done)
        print(f"betmines: bilan {w}/{len(done)} Doubles gagnés · P&L simulé {pnl:+.2f} (mise plate)")


if __name__ == "__main__":
    run(force="--force" in sys.argv)
