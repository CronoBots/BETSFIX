"""MODE PUBLIC / MASQUAGE DES SOURCES — le stack de données (Unibet, SofaScore, Pinnacle, FotMob, ESPN,
Understat, Flashscore, LiveScore, Sportradar…) est l'AVANTAGE COMPÉTITIF de BETSFIX. Dès que l'app est
publique, personne ne doit voir ces noms. Ce module fournit UN SEUL interrupteur + une passe de
« dé-branding » appliquée au HTML sortant, UNIQUEMENT pour les visiteurs NON propriétaires.

Principe :
- `hide_sources()` : interrupteur (env `BETSFIX_HIDE_SOURCES` OU fichier `data/hide_sources.flag`).
  Par défaut OFF -> pendant la phase de test, RIEN ne change (le propriétaire voit tout).
- `debrand(html)` : (1) SUPPRIME les boutons-liens vers les fiches sources (SofaScore/Unibet) ;
  (2) neutralise les URLs de sources résiduelles ; (3) remplace les NOMS de sources (texte affiché)
  par des libellés neutres — SANS toucher aux noms de classes CSS (`lnk-bn-sofa`) ni casser la mise en page.

Le masquage se fait à la SEULE frontière de sortie (middleware), donc impossible d'oublier un endroit :
tout nouveau texte qui nommerait une source est neutralisé automatiquement côté public.
"""
from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FLAG_FILE = os.path.join(_ROOT, "data", "hide_sources.flag")
_TRUE = {"1", "true", "yes", "on", "oui"}


def hide_sources() -> bool:
    """Le mode public (masquage des sources) est-il actif ? env prioritaire, sinon fichier drapeau
    (bascule à chaud sans redémarrage : créer/supprimer `data/hide_sources.flag`)."""
    v = (os.environ.get("BETSFIX_HIDE_SOURCES") or "").strip().lower()
    if v in _TRUE:
        return True
    if v in {"0", "false", "no", "off", "non"}:
        return False
    return os.path.exists(_FLAG_FILE)


def set_flag(on: bool) -> None:
    """Bascule le fichier drapeau (le propriétaire peut activer/désactiver le mode public sans toucher
    à l'environnement). L'env, s'il est posé, reste prioritaire."""
    try:
        if on:
            open(_FLAG_FILE, "w", encoding="utf-8").write("public mode: sources cachées au public\n")
        elif os.path.exists(_FLAG_FILE):
            os.remove(_FLAG_FILE)
    except Exception:
        pass


# 1) Boutons-liens vers les fiches SOURCES (SofaScore/Unibet) : retirés en entier (le href pointe vers la
#    source = fuite directe). Les classes sont `lnk-bn-sofa` / `lnk-bn-uni`.
_LINK_BTN = re.compile(r'<a\b[^>]*class="[^"]*lnk-bn-(?:sofa|uni)[^"]*"[^>]*>.*?</a>',
                       re.IGNORECASE | re.DOTALL)
# 2) URL de source résiduelle dans un href (filet de sécurité si un lien passe autrement).
_SRC_HREF = re.compile(r'href="https?://[^"]*(?:sofascore|unibet|flashscore|livescore|pinnacle)[^"]*"',
                       re.IGNORECASE)

# 3) Remplacements de TEXTE AFFICHÉ. Ordre = du PLUS SPÉCIFIQUE au plus général (les phrases d'abord,
#    le nom nu en dernier). On ne cible que les formes AFFICHÉES (majuscule initiale) -> les noms de
#    classes CSS en minuscule (`lnk-bn-sofa`, `unibet_url`) ne sont PAS touchés.
_REPL = [
    # — Unibet (fournisseur de cotes) -> « marché » —
    ("Cotes Unibet", "Cotes marché"), ("Cote Unibet", "Cote marché"),
    ("cotes Unibet", "cotes marché"), ("cote Unibet", "cote marché"),
    ("Tous les paris Unibet", "Tous les paris du marché"),
    ("tous les paris Unibet", "tous les paris du marché"),
    ("paris Unibet", "paris du marché"), ("marchés Unibet", "marchés"),
    ("l'app Unibet", "l'app du bookmaker"), ("app Unibet", "app du bookmaker"),
    ("qu'Unibet", "que le marché"), ("qu’Unibet", "que le marché"),
    ("Unibet lui donne", "le marché lui donne"), ("Unibet donne", "le marché donne"),
    ("sur Unibet", "sur le marché"), ("via Unibet", "via le marché"),
    ("BETSFIX/Unibet/Public", "BETSFIX/Marché/Public"),
    ("Unibet", "Marché"),
    # — SofaScore (données / votes / notes) -> neutre —
    ("votes SofaScore", "votes des parieurs"), ("(SofaScore)", "(parieurs)"),
    ("note moyenne des joueurs (SofaScore)", "note moyenne des joueurs"),
    ("Source SofaScore", "Source de données"), ("source SofaScore", "source de données"),
    ("SofaScore limité", "Source momentanément limitée"),
    ("SofaScore momentanément en pause", "Source momentanément en pause"),
    ("SofaScore en pause", "source en pause"),
    ("SofaScore", "les données"),
    # — autres sources nommées -> génériques —
    ("RapidAPI/LiveScore", "nos sources"), ("RapidAPI", "nos sources"), ("LiveScore", "nos sources"),
    ("Flashscore", "nos sources"), ("Understat", "nos sources"), ("FotMob", "nos sources"),
    ("Sportradar", "nos sources"), ("GISMO", "nos sources"), ("Pinnacle", "référence sharp"),
]


def debrand(html: str) -> str:
    """Retire les liens-sources puis neutralise les noms de sources dans le HTML affiché. Idempotent,
    ne lève jamais (fail-open : renvoie l'entrée si quoi que ce soit tourne mal)."""
    try:
        html = _LINK_BTN.sub("", html)
        html = _SRC_HREF.sub('href="#"', html)
        for a, b in _REPL:
            if a in html:
                html = html.replace(a, b)
        return html
    except Exception:
        return html
