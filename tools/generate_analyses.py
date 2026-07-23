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
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Console Windows en cp1252 : les ✓ / · / emojis des logs crasheraient (UnicodeEncodeError).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import httpx  # noqa: E402

for _s in (sys.stdout, sys.stderr):   # console Windows cp1252 -> emojis (✓ · ⚠) sans UnicodeEncodeError
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app import card_data as _cd  # noqa: E402  (POINT UNIQUE de construction des cartes)
from app import notify as _notify  # noqa: E402  (gel des pronos déjà publiés)
from app import sources  # noqa: E402
from app import unibet  # noqa: E402
from app import value  # noqa: E402
from app.match_select import UNIBET_B, UNIBET_PARAMS, fetch_important  # noqa: E402

# Combinés même-match pré-construits Unibet (prepackcoupon), par event_id : VRAIE cote corrélée.
# Rempli dans build_dossier, relu dans _parse_combo pour re-pricer le combiné de l'analyste.
_PREPACK_CACHE: dict[str, list] = {}
# Catalogue des issues éligibles Bet Builder par event_id (pour pricer un combiné ARBITRAIRE exactement).
_CATALOG_CACHE: dict[str, list] = {}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "analyses")
PROGRAMME_PATH = os.path.join(ROOT, "data", "day_programme.json")   # sélection du jour (matin) : liste
#   des matchs que BETSFIX couvrira. Le pari de chacun est publié ~1 h avant SON coup d'envoi (vagues).
UA = {"User-Agent": "Mozilla/5.0"}
CACHE_HOURS = 6
# API locale (uvicorn SYSTEM, port 8000) : réutilise le chemin SofaScore qui marche déjà
# (curl_cffi anti-403 + repli RapidAPI) au lieu de ré-implémenter l'auth ici.
LOCAL_API = "http://127.0.0.1:8000"
# Cadence anti-blocage SofaScore (le repli RapidAPI est épuisé jusqu'au reset mensuel) :
SOFA_GAP = 1.2     # secondes entre 2 appels SofaScore d'un même match
SCAN_GAP = 2.0     # secondes entre 2 matchs (lisse la charge ; négligeable vs ~90s de Claude)

# Marchés à JETER du dossier (bruit) : on garde résultat/totaux/BTTS/handicaps/mi-temps.
NOISE = ("corner", "ntervalle", "ntervalle", "0:00", "10:00", "14:59", "Premier buteur",
         "Premier but", "Score exact", "Score Exact", "Asiatique", "Marque au moins",
         "Pari remboursé", "buteur", "2ème mi-temps", "2e mi-temps", "remboursé")
# Pour les COMBINÉS Coupe du Monde, on GARDE corners / premier but / buteur (marchés INDÉPENDANTS,
# essentiels pour un combiné non corrélé) — on ne filtre que le vrai bruit (intervalles, score exact…).
NOISE_COMBO = ("ntervalle", "0:00", "10:00", "14:59", "Score exact", "Score Exact",
               "Asiatique", "Pari remboursé", "remboursé")
# Sélection des marchés Unibet pour le dossier : au plus _PER_CRIT lignes par TYPE de marché
# (sinon basket/tennis — des centaines de lignes Handicap/Total quasi identiques — noient le dossier
# sous un seul type ; l'analyste doit voir un ÉVENTAIL varié de marchés pour LES 3 SPORTS), et
# _MAX_MK_LINES lignes au total.
_PER_CRIT = 3
_MAX_MK_LINES = 28

# COMBINÉ « Coupe du Monde 2026 » UNIQUEMENT (cas spécial demandé par l'utilisateur) : pour ces matchs,
# l'analyste génère un combiné de 3-4 sélections TRÈS probables qui expriment la MÊME domination du
# favori (jambes corrélées positivement qui passent ensemble), ZÉRO carton, marchés RÉGLABLES seulement.
# On privilégie la chance de passer, pas la grosse cote (Unibet rabote la cote corrélée -> assumé).
# Affiché À LA PLACE du pari simple. Exception CdM seulement (pas les autres tournois). Cf. COMBO_MISSION.
_BIG_TOURNEYS = ("coupe du monde", "world cup")


def _is_big_match(comp: str) -> bool:
    c = (comp or "").lower()
    return any(t in c for t in _BIG_TOURNEYS)


WC_NOTE = (
    "\n\nCONTEXTE COUPE DU MONDE — à INTÉGRER à l'analyse (bloc « CONTEXTE COUPE DU MONDE » ci-dessus) :\n"
    "• ENJEU / QUALIFICATION : sers-toi du CLASSEMENT DU GROUPE + recherche web pour établir l'enjeu réel "
    "(une équipe DÉJÀ qualifiée peut faire TOURNER son effectif et lever le pied ; une équipe qui DOIT "
    "gagner attaque -> plus de buts/corners ; un match où un nul suffit aux deux = fermé). La PHASE "
    "(poules / 8es / quart…) change l'intensité et la prudence. Calibre tes probas là-dessus.\n"
    "• ⚠️ CARTONS = MARCHÉ À ÉVITER (capital) : c'est l'ARBITRE qui décide des cartons et ça varie "
    "ÉNORMÉMENT d'un arbitre à l'autre — nos données ne le captent quasiment pas. C'est notre marché le "
    "MOINS FIABLE et il a fait perdre nos derniers combinés. RÈGLE : JAMAIS de jambe cartons dans un "
    "combiné (interdit). En pari simple, ne retiens un pari cartons que dans le cas EXCEPTIONNEL où tu as "
    "À LA FOIS la moyenne cartons/match de l'arbitre DÉSIGNÉ (donnée ci-dessus ou recherche web « <arbitre> "
    "yellow cards per game ») ET un enjeu clair (match tendu/rival) ; au moindre doute, SKIP les cartons.\n"
    "• COMBINÉ OBLIGATOIRE (CdM) : un combiné est joué sur CHAQUE match de Coupe du Monde. Tu DOIS donc "
    "désigner TON combiné via `COMBOPICK: <ids>` (2-3 jambes du POOL formant une VRAIE domination corrélée, "
    "cohérentes avec TON scénario du match) — JAMAIS `COMBOPICK: NONE` ici. Choisis-le toi (cohérent) "
    "plutôt que de laisser un empilement automatique. La cote combinée doit rester SUPÉRIEURE à celle de "
    "chaque jambe seule ; si 2 jambes sont quasi-redondantes (ex. « moins de 2,5 buts » + « moins de 1,5 en "
    "1re MT » = presque le même événement), la cote s'effondre sous la jambe seule -> combiné inutile, "
    "prends plutôt des jambes d'angles DIFFÉRENTS du même scénario (résultat + total + une équipe marque).\n")

COMBO_MISSION = (
    "\n\nMISSION SPÉCIALE — COMBINÉ Coupe du Monde (même match) : construis LE combiné de ce match. "
    "C'est le pari PHARE (il REMPLACE le pari simple). PHILOSOPHIE (PRIORITAIRE) : on privilégie la "
    "CHANCE DE PASSER, PAS la grosse cote. Mieux vaut un combiné à cote modeste qui PASSE qu'un combiné "
    "à grosse cote qui saute sur une jambe instable. Règles STRICTES :\n"
    "1) ⚠️ ZÉRO CARTON (le point CAPITAL) : AUCUNE jambe sur les cartons (jaunes/rouges/total/équipe). "
    "C'est notre marché le moins fiable (arbitre-dépendant) et il a fait perdre TOUS nos derniers "
    "combinés. Interdit. Évite de même tout marché tributaire d'un fait isolé.\n"
    "2) UNE SEULE LECTURE DE MATCH, déclinée en jambes CORRÉLÉES POSITIVEMENT : identifie le FAVORI "
    "NET du match et exprime SA DOMINATION sous plusieurs angles qui tombent ENSEMBLE quand il domine "
    "(c'est l'esprit du coupon gagnant « premier but FAVORI » + « le plus de corners FAVORI » + "
    "« l'adversaire ne gagne aucune mi-temps »). Comme ces jambes vont dans le même sens, elles PASSENT "
    "ensemble dans le scénario probable. Jambes idéales — TOUTES RÉGLABLES, à exprimer avec une LIGNE "
    "CHIFFRÉE EXPLICITE :\n"
    "   • Double chance / vainqueur du favori : « <Favori> ou nul » ou « <Favori> gagne ».\n"
    "   • Handicap du favori : « <Favori> -1.5 » (gagne par 2+ buts).\n"
    "   • Total d'ÉQUIPE du favori : « <Favori> marque plus de 1.5 ».\n"
    "   • Tirs CADRÉS d'une équipe AVEC un nombre : « <Favori> plus de 4.5 tirs cadrés » (fiable, ~83 % "
    "chez nous) — écris bien « cadrés » (≠ tirs totaux, ≠ buts).\n"
    "   • Les deux équipes marquent : NON, si l'adversaire est faible offensivement.\n"
    "   • AU PLUS UNE jambe de total de buts du match (« plus de 1.5 / 2.5 »).\n"
    "   ⛔ AUCUN CORNER, sous AUCUNE forme (total du match, par équipe, handicap, 1ère MT) : INTERDIT. "
    "C'est le marché qui a fait SAUTER le plus de nos combinés — même en cas de domination, le nombre de "
    "corners reste imprévisible (l'adversaire défend bas sans concéder de corner, ou le favori marque tôt "
    "et lève le pied). Exprime la domination par le RÉSULTAT, les BUTS ou les TIRS CADRÉS, JAMAIS les "
    "corners.\n"
    "   ✅ Les marchés MI-TEMPS sont désormais RÉGLABLES (buts d'une équipe/du match en 1ère/2e MT, "
    "« gagne au moins une mi-temps », « marque dans les deux mi-temps ») — tu PEUX les utiliser comme "
    "jambes de domination corrélée (un favori qui roule marque tôt et gagne une mi-temps).\n"
    "   ⛔ N'utilise PAS : les PROPS JOUEUR individuelles (tirs / tirs cadrés / passes / buteur d'UN "
    "joueur nommé — ex. « Ronaldo tirs cadrés +1.5 ») : UN seul joueur est trop imprévisible (variance) "
    "et ces jambes ont PLOMBÉ le ROI des combinés. Aussi INTERDITS : « premier but / premier buteur », "
    "les CARTONS (règle 1), les TIRS TOTAUX (« Total de tirs +X.5 » : 0/2 chez nous). Les « tirs CADRÉS "
    "d'une ÉQUIPE » avec un nombre SÛR restent autorisés. Reformule toute idée vers un marché d'ÉQUIPE "
    "fiable.\n"
    "   ⛔ COHÉRENCE OBLIGATOIRE : TOUTES les jambes vont dans le MÊME SENS (la domination du MÊME camp). "
    "JAMAIS deux jambes CONTRADICTOIRES — ex. « Angleterre +0.5 » (ne perd pas) AVEC « Croatie -1.5 » "
    "(gagne par 2+) = impossibles ensemble, le combiné ne peut PAS passer. Choisis UN favori et tiens-t'y "
    "sur toutes les jambes.\n"
    "3) Chaque jambe = sélection À TRÈS HAUTE PROBABILITÉ : ta proba estimée ≥ 80 % ET la COTE RÉELLE de "
    "la jambe ≤ ~1.30 (VÉRIFICATION DURE — au-delà, la jambe n'est PAS assez sûre pour un combiné : "
    "écarte-la. Une jambe à 1.60 ou 1.95 (≈ 55-62 %) est INTERDITE). Appuie chaque jambe sur les faits/"
    "tendances du dossier. NOMBRE DE JAMBES : 2 par défaut (une 3e SEULEMENT si elle est elle aussi ≤ "
    "~1.25 et parfaitement corrélée). Moins de jambes = plus de chances de PASSER : un combiné à 3 jambes "
    "à 80 % ne fait que 51 % de proba combinée -> souvent SOUS le seuil de rentabilité. Préfère 2 jambes.\n"
    "4) COTE : on achète de la FIABILITÉ, PAS du rendement. Vise une cote combinée (VRAIE cote corrélée) "
    "≈ 1.50–2.10 — mieux vaut 1.60 qui PASSE que 3.00 qui saute. Une cote combinée > 2.10 = SIGNAL que "
    "tes jambes sont trop justes : RETIRE la jambe la moins sûre jusqu'à revenir dans la cible. "
    "N'utilise QUE des cotes réelles du bloc ci-dessus.\n"
    "5) OBLIGATOIRE — un combiné CdM existe TOUJOURS : ne décline JAMAIS. Si le match n'a pas de favori "
    "écrasant (coin-flip), n'invente pas une fausse domination : bâtis le combiné le plus SÛR possible à "
    "partir de marchés de « forme de match » plutôt que de domination — total de buts (« Plus/Moins de "
    "X.5 »), double chance sur le camp LÉGÈREMENT favori, total d'une équipe, les deux équipes marquent "
    "Oui/Non — en choisissant les 2 jambes les PLUS probables (≥ 78 %, cote ≤ ~1.30) qui se TIENNENT "
    "ensemble. Quitte à viser une cote basse (1.45+) si c'est le seul moyen de rester fiable. Mais "
    "PRODUIS toujours un combiné.\n"
    "Ajoute À LA FIN, après la section Mise, EXACTEMENT ce format (CHAQUE jambe avec SON explication "
    "détaillée APRÈS le tiret — pourquoi CETTE jambe passe, factuel et chiffré ; chaque explication est "
    "une PHRASE COMPLÈTE : MAJUSCULE au début, point final, ponctuation et virgules correctes) :\n"
    "## 🎲 Combiné\n"
    "- <sélection exacte 1> @<cote> — <pourquoi cette jambe : 1 à 2 phrases factuelles et chiffrées>\n"
    "- <sélection exacte 2> @<cote> — <pourquoi cette jambe>\n"
    "  (une 3e ligne SEULEMENT si la jambe est ≤ ~1.25 et parfaitement corrélée — sinon RESTE à 2 jambes)\n"
    "**Cote combinée : <produit à 2 décimales>** — <1 phrase : pourquoi ces jambes expriment la MÊME "
    "domination du favori et passent donc ensemble dans le scénario probable>.\n"
    "Puis une TOUTE DERNIÈRE ligne technique (sous le PICK) au format EXACT :\n"
    "`COMBO: <sel1> @<cote1> | <sel2> @<cote2> | <sel3> @<cote3> = <cote combinée>`\n"
    "⚠️ Dans CETTE ligne COMBO:, pour un total d'ÉQUIPE écris TOUJOURS « <équipe> moins de 1.5 buts » / "
    "« <équipe> plus de 1.5 buts » EN TOUTES LETTRES — JAMAIS « <équipe> -1.5 buts » ni « +1.5 buts » "
    "(le « -1.5 » serait lu comme un HANDICAP et le pari réglé À L'ENVERS). Réserve « <équipe> -1.5 » "
    "(sans « buts ») au seul vrai HANDICAP de résultat."
)

# COMBINÉS TENNIS / BASKET (demande utilisateur) — MÊME esprit que le foot : domination corrélée d'un
# FAVORI NET, 2-3 jambes très probables, cote visée ~1.70-2.40, marchés RÉGLABLES seulement. Déclenchés
# UNIQUEMENT s'il y a un favori net (cote du favori ≤ _COMBO_FAV_MAX) ; l'analyste décline si coin-flip.
_COMBO_FAV_MAX = 1.50      # cote max du favori pour proposer un combiné (≈ proba implicite ≥ 67 %)

_COMBO_OUTPUT_FORMAT = (
    "\nAjoute À LA FIN, après la section Mise, EXACTEMENT ce format (CHAQUE jambe avec SON explication "
    "APRÈS le tiret — pourquoi CETTE jambe passe, factuel et chiffré ; chaque explication = une PHRASE "
    "COMPLÈTE : majuscule au début, point final) :\n"
    "## 🎲 Combiné\n"
    "- <sélection exacte 1> @<cote> — <pourquoi cette jambe>\n"
    "- <sélection exacte 2> @<cote> — <pourquoi cette jambe>\n"
    "**Cote combinée : <produit à 2 décimales>** — <1 phrase : pourquoi ces jambes expriment la MÊME "
    "domination du favori et passent donc ensemble dans le scénario probable>.\n"
    "Puis une TOUTE DERNIÈRE ligne technique (sous le PICK) au format EXACT :\n"
    "`COMBO: <sel1> @<cote1> | <sel2> @<cote2> = <cote combinée>`\n"
    "⚠️ Dans la ligne COMBO:, écris les TOTAUX EN TOUTES LETTRES (« Total de jeux moins de 20.5 », « Total "
    "de points plus de 165.5 », « <équipe> plus de 80.5 points ») — JAMAIS « -20.5 »/« +165.5 » pour un "
    "TOTAL (le signe « - » serait lu comme un HANDICAP et réglé à l'envers). Réserve « <Favori> -X.5 » au "
    "seul HANDICAP (sets au tennis, points au basket)."
)

COMBO_MISSION_TENNIS = (
    "\n\nMISSION SPÉCIALE — COMBINÉ (FAVORI NET). Construis LE combiné de ce match UNIQUEMENT s'il y a un "
    "favori NET, une domination LISIBLE **ET une VALUE réelle** (TA proba nettement au-dessus de la cote "
    "implicite — pas un favori à cote courte sans edge). SINON écris CLAIREMENT qu'aucun combiné solide "
    "n'existe : hors Coupe du Monde, l'ABSTENTION est la BONNE réponse par défaut — ne FORCE JAMAIS un "
    "combiné sans confiance réelle ET value. Philosophie : on achète la CHANCE DE PASSER + la value.\n"
    "1) UNE SEULE LECTURE : le favori s'impose nettement (idéalement 2 sets à 0). Décline CETTE domination "
    "en 2 à 3 jambes CORRÉLÉES POSITIVEMENT (elles passent ENSEMBLE si le favori roule), TOUTES réglables, "
    "chacune avec une LIGNE CHIFFRÉE — choisis parmi :\n"
    "   • Vainqueur du favori : « <Favori> gagne ».\n"
    "   • Handicap de SETS : « <Favori> -1.5 set » (gagne 2-0) — si la domination est franche.\n"
    "   • Total de JEUX : « Total de jeux moins de X.5 » (total du MATCH) OU « <Outsider> moins de X.5 "
    "jeux » (jeux gagnés par la PERDANTE) — un 2-0 maîtrisé = match court / perdante limitée.\n"
    "   • Le favori gagne le 1er set.\n"
    "   ⛔ JAMAIS : aces, doubles fautes, points exacts, « tie-break oui/non », nombre EXACT de jeux/sets "
    "(volatils ou non réglables chez nous).\n"
    "   ⛔ COHÉRENCE : toutes les jambes vont dans le sens du MÊME favori. JAMAIS de jambes contradictoires "
    "(ex. « -1.5 set » = 2-0 court AVEC « Total de jeux plus de 22.5 » = match long).\n"
    "2) Chaque jambe = TRÈS PROBABLE (proba ≥ ~72 %), appuyée par le classement, la forme/surface, le H2H "
    "et la fatigue/calendrier. 2 jambes (3 si CHACUNE reste ≥ 72 %).\n"
    "3) COTE combinée visée ≈ 1.70-2.40 (produit des cotes RÉELLES du dossier UNIQUEMENT). Si le favori est "
    "trop écrasant (cotes trop basses pour atteindre 1.70) ou s'il n'y a pas de favori net, n'écris PAS de "
    "combiné — dis-le explicitement."
    + _COMBO_OUTPUT_FORMAT
)

COMBO_MISSION_BASKET = (
    "\n\nMISSION SPÉCIALE — COMBINÉ (FAVORI NET). Construis LE combiné de ce match UNIQUEMENT s'il y a un "
    "favori NET **ET une VALUE réelle** (TA proba nettement au-dessus de la cote implicite — pas un favori "
    "à cote courte sans edge). SINON écris CLAIREMENT qu'aucun combiné solide n'existe : hors Coupe du "
    "Monde, l'ABSTENTION est la BONNE réponse par défaut — ne FORCE JAMAIS un combiné sans confiance réelle "
    "ET value. On achète la CHANCE DE PASSER + la value.\n"
    "1) UNE SEULE LECTURE : le favori contrôle le match. Décline-la en 2 à 3 jambes CORRÉLÉES, réglables, "
    "chacune avec une LIGNE CHIFFRÉE — choisis parmi :\n"
    "   • Handicap du favori : « <Favori> -X.5 » (couvre l'écart) — X = un handicap où tu estimes ≥ 72 %.\n"
    "   • Total de POINTS du match : « Total de points plus de Y.5 » ou « Total de points moins de Y.5 ».\n"
    "   • Total d'ÉQUIPE du favori : « <Favori> plus de Z.5 points ».\n"
    "   ⛔ JAMAIS : props joueurs exotiques, écart EXACT, score d'un quart-temps, vainqueur d'un "
    "quart-temps (volatils).\n"
    "   ⛔ COHÉRENCE : même favori, scénario cohérent (un gros handicap « -12.5 » suppose un match où il "
    "creuse l'écart — ne le combine pas avec un total de points TRÈS bas).\n"
    "2) Chaque jambe = TRÈS PROBABLE (≥ ~72 %), appuyée par le bilan, la forme, les blessés, le repos/"
    "déplacement et le rythme (pace) des deux équipes. 2 jambes (3 si CHACUNE reste ≥ 72 %).\n"
    "3) COTE combinée visée ≈ 1.70-2.40 (cotes RÉELLES UNIQUEMENT). Si impossible (favori trop écrasant) "
    "ou s'il n'y a pas de favori net, n'écris PAS de combiné."
    + _COMBO_OUTPUT_FORMAT
)
# Consensus sharp : on ne montre Pinnacle comme « vraie proba » que si SA marge est faible (ligne
# liquide/efficiente). Au-delà (petits marchés illiquides), le de-vig est bruité -> EV absurdes -> on
# l'écarte plutôt que d'induire l'analyste en erreur.
_SHARP_MAX_MARGIN = 0.08

