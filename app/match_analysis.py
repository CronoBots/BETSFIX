"""Analyse RÉDIGÉE d'un match (3 sports), « courte & percutante ».

Deux moteurs, même entrée (`brief`) :
  • GRATUIT (par défaut) : `_templated()` compose un texte d'expert à partir de NOS
    données (favori, écart, forme, surface, h2h, value/cote, public). Déterministe,
    instantané, crédible par construction (ne dit que ce que les chiffres disent).
  • CLAUDE (optionnel) : si `settings.anthropic_api_key` est défini, `_claude()` rédige
    une prose plus fluide via l'API Anthropic (Haiku par défaut). Repli automatique sur
    le templaté en cas d'absence de clé ou d'erreur. Aucune nouvelle dépendance (httpx).

`brief` (toutes les clés sont optionnelles, le générateur gère les manques) :
  sport, home, away, favorite, underdog, fav_prob, fav_odds, confidence,
  value{name,odds,edge}, surface, surface_edge, fav_form_wins, fav_form_n,
  h2h_fav, h2h_opp, margin, public_fav, match_id
"""

from __future__ import annotations

import html


def _pick(variants: list[str], seed: int) -> str:
    return variants[seed % len(variants)] if variants else ""


def _support(b: dict, seed: int) -> str:
    """1 à 2 appuis saillants (forme / surface / h2h / marge) -> phrase courte."""
    fav, dog = b.get("favorite") or "", b.get("underdog") or ""
    bits: list[str] = []
    w, n = b.get("fav_form_wins"), b.get("fav_form_n")
    if w is not None and n:
        if w == n and n >= 3:
            bits.append(f"{fav} reste sur {w} victoires de rang")
        elif w / n >= 0.6:
            bits.append(f"{fav} est en forme ({w} victoires sur {n})")
        elif w / n <= 0.4:
            bits.append(f"{fav} traverse un creux ({w} victoire{'s' if w > 1 else ''} sur {n})")
    surf = b.get("surface")
    if surf and b.get("surface_edge"):
        surf_fr = {"terre": "la terre battue", "dur": "le dur", "gazon": "le gazon"}.get(surf, surf)
        bits.append(f"{surf_fr} lui réussit")
    hf, ho = b.get("h2h_fav"), b.get("h2h_opp")
    if hf is not None and ho is not None and (hf + ho) > 0:
        if hf > ho:
            bits.append(f"l'historique lui est favorable ({hf}-{ho})")
        elif ho > hf:
            bits.append(f"l'historique penche pourtant côté {dog} ({ho}-{hf})")
    m = b.get("margin")
    if m:
        bits.append(f"un écart d'environ {abs(round(m))} points est attendu")
    if not bits:
        return ""
    # On en garde au plus 2 (concision), en variant le point de départ selon le match.
    start = seed % len(bits)
    picked = [bits[start]] + ([bits[(start + 1) % len(bits)]] if len(bits) > 1 else [])
    phrase = " ; ".join(picked)
    return phrase[0].upper() + phrase[1:] + "."


