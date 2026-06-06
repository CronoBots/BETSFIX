"""Génère les analyses « analyste » des matchs IMPORTANTS (top-N/sport par profondeur de marché)
en pilotant Claude en HEADLESS (`claude -p`) sur l'abonnement Pro Max.

⚠️ DOIT tourner en session `vince` (où Claude est connecté), PAS dans le service API (SYSTEM,
non authentifié). Écrit chaque analyse dans data/analyses/{sport}_{id}.md (cache 6 h).

Méthodo : confidence-first (classer les paris par CHANCE DE PASSER vs cotes Unibet réelles),
faits recherchés sur le web par Claude (≥2 sources), jamais inventés. Cf. mémoire projet.

Usage (RUN DE MESURE d'abord, petit) :
    python tools/generate_analyses.py --sport foot --top 1
    python tools/generate_analyses.py --sport foot,tennis,basket --top 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.match_select import UNIBET_B, UNIBET_PARAMS, fetch_important  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "analyses")
UA = {"User-Agent": "Mozilla/5.0"}
CACHE_HOURS = 6

# Marchés à JETER du dossier (bruit) : on garde résultat/totaux/BTTS/handicaps/mi-temps.
NOISE = ("corner", "ntervalle", "ntervalle", "0:00", "10:00", "14:59", "Premier buteur",
         "Premier but", "Score exact", "Score Exact", "Asiatique", "Marque au moins",
         "Pari remboursé", "buteur", "2ème mi-temps", "2e mi-temps", "remboursé")

METHODO = (
    "Tu es mon analyste paris sportifs. Méthodo STRICTE :\n"
    "1. FAITS d'abord : pour CHAQUE équipe/joueur, forme récente (5 derniers), H2H, "
    "blessés/absents, contexte (domicile, amical vs compétition, surface). RECHERCHE WEB ces "
    "faits sur >=2 sources, cite-les, flague l'incertitude, n'invente JAMAIS (si pas sûr, dis-le). "
    "Adapte les stats au sport.\n"
    "2. Classe les paris par CHANCE DE PASSER d'après les stats (PAS par edge/value contrarien).\n"
    "3. Compare aux COTES UNIBET réelles fournies -> meilleur compromis sûreté/rendement "
    "(pas du 1.04 stérile).\n"
    "4. VERDICT : le plus sûr + le meilleur compromis ; dis \"SKIP\" si rien ne vaut le coup. "
    "Rappelle de ne jamais tout miser sur un ticket. Sois concis et direct, en français.\n\n"
)


STORE_FILE = {"foot": "tracking_foot.json", "tennis": "tracking.json",
              "basket": "tracking_basket.json"}


def _load_store(sport: str) -> dict:
    try:
        with open(os.path.join(ROOT, "data", STORE_FILE[sport]), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, KeyError):
        return {}


def _norm(s: str) -> set:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z]+", s) if len(t) >= 3}


def _fiche_id(sport: str, match: dict, store: dict) -> str | None:
    """ID utilisé par la FICHE pour lier l'analyse. Foot : id Unibet (= clé du store, mappée via
    match_id côté fiche). Tennis/basket : id Sofa (clé du store), retrouvé par correspondance de
    noms UNIQUE (sinon None -> on ne génère pas, jamais de mauvaise liaison)."""
    if sport == "foot":
        return str(match["id"])
    mh, ma = _norm(match.get("home")), _norm(match.get("away"))
    if not mh or not ma:
        return None
    hits = []
    for k, r in store.items():
        rh, ra = _norm(r.get("home")), _norm(r.get("away"))
        if (rh & mh and ra & ma) or (rh & ma and ra & mh):
            hits.append(str(k))
    return hits[0] if len(hits) == 1 else None


def _fresh(path: str) -> bool:
    try:
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        return age_h < CACHE_HOURS
    except OSError:
        return False


async def build_dossier(client: httpx.AsyncClient, match: dict) -> str | None:
    """Dossier compact : marchés Unibet utiles (hors bruit), cotes réelles. None si indispo."""
    try:
        r = await client.get(f"{UNIBET_B}/betoffer/event/{match['id']}.json",
                             params=UNIBET_PARAMS, headers=UA)
        bo = r.json()
    except Exception:
        return None
    lines = []
    for b in bo.get("betOffers", []) or []:
        crit = (b.get("criterion") or {}).get("label", "")
        if not crit or any(s in crit for s in NOISE):
            continue
        outs = []
        for o in b.get("outcomes") or []:
            lbl = o.get("label") or o.get("englishLabel") or "?"
            ln = o.get("line")
            lns = f" {ln / 1000:g}" if ln is not None else ""
            outs.append(f"{lbl}{lns}={o.get('odds', 0) / 1000:.2f}")
        if outs:
            lines.append(f"- {crit}: " + " | ".join(outs))
        if len(lines) >= 22:
            break
    if not lines:
        return None
    return (f"MATCH: {match['name']} ({match['comp']}, coup d'envoi {match['start']})\n"
            "COTES UNIBET BELGIQUE REELLES (n'invente AUCUNE cote) :\n" + "\n".join(lines))


def run_claude(prompt: str, timeout: int = 360) -> str:
    """Lance Claude en headless sur l'abonnement et renvoie l'analyse (stdout)."""
    exe = shutil.which("claude") or "claude"
    p = subprocess.run([exe, "-p", "--dangerously-skip-permissions"], input=prompt,
                       text=True, capture_output=True, timeout=timeout, encoding="utf-8")
    return (p.stdout or "").strip()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="foot", help="foot,tennis,basket (séparés par virgule)")
    ap.add_argument("--top", type=int, default=5, help="top N matchs par sport (défaut lean : 5)")
    ap.add_argument("--force", action="store_true", help="ignore le cache 6 h")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sports = [s.strip() for s in args.sport.split(",") if s.strip()]
    total_t0 = time.time()
    n_gen = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for sport in sports:
            try:
                top = await fetch_important(sport, args.top, client)
            except Exception as e:
                print(f"[{sport}] sélection échouée : {e}")
                continue
            store = _load_store(sport)
            print(f"[{sport}] {len(top)} matchs sélectionnés (profondeur de marché).")
            for m in top:
                fid = _fiche_id(sport, m, store)   # id que la fiche utilise pour lier l'analyse
                if not fid:
                    print(f"  · {m['name']} : non lié à une fiche {sport} (pas dans le store), on saute.")
                    continue
                path = os.path.join(OUT, f"{sport}_{fid}.md")
                if not args.force and _fresh(path):
                    print(f"  · {m['name']} : analyse fraîche en cache, on saute.")
                    continue
                doss = await build_dossier(client, m)
                if not doss:
                    print(f"  · {m['name']} : pas de marchés exploitables, on saute.")
                    continue
                t0 = time.time()
                try:
                    analysis = run_claude(METHODO + doss)
                except subprocess.TimeoutExpired:
                    print(f"  ✗ {m['name']} : timeout Claude.")
                    continue
                dt = time.time() - t0
                if not analysis:
                    print(f"  ✗ {m['name']} : sortie vide.")
                    continue
                header = (f"<!-- généré {datetime.now(timezone.utc).isoformat()} · {dt:.0f}s -->\n"
                          f"# {m['name']} — {m['comp']}\n\n")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(header + analysis + "\n")
                n_gen += 1
                print(f"  ✓ {m['name']} : {len(analysis)} car. en {dt:.0f}s -> {os.path.basename(path)}")
    print(f"\nTerminé : {n_gen} analyse(s) générée(s) en {time.time() - total_t0:.0f}s. Dossier : {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
