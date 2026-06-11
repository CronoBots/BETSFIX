"""Sonde RÉELLE du module app/sources.py : prend les matchs importants du jour (sélection
Unibet, comme le scan) et affiche le bloc « DONNÉES MULTI-SOURCES » produit pour chacun.

Usage : python tools/probe_sources.py [--sport foot,tennis,basket] [--top 2]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import httpx  # noqa: E402

from app import sources  # noqa: E402
from app.match_select import fetch_important  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="foot,tennis,basket")
    ap.add_argument("--top", type=int, default=2)
    args = ap.parse_args()
    async with httpx.AsyncClient(timeout=20) as client:
        for sport in [s.strip() for s in args.sport.split(",") if s.strip()]:
            try:
                top = await fetch_important(sport, args.top, client, within_hours=48)
            except Exception as e:
                print(f"[{sport}] sélection échouée : {e}")
                continue
            print(f"\n========== {sport.upper()} : {len(top)} match(s) ==========")
            for m in top:
                t0 = time.time()
                block = await sources.extras(client, sport, m)
                dt = time.time() - t0
                print(f"\n--- {m.get('name')} ({m.get('comp')}, {m.get('start')}) [{dt:.1f}s] ---")
                print(block if block else "(aucune donnée multi-sources)")


if __name__ == "__main__":
    asyncio.run(main())