METHODO = (
    "Tu es mon analyste paris sportifs PROFESSIONNEL. Objectif : des pronostics SÛRS et bien fondés, "
    "pas du volume. Mieux vaut 1 pari béton (ou aucun) que 3 moyens.\n\n"
    "RECHERCHE WEB OBLIGATOIRE d'abord (>=2 sources FIABLES et RÉCENTES, cite-les entre parenthèses ; "
    "n'invente JAMAIS — si une info manque ou est incertaine, écris-le explicitement). Vérifie selon "
    "le sport :\n"
    "• FOOT : composition probable / titulaires & ABSENTS (blessés, suspendus, repos), enjeu réel "
    "(amical vs compétition, rotation), forme des 5 derniers AVEC adversaire+score, dynamique "
    "domicile/extérieur, météo si pertinent, contexte (derby, fin de saison…).\n"
    "• TENNIS : SURFACE (et bilan du joueur SUR cette surface), classement ATP/WTA et écart, forme "
    "récente sur surface, fatigue/calendrier (match la veille, voyages), historique H2H, abandons "
    "récents, conditions (indoor/outdoor). ⚠️ DÉBUT de tournoi / CHANGEMENT de surface = upsets "
    "fréquents : un favori court SANS preuve de forme récente sur LA surface (cf. forme 14 jours des "
    "DONNÉES MULTI-SOURCES) n'est PAS un pari sûr — baisse ta proba ou SKIP.\n"
    "• BASKET : absents/blessés clés & temps de repos (back-to-back), rythme (pace) des 2 équipes, "
    "forme à domicile/extérieur, enjeu (tanking, playoffs). PROPS JOUEUR : compare la LIGNE du marché "
    "(ex. « passes +5.5 ») à la MOYENNE SAISON et à la FORME 5 derniers du bloc DONNÉES JOUEURS — ne "
    "joue un prop QUE si moyenne ET forme récente dépassent nettement la ligne (ou sont nettement en "
    "dessous pour un « Moins »), et tiens compte des absents (rôle/minutes qui changent).\n"
    "Les cotes ci-dessous sont celles d'UNIBET (réelles) : ne les invente jamais.\n\n"
    "DONNÉES FOURNIES CI-DESSOUS — ce sont des FAITS, sers-t'en en PRIORITÉ sur le narratif :\n"
    "• SÉRIES SPORTRADAR (forme récente factuelle, déjà mappées aux marchés). Base principale. Si une "
    "série contredit ton intuition web, suis la série.\n"
    "• DONNÉES MULTI-SOURCES (ESPN / FotMob / Understat) : forme avec adversaires+scores, classements "
    "À JOUR, blessés/absents nominatifs, H2H, xG, météo, fatigue/back-to-back. Source indépendante "
    "n°2 : un fait présent ici ET confirmé par ta recherche web (ou une autre source du dossier) = 2 sources "
    "concordantes. Les BLESSÉS listés ici sont fiables et récents — intègre-les TOUJOURS.\n"
    "• SENTIMENT (votes communauté) : signal d'appoint, jamais décisif seul.\n"
    "• H2H (confrontations directes) quand fourni.\n\n"
    "RÈGLE DE SÉLECTION (clé) : ne retiens un pari QUE s'il est soutenu par AU MOINS 2 éléments "
    "factuels CONCORDANTS (ex. série + contexte, ou stat + forme + H2H). Un seul argument = pas assez. "
    "Classe par CHANCE DE PASSER (probabilité réelle), PAS par edge contrarien. Sois HONNÊTE sur la "
    "proba (pas de gonflage). Un match incertain = moins de paris, voire SKIP.\n\n"
    "ANCRE SHARP (Pinnacle) : quand un « CONSENSUS SHARP » est fourni, c'est la proba la PLUS proche du "
    "VRAI (book sharp à faible marge). Sers-t'en comme ancre PRIORITAIRE : si TA proba et Pinnacle "
    "convergent et que la cote Unibet les BAT (EV+ indiqué), c'est le signal de value le plus fiable ; "
    "si tu diverges FORTEMENT de Pinnacle sans raison factuelle solide, c'est probablement TOI qui as "
    "tort -> prudence ou SKIP.\n\n"
    "VALUE — DÉTECTION SYSTÉMATIQUE (clé du ROI) : chaque issue du bloc COTES porte sa PROBA JUSTE "
    "« (jXX%) » = la proba du marché MARGE RETIRÉE (de-vig), et chaque marché sa « [marge X%] ». "
    "Procédure pour CHAQUE pari envisagé : (1) estime TA proba à partir des FAITS ; (2) compare-la à la "
    "proba juste jXX% de cette issue ; il y a VALUE si TA proba dépasse jXX% d'AU MOINS ~5 points. "
    "(3) Ne retiens que des paris à la fois SÛRS (ta proba ≥ 65 %) ET porteurs de value (ta proba > jXX%). "
    "Si ta proba ≤ jXX%, le marché te paie MOINS que le risque -> écarte. Balaie TOUS les marchés fournis "
    "(vainqueur, totaux, handicaps, sets/jeux, props joueur…), pas seulement le 1X2, et garde les "
    "meilleures value. PRIVILÉGIE les marchés à FAIBLE marge (lignes principales, ~3-6 % : plus efficients "
    "et fiables) ; MÉFIE-TOI des marges élevées (≥8 %, souvent props/exotiques : le book s'y protège). "
    "Indique la value dans l'explication (ex. « ma proba ~72 % vs juste 64 % -> value +12 % d'EV »).\n\n"
    "Rends ensuite ton analyse en respectant EXACTEMENT la structure ci-dessous — MÊMES titres, MÊME "
    "ordre, MÊME tableau — pour TOUS les sports (affiché tel quel dans l'app). AUCUN titre en haut, "
    "AUCUNE autre section.\n\n"
    "## 📋 Les faits\n"
    "BREF — 3 à 4 puces MAX : seulement le contexte DÉCISIF (un absent majeur, l'enjeu, une série nette, "
    "le facteur clé du match), avec sources entre parenthèses. PAS de redite avec l'explication du pari, "
    "PAS de remplissage. L'essentiel des chiffres va dans l'explication du pari (ci-dessous), pas ici.\n\n"
    "## 📊 Paris classés par chance de passer\n"
    "Tableau markdown avec EXACTEMENT ces colonnes : UNE SEULE LIGNE = LE pari le PLUS PROBABLE de TOUT "
    "le marché Unibet (balaie TOUS les marchés affichés ci-dessus, pas seulement le 1X2 : vainqueur, "
    "totaux, handicaps, double chance, sets/jeux, équipe… et garde celui qui a la plus forte proba de "
    "passer AVEC de la value). **UN SEUL pari par match — qualité avant quantité** : ZÉRO ligne si le "
    "match n'offre rien de solide (SKIP est une décision gagnante).\n"
    "RÈGLES DURES (notre historique RÉEL de règlements l'exige) :\n"
    "• Ne liste PAS ce pari si ta proba honnête est < 65 % — sur notre historique, les paris annoncés "
    "50-64 % ne passent qu'à ~50 %. Mieux vaut tableau VIDE et SKIP qu'un pari médiocre.\n"
    "• Privilégie un marché RÉGLABLE, cote idéale 1.30-1.70. Méfie-toi des « Total buts/points » et "
    "« total d'une équipe » (historiquement moins bons chez nous) : ne les retiens QUE sur une value "
    "claire et bien étayée — mais ne t'interdis PAS un bon pari (vainqueur, double chance, handicap, "
    "sets/jeux, premier but… restent pleinement ouverts). L'objectif n'est PAS zéro pari : si un pari "
    "solide ≥ 65 % existe, retiens-le.\n"
    "• Cote ≥ 1.70 : zone TRÈS dangereuse (39 % de réussite réelle chez nous). N'en liste un QUE si ta "
    "proba ≥ 70 % ET ≥ 3 éléments factuels concordants dont AU MOINS UN des DONNÉES MULTI-SOURCES "
    "(blessure adverse majeure, fatigue/back-to-back, série limpide). Sinon, ne le liste pas.\n"
    "| Pari | Cote | Proba | Risque |\n"
    "(Proba = TON estimation honnête en %, juste le nombre + %. Risque = EXACTEMENT 🟢 sûr ou 🟠 "
    "moyen. JAMAIS de 🔴 risqué (un pari risqué n'est pas une reco). Écarte les marchés inexploitables "
    "(props sans nom, cotes 0.00) et IGNORE toute cote < 1.10 — gain négligeable, jamais un pari.)\n\n"
    "## 🎯 Le pari à jouer\n"
    "C'est le CŒUR de la fiche. UNE SEULE puce pour LE pari du tableau (même sélection EXACTE). Format EXACT "
    "(le nom du pari + la cote dans le LABEL en gras, l'explication APRÈS les deux-points) :\n"
    "`- **<Sélection exacte du tableau> @<cote> :** <explication>`\n"
    "L'<explication> = la JUSTIFICATION la PLUS complète, précise et PROFESSIONNELLE de CE pari, en 4 à 6 "
    "phrases COMPLÈTES et AUTONOMES (le site les affiche EN PUCES, une par phrase — donc PAS un pavé d'un "
    "seul tenant, MAIS surtout NE BÂCLE PAS : chaque phrase est ENTIÈRE, soignée et argumentée, jamais un "
    "fragment télégraphique). Chaque phrase développe UN argument distinct et se lit seule. Couvre, dans "
    "cet ordre : (1) le SCÉNARIO de match et la forme récente CHIFFRÉE, avec les absents déterminants ; "
    "(2) les STATS clés par équipe/joueur (H2H, moyennes, contexte/enjeu, surface/repos) ; (3) la VALUE "
    "(ta proba estimée vs proba juste de la cote, ex. « ma proba ~72 % contre une cote qui en implique 64 "
    "% → value nette ») ; (4) le RISQUE principal à connaître. Ton de pro du pari sportif, chiffres "
    "concrets, français impeccable, ZÉRO généralité ni remplissage ni redite — mais une analyse FOUILLÉE. "
    "⛔ INTERDIT : réduire l'explication à un calcul de probabilité/marge/écart-type ; les points (1) et (2) "
    "(faits, forme, H2H, stats CHIFFRÉES) sont le CŒUR, la proba/value n'est qu'une clause.\n"
    "- **À éviter / SKIP :** ce qui est piégeux ; si le match est un coin-flip, dis-le et recommande "
    "de SKIP (ne rien jouer est une décision gagnante).\n\n"
    "## 🧪 Pari provisoire (indicatif — OBLIGATOIRE dès que tu SKIP le pari à jouer ci-dessus)\n"
    "Dès que tu SKIP (aucun pari de VALUE), tu DOIS remplir cette section (sinon le match n'a AUCUN pari à "
    "afficher et disparaît). Désigne ICI le MEILLEUR pari « si l'on devait absolument "
    "en jouer un » : celui que TON analyse identifie comme le plus solide factuellement — L'ANGLE QUE TU AS "
    "TOI-MÊME POINTÉ dans ton raisonnement (JAMAIS le favori des cotes par défaut s'il ne tient pas ; si tu "
    "as écrit « l'angle le plus solide serait un under », alors le provisoire EST cet under, PAS la victoire "
    "du favori). Analyse-le AUSSI SÉRIEUSEMENT qu'un vrai pari : 4 à 6 phrases COMPLÈTES, chiffrées et "
    "AUTONOMES, COHÉRENTES avec tes faits, dans CET ordre : (1) la FORME récente CHIFFRÉE + série en cours + "
    "contexte (domicile/repos/enjeu, absents déterminants) ; (2) les STATS clés & le H2H avec les NOMBRES "
    "(bilan, moyennes marquées/encaissées, différentiel, score du dernier duel) ; (3) pourquoi CE marché "
    "précis (ce que les faits écartent / laissent probable) ; (4) le RISQUE principal CONCRET (un fait qui "
    "pourrait le faire tomber). "
    "⛔ INTERDIT ABSOLU : réduire l'analyse à un calcul de probabilité / marge / écart-type "
    "(ex. « sur la marge attendue ~+10 et un écart-type ~13, la probabilité ressort à ~78 % »). CE N'EST PAS "
    "UNE ANALYSE — c'est du méta paresseux qui trahit un manque de faits, et c'est INACCEPTABLE. La proba/value "
    "tient en UNE demi-phrase AU PLUS, JAMAIS le cœur. Si une équipe a peu de données (récente/expansion), "
    "DONNE quand même les faits RÉELS que tu as (ses derniers résultats, son effectif, et le bilan/forme de "
    "l'adversaire établi) — jamais une tautologie de modèle. NIVEAU EXIGÉ (exemple à égaler) : « série de 9 "
    "victoires, H2H 6-0 jamais battue, à domicile, meilleur différentiel 81,9 marqués / 75,8 encaissés contre "
    "77,1 / 79,9 ; dernier duel 74-69 → victoire sèche probable ; risque : un soir d'adresse extérieure ». "
    "Rappelle qu'il est INDICATIF (hors ROI, PAS de mise : on s'abstient faute de value, mais voici le "
    "meilleur angle). Format EXACT :\n"
    "`- **<Sélection exacte réglable> @<cote> — <TA proba honnête %> :** <explication fouillée>`\n"
    "puis, en DERNIÈRE ligne de cette section, `PROV: <CODE>` (MÊME liste de codes que le PICK plus bas). "
    "Si tu AS un pari à jouer (value), N'ÉCRIS PAS cette section du tout.\n\n"
    "## 💰 Mise\n"
    "Une phrase : mise PLATE et petite EXPRIMÉE EN % DE BANKROLL (ex. « 1 à 2 % de la bankroll »), "
    "JAMAIS en « unités »/« u » ni en €, JAMAIS de combiné, 1 à 2 paris max par jour. Factuel, en français.\n\n"
    "Enfin, AJOUTE EN DERNIÈRE LIGNE, pour le règlement auto, au format EXACT `PICK: <CODE>` "
    "correspondant à TON pari. HOME = 1re équipe/joueur, AWAY = 2e. UNIQUEMENT un "
    "de ces codes (privilégie un marché RÉGLABLE pour ton Pari 1) :\n"
    "- Total buts/points : `OVER 2.5` / `UNDER 3.5`\n"
    "- Total d'une ÉQUIPE : `TEAMTOT HOME OVER 1.5` / `TEAMTOT AWAY UNDER 85.5`\n"
    "- Handicap : `HCAP HOME -1.5` / `HCAP AWAY +10.5` (ligne signée). ⚠️ FOOT — n'utilise QUE des lignes en "
    "DEMI-POINT (…-1.5, -0.5, +0.5, +1.5…) : elles sont NON AMBIGUËS (aucun remboursement). N'utilise JAMAIS "
    "un handicap ENTIER (+1, -1, +2…) : sur Unibet un handicap entier existe en DEUX marchés DIFFÉRENTS — "
    "« Handicap Asiatique » (remboursé si l'équipe perd/gagne d'EXACTEMENT la ligne) ET « 3-Way Handicap » "
    "(« commence 1-0 », 3 issues, perd si nul handicapé) — impossible de savoir lequel régler. Pour l'angle "
    "« le favori ne perd pas » (gagne OU nul) → DOUBLE CHANCE `DC 1X`/`DC X2` (marché clair, mieux coté). +1 "
    "≠ double chance ≠ handicap asiatique : trois marchés distincts, ne les confonds pas.\n"
    "- Les deux marquent : `BTTS YES` / `BTTS NO`\n"
    "- Résultat 1X2 (foot) : `1X2 1` / `1X2 X` / `1X2 2`\n"
    "- Double chance (foot) : `DC 1X` / `DC 12` / `DC X2`\n"
    "- Vainqueur (tennis/basket) : `WIN HOME` / `WIN AWAY`\n"
    "- Au moins un set (tennis) : `SET HOME` / `SET AWAY`\n"
    "- Vainqueur d'un set : `SETWIN 1 HOME` / `SETWIN 2 AWAY`\n"
    "- Score exact en sets (tennis) : `SETSCORE 2 0` (sets HOME-AWAY)\n"
    "- Total jeux d'un set : `SETGAMES 1 OVER 7.5` / `SETGAMES 1 UNDER 9.5`\n"
    "- Total jeux du match : `TOTGAMES OVER 20.5` / `TOTGAMES UNDER 22.5`\n"
    "- 1er jeu de service tenu : `HOLD1 HOME YES` / `HOLD1 AWAY NO`\n"
    "Si ton Pari 1 n'entre dans AUCUN code, écris `PICK: NONE`. Une seule ligne.\n"
    "\n\nTABLE DE CALIBRATION (ENREGISTREMENT INTERNE — ces prédictions ne sont NI affichées NI jouées ; "
    "elles servent UNIQUEMENT à calibrer le modèle : on vérifie après match si ta confiance tient, sur "
    "TOUT l'éventail de proba). APRÈS la ligne PICK, ajoute 10 à 14 lignes au format EXACT, une par "
    "prédiction :\n"
    "`CALIB: <sélection RÉGLABLE exacte> @<cote réelle> | <TA proba honnête %>`\n"
    "RÈGLES : (a) couvre TOUT le spectre — des quasi-certaines (~85-90 %) aux serrées (~45-55 %), PAS "
    "seulement les sûres ; sois HONNÊTE et calibré, JAMAIS optimiste (l'intérêt est de mesurer si tes "
    "55 % passent vraiment à 55 %). (b) marchés VARIÉS et RÉGLABLES (résultat, double chance, totaux, "
    "total d'équipe, handicaps, BTTS, sets/jeux — formulés comme les CODES ci-dessus pour être réglables), "
    "au plus 1 par marché. (c) UNIQUEMENT des cotes RÉELLES du dossier. (d) la value n'importe PAS ici : "
    "on mesure la justesse de TA proba, pas le rendement. Exemples : `CALIB: Allemagne gagne @1.55 | 71%` "
    "puis `CALIB: Plus de 2.5 buts @1.95 | 52%`, etc. Tous les marchés RÉGLABLES sont les bienvenus, "
    "VARIE-les un maximum pour calibrer PARTOUT : résultat, double chance, totaux, total d'équipe, "
    "handicaps, BTTS, corners, cartons, tirs cadrés ; MI-TEMPS (buts équipe/match, gagne une MT, marque "
    "dans les 2 MT) ; foot score exact, PREMIER BUT (équipe) ; tennis sets/jeux, score exact, handicap de "
    "jeux, tie-break ; basket QUART-TEMPS et mi-temps ; et désormais les PROPS JOUEUR (foot : tirs/tirs "
    "cadrés/passes décisives/tacles/fautes d'un joueur NOMMÉ ; basket : points/rebonds/passes d'un joueur "
    "NOMMÉ) — réglés via les données Opta (FotMob) / box-score ESPN. ÉVITE seulement : aces tennis, "
    "interceptions/contres/double-double basket, et premier BUTEUR (joueur) — pas de source.\n"
)


STORE_FILE = {"foot": "tracking_foot.json", "tennis": "tracking_tennis.json",
              "basket": "tracking_basket.json"}
_LEGACY = {"tennis": "tracking.json"}   # ancien nom, repli avant migration


