"""Back-test & calibration du modèle de classement (Python pur, sans dépendances).

Collecte des matchs RG historiques (ATP+WTA, plusieurs saisons) via SofaScore,
puis calibre P(victoire) = sigmoid(b0 + b1 * (g(rank_adv) - g(rank_joueur)))
par régression logistique (Newton-Raphson). Évalue en validation croisée :
log-loss, score de Brier, précision, et table de calibration.

Lancement :  python tools/backtest.py
"""

from __future__ import annotations

import math
import httpx

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sofascore.com/",
     "Origin": "https://www.sofascore.com"}
B = "https://api.sofascore.com/api/v1"
TOURNAMENTS = {"atp": 2480, "wta": 2483}


def _get(client, path):
    try:
        r = client.get(path, timeout=25)
        if r.status_code != 200:
            return None
        return r.json()
    except httpx.HTTPError:
        return None


def collect():
    """Retourne une liste de (rank_home, rank_away, home_won:0/1)."""
    rows = []
    with httpx.Client(base_url=B, headers=H) as client:
        for tour, tid in TOURNAMENTS.items():
            seasons = (_get(client, f"/unique-tournament/{tid}/seasons") or {}).get("seasons", [])
            for s in seasons[:8]:  # ~8 dernières éditions
                sid = s["id"]
                for page in range(6):
                    data = _get(client, f"/unique-tournament/{tid}/season/{sid}/events/last/{page}")
                    if not data:
                        break
                    for ev in data.get("events", []) or []:
                        st = (ev.get("status") or {}).get("type")
                        wc = ev.get("winnerCode")
                        rh = (ev.get("homeTeam") or {}).get("ranking")
                        ra = (ev.get("awayTeam") or {}).get("ranking")
                        if st == "finished" and wc in (1, 2) and rh and ra:
                            rows.append((rh, ra, 1 if wc == 1 else 0))
                    if not data.get("hasNextPage"):
                        break
    return rows


def feat(rh, ra):
    """x > 0 quand le joueur 'home' est mieux classé (rang plus petit)."""
    return math.log(ra) - math.log(rh)


def sigmoid(z):
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1 / (1 + math.exp(-z))


def fit_logistic(data, iters=50):
    """Newton-Raphson pour P(y=1)=sigmoid(b0+b1*x). data: liste de (x, y)."""
    b0 = b1 = 0.0
    for _ in range(iters):
        g0 = g1 = 0.0
        h00 = h01 = h11 = 0.0
        for x, y in data:
            p = sigmoid(b0 + b1 * x)
            err = p - y
            g0 += err
            g1 += err * x
            w = p * (1 - p)
            h00 += w
            h01 += w * x
            h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        b0 -= (h11 * g0 - h01 * g1) / det
        b1 -= (-h01 * g0 + h00 * g1) / det
    return b0, b1


def log_loss(data, b0, b1):
    s = 0.0
    for x, y in data:
        p = min(max(sigmoid(b0 + b1 * x), 1e-12), 1 - 1e-12)
        s += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return s / len(data)


def brier(data, b0, b1):
    return sum((sigmoid(b0 + b1 * x) - y) ** 2 for x, y in data) / len(data)


def accuracy(data, b0, b1):
    return sum(1 for x, y in data if (sigmoid(b0 + b1 * x) >= 0.5) == (y == 1)) / len(data)


def calibration_table(data, b0, b1, bins=5):
    buckets = [[0, 0.0, 0] for _ in range(bins)]  # [n, sum_p, wins]
    for x, y in data:
        p = sigmoid(b0 + b1 * x)
        i = min(int(p * bins), bins - 1)
        buckets[i][0] += 1
        buckets[i][1] += p
        buckets[i][2] += y
    out = []
    for i, (n, sp, w) in enumerate(buckets):
        if n:
            out.append((f"{i/bins:.0%}-{(i+1)/bins:.0%}", n, sp / n, w / n))
    return out


def main():
    print("Collecte des matchs historiques (peut prendre ~30s)...")
    rows = collect()
    print(f"Dataset : {len(rows)} matchs avec classements + résultat\n")

    feats = [(feat(rh, ra), y) for rh, ra, y in rows]

    # Validation croisée par moitié alternée (déterministe, pas de hasard)
    train = [d for i, d in enumerate(feats) if i % 2 == 0]
    test = [d for i, d in enumerate(feats) if i % 2 == 1]
    b0, b1 = fit_logistic(train)
    print(f"Coefficients calibrés (sur train) : b0={b0:.4f}  b1={b1:.4f}")
    print(f"  (b0~0 attendu : pas d'avantage 'home' au tennis)\n")
    print("=== MODÈLE CALIBRÉ (évalué sur test) ===")
    print(f"  log-loss : {log_loss(test, b0, b1):.4f}  (plus bas = mieux)")
    print(f"  Brier    : {brier(test, b0, b1):.4f}")
    print(f"  précision: {accuracy(test, b0, b1):.1%}")

    # Baseline : ancien modèle Elo fait main (2200-400*log10(rank), /400)
    def elo_pred(x):  # x = ln(ra)-ln(rh) ; reconstruit via ratings
        return x  # placeholder, comparé séparément ci-dessous
    print("\n=== Référence : modèle 'naïf' (favori du classement = 100%) ===")
    naive_ll = 0.0
    for x, y in test:
        p = 0.999 if x > 0 else 0.001
        p = min(max(p, 1e-12), 1 - 1e-12)
        naive_ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    print(f"  log-loss (favori certain) : {naive_ll/len(test):.4f}  (catastrophique = surconfiance)")

    print("\n=== Calibration (modèle calibré, sur tout le dataset) ===")
    bf0, bf1 = fit_logistic(feats)
    print(f"  Coefficients FINAUX (tout le dataset) : b0={bf0:.4f}  b1={bf1:.4f}")
    print(f"  {'bucket':>10} {'n':>5} {'proba_moy':>10} {'taux_reel':>10}")
    for label, n, pmean, real in calibration_table(feats, bf0, bf1):
        print(f"  {label:>10} {n:>5} {pmean:>10.1%} {real:>10.1%}")

    print(f"\n>>> À intégrer dans le modèle : RANK_B0={bf0:.4f}, RANK_B1={bf1:.4f}")


if __name__ == "__main__":
    main()