def _templated(b: dict) -> str:
    """Analyse courte (3-4 phrases) à partir des données — déterministe (pas d'aléa)."""
    fav, dog = b.get("favorite") or "", b.get("underdog") or ""
    fp = round((b.get("fav_prob") or 0) * 100)
    seed = int(b.get("match_id") or 0)
    conf = b.get("confidence") or "moyenne"
    s: list[str] = []

    # 1) Force
    if fp and fp < 53:
        s.append(_pick([f"Match très ouvert : {fav} et {dog} se tiennent ({fp} %/{100 - fp} %).",
                        f"Affiche serrée, sans favori net ({fp} %/{100 - fp} %)."], seed))
    elif fp >= 65:
        s.append(_pick([f"{fav} part large favori ({fp} %).",
                        f"Net avantage à {fav} ({fp} % de chances)."], seed))
    elif fp:
        s.append(_pick([f"{fav} a la faveur des pronostics ({fp} %), sans gros écart.",
                        f"Léger avantage {fav} ({fp} %)."], seed))

    # 2) Appui
    sup = _support(b, seed)
    if sup:
        s.append(sup)

    # 3) Verdict de pari
    v = b.get("value")
    if v and v.get("odds"):
        edge = round((v.get("edge") or 0) * 100, 1)
        s.append(_pick([
            f"À {v['odds']}, la cote de {v['name']} paraît trop généreuse (~+{edge} %) : "
            "une value — gros gain possible, mais ça passe moins souvent.",
            f"Value repérée sur {v['name']} à {v['odds']} (~+{edge} % en notre faveur) : "
            "rentable sur la durée, jamais garanti sur un match."], seed))
    elif fp >= 65 and b.get("fav_odds"):
        s.append(_pick([f"À {b['fav_odds']}, c'est un pari de confiance : assez sûr, mais petit gain.",
                        f"Pari de confiance à {b['fav_odds']} : faible risque, faible gain."], seed))
    else:
        s.append(_pick(["Ni value ni favori vraiment net : mieux vaut s'abstenir.",
                        "Pas de pari intéressant ici — à passer."], seed))

    # 4) Garde-fous
    extra: list[str] = []
    if conf == "faible":
        extra.append("données limitées, prudence")
    pf, fprob = b.get("public_fav"), b.get("fav_prob")
    if pf is not None and fprob is not None and pf - fprob >= 0.18:
        extra.append(f"le public sur-mise sur {fav} ({round(pf * 100)} %)")
    if extra:
        msg = " ; ".join(extra)
        s.append("⚠️ " + msg[0].upper() + msg[1:] + ".")
    return " ".join(x for x in s if x)


# --------------------------------------------------------------- Claude (optionnel)
_SYSTEM = (
    "Tu es un analyste paris sportifs francophone, sobre et crédible. On te donne les "
    "DONNÉES d'un match (probabilités du modèle, forme, face-à-face, surface, cotes, value). "
    "Rédige une analyse COURTE ET PERCUTANTE en français (3-4 phrases max) basée UNIQUEMENT "
    "sur ces données. INTERDIT d'inventer des faits absents (blessures, compositions, actualité). "
    "Donne le verdict (favori/value/à éviter), 1-2 appuis chiffrés, et le risque. Pas de "
    "garantie, ton honnête. Pas de markdown, pas de titre, juste le paragraphe."
)


async def _claude(b: dict, settings) -> str | None:
    """Rédaction via l'API Anthropic (httpx). Renvoie None en cas d'échec -> repli templaté."""
    import json

    import httpx

    body = {
        "model": settings.analysis_model,
        "max_tokens": 320,
        "system": _SYSTEM,
        "messages": [{"role": "user",
                      "content": "Données du match (JSON) :\n" + json.dumps(b, ensure_ascii=False)}],
    }
    headers = {"x-api-key": settings.anthropic_api_key,
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    text = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
    return text.strip() or None


def _wrap(text: str, by_claude: bool) -> str:
    if not text:
        return ""
    src = "Claude" if by_claude else "automatique"
    note = (f'<div class="dim" style="font-size:10.5px;margin-top:7px">Analyse {src}, à partir '
            "des données du match — pas un conseil garanti.</div>")
    return (f'<h2>🧠 L\'analyse</h2><div class="banner analysis">'
            f'{html.escape(text, quote=False)}{note}</div>')


async def write_analysis(brief: dict, settings=None) -> str:
    """Renvoie le bloc HTML d'analyse : prose Claude si une clé est configurée, sinon templaté."""
    text, by_claude = None, False
    if settings and getattr(settings, "anthropic_api_key", ""):
        try:
            text = await _claude(brief, settings)
            by_claude = bool(text)
        except Exception:
            text = None
    if not text:
        text = _templated(brief)
    return _wrap(text, by_claude)