def _load_store(sport: str) -> dict:
    for fn in (STORE_FILE.get(sport), _LEGACY.get(sport)):
        if not fn:
            continue
        try:
            with open(os.path.join(ROOT, "data", fn), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
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


def _purge_duplicates(sport: str, fid: str, m: dict) -> None:
    """REMPLACE l'ancien scan d'un MÊME match re-publié sous un AUTRE id (Unibet qui re-liste,
    reprogrammation pluie, id Sofa résolu différemment) : supprime les sidecars du même sport aux
    MÊMES équipes dont le coup d'envoi est à ≤ 30 h du nouveau — SAUF s'ils sont déjà RÉGLÉS
    (un match d'une série de playoffs déjà joué = de l'historique, on n'y touche jamais)."""
    new_ts = _kickoff_ts(m.get("start") or "")
    mh, ma = _norm(m.get("home", "")), _norm(m.get("away", ""))
    if new_ts is None or not mh or not ma:
        return
    for p in glob.glob(os.path.join(OUT, f"{sport}_*.json")):
        oid = os.path.basename(p)[len(sport) + 1:-5]
        if oid == str(fid):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        oh, oa = _norm(d.get("home", "")), _norm(d.get("away", ""))
        if not ((oh & mh and oa & ma) or (oh & ma and oa & mh)):
            continue
        ots = _kickoff_ts(d.get("start") or "")
        if ots is None or abs(ots - new_ts) > 30 * 3600:
            continue                       # trop éloigné = autre manche de la série, on garde
        settled = bool((d.get("result") or {}).get("score")) or any(
            b.get("result") for b in (d.get("bets") or []))
        if settled:
            continue                       # déjà réglé = historique/track record, intouchable
        for ext in (".json", ".md"):
            try:
                os.remove(p[:-5] + ext)
            except OSError:
                pass
        print(f"  · doublon remplacé : {sport}_{oid} ({d.get('name', '?')}) -> {sport}_{fid}")


def _fresh(path: str) -> bool:
    try:
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        return age_h < CACHE_HOURS
    except OSError:
        return False


_SOFA_SPORT = {"foot": "football", "tennis": "tennis", "basket": "basketball"}
_SCHED_CACHE: dict = {}   # (sport, jour) -> events : 1 SEUL appel scheduled-events par sport/jour


async def _scheduled(sport: str, day: str) -> list:
    """Planning SofaScore d'un sport pour un jour, MIS EN CACHE (1 appel par sport/jour au lieu
    d'un par match). Cache aussi l'échec (liste vide) pour ne pas re-marteler un endpoint bloqué."""
    key = (sport, day)
    if key in _SCHED_CACHE:
        return _SCHED_CACHE[key]
    from app import sofa_http
    path = _SOFA_SPORT.get(sport)
    evs = []
    if path and day:
        try:
            r = await sofa_http.get(
                f"https://api.sofascore.com/api/v1/sport/{path}/scheduled-events/{day}")
            if r.status_code == 200:
                evs = (r.json() or {}).get("events") or []
        except Exception:
            evs = []
    _SCHED_CACHE[key] = evs
    return evs


def _kickoff_ts(start: str):
    """Timestamp (s) du coup d'envoi Unibet (ISO) — pour départager les manches d'une série."""
    try:
        return datetime.fromisoformat((start or "").replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _card_sig(card) -> tuple | None:
    """Signature du CONTENU PUBLIÉ d'une carte prono (simple/combiné) -> détecter si le prono a CHANGÉ à un
    re-check. None si pas de carte (abstention). Combiné : cote + (marché, sélection) de chaque jambe.
    Simple : marché + sélection + cote. Ignore les libellés cosmétiques (heure, etc.)."""
    if not card:
        return None
    if card.get("type") == "combo":
        return ("combo", str(card.get("cote")),
                tuple((str(l[0]), str(l[1])) for l in (card.get("legs") or [])))
    return ("simple", str(card.get("market")), str(card.get("pick")), str(card.get("cote")))


def _carry_shadow_from_old(sport: str, fid: str, old_side: dict) -> None:
    """FANTÔMES DU PICK PRÉCÉDENT (demande user 2026-07-08). À la ré-analyse rapprochée (~1 h avant), le
    pari RETENU pour le ROI/stats est TOUJOURS le dernier généré (le nouveau sidecar vient d'être réécrit).
    Mais les prédictions conseillées AVANT ne doivent pas disparaître du calibrage : on les reporte en
    « fantômes » (shadow) dans le nouveau sidecar. Union par CODE : on n'ajoute QUE les codes absents du
    nouveau shadow (0 doublon, 0 double-comptage ROI car le nouveau pick seul finit dans `bets`). result
    remis à None (match pré-coup d'envoi). No-op si le prono est INCHANGÉ (mêmes codes)."""
    old = old_side.get("shadow") or []
    if not old:
        return
    p = os.path.join(OUT, f"{sport}_{fid}.json")
    try:
        new = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return
    cur = new.get("shadow") or []
    have = {s.get("code") for s in cur if s.get("code")}
    added = 0
    for s in old:
        c = s.get("code")
        if not c or c in have:
            continue
        have.add(c)
        cur.append({"sel": s.get("sel"), "cote": s.get("cote"), "prob": s.get("prob"),
                    "code": c, "result": None, "ghost_from": "pre_refresh"})
        added += 1
    if added:
        new["shadow"] = cur
        with open(p, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False)
        print(f"    👻 {added} prédiction(s) précédente(s) reportée(s) en fantôme (calibrage).")


def _analyzed_too_early(path: str, start: str, window_h: float) -> bool:
    """L'analyse EXISTANTE a-t-elle été faite quand le match était ENCORE HORS fenêtre (lead au moment de
    l'analyse > window_h) ? Si oui, elle est « trop en avance » sur le coup d'envoi -> à rafraîchir UNE
    fois quand le match approche. AUTO-LIMITÉ : une fois analysé DANS la fenêtre, le lead <= window_h ->
    plus de refresh (pas de re-post en boucle). mtime du .md = instant de la dernière analyse."""
    ko = _kickoff_ts(start)
    if ko is None:
        return False
    try:
        analyzed = os.path.getmtime(path)
    except OSError:
        return False
    return (ko - analyzed) / 3600 > window_h


def _load_programme_ids() -> set:
    """IDs (Unibet) des matchs du programme du jour. Vide si absent OU PÉRIMÉ (> 30 h : scan du matin
    manqué -> on n'analyse pas un vieux programme). 30 h couvre les matchs d'après-minuit d'un programme
    posé la veille au matin. -> --from-programme ne garde alors rien."""
    try:
        if (time.time() - os.path.getmtime(PROGRAMME_PATH)) / 3600 > 30:
            return set()
        d = json.load(open(PROGRAMME_PATH, encoding="utf-8"))
        return {str(m.get("id")) for m in (d.get("matches") or [])}
    except (OSError, ValueError):
        return set()


def _set_programme_status(match_id: str, status: str, provisional: dict | None = None) -> None:
    """Marque le STATUT d'un match dans le programme du jour (data/day_programme.json) pour l'affichage
    site : 'bet' (un pari a été retenu/publié) ou 'abstained' (analysé mais aucun pari ≥ seuil = pas de
    value). `provisional` = pari INDICATIF affiché sur les abstentions (jamais compté au ROI/stats). Il
    n'est retenu QUE pour un statut 'abstained' ; sinon on le retire (un match qui devient 'bet' n'a plus
    de provisoire). No-op si le match n'y figure pas. Écriture atomique (les vagues ne se chevauchent pas)."""
    try:
        with open(PROGRAMME_PATH, encoding="utf-8") as f:
            prog = json.load(f)
    except (OSError, ValueError):
        return
    hit = False
    for m in (prog.get("matches") or []):
        if str(m.get("id")) == str(match_id):
            new_prov = provisional if status == "abstained" else None
            if m.get("status") == status and m.get("provisional") == new_prov:
                return                       # déjà à jour (statut + provisoire)
            m["status"] = status
            if new_prov:
                m["provisional"] = new_prov
            else:
                m.pop("provisional", None)
            hit = True
            break
    if not hit:
        return
    tmp = PROGRAMME_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prog, f, ensure_ascii=False)
        os.replace(tmp, PROGRAMME_PATH)
    except OSError:
        pass


async def _build_and_post_programme(client, sports: list, args) -> None:
    """MATIN : sélectionne les matchs du jour (top N/sport dans la fenêtre), les enregistre dans
    data/day_programme.json et poste le « programme du jour » sur Telegram — SANS analyser. Le pari de
    chaque match sera publié ~1 h avant SON coup d'envoi par les vagues (--from-programme --refresh-early)."""
    from app import notify
    _ICON = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
    _NOM = {"foot": "Football", "tennis": "Tennis", "basket": "Basket"}
    matches = []
    # Repli PAR SPORT : programme précédent, pour ne pas effacer un sport dont la sélection échoue.
    # + PRÉSERVATION DES STATUTS (bet/abstained) au re-run : régénérer le programme ne doit PAS remettre à
    # zéro le statut d'un match déjà analysé/publié (sinon un pari publié repasse « pending » -> doublon
    # « Paris du jour » vs programme). On reporte le statut existant par id.
    prev_by_sport: dict = {}
    prev_status: dict = {}
    try:
        _pv = json.load(open(PROGRAMME_PATH, encoding="utf-8"))
        for _m in (_pv.get("matches") or []):
            prev_by_sport.setdefault(_m.get("sport"), []).append(_m)
            if _m.get("status"):
                prev_status[str(_m.get("id"))] = _m.get("status")
    except (OSError, ValueError):
        pass
    n_ok = 0
    for sport in sports:
        always = _is_big_match if sport == "foot" else None
        top = None
        for _attempt in range(3):                 # getaddrinfo = hoquet fréquent (cf. CLAUDE.md) -> on retente
            try:
                top = await fetch_important(sport, args.top, client, within_hours=args.hours, always=always)
                break
            except Exception as e:
                top = None
                if _attempt < 2:
                    await asyncio.sleep(2)
                else:
                    print(f"[{sport}] sélection programme échouée (3 essais) : {e}")
        if top is None:                           # réseau KO -> on CONSERVE le programme précédent de CE sport
            if prev_by_sport.get(sport):
                matches.extend(prev_by_sport[sport])
                print(f"[{sport}] réseau KO -> programme précédent conservé ({len(prev_by_sport[sport])} match(s)).")
            continue
        n_ok += 1
        if args.only_big:
            top = [m for m in top if _is_big_match(m.get("comp") or m.get("circuit") or "")]
        for m in top:
            _e = {"id": str(m.get("id")), "sport": sport, "name": m.get("name", ""),
                  "start": m.get("start", ""), "comp": m.get("comp") or m.get("circuit") or ""}
            if str(m.get("id")) in prev_status:          # préserve le statut au re-run (bet/abstained)
                _e["status"] = prev_status[str(m.get("id"))]
            matches.append(_e)
    # ⛔ NE JAMAIS écraser un programme valide par du VIDE : si AUCUN sport n'a été récupéré (échec réseau
    # TOTAL), on garde le fichier précédent INTACT (mtime compris) -> les vagues continuent sur l'ancien
    # programme au lieu de rester muettes toute la journée (bug audit : point de défaillance unique matinal).
    if n_ok == 0:
        print("Programme : AUCUN sport récupéré (réseau ?) -> programme précédent conservé intact.")
        return
    matches.sort(key=lambda x: x.get("start") or "")
    prog = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "matches": matches}
    tmp = PROGRAMME_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prog, f, ensure_ascii=False)
        os.replace(tmp, PROGRAMME_PATH)
    except OSError as e:
        print(f"  (écriture programme échouée : {e})")
    print(f"Programme du jour : {len(matches)} match(s) sélectionné(s).")
    if not matches or args.no_notify:
        return
    lines = [f"📋 <b>Programme du jour</b> — {len(matches)} match(s)"]
    cur = None
    for m in matches:
        if m["sport"] != cur:
            cur = m["sport"]
            lines.append(f"\n{_ICON.get(cur, '')} <b>{_NOM.get(cur, cur)}</b>")
        hm = ""
        try:
            hm = datetime.fromisoformat(m["start"].replace("Z", "+00:00")).astimezone().strftime("%H:%M")
        except (ValueError, AttributeError):
            pass
        nm = str(m["name"]).replace(" - ", " — ")
        lines.append(f"• {nm}" + (f" — {hm}" if hm else ""))
    lines.append("\n<i>Le pari de chaque match est publié ~1 h avant son coup d'envoi.</i>")
    try:
        notify.send_sync("\n".join(lines))
    except Exception as exc:
        print(f"  (programme Telegram ignoré : {exc})")


async def _resolve_sofa(sport: str, match: dict) -> str | None:
    """Résout l'id SofaScore d'un match (noms + DATE/HEURE). On scanne le planning de jour-1/jour/
    jour+1 (un match à 00:30 UTC peut être listé la VEILLE côté SofaScore) et, parmi les events aux
    BONS NOMS, on garde celui dont le coup d'envoi est le PLUS PROCHE de l'heure Unibet -> départage
    les SÉRIES de playoffs (mêmes équipes plusieurs fois). Repli : API de recherche. None si rien."""
    start = match.get("start") or ""
    day = start[:10]
    if not day or sport not in _SOFA_SPORT:
        return None
    mh, ma = _norm(match.get("home", "")), _norm(match.get("away", ""))
    if not mh or not ma:
        return None
    target = _kickoff_ts(start)
    days = {day}
    try:
        d0 = datetime.fromisoformat(day)
        days |= {(d0 - timedelta(days=1)).strftime("%Y-%m-%d"),
                 (d0 + timedelta(days=1)).strftime("%Y-%m-%d")}
    except ValueError:
        pass
    cand = []   # (id, startTimestamp) des events aux bons noms (sur jour-1/jour/jour+1)
    for dd in days:
        for ev in await _scheduled(sport, dd):
            h = _norm(((ev.get("homeTeam") or {}).get("name") or ""))
            a = _norm(((ev.get("awayTeam") or {}).get("name") or ""))
            if (h & mh and a & ma) or (h & ma and a & mh):
                cand.append((str(ev.get("id")), ev.get("startTimestamp")))
    if cand:
        if target is not None:        # le coup d'envoi le plus PROCHE (≠ une autre manche de la série)
            cand.sort(key=lambda c: abs((c[1] or 0) - target))
        return cand[0][0]
    return await _resolve_sofa_search(sport, match, day, mh, ma, target)   # repli recherche


async def _resolve_sofa_search(sport: str, match: dict, day: str, mh: set, ma: set,
                               target=None) -> str | None:
    """Repli : /search/all?q=... -> événement du BON sport, ≥1 équipe en commun, dont le coup d'envoi
    est le PLUS PROCHE de l'heure Unibet (±36 h max -> écarte les autres manches)."""
    from app import sofa_http
    import urllib.parse
    q = urllib.parse.quote(f"{match.get('home', '')} {match.get('away', '')}".strip())
    try:
        r = await sofa_http.get(f"https://api.sofascore.com/api/v1/search/all?q={q}")
        results = (r.json() or {}).get("results") or [] if r.status_code == 200 else []
    except Exception:
        return None
    want = _SOFA_SPORT.get(sport)
    best = None   # (écart_temps, id)
    for res in results:                                  # résultats déjà classés par pertinence
        if res.get("type") != "event":
            continue
        ent = res.get("entity") or {}
        sp = (((ent.get("tournament") or {}).get("category") or {}).get("sport") or {}).get("slug")
        if want and sp and sp != want:                   # bon sport
            continue
        h = _norm(((ent.get("homeTeam") or {}).get("name") or ""))
        a = _norm(((ent.get("awayTeam") or {}).get("name") or ""))
        if not (h & mh or h & ma or a & mh or a & ma):   # au moins UNE équipe en commun
            continue
        ts = ent.get("startTimestamp")
        if target is not None and ts:
            gap = abs(ts - target)
            if gap > 36 * 3600:                          # trop loin -> autre manche, on écarte
                continue
            if best is None or gap < best[0]:
                best = (gap, str(ent.get("id")))
            continue
        if ts and day:                                   # pas d'heure cible -> au moins la bonne date
            try:
                if datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") != day:
                    continue
            except (ValueError, OSError, OverflowError):
                pass
        return str(ent.get("id"))
    return best[1] if best else None


async def _tennis_extras(client: httpx.AsyncClient, sofa_id: str, home: str, away: str):
    """Données SofaScore TENNIS pour le dossier : classement ATP/WTA + écart, surface (et son poids),
    H2H, votes. (La forme via /events/last est bloquée 403 -> on s'appuie sur le web pour ça.)"""
    from app import sofa_http
    base = "https://api.sofascore.com/api/v1"
    try:
        r = await sofa_http.get(f"{base}/event/{sofa_id}")
        ev = (r.json() or {}).get("event") or {} if r.status_code == 200 else {}
    except Exception:
        ev = {}
    ht, at = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
    rh, ra = ht.get("ranking"), at.get("ranking")
    surface = ev.get("groundType") or (ev.get("tournament") or {}).get("groundType")
    tour = (ev.get("tournament") or {}).get("name")
    # CIRCUIT (WTA/ATP) : catégorie du tournoi si explicite, sinon GENRE des joueurs (M->ATP, F->WTA).
    cat = (((ev.get("tournament") or {}).get("category") or {}).get("name") or "").upper()
    g = (ht.get("gender") or at.get("gender") or "").upper()
    circuit = ("WTA" if "WTA" in cat else "ATP" if "ATP" in cat
               else "WTA" if g == "F" else "ATP" if g == "M" else "")
    await asyncio.sleep(SOFA_GAP)
    hw = aw = None
    try:
        h = await sofa_http.get(f"{base}/event/{sofa_id}/h2h")
        td = (h.json() or {}).get("teamDuel") or {} if h.status_code == 200 else {}
        hw, aw = td.get("homeWins"), td.get("awayWins")
    except Exception:
        pass
    votes = await _fetch_votes(client, "tennis", sofa_id)
    facts, sx = [], {}
    if circuit:
        sx["circuit"] = circuit
    if rh or ra:
        facts.append(f"Classement officiel : {home} #{rh or '?'} vs {away} #{ra or '?'}")
    if surface:
        facts.append(f"Surface : {surface} (le bilan SUR cette surface prime — vérifie-le sur le web)")
    if tour:
        facts.append(f"Tournoi : {tour}")
    if hw is not None and aw is not None and (hw or aw):
        facts.append(f"H2H (confrontations directes) : {home} {hw}-{aw} {away}")
        sx["h2h"] = {"home_wins": hw, "away_wins": aw, "draws": 0}
    if votes and votes[0] is not None:
        facts.append(f"Sentiment (votes communauté, appoint) : {home} {votes[0]}% / {away} {votes[1]}%")
    txt = ("\n\nDONNÉES SPORTRADAR TENNIS (factuel — base à croiser avec ta recherche web "
           "forme/surface) :\n- " + "\n- ".join(facts)) if facts else ""
    return txt, sx


async def _sofa_extras(client: httpx.AsyncClient, sport: str, sofa_id: str | None,
                       home: str, away: str) -> str:
    """Séries SofaScore + H2H + votes, récupérés via l'API locale (best-effort, "" si indispo).

    Réutilise les endpoints existants (/foot|/basket/match/{id}/{streaks,h2h,votes}) qui passent
    déjà par curl_cffi anti-403 + repli RapidAPI. Tennis : classement + surface + H2H + votes via
    SofaScore direct (cf. `_tennis_extras`). Renvoie (texte_dossier, meta_structurée)."""
    if not sofa_id:
        return "", {}
    if sport == "tennis":
        return await _tennis_extras(client, sofa_id, home, away)
    if sport not in ("foot", "basket"):
        return "", {}

    async def _get(ep: str):
        try:
            r = await client.get(f"{LOCAL_API}/{sport}/match/{sofa_id}/{ep}", timeout=20)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    # Appels SÉQUENTIELS (pas de rafale de 3 simultanés) + court délai : on ménage SofaScore
    # (plus de filet RapidAPI ce mois-ci → éviter de re-déclencher le blocage IP).
    streaks = await _get("streaks")
    await asyncio.sleep(SOFA_GAP)
    h2h = await _get("h2h")
    await asyncio.sleep(SOFA_GAP)
    votes = await _fetch_votes(client, sport, sofa_id)   # caché : partagé avec le sidecar
    out = []
    sx = {}   # données STRUCTURÉES pour le sidecar -> la fiche les rend sans rappeler SofaScore
    if streaks and (streaks.get("general") or streaks.get("head_to_head")):
        def _side(items, side):
            return " ; ".join(f"{s['name']}: {s['value']}" for s in items
                              if s.get("side") == side and s.get("value"))
        gen = streaks.get("general") or []
        sh, sa = _side(gen, "home"), _side(gen, "away")
        lines = ["SÉRIES SPORTRADAR (forme récente factuelle, mappées aux marchés — base PRINCIPALE "
                 "pour classer les paris) :"]
        if sh:
            lines.append(f"- [{home}] {sh}")
        if sa:
            lines.append(f"- [{away}] {sa}")
        hh = streaks.get("head_to_head") or []
        if hh:
            lines.append("- [H2H] " + " ; ".join(f"{s['name']}: {s['value']}" for s in hh
                                                  if s.get("value")))
        if len(lines) > 1:
            out.append("\n".join(lines))
        # structuré (listes JSON-sérialisables) pour le bloc Tendances de la fiche
        sx["streaks"] = {
            "home": [[s.get("name"), s.get("value")] for s in gen if s.get("side") == "home" and s.get("value")],
            "away": [[s.get("name"), s.get("value")] for s in gen if s.get("side") == "away" and s.get("value")],
            "h2h": [[s.get("name"), s.get("value")] for s in hh if s.get("value")],
        }
    if h2h and any(h2h.get(k) is not None for k in ("homeWins", "awayWins", "draws")):
        out.append(f"H2H (confrontations directes) : {home} {h2h.get('homeWins', 0)} - "
                   f"{h2h.get('awayWins', 0)} {away} ({h2h.get('draws', 0)} nuls)")
        sx["h2h"] = {"home_wins": h2h.get("homeWins"), "away_wins": h2h.get("awayWins"),
                     "draws": h2h.get("draws")}
    if votes and votes[0] is not None:
        nul = f" / nul {votes[2]}%" if votes[2] is not None else ""
        out.append(f"SENTIMENT (votes communauté, signal d'appoint) : {home} {votes[0]}%"
                   f"{nul} / {away} {votes[1]}%")
    return (("\n\n" + "\n".join(out)) if out else ""), sx


