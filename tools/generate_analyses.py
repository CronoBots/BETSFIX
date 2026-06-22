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
    "yellow cards per game ») ET un enjeu clair (match tendu/rival) ; au moindre doute, SKIP les cartons.\n")

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
    "   ⛔ N'utilise PAS : « premier but / premier buteur » (pas encore réglable -> resterait en attente), "
    "les CARTONS (règle 1), ni les TIRS TOTAUX (« Total de tirs +X.5 » : 0/2 chez nous). Les « tirs CADRÉS "
    "» avec un nombre RESTENT autorisés. Reformule toute autre idée vers un marché fiable.\n"
    "   ⛔ COHÉRENCE OBLIGATOIRE : TOUTES les jambes vont dans le MÊME SENS (la domination du MÊME camp). "
    "JAMAIS deux jambes CONTRADICTOIRES — ex. « Angleterre +0.5 » (ne perd pas) AVEC « Croatie -1.5 » "
    "(gagne par 2+) = impossibles ensemble, le combiné ne peut PAS passer. Choisis UN favori et tiens-t'y "
    "sur toutes les jambes.\n"
    "3) Chaque jambe = sélection TRÈS PROBABLE (proba ≥ ~75 %), appuyée par les faits/tendances du dossier "
    "(forme, classement, blessés, tendances corners/buts Flashscore). 3 jambes (4 seulement si chacune "
    "reste ≥ 75 %).\n"
    "4) COTE : comme les jambes sont corrélées, Unibet RÉDUIT la cote combinée — c'est ASSUMÉ et ACCEPTÉ "
    "(on achète de la fiabilité, pas du rendement). Vise une cote combinée (produit des cotes réelles) "
    "≈ 1.70–2.60. N'utilise QUE des cotes réelles du bloc ci-dessus.\n"
    "5) OBLIGATOIRE — un combiné CdM existe TOUJOURS : ne décline JAMAIS. Si le match n'a pas de favori "
    "écrasant (coin-flip), n'invente pas une fausse domination : bâtis le combiné le plus SÛR possible à "
    "partir de marchés de « forme de match » plutôt que de domination — total de buts (« Plus/Moins de "
    "X.5 »), double chance sur le camp LÉGÈREMENT favori, total d'une équipe, les deux équipes marquent "
    "Oui/Non — en choisissant les 2-3 jambes les PLUS probables (≥ ~72 %) qui se TIENNENT ensemble. "
    "Quitte à viser une cote un peu plus basse (1.55+) si c'est le seul moyen de rester fiable. Mais "
    "PRODUIS toujours un combiné.\n"
    "Ajoute À LA FIN, après la section Mise, EXACTEMENT ce format (CHAQUE jambe avec SON explication "
    "détaillée APRÈS le tiret — pourquoi CETTE jambe passe, factuel et chiffré ; chaque explication est "
    "une PHRASE COMPLÈTE : MAJUSCULE au début, point final, ponctuation et virgules correctes) :\n"
    "## 🎲 Combiné\n"
    "- <sélection exacte 1> @<cote> — <pourquoi cette jambe : 1 à 2 phrases factuelles et chiffrées>\n"
    "- <sélection exacte 2> @<cote> — <pourquoi cette jambe>\n"
    "- <sélection exacte 3> @<cote> — <pourquoi cette jambe>\n"
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
    "favori NET et une domination LISIBLE ; sinon écris CLAIREMENT qu'aucun combiné solide n'existe (ne "
    "FORCE jamais). Philosophie : on achète la CHANCE DE PASSER, pas la grosse cote. Règles STRICTES :\n"
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
    "favori NET ; sinon écris CLAIREMENT qu'aucun combiné solide n'existe (ne FORCE jamais). On achète la "
    "CHANCE DE PASSER, pas la grosse cote. Règles STRICTES :\n"
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
    "• SÉRIES SOFASCORE (forme récente factuelle, déjà mappées aux marchés). Base principale. Si une "
    "série contredit ton intuition web, suis la série.\n"
    "• DONNÉES MULTI-SOURCES (ESPN / FotMob / Understat) : forme avec adversaires+scores, classements "
    "À JOUR, blessés/absents nominatifs, H2H, xG, météo, fatigue/back-to-back. Source indépendante "
    "n°2 : un fait présent ici ET confirmé par ta recherche web (ou SofaScore) = 2 sources "
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
    "concrets, français impeccable, ZÉRO généralité ni remplissage ni redite — mais une analyse FOUILLÉE.\n"
    "- **À éviter / SKIP :** ce qui est piégeux ; si le match est un coin-flip, dis-le et recommande "
    "de SKIP (ne rien jouer est une décision gagnante).\n\n"
    "## 💰 Mise\n"
    "Une phrase : mise PLATE et petite EXPRIMÉE EN % DE BANKROLL (ex. « 1 à 2 % de la bankroll »), "
    "JAMAIS en « unités »/« u » ni en €, JAMAIS de combiné, 1 à 2 paris max par jour. Factuel, en français.\n\n"
    "Enfin, AJOUTE EN DERNIÈRE LIGNE, pour le règlement auto, au format EXACT `PICK: <CODE>` "
    "correspondant à TON pari. HOME = 1re équipe/joueur, AWAY = 2e. UNIQUEMENT un "
    "de ces codes (privilégie un marché RÉGLABLE pour ton Pari 1) :\n"
    "- Total buts/points : `OVER 2.5` / `UNDER 3.5`\n"
    "- Total d'une ÉQUIPE : `TEAMTOT HOME OVER 1.5` / `TEAMTOT AWAY UNDER 85.5`\n"
    "- Handicap : `HCAP HOME -1.5` / `HCAP AWAY +10.5` (ligne signée)\n"
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
    txt = ("\n\nDONNÉES SOFASCORE TENNIS (factuel — base à croiser avec ta recherche web "
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
        lines = ["SÉRIES SOFASCORE (forme récente factuelle, mappées aux marchés — base PRINCIPALE "
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
    alt = await sources.extras(client, sport, match)
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
    # Combinés pré-construits Unibet (vraie cote corrélée) : on les met en cache (pour re-pricer le
    # combiné de l'analyste après coup) ET on injecte le menu pour BIAISER l'analyste vers un combiné
    # qui en fait partie (-> on connaîtra sa vraie cote). Foot/basket seulement (tennis : 0 prepack).
    if combo and sport in ("foot", "basket"):
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
    meta = {"odds": odds, **sx}   # odds + streaks/h2h structurés -> sidecar
    return text, meta


def run_claude(prompt: str, timeout: int = 360) -> str:
    """Lance Claude en headless sur l'abonnement et renvoie l'analyse (stdout)."""
    exe = shutil.which("claude") or "claude"
    p = subprocess.run([exe, "-p", "--dangerously-skip-permissions"], input=prompt,
                       text=True, capture_output=True, timeout=timeout, encoding="utf-8")
    return (p.stdout or "").strip()


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
    return ("\n\nCATALOGUE COMBINABLE BET BUILDER — au lieu d'un combiné figé, propose un VIVIER de "
            "5 à 6 JAMBES CANDIDATES prises DANS CETTE LISTE. Un OPTIMISEUR choisira la meilleure "
            "combinaison combinable visant une VRAIE cote ≥ 1.80 avec la chance de passer maximale.\n"
            "⚠️⚠️ CHANGEMENT DE LOGIQUE — ceci REMPLACE toute consigne de « domination corrélée » plus "
            "haut. On calcule désormais la VRAIE cote Unibet, et Unibet RABOTE LOURDEMENT les jambes "
            "CORRÉLÉES (ex. « équipe gagne » + « équipe marque 2 buts » + « +1.5 buts » = 3 fois le même "
            "scénario buts -> cote rabotée de 30 %, value détruite). Il faut désormais des jambes "
            "INDÉPENDANTES (qui ne décrivent PAS le même scénario) :\n"
            "  • Mélange des ANGLES SANS LIEN : 1 résultat (double chance) + 1 total de buts + 1 jambe "
            "d'un AUTRE registre (une équipe marque, mi-temps, props joueur…). JAMAIS 3 jambes « buts ».\n"
            "  • Inclus AU MOINS 1-2 candidates à cote 1.5-2.5 (sinon impossible d'atteindre 1.80 réel).\n"
            "  • Pas deux totaux qui se recoupent (équipe + match) ; chaque candidate ≥ ~65 % ; "
            "PAS de cartons/corners.\n"
            "⚠️ FORMAT EXACT, une ligne par candidate (après la section Mise), id du catalogue ENTRE "
            "CROCHETS + ta proba honnête :\n"
            "`POOL: <sélection> @<cote> [<id>] (<prob>%) — <pourquoi cette jambe, factuel et chiffré>`\n"
            "(NE produis PAS de ligne COMBO: ni de section 🎲 : l'optimiseur bâtit le combiné final.) "
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


_COMBO_REAL_MIN = 1.80      # vraie cote minimale visée pour le combiné (valeur, pas du produit illusoire)
_COMBO_REAL_MAX = 4.20      # au-delà = trop long (proba trop faible) -> on évite
_COMBO_PROB_MIN = 0.33      # chance de passer minimale (évite les longshots quand on maximise l'EV)


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


def _build_combo_from_pool(eid: str, cands: list, max_legs: int = 3) -> dict | None:
    """Choisit, dans le VIVIER, la meilleure combinaison COMBINABLE par EV (= vraie cote × proba :
    capture À LA FOIS la value/le faible rabot ET la chance), sous contraintes vraie cote ≥
    _COMBO_REAL_MIN et chance ≥ _COMBO_PROB_MIN. Si rien ne tient les seuils, prend la combinaison
    combinable à la plus HAUTE vraie cote. None sinon."""
    from itertools import combinations
    cands = [c for c in cands if c.get("oid")][:6]
    n = len(cands)
    if n < 2:
        return None
    best, fallback = None, None     # best = (ev, real, prob, idx) ; fallback = plus haute vraie cote
    for size in range(min(max_legs, n), 1, -1):
        for idx in combinations(range(n), size):
            real = unibet.betbuilder_odds(eid, [cands[i]["oid"] for i in idx])
            if not real or real > _COMBO_REAL_MAX:
                continue
            prob = 1.0
            for i in idx:
                prob *= cands[i]["prob"] / 100
            if fallback is None or real > fallback[1]:
                fallback = (prob, real, idx)
            # EV maximale parmi les combinaisons qui tiennent la value (≥1.80) ET une chance correcte
            # -> meilleur rendement attendu (favorise les jambes peu corrélées = cote réelle plus haute).
            if real >= _COMBO_REAL_MIN and prob >= _COMBO_PROB_MIN:
                ev = real * prob
                if best is None or ev > best[0]:
                    best = (ev, real, prob, idx)
    if best:
        _, real, prob, idx = best
    elif fallback:
        prob, real, idx = fallback
    else:
        return None
    legs = []
    for i in idx:
        lg = {"sel": cands[i]["sel"], "cote": cands[i]["cote"], "code": cands[i]["code"]}
        if cands[i].get("why"):
            lg["why"] = cands[i]["why"]
        legs.append(lg)
    nv = 1.0
    for lg in legs:
        nv *= lg["cote"]
    return {"legs": legs, "total": round(nv, 2), "real_odds": round(real, 2),
            "shave": round(100 * (1 - real / nv), 1) if nv else None,
            "priced_by": "betbuilder_pool", "prob": round(prob * 100),
            "why": f"Combiné optimisé sur la VRAIE cote Unibet ({real:.2f}) — "
                   f"jambes variées peu corrélées, chance estimée {round(prob * 100)}%."}


def _make_combo(analysis: str, sport: str, home: str, away: str, event_id: str | None):
    """Combiné du match : d'abord l'OPTIMISEUR sur le vivier (vraie cote ≥1.80, chance max) ; à défaut
    de vivier exploitable, repli sur l'ancien parsing `COMBO:` (avec pricing/auto-trim)."""
    eid = str(event_id) if event_id else None
    if eid and _CATALOG_CACHE.get(eid):
        cands = _parse_pool(analysis, sport, home, away)
        built = _build_combo_from_pool(eid, cands) if cands else None
        if built:
            return built
    return _parse_combo(analysis, sport, home, away, event_id)


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
                   votes=None, sofa_url: str | None = None, validation: dict | None = None) -> None:
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
    if meta and meta.get("streaks"):
        side["streaks"] = meta["streaks"]
    if meta and meta.get("h2h"):
        side["h2h"] = meta["h2h"]
    circuit = m.get("circuit") or (meta.get("circuit") if meta else None)   # Unibet (path) prioritaire
    if circuit:
        side["circuit"] = circuit
    combo = _make_combo(analysis, sport, m.get("home", ""), m.get("away", ""),   # combiné grand tournoi
                        event_id=str(m.get("id")))
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
    with open(os.path.join(OUT, f"{sport}_{fid}.json"), "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="foot", help="foot,tennis,basket (séparés par virgule)")
    ap.add_argument("--top", type=int, default=3,
                    help="top N matchs par sport/jour (défaut lean : 3 — qualité > quantité)")
    ap.add_argument("--hours", type=int, default=24,
                    help="fenêtre : ne scanner que les matchs à venir dans N heures (défaut 24)")
    ap.add_argument("--force", action="store_true", help="ignore le cache 6 h")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    # Le scan AUTORISE les gros endpoints (scheduled-events) via proxy : il les met en cache
    # (1/sport/jour), donc la conso reste minime — contrairement à l'app live qui les refuse.
    from app import sofa_http
    sofa_http.allow_bulk_proxy = True
    sports = [s.strip() for s in args.sport.split(",") if s.strip()]
    total_t0 = time.time()
    n_gen = 0
    notif_lines: list[str] = []   # récap Telegram (1 ligne par match retenu) -> envoyé à la fin
    async with httpx.AsyncClient(timeout=20) as client:
        for sport in sports:
            try:
                # gros tournois (Coupe du Monde…) : inclus EN PLUS du top N s'ils sont dans la fenêtre.
                always = _is_big_match if sport == "foot" else None
                top = await fetch_important(sport, args.top, client, within_hours=args.hours, always=always)
            except Exception as e:
                print(f"[{sport}] sélection échouée : {e}")
                continue
            store = _load_store(sport)
            print(f"[{sport}] {len(top)} matchs sélectionnés (profondeur de marché).")
            for m in top:
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
                path = os.path.join(OUT, f"{sport}_{fid}.md")
                if not args.force and _fresh(path):
                    print(f"  · {m['name']} : analyse fraîche en cache, on saute.")
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
                                    event_id=str(m.get("id")))
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
                    side_p = os.path.join(OUT, f"{sport}_{fid}.json")
                    try:
                        old = json.load(open(side_p, encoding="utf-8"))
                        settled = (bool((old.get("result") or {}).get("score"))
                                   or any(b.get("result") for b in (old.get("bets") or [])))
                    except (OSError, ValueError):
                        settled = False
                    if not settled:                # jamais toucher un match réglé (historique)
                        for ext in (".json", ".md"):
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
                _write_sidecar(sport, fid, sofa_id, m, meta, analysis, votes, surl, validation)  # -> board
                _purge_duplicates(sport, fid, m)   # le scan le plus récent REMPLACE l'ancien
                n_gen += 1
                _emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}.get(sport, "•")
                _pick = _safe_pick(analysis)
                # Cohérence app/Telegram : sur un match à combiné, on n'annonce le simple que s'il
                # est AFFICHÉ (retenu par la logique du site) — sinon seul le combiné est à l'affiche.
                if _pick and combo and combo.get("legs"):
                    if _an.retained_bet(sport, str(m.get("id"))) is None:
                        _pick = ""
                # Message Telegram soigné (HTML) : titre en gras + compétition/heure, puis le(s) pari(s)
                # avec les cotes en gras.
                _bits = []
                if m.get("comp"):
                    _bits.append(html.escape(str(m["comp"])))
                try:
                    _bits.append(datetime.fromisoformat((m.get("start") or "")
                                 .replace("Z", "+00:00")).strftime("%H:%M"))
                except ValueError:
                    pass
                _line = f"{_emo} <b>{html.escape(m['name'])}</b>"
                if _bits:
                    _line += f"\n<i>{' · '.join(_bits)}</i>"
                if _pick:
                    _ph = re.sub(r"@\s*([\d]+[.,][\d]+)", r"· <b>\1</b>", html.escape(_pick))
                    _line += f"\n\n• <b>Simple</b> · {_ph}"
                if combo and combo.get("legs"):
                    _legs = combo["legs"]
                    _cote = (f"{combo['real_odds']:.2f}" if combo.get("real_odds")
                             else f"{combo.get('total', '?')}")
                    _line += f"\n\n• <b>Combiné</b> · {len(_legs)} jambes · cote <b>{_cote}</b>"
                    for _lg in _legs:
                        _c = _lg.get("cote")
                        _line += f"\n– {html.escape(str(_lg.get('sel', '')))}" + (f" · <b>{_c}</b>" if _c else "")
                if not _pick and not (combo and combo.get("legs")):
                    _line += "\n<i>(calibration seule)</i>"
                notif_lines.append(_line)
                print(f"  ✓ {m['name']} : {len(analysis)} car. en {dt:.0f}s -> {os.path.basename(path)}")
                await asyncio.sleep(SCAN_GAP)   # lisse la charge SofaScore entre 2 matchs
    print(f"\nTerminé : {n_gen} analyse(s) générée(s) en {time.time() - total_t0:.0f}s. Dossier : {OUT}")
    # Notification Telegram (no-op si non configuré) : UN MESSAGE PAR MATCH (pas de récap groupé,
    # pas de suppression). Chaque message est autonome (sport + match + pari(s)).
    if notif_lines:
        try:
            from app import notify
            if notify.configured():
                for _line in notif_lines:
                    await notify.send(_line)
        except Exception as _exc:
            print(f"  (notif Telegram ignorée : {_exc})")


if __name__ == "__main__":
    asyncio.run(main())