async def build_dossier(client: httpx.AsyncClient, match: dict, sport: str = "foot",
                        sofa_id: str | None = None) -> str | None:
    """Dossier compact : marchés Unibet utiles (hors bruit) + séries/H2H/votes SofaScore. None si indispo."""
    try:
        r = await client.get(f"{UNIBET_B}/betoffer/event/{match['id']}.json",
                             params=UNIBET_PARAMS, headers=UA)
        bo = r.json()
    except Exception:
        return None
    # Coupe du Monde : filtre RELÂCHÉ -> corners / premier but restent dispo pour bâtir un combiné
    # de marchés INDÉPENDANTS (non corrélés -> pas de réduction Unibet).
    big = _is_big_match(match.get("comp") or match.get("circuit") or "")
    noise = NOISE_COMBO if big else NOISE
    by_crit: dict = {}   # type de marché -> [variantes] (préserve l'ordre Unibet)
    for b in bo.get("betOffers", []) or []:
        crit = (b.get("criterion") or {}).get("label", "")
        if not crit or any(s in crit for s in noise):
            continue
        ocs = [o for o in (b.get("outcomes") or []) if o.get("odds")]
        # DE-VIG : proba JUSTE (marge retirée) par issue + marge du marché -> ancre de value pour
        # l'analyste (value = SA proba > proba juste « j% »). Calcul sur TOUTES les issues cotées.
        _, margin = value.annotate([{"odds": o["odds"] / 1000} for o in ocs])
        fair = value.devig([o["odds"] / 1000 for o in ocs])[0]
        outs = []
        for o, p in zip(ocs, fair):
            od = o["odds"] / 1000
            if od < 1.10:          # cote < 1.10 = gain négligeable -> jamais un pari, on l'écarte
                continue
            lbl = o.get("label") or o.get("englishLabel") or "?"
            ln = o.get("line")
            lns = f" {ln / 1000:g}" if ln is not None else ""
            outs.append(f"{lbl}{lns}={od:.2f} (j{p * 100:.0f}%)")
        if outs:
            by_crit.setdefault(crit, []).append(" | ".join(outs) + f"  [marge {margin * 100:.0f}%]")
    # Diversité. Pour la CdM : on PRIORISE les familles indépendantes du combiné (corners, cartons,
    # premier but, tirs, totaux, handicap, double chance…) et on en montre PLUS de lignes -> l'analyste
    # choisit la plus sûre de CHAQUE aspect parmi un vrai éventail (et plus 0 premier but).
    if big:
        # CdM : part ÉQUITABLE par ASPECT (sinon les corners, 14 marchés, saturent le dossier et
        # écrasent premier but/tirs). ~8 lignes max par aspect -> tous les aspects du combiné présents.
        ASPECTS = [("Issue", ("vainqueur", "temps réglementaire", "double chance", "handicap")),
                   ("Total buts", ("total de buts", "nombre total de buts")),
                   ("Corners", ("corner",)), ("Cartons", ("carton",)),
                   ("Premier but", ("premier but", "premier buteur", "buteur")),
                   ("Tirs", ("tir",)), ("Mi-temps", ("mi-temps",)),
                   ("But équipe", ("buts par", "marque"))]
        lines, used = [], set()
        for _lab, kws in ASPECTS:
            cnt = 0
            for crit, variants in by_crit.items():
                if crit in used or not any(k in crit.lower() for k in kws):
                    continue
                for v in variants[:3]:
                    lines.append(f"- {crit}: {v}")
                    cnt += 1
                used.add(crit)
                if cnt >= 8:
                    break
        for crit, variants in by_crit.items():          # le reste des marchés (jusqu'au plafond)
            if crit in used:
                continue
            for v in variants[:2]:
                lines.append(f"- {crit}: {v}")
            if len(lines) >= 80:
                break
    else:
        lines = []
        for crit, variants in by_crit.items():
            for v in variants[:_PER_CRIT]:
                lines.append(f"- {crit}: {v}")
            if len(lines) >= _MAX_MK_LINES:
                break
    if not lines:
        return None
    home, away = match.get("home", ""), match.get("away", "")
    odds = _result_odds(bo)
    imp = ""   # proba IMPLICITE du marché (marge retirée) = ancre de calibrage + détection de value
    o1, ox, o2 = odds if odds else (None, None, None)
    # FAVORI NET (tennis/basket) : cote du favori ≤ seuil -> on propose un combiné « domination corrélée »
    # (l'analyste décline si coin-flip). Le foot garde son combiné CdM (big).
    _fav = min([o for o in (o1, o2) if o], default=None)
    combo_tb = (sport in ("tennis", "basket") and _fav is not None and _fav <= _COMBO_FAV_MAX)
    if o1 and o2:
        inv = [1 / o1, (1 / ox if ox else 0.0), 1 / o2]
        s = sum(inv)
        if s > 0:
            ph, pd, pa = inv[0] / s, inv[1] / s, inv[2] / s
            parts = [f"{home} {ph * 100:.0f}%"] + ([f"nul {pd * 100:.0f}%"] if ox else []) + [f"{away} {pa * 100:.0f}%"]
            imp = ("\nPROBA IMPLICITE DU MARCHÉ (vainqueur, marge retirée) : " + " / ".join(parts)
                   + " — CALIBRE ta proba là-dessus : nettement AU-DESSUS = value (signale-la) ; "
                   "en dessous = écarte le pari.")
    # CONSENSUS SHARP (Pinnacle) : proba la PLUS proche du vrai (book sharp, faible marge). EV au prix
    # Unibet (proba_sharp × cote_unibet − 1) -> une EV+ ici = la cote Unibet bat le sharp = VALUE FORTE.
    sharp = ""
    try:
        from app import pinnacle
        sp = await asyncio.to_thread(pinnacle.sharp_probs, home, away, sport)
    except Exception:
        sp = None
    if sp and o1 and o2 and (sp.get("margin") or 1) <= _SHARP_MAX_MARGIN:
        seg = [f"{home} {sp['home'] * 100:.0f}%"] \
            + ([f"nul {sp['draw'] * 100:.0f}%"] if sp.get("draw") else []) \
            + [f"{away} {sp['away'] * 100:.0f}%"]
        evh, eva = sp["home"] * o1 - 1, sp["away"] * o2 - 1
        evseg = [f"{home} {evh * 100:+.0f}%", f"{away} {eva * 100:+.0f}%"]
        sharp = ("\nCONSENSUS SHARP (Pinnacle, book de référence — proba la PLUS proche du vrai) : "
                 + " / ".join(seg) + ". EV au prix Unibet : " + " / ".join(evseg)
                 + " — une EV+ ICI = la cote Unibet BAT le sharp = VALUE FORTE ; ancre n°1 pour calibrer "
                   "(si ta proba et Pinnacle convergent contre Unibet, c'est le meilleur signal).")
    extras, sx = await _sofa_extras(client, sport, sofa_id, home, away)
    # Sources GRATUITES indépendantes (ESPN/FotMob/Understat) : forme+scores, classements frais,
    # blessés, H2H, xG, météo — la source n°2 de la méthodo quand SofaScore est bloqué.
    src_prov: dict = {}   # traçabilité COMPLÉTUDE : sources multi ayant réellement répondu -> sidecar
    alt = await sources.extras(client, sport, match, prov=src_prov)
    # DONNÉES JOUEURS (basket) : moyennes saison + forme des joueurs cités dans les PROPS -> parier
    # les props (points/rebonds/passes…) avec des chiffres. Joueurs lus dans `participant` des marchés.
    pblock = ""
    if sport == "basket":
        players = [o.get("participant") for b in bo.get("betOffers", []) or []
                   if "joueur" in ((b.get("criterion") or {}).get("label") or "").lower()
                   for o in (b.get("outcomes") or []) if o.get("participant")]
        if players:
            from app import player_stats
            pblock = await asyncio.to_thread(player_stats.props_block, players)
    elif sport == "foot":
        teams = {home.lower(), away.lower()}
        players = [o.get("participant") for b in bo.get("betOffers", []) or []
                   if any(k in ((b.get("criterion") or {}).get("label") or "").lower()
                          for k in ("joueur", "buteur", "marque", "tir"))
                   for o in (b.get("outcomes") or [])
                   if o.get("participant") and o["participant"].lower() not in teams]
        if players:
            from app import player_stats
            pblock = await asyncio.to_thread(player_stats.soccer_props_block, players)
    # COUPE DU MONDE : contexte (arbitre + phase/groupe + classement) + mission combiné.
    wc_ctx = await sources.world_cup_extras(client, match) if big else ""
    if big:
        combo = WC_NOTE + COMBO_MISSION
    elif combo_tb and sport == "tennis":
        combo = COMBO_MISSION_TENNIS
    elif combo_tb and sport == "basket":
        combo = COMBO_MISSION_BASKET
    else:
        combo = ""
    # PROPS JOUEUR auto-révisables : interdites par défaut (règle 3 du COMBO_MISSION), mais RÉ-INTÉGRÉES
    # automatiquement dès que les FANTÔMES les prouvent (calibration « Props joueur » fiable). On lève
    # alors l'interdiction, en gardant le seuil ≥80 %/cote ≤~1.30 (pas de retour aux jambes à 1.60/1.95).
    if combo:
        from app import analyses as _an
        _pp_ok, _pp = _an.combo_player_props_allowed()
        if _pp_ok:
            combo += (
                "\n\n⚠️ MISE À JOUR DATA (auto-révisable) : les PROPS JOUEUR ont désormais FAIT LEURS "
                f"PREUVES en calibration ({_pp['n']} prédictions fantômes, réussite {_pp.get('win_rate')}% "
                f"vs {_pp.get('avg_conf')}% annoncés). L'interdiction des props joueur (règle 3) est LEVÉE : "
                "tu PEUX réintégrer UNE prop joueur comme jambe — MAIS seulement si elle respecte la règle "
                "3 (ta proba ≥ 80 % ET cote réelle ≤ ~1.30) et reste CORRÉLÉE à la domination du favori "
                "(ex. « <buteur du favori> - tirs cadrés +0.5 » à cote basse). Une prop à cote > 1.30 reste "
                "INTERDITE.\n")
    # Combinés pré-construits Unibet (vraie cote corrélée) : on les met en cache (pour re-pricer le
    # combiné de l'analyste après coup) ET on injecte le menu pour BIAISER l'analyste vers un combiné
    # qui en fait partie (-> on connaîtra sa vraie cote). TENNIS INCLUS (2026-07-04) : il A un catalogue
    # Bet Builder (196 outcomes vérifiés) -> ses combinés même-match étaient sur-cotés au PRODUIT (ex.
    # Lehecka Set 1 + match : produit 1.83 alors que la vraie cote corrélée Unibet = 1.44).
    if combo and sport in ("foot", "basket", "tennis"):
        # Catalogue Bet Builder : l'analyste construit son combiné DEDANS et cite l'id de chaque jambe
        # -> pricing TOUJOURS exact (vraie cote corrélée, jamais de repli produit).
        catalog = await asyncio.to_thread(unibet.betbuilder_catalog, str(match["id"]))
        if catalog:
            _CATALOG_CACHE[str(match["id"])] = catalog
            combo += _betbuilder_menu(catalog, sport, match.get("home", ""), match.get("away", ""))
        prepacks = await asyncio.to_thread(unibet.prepack_combos, str(match["id"]))   # repli
        if prepacks:
            _PREPACK_CACHE[str(match["id"])] = prepacks
    text = (f"MATCH: {match['name']} ({match['comp']}, coup d'envoi {match['start']})\n"
            "COTES UNIBET BELGIQUE REELLES (n'invente AUCUNE cote) — chaque issue porte sa PROBA JUSTE "
            "« (jXX%) » (marge retirée) et chaque marché sa « [marge X%] ». VALUE = ta proba > jXX% "
            "(détaille la procédure value plus haut) :\n" + "\n".join(lines)
            + imp + sharp + extras + alt + pblock + wc_ctx + combo)
    meta = {"odds": odds, "sources_prov": src_prov, **sx}   # odds + streaks/h2h + provenance -> sidecar
    return text, meta


def _resolve_claude() -> str:
    """Chemin du VRAI binaire claude, résolu depuis le PATH en SAUTANT le cwd et le dossier projet.
    Pourquoi : un `claude.bat` vit dans le dossier BETSFIX (lanceur double-clic INTERACTIF). Sous
    tâche planifiée (cwd=projet, Windows cherche le cwd en premier), `shutil.which('claude')` tombait
    sur ce .bat -> lançait un claude INTERACTIF qui se bloque jusqu'au timeout (0 analyse, exit 1).
    On cherche donc dir par dir dans le PATH, en ignorant cwd/projet, pour ne prendre que le vrai
    claude (npm `claude.CMD`). Repli sur shutil.which puis 'claude' si rien."""
    skip = {os.path.abspath(os.getcwd()),
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}
    # Windows : EXÉCUTABLE = une extension PATHEXT (.CMD/.EXE…) ; le fichier `claude` SANS extension
    # (script shell Unix) n'est PAS lançable par subprocess (WinError 193) -> on l'exclut. POSIX : ext "".
    pathext = os.environ.get("PATHEXT", "")
    exts = pathext.split(os.pathsep) if pathext else [""]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or os.path.abspath(d) in skip:
            continue
        for ext in exts:
            cand = os.path.join(d, "claude" + ext)
            if os.path.isfile(cand):
                return cand
    return shutil.which("claude") or "claude"


CLAUDE_MODEL = "opus"   # analyse ET panel 3 agents : on épingle le modèle le PLUS CAPABLE (qualité > vitesse,
                        # demande user 2026-07-08 : analyses complètes & professionnelles, pas rapides).


def run_claude(prompt: str, timeout: int = 360) -> str:
    """Lance Claude en headless sur l'abonnement et renvoie l'analyse (stdout). Épingle le modèle le PLUS
    CAPABLE (CLAUDE_MODEL) pour des analyses complètes et professionnelles. REPLI sur le modèle par défaut
    du CLI si l'appel avec --model rend une sortie vide (renommage/indispo du modèle) -> le scan ne tombe
    JAMAIS à zéro. Un timeout se propage tel quel (le match est sauté par l'appelant, pas de double run)."""
    exe = _resolve_claude()
    base = [exe, "-p", "--dangerously-skip-permissions"]
    for cmd in ([*base, "--model", CLAUDE_MODEL], base):
        p = subprocess.run(cmd, input=prompt, text=True, capture_output=True,
                           timeout=timeout, encoding="utf-8")   # TimeoutExpired -> propagé (voulu)
        out = (p.stdout or "").strip()
        if out:
            return out
    return ""


def _result_odds(bo: dict) -> tuple:
    """(o1, ox, o2) du marché VAINQUEUR DU MATCH, par type d'issue (OT_ONE/OT_CROSS/OT_TWO) —
    robuste pour les 3 sports : « Temps réglementaire » (foot) / « Cotes du match » (tennis/basket,
    y compris « - Prolongations incluses »). (None,None,None) sinon."""
    for b in bo.get("betOffers", []) or []:
        crit = ((b.get("criterion") or {}).get("label") or "").lower()
        if "cotes du match" in crit or "temps réglementaire" in crit or "temps reglementaire" in crit:
            o = {oc.get("type"): oc.get("odds", 0) / 1000 for oc in (b.get("outcomes") or [])}
            o1, ox, o2 = o.get("OT_ONE"), o.get("OT_CROSS"), o.get("OT_TWO")
            if o1 and o2:
                return (o1, ox, o2)
    return (None, None, None)


def _parse_pick(analysis: str) -> str:
    """Extrait le CODE de la ligne technique `PICK: <CODE>` (pour le règlement auto). '' sinon."""
    m = re.search(r"^[\s`*>\-]*PICK:\s*(.+?)\s*$", analysis, re.M)
    if not m:
        return ""
    code = re.sub(r"[`*]", "", m.group(1)).strip().upper()
    return "" if code in ("", "NONE") else code


def _parse_combo_designation(analysis: str):
    """DÉCISION de combiné de l'analyste (fix B 2026-07-05 : le combiné reflète SON jugement, pas un
    empilement mécanique). `COMBOPICK: <id>+<id>[+<id>]` -> [oids choisis dans le POOL] ; `COMBOPICK: NONE`
    -> [] (abstention EXPLICITE : l'analyste ne veut pas de combiné) ; ligne absente -> None (repli
    optimiseur, rétrocompat avec les anciennes fiches)."""
    m = re.search(r"^[\s`*>\-]*COMBOPICK:\s*(.+?)\s*$", analysis, re.M)
    if not m:
        return None
    body = re.sub(r"[`*]", "", m.group(1)).strip()
    if body.upper().startswith("NONE"):
        return []
    ids = [int(x) for x in re.findall(r"\d{5,}", body)]
    return ids if ids else []


def _cb_toks(s: str):
    """(mots ≥4 lettres, nombres) d'une sélection — séparés car le NOMBRE (ligne) est discriminant
    (« Plus de 2.5 » ≠ « Plus de 1.5 ») : on l'exige égal au matching, pas juste un recouvrement de mots."""
    s = (s or "").lower()
    words = {w for w in re.findall(r"[a-zà-ÿ]{4,}", s)}
    nums = {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", s)}
    return words, nums


def _prepack_menu(combos: list, sport: str, home: str, away: str) -> str:
    """Menu (pour le dossier analyste) des combinés pré-construits Unibet AVEC leur VRAIE cote.
    On NE garde que les combos dont TOUTES les jambes sont RÉGLABLES (code_from_pick non vide) et
    sans cartons/corners, cap à 14 entrées variées (-> combiné à la fois vraie cote ET réglable)."""
    if not combos:
        return ""
    from app.settle_analyst import code_from_pick
    rows, n = [], 0
    for c in combos:
        if not (2 <= c["n"] <= 3):
            continue
        sels = [l["sel"] for l in c["legs"]]
        if any(re.search(r"carton|jaune|rouge|corner", s, re.I) for s in sels):
            continue
        if not all(code_from_pick(s, sport, home, away) for s in sels):   # une jambe non réglable -> exclu
            continue
        n += 1
        legs = " + ".join(f"{l['sel']} @{l['odds']}" for l in c["legs"])
        rows.append(f"  [BB{n}] cote RÉELLE {c['real_odds']:.2f} : {legs}")
        if n >= 14:
            break
    if not rows:
        return ""
    return ("\n\nCOMBINÉS PRÉ-CONSTRUITS UNIBET (vraie cote corrélée, déjà combinables) — PRIVILÉGIE-LES "
            "FORTEMENT : choisis le MEILLEUR de cette liste selon ta méthodo (chance de passer d'abord, "
            "jambes fiables/réglables, PAS de cartons/corners), et REPRENDS SES JAMBES EXACTES dans ta "
            "ligne COMBO: et la section 🎲. La « cote RÉELLE » indiquée EST la vraie cote Unibet (déjà "
            "rabotée) : c'est elle qu'il faut viser/citer, pas le produit. Ne bâtis un combiné HORS liste "
            "que si AUCUN d'ici n'est jouable.\n" + "\n".join(rows) + "\n")


def _betbuilder_menu(catalog: list, sport: str, home: str, away: str) -> str:
    """Menu du CATALOGUE d'issues combinables (Bet Builder) avec leur id — l'analyste construit son
    combiné À PARTIR de cette liste et CITE l'id de chaque jambe -> pricing TOUJOURS exact (jamais de
    repli produit). On exclut cartons/corners (méthodo) et on cap à ~80 lignes variées."""
    if not catalog:
        return ""
    ban = ("corner", "carton", "jaune", "rouge")
    keep = ("temps réglementaire", "double chance", "total de buts", "les deux équipes",
            "mi-temps", "gagne au moins", "marque", "handicap", "total de points", "rebonds",
            "passes", "points du joueur", "tirs cadrés", "cotes du match", "vainqueur", "1x2")
    rows, seen = [], set()
    for c in catalog:
        t = (c.get("text") or "").lower()
        if not c.get("odds") or c["text"] in seen:
            continue
        if any(b in t for b in ban) or not any(k in t for k in keep):
            continue
        seen.add(c["text"])
        rows.append(f"  [{c['id']}] {c['text']} @{c['odds']}")
        if len(rows) >= 80:
            break
    if len(rows) < 4:
        return ""
    # Cible/contraintes NOUVELLES au FOOT uniquement (demande user) ; le basket garde son texte d'avant.
    _foot = sport == "foot"
    _target = ("une VRAIE cote ENTRE 1.75 ET 2.25 (cible ferme), chance de passer MAXIMALE dans cette "
               "fourchette (fiabilité + rendement)" if _foot
               else "une VRAIE cote ≥ 1.80 avec la chance de passer maximale")
    _range = ("  • Inclus AU MOINS 2-3 candidates à cote 1.35-1.80 (sinon impossible d'atteindre "
              "1.75-2.25 réel une fois Unibet raboté). AUCUNE jambe sous 1.10 (inutile : n'apporte rien).\n"
              "  • Pas deux totaux qui se recoupent (équipe + match) ; PAS de cartons/corners ; PAS de "
              "props JOUEUR individuelles (tirs/tirs cadrés/passes/points d'UN joueur nommé — trop de "
              "variance) : privilégie les marchés d'ÉQUIPE / de MATCH.\n" if _foot
              else "  • Inclus AU MOINS 1-2 candidates à cote 1.5-2.5 (sinon impossible d'atteindre 1.80 "
              "réel). Pas deux totaux qui se recoupent ; chaque candidate ≥ ~65 % ; PAS de cartons/corners.\n")
    return ("\n\nCATALOGUE COMBINABLE BET BUILDER — construis ton combiné À PARTIR de cette liste (cite "
            "l'id de chaque jambe -> pricing exact). Objectif : " + _target + ".\n"
            "⚠️ PRINCIPE — DOMINATION CORRÉLÉE (c'est TOI qui décides le combiné, pas un optimiseur "
            "aveugle) : un combiné même-match doit parier UN SEUL scénario cohérent, décliné en jambes qui "
            "TOMBENT ENSEMBLE (ex. le favori domine -> il ne perd pas + gagne une mi-temps + son total). "
            "Fait mathématique : l'EV d'un combiné = le PRODUIT des value de chaque jambe, INDÉPENDANT de la "
            "corrélation (Unibet rabote la cote des jambes corrélées MAIS leur proba conjointe monte d'autant "
            "-> la value NE change PAS). Donc la corrélation ne « détruit » RIEN : elle AUGMENTE la chance de "
            "passer. Conséquences IMPÉRATIVES :\n"
            "  • Chaque jambe doit être crédible et cohérente avec le MÊME scénario que les autres.\n"
            "  • INTERDIT : des jambes qui gagnent dans des scénarios OPPOSÉS (ex. « X n'est pas balayé » + "
            "« son adversaire reste tout proche » = hedge incohérent), ou une jambe hors-sujet juste pour "
            "gonfler la cote.\n"
            + _range
            + "⚠️ FORMAT EXACT. D'abord le VIVIER (6-8 candidates du catalogue, pour la calibration), une "
            "ligne chacune, id ENTRE CROCHETS + ta proba honnête :\n"
            "`POOL: <sélection> @<cote> [<id>] (<prob>%) — <pourquoi : CLAIR, UNE seule idée nette, factuel "
            "et chiffré ; PAS de tournure ambiguë du type « ne perd pas mais bascule sur un but adverse »>`\n"
            "PUIS, sur UNE ligne, TA DÉCISION de combiné :\n"
            "`COMBOPICK: <id>+<id>[+<id>]`  = les 2-3 ids DU POOL qui forment TA domination corrélée, "
            "à cotes PROCHES (chacune ~1.25-1.65) et d'ANGLES DIFFÉRENTS. ⚠️ NE choisis JAMAIS une jambe à "
            "cote ÉLEVÉE (≥ ~1.55) corrélée aux autres : elle ABSORBE le combiné (la cote combinée tombe SOUS "
            "cette jambe -> combiné inutile). La cote combinée doit rester au-dessus de CHAQUE jambe.  "
            "OU  `COMBOPICK: NONE`  s'il n'existe AUCUN combiné cohérent ET porteur de value (coin-flip, "
            "jambes non corrélées, marché à éviter, no-bet). Ne fabrique JAMAIS un combiné « juste pour "
            "parier » : NONE est la bonne réponse quand le match ne s'y prête pas.\n"
            "Catalogue :\n" + "\n".join(rows) + "\n")


def _match_prepack(legs: list, prepacks: list):
    """Re-price le combiné de l'analyste : si ses jambes correspondent à un prepack, renvoie
    (real_odds, shave_pct, prepack_id). Match = chaque jambe analyste retrouvée (≥60 % de tokens)
    dans une jambe DISTINCTE du prepack, et même nombre de jambes. None sinon."""
    leg_tok = [_cb_toks(l["sel"]) for l in legs]
    for pp in prepacks:
        if pp["n"] != len(legs):
            continue
        pp_tok = [_cb_toks(l["sel"]) for l in pp["legs"]]
        used, ok = set(), True
        for lw, ln in leg_tok:
            if not lw:
                ok = False
                break
            best_j, best_s = -1, 0.0
            for j, (pw, pn) in enumerate(pp_tok):
                if j in used or not pw:
                    continue
                if ln and ln != pn:          # NOMBRE/ligne présent mais différent -> jambe différente
                    continue
                s = len(lw & pw) / len(lw)   # recouvrement des MOTS
                if s > best_s:
                    best_j, best_s = j, s
            if best_j < 0 or best_s < 0.6:
                ok = False
                break
            used.add(best_j)
        if ok:
            return pp["real_odds"], pp["shave_pct"], pp["prepack_id"]
    return None


def _normalize_leg_sel(sel: str, home: str, away: str) -> str:
    """Réécrit certaines formulations de l'analyste vers le libellé du catalogue Bet Builder.
    Surtout la DOUBLE CHANCE (« <équipe> ou nul » -> « double chance 1X/X2 » ; « <A> ou <B> » -> 12)."""
    s = (sel or "").lower()
    ht = {w for w in re.findall(r"[a-zà-ÿ]{4,}", (home or "").lower())}
    at = {w for w in re.findall(r"[a-zà-ÿ]{4,}", (away or "").lower())}
    if re.search(r"\bou\b.*\bnul\b", s) or "double chance" in s:
        left = {w for w in re.findall(r"[a-zà-ÿ]{4,}", s.split(" ou ")[0])}
        if "1x" in s or (ht & left):
            return "double chance 1X"
        if "x2" in s or (at & left):
            return "double chance X2"
    if re.search(r"\bou\b", s) and (ht & {w for w in re.findall(r'[a-zà-ÿ]{4,}', s)}) and \
            (at & {w for w in re.findall(r'[a-zà-ÿ]{4,}', s)}):
        return "double chance 12"
    # VAINQUEUR DU MATCH (tennis/2-way) : « X gagne » / « X vainqueur » / « X remporte le match » -> libellé
    # catalogue Bet Builder « Cotes du match X ». On EXCLUT les marchés de SET/JEU/MI-TEMPS (« gagne le
    # set 1 » = autre marché, déjà bien matché). Le nom du joueur/équipe est ce qui reste après nettoyage.
    if (re.search(r"\b(gagne|vainqueur|remporte|l'emporte)\b", s)
            and not re.search(r"\bset\b|\bjeux?\b|\bmanche\b|mi-?temps|\bquart\b|\bmt\b|but", s)):
        name = re.sub(r"\b(gagne|gagnant|vainqueur|remporte|l'emporte|le|du|au|ce)\b|\bmatch\b|\bcotes?\b",
                      " ", s)
        name = re.sub(r"\s+", " ", name).strip(" :-–—")
        if name and len(name) >= 2:
            return f"cotes du match {name}"
    return sel


def _resolve_combo(legs: list, catalog: list, home: str = "", away: str = "", tol: float = 0.12):
    """Résout les jambes d'un combiné en outcome_ids du Bet Builder (pour pricer exactement).
    STRICT : Jaccard de mots (pénalise les tokens en trop), MÊME ensemble de nombres (ligne exacte),
    et AUTO-VÉRIF par la cote (la cote résolue doit coller à celle de la jambe à ±tol, sinon résolution
    douteuse -> on abandonne le combiné entier). Renvoie [outcome_ids] ou None (alors repli)."""
    ids = []
    for leg in legs:
        lw, ln = _cb_toks(_normalize_leg_sel(leg.get("sel", ""), home, away))
        lc = leg.get("cote")
        best, bj = None, 0.0
        for c in catalog:
            cw, cn = _cb_toks(c.get("text", ""))
            if ln != cn or not (lw | cw):
                continue
            jac = len(lw & cw) / len(lw | cw)
            if jac > bj:
                best, bj = c, jac
        if not best or bj < 0.5:
            return None
        if lc and best.get("odds") and abs(best["odds"] - lc) / lc > tol:
            return None                          # cote incohérente -> mauvaise résolution probable
        ids.append(best["id"])
    return ids if len(ids) == len(legs) else None


# Recalibrage « Équilibré » (2026-07-02) : les combinés perdaient (ROI −11 %, 44 % réussite @2.14) car
# le plancher de chance était bien trop bas (0.33 -> longshots -EV). On resserre : chance combinée ≥ 58 %
# (au-dessus du seuil de rentabilité de la zone de cote visée) et cote 1.50–2.10 (fini les 3.90/4.20).
# CIBLE combiné (demande user 2026-07-02) : cote combinée entre 1.75 et 2.25 — ni trop court (aucun
# rendement), ni trop gourmand (proba trop faible). Et AUCUNE jambe inutile sous 1.10 (n'apporte rien).
_COMBO_REAL_MIN = 1.75      # bas de la fourchette cible
_COMBO_REAL_MAX = 2.25      # haut de la fourchette cible
_COMBO_PROB_MIN = 0.40      # chance mini pour un combo « value » dans la fourchette (au-delà = longshot)
_COMBO_MEANINGFUL = 1.70    # cote plancher du repli « le plus sûr » (proche de la cible, jamais 1.03)
_COMBO_LEG_MIN = 1.10       # cote MINIMALE d'une jambe : sous 1.10 = jambe inutile (n'apporte rien) -> écartée
# GARDE-FOUS LOGIQUE (2026-07-05, après audit adversarial : 4 combinés/5 illogiques). Un combiné même-match
# n'a de sens QUE s'il est une « domination corrélée » (les jambes tombent ENSEMBLE) ET pas un coin-flip.
_COMBO_CORR_MIN = 0.999     # k = produit_cotes/vraie_cote ; seuil ≈ 1 (0.999 = tolérance flottante pour ne pas
#   rejeter l'indépendance stricte k=1.0). k < ~1 = jambes ANTI-corrélées (couverture, la vraie cote est
#   GONFLÉE au-dessus du produit) -> combiné illogique (cas Fritz/Bublik k=0.86, De Minaur 0.96, Mexique 0.99)
#   -> ÉCARTÉ. On n'accepte qu'une corrélation NON négative (domination, pas hedge).
_COMBO_CONJ_MIN = 0.55      # proba conjointe MINIMALE d'un combiné « value » : au-dessus du coin-flip (écarte
#   Toronto 46 %, Fritz 47 %). Le repli CdM (1 combiné/match) n'est PAS soumis à ce seuil.
_COMBO_MIN_LIFT = 1.10      # la vraie cote combinée DOIT dépasser d'au moins +10 % la cote de sa jambe la plus
#   haute — sinon le combiné est DOMINÉ (jouer cette jambe SEULE rapporte plus AVEC moins de risque). Arrive
#   quand 2 jambes sont quasi-REDONDANTES -> rabotage extrême (cas Mexique signalé user : combiné 1.47 <
#   jambe « Moins 2.5 » @1.58). S'applique à TOUS les combos (best/safest/dernier recours CdM inclus).


def _parse_pool(analysis: str, sport: str, home: str, away: str) -> list[dict]:
    """Parse le VIVIER de jambes candidates `POOL: <sel> @<cote> [<id>] (<prob>%) — <why>`."""
    from app.settle_analyst import code_from_pick
    out = []
    for m in re.finditer(r"^[\s`*>\-]*POOL:\s*(.+?)\s*$", analysis, re.M):
        part = re.sub(r"[`*]", "", m.group(1))
        mm = re.search(r"(.+?)@\s*([\d]+[.,][\d]+)\s*[\[#(]\s*(\d{6,})\s*[\])]?", part)
        if not mm:
            continue
        sel = mm.group(1).strip(" -–—")
        cote = float(mm.group(2).replace(",", "."))
        pm = re.search(r"(\d{1,3})\s*%", part)
        wm = re.search(r"[—–]\s*(.+)$", part)
        if sel and cote >= 1.01:
            out.append({"sel": sel, "cote": round(cote, 3),
                        "code": code_from_pick(sel, sport, home, away),
                        "oid": int(mm.group(3)), "prob": int(pm.group(1)) if pm else 70,
                        "why": wm.group(1).strip() if wm else ""})
    return out


def _clean_leg_text(t: str) -> str:
    """Nettoie le libellé d'un outcome du catalogue Bet Builder (verbeux) pour l'affichage, SANS en
    changer le sens : retire « du joueur », la mention « (Réglé selon les données Opta) », espaces."""
    t = re.sub(r"\s*\(R[ée]gl[ée]\s+selon[^)]*\)", "", t or "")
    t = re.sub(r"\s+du\s+joueur\b", "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip(" -–—:")


def _build_combo_from_pool(eid: str, cands: list, sport: str, home: str = "", away: str = "",
                           max_legs: int = 3, pick_none: bool = False, is_wc: bool = False,
                           must_include: set | None = None) -> dict | None:
    """Choisit, dans le VIVIER, la meilleure combinaison COMBINABLE par EV (= vraie cote × proba :
    capture À LA FOIS la value/le faible rabot ET la chance), sous contraintes vraie cote ≥
    _COMBO_REAL_MIN et chance ≥ _COMBO_PROB_MIN.

    La proba de CHAQUE jambe est RECALIBRÉE (`calibrated_conf`, MÊME boucle de feedback que le pari
    simple) AVANT le produit : la sur-confiance du LLM se COMPOSE en combiné (3 jambes gonflées de
    7 pts → produit largement gonflé), donc sans ça l'EV est systématiquement surévaluée (cause du
    ROI combiné négatif). Le plancher de chance _COMBO_PROB_MIN est désormais une BARRIÈRE DURE (best
    ET repli) : fini le repli « longshot à haute cote » qui sortait un combiné sous la chance mini.
    None si aucune combinaison ne tient la chance calibrée."""
    from itertools import combinations
    from app.analyses import calibrated_conf, combo_player_props_allowed
    # PORTÉE FOOT SEULEMENT (demande user) : la cible 1.75-2.25, le filtre props joueur et le couperet
    # « jambe < 1.10 » ne s'appliquent qu'au FOOT. Le basket garde son comportement d'avant (fourchette
    # large 1.80-4.20, chance ≥0.33, props joueur autorisées) -> zéro régression basket.
    _foot = sport == "foot"
    # REPLI « un combiné par match » = Coupe du Monde SEULEMENT (demande user 2026-07-05). Hors CdM, le foot
    # s'ALIGNE sur tennis/basket : combiné uniquement si VRAIE value (EV>1), sinon abstention. Seul le foot
    # CdM garde le repli « le plus sûr » forcé (le combiné phare de chaque match de Coupe du Monde).
    _wc_foot = _foot and is_wc
    r_min = _COMBO_REAL_MIN if _foot else 1.80
    r_max = _COMBO_REAL_MAX if _foot else 4.20
    p_min = _COMBO_PROB_MIN if _foot else 0.33
    r_mean = _COMBO_MEANINGFUL if _foot else 1.60
    cands = [c for c in cands if c.get("oid")]
    if _foot:
        # FILTRE DÉTERMINISTE : props JOUEUR écartées du vivier tant qu'elles n'ont pas fait leurs preuves
        # (auto-révisable via les fantômes). ET jambe inutile < 1.10 écartée (n'apporte rien à la cote).
        if not combo_player_props_allowed()[0]:
            cands = [c for c in cands if not (c.get("code") or "").startswith(("PLAYERFB", "PLAYERBK"))]
        cands = [c for c in cands if (c.get("cote") or 0) >= _COMBO_LEG_MIN]
    # CAP pour borner le nb de combinaisons (chaque combinaison = 1 appel pricing). MAIS on GARANTIT la
    # DIVERSITÉ DE COTES : on garde les meilleures par confiance ET les jambes à COTE HAUTE. Sans ces
    # dernières, un gros favori (marchés tous courts + corrélés) ne produirait QUE des combinés DOMINÉS
    # (cote combinée ≤ la jambe la plus haute -> absurde). Avec une jambe haute, un combiné NON-DOMINÉ
    # devient possible (ex. USA-Belgique : Total -3.5 @1.46 + BTTS @1.50 -> 2.65). cf. garde-fou domination.
    _top_odds = sorted(cands, key=lambda c: -(c.get("cote") or 0))[:3]   # 3 plus hautes cotes
    cands = cands[:6]
    for c in _top_odds:
        if c not in cands:
            cands.append(c)
    n = len(cands)
    if n < 2:
        return None
    # proba de jambe RECALIBRÉE (réduit la sur-confiance ; no-op tant que l'échantillon de la
    # catégorie est trop maigre -> jamais de sur-correction sur du bruit).
    for c in cands:
        cp = calibrated_conf(c.get("prob"), sport, c.get("code", ""))
        c["_cprob"] = (cp if cp is not None else (c.get("prob") or 70)) / 100
    # VRAIES cotes de jambes = CATALOGUE Bet Builder (vérité Unibet), pas les cotes POOL tapées par le LLM
    # (qui peuvent diverger). Sert au facteur de corrélation k ET au produit `total` affiché -> les deux
    # coïncident -> proba conjointe cohérente et auditable.
    _cat = {str(c.get("id")): c for c in _CATALOG_CACHE.get(eid, []) if c.get("id")}
    def _leg_odds(i):
        o = _cat.get(str(cands[i].get("oid")), {}).get("odds")
        return o if o else cands[i]["cote"]
    # best = meilleure EV parmi les combos « value » (real ≥ MIN ET chance ≥ MIN) ; safest = le PLUS SÛR
    # (plus haute chance), repli MOINS GOURMAND quand aucun combo value n'existe -> on ne force JAMAIS un
    # longshot à haute cote, et on produit TOUJOURS un combiné si le vivier a ≥2 jambes (pas de suppression).
    # ANCRAGE sur la désignation de l'analyste (fix 2026-07-06) : si `must_include` est fourni, on ne
    # considère QUE les combinaisons qui contiennent TOUTES ses jambes -> le combiné final reste SON choix
    # (au pire enrichi d'une jambe pour dé-dominer), jamais un combiné de remplacement décorrélé de sa prose.
    _must_idx = ({i for i, c in enumerate(cands) if c.get("oid") in must_include} if must_include else set())
    best, safest, any_safe = None, None, None
    for size in range(min(max_legs, n), 1, -1):
        for idx in combinations(range(n), size):
            if _must_idx and not _must_idx.issubset(idx):
                continue
            real = unibet.betbuilder_odds(eid, [cands[i]["oid"] for i in idx])
            if not real or real > r_max:
                continue
            prob = 1.0
            nvp = 1.0
            max_leg = 1.0
            for i in idx:
                prob *= cands[i]["_cprob"]
                lo = _leg_odds(i)            # cote CATALOGUE (= produit `total` affiché) -> k cohérent
                nvp *= lo
                if lo > max_leg:
                    max_leg = lo
            # GARDE-FOU DOMINATION (2026-07-05, signalé user — ABSOLU) : un combiné DOIT payer plus que n'importe
            # laquelle de ses jambes seule. Sinon (2 jambes redondantes -> rabotage extrême) la cote combinée
            # tombe sous une jambe -> jouer la jambe SEULE est strictement meilleur -> combiné écarté.
            if real < max_leg * _COMBO_MIN_LIFT:
                continue
            # CORRÉLATION MARCHÉ (2026-07-05) : les jambes d'un combiné same-match sont corrélées, donc la
            # proba conjointe honnête n'est PAS le produit des probas (= hypothèse d'INDÉPENDANCE) mais ce
            # produit AJUSTÉ par la corrélation que le marché price déjà dans la VRAIE cote Bet Builder.
            # k = nvp/real = (proba produit implicite) / (proba corrélée implicite) : k<1 si jambes
            # ANTI-corrélées (real gonflée AU-DESSUS du produit des cotes), k>1 si corrélées positivement
            # (real rabotée SOUS le produit = domination corrélée). Sans ça, un combiné anti-corrélé affiche
            # une FAUSSE value (cas FAA/ADF : 41 % produit vs ~28 % réel car Unibet cote 3.70 > produit 2.58).
            # Cohérent avec le pricing Bet Builder déjà capté. cf. [[kambi-betbuilder-pricing]].
            k = (nvp / real) if (real and nvp) else 1.0   # facteur de corrélation MARCHÉ (>1 = domination)
            prob = max(0.0, min(1.0, prob * k))
            if any_safe is None or prob > any_safe[0]:   # tout dernier recours (n'importe quelle cote)
                any_safe = (prob, real, idx)
            # repli PRINCIPAL (CdM) = le PLUS SÛR PARMI les combos CORRÉLÉS POSITIVEMENT (k ≥ _COMBO_CORR_MIN)
            # et à cote significative (≥ r_mean) : garantit un combiné « domination corrélée » par match de CdM,
            # jamais un hedge anti-corrélé ni un combiné dégénéré à 1.03. (Repli sur any_safe si aucun corrélé.)
            if real >= r_mean and k >= _COMBO_CORR_MIN and (safest is None or prob > safest[0]):
                safest = (prob, real, idx)
            # BEST = value réelle STRICTE. Un combiné même-match n'est retenu que s'il est (1) CORRÉLÉ
            # POSITIVEMENT (k ≥ _COMBO_CORR_MIN : domination, pas hedge), (2) au-dessus du coin-flip
            # (prob ≥ _COMBO_CONJ_MIN) et (3) porteur de value (EV>1, sauf repli CdM). Sinon ABSTENTION.
            # Encode l'audit 2026-07-05 : écarte Fritz/De Minaur (anti-corrélés), Toronto (proba 46 %).
            if (real >= r_min and k >= _COMBO_CORR_MIN and prob >= _COMBO_CONJ_MIN
                    and (_wc_foot or real * prob > 1.0)):
                ev = real * prob
                if best is None or ev > best[0]:
                    best = (ev, real, prob, idx)
    if best:
        _, real, prob, idx = best
    elif _wc_foot:
        # CdM : un combiné par match, même sans value réelle et même si PICK: NONE. Priorité : le PLUS SÛR
        # corrélé (safest), sinon n'importe quel combiné priçable (any_safe). TOUS DEUX SONT NON-DOMINÉS
        # (le garde-fou domination est ABSOLU -> un dominé n'entre jamais dans safest/any_safe). Un combiné
        # dont une jambe paye plus que le total est ABSURDE -> JAMAIS renvoyé. La diversité de cotes du
        # vivier (jambes hautes gardées) garantit qu'un combiné non-dominé existe presque toujours ; sinon
        # (vivier vraiment inexploitable) on renvoie None plutôt qu'un combiné dégénéré.
        if safest:
            prob, real, idx = safest
        elif any_safe:
            prob, real, idx = any_safe
        else:
            return None
    elif pick_none:
        # HORS CdM + PICK: NONE : on n'accepte un combiné QUE s'il a une VRAIE value (best ci-dessus). Pas
        # de repli forcé sur un match jugé sans signal -> discipline « coin-flip -> on passe » (cas FAA/ADF).
        return None
    else:
        return None                     # tennis/basket ET foot HORS CdM sans value réelle -> ABSTENTION
    # LIAISON À L'OUTCOME BET BUILDER RÉEL : la jambe affichée (texte + cote) est celle du CATALOGUE
    # pour l'oid pricé, JAMAIS le texte/cote tapé par le LLM (qui peut diverger -> carte à cote fantôme,
    # ex. « Autriche -1.5 @1.11 » affiché alors que l'oid pricé = « -2.5 @1.58 » -> combiné 2.07 ≠ 1.17
    # réel). Le CODE de règlement est re-dérivé du VRAI libellé. Ainsi carte = oid pricé = Unibet.
    from app.settle_analyst import code_from_pick
    legs = []
    for i in idx:
        oid = cands[i].get("oid")
        real_out = _cat.get(str(oid)) if oid else None
        if real_out and real_out.get("odds"):
            sel = _clean_leg_text(real_out.get("text") or cands[i]["sel"])
            cote = real_out["odds"]
            code = code_from_pick(sel, sport, home, away)
        else:                                # oid absent du catalogue -> on garde ce que le LLM a écrit
            sel, cote, code = cands[i]["sel"], cands[i]["cote"], cands[i]["code"]
        lg = {"sel": sel, "cote": cote, "code": code}
        if oid:
            lg["oid"] = oid                  # outcome_id Kambi -> re-pricing live de la cote (1 appel)
        if cands[i].get("why"):
            lg["why"] = cands[i]["why"]
        legs.append(lg)
    nv = 1.0
    for lg in legs:
        nv *= lg["cote"]
    # Libellé HONNÊTE sur la corrélation réelle (fini le « peu corrélées » systématique) : k = total/real
    # = ce que le marché price. k>1 -> cote corrélée RABOTÉE sous le produit = jambes qui tombent ensemble
    # (bon signe) ; k<1 -> cote GONFLÉE = jambes peu liées, retenu seulement pour la value.
    k = nv / real if real else 1.0
    if k >= 1.03:
        corr = "jambes à domination corrélée (tendent à tomber ensemble)"
    elif k <= 0.97:
        corr = "jambes peu liées à cote pleine (retenu pour la value)"
    else:
        corr = "jambes quasi indépendantes"
    return {"legs": legs, "total": round(nv, 2), "real_odds": round(real, 2),
            "shave": round(100 * (1 - real / nv), 1) if nv else None,
            "priced_by": "betbuilder_pool", "prob": round(prob * 100),
            "why": f"Combiné optimisé sur la VRAIE cote Unibet ({real:.2f}) — {corr}, "
                   f"chance estimée {round(prob * 100)}%."}


# Vivier de repli CdM : marchés de DOMINATION SÛRS (liste blanche de préfixes de code) — résultat, double
# chance, temps réglementaire, totaux buts, buts d'équipe, mi-temps, handicap, premier but. Le RÈGLEMENT
# d'un combiné se fait sur les CODES (pas les oids), donc un code en liste blanche = jambe réglable sûre.
_WC_FB_CODES = ("1X2", "DC", "REGTIME", "OVER", "UNDER", "TEAMTOT", "TEAMHALF",
                "WINHALF", "HALFRES", "HALFTOT", "FIRSTGOAL", "HCAP", "HCAP3", "BTTS")
# Liste NOIRE de textes : marchés au code AMBIGU/non fiable (« moins de 0.5 penalty » -> UNDER 0.5 réglerait
# un total de BUTS = FAUX) ou bannis (tirs/corners/cartons/props joueur). Filtre le sel ET le texte catalogue.
_WC_FB_BAN = ("penalt", "tir ", "tirs", "corner", "carton", "joueur", "faute", "hors-jeu", "hors jeu", "opta")


def _wc_fallback_vivier(analysis: str, eid: str, sport: str, home: str, away: str) -> list:
    """Vivier de DOMINATION de repli (CdM SEULEMENT) : construit depuis les FANTÔMES de l'analyse (codes
    déjà dérivés/validés par le pipeline), filtrés en LISTE BLANCHE de marchés sûrs + LISTE NOIRE de textes
    ambigus, puis mappés à leur oid Bet Builder (code identique + cote la plus proche). Garantit qu'un match
    de Coupe du Monde a TOUJOURS un combiné, même quand le pool cité par l'analyste est pauvre (props/mauvais
    identifiants). Le règlement reste sûr (il s'appuie sur les CODES, en liste blanche). [] si rien d'exploitable."""
    from app.settle_analyst import code_from_pick
    cal = _parse_calib(analysis, sport, home, away)
    cat = _CATALOG_CACHE.get(eid) or []
    cat_by_code: dict = {}
    for c in cat:
        t = (c.get("text") or "")
        if any(w in t.lower() for w in _WC_FB_BAN):
            continue
        code = code_from_pick(t, sport, home, away)
        if code:
            cat_by_code.setdefault(code, []).append(c)
    out, seen = [], set()
    for s in cal:
        code, sel = s.get("code") or "", s.get("sel") or ""
        if not code or code in seen or not code.startswith(_WC_FB_CODES):
            continue
        if any(w in sel.lower() for w in _WC_FB_BAN):
            continue
        opts = cat_by_code.get(code)
        if not opts:
            continue
        cote = s.get("cote") or 0
        best = min(opts, key=lambda c: abs((c.get("odds") or 0) - cote))   # même code, cote la plus proche
        out.append({"oid": best.get("id"), "sel": sel, "cote": best.get("odds") or cote,
                    "code": code, "prob": s.get("prob")})
        seen.add(code)
    return out


def _make_combo(analysis: str, sport: str, home: str, away: str, event_id: str | None,
                comp: str = ""):
    """Combiné du match. PRIORITÉ (fix B 2026-07-05) à la DÉCISION de l'analyste (`COMBOPICK:`) : il désigne
    LUI-MÊME son combiné (domination corrélée) ou s'abstient (`NONE`). Repli sur l'optimiseur du vivier si
    pas de désignation (rétrocompat) ou pour garantir le combiné CdM. Filtres logique appliqués dans tous
    les cas. `comp` -> détecte la Coupe du Monde (1 combiné/match réservé à la CdM)."""
    eid = str(event_id) if event_id else None
    # ROBUSTESSE (a) 2026-07-11 : recharger le catalogue Bet Builder s'il manque (hoquet réseau au chargement
    # du doss) -> un combiné CdM (obligatoire) n'est jamais perdu sur un hoquet ponctuel. cf. Argentine-Suisse.
    if eid and not _CATALOG_CACHE.get(eid):
        try:
            _reload = unibet.betbuilder_catalog(eid)
            if _reload:
                _CATALOG_CACHE[eid] = _reload
        except Exception:
            pass
    _pick_none = not _parse_pick(analysis)   # PICK: NONE / SKIP -> pas de combiné de repli forcé (garde-fou)
    _is_wc = _is_big_match(comp)             # CdM -> garde un combiné par match ; hors CdM -> aligné value-only
    _designation = _parse_combo_designation(analysis)   # [oids] | [] (COMBOPICK: NONE) | None (absent)
    if eid and _CATALOG_CACHE.get(eid):
        cands = _parse_pool(analysis, sport, home, away)
        # 1) DÉCISION EXPLICITE de l'analyste : NONE hors CdM = abstention (respecte sa réserve « no-bet /
        #    jamais en combiné » que l'optimiseur mécanique ignorait -> cause des combinés illogiques).
        if _designation == [] and not _is_wc:
            return None
        # 2) COMBOPICK: <ids> -> on ne price QUE les jambes qu'il a désignées (son scénario corrélé), puis
        #    on passe les MÊMES filtres logique. Une désignation incohérente (anti-corrélée, coin-flip) est
        #    donc quand même écartée hors CdM.
        if _designation:
            picked = [c for c in cands if c.get("oid") in set(_designation)]
            if len(picked) >= 2:
                _pick_oids = {c.get("oid") for c in picked}
                # a) la désignation TELLE QUELLE (ses jambes exactes).
                built = _build_combo_from_pool(eid, picked, sport, home, away,
                                               pick_none=False, is_wc=_is_wc)
                if built:
                    return built
                # b) désignation dominée/invalide -> combiné ANCRÉ : on GARDE ses jambes et on en ajoute une
                # du vivier pour dé-dominer, plutôt que de repartir sur un combiné qui ignore son choix.
                built = _build_combo_from_pool(eid, cands, sport, home, away,
                                               pick_none=False, is_wc=_is_wc, must_include=_pick_oids)
                if built:
                    return built
                # (a) ET (b) ont échoué (désignation dominée/non priçable).
                #  • HORS CdM : on N'IGNORE PAS sa désignation au profit de l'optimiseur brut (branche 3)
                #    qui renverrait un combiné DÉCORRÉLÉ -> ABSTENTION (évite le mismatch Mexique-Angleterre).
                #  • EN CdM : EXCEPTION (règle user) -> il faut ABSOLUMENT un combiné par match. On tombe donc
                #    sur le repli optimiseur (branche 3) qui GARANTIT un combiné. Le combiné est calculé UNE
                #    fois et écrit tel quel (source unique) -> pas de mismatch publié≠réglé même s'il finit
                #    différent de la désignation exacte.
                if not _is_wc:
                    return None
        # 3) repli OPTIMISEUR sur tout le vivier (CdM à GARANTIR, ou pas de désignation lisible ≥2 jambes).
        built = (_build_combo_from_pool(eid, cands, sport, home, away,
                                        pick_none=_pick_none, is_wc=_is_wc)
                 if cands else None)
        if built:
            return built
        # ROBUSTESSE (b) 2026-07-11 : en CdM, si le pool de l'analyste est trop pauvre (props/mauvais ids)
        # pour bâtir un combiné, REPLI sur un vivier de DOMINATION reconstruit depuis les FANTÔMES (codes
        # validés, liste blanche) -> garantit VRAIMENT le combiné CdM obligatoire (cf. Argentine-Suisse, où
        # l'analyste avait cité des props joueur). Le règlement reste sûr (jambes en liste blanche de codes).
        if _is_wc:
            _fb = _wc_fallback_vivier(analysis, eid, sport, home, away)
            if _fb:
                built = _build_combo_from_pool(eid, _fb, sport, home, away, pick_none=False, is_wc=True)
                if built:
                    return built
        # Catalogue Bet Builder PRÉSENT -> le combiné ne peut venir QUE de COMBOPICK/optimiseur (tous deux
        # passés aux filtres logique). On NE retombe PAS sur le parseur legacy `COMBO:` (non filtré : il
        # laissait passer des combinés sans proba ni contrôle de corrélation, cf. fuite Chine-Taipei 2026-07-05).
        return None
    combo = _parse_combo(analysis, sport, home, away, event_id)   # legacy : UNIQUEMENT si pas de catalogue
    # GARDE-FOU (2026-07-04) : un combiné BETSFIX est TOUJOURS même-match -> ses jambes sont CORRÉLÉES ->
    # sa cote = la VRAIE cote corrélée Unibet (Bet Builder), JAMAIS le produit naïf (qui SUR-évalue -> fausse
    # value/EV -> combiné retenu à tort). Si on n'a pas pu obtenir cette cote réelle (`real_odds` absent, p.ex.
    # match sans Bet Builder = combiné non plaçable), on NE PROPOSE PAS le combiné : mieux vaut pas de combiné
    # qu'une cote fausse. (Cause du bug tennis pricé 1.83 au lieu de 1.44 corrélé.) cf. [[kambi-betbuilder-pricing]].
    if combo and not combo.get("real_odds"):
        print(f"  · combiné écarté : pas de vraie cote corrélée Unibet (produit {combo.get('total')}) "
              f"-> abstention (jamais de cote sur-évaluée).")
        return None
    return combo


def _parse_combo(analysis: str, sport: str, home: str, away: str,
                 event_id: str | None = None) -> dict | None:
    """Parse la ligne technique `COMBO: s1 @c1 | s2 @c2 | … = total` -> {legs:[{sel, cote, code}],
    total}. Le `code` (règlable) est dérivé de chaque sélection. None si absent/invalide.
    Si `event_id` a des prepacks en cache ET que le combiné y correspond, ajoute la VRAIE cote
    Unibet (`real_odds`/`shave`) — sinon `total` reste le produit des cotes (repli)."""
    # tolère un préfixe backtick/astérisque/«-»/«>» (l'analyste entoure parfois la ligne de `code`).
    m = re.search(r"^[\s`*>\-]*COMBO:\s*(.+?)\s*$", analysis, re.M)
    if not m:
        return None
    from app.settle_analyst import code_from_pick
    body = re.sub(r"[`*]", "", m.group(1)).strip()
    body = re.split(r"=\s*[\d]+[.,]?[\d]*\s*$", body)[0]          # retire « = total » final
    legs = []
    cited_ids = []                                                # id d'issue Bet Builder cité par jambe (ou None)
    for part in body.split("|"):
        # « <sel> @<cote> [<id>] » : l'id (≥6 chiffres) entre crochets/#/() après la cote est OPTIONNEL.
        mm = re.search(r"(.+?)@\s*([\d]+[.,][\d]+)\s*(?:[\[#(]\s*(\d{6,})\s*[\])]?)?", part.strip())
        if not mm:
            continue
        sel = mm.group(1).strip(" -–—")
        cote = float(mm.group(2).replace(",", "."))
        if sel and cote >= 1.01:
            legs.append({"sel": sel, "cote": round(cote, 3),
                         "code": code_from_pick(sel, sport, home, away)})
            cited_ids.append(int(mm.group(3)) if mm.group(3) else None)
    if len(legs) < 2:
        return None
    total = 1.0
    for leg in legs:
        total *= leg["cote"]
    # Explication PAR JAMBE (« - <sel> @cote — <pourquoi> ») + synthèse (« Cote combinée : X — … »)
    # depuis la section « 🎲 Combiné » -> à afficher SOUS le combiné (détail de chaque jambe).
    # La ligne technique COMBO: ABRÈGE souvent les sels (« Canada » vs prose « Canada vainqueur ») :
    # on matche par RECOUVREMENT DE TOKENS (mots ≥4 lettres + nombres), pas par clé exacte (sinon why
    # toujours None et le combiné s'affiche sans explication par jambe).
    def _toks(s):
        s = (s or "").lower()
        words = {w for w in re.findall(r"[a-zà-ÿ]{4,}", s)}
        nums = {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", s)}
        return words | nums
    prose, synth = [], ""        # prose = [(tokens, why)] par jambe de la section prose
    sec = re.search(r"##[^\n]*[Cc]ombin[ée](.*?)(?:\n##|\Z)", analysis, re.S)
    if sec:
        for ln in sec.group(1).splitlines():
            bm = re.match(r"[>*\-\s]+(.+?)@\s*[\d.,]+[\s*`]*[—–:-]\s*(.+)$", ln.strip())
            if bm:
                prose.append((_toks(bm.group(1)), re.sub(r"[*`]", "", bm.group(2)).strip()))
            sm = re.search(r"[Cc]ote\s+combin[ée].*?[—–]\s*(.+)$", ln.strip())   # texte APRÈS le tiret
            if sm:
                synth = re.sub(r"[*`]", "", sm.group(1)).strip()
    for leg in legs:
        lt = _toks(leg["sel"])
        if not lt:
            continue
        best, best_score = "", 0.0
        for ptoks, w in prose:
            score = len(lt & ptoks) / len(lt)   # part des tokens de la jambe retrouvés dans la prose
            if score > best_score:
                best, best_score = w, score
        if best and best_score >= 0.5:
            leg["why"] = best
    out = {"legs": legs, "total": round(total, 2)}
    if synth:
        out["why"] = synth
    # Pricing de la VRAIE cote corrélée, par ordre de fiabilité :
    eid = str(event_id) if event_id else None
    catalog = _CATALOG_CACHE.get(eid) if eid else None

    def _apply_price(ids):
        """Price le PLUS GRAND sous-ensemble COMBINABLE (≥2 jambes) de `ids` (parallèle à `legs`) via
        Kambi, et ramène le combiné AFFICHÉ à cette version réellement plaçable (Unibet refuse certaines
        combinaisons -> on garde le plus de jambes possible). True si pricé."""
        from itertools import combinations
        n = len(ids)
        for k in range(n, 1, -1):                  # taille décroissante : on garde le MAX de jambes
            cands = []
            for idx in combinations(range(n), k):
                real = unibet.betbuilder_odds(eid, [ids[i] for i in idx])
                if real:
                    cands.append((real, idx))
            if cands:                              # à cette taille, on prend la cote la plus HAUTE
                real, idx = max(cands, key=lambda x: x[0])
                kept = [legs[i] for i in idx]
                for _j, _i in enumerate(idx):
                    if ids[_i]:
                        kept[_j]["oid"] = ids[_i]   # outcome_id Kambi -> re-pricing live
                nv = 1.0
                for lg in kept:
                    nv *= lg["cote"]
                out["legs"] = kept
                out["total"] = round(nv, 2)
                out["real_odds"] = round(real, 2)
                out["shave"] = round(100 * (1 - real / nv), 1) if nv else None
                if k < n:
                    out["trimmed"] = n - k         # jambes retirées (non combinables sur Unibet)
                return True
        return False

    # 1a) IDs CITÉS par l'analyste (depuis le catalogue) -> pricing EXACT. On vérifie d'abord que
    # chaque id existe dans le catalogue et que sa cote colle à la jambe (anti-erreur de citation).
    if eid and catalog and cited_ids and all(cited_ids) and len(cited_ids) == len(legs):
        odds_by_id = {c["id"]: c.get("odds") for c in catalog}
        good = all(
            oid in odds_by_id and odds_by_id[oid]
            and abs(odds_by_id[oid] - leg["cote"]) / leg["cote"] <= 0.12
            for oid, leg in zip(cited_ids, legs))
        if good and _apply_price(cited_ids):
            out["priced_by"] = "betbuilder_id"
    # 1b) sinon : résolution texte des jambes -> outcome_ids -> vraie cote.
    if "real_odds" not in out and catalog:
        ids = _resolve_combo(legs, catalog, home, away)
        if ids and _apply_price(ids):
            out["priced_by"] = "betbuilder"
    # 2) Repli — correspondance avec un combiné pré-construit (prepack).
    if "real_odds" not in out and eid:
        prepacks = _PREPACK_CACHE.get(eid)
        if prepacks:
            hit = _match_prepack(legs, prepacks)
            if hit:
                out["real_odds"], out["shave"], out["prepack_id"] = round(hit[0], 2), hit[1], hit[2]
                out["priced_by"] = "prepack"
    return out


def _parse_calib(analysis: str, sport: str, home: str, away: str) -> list[dict]:
    """Prédictions « fantômes » pour le CALIBRAGE (lignes `CALIB: <sel> @<cote> | <proba>%`). NON
    affichées, NON jouées : réglées après match pour enrichir la courbe de calibration sur TOUT le
    spectre de proba (corrige le biais de sélection du « 1 pari joué / match »). On ne garde que les
    prédictions RÉGLABLES (code non vide) ; déduplication par code."""
    from app.settle_analyst import code_from_pick
    out, seen = [], set()
    for m in re.finditer(r"^[\s`*>\-]*CALIB:\s*(.+?)@\s*(\d+[.,]\d+)\s*\|\s*(\d{1,3})\s*%?", analysis, re.M):
        sel = re.sub(r"[`*]", "", m.group(1)).strip(" -–—:")
        try:
            cote = float(m.group(2).replace(",", "."))
            prob = int(m.group(3))
        except ValueError:
            continue
        if not sel or cote < 1.01 or not (1 <= prob <= 99):
            continue
        code = code_from_pick(sel, sport, home, away)
        if not code or code in seen:        # non réglable ou doublon -> ignoré
            continue
        seen.add(code)
        out.append({"sel": sel, "cote": round(cote, 3), "prob": prob, "code": code, "result": None})
    return out


def _safe_pick(analysis: str) -> str:
    """Extrait le pari retenu (le plus probable) sous forme « sélection @ cote ». '' sinon.
    Gère 3 formats par ordre de priorité :
      1) section actuelle « ## 🎯 Le pari à jouer » → 1er point en gras `**sél @cote :**` ;
      2) anciens libellés « Pari 1 » / « Le plus sûr » (analyses déjà générées) ;
      3) repli : 1re ligne de données du tableau « Paris classés par chance de passer »."""
    def _clean(txt: str) -> str:
        txt = re.sub(r"\*\*|\*", "", txt).strip()
        mm = re.search(r"(.+?@\s*[\d]+[.,][\d]+)", txt)   # garde tout jusqu'à « @ cote » inclus
        if mm:
            return mm.group(1).strip()
        txt = re.split(r"\s[—–-]\s|\s*:\s*$|\s*:\s", txt)[0].strip()   # coupe à la justification
        return txt[:90]

    def _is_skip(s: str) -> bool:
        return bool(re.match(r"(?i)\s*(skip|aucun|pas de pari|abst)", s))

    # 1) Nouveau format : 1er bullet en gras sous « Le pari à jouer »
    m = re.search(r"##\s*🎯[^\n]*\n+\s*[-*]\s*\*\*(.+)", analysis)
    if m:
        if _is_skip(m.group(1)):
            return ""                       # SKIP explicite du pari simple → pas de repli tableau
        cand = _clean(m.group(1))
        if cand:
            return cand
    # 2) Anciens libellés explicites
    m = re.search(r"(?:Pari\s*1|Le plus s[ûu]r)\s*:?\**\s*(.+)", analysis)
    if m:
        cand = _clean(m.group(1))
        if cand:
            return cand
    # 3) Repli : 1re ligne de données du tableau de classement (| Pari | Cote | … |)
    m = re.search(r"^\|\s*([^|]+?)\s*\|\s*([\d]+[.,][\d]+)\s*\|", analysis, re.M)
    if m:
        sel = re.sub(r"\*\*|\*", "", m.group(1)).strip()
        if sel and sel.lower() not in ("pari", "sélection", "selection"):
            return f"{sel} @{m.group(2).replace(',', '.')}"[:90]
    return ""


_PROV_SKIP_RE = re.compile(r"(?i)^\s*(skip|aucun|pas de pari|abst|à\s*[ée]viter|a\s*eviter|—|-)\b")


def _provisional_pick(analysis: str, meta: dict | None, m: dict) -> dict | None:
    """Pari PROVISOIRE (indicatif) pour un match ANALYSÉ mais SANS value retenue (abstention) : le pari
    le PLUS PROBABLE « si l'on devait en jouer un ». Source = TÊTE du tableau « Paris classés par chance
    de passer » (le pari #1 par probabilité, ANALYSÉ de la même manière que les vrais pronos), quelle que
    soit sa value/vérifiabilité. On NE lit PAS la section « Le pari à jouer » (elle dit SKIP/à éviter en
    abstention). Repli ULTIME seulement si aucun tableau exploitable : favori 1X2 des cotes. Purement
    AFFICHAGE (programme) — JAMAIS écrit dans paris/stat_bet/shadow -> JAMAIS compté au ROI/stats/
    calibration (demande user 2026-07-09). None si rien d'exploitable. Renvoie {"sel", "cote": float|None}."""
    # 0) PRIORITÉ : la section « 🧪 Pari provisoire » que l'analyste DÉSIGNE + ANALYSE lui-même en cas de SKIP
    #    (le meilleur angle indicatif, COHÉRENT avec son raisonnement). Sinon on retombait sur le favori 1X2
    #    BRUT (repli), qui CONTREDIT l'analyse (« Victoire favori » vs « ne jouez pas le favori, jouez l'under »).
    _mprov = re.search(r"##\s*🧪[^\n]*\n+\s*[-*]\s*\*\*(.+?)@\s*([\d]+[.,]?[\d]*)\s*"
                       r"(?:[—–-]\s*(\d{1,3})\s*%)?[^:]*:", analysis or "", re.M)
    if _mprov:
        _psel = re.sub(r"\*\*|\*", "", _mprov.group(1)).strip(" —–-:")
        if _psel and not _PROV_SKIP_RE.match(_psel):
            try:
                _pc = float(_mprov.group(2).replace(",", "."))
            except ValueError:
                _pc = None
            _pp = min(int(_mprov.group(3)), 100) if _mprov.group(3) else None
            return {"sel": _psel[:90], "cote": _pc, "prob": _pp}
    # 1) Le pari le plus probable = 1re ligne de données du tableau classé par chance (| sél | cote | proba | … |).
    #    Le tableau est déjà trié par l'analyste (proba décroissante) -> row[0] = le plus probable.
    for mm in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*([\d]+[.,][\d]+)\s*\|\s*([^|]*?)\s*\|", analysis or "", re.M):
        sel = re.sub(r"\*\*|\*", "", mm.group(1)).strip()
        if not sel or sel.lower() in ("pari", "sélection", "selection", "marché", "marche"):
            continue                              # en-tête du tableau
        if _PROV_SKIP_RE.match(sel):
            continue                              # ligne « à éviter / SKIP » -> pas un pari
        try:
            cote = float(mm.group(2).replace(",", "."))
        except ValueError:
            cote = None
        _pm = re.search(r"(\d{1,3})", mm.group(3) or "")   # confiance (proba) de l'analyste, comme un vrai pari
        prob = min(int(_pm.group(1)), 100) if _pm else None
        return {"sel": sel[:90], "cote": cote, "prob": prob}
    # PLUS DE REPLI « favori 1X2 brut » (retour user 2026-07-11) : il CONTREDISAIT l'analyse (« Victoire
    # favori » alors que le raisonnement dit « ne joue pas le favori »). Un provisoire vient DÉSORMAIS
    # UNIQUEMENT de l'analyse : la section « 🧪 Pari provisoire » (priorité) ou la tête du tableau classé.
    # Si l'analyste n'a désigné NI l'un NI l'autre -> None (pas de provisoire, match caché) plutôt qu'un
    # favori incohérent. La qualité/cohérence prime sur la quantité.
    return None


def _track_provisional(sport, m, prov) -> None:
    """Enregistre le provisoire dans le SUIVI SÉPARÉ (app/provisional.py) — info seule, JAMAIS le ROI réel.
    Best-effort : ne casse jamais le scan. Si la (ré)analyse n'a PAS de provisoire, on RETIRE l'entrée non
    réglée du suivi -> cohérence Stats ↔ À venir (le suivi ne garde pas un pari que l'affichage a effacé)."""
    try:
        from app import provisional as _pvt
        if not prov or not prov.get("sel"):
            _pvt.drop_unsettled(m.get("id"))          # plus d'affichage -> plus de suivi (non réglé)
            return
        _pvt.record(sport, m.get("id"), m.get("home", ""), m.get("away", ""), m.get("start", ""),
                    m.get("name", ""), m.get("comp", ""), prov.get("sel"), prov.get("cote"))
    except Exception:
        pass


def _analyze_combo_legs(combo: dict) -> None:
    """Enrichit CHAQUE jambe du combiné du jour d'une JUSTIFICATION DÉDIÉE (`leg['why']`) + une SYNTHÈSE
    (`combo['synth']`), via UN appel Claude — les jambes sont analysées comme des paris à jouer (demande
    user 2026-07-11). Best-effort : ne casse jamais le scan si l'appel échoue (les jambes restent affichées
    sans justification). Appelé UNE fois par jour (le combiné est figé après publication)."""
    from app import analyses as _an
    legs = combo.get("legs") or []
    if not legs:
        return
    blocs = []
    for i, l in enumerate(legs, 1):
        md = _an.load(l.get("sport"), l.get("mid")) or ""
        try:
            faits = (_an._find(_an._sections(md), "📋", "Les faits", "faits") or "")[:1000]
        except Exception:
            faits = ""
        blocs.append(f"[{i}] {l.get('sport')} — {l.get('home')} vs {l.get('away')} — "
                     f"pari : {l.get('sel')} @{l.get('cote')} (proba estimée ~{round((l.get('prob') or 0) * 100)}%)\n"
                     f"    Faits du match : {faits or '(pas de faits captés)'}")
    # Le combiné du jour a AU PLUS 1 jambe par match (pick_combo) -> jambes de matchs DIFFÉRENTS =
    # INDÉPENDANTES (k=1, proba = produit brut). La synthèse ne DOIT donc pas prétendre une « corrélation »
    # (bug 2026-07-22 : « les deux jambes tennis tombent dans le même scénario » alors que ce sont 2 matchs
    # distincts). On n'autorise le vocabulaire de corrélation que si des jambes partagent le même match.
    _mids = [l.get("mid") for l in legs]
    _independent = len(set(_mids)) == len(legs)
    _synth_instr = (
        "Puis une SYNTHÈSE de 1 à 2 phrases, HONNÊTE : ces jambes portent sur des MATCHS DIFFÉRENTS, elles "
        "sont INDÉPENDANTES. N'invente AUCUNE corrélation, ne dis PAS qu'elles « tombent dans le même "
        "scénario » ni qu'elles sont « corrélées ». Dis plutôt ce qui rend CHAQUE issue solide "
        "individuellement, et pourquoi empilées elles forment le pari du jour le plus fiable (forte chance "
        "de passer). "
    ) if _independent else (
        "Puis une SYNTHÈSE de 1 à 2 phrases : ce qui rend ces jambes solides ENSEMBLE, leur corrélation "
        "RÉELLE — des issues du MÊME match qui tombent dans le même scénario. "
    )
    prompt = (
        "Tu es un analyste PRO du pari sportif. Tu justifies le COMBINÉ MULTISPORT DU JOUR (info seule, "
        "hors ROI) de BETSFIX. Pour CHAQUE jambe ci-dessous, écris une JUSTIFICATION propre à CE pari "
        "précis, en 3 phrases COMPLÈTES et AUTONOMES (français impeccable, ton de pro, aucune généralité "
        "ni remplissage). EXIGENCES STRICTES :\n"
        "  1) Cite AU MOINS DEUX faits CHIFFRÉS concrets tirés des faits fournis (forme récente, H2H, "
        "buts/points de moyenne, absents clés, xG, série en cours, cote vs proba estimée).\n"
        "  2) Nomme explicitement L'ANGLE qui rend ce pari solide (l'avantage principal), pas un survol.\n"
        "  3) Termine par une courte réserve honnête (« bémol : … ») = le principal risque de la jambe, "
        "en une clause — un pro reconnaît le risque sans survendre.\n"
        "  N'invente AUCUN chiffre : si un fait manque, appuie-toi sur ce qui est fourni. Pas de méta "
        "(ni « value », ni « proba », ni « seuil » comme sujet) : parle du MATCH.\n"
        + _synth_instr +
        "Réponds AU FORMAT EXACT, une entrée par ligne, RIEN d'autre :\n"
        "LEG1: <justification jambe 1>\nLEG2: <justification jambe 2>\n(… une ligne LEGn par jambe)\n"
        "SYNTH: <synthèse>\n\nJambes :\n" + "\n".join(blocs))
    try:
        out = run_claude(prompt, timeout=200)
    except Exception:
        out = ""
    if not out:
        return
    for i, l in enumerate(legs, 1):
        mm = re.search(rf"^\s*LEG\s*{i}\s*:\s*(.+)", out, re.M)
        if mm:
            l["why"] = mm.group(1).strip()
    ms = re.search(r"^\s*SYNTH\s*:\s*(.+)", out, re.M)
    if ms:
        combo["synth"] = ms.group(1).strip()


def _analyze_samematch_legs(combo: dict, analysis: str, home: str, away: str) -> None:
    """Justification DÉDIÉE par jambe (`leg['why']`) + synthèse (`combo['why']`) pour un combiné SAME-MATCH
    (Coupe du Monde) dont des jambes n'ont PAS de why — cas du combiné de REPLI construit par l'optimiseur
    (`_wc_fallback_vivier`), dont les jambes ne figurent pas dans la prose « 🎲 Combiné » de l'analyste. UN
    appel Claude, faits tirés de l'analyse du match. Best-effort. Garantit qu'AUCUN combiné affiché n'est
    sans analyse par jambe (demande user 2026-07-11). No-op si toutes les jambes ont déjà un why."""
    legs = combo.get("legs") or []
    # Régénérer si des jambes n'ont PAS de justification OU si la synthèse est GÉNÉRIQUE (texte automatique
    # de l'optimiseur « Combiné optimisé sur la VRAIE cote… ») : dans les deux cas l'analyse n'est pas au
    # niveau premium (demande user 2026-07-12 : que TOUS les combinés aient une vraie analyse par jambe).
    _generic = (combo.get("why") or "").startswith("Combiné optimisé")
    if not legs or (all(l.get("why") for l in legs) and not _generic):
        return
    faits = ""
    m = re.search(r"##[^\n]*[Ff]aits(.*?)(?:\n##|\Z)", analysis, re.S)
    if m:
        faits = re.sub(r"[*`>]", "", m.group(1)).strip()[:1200]
    blocs = [f"[{i}] {l.get('sel')} @{l.get('cote')} (proba estimée ~{l.get('prob')}%)"
             for i, l in enumerate(legs, 1)]
    prompt = (
        f"Tu analyses le COMBINÉ Coupe du Monde de {home} vs {away} (pari PHARE du match, compté au ROI). "
        "Pour CHAQUE jambe ci-dessous, écris une JUSTIFICATION propre à CE pari précis : 2 à 3 phrases "
        "COMPLÈTES et AUTONOMES, chiffrées, ton de pro du pari sportif, français impeccable, ZÉRO généralité "
        "ni remplissage — explique pourquoi ce pari est solide en t'appuyant sur les faits du match (forme, "
        "H2H, absents, style de jeu). Puis une SYNTHÈSE (1 à 2 phrases : pourquoi ces jambes tombent ENSEMBLE "
        "= domination corrélée). Réponds AU FORMAT EXACT, une entrée par ligne, RIEN d'autre :\n"
        "LEG1: <justification jambe 1>\nLEG2: <justification jambe 2>\n(… une ligne LEGn par jambe)\n"
        "SYNTH: <synthèse>\n\nFaits du match :\n" + (faits or "(pas de faits captés)")
        + "\n\nJambes :\n" + "\n".join(blocs))
    try:
        out = run_claude(prompt, timeout=200)
    except Exception:
        out = ""
    if not out:
        return
    for i, l in enumerate(legs, 1):
        mm = re.search(rf"^\s*LEG\s*{i}\s*:\s*(.+)", out, re.M)
        if mm:
            l["why"] = mm.group(1).strip()
    ms = re.search(r"^\s*SYNTH\s*:\s*(.+)", out, re.M)
    if ms:
        combo["why"] = ms.group(1).strip()


_VOTES_CACHE: dict = {}   # (sport, sofa_id) -> votes : évite de récupérer les votes 2× par match


async def _fetch_votes(client: httpx.AsyncClient, sport: str, sofa_id: str | None):
    """Votes communauté (%home, %away, %draw) via l'API locale, MIS EN CACHE (dossier + sidecar
    partagent le même appel). None sinon. Tennis : endpoint /matches/{id}/votes (préfixe distinct)."""
    if sport not in ("foot", "basket", "tennis") or not sofa_id:
        return None
    key = (sport, str(sofa_id))
    if key in _VOTES_CACHE:
        return _VOTES_CACHE[key]
    url = (f"{LOCAL_API}/matches/{sofa_id}/votes" if sport == "tennis"
           else f"{LOCAL_API}/{sport}/match/{sofa_id}/votes")
    res = None
    try:
        r = await client.get(url, timeout=15)
        if r.status_code == 200:
            j = r.json()
            if j.get("home_percent") is not None:
                res = (j["home_percent"], j.get("away_percent"), j.get("draw_percent"))
    except Exception:
        res = None
    _VOTES_CACHE[key] = res
    return res


async def _sofa_url(sofa_id) -> str | None:
    """URL publique SofaScore du match (customId + slug). None si id non exploitable."""
    sid = str(sofa_id or "")
    if not sid.isdigit() or len(sid) > 8:
        return None
    from app import sofa_http
    try:
        r = await sofa_http.get(f"https://api.sofascore.com/api/v1/event/{sid}")
        ev = (r.json() or {}).get("event") or {} if r.status_code == 200 else {}
    except Exception:
        return None
    cid, slug = ev.get("customId"), ev.get("slug")
    return f"https://www.sofascore.com/{slug}/{cid}" if (cid and slug) else None


# ------------------------------------------------------ VALIDATION PAR PANEL (3 agents indépendants)
# Après l'analyse, 3 validateurs SCEPTIQUES jugent le pari retenu selon un ANGLE distinct (pour ne pas se
# répéter). Le pari n'est gardé que si la MAJORITÉ valide (≥2/3) ; sinon le match passe en SKIP (choix
# utilisateur 2026-06-16 : qualité > quantité). La proba de consensus alimente une calibration plus juste.
_VALIDATORS = [
    ("donnees", "🔍",
     "ANGLE — COHÉRENCE DES DONNÉES : les faits multi-sources (forme récente, blessés, classement, H2H, "
     "stats par équipe/joueur) soutiennent-ils VRAIMENT ce pari, SANS contradiction ? Un fait qui va à "
     "l'encontre, un échantillon trop faible, ou des sources qui divergent -> REJETÉ."),
    ("value", "💰",
     "ANGLE — VALUE & COTE : la proba estimée dépasse-t-elle la proba juste de la cote, ou au moins "
     "l'égale sur un favori solide ? La cote est-elle raisonnable ? Rejette seulement si la value est "
     "CLAIREMENT absente (cote sous le sharp, pari payé moins que le risque)."),
    ("diable", "😈",
     "ANGLE — AVOCAT DU DIABLE : cherche un scénario de perte CRÉDIBLE et DOCUMENTÉ (blessure/repos/"
     "turnover avéré, piège net, enjeu inversé). Rejette seulement si ce risque est RÉEL et étayé par les "
     "faits — pas une simple possibilité théorique (tout pari a une part de variance)."),
]
_VALIDATOR_BASE = (
    "Tu es un VALIDATEUR de pari sportif INDÉPENDANT et lucide. On te donne le DOSSIER FACTUEL d'un "
    "match et UN pari proposé par un autre analyste. Juge selon ton angle ci-dessous. VALIDE un pari "
    "RAISONNABLE et soutenu par les faits ; ne REJETTE QUE si tu identifies un PROBLÈME CONCRET et sérieux "
    "(une donnée qui contredit clairement le pari, une value absente, un piège net). PAS de rejet « par "
    "prudence » ou « dans le doute » sans raison précise — un pari correct doit passer.\n{angle}\n\n"
    "Réponds en 3 lignes EXACTEMENT, RIEN d'autre :\n"
    "VERDICT: VALIDÉ   (ou REJETÉ)\n"
    "PROBA: <ta proba honnête de gain, juste le nombre en %>\n"
    "RAISON: <une seule phrase factuelle>\n\n")


def _parse_validation(out: str) -> dict:
    """Sortie d'un validateur -> {verdict('valide'/'rejete'), prob(int|None), reason}. Sortie illisible
    ou sans VERDICT clair -> REJETÉ (prudence)."""
    txt = out or ""
    mv = re.search(r"VERDICT\s*:\s*\**\s*(VALID|REJET)", txt, re.I)
    verdict = "valide" if (mv and mv.group(1).upper().startswith("VALID")) else "rejete"
    mp = re.search(r"PROBA\s*:\s*\**\s*(\d{1,3})", txt)
    prob = min(int(mp.group(1)), 100) if mp else None
    mr = re.search(r"RAISON\s*:\s*\**\s*(.+)", txt)
    reason = (mr.group(1).strip().lstrip("*").strip() if mr else "").split("\n")[0][:200]
    return {"verdict": verdict, "prob": prob, "reason": reason}


async def _validate_bet(doss: str, bet: dict, analyst_prob, sport: str) -> dict:
    """3 validateurs (angles distincts) jugent le pari retenu, séquentiellement (limites de débit Pro Max).
    -> {verdict('valide'/'rejete'), n_ok, n, consensus_prob, votes:[{angle,emoji,verdict,prob,reason}]}.
    Règle : VALIDÉ si MAJORITÉ (≥2 sur 3)."""
    head = (f"PARI PROPOSÉ À VALIDER : « {bet.get('sel', '')} » @ {bet.get('cote')} "
            f"(proba annoncée par l'analyste : {analyst_prob}%).\n\n")
    votes = []
    for key, emoji, angle in _VALIDATORS:
        try:
            out = await asyncio.to_thread(run_claude, _VALIDATOR_BASE.format(angle=angle) + head + doss, 240)
        except Exception:
            out = ""
        v = _parse_validation(out)
        v["angle"], v["emoji"] = key, emoji
        votes.append(v)
    n_ok = sum(1 for v in votes if v["verdict"] == "valide")
    probs = [v["prob"] for v in votes if v["prob"] is not None]
    return {"verdict": "valide" if n_ok >= 2 else "rejete", "n_ok": n_ok, "n": len(votes),
            "consensus_prob": (round(sum(probs) / len(probs)) if probs else None), "votes": votes}


def _write_sidecar(sport: str, fid: str, sofa_id: str, m: dict, meta: dict, analysis: str,
                   votes=None, sofa_url: str | None = None, validation: dict | None = None,
                   combo=None) -> None:
    """Métadonnées de l'analyse (équipes, compétition, coup d'envoi, cotes 1X2, pick, votes, +
    séries/H2H STRUCTURÉS + liens SofaScore/Unibet) -> sidecar JSON. La fiche rend tout depuis ce
    fichier, SANS rappeler SofaScore (une fois analysé, plus aucune raison d'appeler SofaScore)."""
    o1, ox, o2 = (meta.get("odds") if meta else None) or (None, None, None)
    side = {"sport": sport, "id": str(fid), "sofa_id": str(sofa_id),
            "home": m.get("home", ""), "away": m.get("away", ""),
            "name": m.get("name", ""), "comp": m.get("comp", ""), "start": m.get("start", ""),
            "o1": o1, "ox": ox, "o2": o2, "pick": _safe_pick(analysis),
            "pick_code": _parse_pick(analysis),   # code technique pour le règlement auto après match
            "unibet_url": (f"https://fr.unibetsports.be/betting/sports/event/{m.get('id')}"
                           if m.get("id") else None),
            "sofa_url": sofa_url,
            "generated": datetime.now(timezone.utc).isoformat()}
    # COMPLÉTUDE des données : quelles sources multi ont répondu + score (0 = analyse sur cotes seules,
    # sans enrichissement -> l'invariant selfcheck le signale). Rend la démarche AUDITABLE : on sait, fiche
    # par fiche, si l'analyse a été faite « de la même manière » (données riches) ou dégradée.
    _prov = (meta or {}).get("sources_prov") or {}
    side["sources"] = sorted(k for k, v in _prov.items() if v)
    if meta and meta.get("streaks"):
        side["streaks"] = meta["streaks"]
    if meta and meta.get("h2h"):
        side["h2h"] = meta["h2h"]
    # `data_score` = RICHESSE RÉELLE des données de l'analyse (fix 2026-07-14) : compte AUSSI les séries
    # Sportradar (streaks) et le H2H, pas seulement FotMob/ESPN/Understat (`sources`). Avant, un match
    # richement analysé via Sportradar+Flashscore affichait `data_score 2` à tort (ex. St Johnstone : 5
    # victoires/14 buts + streaks 8/8) et faisait un faux « cotes seules ». Reflète ce qui a servi à l'analyse.
    side["data_score"] = len(side["sources"]) + (1 if side.get("streaks") else 0) + (1 if side.get("h2h") else 0)
    circuit = m.get("circuit") or (meta.get("circuit") if meta else None)   # Unibet (path) prioritaire
    if circuit:
        side["circuit"] = circuit
    # SOURCE UNIQUE (fix 2026-07-06) : le combiné est calculé UNE fois dans la boucle et passé ici, pour que
    # la carte PUBLIÉE (notif) == le sidecar (carte image) == le RÈGLEMENT. Le recalculer ici appelait
    # _make_combo une 2e fois ; or il dépend du pricing Kambi FLAKY -> les 2 appels pouvaient diverger
    # (COMBOPICK 2 jambes publié vs optimiseur 3 jambes réglé : Mexique-Angleterre 2026-07-06). Repli calcul
    # local UNIQUEMENT si non fourni (rétrocompat pour un éventuel autre appelant).
    if combo is None:
        combo = _make_combo(analysis, sport, m.get("home", ""), m.get("away", ""),   # combiné grand tournoi
                            event_id=str(m.get("id")),
                            comp=m.get("comp") or m.get("circuit") or "")
    if combo:
        side["combo"] = combo
    calib = _parse_calib(analysis, sport, m.get("home", ""), m.get("away", ""))   # prédictions fantômes (calibrage)
    if calib:
        side["shadow"] = calib
    if validation:                       # verdict du panel de validateurs (3 agents) sur le pari retenu
        side["validation"] = validation
    if votes and votes[0] is not None:
        side["pub_home"], side["pub_away"] = votes[0] / 100, votes[1] / 100
        if len(votes) > 2 and votes[2] is not None:
            side["pub_draw"] = votes[2] / 100
    _sp_path = os.path.join(OUT, f"{sport}_{fid}.json")
    # FIGER LE PARI PUBLIÉ (demande user 2026-07-14) : si un pari a déjà été GELÉ à sa publication, on le
    # PRÉSERVE tel quel à travers le rescan -> il n'est jamais retiré ni re-prixé (l'abonné a parié à ce prix).
    try:
        if os.path.exists(_sp_path):
            _old_sc = json.load(open(_sp_path, encoding="utf-8"))
            if isinstance(_old_sc.get("published_bet"), dict) and _old_sc["published_bet"].get("sel"):
                side["published_bet"] = _old_sc["published_bet"]
    except (OSError, ValueError):
        pass
    with open(_sp_path, "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="foot", help="foot,tennis,basket (séparés par virgule)")
    ap.add_argument("--top", type=int, default=3,
                    help="top N matchs par sport/jour (défaut lean : 3 — qualité > quantité)")
    ap.add_argument("--hours", type=float, default=24.0,
                    help="fenêtre : ne scanner que les matchs à venir dans N heures (défaut 24). "
                         "Accepte les décimaux (ex. 1.5 pour les vagues rapprochées).")
    ap.add_argument("--force", action="store_true", help="ignore le cache 6 h")
    ap.add_argument("--only-big", action="store_true",
                    help="scanner UNIQUEMENT les gros tournois (Coupe du Monde)")
    ap.add_argument("--match", default="",
                    help="ne (ré-)analyser QUE les matchs dont le nom contient ce texte (ex. « Suisse ») ; "
                         "force la ré-analyse de CE match seul (contourne gel + cache), sans toucher les autres")
    ap.add_argument("--no-notify", action="store_true",
                    help="régénère l'analyse SANS re-poster sur Telegram (publication à décider ensuite)")
    ap.add_argument("--refresh-early", action="store_true",
                    help="ré-analyse UNE fois un match déjà publié mais analysé TROP TÔT (lead > --hours) "
                         "quand il approche du coup d'envoi -> pick frais. Les matchs déjà frais sont gelés.")
    ap.add_argument("--programme", action="store_true",
                    help="MATIN : sélectionne les matchs du jour (top N/sport), enregistre le programme et "
                         "poste la LISTE sur Telegram, SANS analyser (les paris viennent ~1 h avant chacun).")
    ap.add_argument("--from-programme", action="store_true",
                    help="ne (ré-)analyser QUE les matchs du programme du jour (data/day_programme.json).")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    # Le scan AUTORISE les gros endpoints (scheduled-events) via proxy : il les met en cache
    # (1/sport/jour), donc la conso reste minime — contrairement à l'app live qui les refuse.
    from app import sofa_http
    sofa_http.allow_bulk_proxy = True
    sports = [s.strip() for s in args.sport.split(",") if s.strip()]
    # MODE PROGRAMME (matin) : sélectionne + poste la LISTE du jour, SANS analyser, puis sort.
    if args.programme:
        async with httpx.AsyncClient(timeout=20) as client:
            await _build_and_post_programme(client, sports, args)
        return
    _prog_ids = _load_programme_ids() if args.from_programme else None
    total_t0 = time.time()
    n_gen = 0
    notif_lines: list[str] = []   # texte Telegram (repli si la carte image échoue) — 1 par match
    notif_cards: list = []        # données de la CARTE IMAGE par match (Option 2 : tout dans l'image)
    async with httpx.AsyncClient(timeout=20) as client:
        for sport in sports:
            try:
                # gros tournois (Coupe du Monde…) : inclus EN PLUS du top N s'ils sont dans la fenêtre.
                always = _is_big_match if sport == "foot" else None
                # --match / --from-programme : pool ÉLARGI pour ne rater aucun match ciblé (hors top-N).
                _nsel = 40 if (args.match or args.from_programme) else args.top
                top = await fetch_important(sport, _nsel, client, within_hours=args.hours, always=always)
            except Exception as e:
                print(f"[{sport}] sélection échouée : {e}")
                continue
            if args.only_big:      # CdM uniquement : on écarte tout match hors gros tournoi
                top = [m for m in top if _is_big_match(m.get("comp") or m.get("circuit") or "")]
            if args.match:         # cible : ne garder que les matchs dont le nom contient le texte demandé
                _q = args.match.lower()
                top = [m for m in top if _q in (m.get("name") or "").lower()]
            if args.from_programme:   # ne garder QUE les matchs du programme du jour (matin)
                top = [m for m in top if str(m.get("id")) in (_prog_ids or set())]
            store = _load_store(sport)
            print(f"[{sport}] {len(top)} matchs sélectionnés (profondeur de marché).")
            for _rank, m in enumerate(top):
                # NE JAMAIS analyser un match DÉJÀ COMMENCÉ (garde : la sélection filtre déjà le futur,
                # mais un match peut démarrer pendant le scan ; combinés/value = pré-match uniquement).
                mts = _kickoff_ts(m.get("start") or "")
                if mts and mts <= datetime.now(timezone.utc).timestamp():
                    print(f"  · {m['name']} : déjà commencé -> ignoré (pré-match uniquement).")
                    continue
                fid = _fiche_id(sport, m, store)   # id que la fiche utilise pour lier l'analyse
                if not fid and sport in ("tennis", "basket"):
                    # AUTONOME : pas dans le store -> on résout l'id SofaScore par noms + date
                    # (scheduled-events), au lieu de sauter le match.
                    fid = await _resolve_sofa(sport, m)
                # REPLI quand SofaScore est indisponible (API verrouillée côté SofaScore) : on NE SAUTE
                # PLUS le match. On prend l'id UNIBET comme clé de fiche (comme le foot le fait déjà) :
                # l'analyse est générée depuis Unibet et s'affiche normalement (la fiche se lie aux cotes
                # par NOM, pas par id Sofa). `sofa_id` restera vide -> le match ne se règle pas auto tant
                # qu'un id Sofa n'est pas résolu, mais l'OPPORTUNITÉ n'est pas perdue.
                unibet_fallback = False
                if not fid:
                    fid = str(m.get("id") or "")
                    if not fid:
                        print(f"  · {m['name']} : aucun id exploitable (ni Sofa ni Unibet), on saute.")
                        continue
                    unibet_fallback = True
                    print(f"  · {m['name']} : id SofaScore introuvable -> repli id Unibet (réglage différé).")
                # GEL DES PRONOS PUBLIÉS (fix 2026-07-06) : un match DÉJÀ posté sur Telegram et PAS encore
                # commencé n'est PLUS ré-analysé — sinon une nouvelle analyse peut CHANGER le combiné/pick que
                # les abonnés ont déjà VU (et parié), créant l'écart prédit≠réglé (cas Portugal-Espagne :
                # combiné 3 jambes @1.79 posté à 00:47 puis réécrit en 2 jambes @1.49 au re-scan 09h). Le
                # `_fresh` (cache 6 h) ne suffit pas : 8 h peuvent séparer 2 scans d'un même match. On teste
                # ICI (après résolution de `fid`) car le registre notify est clé par `fid` (= id SofaScore en
                # tennis/basket, ≠ id Unibet `m['id']`). La COTE reste rafraîchie live (app/main combo-refresh,
                # mêmes jambes) et le résultat est réglé. `--force` outrepasse (re-analyse volontaire).
                path = os.path.join(OUT, f"{sport}_{fid}.md")
                # REFRESH « analysé trop tôt » (--refresh-early, vagues rapprochées) : un match PUBLIÉ dont
                # l'analyse a été faite quand il était ENCORE hors fenêtre (lead > --hours) est ré-analysé
                # UNE fois à l'approche -> pick FRAIS près du coup d'envoi, puis re-posté. Auto-limité (voir
                # _analyzed_too_early). Sinon le GEL protège intégralement le pick déjà publié (inchangé).
                _refresh = (args.refresh_early and _notify.get_prono(str(fid))
                            and _analyzed_too_early(path, m.get("start"), args.hours))
                if not (args.force or args.match or _refresh) and _notify.get_prono(str(fid)):
                    print(f"  · {m['name']} : déjà publié sur Telegram (gelé) -> pas de ré-analyse.")
                    _set_programme_status(str(m.get("id")), "bet")   # publié -> statut « bet » même si sauté
                    continue
                # Analyse PRÉCÉDENTE mémorisée AVANT écrasement (ré-analyse : re-check 1 h avant OU --force) :
                # (a) _old_sig -> ne RE-POSTER que si le prono a CHANGÉ (jamais de spam d'un pick inchangé) ;
                # (b) _old_side -> report de l'ancien pick/prédictions en FANTÔMES si changé (calibrage).
                _old_sig = _old_side = None
                if _refresh or args.force:
                    try:
                        _old_side = json.load(open(os.path.join(OUT, f"{sport}_{fid}.json"), encoding="utf-8"))
                        _old_sig = _card_sig(_cd.build_prono_card(_old_side))
                    except (OSError, ValueError):
                        _old_side = None
                if _refresh:
                    print(f"  ↻ {m['name']} : publié le matin -> re-vérification (~1 h avant).")
                if not (args.force or args.match or _refresh) and _fresh(path):
                    print(f"  · {m['name']} : analyse fraîche en cache, on saute.")
                    # STATUT programme : REFLÉTER l'analyse EXISTANTE (sinon le site affiche « à analyser »
                    # pour un match DÉJÀ analysé — cas d'un programme réécrit puis scan sautant le cache).
                    # bet si un pari est retenu/combiné dans le sidecar, sinon abstained.
                    from app import analyses as _an_fc
                    try:
                        _sc_fc = json.load(open(os.path.join(OUT, f"{sport}_{fid}.json"), encoding="utf-8"))
                        _has_c_fc = bool((_sc_fc.get("combo") or {}).get("legs"))
                    except (OSError, ValueError):
                        _has_c_fc = False
                    _set_programme_status(str(m.get("id")),
                                          "bet" if (_has_c_fc or _an_fc.retained_bet(sport, str(fid)))
                                          else "abstained")
                    continue
                # id SofaScore pour les séries/H2H/votes. tennis/basket : la clé du store EST l'id
                # Sofa. foot : champ match_id du store si présent, sinon résolution autonome
                # (scheduled-events), sinon repli sur l'id Unibet (séries indispo mais analyse OK).
                if unibet_fallback:
                    sofa_id = ""                 # pas d'id Sofa fiable -> règlement différé (pas de faux id)
                elif sport == "foot":
                    sofa_id = str((store.get(fid) or {}).get("match_id") or "")
                    if not sofa_id or sofa_id == fid:
                        sofa_id = await _resolve_sofa(sport, m) or fid
                else:
                    sofa_id = fid
                built = await build_dossier(client, m, sport=sport, sofa_id=sofa_id)
                if not built:
                    print(f"  · {m['name']} : pas de marchés exploitables, on saute.")
                    continue
                doss, meta = built
                # GARDE-FOU « faits ≥2 sources » (demande user 2026-07-06) : le BASKET INTERNATIONAL obscur
                # (AfroBasket, qualifs asiatiques…) n'est couvert par AUCUNE source d'enrichissement (ESPN
                # basket = NBA/WNBA seulement) -> analyse sur cotes seules (data_score 0). On ne PARIE PAS un
                # match qu'on ne peut pas vérifier : on l'écarte AVANT l'analyse (économise le run Claude).
                # Ne s'applique qu'au basket (foot/tennis : un 0 est un hoquet réseau transitoire, pas structurel).
                if sport == "basket" and not [k for k, v in (meta.get("sources_prov") or {}).items() if v]:
                    print(f"  · {m['name']} : basket sans aucune source d'enrichissement -> écarté (cotes seules).")
                    continue
                # ÉLARGISSEMENT MESURÉ (demande user 2026-07-12 « élargir mais seulement si les sources
                # suivent ») : le pool passe de top 3 à top 5/sport. Le TOP 3 mainstream reste NON gaté (un
                # data_score bas y est un hoquet réseau transitoire, cf. gate basket ci-dessus). Mais la QUEUE
                # élargie (rangs 4-5) n'est retenue QUE si ≥2 sources d'enrichissement ont réellement répondu ;
                # sinon c'est une ligue que nos sources ne couvrent pas (ITF/AfroBasket/…) et on ne publie PAS
                # d'analyse creuse. Seuil ≥2 = principe « faits ≥2 sources ». Gros tournois (CdM) jamais gatés.
                _big_tail = _is_big_match(m.get("comp") or m.get("circuit") or "")
                _ds = len([k for k, v in (meta.get("sources_prov") or {}).items() if v])
                if _rank >= 3 and not _big_tail and _ds < 2:
                    print(f"  · {m['name']} : match élargi (rang {_rank + 1}) data_score {_ds}<2 "
                          f"-> écarté (sources insuffisantes).")
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
                # MODE STRICT : tableau de paris VIDE (aucun pari ≥ seuil) -> match NON RETENU.
                # On n'écrit RIEN (ni .md ni sidecar) et on RETIRE un éventuel scan précédent du
                # même match s'il n'est pas réglé. Le match pourra être ré-analysé au scan suivant
                # (compos/blessures publiées entre-temps peuvent débloquer un pari fiable).
                from app import analyses as _an
                from app.settle_analyst import code_from_pick
                bets = _an._parse_bets(_an._bets_section(analysis) or "")
                # GARDE-FOU RÈGLEMENT (demande user : ne JAMAIS garder un pari non vérifiable) : on ne
                # conserve un pari simple QUE s'il est VÉRIFIABLE — code de règlement non vide OU métrique
                # live (corners/tirs cadrés…). Sinon il resterait « en attente » à jamais -> on l'écarte.
                def _verifiable(b):
                    sel = b.get("sel", "")
                    if code_from_pick(sel, sport, m.get("home", ""), m.get("away", "")):
                        return True
                    return bool(_an._leg_metric({"sel": sel, "code": ""},
                                                m.get("home", ""), m.get("away", "")).get("live_ok"))
                _before = len(bets)
                bets = [b for b in bets if _verifiable(b)]
                if len(bets) < _before:
                    print(f"  · {m['name']} : {_before - len(bets)} pari(s) NON vérifiable(s) écarté(s).")
                # Si un COMBINÉ existe (CdM foot OU favori net tennis/basket), c'est LUI qui fait foi -> on
                # RETIENT le match même si la table de paris simples est vide.
                combo = _make_combo(analysis, sport, m.get("home", ""), m.get("away", ""),
                                    event_id=str(m.get("id")),
                                    comp=m.get("comp") or m.get("circuit") or "")
                # ANALYSE PAR JAMBE GARANTIE & PREMIUM (demande user 2026-07-11/12) : tout combiné dont les
                # jambes n'ont pas de why OU dont la synthèse est GÉNÉRIQUE (texte auto de l'optimiseur) est
                # enrichi via un appel Claude dédié (justification chiffrée par jambe + vraie synthèse). No-op
                # si déjà premium (COMBOPICK avec prose riche). -> tous les combinés au MÊME niveau.
                _cw = (combo or {}).get("why") or ""
                if combo and combo.get("legs") and (_cw.startswith("Combiné optimisé")
                                                    or not all(l.get("why") for l in combo["legs"])):
                    _analyze_samematch_legs(combo, analysis, m.get("home", ""), m.get("away", ""))
                # VALIDATION PAR PANEL (3 agents) du pari simple — SAUF si un combiné porte le match
                # (le combiné est le pari phare, structure validée à part — comme la CdM).
                validation = None
                skip_reason = None
                if not bets and not combo:
                    skip_reason = "aucun pari ≥ seuil"
                elif not combo:                  # pas de combiné -> le simple EST le pari -> panel
                    validation = await _validate_bet(doss, bets[0], bets[0].get("prob"), sport)
                    if validation["verdict"] == "rejete":
                        skip_reason = f"pari REJETÉ par le panel ({validation['n_ok']}/{validation['n']})"
                    else:
                        print(f"    ✓ panel : {validation['n_ok']}/{validation['n']} validé "
                              f"(consensus {validation['consensus_prob']}%)")
                if skip_reason:
                    print(f"  · {m['name']} : {skip_reason} -> match écarté (non retenu, {dt:.0f}s).")
                    # PARI PROVISOIRE (affichage seul) : le match est analysé mais sans value -> on propose
                    # quand même « le pari si l'on devait en jouer un » sur le programme (demande user
                    # 2026-07-09). Stocké dans le programme, JAMAIS dans les paris/stats -> ROI intact.
                    _prov = _provisional_pick(analysis, meta, m)
                    if _prov:
                        _prov["fid"] = str(fid)   # clé du .md -> la carte provisoire peut ouvrir l'analyse
                    _set_programme_status(str(m.get("id")), "abstained", provisional=_prov)   # + pari indicatif
                    _track_provisional(sport, m, _prov)   # suivi SÉPARÉ info-seule (jamais dans le ROI réel)
                    # PROVISOIRE : on GARDE le .md (analyse consultable au clic sur la carte). Le .md est
                    # PUREMENT AFFICHAGE — list_for/stats/calibration ne lisent QUE les .json -> ROI/calib
                    # INCHANGÉS. On (ré)écrit le .md et on ne le supprime pas ; on ne retire QUE le .json.
                    if _prov:
                        try:
                            with open(path, "w", encoding="utf-8") as _fmd:
                                _fmd.write(f"<!-- provisoire · {datetime.now(timezone.utc).isoformat()} -->\n\n"
                                           + analysis + "\n")
                        except OSError:
                            pass
                    side_p = os.path.join(OUT, f"{sport}_{fid}.json")
                    try:
                        old = json.load(open(side_p, encoding="utf-8"))
                        settled = (bool((old.get("result") or {}).get("score"))
                                   or any(b.get("result") for b in (old.get("bets") or [])))
                    except (OSError, ValueError):
                        settled = False
                    # Ne JAMAIS effacer un match RÉGLÉ (historique) NI un prono DÉJÀ PUBLIÉ : si une
                    # ré-analyse (--refresh-early / --force) bascule en abstention, on GARDE le sidecar
                    # publié -> le site continue d'afficher EXACTEMENT le pari reçu par l'abonné (cohérence
                    # Telegram = site ; sinon le match disparaissait du site, bug audit).
                    _published = bool(_notify.get_prono(str(fid)))
                    if not settled and not _published:
                        # FANTÔMES (demande user 2026-07-10) : une abstention nourrit la CALIBRATION. On écrit
                        # un sidecar MINIMAL `abstained` (méta + shadow SEULS -> aucun pari/stat_bet/combo) au
                        # lieu de le supprimer -> les fantômes entrent dans la calibration. list_for IGNORE les
                        # sidecars `abstained` et les stats ne comptent que les paris -> ROI/affichage INCHANGÉS.
                        _shadow = _parse_calib(analysis, sport, m.get("home", ""), m.get("away", ""))
                        if _shadow:
                            _o = (meta.get("odds") if meta else None) or (None, None, None)
                            try:
                                json.dump({"sport": sport, "id": str(fid), "sofa_id": str(sofa_id or ""),
                                           "home": m.get("home", ""), "away": m.get("away", ""),
                                           "name": m.get("name", ""), "comp": m.get("comp", ""),
                                           "start": m.get("start", ""), "o1": _o[0], "ox": _o[1], "o2": _o[2],
                                           "shadow": _shadow, "abstained": True,
                                           "generated": datetime.now(timezone.utc).isoformat()},
                                          open(side_p, "w", encoding="utf-8"), ensure_ascii=False)
                            except OSError:
                                pass
                            if not _prov:               # pas de provisoire -> pas d'analyse à consulter -> .md inutile
                                try:
                                    os.remove(path)
                                except OSError:
                                    pass
                        else:                           # aucun fantôme exploitable -> comportement d'avant
                            _exts = (".json",) if _prov else (".json", ".md")
                            for ext in _exts:
                                try:
                                    os.remove(os.path.join(OUT, f"{sport}_{fid}{ext}"))
                                except OSError:
                                    pass
                    continue
                # Pas d'entête « # {nom} » : la fiche affiche déjà le nom du match (doublon évité).
                header = f"<!-- généré {datetime.now(timezone.utc).isoformat()} · {dt:.0f}s -->\n\n"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(header + analysis + "\n")
                votes = await _fetch_votes(client, sport, sofa_id)
                surl = await _sofa_url(sofa_id)
                _write_sidecar(sport, fid, sofa_id, m, meta, analysis, votes, surl, validation, combo)  # -> board (MÊME combo que la notif)
                if _old_side:                      # ré-analyse (refresh/force) : ancien pick/prédictions -> fantômes
                    _carry_shadow_from_old(sport, fid, _old_side)
                _purge_duplicates(sport, fid, m)   # le scan le plus récent REMPLACE l'ancien
                n_gen += 1
                # === Message Telegram PRO (HTML) : en-tête match + lieu/jour/heure, puis le(s) pari(s) ===
                _emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}.get(sport, "•")
                _pick = _safe_pick(analysis)
                _rb = _an.retained_bet(sport, str(m.get("id")))   # pari simple AFFICHÉ (sel/cote/prob) ou None
                _has_combo = bool(combo and combo.get("legs"))
                # STATUT programme (site) : « bet » SEULEMENT si un pari est PUBLIABLE (combiné OU simple
                # RETENU = ≥65 % + EV+ + garde-fous) — pas juste « analysé » (ex. favori @1.16 sans value ->
                # « abstained »). ⚠️ retained_bet doit utiliser `fid` (clé du sidecar = id SofaScore en
                # basket/tennis), PAS l'id Unibet `m['id']` : sinon un pari retenu basket/tennis est vu
                # « None » -> statut « abstained » à tort -> DOUBLON (match dans « Paris du jour » ET dans
                # le programme). Bug vécu Connecticut Sun (id Unibet 1026378520 ≠ sidecar 15415798).
                _rb_status = _an.retained_bet(sport, str(fid))
                if _has_combo or _rb_status:
                    _set_programme_status(str(m.get("id")), "bet")
                else:                              # analysé, pari dans le tableau mais non RETENU -> provisoire
                    _prov2 = _provisional_pick(analysis, meta, m)
                    if _prov2:
                        _prov2["fid"] = str(fid)   # .md/sidecar déjà écrits ici -> carte provisoire cliquable
                    _set_programme_status(str(m.get("id")), "abstained", provisional=_prov2)
                    _track_provisional(sport, m, _prov2)   # suivi SÉPARÉ info-seule
                # Le simple n'est annoncé que s'il est à l'affiche sur l'app : à combiné -> seulement s'il
                # aurait été RETENU ; hors combiné -> le « plus sûr ».
                _pick_shown = bool(_rb) if _has_combo else bool(_pick or _rb)
                _bits = []
                if m.get("comp"):
                    _bits.append(html.escape(str(m["comp"])))
                try:
                    _dt = datetime.fromisoformat((m.get("start") or "").replace("Z", "+00:00"))
                    _dd = (_dt.date() - datetime.now(timezone.utc).date()).days
                    _day = "aujourd'hui" if _dd == 0 else ("demain" if _dd == 1 else _dt.strftime("%d/%m"))
                    _bits.append(f"{_day} {_dt.strftime('%H:%M')}")
                except ValueError:
                    pass
                _line = f"{_emo} <b>{html.escape(m['name'])}</b>"
                if _bits:
                    _line += f"\n<i>{' · '.join(_bits)}</i>"
                if _pick_shown:
                    if _rb:
                        _sel = html.escape(str(_rb.get("sel", "")))
                        _co, _pr = _rb.get("cote"), _rb.get("prob")
                        _stat = " · ".join(x for x in (
                            f"Cote <b>{_co:g}</b>" if _co else "",
                            f"Confiance <b>{_pr}%</b>" if _pr else "") if x)
                        _line += f"\n\n• <b>{_sel}</b>" + (f"\n{_stat}" if _stat else "")
                    else:
                        _mm = re.search(r"(.+?)\s*@\s*([\d]+[.,][\d]+)", _pick)
                        if _mm:
                            _line += (f"\n\n• <b>{html.escape(_mm.group(1).strip())}</b>"
                                      f"\nCote <b>{_mm.group(2).replace(',', '.')}</b>")
                        else:
                            _line += f"\n\n• <b>{html.escape(_pick)}</b>"
                if _has_combo:
                    _legs = combo["legs"]
                    _cote = (f"{combo['real_odds']:.2f}" if combo.get("real_odds")
                             else f"{combo.get('total', '?')}")
                    _line += f"\n\n• <b>Combiné · cote {_cote}</b>"
                    for _lg in _legs:
                        _c = _lg.get("cote")
                        _line += f"\n• {html.escape(str(_lg.get('sel', '')))}" + (f" — <b>{_c}</b>" if _c else "")
                if not _pick_shown and not _has_combo:
                    _line += "\n<i>(calibration seule)</i>"
                # --- Données de la CARTE IMAGE — POINT UNIQUE app/card_data (mêmes données qu'au repost) ---
                # Carte construite depuis le SIDECAR FRAÎCHEMENT ÉCRIT (source de vérité, combiné FINAL
                # inclus) plutôt que des variables de boucle : _make_combo est appelé 2× (sidecar + notif)
                # et le pricing Kambi est flaky réseau -> un combiné pouvait manquer à l'envoi Telegram
                # (bug récurrent : Winnipeg, combinés tennis…). Repli sur les variables si lecture KO.
                try:
                    _side_fresh = json.load(open(os.path.join(OUT, f"{sport}_{fid}.json"), encoding="utf-8"))
                except (OSError, ValueError):
                    _side_fresh = {"sport": sport, "id": m.get("id"), "name": m.get("name"),
                                   "comp": m.get("comp"), "start": m.get("start"), "pick": _pick,
                                   "combo": combo}
                _card = _cd.build_prono_card(_side_fresh)
                # RÉ-ANALYSE (re-check 1 h avant OU --force) : ne REPUBLIER QUE si le prono a CHANGÉ vs ce qui
                # était déjà publié. Identique -> rien reposté (pas de spam abonnés) ; le sidecar est déjà
                # réécrit (mtime frais) -> pas de boucle. Un NOUVEAU match (jamais publié) a _old_sig=None
                # -> posté normalement. (demande user 2026-07-08)
                if _old_sig is not None and _card_sig(_card) == _old_sig:
                    print(f"  = {m['name']} : prono INCHANGÉ à la ré-analyse -> pas de repost.")
                    await asyncio.sleep(SCAN_GAP)
                    continue
                if _refresh or (args.force and _old_sig is not None):
                    print(f"  🔄 {m['name']} : prono MIS À JOUR -> republié.")
                notif_lines.append(_line)
                notif_cards.append(_card)
                print(f"  ✓ {m['name']} : {len(analysis)} car. en {dt:.0f}s -> {os.path.basename(path)}")
                await asyncio.sleep(SCAN_GAP)   # lisse la charge SofaScore entre 2 matchs
    print(f"\nTerminé : {n_gen} analyse(s) générée(s) en {time.time() - total_t0:.0f}s. Dossier : {OUT}")
    # Notification Telegram (no-op si non configuré) : UN MESSAGE PAR MATCH (pas de récap groupé,
    # pas de suppression). Chaque message est autonome (sport + match + pari(s)).
    # --no-notify : régénère l'analyse SANS re-poster (ré-analyse ciblée d'un match déjà publié -> on
    # évite un doublon/prono changé chez les abonnés ; la décision de publier reste manuelle).
    if args.no_notify and notif_lines:
        print("  (--no-notify : analyse régénérée, AUCUN envoi Telegram)")
    if notif_lines and not args.no_notify:
        try:
            from app import notify
            if notify.configured():
                import card_image   # tools/card_image.py (même dossier que ce script)
                os.makedirs("data/_cards", exist_ok=True)
                # ORDRE CHRONOLOGIQUE des coups d'envoi (les cartes les plus tôt en premier)
                _order = sorted(range(len(notif_cards)),
                                key=lambda i: (notif_cards[i] or {}).get("_start") or "zzz")
                for _i in _order:
                    _line, _card = notif_lines[_i], notif_cards[_i]
                    # PAS de carte = build_prono_card a décidé de NE RIEN publier (pari simple NON retenu
                    # / « calibration seule ») -> on s'ABSTIENT. Sinon le repli texte re-postait des
                    # favoris sans value (@1.1-1.4) que les stats ne comptent pas -> incohérence
                    # « posté ≠ compté » (cf. card_data : pick_shown = bool(rb)).
                    if not _card:
                        continue
                    _sent = None
                    try:                            # carte image (Option 2 : tout dans l'image)
                        _png = f"data/_cards/scan_{_i}.png"
                        await card_image.render_card(_card, _png)
                        _sent = notify.send_photo_sync(_png, "")
                        if _sent:                    # mémorise l'id du prono -> le résultat y répondra
                            notify.remember_prono(_card.get("_mid"), _sent, _card.get("match"))
                            # FIGE le pari CONSEILLÉ dès la publication (demande user 2026-07-14) -> ni
                            # retiré ni re-prixé au rescan (l'abonné a parié à ce prix). Idempotent.
                            try:
                                if _card.get("_sport") and _card.get("type") != "combo":
                                    from app import analyses as _an_fz
                                    _an_fz.freeze_published_bet(_card["_sport"], _card["_mid"])
                            except Exception:
                                pass
                    except Exception as _ce:
                        print(f"  (carte image échouée, repli texte : {_ce})")
                    if not _sent:                   # la carte EXISTE mais rendu/envoi KO -> repli texte
                        await notify.send(_line)
        except Exception as _exc:
            print(f"  (notif Telegram ignorée : {_exc})")

    # COMBINÉ MULTISPORT DU JOUR (info seule, hors ROI réel) — 1 par jour : reprend les paris LES PLUS
    # PROBABLES de tous les matchs analysés du jour, cote ≥ 1.9, taux de réussite maximal (peut mélanger
    # sports et marchés). Construit en FIN de scan (tous les sidecars écrits). Publié aux abonnés (Telegram)
    # comme les autres pronos. record_daily fige dès l'envoi (published = frozen -> pas de re-scan changeant).
    try:
        import datetime as _dt
        from app import combo_daily as _cdaily
        _day = _cdaily.day_key()          # clé-jour UNIQUE (jour sportif local 06h→06h, source combo_daily)
        _prev = _cdaily.today(_day)
        if _prev and (_prev.get("sent") or _prev.get("result")):
            print("  🎯 Combiné du jour : déjà publié aujourd'hui (figé).")     # pas de re-analyse Claude
        else:
            _combo = _cdaily.build_for_day(_day)
            if _combo:
                _analyze_combo_legs(_combo)          # ANALYSE DÉDIÉE par jambe (comme un pari à jouer)
                if _cdaily.record_daily(_combo, _day):
                    print(f"  🎯 Combiné du jour : cote {_combo['cote']} · {round(_combo['prob'] * 100)}% · "
                          f"{len(_combo['legs'])} jambes"
                          f"{' (jambes analysées)' if any(l.get('why') for l in _combo['legs']) else ''}.")
                    if not args.no_notify:
                        from app import notify
                        if notify.configured() and await notify.send(_cdaily.telegram_text(_combo)):
                            _cdaily.mark_sent(_day)  # figé après publication aux abonnés
            else:
                print("  🎯 Combiné du jour : vivier insuffisant pour atteindre 1,95 aujourd'hui.")
        # DÉDUP (demande user 2026-07-12) : le combiné du jour est construit APRÈS les provisoires -> ses
        # jambes ont pu être trackées en provisoire pendant la boucle. On les retire ICI pour qu'un match
        # n'apparaisse JAMAIS à deux endroits (combiné du jour ET provisoire). No-op si rien à retirer.
        from app import provisional as _pvt
        _npr = _pvt.prune_retained()
        if _npr:
            print(f"  · {_npr} provisoire(s) retiré(s) (jambe(s) du combiné du jour -> pas de doublon).")
    except Exception as _exc:
        print(f"  (combiné du jour ignoré : {_exc})")


if __name__ == "__main__":
    asyncio.run(main())
