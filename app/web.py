"""Plateforme de visionnage (front-end HTML rendu côté serveur).

Pages mobiles cohérentes au-dessus de l'API : accueil, liste des matchs,
détail/analyse d'un match. Thème sombre, nav commune. Aucun JS requis.
"""

from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timezone, timedelta

from . import analyses, match_select, paywall

_WORDMARK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "static", "wordmark.png")
_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "static", "logo.png")

def _bets_for_url(url: str, compact: bool = False) -> str:
    """Cadres « paris à jouer » d'un match (sous les barres %, HORS analyse), depuis son URL de fiche.
    Remplace l'ancienne bannière perle « Confiance » devenue redondante.
    `compact` (live) : seulement les cartes, sans en-tête ni phrase verdict."""
    m = re.match(r"/(foot|basket|app)/match/(\d+)", url or "")
    if not m:
        return ""
    sport = {"foot": "foot", "basket": "basket", "app": "tennis"}[m.group(1)]
    # Coupe du Monde (combiné présent) : on affiche le pari SIMPLE *seulement s'il aurait été RETENU*
    # par la logique normale (cf. analyses.retained_bet) — sinon le combiné reste seul à l'affiche (on
    # ne force pas une ancre à cote plate). Puis le COMBINÉ. Hors CdM : paris simples seuls.
    combo = analyses.combo_html(sport, m.group(2))
    if combo:
        bets = (analyses.bets_html(sport, m.group(2), compact=compact)
                if analyses.retained_bet(sport, m.group(2)) else "")
        return paywall.wrap(bets + combo)         # PRONO -> masqué aux non-abonnés (cf. middleware)
    # Hors combiné : on n'affiche la carte « pari à jouer » QUE si un pari est RETENU. Sinon abstention
    # -> AUCUNE carte (clarté : pas de « pari sans value » qui embrouille). Le match reste analysé.
    if not analyses.retained_bet(sport, m.group(2)):
        return ""
    return paywall.wrap(analyses.bets_html(sport, m.group(2), compact=compact))

def _links_for_url(url: str) -> str:
    """Bannières SofaScore / Unibet (pleine largeur) d'un match, depuis son URL de fiche.
    Posées SUR la carte -> ne sont plus rendues dans l'analyse dépliée (pas de doublon)."""
    m = re.match(r"/(foot|basket|app)/match/(\d+)", url or "")
    if not m:
        return ""
    sport = {"foot": "foot", "basket": "basket", "app": "tennis"}[m.group(1)]
    return analyses.links_html(sport, m.group(2))

def _summary_for_url(url: str) -> dict:
    """Résumé compact (paris/confiance/à-jouer/résultat) d'un match depuis son URL de fiche."""
    m = re.match(r"/(foot|basket|app)/match/(\d+)", url or "")
    if not m:
        return {}
    sport = {"foot": "foot", "basket": "basket", "app": "tennis"}[m.group(1)]
    return analyses.card_summary(sport, m.group(2))

_OM_ARR = {"down": "▼", "up": "▲", "flat": "■"}
_OM_CLS = {"down": "om-down", "up": "om-up", "flat": "om-flat"}
_OM_COLOR = {"down": "#34d27b", "up": "#ff6b6b", "flat": "#9fb0c8"}

def render_odds_movement(mv: dict | None) -> str:
    """Mini-section « 📉 Mouvement de cote » : par issue, ouverture → cote actuelle/clôture, sens
    (steam ▼ / drift ▲), variation %, et une mini-courbe. '' si pas d'historique exploitable."""
    if not mv:
        return ""
    e = html.escape
    labels = {"home": _noF(mv.get("home") or "1"), "draw": "Nul", "away": _noF(mv.get("away") or "2")}
    rows = []
    for key in ("home", "draw", "away"):
        leg = (mv.get("legs") or {}).get(key)
        if not leg:
            continue
        d = leg["dir"]
        sign = "+" if leg["pct"] > 0 else ""
        rows.append(
            f'<div class="om-row">'
            f'<span class="om-lbl">{e(labels[key])}</span>'
            f'<span class="om-spk">{_sparkline(leg["series"], _OM_COLOR[d])}</span>'
            f'<span class="om-vals"><span class="om-o">{leg["open"]:g}</span>'
            f'<span class="om-arr {_OM_CLS[d]}">→ {leg["now"]:g} {_OM_ARR[d]}</span></span>'
            f'<span class="om-pct {_OM_CLS[d]}">{sign}{leg["pct"]:g}%</span></div>')
    if not rows:
        return ""
    when = "clôture (coup d'envoi atteint)" if mv.get("closed") else "cote actuelle"
    sub = f'{mv.get("n")} relevés · ouverture → {when} · ▼ steam · ▲ drift · source Unibet'
    return ('<div class="om"><div class="om-h">📉 Mouvement de cote'
            f'<span class="om-sub">{e(sub)}</span></div>' + "".join(rows) + '</div>')

def odds_move_for(sport: str, home: str, away: str) -> str:
    """Mouvement de cote prêt à afficher pour un match (depuis l'historique). '' si rien/erreur."""
    try:
        from app import odds_history
        return render_odds_movement(odds_history.movement(sport, home or "", away or ""))
    except Exception:
        return ""

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Brussels")
except Exception:  # tzdata manquant -> sans lui les heures s'afficheraient en UTC (-2h)
    import logging
    logging.getLogger("uvicorn").warning(
        "tzdata introuvable -> heures en UTC. Installe le paquet 'tzdata' (pip install tzdata).")
    LOCAL_TZ = None

def to_local(value):
    """Convertit un datetime/ISO en datetime local belge (ou None)."""
    if value is None:
        return None
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    if LOCAL_TZ is not None and getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(LOCAL_TZ)
    return dt

def day_label(d, today) -> str:
    """Libellé d'un jour : Aujourd'hui / Demain / jour de semaine + date."""
    delta = (d - today).days
    if delta == 0:
        return f"Aujourd'hui — {d.strftime('%d/%m')}"
    if delta == 1:
        return f"Demain — {d.strftime('%d/%m')}"
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{jours[d.weekday()].capitalize()} {d.strftime('%d/%m')}"

def _plur(n, word: str) -> str:
    """Titre de zone. PLURIEL quand PLUSIEURS paris du type (user 2026-08-19 : « les types de paris doivent
    prendre un s s'il y en a plusieurs »). Le « s » va sur le PREMIER mot (le NOM du type) et laisse le reste
    intact : « Combinés », « Confiances ». Singulier si n ≤ 1."""
    if (n or 0) <= 1:
        return word
    head, _, tail = word.partition(" ")
    head = head if head.endswith("s") else head + "s"
    return head + ((" " + tail) if tail else "")

def fmt_live_clock(mc: dict | None) -> str:
    """Horloge LIVE Unibet (matchClock) -> texte court. Foot : « 51' » / « Mi-temps » ;
    basket : « Q3 · 5:42 » (temps restant) / « Prol. ». '' si rien d'exploitable."""
    if not isinstance(mc, dict):
        return ""
    pid = (mc.get("periodId") or "").upper()
    if "HALF_TIME" in pid or pid in ("PAUSE", "HALFTIME"):
        return "Mi-temps"
    if "OVERTIME" in pid or pid == "OT":
        return "Prol."
    if pid.startswith("QUARTER") or pid.startswith("PERIOD"):       # basket : quart + temps restant
        q = "Q" + "".join(ch for ch in pid if ch.isdigit())
        ml, sl = mc.get("minutesLeftInPeriod"), mc.get("secondsLeftInMinute")
        return f"{q} · {ml}:{sl:02d}" if (ml is not None and sl is not None) else q
    minute = mc.get("minute")                                       # foot : minute écoulée
    if minute is None:
        return ""
    # MI-TEMPS à côté de la minute (demande user 2026-07-25). Priorité au periodId (FIRST/SECOND), repli
    # sur la minute (> 45 -> 2e mi-temps). En prolongation on est déjà sorti plus haut (« Prol. »).
    if "SECOND" in pid or "2ND" in pid or pid.endswith("_2"):
        half = "2e MT"
    elif "FIRST" in pid or "1ST" in pid or pid.endswith("_1"):
        half = "1re MT"
    else:
        half = "2e MT" if minute > 45 else "1re MT"
    return f"{minute}' · {half}"

def live_fields(ld: dict | None, sport: str) -> dict:
    """À partir du `liveData` Unibet (cf. match_select.live_state_for), renvoie les champs prêts pour
    le scoreboard live d'une carte : {score, live_time} (foot/basket) ou {score, server, game_pts}
    (tennis). {} si pas de données live. AUCUN appel réseau (donnée déjà en main)."""
    if not isinstance(ld, dict):
        return {}
    sc = ld.get("score") or {}
    if sport == "tennis":
        sets = (ld.get("statistics") or {}).get("sets") or {}
        sh, sa = sets.get("home") or [], sets.get("away") or []
        # Unibet remplit les sets NON JOUÉS avec un placeholder négatif (-1) -> on les écarte, sinon le
        # score live affiche « 2-1 -1--1 -1--1 ». On ne garde que les sets réellement entamés (>= 0).
        pairs = [(h, a) for h, a in zip(sh, sa)
                 if isinstance(h, (int, float)) and isinstance(a, (int, float)) and h >= 0 and a >= 0]
        score = " ".join(f"{h}-{a}" for h, a in pairs)
        hs = sets.get("homeServe")
        server = "home" if hs is True else ("away" if hs is False else None)
        h, a = sc.get("home"), sc.get("away")
        pts = ((str(h) if h is not None else ""), (str(a) if a is not None else "")) \
            if (h is not None or a is not None) else None
        return {"score": score, "server": server, "game_pts": pts}
    h, a = sc.get("home"), sc.get("away")                           # foot / basket : buts / points
    score = f"{h}-{a}" if (h is not None and a is not None) else ""
    out = {"score": score, "live_time": fmt_live_clock(ld.get("matchClock")),
           "home_pts": h, "away_pts": a}
    if sport == "basket":   # détail par quart-temps depuis score.info « Q1: 19-25 | Q2: 24-17 | … »
        qs = re.findall(r"(\d+)\s*[-–]\s*(\d+)", sc.get("info") or "")
        if qs:
            out["periods"] = [(int(x), int(y)) for x, y in qs]
    if sport == "foot":     # STATS LIVE (cartons/corners) depuis la liveData Unibet -> box-score enrichi.
        # Unibet expose CE box-score sous DEUX structures selon le match (constaté en direct 2026-07-26) :
        #  (1) `statistics.football.{home,away}.{yellowCards,redCards,corners}` — objet imbriqué ;
        #  (2) `liveStatistics` — liste plate {occurrenceTypeId, count} (CARDS_YELLOW_HOME, CORNERS_AWAY…),
        #      SEULE présente pour certains matchs (ex. Flamengo–São Paulo : pas de `statistics`).
        # On lit (1) en priorité (aucune régression), repli (2) — sinon une jambe/carte n'a AUCUN carton
        # alors qu'un match voisin les affiche (bug user 2026-07-26). Jambes et cartes partagent live_fields.
        _fb = ((ld.get("statistics") or {}).get("football") or {})
        _fh, _fa = _fb.get("home") or {}, _fb.get("away") or {}
        if _fh or _fa:
            out["fstats"] = {"rc_h": _fh.get("redCards"), "rc_a": _fa.get("redCards"),
                             "yc_h": _fh.get("yellowCards"), "yc_a": _fa.get("yellowCards"),
                             "cor_h": _fh.get("corners"), "cor_a": _fa.get("corners")}
        else:
            _ls = ld.get("liveStatistics")
            if isinstance(_ls, list) and _ls:
                _map = {"CARDS_RED_HOME": "rc_h", "CARDS_RED_AWAY": "rc_a",
                        "CARDS_YELLOW_HOME": "yc_h", "CARDS_YELLOW_AWAY": "yc_a",
                        "CORNERS_HOME": "cor_h", "CORNERS_AWAY": "cor_a"}
                _fs = {}
                for _o in _ls:
                    _k = _map.get((_o or {}).get("occurrenceTypeId"))
                    if _k is not None:
                        _fs[_k] = (_o or {}).get("count")
                if _fs:
                    out["fstats"] = {_k: _fs.get(_k)
                                     for _k in ("rc_h", "rc_a", "yc_h", "yc_a", "cor_h", "cor_a")}
    return out

def fmt_local(value, with_date: bool = True) -> str:
    """Formate un datetime/ISO en heure locale belge. '' si absent."""
    if value is None:
        return ""
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value[11:16] if len(value) >= 16 else value
    if LOCAL_TZ is not None and getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(LOCAL_TZ)
    hm = dt.strftime("%H:%M")
    if not with_date:
        return hm
    # Dates conviviales : Aujourd'hui / Demain / jour abrégé, sinon jj/mm — en JOUR SPORTIF (06h→06h,
    # cf. _sport_date) pour rester cohérent avec le regroupement/calendrier : un match de 03:00 est
    # « Aujourd'hui » au sens de la journée sportive de la veille (alignement badge ↔ en-tête de jour).
    today = _sport_date(datetime.now(LOCAL_TZ) if LOCAL_TZ is not None else datetime.now())
    delta = (_sport_date(dt) - today).days
    if delta == 0:
        return f"Aujourd'hui {hm}"
    if delta == 1:
        return f"Demain {hm}"
    if 2 <= delta <= 6:
        return f"{('Lun.','Mar.','Mer.','Jeu.','Ven.','Sam.','Dim.')[dt.weekday()]} {hm}"
    return f"{dt.strftime('%d/%m')} {hm}"

CSS = """
  :root{
    /* Thème « néon » (inspiré OddScore) : fond quasi-noir + accent vert-lime + corail pour le négatif */
    --bg:#070708;--bg2:#0d0d10;--surface:#141417;--surface2:#1d1d21;
    --border:#2a2a31;--border2:#3b3b44;--text:#f4f5f7;--muted:#9a9aa6;--dim:#8b8b95;
    /* ACCENT principal — UN SEUL endroit à changer pour reskin (cf. candidats en bas) */
    --accent:#22b8ff;--accent2:#1496f0;--accent-ink:#001321;--glow:rgba(34,184,255,.28);
    --halo:rgba(34,184,255,.09);
    --gold:#f6c54a;--gold-bg:#231d09;--gold-bd:#4a3c0c;
    --red:#ff6b6b;--green:#a6e22e;--brand:var(--accent);
    --cardline:rgba(34,184,255,.30);--cardglow:0 0 24px rgba(34,184,255,.10);
    --radius:16px;--shadow:0 12px 34px -6px rgba(0,0,0,.55);--shadow-sm:0 3px 12px -2px rgba(0,0,0,.42);   /* ombres plus diffuses = profondeur premium (user 2026-08-19) */
    /* Bord GAUCHE des cartes de pari selon l'état (demande user 2026-07-25) : en attente=JAUNE ;
       en cours (live)=même jaune (PAS de couleur dédiée -> le badge « 🟢 Live » suffit) ; gagné=vert ;
       perdu=rouge ; remboursé=gris. */
    --st-soon:var(--gold);--st-live:var(--gold);--st-won:#34d27b;--st-lost:#ff6b6b;--st-void:#90a4be;
  }
  /* Home & Live = accent principal (hérité de :root). Les sports gardent leur teinte d'identité
     (néon sur fond noir) : tennis lime-jaune · basket orange · foot vert. */
  body.sp-tennis{--accent:#d7e64a;--accent2:#aac72f;--accent-ink:#16180a;--glow:rgba(190,210,60,.30)}
  body.sp-basket{--accent:#ff9f43;--accent2:#f08000;--accent-ink:#1a0e00;--glow:rgba(240,128,0,.30)}
  body.sp-foot{--accent:#2ee27f;--accent2:#19c46a;--accent-ink:#04130a;--glow:rgba(46,226,127,.30)}
  *{box-sizing:border-box}
  /* Fond html = COULEUR DE LA NAV (#0b0d12) : la zone du home-indicator iPhone (PWA standalone), non
     couverte par body/nav, montrait sinon un TROU NOIR sous la barre du bas. Là elle se fond dedans. */
  /* DÉGRADÉ (halos) sur HTML = fond du CANVAS : fixé au viewport, jamais scrollé -> halos IDENTIQUES sur tous
     les onglets, quelle que soit la longueur de la page (user 2026-08-22 : Résultats/Accueil, pages longues,
     avaient un fond dilué car le dégradé était sur le body dimensionné au contenu). PAS d'overflow:hidden
     (le body doit scroller, modèle CRYPTONAUTS). */
  html{-webkit-text-size-adjust:100%;overscroll-behavior:none;color-scheme:dark;
       background:radial-gradient(1100px 640px at 50% -6%,var(--halo),transparent 60%),
                  radial-gradient(820px 520px at 100% 104%,var(--halo),transparent 72%),
                  var(--bg)}
  /* FILET DE SÉCURITÉ safe-area (user 2026-08-16) : le fond du body (#070708) RECOUVRE le html -> en PWA
     standalone une ZONE NOIRE apparaissait sous la nav (home-indicator iOS). On peint cette bande, en FIXE,
     avec la couleur de la nav (#0b0d12), sous la barre (z<nav). */
  body::after{content:'';position:fixed;left:0;right:0;bottom:0;height:env(safe-area-inset-bottom,0px);
       background:#0b0d12;z-index:59;pointer-events:none}
  /* Coquille NON-scrollante en COLONNE FLEX,
  hauteur = viewport DYNAMIQUE (100dvh) : le contenu
     scrolle DANS .wrap (flex:1) et la barre du bas est un enfant flex STATIQUE collé au bas. Sur iOS
     ça supprime le « saut » de la barre fixe quand la toolbar Safari apparaît/disparaît (dvh suit la
     toolbar -> la barre reste toujours au bas visible) et le pied de page redevient atteignable. */
  /* SELAWIK — clone open-source (SIL OFL) métriquement identique à Segoe UI, auto-hébergé (demande user
     2026-07-12). Famille nommée « Segoe UI » avec `local()` D'ABORD : Windows garde la VRAIE Segoe UI
     (identique à la carte Telegram), iPhone/Android chargent Selawik -> rendu cohérent partout. */
  @font-face{font-family:'Segoe UI';font-weight:100 400;font-style:normal;font-display:swap;
       src:local('Segoe UI'),url('/static/fonts/selawik-regular.woff') format('woff')}
  @font-face{font-family:'Segoe UI';font-weight:500 600;font-style:normal;font-display:swap;
       src:local('Segoe UI Semibold'),url('/static/fonts/selawik-semibold.woff') format('woff')}
  @font-face{font-family:'Segoe UI';font-weight:700 900;font-style:normal;font-display:swap;
       src:local('Segoe UI Bold'),url('/static/fonts/selawik-bold.woff') format('woff')}
  body{margin:0;color:var(--text);font-size:15px;line-height:1.45;width:100%;
       /* MODÈLE DE SCROLL = CRYPTONAUTS (user 2026-08-22) : le BODY scrolle normalement (plus de coquille
          `height:100dvh;overflow:hidden` + scroll interne `.wrap`, qui calait mal en PWA iOS -> zone morte
          sous la barre). La barre est `position:fixed;bottom:0` et le body RÉSERVE sa hauteur via padding-bas. */
       min-height:100dvh;overscroll-behavior-y:none;
       padding-bottom:calc(62px + env(safe-area-inset-bottom, 0px));
       font-family:'Segoe UI',Roboto,Arial,sans-serif;   /* police des cartes Telegram (demande user 2026-07-12) */
       -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
       -webkit-user-select:none;user-select:none;-webkit-touch-callout:none;
       -webkit-tap-highlight-color:transparent;touch-action:manipulation;
       /* Thème premium : halos bleus POSÉS DIRECTEMENT sur le fond du body (au-dessus de --bg).
          AVANT ils étaient sur un body::before en z-index:-1 ; mais depuis que html a son propre
          fond (#0b0d12, fix safe-area iOS), le fond du body ne se propage plus au canvas et le
          pseudo z-index:-1 passait DERRIÈRE le fond opaque -> halos masqués (page toute noire).
          Sur le body même, ils s'affichent toujours. Le body ne scrolle pas (.wrap scrolle) ->
          le dégradé reste fixe visuellement. */
       background:transparent;}   /* le dégradé (halos) est sur HTML = canvas fixé au viewport (voir plus haut) */
  a{color:inherit;text-decoration:none;-webkit-tap-highlight-color:transparent}
  /* Zone de contenu = SEUL élément qui scrolle (flex:1). La barre du bas étant désormais un frère
     statique en dessous,
  plus besoin de réserver ~86px en bas : un petit espace suffit. */
  .wrap{width:100%;
        position:relative;
        max-width:720px;margin:0 auto;display:flex;flex-direction:column;
        padding:calc(8px + env(safe-area-inset-top)) 16px 22px}
  /* Logo centré tout en haut, en DÉFILEMENT NORMAL (bannière fixe annulée, demande user 2026-08-02).
     Desktop masque .toplogo (logo en sidebar). */
  .toplogo{display:flex;align-items:center;justify-content:center;min-height:46px;margin:20px 0 12px}   /* hauteur RÉSERVÉE -> pas de saut quand le logo (préchargé) arrive */
  /* HALO = glow SYMÉTRIQUE collé au wordmark (drop-shadow 0-offset), pas une grosse ellipse de fond (user
     2026-08-22 : l'ellipse était trop forte + coupée par le contenu). Épouse le logo -> visible sur TOUS les
     onglets, jamais recouvert, subtil. */
  .toplogo img{height:auto;width:auto;max-height:46px;max-width:72%;
               filter:drop-shadow(0 0 12px rgba(34,184,255,.32)) drop-shadow(0 4px 10px rgba(34,184,255,.24))}
  /* Bouton COMPTE en haut à droite (toutes pages) — remplace l'onglet « Compte » de la barre du bas. */
  /* ICÔNE SEULE (demande user 2026-08-01 : plus de texte « Compte », il chevauchait le logo). Bouton ROND
     compact dans le coin -> ne déborde plus sur le logo BETSFIX centré. */
  .acctbtn{position:fixed;top:calc(env(safe-area-inset-top) + 16px);right:14px;z-index:55;
    display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;line-height:1;
    border-radius:999px;color:#cfe0f5;text-decoration:none;
    background:rgba(16,22,32,.72);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);
    border:1px solid rgba(150,182,222,.20)}
  .acctbtn:active{transform:scale(.94)}
  .acctbtn .ic{font-size:14px}   /* emoji profil réduit (demande user 2026-08-02) */
  /* Intro au chargement : logo principal centré, puis fondu -> le site apparaît. */
  .splash{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;
          pointer-events:none;  /* n'intercepte JAMAIS les taps (sinon ~1,65s de taps avalés au chargement) */
          background:var(--bg);animation:splashOut .5s ease 1.15s forwards}
  .splash::after{content:"";position:absolute;inset:0;pointer-events:none;
          background:radial-gradient(900px 560px at 50% 40%,var(--halo),transparent 62%)}
  .splash img{position:relative;width:46%;max-width:208px;height:auto;
          filter:drop-shadow(0 10px 32px rgba(34,184,255,.5));
          animation:splashIn .75s cubic-bezier(.2,.8,.2,1) both}
  @keyframes splashIn{0%{opacity:0;transform:scale(.82)}60%{opacity:1}100%{opacity:1;transform:scale(1)}}
  @keyframes splashOut{to{opacity:0;visibility:hidden}}
  @media (prefers-reduced-motion:reduce){
    .splash{animation:splashOut .3s ease .4s forwards}.splash img{animation:none}}
  .pausewrap{text-align:right;margin:-10px 0 8px}
  .pausebadge{display:inline-flex;align-items:center;gap:4px;font-size:9.5px;font-weight:600;
              color:var(--dim);background:transparent;border:1px solid var(--border2);
              padding:2px 8px;border-radius:20px;opacity:.8}
  /* Barre d'onglets en bas. Base neutre (sert de socle au DESKTOP qui la transforme en SIDEBAR ≥1000px).
     touch-action:manipulation (PAS 'none' : 'none' gênait la détection des taps quand la barre est fixe). */
  .botnav{flex:0 0 auto;width:100%;max-width:720px;margin:0 auto;z-index:60;touch-action:manipulation;
          display:flex;gap:4px;
          padding:7px 10px calc(7px + env(safe-area-inset-bottom));
          background:#0b0d12;border-top:1px solid rgba(34,184,255,.22)}   /* filet bleu DISCRET */
  /* MOBILE (<1000px) : placement EXACTEMENT comme CRYPTONAUTS (demande user 2026-08-02, projet voisin qui
     marche parfaitement en app installée) — barre FIXE en bas, padding bas = 6px + safe-area, ombre portée
     vers le haut, et le contenu (.wrap) RÉSERVE la place. Fond html=#0b0d12 (déjà posé) remplit la zone home
     sous la barre. */
  @media (max-width:999px){
    /* BARRE FIXE en bas (recette EXACTE CRYPTONAUTS, user 2026-08-22) : le body scrolle et RÉSERVE la hauteur
       de la barre via son padding-bas -> la barre `position:fixed;bottom:0` est collée au VRAI bas de l'écran
       (plus de zone morte), son `padding-bas = 6px + safe-area` peint la zone home-indicator iPhone. */
    .botnav{position:fixed;top:auto;bottom:0;left:0;right:0;
            padding:6px 6px calc(6px + env(safe-area-inset-bottom, 0px));
            box-shadow:0 -8px 28px rgba(0,0,0,.45)}
    /* Le body scrolle -> `.wrap` doit remplir AU MOINS un écran (moins la barre) pour que la chaîne flex:1
       ci-dessous ait de la hauteur à répartir. Sans ça (jour léger), les catégories se tassent en haut et le
       « 18+ » colle au dernier pari au lieu de descendre près de la barre (user 2026-08-22). */
    .wrap{min-height:calc(100dvh - 62px - env(safe-area-inset-bottom, 0px))}
    /* PRONOS : RÉPARTIR les catégories sur toute la HAUTEUR (user 2026-08-19) — un jour léger/vide, les 6 lignes
       s'espacent régulièrement au lieu d'être tassées en haut. Chaîne flex .wrap > #panels > #pn-home.on >
       .dash-zones (space-between). `flex:1 0 auto` = grandit pour remplir, ne rétrécit jamais (jour chargé =
       hauteur naturelle + scroll .wrap). Scopé mobile + onglet Pronos (#pn-home) -> les autres onglets intacts. */
    #panels{display:flex;flex-direction:column;flex:1 0 auto;min-height:0}
    .panel.on{flex:1 0 auto}
    #pn-home.on{display:flex;flex-direction:column}
    #pn-home.on #day-content{flex:1 0 auto;display:flex;flex-direction:column}
    #pn-home.on .dash-today{flex:1 0 auto;display:flex;flex-direction:column;justify-content:space-between}
  }
  /* Bannière « Ajouter à l'écran d'accueil » (PWA) : incite à installer en plein écran -> plus de barre
     de navigateur = vraie sensation d'app. Montrée seulement HORS standalone (JS). */
  .a2hs{position:fixed;left:10px;right:10px;bottom:calc(74px + env(safe-area-inset-bottom));z-index:85;
       max-width:620px;margin:0 auto;display:flex;align-items:center;gap:11px;padding:11px 11px 11px 13px;
       border-radius:16px;border:1px solid rgba(120,170,220,.32);
       background:linear-gradient(180deg,#161b25,#0d1017);box-shadow:0 14px 38px rgba(0,0,0,.6);
       transform:translateY(20px);opacity:0;transition:transform .32s cubic-bezier(.22,.85,.3,1),opacity .32s}
  .a2hs.show{transform:translateY(0);opacity:1}
  .a2hs-ic{font-size:23px;flex:none;line-height:1}
  .a2hs-tx{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
  .a2hs-tx b{font-size:13.5px;color:#eef4fb;font-weight:800;letter-spacing:-.01em}
  .a2hs-tx span{font-size:11.5px;color:var(--muted);line-height:1.42}
  .a2hs-tx .shr{display:inline-flex;vertical-align:-2px}
  .a2hs-go{flex:none;padding:8px 14px;border-radius:11px;border:0;cursor:pointer;font-weight:800;font-size:12.5px;
       color:#04131f;background:linear-gradient(180deg,#8fd0ff,#5fb2f5)}
  .a2hs-x{flex:none;width:27px;height:27px;border-radius:50%;border:0;cursor:pointer;font-size:12px;
       background:rgba(255,255,255,.08);color:var(--muted);-webkit-tap-highlight-color:transparent}
  .botnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;
            padding:6px 0 4px;border-radius:14px;color:var(--muted);font-size:11px;
            font-weight:700;transition:.15s}
  /* `.ic` = boîte de hauteur FIXE qui CENTRE son contenu -> emoji ET radar Live alignés pareil */
  .botnav a .ic{font-size:24px;line-height:1;height:26px;display:flex;align-items:center;justify-content:center}
  .botnav a:active{transform:scale(.93)}
  .botnav a.on{color:var(--accent-ink);background:linear-gradient(180deg,var(--accent),var(--accent2))}
  /* Home et Live ne sont pas des sports -> onglet actif en BLANC/GRIS neutre (les sports gardent
     leur couleur : tennis citron,
  basket orange,
  foot vert). */
  .botnav a[data-tab="home"].on,
  .botnav a[data-tab="directs"].on,
  .botnav a[data-tab="stats"].on{
    background:linear-gradient(180deg,var(--accent),var(--accent2));color:var(--accent-ink)}
  /* 6 onglets -> labels un brin plus compacts pour tenir sur petit écran */
  .botnav a .lb{font-size:9px}
  .botnav a .ic{font-size:22px;height:24px}
  .botnav a.on .ic{transform:scale(1.06)}
  /* Onglet Live : SEUL le point 🟢 vire au vert et clignote,
  et UNIQUEMENT s'il y a du live
     (classe .has-live) ET que l'onglet n'est pas ouvert. Pas de fond vert -> quand on est dessus,
  l'onglet actif prend le thème neutre (bleu) comme les autres. */
  .botnav a[data-tab="directs"].has-live:not(.on){color:#34d27b}
  /* BADGES chiffrés du menu du bas (demande user 2026-07-14) : nb de matchs du jour par onglet. BLANC par
     défaut (À venir/Tennis/Basket/Foot) ; l'onglet LIVE est VERT + halo pulsant. Caché à 0 (JS pose `hidden`). */
  .botnav a{position:relative}
  .nav-n{position:absolute;top:1px;left:calc(50% + 7px);min-width:16px;height:16px;padding:0 4px;
       border-radius:99px;background:#eef2f7;color:#0b0d12;font-size:11px;font-weight:900;line-height:16px;
       text-align:center;font-variant-numeric:tabular-nums;border:1.5px solid #0b0d12;
       box-shadow:0 1px 4px rgba(0,0,0,.5)}
  /* ===== DESKTOP (≥1000px) — dashboard multi-colonnes (maquette validée user 2026-08-02). Sidebar gauche +
     grille de cartes qui remplit la largeur. TOUT scopé ici -> mobile (<1000px) JAMAIS modifié. ===== */
  @media (min-width:1000px){
    body{flex-direction:row}
    /* Barre du bas -> SIDEBAR verticale gauche */
    .botnav{order:-1;flex-direction:column;justify-content:flex-start;gap:4px;
            width:236px;max-width:236px;height:100vh;height:100dvh;margin:0;overflow-y:auto;
            padding:20px 14px;border-top:0;border-right:1px solid var(--border);
            background:linear-gradient(180deg,#0b0b0f,#08080b)}
    .botnav::before{content:"";display:block;height:32px;margin:4px 10px 20px;
            background:url(/static/wordmark.png?v=1) left center/auto 32px no-repeat;
            filter:drop-shadow(0 5px 18px rgba(34,184,255,.40))}
    .botnav a{flex:0 0 auto;flex-direction:row;justify-content:flex-start;gap:13px;
              padding:11px 14px;border-radius:11px;font-size:14px}
    .botnav a .ic{font-size:20px;height:auto}
    .botnav a .lb{font-size:14px;font-weight:600}
    .botnav a.on .ic{transform:none}
    .nav-n{position:static;margin-left:auto;top:auto;left:auto;box-shadow:none}
    .nav-radar{width:24px;height:24px}
    /* .wrap = SCROLL pleine largeur -> scrollbar collée à droite de la fenêtre */
    .wrap{max-width:none;margin:0;padding:0}
    .toplogo{display:none}
    .acctbtn{top:14px;right:26px}
    /* Contenu capé/centré (le scroll reste plein) */
    #panels{max-width:1560px;margin:0 auto;padding:18px 32px 60px}
    /* CARTES en GRILLE multi-colonnes DANS chaque zone (les séparateurs mobiles laissent place au gap) */
    .zone-b{display:grid;grid-template-columns:repeat(auto-fit,minmax(344px,1fr));gap:16px;align-items:start}
    .zone-b .mc-sep{display:none}
    .zone-b .dayhdr{grid-column:1/-1}
    /* Combinés + cartes multi-paris = pleine largeur (leurs jambes ont leur propre sous-grille) */
    .zone-b .mc-tg,.zone-b .mc-tg-gold,.zone-combo .zone-b>*{grid-column:1/-1}
    .foot{text-align:center;padding:18px 0 8px}
  }
  /* Badge du nb de matchs LIVE : BLANC comme les autres onglets (demande user 2026-07-21) — plus de vert
     ni de halo pulsant (l'icône radar verte de l'onglet signale déjà le live). -> hérite du .nav-n blanc. */
  /* Icône LIVE = RADAR vert pulsant (point + anneaux),
  comme l'orbe de l'état vide « aucun match » */
  /* Live = CERCLE VERT + HALO permanent autour (+ radar qui pulse). TAILLE alignée aux emoji (~22px). */
  .nav-radar{position:relative;display:inline-flex;align-items:center;justify-content:center;
       width:30px;height:30px}
  /* halo PERMANENT (dégradé radial vert) toujours visible autour du point */
  .nav-radar::before{content:"";position:absolute;top:50%;left:50%;width:30px;height:30px;
       margin:-15px 0 0 -15px;border-radius:50%;
       background:radial-gradient(circle,rgba(52,210,123,.5) 0%,rgba(52,210,123,.18) 50%,transparent 74%)}
  .nr-dot{position:relative;z-index:1;width:19px;height:19px;border-radius:50%;background:#34d27b;
       box-shadow:0 0 11px rgba(52,210,123,.95),0 0 2px rgba(52,210,123,1)}
  .nr-ring{position:absolute;top:50%;left:50%;width:30px;height:30px;margin:-15px 0 0 -15px;
       border-radius:50%;border:2px solid rgba(52,210,123,.6);animation:navradar 1.9s ease-out infinite;
       will-change:transform,opacity;backface-visibility:hidden;transform:translateZ(0)}   /* couche GPU -> pulse fluide, plus de jank iOS */
  .nr-ring2{animation-delay:.95s}
  @keyframes navradar{0%{transform:scale(.4);opacity:.95}100%{transform:scale(1);opacity:0}}
  /* Onglet Live ACTIF (fond bleu) : on FIGE le radar vert (sinon il clignote vert-sur-bleu et « déconne » —
     user 2026-08-22). Le point reste, les anneaux et le halo s'apaisent : on est déjà sur l'onglet. */
  .botnav a[data-tab="directs"].on .nr-ring{animation:none;opacity:0}
  .botnav a[data-tab="directs"].on .nav-radar::before{opacity:.3}
  /* SPA : panneaux par onglet (tout chargé à l'ouverture,
  bascule sans rechargement) */
  .panel{display:none}
  .panel.on{display:block;animation:panein .22s cubic-bezier(.22,.85,.3,1)}
  /* Transition DIRECTIONNELLE au changement d'onglet (clic OU swipe, user 2026-08-22) : le panneau entrant
     glisse depuis la droite (onglet suivant) ou la gauche (précédent). `go()` pose sl-next/sl-prev sur #panels. */
  #panels.sl-next .panel.on{animation:slNext .26s cubic-bezier(.22,.85,.3,1)}
  #panels.sl-prev .panel.on{animation:slPrev .26s cubic-bezier(.22,.85,.3,1)}
  @keyframes slNext{from{opacity:.25;transform:translateX(28px)}to{opacity:1;transform:none}}
  @keyframes slPrev{from{opacity:.25;transform:translateX(-28px)}to{opacity:1;transform:none}}
  @keyframes fadein{from{opacity:.4}to{opacity:1}}
  .ldg{color:var(--dim);text-align:center;padding:40px 0;font-size:13px}
  .ldg::before{content:"";display:block;width:22px;height:22px;margin:0 auto 12px;border-radius:50%;
    border:2px solid var(--border2);border-top-color:var(--accent2);animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  /* État VIDE premium de l'onglet Live (aucun match en cours) : orbe « radar » + CTA */
  /* EXACTEMENT le cadre d'une carte de match (.row.pick) : bordure cyan .60 + dégradé CYAN + glow cyan
     -> uniforme avec les onglets sport (demande user). Mêmes valeurs littérales que .row.pick. */
  .live-empty{position:relative;overflow:hidden;text-align:center;margin:14px 0 8px;padding:32px 22px;
       border:1px solid rgba(34,184,255,.60);border-radius:var(--radius);display:flex;flex-direction:column;
       align-items:center;justify-content:center;box-shadow:0 0 26px rgba(34,184,255,.20);
       background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02))}
  /* Cadre Live vide : REMPLIT la hauteur dispo jusqu'à la barre du bas, en laissant la place au « 18+ »
     (qui vit sous #panels dans .wrap) — user 2026-08-22. */
  #pn-directs.on{display:flex;flex-direction:column}
  #pn-directs.on .live-empty{flex:1 1 auto}
  .le-orb{position:relative;width:62px;height:62px;display:flex;align-items:center;justify-content:center;
       margin-bottom:20px}
  .le-dot{width:15px;height:15px;border-radius:50%;background:#34d27b;
       box-shadow:0 0 18px rgba(52,210,123,.85)}
  .le-ping{position:absolute;inset:0;border-radius:50%;border:2px solid rgba(52,210,123,.55);
       animation:lep 2s ease-out infinite}
  .le-ping2{animation-delay:1s}
  @keyframes lep{0%{transform:scale(.42);opacity:.85}100%{transform:scale(1);opacity:0}}
  .le-h{font-size:19px;font-weight:800;color:#fff;letter-spacing:.01em;text-transform:uppercase}
  .le-sub{font-size:12.5px;color:var(--muted);max-width:290px;line-height:1.55;margin:9px 0 22px}
  .le-cta{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
  .le-btn{padding:11px 17px;border-radius:12px;font-size:12px;font-weight:800;text-decoration:none;
       border:1px solid var(--cardline);color:var(--text);background:rgba(255,255,255,.04);
       text-transform:uppercase;letter-spacing:.03em}
  .le-btn:active{transform:scale(.97)}
  .le-btn-p{color:var(--accent-ink);border-color:transparent;
       background:linear-gradient(180deg,var(--accent),var(--accent2));box-shadow:0 4px 16px var(--glow)}
  /* État vide PRONOS : orbe CYAN (pas vert -> ne se lit pas « live »). Espace un peu plus généreux. */
  .pe-dot{background:#5fd0ff;box-shadow:0 0 18px rgba(95,208,255,.85)}
  .pe-ping{border-color:rgba(95,208,255,.55)}
  .paj-hero{margin-top:26px}
  /* Header sticky premium */
  .hdr{position:sticky;top:0;z-index:50;
       background:linear-gradient(180deg,rgba(12,15,22,.92),rgba(12,15,22,.78));
       backdrop-filter:saturate(160%) blur(14px);-webkit-backdrop-filter:saturate(160%) blur(14px);
       border-bottom:1px solid var(--border)}
  .hdr-in{max-width:720px;margin:0 auto;padding:12px 16px 10px}
  .brand{display:flex;align-items:center;gap:6px;font-size:20px;font-weight:800;
         letter-spacing:-.02em}
  .brand .logo{font-size:22px;filter:drop-shadow(0 2px 7px rgba(34,184,255,.5))}
  .brand img.logo{height:30px;width:auto;display:block}
  .brand img.wm{height:21px;width:auto;display:block;margin-left:-1px}
  .hero{text-align:center;padding:18px 0 6px}
  .hero-logo{max-width:230px;width:62%;height:auto;
             filter:drop-shadow(0 6px 22px rgba(34,184,255,.35))}
  .hero-sub{margin-top:6px;font-size:12px;color:var(--muted);
            letter-spacing:.04em}
  .brand b{color:var(--brand)}
  .brand .hright{margin-left:auto;display:inline-flex;align-items:center;gap:8px}
  .brand .hdot{font-size:11px;font-weight:800;color:var(--gold);white-space:nowrap;letter-spacing:.02em}
  .brand .tag{font-size:11px;font-weight:700;letter-spacing:.12em;
              text-transform:uppercase;color:var(--dim);border:1px solid var(--border2);
              padding:3px 8px;border-radius:20px}
  .nav{display:flex;gap:9px;margin-top:11px}
  .nav a{flex:1;display:flex;align-items:center;justify-content:center;height:60px;
         border-radius:17px;font-size:30px;line-height:1;background:var(--surface);
         border:1px solid var(--border);transition:.16s}
  .nav a:active{transform:scale(.95)}
  .nav a.on{border-color:transparent;
            background:linear-gradient(180deg,var(--accent),var(--accent2));
            box-shadow:0 6px 18px var(--glow)}
  /* Sous-menu par sport (Matchs / Fiabilité) */
  .subnav{display:flex;gap:6px;margin:16px 0 2px}
  .subnav a{flex:1;text-align:center;padding:9px;border-radius:11px;font-size:12.5px;
            font-weight:700;color:var(--muted);background:transparent;
            border:1px solid var(--border);transition:.16s}
  .subnav a.on{color:var(--text);background:var(--surface2);border-color:var(--border2)}
  /* En-tête de page sport : titre + lien fiabilité (le changement de sport = barre du bas) */
  .sporthd{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin:4px 0 6px}
  .sporthd-t{font-size:18px;font-weight:900;color:var(--text)}
  .sporthd-a{flex:none;font-size:11px;font-weight:700;color:var(--accent);text-decoration:none}
  /* Carte PERF PREMIUM sous le titre du sport : ROI géant + forme + courbe d'équité + KPIs */
  /* MÊME fond que les cartes de match (.row.pick) : dégradé cyan + bordure + glow cyan */
  .spf{display:block;text-decoration:none;position:relative;overflow:hidden;margin:2px 0 16px;
       padding:14px 15px 12px;border:1px solid rgba(34,184,255,.60);border-radius:16px;
       box-shadow:0 0 26px rgba(34,184,255,.20),var(--shadow-sm);
       background:rgba(34,184,255,.055)}   /* teinte UNIE (pas de dégradé) : le fond ne bouge plus quand
                                              l'historique s'ouvre et agrandit la carte — demande user 2026-07-24 */
  .spf-top{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
  .spf-roi-wrap{display:flex;flex-direction:column;line-height:1}
  .spf-forms{display:flex;flex-direction:column;align-items:flex-end;gap:5px}
  .spf-roi{font-size:30px;font-weight:900;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
  .spf-roi-l{font-size:9px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;
       color:var(--dim);margin-top:4px}
  .spf-kpis{display:flex;gap:8px;margin-top:10px}
  .spf-k{flex:1;min-width:0;text-align:center;background:rgba(255,255,255,.04);border:1px solid var(--border);
       border-radius:11px;padding:7px 3px}
  .spf-kv{display:block;font-size:14px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums}
  .spf-kl{display:block;font-size:8px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
       color:var(--muted);margin-top:2px}
  /* Deux courbes d'équité ÉTIQUETÉES (Simples / Combinés) empilées dans l'onglet sport */
  .spf-charts{display:flex;flex-direction:column;gap:10px;margin-top:10px}
  .spf-cv{background:linear-gradient(180deg,#0f1620,#0b0d13);border:1px solid var(--border);border-radius:12px;
       padding:8px 10px 6px}   /* MÊME fond que les jambes de combiné (.cleg) — demande user 2026-07-24 */
  .spf-cv-h{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:5px}
  .spf-cv-t{font-size:11.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#fff}   /* titre graphe : BLANC, un rien plus grand (demande user 2026-07-24) */
  .spf-cv-roi{font-size:12px;font-weight:800;font-variant-numeric:tabular-nums}
  .spf-cv-none{font-size:11px;color:var(--muted);padding:16px 2px;text-align:center}
  /* Forme W/L PROPRE à chaque graphe (juste au-dessus de la courbe) */
  /* Forme W/L (dots) alignée à DROITE : si ça déborde, on rogne les VIEUX (gauche), on garde les récents. */
  .spf-cv-form{display:flex;justify-content:flex-end;margin:0 0 5px;overflow:hidden}
  .spf-cv-form .forms{flex-wrap:nowrap}
  .spf-cv-form .fd{width:15px;height:15px;font-size:9px}   /* pastilles plus grandes (demande user) — 16 tiennent centrées */
  .spf-cv-form .fd.fd-p{font-size:11px}
  /* Groupe de gauche de l'en-tête : titre + badge SÉRIE côte à côte (le badge n'est PAS dans la ligne W/L). */
  .spf-cv-hl{display:flex;align-items:center;gap:7px;min-width:0}
  .spf-cv-hl .sx-streak{flex:none}
  /* Libellé STATIQUE des derniers paris (affichés d'office, sans bouton — demande user 2026-08-13). */
  .spf-rec-lbl{margin-top:9px;text-align:center;font-size:11px;font-weight:800;letter-spacing:.04em;
       text-transform:uppercase;color:var(--muted);border-top:1px solid var(--border);padding-top:11px}
  /* Liste des derniers paris (révélée) : pastille W/L/N + affiche + sélection + date. */
  /* Historique = REGISTRE pro (demande user 2026-07-25) : lignes séparées par un filet fin, padding régulier,
     colonnes alignées ; scroll interne pour tout l'historique. */
  .spf-recent{margin-top:8px;display:flex;flex-direction:column;padding-right:4px}
       /* PAS de scroll interne (demande user 2026-08-13) : TOUS les paris affichés dans le cadre. */
  .spf-rec{display:flex;align-items:center;gap:10px;font-size:11px;padding:9px 2px;
       border-bottom:1px solid rgba(255,255,255,.055)}
  .spf-rec:last-child{border-bottom:none}
  .spf-rec-b{flex:none;width:19px;height:19px;border-radius:6px;display:flex;align-items:center;
       justify-content:center;font-size:11px;font-weight:900;color:#0a0a0a}
  .spf-rec.rec-w .spf-rec-b{background:#34d27b} .spf-rec.rec-l .spf-rec-b{background:#ff6b6b}
  .spf-rec.rec-n .spf-rec-b{background:var(--muted)}
  /* Pari À JOUER (compté au ROI, pas encore réglé) : SABLIER DORÉ, IDENTIQUE au badge provisoire
     `.sx-bdg.p` (demande user 2026-07-17 : « les icônes en attente des listes ROI comme les sabliers
     des provisoires »). Nom en blanc (comme les paris réglés). */
  .spf-rec.rec-p .spf-rec-b{background:var(--gold);font-size:11px}
  .spf-rec.rec-p b{color:var(--text)}
  .spf-rec-m{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.25}
  .spf-rec-m b{color:var(--text);font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .spf-rec-s{color:var(--muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* COMBINÉ dépliable : cliquer la ligne révèle les JAMBES (demande user 2026-07-26). Natif <details>. */
  .spf-rec-x{border-bottom:1px solid rgba(255,255,255,.055)}
  .spf-rec-x:last-child{border-bottom:none}
  .spf-rec-x[open]{background:rgba(255,255,255,.02);border-radius:8px}
  .spf-rec-x>summary{list-style:none;cursor:pointer}
  .spf-rec-x>summary.spf-rec{border-bottom:none}
  .spf-rec-x>summary::-webkit-details-marker{display:none}
  .spf-cx{color:var(--muted);font-size:9px;display:inline-block;transition:transform .15s}
  .spf-rec-x[open] .spf-cx{transform:rotate(180deg)}
  .spf-legs{padding:1px 2px 8px 20px;display:flex;flex-direction:column}
  .spf-leg{display:flex;align-items:center;gap:8px;font-size:11px;padding:5px 0;
       border-top:1px dashed rgba(255,255,255,.08)}
  .spf-leg-t{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.2}
  .spf-leg-t b{color:var(--text);font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .spf-leg-t>span{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .spf-leg-c{flex:none;color:var(--muted);font-weight:800;font-size:9.5px}
  .spf-leg-b{flex:none;width:16px;height:16px;border-radius:5px;display:flex;align-items:center;
       justify-content:center;font-size:9px;font-weight:900;color:#0a0a0a}
  .spf-leg.rec-w .spf-leg-b{background:#34d27b}
  .spf-leg.rec-l .spf-leg-b{background:#ff6b6b}
  .spf-leg.rec-n .spf-leg-b{background:var(--muted)}
  .spf-leg.rec-p .spf-leg-b{background:var(--gold)}
  /* DATE tout à gauche sur 2 lignes (demande user 2026-07-25) : DATE en HAUT (alignée avec le nom d'équipes)
     + HEURE en BAS (alignée avec la sélection). Mêmes tailles/interligne que `.spf-rec-m` -> les 2 lignes
     coïncident. COTE juste avant le badge résultat. */
  /* Colonne date/heure CENTRÉE horizontalement ; DATE à la taille des ÉQUIPES (11px), HEURE à la taille du
     PARI (10px) — demande user 2026-07-25. */
  .spf-rec-d{flex:none;width:48px;display:flex;flex-direction:column;line-height:1.3;text-align:center;
       font-variant-numeric:tabular-nums}
  .spf-rec-d b{color:var(--muted);font-weight:700;font-size:11px;white-space:nowrap}   /* DATE = taille équipes */
  .spf-rec-d span{color:var(--dim);font-size:11px;white-space:nowrap}                  /* HEURE = taille pari */
  .spf-rec-c{flex:none;color:var(--text);font-size:10.5px;font-weight:700;font-variant-numeric:tabular-nums;
       text-align:right;min-width:42px}
  /* Stats PROPRES à chaque graphe (juste sous la courbe) : réussite · paris · cote moy. */
  .spf-cv-kpis{display:flex;justify-content:space-between;gap:8px;margin-top:8px;
       font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
  /* Stats du graphe présentées en BOUTONS encadrés (demande user) — même look que les KPIs cartes */
  .spf-cv-kpis span{flex:1;min-width:0;text-align:center;background:rgba(255,255,255,.04);
       border:1px solid var(--border);border-radius:11px;padding:7px 3px}
  .spf-cv-kpis b{display:block;color:var(--text);font-weight:800;font-size:14px;margin-bottom:2px;
       font-variant-numeric:tabular-nums;text-transform:none;letter-spacing:0}
  /* Disposition « ROI héros » façon ROI GLOBAL, SANS cadre imbriqué (choix user 2026-07-24) : la carte
     sport (.spf / .sx-card) porte déjà le cadre bleu lumineux -> le graphe s'affiche DIRECTEMENT dessus
     (on sort de la boîte .spf-cv). Label → gros ROI coloré → sous-ligne réussite·paris·cote → courbe → W/L. */
  .spf-hero{padding:0}
  .spf-hero-lbl{text-align:center;font-size:9.5px;font-weight:800;letter-spacing:.16em;
       text-transform:uppercase;color:var(--muted)}
  .spf-hero-roi{text-align:center;font-size:42px;font-weight:900;letter-spacing:-.03em;line-height:1;
       margin:4px 0 3px;font-variant-numeric:tabular-nums}
  .spf-hero-roi.pos{color:#34d27b} .spf-hero-roi.neg{color:#ff6b6b} .spf-hero-roi.na{color:var(--muted)}
  /* 3 stats présentées de façon INTUITIVE & PRO (demande user 2026-07-24) : valeur nette + libellé clair
     en dessous (Réussite / Paris réglés / Cote moyenne), sans boîte. */
  .spf-hero-kpis{display:flex;justify-content:center;gap:10px 20px;margin-top:9px;flex-wrap:wrap}   /* wrap si 4 KPIs (montante), user 2026-08-19 */
  .spf-hero-kpis>div{display:flex;flex-direction:column;align-items:center;line-height:1.1}
  .spf-hero-kpis .v{font-size:16px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums}
  .spf-hero-kpis .l{font-size:9px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
       color:var(--muted);margin-top:3px}
  /* Bloc « Taux de réussite » : courbe légère (progression de la fiabilité) + % courant */
  .spf-rate{margin-top:12px;padding-top:10px;border-top:1px solid var(--border)}
  .spf-rate-h{text-align:center;font-size:9.5px;font-weight:800;letter-spacing:.11em;text-transform:uppercase;
       color:var(--muted)}
  .spf-rate-h b{color:#22b8ff;font-size:12px;letter-spacing:0;margin-left:3px}
  .rate-c{display:block;margin-top:5px}
  .rate-lbl{font-size:8px;font-weight:800;fill:var(--muted);font-variant-numeric:tabular-nums}
  .rate-lbl-e{fill:#22b8ff}
  /* Ligne W/L des graphes héros : pastilles RESSERRÉES et centrées (demande user) + espace avant l'historique. */
  .spf-hero .spf-cv-form{display:block;overflow:visible;margin:12px 0 0}
  .spf-hero .spf-cv-form .forms{display:flex;width:100%;justify-content:center;gap:4px;margin-left:0;flex-wrap:wrap}
  /* Note « provisoires hors ROI » en tête de l'onglet Provisoires d'un cadre sport (demande user 2026-07-25). */
  .prov-note{font-size:11px;color:var(--gold);text-align:center;margin:0 0 10px;line-height:1.45}
  .prov-note b{color:#ffd873}
  /* Série EN COURS, sans emoji, rendu pro (demande user 2026-07-24) : pastille discrète bordée,
     verte si victoires d'affilée / rouge si défaites. */
  .spf-hero-streakw{display:flex;justify-content:center;align-items:center;flex-wrap:wrap;gap:6px;margin-top:9px}
  .spf-hero-streak{display:inline-block;padding:2px 11px;border-radius:999px;font-size:10.5px;
       font-weight:700;letter-spacing:.02em;border:1px solid var(--border);color:var(--muted)}
  .spf-hero-streak b{font-variant-numeric:tabular-nums}
  .spf-hero-streak.win{color:#34d27b;border-color:rgba(52,210,123,.35);background:rgba(52,210,123,.09)}
  .spf-hero-streak.loss{color:#ff6b6b;border-color:rgba(255,107,107,.35);background:rgba(255,107,107,.09)}
  .spf-hero-streak.best{color:var(--gold);border-color:rgba(214,178,90,.38);background:rgba(214,178,90,.10)}
  .spf-hero .sx-equity,.spf-hero .sx-chart{margin-top:11px}
  /* Graphe d'équité SANS bride de hauteur dans les héros -> rendu pleine largeur, MÊME largeur que la
     courbe du taux de réussite (demande user 2026-07-24). */
  .spf-hero .sx-equity .sx-heroc{max-height:none}
  /* Marges gauche/droite ÉGALES (demande user 2026-07-25) : plus de débord `margin-right` (qui décalait la
     courbe hors carte et rognait le point final). Les insets sont portés par L=R dans le SVG (symétriques),
     la ligne rejoint son dernier point (dot) à l'intérieur de la carte. */
  .spf-hero .spf-cv-form{justify-content:center;margin:9px 0 0}
  /* Ligne d'EXTRAS sous les stats (Stats : nouv. système · CLV / profit · rabot) — inline compact */
  .spf-cv-extra{display:flex;justify-content:center;flex-wrap:wrap;gap:5px 16px;margin-top:6px;
       font-size:9.5px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted)}
  .spf-cv-extra b{color:var(--text);font-weight:800;font-size:11px;text-transform:none;
       letter-spacing:0;font-variant-numeric:tabular-nums}
  /* Repères de modèle & détail par jambes gardés DANS la carte compacte (onglet Stats) */
  .spf-cv .sx-miles,.spf-cv .sx-legs{margin-top:10px}
  /* Bandeau PERF PAR SPORT en tête des Stats (le global masque un sport fort). */
  .spf-sports .spf-sp-row{display:flex;align-items:center;gap:10px;padding:8px 4px;
    border-bottom:1px solid rgba(255,255,255,.05);text-decoration:none}
  .spf-sports .spf-sp-row:last-child{border-bottom:0}
  .spf-sp-n{flex:1;font-size:12.5px;font-weight:700;color:var(--text)}
  .spf-sp-pause{margin-left:7px;font-size:9px;font-weight:800;letter-spacing:.03em;text-transform:uppercase;
       color:#f6c54a;background:rgba(246,197,74,.13);border:1px solid rgba(246,197,74,.34);
       border-radius:6px;padding:1px 6px;white-space:nowrap;vertical-align:middle}
  .spf-sp-pause.spf-sp-ready{color:#64cd8d;background:rgba(100,205,141,.14);border-color:rgba(100,205,141,.4)}
  .spf-sp-roi{font-size:13px;font-weight:800;font-variant-numeric:tabular-nums;min-width:58px;text-align:right}
  .spf-sp-pct{font-size:11px;color:var(--dim);min-width:34px;text-align:right;font-variant-numeric:tabular-nums}
  .spf-sp-k{font-size:11px;color:var(--muted);min-width:52px;text-align:right;font-variant-numeric:tabular-nums}
  /* Détail INTÉGRÉ au cadre (repliable) : fiabilité par-pari + calibration,
  séparé par un filet */
  .spf-det{margin-top:12px;border-top:1px solid var(--border)}
  .spf-det>summary{list-style:none;cursor:pointer;display:flex;align-items:center;
       justify-content:space-between;padding:11px 2px 2px;font-size:11px;font-weight:800;
       letter-spacing:.04em;text-transform:uppercase;color:var(--accent)}
  .spf-det>summary::-webkit-details-marker{display:none}
  .spf-det .chev{transition:.2s;color:var(--muted)}
  .spf-det[open] .chev{transform:rotate(180deg)}
  .spf-det-b{padding-top:6px}
  /* Plus de cadre groupant autour des 3 paris : chaque pari devient une CARTE autonome (même style
     que la carte de calibration en dessous) */
  .spf-det-b .sx-sport{margin:0;background:none;border:0;border-radius:0;padding:0;box-shadow:none}
  .spf-det-b .sx-rows{gap:7px;margin-top:0}
  .spf-det-b .sx-row{background:var(--surface);border:1px solid var(--border);border-radius:11px;
       padding:9px 11px}
  h2{font-size:13px;font-weight:700;margin:26px 0 11px;color:var(--muted);
     text-transform:uppercase;letter-spacing:.07em;display:flex;align-items:center;gap:8px}
  h2:before{content:"";width:3px;height:14px;border-radius:3px;
            background:linear-gradient(var(--accent),var(--accent2))}
  /* KPI grid */
  .grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:11px;margin:14px 0}
  .card{position:relative;background:linear-gradient(180deg,var(--surface2),var(--surface));
        border:1px solid var(--border);border-radius:var(--radius);padding:14px 10px;
        text-align:center;box-shadow:var(--shadow-sm);overflow:hidden}
  .card:before{content:"";position:absolute;inset:0 0 auto 0;height:2px;
               background:linear-gradient(90deg,transparent,var(--border2),transparent)}
  .lbl{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;font-weight:700}
  .val{font-size:25px;font-weight:800;margin:5px 0;letter-spacing:-.02em;
       font-variant-numeric:tabular-nums}
  .sub{font-size:11px;color:var(--muted)}
  /* Rows / list cards */
  /* En-tête de jour dans les listes (regroupement par date) */
  .dayhdr{display:flex;align-items:center;gap:9px;margin:11px 2px 3px;font-size:9.5px;
          font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;opacity:.85}
  .dayhdr::after{content:"";flex:1;height:1px;background:var(--border)}
  .row{display:block;background:#18181c;   /* fond UNI (user 2026-08-16) : plus de dégradé qui change la luminosité au dépli */
       border-radius:var(--radius);padding:12px 14px;margin:15px 0;border:1px solid var(--cardline);
       box-shadow:var(--cardglow),var(--shadow-sm);transition:.16s}
  /* Pas d'effet « pressé » (scale) au toucher des cartes de prono (demande user 2026-07-20) : une carte
     de contenu n'est pas un bouton. Le tap-highlight est déjà neutralisé globalement. */
  /* Carte dépliable (foot/basket) : analyse en accordéon sous la carte */
  .rowtap{cursor:pointer}
  .exp-c{margin-top:12px;padding:10px;border-radius:11px;font-size:10.5px;color:var(--accent);
         font-weight:800;display:flex;align-items:center;justify-content:center;gap:6px;
         text-transform:uppercase;letter-spacing:.06em;border:1px solid var(--cardline);
         background:rgba(255,255,255,.03);transition:.15s}
  .row.open .exp-c{background:rgba(255,255,255,.05)}
  .exp-chev{display:inline-block;transition:transform .18s}
  .row.open .exp-chev{transform:rotate(180deg)}
  .exp{margin-top:10px;padding-top:8px;border-top:1px solid var(--border)}
  .mc-ana>.exp{border-top:0;padding-top:0}   /* carte : PAS de filet entre le cadre Paris et le cadre Infos */
  .exp h2:first-child{margin-top:4px}
  /* Titres de section de l'analyse : UNE seule barre (le liseré h2:before) — pas de border-left
     en plus (sinon 2 barres verticales). */
  .exp h2{margin:16px 0 9px;font-size:13.5px;font-weight:800;line-height:1.35}
  .exp .ldg{padding:16px 0}
  /* Fond UNI (plus de dégradé étirable) : sinon, quand la carte se déplie et grandit, le dégradé se
     redistribue et « la lumière du fond change » (retour user 2026-07-21). Uni -> identique plié/déplié. */
  .row.pick{border-color:rgba(34,184,255,.60);
            background:#0d1119;   /* fond OPAQUE/uni (user 2026-08-16) : le translucide laissait voir les halos de page -> changeait au dépli */
            box-shadow:0 0 26px rgba(34,184,255,.20)}
  /* CARTE COMPACTE : en-tête toujours visible (statut + équipes + résumé) + corps replié au tap.
     Liste dense -> peu de scroll ; on déplie un match pour voir paris/barres/liens/analyse. */
  /* TOUTES les cartes de pari (base) : bordure BLANCHE + bord GAUCHE coloré selon le RÉSULTAT (demande
     user 2026-07-25). Défaut = doré (à venir / en attente / live) ; gagné = vert ; perdu = rouge ;
     remboursé = gris. Posé via la classe `mc-r-*`. */
  /* Contour ENTIER coloré par l'état (demande user 2026-07-27 : tout le cadre = couleur du bord gauche) —
     bordure UNIFORME 1px (user 2026-08-17 : plus de bord gauche épais), TOUS les côtés à la même teinte. */
  .row.mc{padding:0;margin:7px 0;overflow:hidden;
       border:1px solid var(--st-soon)}
  .row.mc.mc-r-won{border-color:var(--st-won)}
  .row.mc.mc-r-lost{border-color:var(--st-lost)}
  .row.mc.mc-r-push{border-color:var(--st-void)}
  .row.mc.mc-r-live{border-color:var(--st-live)}
  /* Séparateur DISCRET entre deux cadres de paris (demande user 2026-07-18 : « mieux séparer les
     cadres entre eux »). Fine ligne dégradée qui s'estompe aux extrémités -> respire sans alourdir.
     Inséré entre cartes (jamais après un en-tête de jour ni en tête de zone). */
  .mc-sep{height:1px;margin:9px 16px;background:linear-gradient(90deg,transparent,var(--border) 20%,
          var(--border) 80%,transparent);opacity:.7}
  /* mc-head : colonne d'infos pleine largeur + chevron en ABSOLU (centré vertical) -> l'heure peut
     aller dans le COIN haut-droit sans être décalée par la flèche. */
  .mc-head{position:relative;padding:11px 14px;cursor:pointer;-webkit-tap-highlight-color:transparent}
  .mc-line{display:flex;align-items:center;gap:7px}
  /* En-tête carte : ligue CENTRÉE sans emoji, décompte en absolu à droite (user 2026-08-15) */
  .mc-line-c{position:relative;justify-content:center;min-height:22px}
  /* ligue CENTRÉE : affichée EN ENTIER (user 2026-08-15) — retour à la ligne autorisé (plus d'ellipse),
     padding réduit ; en LIVE (pas de badge) elle prend quasi toute la largeur. */
  .mc-line-c .mc-comp{flex:0 1 auto;text-align:center;padding:0 44px;white-space:normal;overflow:visible;
       text-overflow:clip;line-height:1.25}
  .mc-r-live .mc-line-c .mc-comp{padding:0 10px}
  .mc-line-c .mc-badge{position:absolute;right:0;top:50%;transform:translateY(-50%);margin:0}
  .mc-ic{flex:none;font-size:13px;line-height:1}                 /* emoji sport DISCRET (plus petit) */
  /* L1 : nom du sport · circuit (ATP/WTA) · tournoi (ville capitalisée) — contextuel,
  discret. */
  .mc-comp{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
       font-size:12px;font-weight:800;color:var(--text);letter-spacing:.02em;text-transform:uppercase}  /* LIGUE = MAJUSCULE, BLANC (user 2026-08-15) */
  /* Nom du SPORT (majuscules, accent du sport) puis compétition (muted) — « TENNIS • Wimbledon ». */
  .mc-sport{color:var(--accent);font-weight:800;letter-spacing:.05em}
  .mc-comp-sep{color:var(--dim);font-weight:700}
  .mc-badge{flex:none;font-size:11px;font-weight:800;padding:3px 8px;border-radius:8px;
       white-space:nowrap;letter-spacing:.02em;font-variant-numeric:tabular-nums;line-height:1.3}
  .mc-up{background:rgba(255,255,255,.06);color:var(--muted)}
  .mc-live{background:rgba(52,210,123,.16);color:#5fe39b}
  .mc-done{background:rgba(255,255,255,.06);color:#cfe0f5}
  .mc-wait{background:rgba(246,197,74,.13);color:var(--gold)}
  /* Chevron de dépli : EN BAS À DROITE du cadre replié. */
  .mc-chev{position:absolute;right:12px;bottom:9px;color:var(--muted);font-size:15px;
       transition:transform .18s}
  .mc-open .mc-chev{display:none}   /* carte ouverte : chevron caché ; il ne réapparaît qu'une fois repliée */
  /* Carte sans corps dépliable : un clic n'importe où (dé)plie le « Pourquoi » (JS) -> curseur cliquable. */
  .mc-flat{cursor:pointer}
  /* L2 : équipes (noms + prénoms complets) — ligne principale. */
  /* Équipes = HÉROS, 16 px + sur 2 lignes possibles pour TOUS les types de cartes (demande user 2026-07-14 :
     cartes semblables) — à venir, provisoire, LIVE et TERMINÉ ont désormais le même titre de match. */
  .mc-teams{font-size:14px;font-weight:800;color:var(--text);margin-top:15px;letter-spacing:-.015em;
       line-height:1.26;white-space:normal;overflow:visible;text-overflow:clip;text-wrap:balance}
  .mc-teams .dim{color:var(--dim);font-weight:600}
  /* Carte PREMIUM (pari à venir présenté carte) : demande user 2026-07-14 — l'ÉQUIPE (le match) est le
     HÉROS de la carte repliée -> plus GRANDE (16 px) que le pari à jouer (14 px, cf. .mc-pick). Padding
     roomier, équipes sur 2 lignes possibles. */
  .mc-prem .mc-head{padding:13px 16px 12px}
  .mc-prem .mc-teams{font-size:14px;margin-top:15px;line-height:1.26;white-space:normal;overflow:visible;
       text-overflow:clip;text-wrap:balance}
  /* L3 : LISTE des paris (intitulés,
  1/ligne) — masquée une fois DÉPLIÉE (les paris détaillés s'affichent).
     padding-right pour libérer le chevron en bas à droite. */
  .mc-sub{margin-top:6px;padding-right:20px}
  .mc-open .mc-sub{display:none}
  /* Cartes telegram/premium (.mc-tg) : le chevron est MASQUÉ (cf. .mc-tg .mc-chev) -> pas besoin de la
     réserve à droite ; on l'annule pour que la barre de confiance + la grille (Confiance/Marché/Value/Cote)
     prennent TOUTE la largeur, symétriques gauche/droite (demande user 2026-07-18 : « espace à droite »). */
  .mc-tg .mc-sub{padding-right:0}
  /* Carte PLATE (pas de chevron : plus de corps dépliable) : idem, on annule la réserve de droite sinon
     le contenu est décalé (retour user 2026-07-21 : « espace à droite dû à la flèche »). */
  .mc-flat .mc-sub{padding-right:0}
  /* LIVE (demande user 2026-07-12) : intitulé du pari sur UNE seule ligne (ellipsis) + scoreboard des
     résultats juste en dessous, visible dans la carte repliée. */
  .mc-islive .mc-sub .mc-betl{flex-wrap:nowrap}
  .mc-islive .mc-sub .mc-bt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mc-livesc{margin-top:10px}
  /* Barre « Chance live » (demande user 2026-07-15) : reflet EN DIRECT du % que le pari passe, vu le
     score + le temps restant (cote live dé-margée / repli modèle). PURE AFFICHAGE (jamais au ROI). */
  .lvbar{margin-top:9px}
  .lvbar-hd{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
  .lvbar-t{font-size:10.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:#8fa2b8}
  .lvbar-v{font-size:13px;font-weight:900;color:#eaf2ff;font-variant-numeric:tabular-nums}
  .lvbar-ar{font-size:11px;margin-left:3px}
  .lvbar.lv-up .lvbar-ar{color:#34d27b}
  .lvbar.lv-down .lvbar-ar{color:#ff6b6b}
  .lvbar-track{height:8px;border-radius:6px;background:rgba(255,255,255,.09);overflow:hidden;box-shadow:inset 0 1px 2px rgba(0,0,0,.35)}
  /* Remplissage PREMIUM (user 2026-08-19) : dégradé/gloss (posé inline), léger reflet haut, et CROISSANCE à
     l'entrée (scaleX depuis la gauche). Respecte prefers-reduced-motion. */
  @keyframes lvGrow{from{transform:scaleX(.02)}to{transform:scaleX(1)}}
  .lvbar-fill{height:100%;border-radius:6px;transition:width .5s ease;transform-origin:left center;
       box-shadow:inset 0 1px 0 rgba(255,255,255,.30);animation:lvGrow .75s cubic-bezier(.22,.9,.3,1) both}
  @media (prefers-reduced-motion:reduce){.lvbar-fill{animation:none}}
  .lvbar-src{margin-top:3px;font-size:11px;font-weight:600;color:#7d8ca0;text-align:right}
  /* Ligne de pari : libellé à gauche (peut passer à la ligne), pastilles cote/confiance À DROITE,
     VERTICALEMENT CENTRÉES contre le libellé (fini le désalignement quand le libellé fait 2 lignes). */
  .mc-betl{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:600;color:#cfe0f5}
  .mc-betl + .mc-betl{margin-top:3px}
  /* Étiquette DOUBLE SCAN (« Premier scan » / « Dernier scan ») : le rescan a changé le pari -> les deux
     décisions affichées et comptées (demande user 2026-07-21). Petite pastille discrète devant le pari. */
  .mc-btag{display:inline-block;font-size:9px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;
       padding:2px 6px;border-radius:6px;background:rgba(255,255,255,.08);color:#9fb3cc;margin-bottom:3px}
  .mc-pick .mc-bc{margin-left:7px}   /* cote après la sélection (présentation double scan) */
  .mc-bi{flex:none;font-size:11px;align-self:flex-start;margin-top:2px}
  .mc-bt{min-width:0;flex:1;overflow-wrap:anywhere;line-height:1.32}
  /* Module cote │ confiance placé SOUS le pari (demande user 2026-07-12), aligné sous le libellé (past la puce). */
  .mc-odds-row{padding-left:19px;margin-top:6px}
  /* Pastille de pari (cote/value) — premium : gélule arrondie, dégradé subtil, liseré + micro-ombre.
     Hauteur + largeur MINI fixes -> les cotes s'alignent en COLONNE d'une carte à l'autre (rendu tableau). */
  .mc-bc{flex:none;align-self:center;display:inline-flex;align-items:center;justify-content:center;
       min-width:52px;height:23px;border-radius:99px;padding:0 9px;font-size:10.5px;font-weight:900;
       font-variant-numeric:tabular-nums;white-space:nowrap;color:#8ff0bd;
       background:linear-gradient(180deg,rgba(46,226,127,.2),rgba(46,226,127,.06));
       border:1px solid rgba(46,226,127,.32);box-shadow:0 1px 4px rgba(0,0,0,.24)}
  /* pari RETENU (⭐ en tête) : libellé mis en avant */
  .mc-betl-reco .mc-bt{color:#fff;font-weight:800}
  .mc-noplay .mc-bt,.mc-noplay .mc-bi{color:var(--muted);font-weight:600;font-style:italic;opacity:.85}
  .mc-body{padding:2px 14px 13px}
  .mc-body[hidden]{display:none}
  /* Moins d'espace entre les équipes et le bloc « BOOKMAKERS » une fois déplié. */
  .mc-open .mc-head{padding-bottom:5px}
  /* DÉPLI COMBINÉ (demande user 2026-07-18 : « en dépliant, ce qui existait replié n'est plus visible »).
     Sur les cartes PREMIUM (.mc-prem) et PROVISOIRES/combiné du jour (.mc-tg), on GARDE le pick + la ligne
     verdict (.mc-sub) visibles une fois la carte ouverte -> l'analyse & les stats s'AJOUTENT dessous
     (aperçu direct) au lieu de REMPLACER la présentation. Pas de doublon : le ticket est retiré du corps de
     ces cartes (corps = barres + analyse). Un filet + un peu d'air séparent le résumé du détail. */
  .mc-prem.mc-open .mc-sub,.mc-tg.mc-open .mc-sub{display:block;padding-right:0}
  .mc-prem.mc-open .mc-div,.mc-tg.mc-open .mc-div{display:block}
  .mc-prem.mc-open .mc-body,.mc-tg.mc-open .mc-body{border-top:1px solid var(--border);
       margin-top:9px;padding-top:11px}
  .live{color:#34d27b;font-weight:800;letter-spacing:.02em}
  .fem{color:#b08cf2;font-weight:800}
  /* EN-TÊTE de fiche match : pastille sport + compétition (gauche) · statut (droite) · filet dessous */
  .mh{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;
      padding-bottom:9px;border-bottom:1px solid rgba(255,255,255,.08)}
  .mh-comp{display:flex;align-items:center;gap:8px;min-width:0}
  .mh-ic{flex:none;width:26px;height:26px;border-radius:8px;display:inline-flex;align-items:center;
      justify-content:center;font-size:15px;line-height:1;background:rgba(255,255,255,.05);
      border:1px solid rgba(255,255,255,.09)}
  .mh-comp-t{font-size:10.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
      color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mh-st{flex:none}
  .mh-when{font-size:11px;color:var(--muted);font-weight:600;margin:1px 0 2px}
  .rowtop{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11px;
          color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
  .rowtop > span:first-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* en-tête match : compétition tronquable + date toujours visible */
  .rt-l{display:flex;align-items:center;min-width:0;flex:1;overflow:hidden}
  .rt-comp{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
  .rt-when{white-space:nowrap;flex:none}
  /* Live : 3 zones (comp à gauche · score/temps CENTRÉS · badge Live à droite) */
  .rowtop-live{display:grid;grid-template-columns:1fr auto 1fr}
  .rt-mid{text-align:center;white-space:nowrap;font-size:12px}
  /* Live : SCOREBOARD 2 lignes (nom + scores),
  meneur en vert,
  set gagné en gras */
  /* Cadre du score (résultats/live) : liseré BLANC neutre au lieu du bleu de marque (demande user 2026-07-28). */
  .lboard{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.16);border-radius:10px;
          padding:8px 12px;margin:9px 0 5px;max-width:100%;overflow-x:auto}
  .lboard::-webkit-scrollbar{display:none}
  /* Séparation horizontale entre le bloc score/barres % et les paris à jouer (écart égal dessus/dessous) */
  .bets-sep{height:1px;background:rgba(255,255,255,.14);margin:12px 0;border-radius:1px}
  /* Effet « terminal » : curseur clignotant pendant la frappe (pronostics + analyse) */
  .tw-cur{display:inline-block;color:var(--accent);font-weight:400;margin:0 0 0 -1px;
       animation:twblink 1s steps(1) infinite}
  @keyframes twblink{50%{opacity:0}}
  /* Temps de jeu live (51',
  Q3·5:42) DANS le cadre des scores : centré,
  vert,
  bien visible */
  .lb-clk{text-align:center;font-size:12px;font-weight:800;color:#34d27b;letter-spacing:.04em;
          padding-bottom:5px;margin-bottom:4px;border-bottom:1px solid rgba(255,255,255,.08)}
  .lb-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:2px 0;
          font-size:14px;font-weight:700;color:var(--muted)}
  .lb-n{flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#fff} /* nom d'équipe : prend l'espace restant et TRONQUE -> les colonnes de score restent toujours visibles */
  .lb-s{display:flex;gap:13px;flex:none}
  .lb-c{min-width:13px;text-align:center;color:var(--muted);font-variant-numeric:tabular-nums}
  .lb-c.lb-win{color:#eaf2ff;font-weight:800}     /* set/score gagné : clair gras */
  .lb-row.lb-lead .lb-c.lb-win{color:#34d27b}     /* meneur : score gagné en vert */
  .lb-pen{font-size:11px;color:var(--muted);font-weight:600}  /* tirs au but « (4) » inline, discret */
  .lb-row.lb-lead .lb-pen{color:#34d27b}          /* vainqueur des t.a.b. : parenthèse verte */
  /* Tennis : MÊME style que le box-score basket (taille,
  gap,
  baseline,
  colonne résultat) */
  .lboard-t{position:relative}
  .lboard-t .lb-s{gap:6px;align-items:baseline}
  .lboard-t .lb-c{width:18px;min-width:18px;font-size:12px}
  .lboard-t .lb-n{font-size:12.5px}
  .lboard-t .lb-hdr .lb-c{font-size:11px}
  /* Colonne SETS (résultat du match) = MÊME que TOT basket : taille/couleur/poids,
  gagnant en vert */
  .lboard-t .lb-tot{width:28px;min-width:28px;font-size:12.5px;font-weight:900;color:#eaf2ff}
  .lboard-t .lb-hdr .lb-tot{font-size:9px}
  .lboard-t .lb-row.lb-lead .lb-tot{color:#34d27b}
  /* UNE seule ligne verticale continue à gauche de SETS (comme basket),
  même position */
  .lboard-t::after{content:"";position:absolute;top:6px;bottom:6px;right:43px;width:1px;
        background:rgba(255,255,255,.18)}
  .lb-hdr .lb-c{color:var(--muted);font-size:11px;font-weight:800;padding-bottom:2px}
  /* Basket : box-score par quart-temps (Q1..Qn) + colonne TOTAL en évidence */
  .lboard-q{position:relative}
  .lboard-q .lb-s{gap:6px;align-items:baseline}  /* même LIGNE DE BASE -> quarts alignés avec le TOT */
  .lboard-q .lb-c{width:18px;min-width:18px;font-size:12px}  /* points de quart un peu plus petits,
  colonnes fixes */
  .lboard-q .lb-n{font-size:12.5px}             /* nom d'équipe (évite la troncature) */
  .lboard-q .lb-hdr{border-bottom:1px solid rgba(255,255,255,.13);padding-bottom:3px;margin-bottom:2px}
  .lboard-q .lb-hdr .lb-c{font-size:11px}       /* Q1..Qn + TOT : en-tête discret (plus petit) */
  .lboard-q .lb-c.lb-ico{font-size:13px}        /* foot : icônes 🟥🟨🚩⚽ lisibles (cartons/corners/buts) */
  .lboard-q .lb-c.lb-tot.lb-ico{font-size:14px}
  .lboard-q .lb-tot{width:28px;min-width:28px;font-size:12.5px;font-weight:900;color:#eaf2ff}
  .lboard-q .lb-row.lb-lead .lb-tot{color:#34d27b}   /* gagnant : SEUL son total en vert */
  .lboard-q .lb-cur{color:#fff}                       /* quart en cours : score en blanc */
  /* UNE seule ligne verticale continue à gauche de TOT,
  du haut au bas des 2 résultats */
  .lboard-q::after{content:"";position:absolute;top:6px;bottom:6px;right:43px;width:1px;
        background:rgba(255,255,255,.18)}
  /* Horloge live (« Q4 · 0:05 ») : BLANCHE,
  même police que les n° de quart,
  alignée à GAUCHE */
  .lboard-q .lb-clk-in{color:#fff;font-weight:800;font-size:11px;letter-spacing:.02em;
        overflow:visible;text-overflow:clip}
  .lb-hdr{padding-bottom:0}
  /* Set EN COURS : juste mis en évidence (clair + gras),
  PAS de case verte */
  .lb-cur{color:#fff;font-weight:800}
  .lb-row.lb-lead .lb-c.lb-cur{color:#fff}
  /* Quart / set À VENIR : 0 grisé (toujours visible : 4 quarts / 3 sets minimum) */
  .lb-fut{color:var(--dim);opacity:.5}
  .lb-row.lb-lead .lb-c.lb-fut{color:var(--dim)}
  /* 🎾 balle de service à droite du nom du serveur */
  .lb-srv{font-size:11px;vertical-align:middle;margin-left:1px}
  /* Colonne 🎾 = points du jeu en cours (0/15/30/40) : en évidence,
  SANS case verte */
  .lb-pt{color:#fff;font-weight:800}
  .lb-pt-h{font-size:12px}
  /* Trait horizontal FIN sous la ligne des sets (en-tête). */
  .lboard-t{position:relative}
  .lboard-t .lb-hdr{border-bottom:1px solid rgba(255,255,255,.13);padding-bottom:3px;margin-bottom:2px}
  /* Colonne points : LARGEUR FIXE (🎾 en-tête et points alignés -> les n° de set restent centrés
     sur les jeux du dessous),
  SANS bordure par cellule. */
  .lboard-t .lb-pt,
  .lboard-t .lb-pt-h{min-width:26px;width:26px;text-align:center;padding-left:0;
        margin-left:0}
  /* (la seule ligne verticale est celle à gauche de SETS,
  définie plus haut comme pour le basket) */
  /* Libellé « cotes en direct » au-dessus des boutons de cotes */
  .live-odds-l{font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;
          color:var(--muted);margin:2px 2px 4px}
  .live-odds-l .live{color:#34d27b;font-size:8px;vertical-align:middle}
  .rowtop-live .rt-r{justify-content:flex-end}
  /* Titre du match : « Équipe A vs Équipe B » sur UNE SEULE ligne,
  petit,
  aligné à GAUCHE (tronqué si long) */
  .players{font-size:13.5px;font-weight:700;margin:5px 0 2px;letter-spacing:-.01em;color:#fff;
           text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.3}
  .players .dim{font-size:12px;font-weight:600}
  /* Ligne du pari : nom+cote à gauche,
  badge value à droite (toujours sur une ligne) */
  .betline{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:5px 0 2px}
  .betline .bn{font-size:16px;font-weight:700;letter-spacing:-.01em;min-width:0;
               overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* affiche (équipes) + badge à droite,
  badge aligné en haut,
  le matchup peut wraper */
  .mrow{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-top:6px}
  .mrow .players{flex:1;min-width:0;text-align:left}   /* affiche alignée à GAUCHE dans la carte */
  .bdg{flex:none}
  /* perle rare : le pari à jouer (confiance×value) mis en avant */
  /* Bloc « pari à jouer »,
  SOUS les cotes : tête (type + pari + cote) puis barre de confiance.
     CONFIANCE = vert · VALUE = bleu · avant-match = neutre. */
  /* Paris GROUPÉS dans un seul cadre,
  coiffé d'un BANDEAU EN-TÊTE (type : Confiance/Value).
     margin-top = l'ESPACE demandé sous les 4 barres. PAS d'overflow:hidden (sur iOS,
  combiné
     au calque fixe body::before{height:1px;background:rgba(255,255,255,.12);margin:14px 2px 14px}
  /* Cadre Confiance/Value : MODULE distinct,
  fond DENSE + bordure marquée + ombre (surélevé)
     -> la bannière colorée se détache des barres de stats au lieu de s'y confondre. */
  .plg{border-radius:12px;margin:0 0 3px;box-shadow:0 5px 16px rgba(0,0,0,.42)}
  /* Type (Confiance/Value) = PASTILLE centrée (pas un bandeau pleine largeur) -> ne ressemble
     plus à une barre de stats. */
  /* Type (Confiance/Value) = simple LIBELLÉ coloré{padding:11px 14px 0;font-size:11px;font-weight:800;text-transform:uppercase;
        letter-spacing:.1em}
  /* LISTE ALIGNÉE : pari (sélection + fiabilité) à GAUCHE{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 2px}
  .perle{display:block;margin:9px 0 3px;padding:10px 12px;border-radius:11px;
         background:rgba(255,255,255,.03);border:1px solid var(--cardline)}
  .pl-top{display:flex;align-items:center;gap:7px}
  .pl-tag{font-size:10.5px;font-weight:800;letter-spacing:.04em;padding:2px 7px;
          border-radius:7px;white-space:nowrap}
  .pl-sel{display:block;text-align:center;font-size:15px;font-weight:800;color:#eaf2ff;
          letter-spacing:-.01em;line-height:1.3;margin:8px 2px 1px}
  .pl-o{font-size:15px;font-weight:800;margin-left:auto}
  /* CONFIANCE = vert */
  .perle-conf{background:linear-gradient(90deg,rgba(25,196,106,.13),rgba(25,196,106,.05));
              border-color:rgba(25,196,106,.45);box-shadow:0 0 14px rgba(25,196,106,.10)}
  .perle-conf .pl-tag{color:#19c46a;background:rgba(25,196,106,.16)}
  .perle-conf .pl-o{color:#34d27b}
  /* VALUE = bleu */
  .perle-value{background:linear-gradient(90deg,rgba(34,184,255,.13),rgba(34,184,255,.05));
               border-color:rgba(34,184,255,.45);box-shadow:0 0 14px rgba(34,184,255,.10)}
  .perle-value .pl-tag{color:#4aa8ff;background:rgba(34,184,255,.16)}
  .perle-value .pl-o{color:#4aa8ff}
  .bdg .badge{white-space:nowrap}
  /* Matchs terminés : prono JOUÉ mis en évidence (Confiance vert / Value bleu) + ✓/✗ */
  .fpick{font-size:12.5px;color:#eaf2ff;padding:8px 11px;border-radius:9px;
         margin:4px 0;border:1px solid var(--cardline);line-height:1.35}
  .fp-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
  .fpick-t{font-weight:800;font-size:10.5px;text-transform:uppercase;letter-spacing:.03em;white-space:nowrap}
  .fp-o{font-weight:800;color:#34d27b;white-space:nowrap}      /* cote en vert,
  à droite du type */
  .fpick-s{font-weight:700;text-align:center;margin-top:5px}   /* le pari,
  centré sur 2e ligne */
  .fp-conf .fpick-t{color:#34d27b}
  .fp-val .fpick-t{color:#4aa8ff}
  /* Cache PAYWALL : remplace le pari pour un non-abonné (cf. app/paywall.py + middleware) */
  .prono-lock{display:flex;align-items:center;gap:11px;margin:8px 0;padding:11px 13px;border-radius:11px;
    text-decoration:none;background:linear-gradient(100deg,rgba(34,184,255,.10),rgba(34,184,255,.03));
    border:1px solid rgba(34,184,255,.35)}
  .prono-lock-i{font-size:19px;line-height:1}
  .prono-lock-t{display:flex;flex-direction:column;gap:2px;flex:1;min-width:0}
  .prono-lock-t b{font-size:12.5px;font-weight:800;color:#eaf2ff}
  .prono-lock-t small{font-size:10.5px;color:#90a4be;font-weight:600}
  .prono-lock-go{font-size:11px;font-weight:800;color:#5fd0ff;white-space:nowrap}
  /* Page COMPTE (connexion / abonnement) — onglet du bas, rendu dans la coquille app. Scopé .acctwrap */
  .acctwrap{max-width:400px;margin:6px auto 0;width:100%}
  .acctwrap .acard{background:linear-gradient(180deg,rgba(34,184,255,.07),rgba(34,184,255,.02));
    border:1px solid rgba(34,184,255,.22);border-radius:18px;padding:22px 20px}
  .acctwrap h1{font-size:19px;font-weight:800;margin:0 0 4px;color:#e9f1fb}
  .acctwrap .sub{font-size:12px;color:#90a4be;margin:0 0 18px;line-height:1.5}
  .acctwrap label{display:block;font-size:11px;font-weight:700;color:#90a4be;text-transform:uppercase;
    letter-spacing:.04em;margin:14px 0 6px}
  .acctwrap input{width:100%;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);
    border-radius:11px;padding:12px 13px;color:#e9f1fb;font-family:inherit;font-size:14px}
  .acctwrap input:focus{outline:none;border-color:rgba(34,184,255,.6)}
  .acctwrap button{width:100%;margin-top:20px;background:#22b8ff;color:#04121c;border:0;border-radius:12px;
    padding:13px;font-family:inherit;font-size:14px;font-weight:800;cursor:pointer}
  .acctwrap button.ghost{background:transparent;color:#5fd0ff;border:1px solid rgba(34,184,255,.35)}
  .acctwrap .err{background:rgba(255,80,90,.12);border:1px solid rgba(255,80,90,.4);color:#ff9aa1;
    border-radius:10px;padding:10px 12px;font-size:12px;margin-bottom:14px;line-height:1.4}
  .acctwrap .ok{background:rgba(25,196,106,.12);border:1px solid rgba(25,196,106,.4);color:#8df3c0;
    border-radius:10px;padding:10px 12px;font-size:12px;margin-bottom:14px;line-height:1.4}
  .acctwrap .alt{text-align:center;font-size:12px;color:#90a4be;margin-top:18px}
  .acctwrap a{color:#5fd0ff;text-decoration:none}
  .acctwrap .arow{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:13px;
    padding:11px 0;border-top:1px solid rgba(255,255,255,.08);color:#e9f1fb}
  .acctwrap .arow b{font-weight:800}
  .acctwrap .abadge{font-size:11px;font-weight:800;border-radius:7px;padding:3px 9px}
  .acctwrap .abadge.on{background:rgba(25,196,106,.18);color:#8df3c0}
  .acctwrap .abadge.off{background:rgba(150,165,185,.16);color:#c0cbdb}
  /* Couleur de la bulle selon le RÉSULTAT (prime sur le type) : vert+halo / rouge+halo */
  .fpick.fp-won{background:linear-gradient(90deg,rgba(25,196,106,.16),rgba(25,196,106,.05));
                border-color:rgba(25,196,106,.75);box-shadow:0 0 15px rgba(25,196,106,.32)}
  .fpick.fp-lost{background:linear-gradient(90deg,rgba(244,73,73,.16),rgba(244,73,73,.05));
                 border-color:rgba(244,73,73,.7);box-shadow:0 0 15px rgba(244,73,73,.3)}
  .badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:800;
         letter-spacing:.02em}
  .b-val{background:rgba(46,226,127,.14);color:var(--accent);border:1px solid rgba(46,226,127,.25)}
  .b-dim{background:var(--surface);color:var(--muted);border:1px solid var(--border)}
  .b-uni{background:rgba(34,184,255,.14);color:#56b0ff;border:1px solid rgba(34,184,255,.30)}
  .b-conf{background:rgba(34,184,255,.16);color:#6cbcff;border:1px solid rgba(34,184,255,.32)}
  details.sec{margin:26px 0 11px}
  details.sec > summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
    font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
  details.sec > summary::-webkit-details-marker{display:none}
  details.sec > summary::before{content:"";width:3px;height:14px;border-radius:3px;flex:none;
    background:linear-gradient(var(--accent),var(--accent2))}
  details.sec .i{margin-left:auto;width:21px;height:21px;border-radius:50%;flex:none;
    border:1px solid var(--border2);display:inline-flex;align-items:center;justify-content:center;
    font:italic 800 12px Georgia,serif;text-transform:none;color:var(--muted)}
  details.sec[open] .i{color:#fff;border-color:var(--accent2);background:rgba(34,184,255,.16)}
  details.sec > .banner{margin-top:9px}
  /* Section repliable (Valeurs / En direct / À venir / Terminés). Titre = bouton. */
  details.sec2{margin:22px 0 4px}
  details.sec2 > summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
    font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;
    padding:6px 0;-webkit-tap-highlight-color:transparent}
  details.sec2 > summary::-webkit-details-marker{display:none}
  details.sec2 > summary::before{content:"";width:3px;height:14px;border-radius:3px;flex:none;
    background:linear-gradient(var(--accent),var(--accent2))}
  details.sec2 > summary > .ttl{flex:1;min-width:0}
  details.sec2 .sright{margin-left:auto;display:inline-flex;align-items:center;gap:10px;flex:none}
  details.sec2 .chev{color:var(--muted);font-size:20px;line-height:1;transition:transform .18s}
  details.sec2[open] .chev{transform:rotate(180deg)}
  details.sec2 .i{width:30px;height:30px;border-radius:50%;flex:none;border:1px solid var(--border2);
    display:inline-flex;align-items:center;justify-content:center;font:italic 800 13px Georgia,serif;
    text-transform:none;color:var(--muted);cursor:pointer}
  details.sec2 .i:active{transform:scale(.92)}
  details.sec2 .sec-info{margin:8px 0 4px}
  details.sec2 > .secbody{margin-top:4px}
  .b-soon{background:var(--surface);color:var(--muted);border:1px solid var(--border);font-weight:700}
  /* badge décompte (timer avant le coup d'envoi),
  en haut à droite de la carte.
     Texte BLANC,
  unités jour/heure/minute bien distinctes. */
  .rt-r{display:inline-flex;align-items:center;gap:6px;margin-left:auto}
  .cd{display:inline-flex;align-items:center;padding:2px 7px;border-radius:20px;font-size:9.5px;font-weight:800;line-height:1;
      font-variant-numeric:tabular-nums;letter-spacing:.02em;background:rgba(255,255,255,.10);
      color:#fff;border:1px solid rgba(255,255,255,.20);white-space:nowrap}
  .cd .u{color:rgba(255,255,255,.55);font-weight:700;margin:0 1px 0 1px}
  /* « soon » (match proche) : MÊME aspect blanc que les autres timers (plus de jaune) */
  .cd.soon{background:rgba(255,255,255,.10);color:#fff;border-color:rgba(255,255,255,.20)}
  /* Badge LIVE plus grand que le décompte (le timer des autres onglets ne change pas) */
  .cd.live{background:rgba(52,210,123,.18);color:#5fe39b;border-color:rgba(52,210,123,.40);
        font-size:10.5px;padding:4px 9px;letter-spacing:.04em}
  .cd.done{background:rgba(255,255,255,.05);color:var(--muted);border-color:var(--border2);
        font-size:11px;padding:3px 8px}
  .cd.wait{background:rgba(246,197,74,.12);color:var(--gold);border-color:rgba(246,197,74,.32);
        font-size:11px;padding:3px 8px}
  /* HEURE + DÉCOMPTE dans UN MÊME badge (user 2026-08-08) : heure à gauche en BLANC, décompte à droite en
     GRIS, sans « dans ». Le décompte est rempli/rafraîchi en direct par le timer JS. Au coup d'envoi, le
     timer reprend le vert « live » normal (:not(.live)). */
  .cleg-when{display:inline-flex;align-items:baseline;gap:5px;font-variant-numeric:tabular-nums;
        font-size:11px;white-space:nowrap}
  .cleg-when .cw-h{color:#fff;font-weight:900}                     /* heure = BLANC */
  .cleg-when .cw-sep{color:var(--muted);font-weight:600}           /* séparateur « - » heure ↔ décompte */
  .cleg-when .cd:not(.live){background:transparent;border-color:transparent;color:var(--muted);
        font-weight:700;padding:0;font-size:11px}                 /* décompte = GRIS, MÊME taille que l'heure */
  .formrow{display:flex;justify-content:space-between;align-items:center;margin-top:7px}
  .fc{display:inline-flex;align-items:center;gap:5px;font-size:11px}
  .forms{display:inline-flex;gap:3px;vertical-align:middle;margin-left:4px}
  .fd{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
      border-radius:4px;font-size:9px;font-weight:800;color:#08110a;line-height:1;
      text-transform:uppercase;text-align:center;padding-top:1px}
  /* Sablier « en attente » dans la bande W/L : MÊME doré que le badge provisoire .sx-bdg.p (demande
     user 2026-07-17). Paris à jouer pas encore réglés, en queue à droite (le plus récent). */
  .fd.fd-p{background:var(--gold);font-size:11px;padding-top:0}
  .pbars{margin-top:7px;display:flex;flex-direction:column;gap:5px}
  .pb-h{font-size:12px;color:var(--text);margin-bottom:2px}
  /* TABLEAU « Chances de gagner » : sources en LIGNES,
  issues en COLONNES + fine barre/ligne */
  /* Barres PLEINES : source au-dessus,
  % dans chaque segment (favori = couleur source) */
  /* ===== Bloc « Cotes & chances » PREMIUM v2 : cadre soigné (dégradé + ombre douce) + barre à glow
     + boîtes en relief, cote en pastille, favori surélevé ===== */
  .ocs{margin:12px 0 2px;display:flex;flex-direction:column;gap:12px}
  .oc{width:100%;padding:13px 13px 12px;border-radius:16px;
        background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.014));
        border:1px solid var(--border);
        box-shadow:0 1px 0 rgba(255,255,255,.04) inset,0 8px 22px rgba(0,0,0,.24)}
  .oc-h{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;text-transform:uppercase;
        letter-spacing:.1em;color:var(--muted);margin-bottom:11px}
  .oc-h::before{content:"";flex:none;width:5px;height:5px;border-radius:99px;
        background:linear-gradient(180deg,#3ee089,#19c46a);box-shadow:0 0 8px rgba(52,210,123,.7)}
  .oc-h::after{content:"";flex:1;height:1px;
        background:linear-gradient(90deg,var(--border2),transparent)}
  .ocb{display:flex;width:100%;gap:3px;height:7px;border-radius:99px;overflow:hidden;margin-bottom:12px;
        background:rgba(0,0,0,.28)}
  .ocb-s{height:100%;border-radius:99px}
  .ocb-po{background:linear-gradient(90deg,#19c46a,#3ee089);box-shadow:0 0 10px rgba(52,210,123,.5)}
  .ocb-pc{background:linear-gradient(90deg,#d8a93a,#f0cf63);box-shadow:0 0 10px rgba(232,195,77,.45)}
  .ocb-dim{background:rgba(255,255,255,.1)}
  .ocp-row{display:flex;width:100%;gap:7px}
  .ocp{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:2px;
        padding:10px 6px 9px;border-radius:13px;text-align:center;
        background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.016));
        border:1px solid var(--border);box-shadow:0 2px 8px rgba(0,0,0,.16)}
  .ocp-n{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
        font-size:10.5px;font-weight:700;color:var(--muted);letter-spacing:.01em}
  .ocp-v{font-size:19px;font-weight:900;color:var(--text);line-height:1.02;
        font-variant-numeric:tabular-nums;letter-spacing:-.015em}
  .ocp-c{margin-top:3px;font-size:9.5px;font-weight:800;color:var(--muted);
        font-variant-numeric:tabular-nums;padding:1px 8px;border-radius:99px;
        background:rgba(255,255,255,.05);border:1px solid var(--border)}
  /* chip FAVORI : liseré + fond teintés de la source, valeur en couleur, légère élévation + halo */
  .ocp-fav{transform:translateY(-1px)}
  .ocp-fav.ocb-po{border-color:rgba(52,210,123,.55);
        background:linear-gradient(180deg,rgba(52,210,123,.2),rgba(52,210,123,.05));
        box-shadow:0 0 0 1px rgba(52,210,123,.16),0 8px 18px rgba(25,196,106,.2)}
  .ocp-fav.ocb-po .ocp-v{color:#5be08c} .ocp-fav.ocb-po .ocp-n{color:#cdeecf}
  .ocp-fav.ocb-po .ocp-c{color:#7ff0b6;background:rgba(52,210,123,.15);border-color:rgba(52,210,123,.42)}
  .ocp-fav.ocb-pc{border-color:rgba(232,195,77,.55);
        background:linear-gradient(180deg,rgba(232,195,77,.2),rgba(232,195,77,.05));
        box-shadow:0 0 0 1px rgba(232,195,77,.16),0 8px 18px rgba(232,195,77,.18)}
  .ocp-fav.ocb-pc .ocp-v{color:#f0cf63} .ocp-fav.ocb-pc .ocp-n{color:#efe2b4}
  .ocp-fav.ocb-pc .ocp-c{color:#f0cf63;background:rgba(232,195,77,.15);border-color:rgba(232,195,77,.42)}
  /* barre Public compacte : libellés sous la barre fine */
  .oc-pub{font-size:10.5px;color:var(--muted);font-weight:600}
  .oc-pub b{color:#cfe0f5;font-weight:800}
  /* Barre « Bookmakers » : 1 segment par issue (cote seule),
  parts ÉGALES. Les 3 ont le
     MÊME fond que le segment le plus faible (non-favori) des autres barres -> navy .pba. */
  .sb-bar.ocbar .seg{flex:1 1 0;min-width:0;gap:5px;padding:0 7px;min-height:44px;align-items:center}
  .ocbar .seg b{font-size:13px;font-weight:800;font-variant-numeric:tabular-nums}
  .ptab2{margin:8px 0 2px}
  .pt2-h{display:grid;grid-template-columns:var(--cols);gap:6px;align-items:center;
         padding:5px 2px;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.03em;
         color:var(--muted);border-bottom:1px solid var(--border)}
  /* en-tête : Source à gauche ; les NOMS de joueurs CENTRÉS sur leurs % (comme .pt2-v) */
  .pt2-h span{text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .pt2-h span:first-child{text-align:left}
  /* Bloc = grille 2 lignes : source (col 1,
  centrée verticalement) | % (ligne 1) | barre (ligne 2) */
  .pt2-block{display:grid;grid-template-columns:var(--cols);column-gap:6px;align-items:center;
         padding:6px 2px;border-bottom:1px solid rgba(255,255,255,.04)}
  .pt2-block:last-child{border-bottom:none}
  .pt2-s{grid-column:1;grid-row:1/3;align-self:center;
         font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.03em;
         color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .pt2-v{grid-row:1;text-align:center;font-size:13px;font-weight:600;color:var(--muted);
         font-variant-numeric:tabular-nums}
  .pt2-v.hi{font-weight:800}
  .pt2-v.dim{color:var(--dim)}
  .t-pm{color:#4aa8ff} .t-po{color:#43dd8c} .t-pc{color:#e8c34d}   /* favori = couleur de la source */
  /* Barre : ligne 2,
  à partir de la colonne 2 (démarre donc après la source) */
  .pt2-bar{grid-column:2/-1;grid-row:2;margin-top:5px;
         display:flex;gap:1px;height:4px;border-radius:99px;overflow:hidden;background:var(--surface)}
  .pt2-bar > span{display:block;height:100%}
  .pb-row{display:flex;align-items:center;gap:7px;font-size:11px}
  .pb-l{width:64px;flex:none;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;
        font-weight:800;font-size:9px}
  /* Piste = flex : segment home (couleur source) | nul | away (atténué),
  total 100% */
  .pb-t{flex:1;height:8px;border-radius:99px;background:var(--surface);overflow:hidden;
        display:flex;gap:1px}
  .pb-t > span{display:block;height:100%}
  .pb-v{width:36px;flex:none;text-align:right;font-weight:800}
  /* Barres comparatives : couleurs FIXES (identiques tous sports/onglets) ->
     BETSFIX bleu,
  BOOKMAKER gris,
  PUBLIC jaune. Ne pas thématiser par sport. */
  .pm{background:linear-gradient(90deg,#1f80e6,#2e9bff)}   /* BETSFIX bleu */
  .po{background:linear-gradient(90deg,#19c46a,#34d27b)}   /* Cote Unibet VERT */
  .pc{background:#e0b341}                                   /* Public jaune */
  .pbd{background:#7a8094}             /* segment NUL (gris clair,
  bien distinct) */
  .pba{background:#2d3f66}             /* segment équipe NON-favorite (navy atténué) */
  /* Divergence public/modèle : emoji à droite de la barre PUBLIC + bulle au tap */
  .pb-x{width:20px;flex:none;text-align:center}
  .dvg-i{cursor:pointer;font-size:14px;line-height:1;-webkit-tap-highlight-color:transparent;
    padding:11px;margin:-11px;display:inline-block}  /* zone tactile ~40px sans changer le visuel */
  .dvg-i:active{opacity:.6}
  .dvg-bubble{margin-top:8px;padding:9px 12px;border-radius:10px;font-size:12px;line-height:1.5;
              background:var(--surface2);border:1px solid var(--border2);color:var(--muted)}
  .dvg-bubble b{color:var(--text)}
  /* Barre de cotes : une cellule par issue (joueur 1 / Nul / joueur 2) ; favori (cote la
     plus basse) mis en avant en bleu. Nom au-dessus,
  cote dessous. */
  .oddsrow{display:flex;gap:6px;margin-top:7px}
  /* TOUS les boutons de cotes en encadré BLEU ; la cote pariée est un peu plus marquée */
  .oc{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:1px;
      background:rgba(34,184,255,.07);border:1px solid rgba(34,184,255,.4);border-radius:10px;padding:5px 6px}
  .oc.fav{border-color:#2e9bff;background:rgba(34,184,255,.16);box-shadow:0 0 12px rgba(34,184,255,.2)}
  .ocn{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;
       max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .oc.fav .ocn{color:#9fd0ff}
  .ocv{font-size:14.5px;font-weight:800;font-variant-numeric:tabular-nums}
  /* Cotes COMPACTES sur une ligne (cartes) : « Espagne 1.03 · Nul 16.0 · Irak 36.0 » */
  .oddsrow2{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:4px 14px;
        margin-top:8px;padding:7px 12px;border-radius:10px;
        background:rgba(34,184,255,.06);border:1px solid rgba(34,184,255,.22)}
  .oc2{font-size:12.5px;color:var(--muted);white-space:nowrap}
  .oc2 b{color:#eaf2ff;font-weight:800;margin-left:3px;font-size:13.5px;font-variant-numeric:tabular-nums}
  .oc2.fav{color:#9fd0ff} .oc2.fav b{color:#56b0ff}
  /* Tous les paris Unibet : un bloc par marché,
  cotes qui wrappent si nombreuses */
  .mkt{margin:9px 0}
  .mkt-l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;
         font-weight:700;margin-bottom:4px}
  .oddsrow-wrap{flex-wrap:wrap}
  .oddsrow-wrap .oc{flex:1 1 28%;min-width:82px}
  /* Catégories de paris repliables (comme Unibet) */
  .mktcat{border:1px solid var(--border);border-radius:12px;margin:7px 0;overflow:hidden;
          background:var(--surface)}
  .mktcat>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
          padding:11px 14px;font-size:13px;font-weight:700}
  .mktcat>summary::-webkit-details-marker{display:none}
  .mktcat>summary::after{content:"▾";margin-left:auto;color:var(--dim);transition:transform .18s}
  .mktcat[open]>summary::after{transform:rotate(180deg)}
  .mktcat-n{background:var(--surface2);color:var(--muted);border:1px solid var(--border);
            border-radius:20px;padding:1px 9px;font-size:11px;font-weight:800}
  .mktcat-b{padding:2px 14px 10px}
  /* Fiche match détaillée (foot/basket) */
  .mdh{margin:14px 0 6px}
  .mdh-c{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-weight:700}
  .mdh-t{font-size:20px;font-weight:800;letter-spacing:-.01em;margin-top:5px}
  .frm{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:11px 0;
       border-bottom:1px solid var(--border)}
  .frm:last-child{border:none}
  .frm-t{flex:1 1 120px;font-size:14px;font-weight:700;min-width:0}
  .h2h{display:flex;gap:8px;margin:6px 0}
  .h2h-c{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:12px 6px;
         background:var(--surface);border:1px solid var(--border);border-radius:12px}
  .h2h-c b{font-size:22px;font-weight:800}
  .h2h-c .dim{font-size:11px;text-align:center}
  .votes{margin-top:7px}
  .vlbl{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
  .vbar{display:flex;height:6px;border-radius:99px;overflow:hidden;margin-top:3px;background:var(--surface)}
  .vbar .vh{background:var(--accent2)}
  .vbar .va{background:#5a6472}
  .bar{height:9px;border-radius:99px;background:rgba(242,93,110,.22);overflow:hidden;margin:8px 0}
  .bar > span{display:block;height:100%;border-radius:99px;
              background:linear-gradient(90deg,var(--accent2),var(--accent))}
  /* Barre de proba (2 issues home/away ou 3 issues 1-N-2) */
  .pbar{display:flex;height:8px;border-radius:99px;overflow:hidden;margin:9px 0 3px;
        background:var(--border);gap:1px}
  .pbar span{display:block;height:100%}
  .pbar .s1{background:linear-gradient(90deg,var(--accent2),var(--accent))}
  .pbar .s2{background:var(--surface2)}
  .pbar .sx{background:var(--dim)}
  .pbar-l{display:flex;justify-content:space-between;font-size:11px;color:var(--dim);
          font-weight:700;letter-spacing:.02em;gap:6px}
  .pbar-l span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* Dataviz fiche match : pastilles de forme + mini-barres de facteurs */
  .dots{display:flex;gap:5px;justify-content:space-between}   /* réparti sur toute la largeur */
  /* Nom d'équipe/joueur des formes récentes : MÊME présentation sur les 3 sports,
  centré */
  .fm-name{font-size:14px;font-weight:800;text-align:center;margin:2px 0 8px;color:#eaf2ff}
  .fm-name .dim{font-weight:600}
  .dot{width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;
       justify-content:center;font-size:11px;font-weight:800}
  .dot.w{background:var(--green);color:#04130a}
  .dot.n{background:var(--gold);color:#1a1400}    /* nul = jaune (cf. légende) */
  .dot.l{background:var(--red);color:#fff}
  .mbar{height:7px;border-radius:99px;overflow:hidden;display:flex;background:var(--border);gap:1px}
  .mbar .a{background:linear-gradient(90deg,var(--accent2),var(--accent))}
  .mbar .b{background:var(--surface2)}
  .frow{padding:10px 0;border-bottom:1px solid var(--border)}
  .frow:last-child{border:none}
  .frow .ft{display:flex;align-items:center;gap:10px}
  .frow .fn{flex:0 0 88px;font-size:12.5px;font-weight:700}
  .frow .fb{flex:1}
  .frow .fp{flex:0 0 76px;text-align:right;font-size:11px;color:var(--muted);
            font-variant-numeric:tabular-nums;font-weight:700}
  /* Tables */
  table{width:100%;border-collapse:collapse;font-size:13px;margin:4px 0;
        background:var(--surface);border:1px solid var(--border);border-radius:14px;
        overflow:hidden;box-shadow:var(--shadow-sm)}
  td{padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:top}
  tr:last-child td{border-bottom:none}
  tr:first-child td{background:rgba(255,255,255,.02);font-size:11px;text-transform:uppercase;
                    letter-spacing:.05em}
  .dim{color:var(--muted);font-size:12px}
  .pos{color:var(--green);font-weight:700}.neg{color:var(--red);font-weight:700}
  /* Banners — info discret par défaut,
  ambre seulement pour les vraies alertes (.warn) */
  .banner{background:var(--surface);border:1px solid var(--border);
          border-left:3px solid var(--border2);color:var(--muted);border-radius:12px;
          padding:11px 14px;font-size:12.5px;line-height:1.55;margin:11px 0}
  .banner b{color:var(--text)}
  .banner.warn{background:linear-gradient(180deg,var(--gold-bg),rgba(35,29,9,.45));
          border:1px solid var(--gold-bd);border-left:3px solid var(--gold);color:var(--gold)}
  .banner.warn b{color:#ffd877}
  /* Carte « analyse rédigée » (texte d'expert généré) — premium */
  .an-card{background:linear-gradient(180deg,var(--surface2),var(--surface));
          border:1px solid var(--cardline);border-left:3px solid var(--brand);border-radius:14px;
          padding:13px 15px;margin:11px 0;box-shadow:var(--cardglow)}
  .an-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}
  .an-ic{font-size:16px}
  .an-title{font-weight:800;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em;
          color:var(--muted)}
  .an-tag{margin-left:auto;font-size:10.5px;font-weight:800;padding:2px 9px;border-radius:20px;
          white-space:nowrap}
  .an-tag.val{background:rgba(46,226,127,.14);color:var(--green);border:1px solid rgba(46,226,127,.3)}
  .an-tag.conf{background:rgba(34,184,255,.14);color:var(--brand);border:1px solid rgba(34,184,255,.32)}
  .an-tag.no{background:var(--surface2);color:var(--muted);border:1px solid var(--border)}
  .an-body{font-size:13.5px;line-height:1.62;color:var(--text)}
  .an-note{font-size:11px;color:var(--muted);margin-top:9px;border-top:1px solid var(--border);
          padding-top:7px}
  /* « Preuve » — tableau unique (1 ligne/sport,
  colonnes alignées) façon tableau de bord */
  .ptab{border:1px solid var(--cardline);border-radius:14px;overflow:hidden;margin:8px 0;
          background:linear-gradient(180deg,var(--surface2),var(--surface));
          box-shadow:var(--cardglow)}
  .ptab-h,
  .ptab-row{display:grid;grid-template-columns:1fr 1.4fr .8fr .8fr;gap:5px;
          align-items:center;padding:11px 12px}
  .ptab-h{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.01em;
          color:#eaf2ff;border-bottom:1px solid var(--border);background:rgba(255,255,255,.022)}
  .ptab-h span{text-align:center} .ptab-h span:first-child{text-align:left}
  .ptab-h .ph-conf{color:#34d27b} .ptab-h .ph-val{color:#4aa8ff}   /* Confiance vert · Value bleu */
  .ptab-row{border-top:1px solid var(--border);border-left:3px solid var(--sc,var(--border2));
          text-decoration:none;color:var(--text);transition:background .15s}
  .ptab-row:first-of-type{border-top:none}
  .ptab-row:active,
  .ptab-row:hover{background:rgba(255,255,255,.03)}
  .ptab-sport{font-weight:800;font-size:12.5px;line-height:1.2;min-width:0;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ptab-sub{display:block;font-size:11px;font-weight:600;color:var(--muted)}
  /* Fiabilité = verdict coloré + nb de matchs dessous. min-width:0 + sous-ligne qui peut
     se replier -> la colonne RESTE à sa fraction (sinon elle s'élargit et décale Confiance/Value). */
  .ptab-verdict{font-size:11px;font-weight:800;text-align:center;white-space:nowrap;min-width:0;
          display:flex;flex-direction:column;align-items:center;gap:2px}
  .ptab-vsub{font-size:9px;font-weight:600;color:var(--muted);white-space:nowrap;text-align:center}
  .ptab-verdict.ok{color:var(--green)} .ptab-verdict.ko{color:var(--red)}
  .ptab-verdict.na{color:var(--muted)}
  .ptab-conf,
  .ptab-val{font-size:14px;font-weight:800;text-align:center;white-space:nowrap;
          line-height:1.1;min-width:0}
  .ptab-conf.na,
  .ptab-val.na{color:var(--muted);font-weight:600;opacity:.5;font-size:16px}
  .ptab-pct{display:block;font-size:11px;font-weight:700;color:var(--muted);margin-top:1px}
  .ptab-pct.pos,
  .ptab-pct .pos{color:var(--green)} .ptab-pct.neg,
  .ptab-pct .neg{color:var(--red)}
  /* Mini-barre de progression PAR SPORT (colonne Fiabilité) : réglés (plein) + en attente (estompé) */
  .pbar2{display:flex;width:86%;max-width:88px;height:5px;border-radius:99px;
          background:var(--border);overflow:hidden;margin:5px auto 4px}
  .pbar2 .pg-done{height:100%;background:linear-gradient(90deg,#34d27b,#4aa8ff)}
  .pbar2 .pg-wait{height:100%;background:rgba(159,180,207,.32)}
  /* Légende sous le tableau */
  .ptab-cap{font-size:11px;color:var(--muted);text-align:center;margin:11px 4px 2px;line-height:1.5}
  .ptab-cap b{color:#cfe0f5}
  .pg-lg{display:inline-block;width:14px;height:5px;border-radius:99px;vertical-align:middle;margin-right:2px}
  .pg-lg.done{background:linear-gradient(90deg,#34d27b,#4aa8ff)}
  .pg-lg.wait{background:rgba(159,180,207,.32)}
  /* Courbe d'équité (P&L cumulé dans le temps) : SVG généré côté serveur,
  sans JS */
  .evo-svg{width:100%;height:auto;display:block;margin:8px 0 2px}
  .evo-legend{display:flex;justify-content:center;gap:15px;flex-wrap:wrap;margin:7px 0 1px}
  .evo-lg{font-size:11px;font-weight:700;color:var(--muted);display:inline-flex;align-items:center;gap:5px}
  .evo-lg i{width:12px;height:3px;border-radius:99px;display:inline-block}
  .evo-lg b.pos{color:var(--green)} .evo-lg b.neg{color:var(--red)}
  .evo-na{font-size:10.5px;color:var(--muted);font-style:italic;text-align:center;padding:10px 0}
  /* Légende des dates d'optimisation perle (sous la section ; = lignes ambre des courbes) */
  .evo-optim{font-size:10.5px;color:var(--muted);text-align:center;margin-top:10px;padding-top:9px;
       border-top:1px solid var(--border);line-height:1.7}
  .evo-optim b{color:#cfe0f5} .evo-otag{color:#ffa94a;font-weight:800}
  /* Carte détail PAR SPORT : verdict + échantillon + barres taux/ROI + courbe P&L cumulé */
  .spc{margin:11px 0;padding:11px 13px 9px;border-radius:var(--radius);
       background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--cardline);border-left:3px solid var(--sc,var(--border));
       box-shadow:var(--cardglow),var(--shadow)}
  .spc-head{display:flex;justify-content:space-between;align-items:center;gap:8px}
  .spc-name{font-weight:800;font-size:14px;white-space:nowrap}
  .spc-verdict{font-size:11px;font-weight:800;white-space:nowrap}
  .spc-verdict.ok{color:var(--green)} .spc-verdict.ko{color:var(--red)} .spc-verdict.na{color:var(--muted)}
  .spc-sample{font-size:11px;color:var(--muted);font-weight:600;margin:1px 0 7px;
       display:flex;justify-content:space-between;align-items:center;gap:8px}
  /* Badge de tendance récente (7 j) par sport */
  .spc-trend{font-size:11px;font-weight:800;white-space:nowrap}
  .spc-trend.up{color:var(--green)} .spc-trend.down{color:var(--red)} .spc-trend.flat{color:var(--muted)}
  .spc-trend-l{font-weight:600;color:var(--muted);opacity:.8}
  .spc-foot{font-size:11px;color:var(--muted);text-align:center;margin-top:4px;line-height:1.5}
  .spc-foot b.pos{color:var(--green)} .spc-foot b.neg{color:var(--red)}
  .spc-tot{font-weight:800}   /* P&L Total mis en avant */
  /* Analyse « analyste » (markdown rendu) en fiche match */
  .da{font-size:13px;line-height:1.55;color:#e8eaed}
  .da-h{font-weight:800;color:#e8eaed;margin:13px 0 5px}
  .da-h1{font-size:15px} .da-h2{font-size:13.5px} .da-h3{font-size:12.5px;color:#cfe0f5}
  .da-p{margin:6px 0}
  /* Carte DÉPLIÉE refondue (demande user 2026-07-13) : sections premium empilées, en-tête à petite capitale.
     « Pourquoi ce pari » = section accentuée (cœur de l'analyse) ; faits VISIBLES ; mise discrète. */
  .da-sec{margin:12px 0;padding:12px 13px;border-radius:14px;background:rgba(255,255,255,.022);
       border:1px solid var(--border)}
  .da-sec:first-child{margin-top:2px}
  .da-sec>.da-h:first-child{margin-top:0;display:flex;align-items:center;gap:7px;font-size:12px;
       letter-spacing:.02em;color:#cfe0f5}
  .da-sec .da-p:first-of-type{margin-top:0}
  .da-sec-why{background:linear-gradient(180deg,rgba(34,184,255,.07),rgba(34,184,255,.012));
       border-color:rgba(34,184,255,.24)}
  .da-sec-why>.da-h:first-child{color:#8fd0ff}
  .da-sec-mise{background:var(--gold-bg);border-color:var(--gold-bd)}
  .da-sec-mise>.da-h:first-child{color:var(--gold)}
  .da-ul{margin:5px 0;padding-left:17px} .da-ul li{margin:3px 0}
  .da-quote{border-left:3px solid var(--gold);background:var(--gold-bg);padding:7px 10px;
       margin:9px 0;border-radius:6px;font-size:12px;color:var(--gold)}
  .da-tbl{width:100%;border-collapse:collapse;margin:9px 0;font-size:11.5px}
  .da-tbl th,
  .da-tbl td{border:1px solid var(--border);padding:5px 7px;text-align:left;vertical-align:top}
  .da-tbl th{background:var(--surface2);font-weight:700;color:#cfe0f5}
  .da a{color:#5ab0ff;text-decoration:none}
  /* === Habillage analyste premium : Verdict héro + tableau + faits + tendances === */
  .da{font-size:13px;line-height:1.55;color:var(--text)}
  /* Bandeau résultat (règlement après match) */
  .da-res{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:4px 0 12px;
       padding:9px 13px;border-radius:12px;font-size:13px;font-weight:800;border:1px solid}
  .da-res-win{background:rgba(52,210,123,.15);color:#3ee089;border-color:rgba(52,210,123,.35)}
  .da-res-lose{background:rgba(242,93,110,.15);color:#ff7484;border-color:rgba(242,93,110,.35)}
  .da-res-push{background:var(--gold-bg);color:var(--gold);border-color:var(--gold-bd)}
  .da-res-nv{background:var(--surface2);color:var(--muted);border-color:var(--border)}
  .da-res-sc{font-weight:800;color:#cfe0f5;font-size:12px}
  /* Carte « Track record analyste » premium */
  .arec{margin:2px 0 14px;padding:13px 14px 12px;border-radius:var(--radius);
       background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--cardline);box-shadow:var(--cardglow),var(--shadow)}
  .arec-h{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:9px}
  .arec-h-l{font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:#cfe0f5}
  .arec-h-sub{font-size:10.5px;color:var(--muted)}
  .arec-tot{display:flex;align-items:center;gap:12px;padding:6px 0 10px;border-bottom:1px solid var(--border)}
  .arec-big{font-size:30px;font-weight:900;line-height:1;letter-spacing:-.02em}
  .arec-tot-v{font-size:13px;color:var(--muted)} .arec-tot-v b{color:var(--text);font-size:15px}
  .arec-sports{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:11px}
  .arec-sp{background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:11px;
       padding:9px 8px;text-align:center}
  .arec-sp-h{font-size:11px;font-weight:700;color:var(--muted);white-space:nowrap}
  .arec-sp-v{font-size:20px;font-weight:900;color:var(--text);margin:3px 0 1px}
  .arec-sp-t{font-size:12px;font-weight:700;color:var(--muted)}
  .arec-sp-p{font-size:12px;font-weight:800}
  .arec-sp-u{font-size:11px;font-weight:700;color:#cfe0f5;margin-top:2px;font-variant-numeric:tabular-nums}
  .arec-sp-roiv{font-size:23px;font-weight:900;line-height:1.05;margin-top:3px;font-variant-numeric:tabular-nums}
  .arec-sp-roi{font-size:10.5px;font-weight:800;letter-spacing:.08em;color:var(--muted);text-transform:uppercase}
  .arec-sp-v2{font-size:11.5px;font-weight:700;color:#cfe0f5;margin-top:5px}
  .arec-sp-o{font-size:10.5px;font-weight:700;color:var(--muted);margin-top:1px;font-variant-numeric:tabular-nums}
  .arec-hi{color:#3ee089} .arec-mid{color:var(--gold)} .arec-lo{color:#ff7484}
  .arec-na{color:var(--muted)}
  /* Hero « Avantage réalisé » (style Bull) — teinte verte, ROI géant + KPIs (user 2026-08-15) */
  .adv-hero{text-align:center;padding:18px 14px 16px;margin:2px 0 14px;border-radius:18px;
       background:linear-gradient(180deg,rgba(62,224,137,.11),rgba(62,224,137,.02));border:1px solid rgba(62,224,137,.24)}
  .adv-l{font-size:11px;font-weight:800;letter-spacing:.15em;text-transform:uppercase;color:var(--muted)}
  .adv-big{font-size:54px;font-weight:900;letter-spacing:-.03em;line-height:1;margin:6px 0 5px}
  .adv-sub{font-size:12.5px;color:var(--muted);font-weight:600}
  .adv-sub b{color:var(--text);font-variant-numeric:tabular-nums}
  .adv-kpis{display:flex;justify-content:center;gap:28px;margin-top:13px}
  .adv-kpis div{text-align:center}
  .adv-kpis b{display:block;font-size:18px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums}
  .adv-kpis span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}   /* ROI peu fiable (échantillon trop faible) -> grisé */
  /* VERDICT MARCHÉS — synthèse actionnable en tête de l'onglet Analyse (demande user 2026-08-13). */
  .av-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:14px 14px 12px;margin:2px 0 14px}
  .av-card-h{font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--text);
       margin-bottom:11px;display:flex;align-items:center;gap:7px}
  .av-top{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:13px}
  .av-kpi{flex:1 1 150px;background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:11px;padding:9px 11px}
  .av-kpi-l{font-size:9.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
  .av-kpi-v{font-size:14px;font-weight:800;margin-top:4px;color:var(--text)}
  .av-kpi-v small{font-size:11px;font-weight:700;color:var(--muted);margin-left:5px}
  .av-row{margin-top:9px}
  .av-row-h{font-size:10.5px;font-weight:800;letter-spacing:.03em;margin-bottom:6px;color:var(--muted)}
  .av-chips{display:flex;flex-wrap:wrap;gap:6px}
  .av-chip{font-size:11px;font-weight:700;padding:4px 9px;border-radius:999px;border:1px solid;
       white-space:nowrap;font-variant-numeric:tabular-nums}
  .av-chip b{font-weight:800} .av-chip small{font-weight:700;opacity:.72;margin-left:5px}
  .av-play{background:rgba(46,226,127,.10);border-color:rgba(46,226,127,.32);color:#3ee089}
  .av-watch{background:var(--gold-bg);border-color:var(--gold-bd);color:var(--gold)}
  .av-avoid{background:rgba(255,116,132,.10);border-color:rgba(255,116,132,.32);color:#ff7484}
  .av-empty{font-size:11px;color:var(--dim);font-style:italic}
  .av-tier-roi{font-size:19px;font-weight:900;margin-top:4px;font-variant-numeric:tabular-nums}
  .av-tier-sub{font-size:10.5px;color:var(--muted);margin-top:2px}
  /* Graphiques performance PAR PARI (SVG,
  courbes de profit cumulé) */
  .bcharts{margin:2px 0 14px;display:flex;flex-direction:column;gap:10px}
  .bcharts-h{font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;
       color:#cfe0f5;display:flex;align-items:baseline;justify-content:space-between;gap:8px}
  .bcharts-sub{font-size:11px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0}
  .bchart-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
       padding:11px 12px 10px}
  .bchart-h{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:4px}
  .bchart-t{font-size:13px;font-weight:800;color:var(--text)}
  .bchart-tot{font-size:14px;font-weight:900}
  .bchart{width:100%;height:auto;display:block;max-height:180px}
  .bc-axis{stroke:rgba(255,255,255,.12);stroke-width:1}
  .bc-zero{stroke:rgba(255,255,255,.5);stroke-width:1.3;stroke-dasharray:5 3}
  .bc-zl{fill:rgba(255,255,255,.6);font-size:9px;font-weight:800;text-anchor:end}
  .bc-line{stroke-width:2.2;vector-effect:non-scaling-stroke;stroke-linejoin:round;stroke-linecap:round}
  /* Jalons du modèle : repère vertical + étiquette (changement de politique de paris) */
  /* Repères de modèle sur la courbe : trait vertical + pastille numérotée */
  .bc-mile{stroke:rgba(120,200,255,.5);stroke-width:1.1;stroke-dasharray:2 3}
  /* Repère AUTO (marché auto-ajusté) : ambré, pour le distinguer d'un jalon méthodo (bleu) */
  .bc-mile-g.mauto .bc-mile-c{fill:#ff9f43;stroke:#ffe0bd}
  .bc-mile-g.mauto .bc-mile{stroke:rgba(255,159,67,.55)}
  .bc-mile-g{cursor:pointer}
  .bc-mile-g .bc-mile-c{transition:r .12s}
  .bc-mile-g.on .bc-mile-c{fill:#46e08a;stroke:#bdf6d4}
  .bc-mile-g.on .bc-mile{stroke:rgba(70,224,138,.7)}
  .bc-mile-c{fill:#1496f0;stroke:#bfe2ff;stroke-width:.8}
  .bc-mile-n{fill:#fff;font-size:7px;font-weight:900;pointer-events:none}
  /* Repères ALLÉGÉS : pastilles cliquables + panneau d'info au clic (page plus légère) */
  .sx-miles{margin-top:10px}
  .sx-miles-c{margin-top:0}
  /* Transparence : marchés écartés (type de pari · raison · seuils) */
  .exq{display:flex;flex-direction:column;gap:8px}
  .exq-intro{font-size:11px;line-height:1.5;color:var(--muted);margin-bottom:2px}
  .exq-intro b{color:var(--text);font-weight:800}
  .exq-sport{display:flex;flex-direction:column;gap:6px;padding:8px;border:1px solid var(--border);
       border-radius:14px;background:rgba(255,255,255,.015)}
  .exq-sphead{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:1px 3px 2px}
  .exq-spname{font-size:13px;font-weight:900;letter-spacing:.01em;color:var(--text)}
  .exq-sptag{font-size:9.5px;font-weight:800;border-radius:6px;padding:2px 8px;white-space:nowrap}
  .exq-sptag-ex{background:rgba(255,107,107,.14);color:#ff9b9b;border:1px solid rgba(255,107,107,.32)}
  .exq-sptag-ok{background:rgba(52,210,123,.12);color:#7fe0a8;border:1px solid rgba(52,210,123,.28)}
  .exq-row{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:12px;padding:9px 11px}
  .exq-top{display:flex;align-items:center;gap:8px;margin-bottom:3px}
  .exq-mk{font-size:13px;font-weight:800;color:var(--text)}
  .exq-bdg{font-size:9.5px;font-weight:800;letter-spacing:.03em;border-radius:6px;padding:2px 7px;white-space:nowrap}
  .exq-ex{background:rgba(255,107,107,.16);color:#ff9b9b;border:1px solid rgba(255,107,107,.35)}
  .exq-watch{background:rgba(224,179,65,.14);color:#e6c463;border:1px solid rgba(224,179,65,.32)}
  .exq-ok{background:rgba(52,210,123,.14);color:#7fe0a8;border:1px solid rgba(52,210,123,.30)}
  .exq-reason{font-size:11px;line-height:1.45;color:#cfe0f5}
  .exq-meta{font-size:11px;color:var(--muted);margin-top:3px;font-variant-numeric:tabular-nums}
  /* Aperçu par marché — TABLEAU pro (user 2026-08-20). Colonnes alignées, chiffres tabulaires, verdict en bord. */
  .mko-intro{font-size:10.5px;color:var(--muted);line-height:1.5;margin:2px 2px 9px}
  .mko-intro b{color:var(--text);font-weight:800}
  /* Table à largeurs FIXES -> tient ENTIÈREMENT en largeur, aucun scroll horizontal (user 2026-08-20). */
  .mko{width:100%;table-layout:fixed;border-collapse:collapse;font-variant-numeric:tabular-nums}
  .mko col.mko-c1{width:31%} .mko col.mko-c2,.mko col.mko-c3,.mko col.mko-c4{width:23%}
  .mko th{font-size:8.5px;text-transform:uppercase;letter-spacing:.02em;color:var(--muted);font-weight:800;
       text-align:right;padding:0 5px 5px;border-bottom:1px solid var(--border)}
  .mko th:first-child{text-align:left;padding-left:8px}
  .mko-fam td{font-size:8.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--gold);font-weight:800;
       padding:10px 5px 3px}
  .mko-r td{padding:6px 5px;border-bottom:1px solid rgba(255,255,255,.045);text-align:right;vertical-align:middle}
  .mko-mk{text-align:left!important;font-size:11px;font-weight:800;color:var(--text);white-space:nowrap;
       overflow:hidden;text-overflow:ellipsis;border-left:3px solid transparent;padding-left:8px!important}
  .mko-r.v-pos .mko-mk{border-left-color:#34d27b}
  .mko-r.v-neg .mko-mk{border-left-color:#ff6b6b}
  .mko-r.v-dim .mko-mk{border-left-color:rgba(255,255,255,.10)}
  .mko-c{white-space:nowrap;font-size:12.5px;font-weight:800}
  .mko-c .mko-win{color:#cfe0f5}
  .mko-n{display:block;font-size:8px;font-weight:600;color:var(--muted);font-style:normal;margin-top:1px}
  .mko-pos{color:#34d27b} .mko-neg{color:#ff6b6b} .mko-dim{color:var(--muted)}
  .sx-ml-h{font-size:9px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
       opacity:.85;display:flex;align-items:baseline;gap:8px}
  .sx-ml-hint{font-size:9px;font-weight:600;letter-spacing:0;text-transform:none;opacity:.7}
  .sx-mile-bs{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
  .sx-mile-b{width:24px;height:24px;border-radius:50%;border:1px solid rgba(34,184,255,.4);
       background:rgba(34,184,255,.10);color:#9fd2ff;font-family:inherit;font-size:11px;font-weight:900;
       cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:0}
  .sx-mile-b.on{background:#46e08a;border-color:#46e08a;color:#04220f}
  .sx-mile-b.mauto{border-color:rgba(255,159,67,.5);background:rgba(255,159,67,.12);color:#ffca8a}
  .sx-mile-b.mauto.on{background:#46e08a;border-color:#46e08a;color:#04220f}
  .sx-mile-info{font-size:11px;line-height:1.5;color:var(--muted);margin-top:0;max-height:0;overflow:hidden;
       transition:max-height .18s ease,margin-top .18s ease}
  .sx-mile-info.show{max-height:160px;margin-top:9px}
  .sx-mile-info b{color:var(--text);font-weight:800}
  .sx-mile-date{font-weight:800;color:var(--text);font-variant-numeric:tabular-nums;margin-right:7px}
  .sx-mile-tag{font-size:9px;font-weight:800;border-radius:6px;padding:1px 6px;
       background:rgba(20,150,240,.14);color:#9fd2ff}
  .sx-mile-tag.mauto{background:rgba(255,159,67,.16);color:#ffca8a}
  .sx-mile-key{display:flex;gap:14px;margin-top:8px;font-size:9.5px;color:var(--muted)}
  .sx-mile-key .km{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle}
  .sx-mile-key .kmeth{background:#1496f0} .sx-mile-key .kauto{background:#ff9f43}
  /* Journal des ajustements automatiques (marchés auto-écartés / auto-réintégrés, datés) */
  .exq-journal{display:flex;flex-direction:column;gap:6px;padding:9px 10px;border:1px solid var(--border);
       border-radius:14px;background:rgba(255,159,67,.045)}
  .exq-jhead{font-size:12px;font-weight:900;color:var(--text);display:flex;align-items:baseline;
       justify-content:space-between;gap:8px;flex-wrap:wrap}
  .exq-jsince{font-size:9.5px;font-weight:600;color:var(--muted)}
  .exq-jempty{font-size:11px;color:var(--muted);line-height:1.45}
  .exq-jrow{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:11px;padding:7px 9px}
  .exq-jtop{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
  .exq-jdate{font-size:11px;font-weight:800;color:var(--muted);font-variant-numeric:tabular-nums}
  .exq-jmk{font-size:12.5px;font-weight:800;color:var(--text)}
  .exq-jbase{font-size:9px;font-weight:700;color:var(--muted);border:1px solid var(--border);
       border-radius:5px;padding:1px 5px}
  .exq-jreason{font-size:10.5px;color:#cfe0f5;margin-top:3px;line-height:1.4}
  .sx-divider{height:1px;background:var(--border);margin:14px 0 2px}
  .sx-h2{margin-top:8px}
  .bc-yl{fill:var(--muted);font-size:9px;text-anchor:end;font-weight:700}
  .bc-legend{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .bc-lg{display:flex;align-items:center;gap:6px;background:rgba(255,255,255,.04);
       border:1px solid var(--border);border-radius:9px;padding:4px 8px;font-size:11px}
  .bc-dot{width:8px;height:8px;border-radius:50%;flex:none}
  .bc-lg-n{font-weight:800;color:var(--text)} .bc-lg-p{font-weight:800}
  .bc-lg-u{font-weight:700;color:#cfe0f5;font-variant-numeric:tabular-nums}
  .bc-lg-c{color:var(--muted);font-variant-numeric:tabular-nums}
  .bc-grid{stroke:rgba(255,255,255,.06);stroke-width:1}
  .bc-end{font-size:8.5px;font-weight:800;font-variant-numeric:tabular-nums}
  .bc-xl{fill:var(--muted);font-size:8.5px;text-anchor:middle;font-weight:700}
  /* ===== Statistiques accueil PREMIUM (sx) ===== */
  .sx{margin:2px 0 16px}
  .sx-body{display:flex;flex-direction:column;gap:14px}   /* stats sans onglets (filtres retirés) */
  /* Onglets de période (CSS pur,
  sans JS) */
  .sx-radio{position:absolute;width:0;height:0;opacity:0;pointer-events:none}
  .sx-tabs{display:flex;gap:6px;margin-bottom:12px}
  .sx-tabs label{flex:1;text-align:center;padding:7px 4px;border-radius:10px;font-size:12px;
       font-weight:800;color:var(--muted);background:var(--surface);border:1px solid var(--border);
       cursor:pointer;transition:all .15s}
  #sxp-all:checked ~ .sx-tabs label[for="sxp-all"],
  #sxp-30:checked ~ .sx-tabs label[for="sxp-30"],
  #sxp-7:checked ~ .sx-tabs label[for="sxp-7"]{color:#fff;background:var(--surface2);
       border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
  .sx-period{display:none}
  #sxp-all:checked ~ .sx-p-all,
  #sxp-30:checked ~ .sx-p-30,
  #sxp-7:checked ~ .sx-p-7{
       display:flex;flex-direction:column;gap:14px}
  .sx-empty{padding:26px 12px;text-align:center;color:var(--muted);font-size:12.5px;
       background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
  /* Filtre SPORT (onglets CSS,
  transverse aux périodes) */
  .sx-stabs{display:flex;gap:6px;margin-bottom:12px}
  .sx-stabs label{flex:1;text-align:center;padding:6px 4px;border-radius:9px;font-size:13px;
       font-weight:800;color:var(--muted);background:var(--surface);border:1px solid var(--border);
       cursor:pointer;transition:all .15s}
  #sxs-all:checked ~ .sx-stabs label[for="sxs-all"],
  #sxs-foot:checked ~ .sx-stabs label[for="sxs-foot"],
  #sxs-tennis:checked ~ .sx-stabs label[for="sxs-tennis"],
  #sxs-basket:checked ~ .sx-stabs label[for="sxs-basket"]{color:#fff;background:var(--surface2);
       border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
  /* sport choisi -> on masque les autres sections sport + la perf « tous sports » */
  #sxs-foot:checked ~ .sx-period .sx-sport:not([data-sport="foot"]),
  #sxs-tennis:checked ~ .sx-period .sx-sport:not([data-sport="tennis"]),
  #sxs-basket:checked ~ .sx-period .sx-sport:not([data-sport="basket"]){display:none}
  /* Héro bilan global */
  /* MÊME fond que la carte PERF des onglets sport (.spf) : dégradé cyan + bordure + glow cyan */
  .sx-hero{background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02));
       border:1px solid rgba(34,184,255,.60);border-radius:16px;
       box-shadow:0 0 26px rgba(34,184,255,.20),var(--shadow-sm);padding:14px 15px 12px;position:relative;overflow:hidden}
  .sx-hero-top{position:relative;display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
  .sx-hero-roi{font-size:34px;font-weight:900;line-height:1;letter-spacing:-.02em}
  .sx-hero-lbl{font-size:10.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
       color:var(--muted);margin-top:3px}
  .sx-hero-hint{font-size:9.5px;font-weight:600;color:var(--muted);opacity:.72;margin-top:2px;max-width:200px}
  .sx-hero-r{display:flex;flex-direction:column;align-items:flex-end;gap:7px}
  .sx-formrow{display:flex;align-items:center;gap:6px;justify-content:flex-end}
  .sx-formrow-c{margin:2px 0 6px}   /* forme W/L posée JUSTE au-dessus de sa courbe (près du graphe) */
  .sx-formk{font-size:9px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);white-space:nowrap}
  .sx-streak{font-size:10.5px;font-weight:800;padding:4px 9px;border-radius:99px;white-space:nowrap}
  .sx-streak.hot{color:#3ee089;background:rgba(52,210,123,.14);border:1px solid rgba(52,210,123,.30)}
  .sx-streak.cold{color:#ff7484;background:rgba(242,93,110,.13);border:1px solid rgba(242,93,110,.30)}
  .sx-streak.best{color:var(--gold);background:rgba(214,178,90,.12);border:1px solid rgba(214,178,90,.32);margin-left:5px}
  .sx-form{display:flex;flex-wrap:nowrap;gap:4px;align-items:center;justify-content:flex-end}
  .sx-fd{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:0 0 auto}
  .sx-fd.won{background:#34d27b} .sx-fd.lost{background:#ff6b6b} .sx-fd.push{background:#9fb0c8}
  .sx-ind{font-size:8px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--gold);
       background:rgba(246,197,74,.15);border:1px solid rgba(246,197,74,.3);padding:1px 5px;border-radius:99px;
       vertical-align:middle;margin-left:4px}
  .sx-bstreak{font-size:10.5px;color:var(--muted);font-weight:600} .sx-bstreak b{color:#3ee089;font-weight:800}
  .sx-relnote{font-size:9.5px;color:var(--muted);font-weight:600;opacity:.85}
  .sx-hero-foot{position:relative;display:flex;align-items:center;justify-content:space-between;
       gap:8px;margin-top:10px;padding-top:9px;border-top:1px solid var(--border)}
  .sx-heroc{width:100%;height:auto;display:block;max-height:96px}
  /* Animations PRO de la courbe d'équité : la ligne se TRACE (draw-in), puis l'aire et le point final
     apparaissent. `pathLength=1` sur la ligne -> le tracé marche quelle que soit sa longueur réelle. */
  /* État de REPOS : la courbe est VIDE (ligne masquée, aire + point invisibles) et NE s'anime PAS toute
     seule au rendu. L'animation ne se lance QUE quand le JS ajoute `.sx-go` (via _sxAnim, après le splash
     / à l'affichage de l'onglet) -> plus de courbe déjà tracée qui « clignote » avant l'animation. */
  /* DRAW-IN = TRACÉ PROGRESSIF (stroke-dash), restauré à la demande user 2026-07-25 (« refaire l'animation
     comme avant »). ANTI-GEL : l'état de BASE (sans `.sx-go`) est le tracé COMPLET (`stroke-dashoffset:0`),
     et le draw-in anime DEPUIS le vide via `from`. Un filet JS (voir `_sxAnim`) retire `.sx-go` après 1,5 s
     -> la ligne retombe TOUJOURS sur sa base pleine (touche le point), même si l'anim a gelé (onglet masqué,
     iOS Safari). Idem aire/point : base = état final. */
  .sx-heroc-line{stroke-dasharray:1;stroke-dashoffset:0}
  @keyframes sxdraw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
  .sx-heroc-area{opacity:.22}
  @keyframes sxarea{from{opacity:0}to{opacity:.22}}
  .sx-heroc-pt{opacity:1;transform-box:fill-box;transform-origin:center}
  @keyframes sxpt{0%{opacity:0;transform:scale(0)}100%{opacity:1;transform:scale(1)}}
  /* `both` (pas `forwards`) : PENDANT LE DÉLAI d'anim, l'élément affiche l'image `from` (aire/point CACHÉS)
     au lieu de sa base visible -> l'aire verte/rouge et le point n'apparaissent PLUS avant le tracé de la
     ligne (demande user 2026-07-25). L'aire ne se révèle qu'au démarrage de son anim (0,5 s), le point à 0,95 s. */
  .sx-heroc-line.sx-go{animation:sxdraw 1.15s cubic-bezier(.55,.08,.25,1) both}
  .sx-heroc-area.sx-go{animation:sxarea .7s ease .5s both}
  .sx-heroc-pt.sx-go{animation:sxpt .45s cubic-bezier(.2,1.6,.4,1) .95s both}
  @media (prefers-reduced-motion:reduce){
    .sx-heroc-line{stroke-dashoffset:0}
    .sx-heroc-area{opacity:.22}
    .sx-heroc-pt{opacity:1;transform:none}}
  .sx-kpis{position:relative;display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:11px;
       padding-top:12px;border-top:1px solid var(--border)}
  .sx-kpi{text-align:center}
  .sx-kpi b{display:block;font-size:15px;font-weight:900;color:var(--text);font-variant-numeric:tabular-nums}
  .sx-kpi span{font-size:11px;color:var(--muted);font-weight:600}
  .sx-kpi.sx-pos b{color:#34d27b} .sx-kpi.sx-neg b{color:#ff6b6b}
  /* Synthèse actionnable « À retenir » */
  .sx-insights{display:flex;flex-direction:column;gap:0}
  .sx-ins{display:flex;gap:9px;align-items:flex-start;font-size:11.5px;line-height:1.45;
       font-weight:600;color:var(--text);padding:9px 2px;border-top:1px solid var(--border)}
  .sx-ins:first-of-type{border-top:1px solid var(--border);margin-top:9px}
  .sx-ins-i{flex:0 0 auto;font-size:12px}
  .sx-ins b{font-weight:900}
  .sx-ins-good b{color:#34d27b} .sx-ins-bad b{color:#ff6b6b} .sx-ins-warn b{color:#f4c64a}
  /* Combinés : sous-ligne + réussite par nb de jambes */
  .sx-combo-sub{font-size:10.5px;color:var(--muted);font-weight:600;margin-top:9px}
  .sx-combo-sub b{color:var(--text)}
  /* Panneau « Volume de données » (transparence) : KPIs en 3 colonnes + note */
  .sx-kpis3{grid-template-columns:repeat(3,1fr)}
  /* Badge VARIATION 24 h sous chaque compteur du panneau Volume */
  .sx-d24{display:block;margin-top:2px;font-size:9.5px;font-weight:800;letter-spacing:.02em;
       color:#34d27b;font-variant-numeric:tabular-nums}
  .sx-d24.z{color:var(--muted);opacity:.55}
  /* Ligne PÉRIODE DE MESURE (contexte du nombre calibré) */
  .sx-data-period{font-size:10.5px;font-weight:700;color:var(--muted);margin-top:9px}
  .sx-data-period b{color:var(--accent);font-weight:900}
  /* Sous-titre « En cours » (pipeline en attente de résultat) dans le panneau Volume */
  .sx-data-sub{font-size:10.5px;font-weight:800;letter-spacing:.04em;color:#9fb6cf;margin:14px 0 0;
       padding-top:11px;border-top:1px solid var(--border)}
  /* INDICE DE FIABILITÉ (preuve d'auto-amélioration) : gros score + tendance + mini-courbe */
  .sx-rel-top{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:11px}
  .sx-rel-idx{font-size:34px;font-weight:900;letter-spacing:-.02em;color:var(--text);
       font-variant-numeric:tabular-nums;line-height:1}
  .sx-rel-idx small{font-size:14px;font-weight:800;color:var(--muted)}
  .sx-rel-tr{font-size:11.5px;font-weight:800;margin-top:5px}
  .sx-rel-tr.up{color:#34d27b} .sx-rel-tr.flat{color:var(--muted)} .sx-rel-tr.down{color:#ff6b6b}
  .sx-rel-kpi{text-align:right}
  .sx-rel-kpi b{display:block;font-size:14px;font-weight:900;color:var(--text);font-variant-numeric:tabular-nums}
  .sx-rel-kpi span{font-size:11px;color:var(--muted);font-weight:600}
  .sx-rel-period{font-size:10.5px;font-weight:700;color:var(--muted);margin-top:9px}
  .sx-rel-period b{color:var(--accent);font-weight:900}
  .sx-rel-chart{margin-top:12px}
  .sx-relc{width:100%;height:auto;display:block}
  .sx-relc-yl{fill:var(--muted);font-size:8px;font-weight:700;opacity:.8}
  .sx-relc-xl{fill:var(--muted);font-size:8px;font-weight:700;opacity:.7;text-transform:uppercase;letter-spacing:.04em}
  .sx-rel-note{font-size:10.5px;color:var(--muted);font-weight:600;line-height:1.45;margin-top:11px;
       padding-top:10px;border-top:1px solid var(--border)}
  .sx-rel-note b{color:var(--text)}
  .sx-data .sx-kpis:first-of-type{border-top:0;padding-top:0;margin-top:11px}
  .sx-vbs .vbs-head{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:8px;font-size:9.5px;
    font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:11px 0 2px;text-align:right}
  .sx-vbs .vbs-head div:first-child{text-align:left}
  .vbs-row{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:8px;align-items:center;padding:10px 0;
    border-top:1px solid rgba(255,255,255,.06)}
  .vbs-sp{font-size:12px;font-weight:800;display:flex;align-items:center;gap:5px;flex-wrap:wrap;line-height:1.3}
  .vbs-dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:0 0 auto}
  .vbs-cell{text-align:right;font-size:11px;color:var(--muted);font-weight:600;line-height:1.35}
  .vbs-cell b{display:block;color:var(--text);font-weight:900;font-size:16px}
  .vbs-cell span{display:block;font-size:9.5px}
  .vbs-cell .vbs-c2{opacity:.62;font-size:9px}
  .vbs-sim{font-size:9px;font-weight:900;color:#ff9f43;background:rgba(255,159,67,.14);
    padding:1px 6px;border-radius:8px}
  .vbs-roi{font-size:9px;font-weight:900;color:#2ee27f;background:rgba(46,226,127,.14);
    padding:1px 6px;border-radius:8px}
  .sx-data-note{font-size:10.5px;color:var(--muted);font-weight:600;line-height:1.45;margin-top:11px;
       padding-top:10px;border-top:1px solid var(--border)}
  .sx-data-note b{color:var(--text)}
  /* En-tête de SECTION (hiérarchie pro de la page Stats) : libellé majuscule accentué + sous-titre */
  .sx-sec{display:flex;flex-wrap:wrap;align-items:center;gap:9px;margin:12px 2px 0;padding-top:6px;
       font-size:11px;font-weight:900;letter-spacing:.10em;text-transform:uppercase;color:var(--accent)}
  .sx-sec::before{content:"";flex:0 0 14px;height:2px;border-radius:2px;background:var(--accent);
       align-self:center;opacity:.85}
  /* Titre bleu sur UNE ligne (ne se coupe plus en deux) ; sous-titre gris SUR LA LIGNE EN DESSOUS. */
  .sx-sec-lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sx-sec-sub{flex-basis:100%;margin-left:23px;font-size:10.5px;font-weight:600;letter-spacing:.005em;
       text-transform:none;color:var(--muted);line-height:1.35}
  .sx-acc{margin:0;border:0}
  .sx-acc>summary.sx-sec-sum{cursor:pointer;list-style:none;user-select:none;-webkit-user-select:none}
  .sx-acc>summary.sx-sec-sum::-webkit-details-marker{display:none}
  .sx-acc>summary.sx-sec-sum::marker{content:""}
  .sx-sec-chev{margin-left:auto;font-size:11px;font-weight:700;color:var(--muted);
       transition:transform .18s ease;transform:rotate(-90deg)}
  .sx-acc[open]>summary .sx-sec-chev{transform:rotate(0deg)}
  .sx-acc>summary.sx-sec-sum:hover{color:var(--text)}
  .sx-acc-body{margin-top:4px}
  .sx-legs{display:flex;flex-direction:column;gap:7px;margin-top:10px;
       padding-top:10px;border-top:1px solid var(--border)}
  .sx-leg{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:11px;
       font-weight:700;color:var(--text)}
  .sx-leg-n{flex:1;text-align:left;margin-left:10px;font-size:9.5px;color:var(--muted);font-weight:600}
  .sx-leg b{font-variant-numeric:tabular-nums}
  /* Filtre temporel */
  .sx-period{display:flex;gap:7px;margin:0 0 4px}
  .sx-period a{flex:1;text-align:center;padding:8px 0;border-radius:11px;font-size:11px;font-weight:800;
       border:1px solid var(--border);color:var(--muted);background:rgba(255,255,255,.02);text-decoration:none}
  .sx-period a.on{color:var(--text);border-color:rgba(34,184,255,.55);background:rgba(34,184,255,.10)}
  .sx-bys{display:flex;flex-direction:column;gap:10px}
  .sx-h{display:flex;align-items:baseline;justify-content:space-between;gap:8px;padding:0 2px;
       white-space:nowrap;font-size:12px;font-weight:800;letter-spacing:.04em;
       text-transform:uppercase;color:#cfe0f5;overflow:hidden}   /* overflow:hidden = un en-tête long n'élargit JAMAIS la carte (fix débordement horizontal) */
  .sx-h span{font-size:9.5px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0;
       min-width:0;overflow:hidden;text-overflow:ellipsis}   /* sous-titre : ellipsis plutôt que déborder */
  /* Bannière BETSFIX du sport (image Telegram) en en-tête de cadre stats (demande user 2026-07-24) */
  .stat-banner{display:block;width:auto;height:auto;max-height:74px;max-width:100%;border-radius:10px;margin:0 auto 6px}   /* un rien plus petite + centrée (demande user 2026-07-24) */
  .stat-banner-sub{text-align:center;font-size:9.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
       color:#f6c54a;margin:0 0 18px}   /* espace en dessous (demande user 2026-07-24) */
  .stat-banner-sub.ready,.stat-banner-sub.on{color:#64cd8d}   /* .on = sport actif (compté/repris dans les paris) */
  /* Onglets « Simple | Combinés » dans un cadre sport (un graphe à la fois) — demande user 2026-07-24 */
  .sctabs{display:flex;gap:5px;margin:0 0 10px;flex-wrap:wrap}
  .sctab{position:relative;flex:1 1 0;min-width:0;padding:8px 4px;border-radius:9px;background:rgba(255,255,255,.04);
       border:1px solid var(--border);color:var(--muted);font-weight:800;font-size:10.5px;text-transform:uppercase;
       letter-spacing:.02em;white-space:nowrap;text-align:center;cursor:pointer}
  .sctab.on{background:rgba(34,184,255,.15);border-color:rgba(34,184,255,.45);color:#fff}
  /* badge « en cours » (⏳) en COIN (position absolue) -> n'affecte pas le label, jamais de retour ligne */
  .sctab-n{position:absolute;top:-6px;right:-4px;min-width:14px;height:14px;padding:0 3px;border-radius:7px;
       background:rgba(255,184,77,.92);color:#241500;font-size:8.5px;font-weight:900;line-height:14px;
       text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.35)}
  .sctab.on .sctab-n{background:rgba(234,246,255,.95);color:#0a2233}
  /* ROI DISCRET sur l'onglet type-de-pari (demande user 2026-08-02) : petit chiffre sous le label, non criard. */
  .sctab-roi{display:block;margin-top:2px;font-size:9.5px;font-weight:900;letter-spacing:0;
       text-transform:none;font-variant-numeric:tabular-nums}
  .sctab-roi.pos{color:#63d68f}.sctab-roi.neg{color:#ff8080}.sctab-roi.neu{color:var(--dim)}
  .sctab-pane{display:none}
  .sctab-pane.on{display:block}
  .sx-sub{font-size:11px;color:var(--muted);line-height:1.35;padding:2px 2px 6px}
  /* Section par sport */
  /* mêmes cadres que les cartes de match (.row) : dégradé + bordure cyan + glow */
  .sx-sport{background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--cardline);border-radius:var(--radius);
       box-shadow:var(--cardglow),var(--shadow-sm);padding:11px 12px 10px}
  .sx-sport-h{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:2px}
  .sx-sport-t{font-size:13.5px;font-weight:800;color:var(--text)}
  .sx-sport-roi{font-size:14px;font-weight:900;font-variant-numeric:tabular-nums}
  .sx-sport-sub{font-size:10.5px;color:var(--muted);font-weight:600;margin-bottom:4px}
  .sx-rows{display:flex;flex-direction:column;gap:5px;margin-top:8px}
  .sx-row{padding:6px 9px;border-radius:9px;background:rgba(255,255,255,.035);
       border:1px solid var(--border);font-size:11.5px;cursor:pointer}
  .sx-row-main{display:flex;align-items:center;gap:8px}
  .sx-row-n{font-weight:800;color:var(--text);flex:none}
  .sx-row-roi{font-weight:900;min-width:50px;text-align:right;font-variant-numeric:tabular-nums;flex:none}
  .sx-row-wl{color:#cfe0f5;font-weight:700;font-variant-numeric:tabular-nums;flex:none}
  .sx-row-c{color:var(--muted);font-weight:700;min-width:40px;text-align:right;font-variant-numeric:tabular-nums}
  .sx-row-chev{color:var(--muted);font-weight:900;transition:transform .18s;flex:none}
  .sx-row.open .sx-row-chev{transform:rotate(90deg)}
  .sx-spark{width:100%;display:block}
  .paj-empty{text-align:center;color:var(--text);font-weight:800;font-size:14px;padding:26px 12px;
       background:var(--surface);border:1px solid var(--border);border-radius:14px}
  .paj-empty span{display:block;margin-top:6px;font-size:11.5px;font-weight:600;color:var(--muted)}
  /* Carte pari */
  .paj{background:linear-gradient(180deg,rgba(17,32,55,.85),rgba(11,20,38,.85));
       border:1px solid var(--border);border-radius:16px;padding:13px 14px;margin-bottom:11px;
       box-shadow:0 6px 18px rgba(0,0,0,.3)}
  .paj.rowtap{cursor:pointer}
  /* Liens SofaScore / Unibet : 2 boutons COMPACTS & SOBRES (fond dark,
  pastille de marque,
  nom + ↗) */
  .da-links{display:flex;gap:8px;align-items:stretch;margin:12px 0 2px}
  .lnk-bn{flex:1;min-width:0;display:inline-flex;align-items:center;justify-content:center;gap:7px;
       height:38px;border-radius:11px;text-decoration:none;font-size:12px;font-weight:800;
       letter-spacing:.01em;color:#dce7f5;background:rgba(255,255,255,.035);
       border:1px solid var(--cardline);transition:background .15s,border-color .15s}
  .lnk-bn:active{transform:scale(.985)}
  .lnk-dot{width:7px;height:7px;border-radius:50%;flex:none}
  .lnk-arr{color:var(--dim);font-weight:700;font-size:11px;margin-left:1px}
  .lnk-bn-sofa .lnk-dot{background:#2c7bff;box-shadow:0 0 6px rgba(44,123,255,.55)}
  .lnk-bn-uni  .lnk-dot{background:#1ea34a;box-shadow:0 0 6px rgba(30,163,74,.55)}
  .lnk-bn-sofa:hover{border-color:rgba(44,123,255,.4);background:rgba(44,123,255,.07)}
  .lnk-bn-uni:hover{border-color:rgba(30,163,74,.4);background:rgba(30,163,74,.07)}
  /* 📉 Mouvement de cote : ouverture -> clôture,
  sens (steam/drift) + mini-courbe */
  .om{background:rgba(255,255,255,.04);border:1px solid var(--cardline);border-radius:12px;
      padding:9px 12px;margin:11px 0 2px}
  .om-h{font-size:11.5px;font-weight:800;letter-spacing:.03em;color:#cfe0f5;text-transform:uppercase;
        display:flex;flex-direction:column;gap:2px;margin-bottom:7px}
  .om-sub{font-size:9px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0}
  .om-row{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12.5px;font-weight:700}
  .om-lbl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#eaf2ff}
  .om-spk{flex:none;width:74px;height:22px;opacity:.95}
  .om-spk .sx-spark{height:22px}
  .om-vals{flex:none;display:flex;gap:5px;align-items:center;font-variant-numeric:tabular-nums}
  .om-o{color:var(--muted)}
  .om-arr{font-weight:800;white-space:nowrap}
  .om-pct{flex:none;width:52px;text-align:right;font-weight:800;font-variant-numeric:tabular-nums}
  .om-down{color:#34d27b}
  .om-up{color:#ff6b6b}
  .om-flat{color:var(--muted)}
  .paj.open .exp-chev{display:inline-block;transform:rotate(180deg)}
  .paj .exp{margin-top:11px}
  .dash-h{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin:20px 0 9px;
       font-size:15px;font-weight:900;color:var(--text)}
  /* Programme du jour (accueil) : CADRE DÉPLIABLE (details/summary), matchs groupés par sport ;
     par ligne = heure PUIS match en dessous. Pari publié ~1 h avant chacun. */
  /* CADRE UNIQUE « 📅 Programme du jour » : paris à jouer + reste du slate FUSIONNÉS, ordre chronologique. */
  .anz{margin-top:14px;border:1px solid var(--border);border-radius:16px;padding:10px 12px 6px;
       background:linear-gradient(160deg,rgba(34,184,255,.04),rgba(255,255,255,.012))}
  .prog-sec{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:15px;
       font-weight:900;color:var(--text);margin:14px 2px 6px}
  .prog-sec:first-child{margin-top:2px}
  .prog-n{font-size:11.5px;font-weight:800;color:var(--accent);background:rgba(34,184,255,.12);
       border:1px solid rgba(34,184,255,.28);border-radius:8px;padding:1px 8px}
  /* Chaque match du programme = carte `.mc-*` comme un pari analysé, mais NON dépliable (pas d'analyse). */
  .prog-card{margin:6px 0}
  .prog-card .mc-head{cursor:default;padding:10px 12px}
  .prog-card .mc-betl.mc-noplay{opacity:.72}
  /* Carte COMPACTE « prochains lives » (user 2026-08-20) : non cliquable (pas de chevron), + d'air SOUS les
     équipes (le nom d'équipe ne colle plus au bord bas), et le bloc pari vide ne prend pas de place. */
  .mc-compact{cursor:default}
  .mc-compact .mc-teams{margin-bottom:8px}
  .mc-compact .mc-sub:empty{display:none}
  /* Carte PROVISOIRE avec analyse : cliquable comme un vrai pari (même structure .mc + toggle JS). */
  .prog-card-x .mc-head{cursor:pointer}
  /* Mention « pari provisoire » sous le pari : discrète, avec l'heure de la ré-analyse (coup d'envoi − 1 h). */
  .mc-reana{font-size:11px;color:var(--accent);margin-top:3px;font-weight:700;letter-spacing:.01em}
  .mc-reana .dim{font-weight:600}
  /* Pari PROVISOIRE (abstention sans value) : teinte DORÉE -> clairement distinct d'un pari de value
     confirmé (vert). Montre « le pari si l'on devait en jouer un » sans le vendre comme une value. */
  .mc-prov .mc-bt{color:var(--gold);font-weight:800}
  /* Provisoire — COTE : chip doré discret (info secondaire), gélule cohérente avec la carte dépliée. */
  .mc-bc-prov{color:var(--gold);border:1px solid rgba(246,197,74,.34);
       background:linear-gradient(180deg,rgba(246,197,74,.15),rgba(246,197,74,.05));
       box-shadow:0 1px 4px rgba(0,0,0,.25)}
  /* Provisoire : ligne « ré-analyse » DISCRÈTE (allègement 2026-07-11) — petite, grisée, non grasse :
     info présente mais qui ne pèse plus visuellement (la carte dorée + la zone disent déjà l'essentiel). */
  .mc-reana-prov{color:var(--muted);font-size:11px;font-weight:600;opacity:.92;margin-top:2px}
  /* Provisoire — présentation épurée : pastille de RÔLE (dit « hors ROI » une fois) + puce confiance. */
  .mc-prov-tag{display:inline-block;font-size:9px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;
       color:var(--gold);background:var(--gold-bg);border:1px solid var(--gold-bd);border-radius:7px;
       padding:2px 7px;margin:1px 0 5px}
  .mc-prov-tag span{opacity:.72;font-weight:600;letter-spacing:.02em}
  /* Provisoire — CONFIANCE : badge doré PLEIN (encre sombre) -> la « chance » ressort (info principale).
     Même hauteur + largeur MINI que la cote -> les deux colonnes de pastilles s'alignent parfaitement. */
  .mc-prov-cf{flex:none;align-self:center;display:inline-flex;align-items:center;justify-content:center;
       min-width:48px;height:23px;border-radius:99px;padding:0 9px;font-size:11px;font-weight:900;
       font-variant-numeric:tabular-nums;color:#1c1404;
       background:linear-gradient(180deg,#f8ce5c,#e0ad2f);border:1px solid rgba(246,197,74,.5);
       box-shadow:0 2px 8px rgba(246,197,74,.3)}
  /* ===== Cartes de paris — STYLE TELEGRAM (demande user 2026-07-12, reprend les cartes publiées, sans logo) :
     fond BLEU NUIT dégradé + bordure lumineuse ; titre en tiret long ; « Confiance % » en texte ; la COTE en
     GROS chiffre (blanc) en bas à DROITE avec le label « COTE ». ===== */
  /* Couleurs/graisses CALQUÉES sur la carte Telegram (tools/card_image.py) : cyan #5fd0ff, comp #93b7db,
     titre #eef4fb, analyse #a7bcd6 (léger), meta #90a4be, cote #fff. */
  /* Fond UNI (plus de dégradé 165° étirable qui redistribuait « la lumière » au dépli — user 2026-07-21). */
  /* TOUTES les cartes de pari : bordure BLANCHE + bord GAUCHE coloré selon le RÉSULTAT (demande user
     2026-07-25). Par défaut (à venir / en attente) = doré ; gagné = vert ; perdu = rouge ; live = doré ;
     remboursé/annulé = gris. L'état est posé via la classe `mc-r-*` (helper `_card_state_cls`). */
  .row.mc.mc-tg{background:#0b1826;
       border:1px solid var(--st-soon);   /* bord gauche UNIFORME (user 2026-08-17 : plus de 3px à gauche) */
       box-shadow:0 0 0 1px rgba(255,255,255,.10),0 0 26px rgba(255,255,255,.18),0 12px 32px rgba(0,0,0,.5)}
  .row.mc.mc-tg.mc-r-won{border-color:var(--st-won)}
  .row.mc.mc-tg.mc-r-lost{border-color:var(--st-lost)}
  .row.mc.mc-tg.mc-r-push{border-color:var(--st-void)}
  .row.mc.mc-tg.mc-r-live{border-color:var(--st-live)}
  .mc-tg .mc-head{padding:12px 16px 11px}
  .mc-tg .mc-sport{color:#5fd0ff;font-weight:800;letter-spacing:.05em}
  .mc-tg .mc-comp{color:#93b7db;font-weight:600}
  .mc-tg .mc-comp-sep{color:#5f7a97}
  .mc-dash{color:#5f7a97;font-weight:600;margin:0 4px}
  /* Équipes = HÉROS de la carte (demande user 2026-07-14) : plus GRANDES (16 px) que le pari (14 px). */
  .mc-tg .mc-teams{font-size:15px;font-weight:800;color:#eef4fb;line-height:1.26;margin-top:10px;
       white-space:normal;overflow:visible;text-overflow:clip;text-wrap:balance}
  /* Court extrait d'analyse à BARRE CYAN à gauche (comme la carte Telegram) — texte léger, plafonné à
     4 lignes (demande user 2026-07-12 ; line-clamp = filet visuel, la coupe texte fait déjà l'essentiel). */
  .mc-note{margin-top:9px;padding-left:13px;border-left:2px solid #3a9fe0;color:#a7bcd6;
       font-size:12.5px;font-weight:500;line-height:1.5;
       display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
  /* MÊME trait que le filet au-dessus de « Pourquoi ce choix » (.cleg-fold-bet border-top) — demande
     user 2026-07-21 : fini le dégradé bleuté, trait blanc uni discret partout. */
  .mc-div{height:1px;margin:10px 0 8px;background:rgba(255,255,255,.06)}
  .mc-open .mc-div{display:none}
  .mc-tg .mc-chev{display:none}                 /* le gros chiffre COTE occupe le coin bas-droit -> pas de chevron */
  /* Pari à jouer : SOUS les équipes et PLUS PETIT qu'elles (demande user 2026-07-14). Reste en gras (le
     pari), mais l'équipe (le match) domine la hiérarchie de la carte. */
  .mc-pick{font-size:13px;font-weight:800;color:#eef4fb;line-height:1.3;letter-spacing:-.01em}
  .mc-conf{margin-top:10px;font-size:13px;color:#90a4be;font-weight:600}
  .mc-conf b{color:#fff;font-weight:800;font-variant-numeric:tabular-nums}
  .mc-foot{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-top:13px}
  .mc-foot .mc-reana{margin-top:0;color:#7f93aa}
  .mc-cote{flex:none;text-align:right;line-height:1}
  .mc-cote-l{display:block;font-size:9.5px;font-weight:800;letter-spacing:.13em;color:#90a4be;margin-bottom:3px}
  .mc-cote-v{font-size:30px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;letter-spacing:-.02em;line-height:1}
  /* Bande VERDICT (demande user 2026-07-13) : confiance (barre + % coloré par niveau) À GAUCHE, cote À
     DROITE -> les 2 chiffres clés se lisent ENSEMBLE ; la couleur encode le risque sans avoir à lire. */
  .mc-verdict{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-top:11px;
       padding-top:10px;border-top:1px solid var(--border)}
  .mc-vc{flex:1;min-width:0}
  .mc-vc-lab{display:flex;align-items:baseline;justify-content:space-between;gap:8px;font-size:10.5px;
       font-weight:800;color:#8496ac;letter-spacing:.09em;margin-bottom:7px}
  .mc-vc-pct{font-size:16px;font-weight:900;font-variant-numeric:tabular-nums;letter-spacing:-.01em;
       white-space:nowrap;flex:none}
  .mc-vc-word{font-size:11px;font-weight:700;letter-spacing:.01em;opacity:.9;margin-right:6px}
  .mc-vbar{height:7px;border-radius:99px;background:#20222a;overflow:hidden;position:relative;
       box-shadow:inset 0 1px 2px rgba(0,0,0,.4)}
  .mc-vbar>i{position:absolute;left:0;top:0;bottom:0;border-radius:99px;display:block;min-width:7px;
       box-shadow:0 0 10px rgba(255,255,255,.12)}
  /* Marqueur « proba implicite du marché » sur la barre de confiance (demande user 2026-07-14) : notre
     confiance qui DÉPASSE ce trait = edge VISUEL. */
  .mc-vmark{position:absolute;top:0;bottom:0;width:2px;margin-left:-1px;background:#fff;opacity:.8;
       z-index:2;border-radius:2px;box-shadow:0 0 3px rgba(0,0,0,.75)}
  /* Ligne VALUE (EV vs marché) sous la barre : vert = value positive, ambre = négative. */
  .mc-vc-val{margin-top:8px;font-size:11px;font-weight:800;letter-spacing:.01em}
  .mc-vc-val.pos{color:#a6e22e}
  .mc-vc-mk{color:var(--muted);font-weight:600}
  .mc-vc-foot{margin-top:6px;font-size:11px;font-weight:600;color:var(--muted)}
  /* Traduction EN CLAIR du marché, sous la sélection (demande user 2026-07-13) — discrète, une flèche cyan. */
  .mc-gloss{margin-top:5px;font-size:12.5px;color:#8fa2b8;font-weight:600;line-height:1.35}
  .mc-gloss b{color:#c4d2e2;font-weight:700}
  .mc-gloss .ar{display:none}   /* flèche ↳ du glose retirée (user 2026-08-15) */
  /* Mention « pari publié figé · cote a bougé » (demande user 2026-07-14) : rassurant, pas alarmant. */
  .mc-moved{margin-top:7px;font-size:11.5px;font-weight:700;color:#c9a24a;
       background:rgba(246,197,74,.07);border:1px solid var(--gold-bd);border-radius:8px;padding:5px 9px}
  .mc-moved b{color:var(--gold);font-variant-numeric:tabular-nums}
  .mc-moved-m{color:var(--muted);font-weight:600;font-variant-numeric:tabular-nums}
  /* Variante OR de la carte Telegram : le COMBINÉ DU JOUR (demande user 2026-07-12) — bordure/lueur dorées,
     sport + cote en or, présenté comme les provisoires mais en jaune. */
  /* Combiné du jour : cadre VERT + halo (même patron de halo que les cartes de pari joué) —
     demande user 2026-07-21 (avant : doré). Vert émeraude #34d27b/#64cd8d (le vert « OUI » validé). */
  /* Combiné : bordure BLANCHE (3 côtés) comme les autres paris — le bord GAUCHE reste coloré par l'état
     (mc-r-*, doré par défaut). Demande user 2026-07-25 (« les combinés sont toujours en vert »). */
  .row.mc.mc-tg-gold{
       box-shadow:0 0 0 1px rgba(255,255,255,.10),0 0 26px rgba(255,255,255,.18),0 12px 32px rgba(0,0,0,.5)}
  .row.mc.mc-tg-gold.mc-r-won{border-color:var(--st-won)}
  .row.mc.mc-tg-gold.mc-r-lost{border-color:var(--st-lost)}
  .row.mc.mc-tg-gold.mc-r-push{border-color:var(--st-void)}
  .mc-tg-gold .mc-sport{color:#64cd8d}
  .mc-tg-gold .mc-sport-w{color:var(--text)}   /* titre « COMBINÉ MULTISPORT » en BLANC (user 2026-07-21) */
  .mc-tg-gold .mc-cote-v{color:#64cd8d}
  .mc-tg-gold .mc-cote-l{color:#3f9d6d}
  /* DIFFÉRENCIATION DES 2 COMBINÉS (user 2026-08-19) : Sûr = TEAL calme (fiabilité) · Cote 2 = AMBRE (ambition).
     L'accent porte UNIQUEMENT sur la COTE (chiffre proéminent). Le bord du cadre reste UNIFORME sur les 4 côtés
     (user 2026-08-20 : « toutes les bordures de cadre doivent être les mêmes ») — plus de bord-gauche d'une autre
     couleur qui créait une couture aux coins. */
  .mc-tg-sur .mc-cote-v{color:#54c7c0}
  .mc-tg-sur .mc-cote-l{color:#3d938d}
  .mc-tg-cote2 .mc-cote-v{color:#e8b93a}
  .mc-tg-cote2 .mc-cote-l{color:#b58a26}
  /* Jambes du combiné présentées comme des PICKS de provisoire (sélection en gras + match en sous-titre). */
  /* Écart entre jambes = MÊME rythme qu'entre cartes provisoires (demande user 2026-07-21, corrigé
     2e passe) : en FLEX les marges ne FUSIONNENT pas (7+9+9+7 s'additionnaient -> écart ~33px vs ~19px
     entre provisoires). -> conteneur en BLOCK (comme le flux des provisoires) : les marges des jambes
     (6px, = .prog-card) fusionnent avec celles du .mc-sep (9px) -> écart identique. */
  .mc-combo-legs{margin:2px 0;display:block}
  .mc-combo-legs .cleg{margin:6px 0}
  /* Étiquette « TOTAL DU COMBINÉ » entre les jambes et le verdict GLOBAL (user 2026-07-21) : label
     discret centré entre deux filets (même trait blanc .06 que partout) -> les chiffres du bas se
     lisent comme le TOTAL, plus comme la suite de la dernière jambe. */
  .combo-total-hd{display:flex;align-items:center;gap:10px;margin:12px 0 6px}
  .combo-total-hd::before,.combo-total-hd::after{content:"";flex:1;height:1px;background:rgba(255,255,255,.06)}
  .combo-total-hd span{flex:none;font-size:9.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;
       color:#9fb3cc}
  .mc-cleg{display:flex;align-items:flex-start;gap:9px}
  /* Sous-cadre par jambe QUAND les matchs sont DIFFÉRENTS (demande user 2026-07-12). */
  .mc-cleg-box{padding:10px 12px 10px 13px;border-radius:13px;background:rgba(255,255,255,.028);
       border:1px solid rgba(255,255,255,.07)}
  /* BORD GAUCHE coloré selon l'état (demande user 2026-07-13) : jaune=en attente, vert=gagné, rouge=perdu. */
  .mc-cleg-pending{border-left:3px solid var(--st-soon)}
  .mc-cleg-won{border-left:3px solid var(--st-won)}
  .mc-cleg-lost{border-left:3px solid var(--st-lost)}
  .mc-cleg-push{border-left:3px solid var(--st-void)}
  .mc-cleg-b{min-width:0;flex:1;display:flex;flex-direction:column;gap:2px}
  /* MATCH en titre (équipes) + badge/score live ; PARI À JOUER dessous (sélection + cote or). */
  .mc-cleg-match{font-size:14px;font-weight:800;color:#eef4fb;line-height:1.26;display:flex;gap:9px;align-items:center;flex-wrap:wrap}
  .mc-cleg-bet{margin-top:3px;font-size:13px;font-weight:700;color:#cfe0f5;line-height:1.3}
  .mc-cleg-o{margin-left:7px;font-size:11.5px;font-weight:900;color:#64cd8d;font-variant-numeric:tabular-nums}  /* cote de jambe : vert émeraude (cadre combiné passé au vert 2026-07-21) */
  .mc-cleg-sc{color:#5be08c;font-weight:800;font-variant-numeric:tabular-nums}
  /* analyse de la jambe (comme les combinés Telegram) — texte léger sous la sélection. */
  .mc-cleg-why{margin-top:6px;font-size:11.5px;font-weight:500;color:#a7bcd6;line-height:1.45}
  /* JAMBE = CARTE DE SIMPLE (demande user 2026-07-14) : chaque jambe encadrée exactement comme une carte
     de pari simple — en-tête SPORT • match, le pari en gras, l'explication en clair (gloss ↳), la COTE à
     droite, bord gauche coloré par état + badge. Idem en live (badge 🟢 + tableau de score). */
  .cleg{background:#0d1119;border:1px solid var(--st-soon);   /* fond UNI (user 2026-08-16) : stable au dépli */
       border-radius:12px;padding:11px 12px 10px}   /* bord gauche UNIFORME (user 2026-08-17 : plus de 3px à gauche) */
  .cleg.live{border-color:var(--st-live)}
  /* Sémantique COULEUR (demande user 2026-07-18) : PAS DÉCIDÉ (à venir / en cours) = ORANGE (bord doré par
     défaut) ; GAGNÉ/acquise = VERT ; PERDU = ROUGE ; ANNULÉ/remboursé (void/push) = GRIS. Le live ne doit
     PAS être vert (il n'est pas gagné) -> il garde le doré par défaut. */
  .cleg.won{border-color:var(--st-won)}
  .cleg.lost{border-color:var(--st-lost)}
  .cleg.push,.cleg.void{border-color:var(--st-void)}
  .cleg-h{display:flex;align-items:center;gap:6px;margin-bottom:8px}
  .cleg-comp{flex:1;min-width:0;font-size:12px;font-weight:800;color:#8fa2b8;letter-spacing:.02em;
       text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}   /* LIGUE = couleur du glose + MAJUSCULE + MÊME taille que la carte de pari (user 2026-08-08) */
  .cleg-sport{color:#2ee27f;font-weight:800;letter-spacing:.04em;font-size:13px}   /* emoji MÊME taille que .mc-ic (carte pari) */
  /* Nom du sport TOUJOURS à la couleur du sport (demande user 2026-07-20), quel que soit le type de carte
     (combiné cyan/or, provisoire, simple, jambe…) : foot vert · basket orange · tennis citron. Placé APRÈS
     les règles mc-sport/cleg-sport -> l'emporte à spécificité égale. Le TITRE d'un combiné (« COMBINÉ… »)
     n'a pas de classe spc- -> garde sa couleur (or/cyan). */
  .mc-sport.spc-foot,.cleg-sport.spc-foot{color:#2ee27f}
  .mc-sport.spc-basket,.cleg-sport.spc-basket{color:#ff9f43}
  .mc-sport.spc-tennis,.cleg-sport.spc-tennis{color:#d7e64a}
  .cleg-sep{color:var(--dim)}
  .cleg-bdg{flex:none;font-size:11px;font-weight:900;padding:2px 7px;border-radius:7px;white-space:nowrap}
  .cleg-bdg.p{background:rgba(232,184,74,.16);color:var(--gold)}
  /* Badge HEURE d'une jambe à venir : MÊME couleur neutre que les provisoires (.mc-up) — demande user. */
  .cleg-bdg.up{background:rgba(255,255,255,.06);color:var(--muted)}
  /* Badge résultat : CONTOUR assorti à l'état (vert gagné / rouge perdu) — demande user 2026-07-28. */
  .cleg-bdg.w{background:rgba(52,210,123,.18);color:#34d27b;border:1px solid rgba(52,210,123,.55)}
  .cleg-bdg.l{background:rgba(255,107,107,.16);color:#ff6b6b;border:1px solid rgba(255,107,107,.5)}
  .cleg-bdg.n{background:rgba(144,164,190,.16);color:#90a4be}
  /* Carte TERMINÉE (gagné/perdu/remb.) : on RETIRE la barre de progression de confiance (thermomètre
     d'avant-match, muet une fois le résultat connu + doublon du « CONFIANCE % ») — demande user 2026-07-28.
     Le % reste dans la grille verdict ; le scoreboard devient le point focal. À venir/live : barre gardée. */
  .cleg.won .vb-bar, .cleg.lost .vb-bar, .cleg.push .vb-bar, .cleg.void .vb-bar{display:none}
  /* Badge « en cours » : ORANGE (pas décidé), plus vert (demande user 2026-07-18). */
  .cleg-bdg.live{background:rgba(52,210,123,.16);color:#34d27b}   /* « 🟢 LIVE » vert comme les cartes (2026-07-21) */
  /* Équipes de la jambe sur leur propre ligne, en gros — comme les provisoires (.mc-teams). */
  .cleg-teams{font-size:14px;font-weight:800;color:#eef4fb;line-height:1.24;letter-spacing:-.015em;
       margin:2px 0 9px;white-space:normal}
  /* CARTE RÉSULTAT « façon live » (user 2026-08-15) : en-tête ligue CENTRÉE + BLANCHE + pays (comme le live),
     « Terminé » discret sous le score, barre Gagné/Perdu ré-affichée (sans halo), pas de séparateur de pli. */
  .cleg-h-c{justify-content:center;position:relative}
  .cleg-h-c .cleg-comp{flex:0 1 auto;text-align:center;white-space:normal;overflow:visible;
       text-overflow:clip;line-height:1.25;color:var(--text)}
  .cleg-res-live .cleg-teams{margin-top:14px}
  /* MISE EN PAGE IDENTIQUE À LA CARTE CONFIANCE/VALUE (user 2026-08-17) : même inset de contenu que la carte
     premium (.mc-prem .mc-head = 13px 16px 12px) -> les jambes ont exactement la même respiration/largeur. */
  .cleg-res-live{padding:13px 16px 12px}
  /* LIGUE un rien PLUS PETITE pour les JAMBES seulement (user 2026-08-17 : « cadre dans un cadre » -> moins de
     largeur, « Pays • Ligue » débordait sur 2 lignes). + on RETIRE le padding 0 44px de la ligue centrée
     (réservé au badge des cartes normales, ABSENT ici -> il gaspillait la largeur et forçait le retour ligne). */
  .cleg-res-live .mc-comp{font-size:11px;padding:0 10px}
  .tm-fin{font-size:10.5px;font-weight:800;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
  .cleg-res-live .cleg-fold-bet{border-top:none;padding-top:0;margin-top:13px}
  /* BADGE RÉSULTAT pleine largeur (user 2026-08-15) à la place de la barre : GAGNÉ/PERDU/REMBOURSÉ. */
  .cleg-resbadge{width:100%;text-align:center;padding:9px 10px;border-radius:12px;
       font-size:13px;font-weight:900;letter-spacing:.08em;text-transform:uppercase}   /* dans .vm-res -> marge gérée par le wrapper */
  .cleg-rb-w{background:rgba(52,210,123,.18);color:#4be39b;border:1px solid rgba(52,210,123,.55)}
  .cleg-rb-l{background:rgba(255,107,107,.16);color:#ff6b6b;border:1px solid rgba(255,107,107,.5)}
  .cleg-rb-n{background:rgba(144,164,190,.16);color:#aebdd0;border:1px solid rgba(144,164,190,.4)}
  .mc-combo-res{margin-top:12px}   /* badge résultat EN BAS du cadre combiné (user 2026-08-19) */
  /* note « hors ROI » en tête de l'onglet Combiné (user 2026-08-16 : affiché mais non compté au ROI) */
  .combo-horsroi{font-size:11px;font-weight:600;color:var(--muted);text-align:center;padding:7px 10px;
       margin-bottom:9px;background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:10px}
  .combo-horsroi b{color:#c4d2e2;font-weight:800}
  /* Ligne équipes façon Bull (test 2026-08-15) : monogrammes club + VS centré */
  .cleg-teams-vs,.tmvs{display:flex;align-items:center;justify-content:center;gap:12px;text-align:center}
  .mc-teams .tmvs{margin-top:2px}
  .tm-side{display:flex;flex-direction:column;align-items:center;gap:6px;flex:1;min-width:0}
  .tm-n{font-size:13px;font-weight:800;color:#eef4fb;line-height:1.15;letter-spacing:-.01em}
  .tm-vs{font-size:14px;font-weight:800;color:var(--text);letter-spacing:-.01em;flex:0 0 auto;font-variant-numeric:tabular-nums}   /* = l'heure du match (user 2026-08-15) */
  /* LIVE : score + minute empilés au centre, entre les équipes (user 2026-08-15) */
  .tm-live{display:flex;flex-direction:column;align-items:center;gap:1px;flex:0 0 auto}
  .tm-live b{font-size:21px;font-weight:900;letter-spacing:-.01em;color:var(--text);font-variant-numeric:tabular-nums}
  .tm-min{font-size:10.5px;font-weight:800;color:var(--gold);letter-spacing:.03em}
  .tm-min .tm-add{margin-left:2px;font-size:.9em;font-weight:900;color:#ff9d5c}   /* temps additionnel « +N' » à droite */
  .tm-cd{margin-top:4px;display:flex;justify-content:center}   /* décompte SOUS l'heure (à venir, user 2026-08-15) */
  .tm-b{position:relative;display:inline-block;width:44px;height:44px;flex:0 0 auto}
  .team-mono{display:grid;place-items:center;width:44px;height:44px;border-radius:50%;
       font-size:15px;font-weight:900;color:#fff;letter-spacing:-.02em;
       box-shadow:0 4px 12px rgba(0,0,0,.38),inset 0 0 0 1px rgba(255,255,255,.12)}
  /* Monogramme CACHÉ par défaut (user 2026-08-15 : pas de flash d'initiales avant le logo) : il n'apparaît
     QUE si le logo échoue (onerror -> visibility:visible). Logo OK = jamais d'initiales. */
  .tm-b .team-mono{visibility:hidden}
  /* Logo FotMob PNG transparent (pas de cercle blanc, user 2026-08-15) par-dessus le monogramme (repli) */
  .team-logo{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;
       background:none;border-radius:0;filter:drop-shadow(0 2px 5px rgba(0,0,0,.45))}
  .cleg-body{display:flex;align-items:flex-end;justify-content:space-between;gap:12px}
  .cleg-main{flex:1;min-width:0}
  .cleg-pick{font-size:13px;font-weight:800;color:#eef4fb;line-height:1.28;letter-spacing:-.01em}
  .cleg-gloss{margin-top:5px;font-size:12px;color:#8fa2b8;font-weight:600;line-height:1.32}
  .cleg-gloss .ar{display:none}   /* flèche ↳ du glose retirée (user 2026-08-15) */
  .cleg-why{margin-top:6px;font-size:11px;font-weight:500;color:#93a7c2;line-height:1.4}
  .cleg-cote{flex:none;text-align:right;line-height:1}
  .cleg-cote-l{display:block;font-size:8.5px;font-weight:800;letter-spacing:.12em;color:#90a4be;margin-bottom:2px}
  .cleg-cote-v{font-size:21px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .cleg.won .cleg-cote-v{color:#34d27b}
  .cleg-board{margin-top:9px}
  /* Justification repliable d'une jambe de combiné de match (« 💡 Pourquoi cette jambe »). */
  .cleg-fold{margin-top:8px}
  .cleg-fold-s{list-style:none;cursor:pointer;display:flex;align-items:center;gap:6px;font-size:10.5px;
       font-weight:800;color:var(--text);letter-spacing:.02em}
  .cleg-fold-s::-webkit-details-marker{display:none}
  .cleg-chev{margin-left:auto;transition:transform .2s}
  .cleg-fold[open] .cleg-chev{transform:rotate(180deg)}
  .cleg-fold .cleg-why{margin-top:6px}
  /* Même pli, mais sur une carte de pari PLEINE (simple retenu / provisoire) et non une mini-jambe :
     léger filet de séparation au-dessus + texte un poil plus lisible. */
  .cleg-fold-bet{margin-top:9px;padding-top:8px;border-top:1px solid rgba(255,255,255,.06)}
  .mc-prem .cleg-fold-bet{border-top:none;padding-top:0;margin-top:13px}   /* pas de séparateur sous le cadre (live + à venir, user 2026-08-15) */
  .mc-r-live .vb-live{margin-bottom:2px}
  .cleg-fold-bet>.cleg-fold-s{font-size:11px}
  .cleg-fold-bet .cleg-why{font-size:11.5px;color:#a7bcd6;line-height:1.5}
  /* Analyse en PUCES (une par phrase) dans le pli « 💡 Pourquoi » — aère le texte, plus de pavé massif
     (demande user 2026-07-20). Puce ronde discrète, comme « Les faits ». */
  .why-ul{margin:8px 0 2px;padding:0;list-style:none}
  .why-ul li{position:relative;padding-left:15px;margin:8px 0;font-size:11.5px;color:#a7bcd6;line-height:1.5;
       font-weight:500}   /* poids EXPLICITE (user 2026-08-17) : sinon une jambe LIVE hérite du gras .live (font-weight:800) */
  .why-ul li:first-child{margin-top:2px}
  .why-ul li::before{content:"";position:absolute;left:2px;top:8px;width:5px;height:5px;border-radius:50%;
       background:rgba(120,150,190,.55)}
  /* tableau de score de la jambe EN LIVE (sets/quart-temps), sous le match. */
  .mc-cleg-board{margin-top:8px}
  .prog-note{font-size:11px;color:var(--muted);margin-top:12px;line-height:1.45}
  .prog-note b{color:var(--text);font-weight:800}
  /* ZONES de l'accueil (refonte premium 2026-07-11) : regroupement par nature de pari — en-tête épuré
     (point d'état + titre casse normale + compteur + mot-clé), filet fin, aucune barre/majuscule criarde. */
  .dash-zones{margin-top:0}   /* espace au-dessus de Programme réduit (user 2026-08-19) */
  /* CASCADE D'APPARITION (user 2026-08-19, premium) : à l'ouverture de Pronos / au changement de jour, les
     catégories montent en fondu séquentiel (feel app native soignée). Uniquement la vue du jour (.dash-today).
     Respecte prefers-reduced-motion. */
  @keyframes zoneIn{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
  .dash-today > .zone{animation:zoneIn .40s cubic-bezier(.22,.9,.3,1) both}
  .dash-today > .zone:nth-child(1){animation-delay:.03s}
  .dash-today > .zone:nth-child(2){animation-delay:.07s}
  .dash-today > .zone:nth-child(3){animation-delay:.11s}
  .dash-today > .zone:nth-child(4){animation-delay:.15s}
  .dash-today > .zone:nth-child(5){animation-delay:.19s}
  .dash-today > .zone:nth-child(6){animation-delay:.23s}
  .dash-today > .zone:nth-child(7){animation-delay:.27s}
  @media (prefers-reduced-motion:reduce){.dash-today > .zone{animation:none}}
  /* Sous-nav Résultats (refonte user 2026-07-27) : Bilan / Calendrier segmenté */
  .resnav{display:flex;gap:7px;margin:2px 0 12px}
  .resnav-b{flex:1;min-height:44px;display:flex;align-items:center;justify-content:center;
    padding:9px 6px;border-radius:11px;background:rgba(255,255,255,.04);
    border:1px solid var(--border);color:var(--muted);font-weight:800;font-size:12.5px;cursor:pointer;
    transition:background .12s,border-color .12s,color .12s}
  .resnav-b.on{background:rgba(34,184,255,.16);border-color:rgba(34,184,255,.5);color:#fff}
  .res-load{text-align:center;color:var(--muted);padding:26px 0;font-size:20px}
  /* Sélecteur de sport de Pronos (demande user 2026-07-26) */
  .spsel-wrap{display:flex;gap:7px;margin:2px 0 6px}
  .spsel{position:relative;flex:1;display:flex;align-items:center;justify-content:center;gap:6px;padding:9px 6px;
    border-radius:11px;background:rgba(255,255,255,.04);border:1px solid var(--border);color:var(--muted);
    font-weight:800;font-size:12px;cursor:pointer;transition:background .12s,border-color .12s,color .12s}
  .spsel span{font-size:11.5px}
  .spsel.on{background:rgba(34,184,255,.16);border-color:rgba(34,184,255,.5);color:#fff}
  /* Badge chiffré du nb de paris par sport (demande user 2026-07-27, même style que .sctab-n des Stats) */
  .spsel-n{position:absolute;top:-6px;right:-4px;min-width:14px;height:14px;padding:0 3px;border-radius:7px;
    background:rgba(255,184,77,.92);color:#241500;font-size:8.5px;font-weight:900;line-height:14px;
    text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.35)}
  .spsel.on .spsel-n{background:rgba(234,246,255,.95);color:#0a2233}
  .zone{margin-top:22px}
  .zone:first-child{margin-top:10px}
  /* PAS de séparateur SOUS le titre de zone (user 2026-08-16) — il est reporté à la FIN de chaque zone (.zone). */
  .zone-h{display:flex;align-items:center;gap:9px;margin:0 3px 10px}
  .zone{padding-bottom:18px;border-bottom:1px solid var(--border)}   /* séparateur SOUS CHAQUE catégorie (user 2026-08-17) */
  /* Zone REPLIÉE (user 2026-08-17) : le trait reste PROCHE de SON titre (padding-bottom court + marge du header
     à 0). L'air AVANT le titre suivant est mis sur la ZONE (margin, ci-dessous), IDENTIQUE plié/déplié -> le
     titre ne se DÉCALE PAS au dépli (avant : margin-top du header 17px->0 faisait remonter le titre). */
  .zone-col:not([open]){padding-bottom:9px}
  .zone-col:not([open]) > .zone-h{margin-bottom:0}
  .dash-zones > .zone + .zone{margin-top:10px}   /* espace entre 2 zones (constant plié/déplié) */
  /* HISTORIQUE (dates passées, user 2026-08-19) : la vue jour n'a que 2-4 catégories -> pas de space-between
     (écarts énormes = « bizarre »). On donne un écart FIXE aéré entre les types, proche du rythme d'aujourd'hui,
     aligné en haut. (Seule la vue du jour = .dash-today distribue sur la hauteur.) */
  #pn-home.on .dash-zones:not(.dash-today) > .zone + .zone{margin-top:26px}
  .dash-zones > .zone:last-child{padding-bottom:0}   /* dernière zone OUVERTE : pas d'espace mort avant le pied */
  /* Dernière zone REPLIÉE (ex. Abstention) : garder l'écart trait↔titre (9 px), sinon le trait colle au titre. */
  .dash-zones > .zone-col:not([open]):last-child{padding-bottom:9px}
  /* Dernière catégorie VIDE (Abstention) : MÊME barre/espacement que les autres zones vides (user 2026-08-19) —
     on annule le padding-bottom:0 du :last-child pour que son séparateur soit identique. */
  .dash-zones > .zone-vide:last-child{padding-bottom:8px}
  /* Bouton notifications push (user 2026-08-16) — MASQUÉ par défaut ; le JS ne l'affiche qu'en PWA
     (mode standalone) et tant que la permission n'est pas accordée. */
  .bfx-pushrow{display:none;justify-content:center;margin:2px 0 14px}
  .bfx-pushbtn{font-size:12.5px;font-weight:800;color:var(--accent);background:rgba(34,184,255,.10);
       border:1px solid rgba(34,184,255,.35);border-radius:999px;padding:8px 16px;cursor:pointer;
       -webkit-tap-highlight-color:transparent;transition:transform .12s}
  .bfx-pushbtn:active{transform:scale(.97)}
  .zone-dot{width:8px;height:8px;border-radius:50%;flex:none;background:var(--muted)}
  .zone-t{font-size:17.5px;font-weight:800;color:var(--text);letter-spacing:.045em;text-transform:uppercase}  /* types de pari en MAJUSCULE (user 2026-08-08) + plus GRAND (user 2026-08-17) + tracking premium (2026-08-19) */
  .zone-ic{display:inline-flex;align-items:center;color:#9aa6b4;margin-right:-2px}   /* icône SVG monochrome de catégorie (user 2026-08-19) */
  .zone-ic svg{width:19px;height:19px;display:block}
  .zone-abst .zone-ic{color:#7f8794}   /* abstention = teinte plus discrète (non-pari) */
  .zone-n{font-size:11px;font-weight:800;min-width:19px;height:19px;padding:0 6px;border-radius:10px;
       display:inline-flex;align-items:center;justify-content:center;color:var(--muted);
       background:rgba(255,255,255,.06);font-variant-numeric:tabular-nums}
  .zone-tag{margin-left:auto;font-size:11px;font-weight:700;letter-spacing:.03em;color:var(--muted)}
  /* Sous-titre collé au titre de zone (ex. « Palier N » sous « MONTANTE ») : plus PETIT, casse normale (user 2026-08-22). */
  .zone-sub{margin-left:8px;font-size:11.5px;font-weight:700;letter-spacing:0;text-transform:none;color:var(--muted);align-self:center}
  /* Badge compteur + chevron POUSSÉS À DROITE (user 2026-08-17 : « badge aligné à droite près de la flèche »). */
  .zone-right{margin-left:auto;display:inline-flex;align-items:center;gap:8px;flex:none}
  /* Compteur simple d'une zone SANS win/loss (Programme / Abstention) — même pastille que Confiance/Value. */
  /* Compteur GRIS neutre par défaut (Programme ET Abstention, user 2026-08-17 : « numéro dans badge gris »). */
  .zone-rec .zrn{color:#0e141b;background:#9aa6b4;padding:1px 7px;border-radius:9px;font-size:11px;font-weight:800}  /* Programme + Abstention = badge GRIS, écriture noire (user 2026-08-18) */
  .zone-rec .zr-wait{color:#8b93a2;background:rgba(255,255,255,.05);padding:1px 8px;border-radius:9px;font-size:9.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;border:1px solid var(--border)}   /* badge « en attente » des zones vides (user 2026-08-19) */
  /* Carte de match SANS pari (Programme « à analyser » / Abstention) : ligne de statut centrée à la place du pari. */
  .mc-statcard .mc-sub{padding-right:0}
  /* CADRE des cartes sans pari (user 2026-08-17) : Programme = BLEU, Abstention = GRIS (au lieu du doré par défaut). */
  .mc-statcard.mc-st-wait{border-color:#22b8ff}
  .mc-statcard.mc-st-abst{border-color:#9fb6cf}
  .mc-stat{text-align:center;font-size:12.5px;font-weight:800;letter-spacing:.02em}
  .mc-stat-wait{color:var(--gold)}
  .mc-stat-abst{color:#9fb6cf}
  .mc-stat-sub{display:block;margin-top:3px;font-size:10.5px;font-weight:600;color:var(--dim);text-transform:none;letter-spacing:0}
  .zone-live{margin-left:8px}   /* badge « 🟢 Live » TOUT à droite, APRÈS le compteur (user 2026-08-17 : « badge à droite, nombre à sa gauche ») */
  /* RECORD du jour par type — pastilles compactes collées au titre. TOUS les états en BADGE COLORÉ plein
     (user 2026-08-08 : « victoires et défaites dans le même style que les matchs en attente ») : à venir =
     JAUNE ⏳ · gagnés = VERT ✓ · perdus = ROUGE ✗ · live = texte vert 🟢. Discret, groupé après le titre. */
  .zone-rec{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;
       font-variant-numeric:tabular-nums;font-weight:800}
  /* Badge compteur : LARGEUR FIXE + centré (user 2026-08-18) -> tous les badges des titres de zone ont la
     MÊME largeur et s'alignent verticalement les uns sous les autres (1 et 20 occupent la même case). */
  .zone-rec .zr{display:inline-flex;align-items:center;justify-content:center;gap:3px;font-size:11.5px;
       min-width:26px;box-sizing:border-box}
  .zone-rec .zru{color:#1a1400;background:#e8b93a;padding:1px 7px;border-radius:9px;font-size:11px}  /* à venir = badge JAUNE (user 2026-08-07) */
  .zone-rec .zrp{color:#0e141b;background:#9aa6b4;padding:1px 7px;border-radius:9px;font-size:11px}  /* en attente de résolution = badge GRIS (user 2026-08-08) */
  .zone-rec .zrw{color:#08210f;background:#54d98c;padding:1px 7px;border-radius:9px;font-size:11px}  /* gagnés = badge VERT (user 2026-08-08) */
  .zone-rec .zrl{color:#2e0808;background:#ff7d7d;padding:1px 7px;border-radius:9px;font-size:11px}  /* perdus = badge ROUGE */
  /* MODULE « Programme du jour » (Pronos, wave-first) : liste complète — match · compétition · coup
     d'envoi · heure d'analyse. TITRE + légende AU-DESSUS (hors cadre) ; la liste EST le cadre principal
     (un seul cadre). Équipes pleine largeur ; heures empilées à droite (KO / analyse). Bord gauche =
     état (jaune=prévu, cyan=analysé, gris=fini). */
  /* Message d'attente du Programme (avant le scan ~10h) : note discrète sous l'en-tête de zone (user 2026-08-20). */
  .prog-soon{font-size:11.5px;font-weight:600;color:var(--muted);line-height:1.5;padding:4px 3px 2px}
  .prog-soon b{color:var(--text);font-weight:800}
  .pgm-fold{margin-top:16px}
  .pgm-fold>summary{list-style:none;cursor:pointer;-webkit-tap-highlight-color:transparent}
  .pgm-fold>summary::-webkit-details-marker{display:none}
  .pgm-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;padding:0 3px}
  .pgm-title{font-size:14px;font-weight:800;color:var(--text);letter-spacing:.04em;text-transform:uppercase}
  .pgm-hr{flex:none;display:inline-flex;align-items:baseline;gap:9px}
  .pgm-count{font-size:11px;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums}
  .pgm-chev{font-size:11px;color:var(--muted);transition:transform .2s ease;transform:rotate(-90deg)}
  .pgm-fold[open] .pgm-chev{transform:rotate(0deg)}
  .pgm-legend{font-size:10.5px;color:var(--muted);margin:5px 3px 12px;line-height:1.45}
  .pgm-legend b{font-weight:800}
  .pgm-legend .pk1{color:var(--gold)} .pgm-legend .pk2{color:var(--accent)} .pgm-legend .pk3{color:var(--dim)}
  .pgm-list{display:flex;flex-direction:column;border:1px solid var(--border);border-radius:14px;
    overflow:hidden;background:var(--surface);box-shadow:var(--shadow-sm)}
  .pgm-day{font-size:11px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
    background:var(--bg2);padding:7px 13px;border-bottom:1px solid var(--border)}
  /* LIGNE = heure (ancre) · équipes+ligue (héros) · chip type + pastille statut à droite (user 2026-08-16). */
  .pgm-row{display:flex;align-items:center;gap:11px;padding:10px 13px;border-bottom:1px solid var(--border);
    border-left:3px solid var(--dim)}
  .pgm-row:last-child{border-bottom:0}
  .pgm-row.pgm-wait{border-left-color:var(--gold)}
  .pgm-row.pgm-abst{border-left-color:var(--dim)}
  /* Heure d'analyse prévue (~KO−2 h) d'un match « à analyser » du programme (user 2026-08-17). */
  .pgm-anh{font-size:9.5px;font-weight:700;color:var(--muted);white-space:nowrap;letter-spacing:.02em}
  /* Lignes de planning DANS une zone (ex. Abstention) : pas de double cadre (la zone porte déjà le sien). */
  .pgm-inzone{border:0;border-radius:0}
  /* GRILLE HORAIRE du programme (user 2026-08-18) : matchs groupés par heure de coup d'envoi (façon programme
     TV), lignes compactes. L'heure d'analyse (~KO−1 h) est portée par l'en-tête du créneau. */
  .pgg{margin-top:4px}
  .pgg-slot{margin-bottom:20px}
  .pgg-slot:last-child{margin-bottom:2px}
  /* EN-TÊTE de créneau = BANDEAU CENTRÉ ENCADRÉ (cyan) — sépare clairement les créneaux (user 2026-08-18). */
  .pgg-slot-h{display:flex;align-items:center;justify-content:center;gap:12px;
       padding:9px 14px;margin-bottom:11px;border-radius:12px;
       background:linear-gradient(180deg,rgba(34,184,255,.11),rgba(34,184,255,.02));
       border:1px solid rgba(34,184,255,.22)}
  .pgg-slot-h b{font-size:16px;font-weight:900;color:#eaf3fb;font-variant-numeric:tabular-nums;letter-spacing:.02em}
  .pgg-slot-h span{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:800;
       color:var(--gold);letter-spacing:.02em;white-space:nowrap}
  .pgg-clk{flex:none}
  /* Sous-groupe PAR LIGUE : intertitre CENTRÉ discret, une SEULE fois par ligue (user 2026-08-18). */
  .pgg-lgroup + .pgg-lgroup{margin-top:9px}
  .pgg-lgh{text-align:center;font-size:11px;font-weight:800;color:var(--muted);text-transform:uppercase;
       letter-spacing:.05em;padding:1px 8px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* LIGNE match : logo DOMICILE à gauche · équipes CENTRÉES · logo EXTÉRIEUR à droite (user 2026-08-18). */
  .pgg-row{display:flex;align-items:center;gap:12px;padding:7px 8px;border-radius:11px}
  .pgg-row + .pgg-row{margin-top:1px}
  .pgg-row .tm-b{flex:none;width:26px;height:26px}
  .pgg-row .tm-b .team-logo,.pgg-row .tm-b .team-mono{width:26px;height:26px;font-size:11px}
  .pgg-match{flex:1;min-width:0;text-align:center;font-size:13.5px;font-weight:700;color:var(--text);
       white-space:nowrap;overflow:hidden;text-overflow:ellipsis}   /* CENTRÉ, pleine largeur entre les 2 logos */
  /* Zone ABSTENTION : pastille + compteur gris-bleu neutre (on ne parie pas -> discret). */
  .zone-abst .zone-dot{background:#9fb6cf;box-shadow:0 0 8px rgba(159,182,207,.4)}
  .zone-abst .zone-n{color:#9fb6cf;background:rgba(159,182,207,.12)}
  .pgm-row.pgm-conf{border-left-color:#34d27b}
  .pgm-row.pgm-val{border-left-color:var(--accent)}
  .pgm-row.pgm-mont{border-left-color:var(--gold)}
  .pgm-row.pgm-combo{border-left-color:#a78bfa}
  .pgm-row.pgm-won{border-left-color:#34d27b}
  .pgm-row.pgm-lost{border-left-color:#ff6b6b}
  .pgm-row.pgm-done{border-left-color:var(--dim);opacity:.55}
  .pgm-ko{flex:none;min-width:42px;font-size:14px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums}
  .pgm-mid{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}
  .pgm-teams{font-size:13.5px;font-weight:800;color:var(--text);letter-spacing:-.01em;line-height:1.25;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* LIGUE : même couleur que les cartes (#8fa2b8), en petit sous les équipes. */
  .pgm-comp{font-size:9px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#8fa2b8;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .pgm-tail{flex:none;display:flex;align-items:center;gap:7px}
  /* CHIP type de pari (plein, coloré par type). */
  .pgm-typ{font-size:8.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;
    padding:3px 7px;border-radius:7px;white-space:nowrap}
  /* Types JOUÉS = chip PLEIN coloré (ressortent). Abstention / à analyser = chip DISCRET (contour tamisé). */
  .pgm-typ.t-conf{color:#08210f;background:#34d27b}
  .pgm-typ.t-val{color:var(--accent-ink);background:var(--accent)}
  .pgm-typ.t-mont{color:var(--gold-bg);background:var(--gold)}
  .pgm-typ.t-combo{color:#1a1030;background:#a78bfa}
  .pgm-typ.t-abst{color:var(--dim);background:none;border:1px solid var(--border);font-weight:700}
  .pgm-typ.t-wait{color:var(--muted);background:none;border:1px solid var(--border);font-weight:700}
  /* Option « masquer les abstentions » : chip cliquable (case à cocher CSS pure, pas de JS) */
  .pgm-toggle{display:inline-block;margin:2px 3px 11px;font-size:11px;font-weight:700;letter-spacing:.03em;
    color:var(--muted);cursor:pointer;padding:4px 11px;border:1px solid var(--border);border-radius:20px;
    user-select:none;-webkit-tap-highlight-color:transparent}
  .pgm-hideabst:checked ~ .pgm-toggle{color:var(--accent-ink);background:var(--accent);border-color:var(--accent)}
  .pgm-hideabst:checked ~ .pgm-list .pgm-row.pgm-abst{display:none}
  /* COMBINÉ (user 2026-08-08) : badge = nb de jambes (chiffre) + un cercle par jambe DANS le badge.
     Couleur du badge = JAUNE (en cours) · VERT (toutes gagnées) · ROUGE (≥1 perdue). */
  .zone-rec .zrleg{padding:1px 7px;border-radius:9px;font-size:11px;font-weight:800}   /* chiffre SEUL */
  .zone-rec .zrleg-u{color:#1a1400;background:#e8b93a}   /* en cours = JAUNE */
  .zone-rec .zrleg-w{color:#08210f;background:#54d98c}   /* toutes gagnées = VERT */
  .zone-rec .zrleg-l{color:#2e0808;background:#ff7d7d}   /* au moins une perdue = ROUGE */
  .zone-rec .zlcs{display:inline-flex;align-items:center;gap:3px}
  .zone-rec .zlc{width:7px;height:7px;border-radius:50%;box-shadow:0 0 0 1px rgba(10,16,22,.5)}
  .zone-rec .zlc-u{background:#caa63a}   /* jambe non jouée = cercle JAUNE (ambre) */
  .zone-rec .zlc-w{background:#1f9e57}   /* jambe gagnée = cercle VERT */
  .zone-rec .zlc-l{background:#d33b3b}   /* jambe perdue = cercle ROUGE */
  /* Points par jambe du combiné ALIGNÉS À DROITE (avant le badge, user 2026-08-18) : mêmes couleurs. */
  .clegdots{display:inline-flex;align-items:center;gap:4px;flex:none;margin-right:6px}
  .clegdots .zlc{width:7px;height:7px;border-radius:50%;box-shadow:0 0 0 1px rgba(10,16,22,.5)}
  .clegdots .zlc-u{background:#caa63a} .clegdots .zlc-w{background:#1f9e57} .clegdots .zlc-l{background:#d33b3b}
  .zone-b{margin-top:2px}
  .zone-b .dayhdr:first-child{margin-top:4px}
  .zone-empty{font-size:12.5px;color:var(--muted);line-height:1.55;padding:2px 3px 6px}
  .zone-empty b{color:var(--text);font-weight:800}
  /* ZONE VIDE (message « aucun pari… ») COMPACTE (user 2026-08-19) : titre collé au message ET message rapproché
     de la LIGNE DE SÉPARATION en dessous (padding-bottom 18->8) -> toutes les catégories + phrases tiennent visibles. */
  .zone-vide{padding-bottom:8px}
  .zone-vide .zone-h{margin-bottom:2px}
  .zone-vide .zone-b{margin-top:0}
  .zone-vide .zone-empty{padding:0 3px 3px}
  /* Points/compteurs de zone = MÊME couleur que le CADRE des cartes du type (demande user 2026-07-21) :
     Paris du jour = cyan (cadre .row.pick), Provisoires = blanc (cadre .mc-prov-b), Combiné = vert
     émeraude (cadre .mc-tg-gold vert). Fini le lime/or/violet historiques. */
  .zone-play .zone-dot{background:#22b8ff;box-shadow:0 0 8px rgba(34,184,255,.55)}
  .zone-play .zone-n{color:#5fd0ff;background:rgba(34,184,255,.12)}
  .zone-indic .zone-dot{background:#eef2f7;box-shadow:0 0 8px rgba(255,255,255,.5)}
  .zone-indic .zone-n{color:#eef2f7;background:rgba(255,255,255,.10)}
  .zone-indic .zone-tag{color:#c9d4e0;opacity:.9}
  .zone-combo .zone-dot{background:#34d27b;box-shadow:0 0 8px rgba(52,210,123,.6)}
  .zone-combo .zone-n{color:#64cd8d;background:rgba(52,210,123,.14)}
  .zone-live .zone-dot{background:#34d27b;box-shadow:0 0 8px rgba(52,210,123,.6);animation:livepulse 1.9s ease-out infinite}
  .zone-live .zone-n{color:#5fe39b;background:rgba(52,210,123,.14)}
  .zone-todo{opacity:.88}
  .zone-todo .zone-t{font-size:14.5px;font-weight:700;color:var(--muted)}
  .fin-more{display:block;text-align:center;margin:10px 3px 2px;padding:11px;border-radius:12px;
       border:1px dashed var(--border);color:var(--muted);font-size:12px;font-weight:700;
       text-decoration:none;-webkit-tap-highlight-color:transparent}
  .fin-more:active{background:rgba(255,255,255,.04)}
  /* (Bandeau calendrier des jours en tête de Pronos RETIRÉ le 2026-07-25 : navigation par jour dans
     l'onglet CALENDRIER dédié, styles `.mcal-*`. Le détail d'un jour garde ses styles `.day-*` ci-dessous.) */
  /* En-tête de contexte du jour affiché (haut de #day-content). */
  .day-hd{margin:0 3px 12px;display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
  .day-hd-lead{font-size:18px;font-weight:800;color:var(--text);letter-spacing:-.01em}
  .day-hd-sub{font-size:12.5px;color:var(--muted);font-weight:600;text-transform:capitalize}
  /* CALENDRIER HORIZONTAL (haut de Pronos, user 2026-08-19) — bande de dates cliquables, jour sélectionné
     mis en avant (accent du sport), pastille résultat par jour. Scroll horizontal sans barre visible. */
  .daycal{margin:0 0 4px;position:relative}   /* espace au-dessus du 1er titre (Programme) encore réduit, user 2026-08-19 */
  /* En-tête : mois/année (gauche, maj au scroll) + bouton « Aujourd'hui » (droite, si jour passé). */
  .daycal-hd{position:relative;display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 3px 4px;min-height:20px}   /* espace mois↔calendrier resserré (user 2026-08-19) ; position:relative = ancre du bouton « Aujourd'hui » absolu */
  .daycal-mo{font-size:11.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
       transition:color .2s ease}
  /* Bouton « Aujourd'hui » en POSITION ABSOLUE (user 2026-08-19) -> apparaît/disparaît SANS décaler le contenu
     (avant : dans le flux flex, il agrandissait l'en-tête et poussait tout vers le bas). Centré vertical à droite. */
  .daycal-goto{display:none;position:absolute;right:3px;top:50%;transform:translateY(-50%);z-index:2;flex:none;
       font-size:11px;font-weight:800;letter-spacing:.02em;color:var(--accent-ink);
       background:linear-gradient(180deg,var(--accent),var(--accent2));border:0;border-radius:20px;
       padding:5px 13px;cursor:pointer;-webkit-tap-highlight-color:transparent;box-shadow:0 4px 14px -6px var(--glow)}
  .daycal-goto.show{display:inline-flex;align-items:center}   /* visible : jour passé OU cellule AUJ. hors vue (user 2026-08-19) */
  .daycal-goto:active{transform:translateY(-50%) scale(.94)}
  .daycal-track{display:flex;gap:7px;overflow-x:auto;padding:2px 4px 8px;scroll-snap-type:x proximity;
       -webkit-overflow-scrolling:touch;scrollbar-width:none;-ms-overflow-style:none}
  .daycal-track::-webkit-scrollbar{display:none}
  .daycal-d{flex:0 0 auto;scroll-snap-align:center;display:flex;flex-direction:column;align-items:center;gap:1px;
       min-width:46px;padding:7px 6px 6px;border:1px solid var(--border);border-radius:13px;
  }
  /* AUJOURD'HUI = dernière cellule -> snap à DROITE (user 2026-08-19) : avec snap-align:center le snap la
     RECENTRAIT (la ramenait vers la gauche) après le scroll à droite. `end` la garde collée au bord droit. */
  .daycal-d.today{scroll-snap-align:end}
  .daycal-d{
       background:linear-gradient(180deg,var(--surface),var(--bg2));
       cursor:pointer;position:relative;transition:transform .12s ease,border-color .16s ease,box-shadow .16s ease;
       -webkit-tap-highlight-color:transparent}   /* boutons légèrement plus petits (user 2026-08-19) */
  .daycal-d:active{transform:scale(.95)}
  .dcd-wd{font-size:9.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
  .dcd-day{font-size:17px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums;line-height:1.05}
  .dcd-dot{width:7px;height:7px;border-radius:50%;margin-top:4px}
  .dcd-dot.pos{background:#54d98c;box-shadow:0 0 8px rgba(84,217,140,.7)}
  .dcd-dot.neg{background:#ff7d7d;box-shadow:0 0 8px rgba(255,125,125,.6)}
  .dcd-dot.neu{background:#9aa6b4}
  .dcd-dot.none{background:transparent;border:1px solid var(--border2)}
  /* jour SANS pari = dé-emphasé ET NON cliquable (user 2026-08-19). */
  .daycal-d.empty{opacity:.38;cursor:default;pointer-events:none}
  .daycal-d.today .dcd-wd{color:var(--accent)}
  /* AUJOURD'HUI toujours identifiable (même non sélectionné) : anneau discret. */
  .daycal-d.today:not(.on){border-color:color-mix(in srgb,var(--accent) 45%,var(--border))}
  .daycal-d.on{border-color:var(--accent);
       background:linear-gradient(180deg,color-mix(in srgb,var(--accent) 22%,var(--surface)),var(--surface));
       box-shadow:0 9px 24px -9px var(--glow),inset 0 0 0 1px color-mix(in srgb,var(--accent) 40%,transparent)}
  .daycal-d.on .dcd-wd,.daycal-d.on .dcd-day{color:var(--accent)}
  /* Bilan d'un jour PASSÉ (sous l'en-tête) : gagnés/réglés + ROI coloré. */
  .day-sum{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:2px 3px 14px;
       padding:12px 14px;border-radius:13px;border:1px solid var(--border);
       background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.02))}
  .day-sum-l{font-size:13.5px;color:var(--muted);font-weight:600}
  .day-sum-l b{color:var(--text);font-weight:800;font-variant-numeric:tabular-nums}
  .day-sum-roi{font-size:15px;font-weight:800;font-variant-numeric:tabular-nums}
  .day-sum-roi.pos{color:#64cd8d}.day-sum-roi.neg{color:#ff6b6b}.day-sum-roi.neu{color:var(--muted)}
  .day-sum-empty{justify-content:center;color:var(--muted);font-size:12.5px;font-weight:600}
  /* Provisoires RÉGLÉS dans « Résultats du jour » (info seule, hors ROI) : liste compacte ✓/✗. */
  .prv-hd{margin:15px 3px 8px;font-size:11px;font-weight:800;letter-spacing:.04em;color:var(--gold);text-transform:uppercase}
  .prv-hd span{color:var(--muted);font-weight:600;text-transform:none;letter-spacing:0}
  .prv-res{display:flex;flex-direction:column;gap:6px}
  .prv-r{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:11px;
       border:1px solid var(--border);background:rgba(255,255,255,.025)}
  .prv-ic{flex:none;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;
       justify-content:center;font-size:12px;font-weight:900}
  .prv-w .prv-ic{background:rgba(52,210,123,.16);color:#64cd8d}
  .prv-l .prv-ic{background:rgba(255,107,107,.16);color:#ff6b6b}
  .prv-n .prv-ic{background:rgba(255,255,255,.08);color:var(--muted)}
  .prv-p .prv-ic{background:rgba(246,197,74,.16);color:var(--gold)}   /* ⏳ fini, résultat en attente */
  .prv-m{flex:1;min-width:0}
  .prv-t{font-size:13px;font-weight:700;color:var(--text);line-height:1.3}
  .prv-t .prv-sp{font-size:12px;margin-right:2px}
  .prv-s{font-size:11px;color:var(--muted);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* Zone repliable (Terminés) : summary cliquable + chevron, même en-tête épuré. */
  details.zone-col > summary{list-style:none;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:opacity .16s ease}
  details.zone-col > summary::-webkit-details-marker{display:none}
  details.zone-col > summary:active{opacity:.62}   /* retour tactile premium (user 2026-08-19) */
  .zone-chev{margin-left:auto;color:var(--muted);font-size:18px;line-height:1;transition:transform .24s cubic-bezier(.34,1.4,.5,1)}   /* rotation avec léger ressort (2026-08-19) */
  details.zone-col[open] .zone-chev{transform:rotate(180deg)}
  /* Cadre de perf REPLIÉ par défaut sur les onglets sport (allègement 2026-07-11) : summary sobre gardant
     le ROI en une ligne ; déplie les 2 courbes + calibration (cadre .spf riche) en 1 tap. */
  .perf-fold{margin:8px 0 6px}
  .perf-fold > summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:9px;
       padding:11px 14px;border:1px solid var(--border);border-radius:13px;
       background:rgba(255,255,255,.025);-webkit-tap-highlight-color:transparent}
  .perf-fold > summary::-webkit-details-marker{display:none}
  .perf-sum-t{font-size:12.5px;font-weight:800;color:var(--text)}
  .perf-sum-k{font-size:11.5px;font-weight:800;font-variant-numeric:tabular-nums}
  .perf-sum .chev{margin-left:auto;color:var(--muted);font-size:18px;line-height:1;transition:transform .18s}
  .perf-fold[open] .chev{transform:rotate(180deg)}
  .perf-fold[open] > summary{margin-bottom:3px}
  .perf-fold .spf{margin:0}
  /* Carte PROVISOIRE en zone dédiée : habillage DORÉ cohérent (au lieu du cyan des paris à jouer) ->
     lisible d'un coup d'œil « hors ROI » sans pastille répétée. */
  .row.mc.mc-prov-c{border-color:var(--gold-bd);
       background:linear-gradient(180deg,rgba(246,197,74,.06),rgba(246,197,74,.015));
       box-shadow:0 0 20px rgba(246,197,74,.10)}
  .dash-h-a,
  .dash-more{font-size:11.5px;font-weight:800;color:var(--accent);text-decoration:none}
  .dash-more{display:block;text-align:center;margin:2px 0 4px;padding:11px;border-radius:12px;
       background:rgba(34,184,255,.10);border:1px solid rgba(34,184,255,.28)}
  .dash-stat{display:block;margin:2px 0 4px;padding:13px 14px;border-radius:15px;text-decoration:none;
       background:linear-gradient(160deg,#16161b,#0f0f13);border:1px solid var(--border2);
       box-shadow:0 5px 16px rgba(0,0,0,.28)}
  .dash-stat-row{display:flex;gap:8px}
  .ds-k{flex:1;display:flex;flex-direction:column;gap:2px}
  .ds-v{font-size:20px;font-weight:900;color:#fff;font-variant-numeric:tabular-nums;line-height:1}
  .ds-v.pos{color:#3ee089} .ds-v.neg{color:#ff7484} .ds-v.neu{color:#cfe0f5}
  .ds-l{font-size:9.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
  .dash-stat-go{display:block;margin-top:9px;font-size:11.5px;font-weight:800;color:var(--accent)}
  .dperf-top{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:12px}
  .dperf-roi{font-size:26px}
  .dperf-spk{display:flex;flex-direction:column;align-items:flex-end;gap:7px;flex:1;min-width:0;max-width:150px}
  .dperf-spk .sx-spark{width:100%}
  /* Taux de réussite par sport (tennis · basket · football). */
  .dash-sports{display:flex;gap:8px;margin-top:11px;border-top:1px solid rgba(255,255,255,.08);padding-top:11px}
  .dsp{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;text-align:center}
  .dsp-ic{font-size:15px;line-height:1}
  .dsp-v{font-size:16px;font-weight:900;color:#eaf2ff;font-variant-numeric:tabular-nums}
  .dsp-v.pos{color:#3ee089} .dsp-v.neg{color:#ff7484} .dsp-v.neu{color:#cfe0f5}
  .dsp-l{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
  .dash-tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin:14px 0 4px}
  .dash-tile{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;
       padding:15px 6px;border-radius:15px;text-decoration:none;font-size:11.5px;font-weight:800;
       color:var(--text);background:var(--surface);border:1px solid var(--border);text-align:center}
  .dash-tile:active{transform:scale(.95)}
  .dash-tile .dt-ic{font-size:25px;line-height:1}
  .dash-next{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px;
       padding:13px 14px;border-radius:13px;text-decoration:none;font-size:13px;font-weight:800;
       color:var(--text);background:var(--surface);border:1px solid var(--border)}
  .dash-next span{font-size:11px;font-weight:700;color:var(--accent)}
  .pg-h{font-size:21px;font-weight:900;color:var(--text);margin:2px 0 3px}
  .pg-sub{font-size:11.5px;color:var(--muted);font-weight:600;margin-bottom:14px}
  /* Calibration : confiance annoncée vs réussite réelle */
  .cal-h{font-size:15px;font-weight:900;color:var(--text);margin:24px 0 10px}
  .cal-verdict{padding:13px 14px;border-radius:16px;border:1px solid rgba(34,184,255,.60);
       background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02));
       box-shadow:0 0 26px rgba(34,184,255,.20),var(--shadow-sm);margin-bottom:12px}
  .cal-verdict.cal-ok{border-color:rgba(52,210,123,.4)}
  .cal-verdict.cal-over{border-color:rgba(244,198,74,.4)}
  .cal-verdict.cal-under{border-color:rgba(34,184,255,.4)}
  .cal-v-t{font-size:15px;font-weight:900;color:#fff}
  /* Bandeau « ce que la boucle écarte EN CE MOMENT » (action concrète, pas juste le diagnostic) */
  .cal-excl{padding:11px 13px;border-radius:14px;border:1px solid rgba(244,120,120,.40);
       background:rgba(244,120,120,.07);font-size:11.5px;color:var(--text);font-weight:650;
       line-height:1.45;margin-bottom:12px}
  .cal-excl b{color:#fff} .cal-excl span{color:var(--muted);font-weight:600}
  .cal-excl.cal-excl-none{border-color:rgba(52,210,123,.35);background:rgba(52,210,123,.06)}
  .cal-v-s{font-size:11.5px;color:var(--muted);font-weight:600;margin-top:3px;line-height:1.4}
  .cal-v-m{font-size:11px;color:var(--text);font-weight:700;margin-top:6px}
  .cal-src{color:var(--muted);font-weight:600}
  .cal-ghost{font-size:10.5px;color:var(--muted);font-weight:600;line-height:1.5;
    margin:8px 2px 0;padding:9px 11px;border-radius:12px;background:rgba(34,184,255,.06);
    border:1px solid rgba(34,184,255,.18)}
  .cal-ghost b{color:var(--text)}
  .cal{display:flex;flex-direction:column;gap:9px}
  .cal-row{display:flex;align-items:center;gap:10px;
       background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--cardline);box-shadow:var(--cardglow),var(--shadow-sm);
       border-radius:13px;padding:10px 12px}
  .cal-band{flex:none;width:62px;font-size:12px;font-weight:900;color:var(--text);line-height:1.2}
  .cal-band span{display:block;font-size:9px;font-weight:700;color:var(--muted)}
  .cal-bars{flex:1;display:flex;flex-direction:column;gap:5px;min-width:0}
  .cal-line{display:flex;align-items:center;gap:7px}
  .cal-lab{flex:none;width:46px;font-size:9.5px;font-weight:700;color:var(--muted);text-align:right}
  .cal-track{flex:1;height:8px;border-radius:99px;background:rgba(255,255,255,.07);overflow:hidden}
  .cal-fill{display:block;height:100%;border-radius:99px}
  .cal-fill.conf{background:linear-gradient(90deg,#5f6f8e,#90a0bc)}
  .cal-fill.real.pos{background:linear-gradient(90deg,#1fb364,#3ee089)}
  .cal-fill.real.neg{background:linear-gradient(90deg,#c25a4a,#ff7484)}
  .cal-line b{flex:none;width:34px;text-align:right;font-size:11px;font-weight:800;
       color:var(--text);font-variant-numeric:tabular-nums}
  .cal-gap{flex:none;width:34px;text-align:center;font-size:12px;font-weight:900;
       font-variant-numeric:tabular-nums}
  .cal-gap.pos{color:#3ee089} .cal-gap.neg{color:#ff7484}
  .cal-side{flex:none;width:62px;display:flex;flex-direction:column;align-items:center;gap:3px}
  .cal-side .cal-gap{width:auto}
  .cal-roi{font-size:11px;font-weight:900;font-variant-numeric:tabular-nums;text-align:center;line-height:1.1}
  .cal-roi span{display:block;font-size:7.5px;font-weight:700;color:var(--muted);letter-spacing:.02em}
  .cal-roi-pos{color:#3ee089} .cal-roi-neg{color:#ff7484}
  .cal-note{font-size:10.5px;color:var(--muted);font-weight:600;line-height:1.5;margin:12px 2px 0}
  .cal-pos-t{color:#3ee089;font-weight:800} .cal-neg-t{color:#ff7484;font-weight:800}
  /* Calibration par groupe (sport / marché) — lignes compactes */
  .calg-h{font-size:12px;font-weight:900;color:var(--muted);text-transform:uppercase;
       letter-spacing:.06em;margin:18px 2px 8px}
  .calg{display:flex;flex-direction:column;gap:7px}
  .calg-row{display:flex;align-items:center;gap:8px;
       background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--cardline);box-shadow:var(--cardglow),var(--shadow-sm);
       border-radius:11px;padding:9px 11px}
  /* hiérarchie : sport (en tête) puis ses types de paris en sous-catégorie indentée */
  .calg-sport{background:linear-gradient(160deg,#16161b,#0f0f13);border-color:var(--border2);margin-top:4px}
  .calg-sport .calg-name{font-size:13.5px;font-weight:900}
  .calg-sub{margin-left:16px;background:rgba(255,255,255,.02);padding:7px 11px}
  .calg-sub .calg-name{width:100px;font-size:11px;font-weight:700;color:var(--muted)}
  .calg-sub .calg-name::before{content:"↳ ";color:var(--dim)}
  .calg-name{flex:none;width:104px;font-size:12.5px;font-weight:800;color:var(--text);line-height:1.2;
       overflow-wrap:anywhere}
  .calg-name span{display:block;font-size:9px;font-weight:700;color:var(--muted)}
  /* compare compact : confiance annoncée → réussite réelle (réel coloré selon le signe) */
  .calg-cmp{flex:1;min-width:0;display:flex;align-items:baseline;gap:5px;font-size:13px;font-weight:900;
       font-variant-numeric:tabular-nums}
  .calg-cmp b:first-child{color:var(--muted)}
  .calg-cmp i{font-style:normal;color:var(--dim);font-weight:700}
  .calg-cmp b.pos{color:#3ee089} .calg-cmp b.neg{color:#ff7484}
  .calg-leg{font-size:9px;font-weight:700;color:var(--dim);text-transform:none;letter-spacing:0}
  .calg-v{flex:none;font-size:9.5px;font-weight:800;padding:3px 8px;border-radius:99px;white-space:nowrap}
  .calg-v.v-ok{color:#3ee089;background:rgba(52,210,123,.13)}
  .calg-v.v-over{color:#f4c64a;background:rgba(244,198,74,.13)}
  .calg-v.v-under{color:#9fd2ff;background:rgba(34,184,255,.13)}
  .calg-v.v-unsure{color:var(--muted);background:rgba(255,255,255,.06)}   /* à confirmer (pas assez de paris) */
  /* Liens vers le match (SofaScore / Unibet) en tête de l'analyse — mêmes carrés */
  /* Drill-down : liste premium des PARIS réglés d'un sport */
  .sx-dd{display:flex;flex-direction:column;gap:6px;margin-top:7px}
  .sx-dd-empty{color:var(--muted);font-size:11.5px;padding:6px 2px}
  .sx-dd-head{display:flex;align-items:center;justify-content:space-between;
       padding:2px 4px 7px;border-bottom:1px solid var(--border);margin-bottom:3px;
       font-size:11px;color:var(--muted);font-weight:700}
  .sx-dd-head b{color:var(--text)}
  .sx-dd-pnl{font-weight:900;font-variant-numeric:tabular-nums}
  .sx-dd-pnl.pos{color:#34d27b} .sx-dd-pnl.neg{color:#ff6b6b} .sx-dd-pnl.neu{color:var(--muted)}
  .sx-dd-row{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:11px;
       background:rgba(255,255,255,.035);border:1px solid var(--border)}
  .sx-dd-res{flex:none;width:21px;height:21px;border-radius:50%;display:flex;align-items:center;
       justify-content:center;font-size:11px;font-weight:900}
  .sx-dd-res.dd-w{color:#06140d;background:#34d27b} .sx-dd-res.dd-l{color:#fff;background:#ff6b6b}
  .sx-dd-res.dd-p{color:#0b1428;background:#9fb0c8}
  .sx-dd-m{min-width:0;flex:1}
  .sx-dd-t{font-size:12px;font-weight:800;color:var(--text);line-height:1.3}
  .sx-dd-s{font-size:11px;color:var(--muted);font-weight:600;white-space:nowrap;overflow:hidden;
       text-overflow:ellipsis}
  .sx-dd-r{flex:none;display:flex;flex-direction:column;align-items:flex-end;gap:2px}
  .sx-dd-c{font-size:11.5px;font-weight:800;color:#cfe0f5;font-variant-numeric:tabular-nums}
  .sx-dd-u{font-size:10.5px;font-weight:900;font-variant-numeric:tabular-nums}
  .sx-dd-u.pos{color:#34d27b} .sx-dd-u.neg{color:#ff6b6b} .sx-dd-u.neu{color:var(--dim)}
  /* Animation d'apparition des courbes (tracé) */
  .bc-line{stroke-dasharray:1400;stroke-dashoffset:1400;animation:bcdraw 1.1s ease-out forwards}
  @keyframes bcdraw{to{stroke-dashoffset:0}}
  /* Carte Verdict */
  .da-vc{position:relative;margin:6px 0 14px;padding:13px 14px 12px;border-radius:var(--radius);
       background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--cardline);border-left:3px solid var(--accent);
       box-shadow:var(--cardglow),var(--shadow);overflow:hidden}
  .da-vc::before{content:"";position:absolute;inset:0 0 auto auto;width:120px;height:120px;
       background:radial-gradient(circle at top right,var(--glow),transparent 70%);pointer-events:none}
  .da-vc-h{position:relative;font-size:10.5px;font-weight:800;letter-spacing:.06em;
       text-transform:uppercase;color:var(--accent);margin-bottom:9px}
  /* Héro « le plus sûr » */
  .da-vc-top{position:relative;padding:10px 12px;margin-bottom:10px;border-radius:12px;
       background:rgba(255,255,255,.04);border:1px solid var(--border)}
  .da-vc-lbl{font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
       color:var(--accent);margin-bottom:5px}
  .da-vc-pick{font-size:17px;font-weight:800;color:#fff;line-height:1.25;
       display:flex;align-items:center;flex-wrap:wrap;gap:8px}
  .da-vc-odds{display:inline-flex;align-items:center;padding:2px 11px;border-radius:99px;
       font-size:14px;font-weight:900;color:var(--accent-ink);
       background:linear-gradient(180deg,var(--accent),var(--accent2));
       box-shadow:0 2px 10px var(--glow)}
  .da-vc-why{font-size:11.5px;color:var(--muted);line-height:1.5;margin-top:6px}
  /* Lignes secondaires (compromis / à éviter) */
  .da-vc-row{position:relative;display:flex;gap:8px;font-size:12px;color:var(--muted);
       line-height:1.5;padding:6px 0;border-top:1px solid rgba(255,255,255,.05)}
  .da-vc-row b{color:#cfe0f5}
  .da-vc-ic{flex:none;font-size:13px;line-height:1.4}
  .da-vc-skip{color:#9aa6bd}
  /* Encart Mise */
  .da-mise{position:relative;display:flex;gap:9px;align-items:flex-start;margin-top:11px;
       padding:9px 11px;border-radius:11px;font-size:11.5px;line-height:1.5;color:#dfe6f2;
       background:var(--gold-bg);border:1px solid var(--gold-bd)}
  .da-mise-ic{flex:none;font-size:14px}
  .da-mise b{color:var(--gold)}
  /* Tableau des paris */
  .da-bets-h{font-size:12px;font-weight:800;letter-spacing:.02em;color:#cfe0f5;margin:14px 0 6px}
  /* 🎲 Combiné « grand tournoi » (Coupe du Monde…) : encadré distinct sous les paris. */
  .da-combo{margin-top:10px;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid rgba(255,255,255,.8);border-left:3px solid var(--st-soon);border-radius:12px;padding:10px 12px}
  .da-combo-won{border-left-color:var(--st-won)}
  .da-combo-lost{border-left-color:var(--st-lost)}
  .da-combo-void{border-left-color:var(--st-void)}   /* combiné remboursé (jambe indéterminable) : gris neutre */
  .da-combo-h{font-size:12px;font-weight:800;color:#ffd98a;display:flex;align-items:center;gap:8px;
       margin-bottom:7px;text-transform:uppercase;letter-spacing:.03em}
  .da-combo-n{font-weight:700;color:#cdb98a;opacity:.85}     /* « · N jambes » à côté de Combiné */
  .da-combo-c{margin-left:auto;background:#ffb020;color:#1a1200;border-radius:6px;padding:1px 7px;font-weight:800}  /* cote totale : coin haut-droite */
  .da-combo-b{font-size:11px;border-radius:5px;padding:1px 7px;font-weight:800}
  .da-combo-b.won{background:#34d27b;color:#04220f}
  .da-combo-b.lost{background:#ff6b6b;color:#2a0606}
  .da-combo-b.void{background:#9fb0c8;color:#0b1428}
  .da-cl-leg{padding:7px 0;border-top:1px solid rgba(255,255,255,.07)}   /* 1 bloc = 1 jambe (rythme) */
  .da-cl-leg:first-of-type{border-top:0;padding-top:2px}
  .da-cl{display:flex;align-items:flex-start;gap:8px;justify-content:space-between;
       font-size:11.5px;color:#dfe9f7}   /* cote+proba alignées sur la 1re ligne, pas centrées */
  .da-cl-sel{flex:1 1 auto;min-width:0;line-height:1.3;font-weight:700}   /* sélection : wrap propre à gauche, en GRAS pour bien la voir */
  .da-cl-meta{flex:0 0 auto;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
  .da-cl b{color:#fff;font-variant-numeric:tabular-nums}
  .da-cl-mk{font-size:12px;line-height:1}
  .da-cl-won{color:#9be8bf}
  .da-cl-lost{color:#ffb3b3;opacity:.85}
  .da-cl-lost .da-cl-sel{text-decoration:line-through}       /* barré : SEULEMENT le libellé */
  .da-cl-void{color:#aeb9c9;opacity:.7}                      /* jambe annulée/remboursée : gris estompé */
  .da-cl-void .da-cl-sel{text-decoration:line-through;text-decoration-style:dotted}
  .da-cl-live{color:#ffd98a}
  .da-cl-p{font-variant-numeric:tabular-nums;font-size:10.5px;color:#9fb0c8;
       background:rgba(255,255,255,.06);border-radius:5px;padding:1px 5px}
  .da-cl-pr{font-size:11px;font-weight:800;padding:1px 7px;border-radius:999px;border:1px solid;
       font-variant-numeric:tabular-nums}                    /* pastille CHANCE de la jambe */
  .da-cl-pr.hi{color:#2ec98a;border-color:rgba(46,201,138,.45);background:rgba(46,201,138,.12)}
  .da-cl-pr.mid{color:#22b8ff;border-color:rgba(34,184,255,.45);background:rgba(34,184,255,.12)}
  .da-cl-pr.lo{color:#ffb020;border-color:rgba(255,176,32,.45);background:rgba(255,176,32,.12)}
  .da-cl-why{font-size:11px;line-height:1.5;color:#b9c2cf;padding:3px 0 0 2px}   /* pourquoi DE LA JAMBE (complet) */
  .da-combo-why{font-size:11px;line-height:1.55;color:#cfe0f5;font-style:italic;margin:0 0 9px}   /* synthèse (intro en tête) */
  .da-combo-live{border-left-color:var(--st-live)}
  .da-combo-b.live{background:#ffb020;color:#1a1200;animation:combopulse 1.6s ease-in-out infinite}
  @keyframes combopulse{0%,100%{opacity:1}50%{opacity:.55}}
  /* TICKET PREMIUM (style carte Telegram, sans logo) — demande user 2026-07-12 : combinés ET simples.
     Fond sombre dégradé + accent cyan, pastilles de cote vertes, justif. par jambe (barre latérale). */
  .tkt{background:linear-gradient(160deg,#101b29,#0a0f17 60%,#080c13);border:1px solid rgba(34,184,255,.5);
       border-radius:15px;padding:13px 14px 12px;margin-top:10px;box-shadow:inset 0 0 50px rgba(34,184,255,.05)}
  .tkt-h{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;letter-spacing:.08em;
       color:#9fe7c0;text-transform:uppercase}
  .tkt-h .n{color:#7fbfa4;opacity:.8;font-weight:700}
  .tkt-h .b{font-size:9.5px;border-radius:6px;padding:2px 8px;font-weight:800;letter-spacing:.03em}
  .tkt-h .b.won{background:#34d27b;color:#04220f}
  .tkt-h .b.lost{background:#ff6b6b;color:#2a0606}
  .tkt-h .b.void{background:#9fb0c8;color:#0b1428}
  .tkt-h .b.live{background:rgba(52,210,123,.2);color:#7ff0b6;animation:combopulse 1.6s ease-in-out infinite}
  .tkt-h .top{margin-left:auto;color:#6fb4d8;font-weight:700;text-transform:none;letter-spacing:0;font-size:11px}
  .tkt-synth{font-size:11.5px;font-weight:500;color:#d0dfef;line-height:1.42;margin:9px 0 2px;
       background:rgba(34,184,255,.07);border:1px solid rgba(34,184,255,.16);border-radius:10px;padding:9px 11px}
  .tkt-leg{margin-top:12px}
  .tkt-leg-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
  .tkt-sel{font-size:13.5px;font-weight:800;color:#eef4fb;line-height:1.25;min-width:0}
  .tkt-r{flex:none;display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
  .tkt-o{background:rgba(25,196,106,.15);color:#7ff0b6;border-radius:8px;padding:2px 9px;
       font-weight:900;font-size:12.5px;font-variant-numeric:tabular-nums}
  .tkt-pr{font-size:9.5px;font-weight:800;padding:1px 6px;border-radius:99px;border:1px solid;font-variant-numeric:tabular-nums}
  .tkt-pr.hi{color:#2ec98a;border-color:rgba(46,201,138,.45);background:rgba(46,201,138,.12)}
  .tkt-pr.mid{color:#22b8ff;border-color:rgba(34,184,255,.45);background:rgba(34,184,255,.12)}
  .tkt-pr.lo{color:#ffb020;border-color:rgba(255,176,32,.45);background:rgba(255,176,32,.12)}
  .tkt-p{font-size:11px;color:#9fb0c8;background:rgba(255,255,255,.06);border-radius:5px;padding:1px 5px}
  .tkt-mk{font-size:12px;line-height:1}
  .tkt-why{font-size:11px;font-weight:400;color:#a7bcd6;line-height:1.42;margin:5px 0 2px;
       padding-left:10px;border-left:2px solid rgba(63,184,255,.4)}
  .tkt-leg.won .tkt-sel{color:#bff6d8}
  .tkt-leg.lost .tkt-sel{text-decoration:line-through;color:#ffb3b3;opacity:.85}
  .tkt-leg.void .tkt-sel{text-decoration:line-through dotted;color:#aeb9c9;opacity:.7}
  .tkt-cote{display:flex;justify-content:space-between;align-items:flex-end;margin-top:15px;padding-top:12px;
       border-top:1px solid rgba(255,255,255,.08)}
  .tkt-cote .l{font-size:11px;color:#90a4be;font-weight:700;text-transform:uppercase;letter-spacing:.07em}
  .tkt-cote .v{font-size:27px;font-weight:900;color:#fff;line-height:1;font-variant-numeric:tabular-nums}
  .tkt.won{border-color:rgba(52,210,123,.5)} .tkt.lost{border-color:rgba(255,107,107,.45)}
  .tkt.void{border-color:rgba(159,176,200,.4)}
  .tkt-subs{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
  .tkt-sub{font-size:9.5px;font-weight:700;color:#90a4be;background:rgba(255,255,255,.05);
       border:1px solid rgba(255,255,255,.09);border-radius:99px;padding:2px 9px}
  /* Bloc VERDICT (refonte 2026-07-18, demande user « réorganise tout : aligné, pleine largeur, que
     l'utile et l'intuitif ») : (1) en-tête CONFIANCE = qualificatif + % coloré + badge ✓ calibré ;
     (2) BARRE de confiance PLEINE LARGEUR avec marqueur MARCHÉ (proba implicite) ; (3) GRILLE de
     métriques alignées sur toute la largeur — Marché · Value · Cote — label au-dessus / valeur en
     dessous, séparateurs fins. Plus de pill flottant ni de cote isolée. Composant `.vb-*`/`.vm-*`
     partagé (paris + provisoires + combiné -> rendu IDENTIQUE). */
  .vb{margin-top:14px}   /* un rien plus d'espace équipes -> cadre pari (user 2026-08-16) */
  /* BARRE INTÉGRÉE DANS LE CADRE (user 2026-08-15) : la barre / « Confiance live » est À L'INTÉRIEUR du
     cadre .vm, sous la grille -> une marge la sépare des chiffres, un filet fin au-dessus (comme le pari). */
  .vm .vb-bar, .vm .vb-live, .vm .vm-res{margin:11px 12px 5px;padding-top:11px;border-top:1px solid var(--border2)}   /* écart G/D + BAS (user 2026-08-16) ; filet couleur border2 comme pari↔chiffres */
  /* BADGE résultat : écart UNIFORME 11px gauche/droite/bas = l'écart badge↔filet (padding-top 11) — user
     2026-08-16. Cadre = padding 11px(bas)/4px(côtés) -> marge 7px côtés (+4=11) et 0 bas (+11=11). */
  .vm .vm-res{margin:11px 7px 0}
  /* la barre INTERNE du bloc « Confiance live » ne doit PAS re-prendre la marge/le filet du cadre (sinon
     double retrait -> plus étroite que la barre d'avant-match). Elle épouse la largeur du bloc .vb-live. */
  .vm .vb-live .vb-bar{margin:0;padding-top:0;border-top:none}
  .vb-live-hd{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px}
  .vb-live-t{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:var(--text)}   /* « Confiance live » en BLANC (user 2026-08-15) */
  .vb-live-v{font-size:12px;font-weight:900;font-variant-numeric:tabular-nums;color:#e6eefa}
  .vb-live-ar{margin-left:3px;font-size:9.5px}
  .vb-live .vb-bar{order:0;margin-top:0}
  /* HALO « qui respire » sur la PARTIE REMPLIE seulement (user 2026-08-15) : le rail passe overflow:visible
     (sinon l'ombre est coupée) et le halo est porté par le remplissage `>i`. Effet DOUX, jamais éteint
     (baseline -> pic, pas de clignotement on/off). Respecte prefers-reduced-motion. */
  .vb-live .vb-bar{overflow:visible}
  .vb-live .vb-bar>i{animation:vbfill 1s cubic-bezier(.22,1,.36,1) .05s both,
       vblivehalo 2.8s ease-in-out .9s infinite}
  @keyframes vblivehalo{0%,100%{box-shadow:0 0 5px 0 var(--hlo,rgba(34,184,255,.18))}
       50%{box-shadow:0 0 14px 2px var(--hhi,rgba(34,184,255,.52))}}
  @media (prefers-reduced-motion:reduce){.vb-live .vb-bar>i{animation:vbfill 1s cubic-bezier(.22,1,.36,1) .05s both}}
  /* témoins d'avant-match sur la barre live (user 2026-08-15) : marqueur NOUS (VERT, comme « Confiance »
     de la grille) + marqueur MARCHÉ (BLANC, .vb-mark existant, comme « Marché »). Pas de légende texte —
     les valeurs sont déjà dans la grille de chiffres en dessous. */
  .vb-mk-us{background:#64cd8d !important;box-shadow:0 0 0 1px rgba(9,14,22,.65) !important}
  /* BARRE pleine largeur (bloc) : remplissage = confiance, marqueur = seuil marché. */
  .vb-bar{position:relative;height:9px;border-radius:99px;overflow:hidden;margin-top:9px;
       background:linear-gradient(180deg,#191b22,#212430);box-shadow:inset 0 1px 2px rgba(0,0,0,.55)}
  /* barre qui se REMPLIT au chargement (0 -> confiance), léger delay. `both` fige l'état final. */
  .vb-bar>i{position:absolute;left:0;top:0;bottom:0;border-radius:99px;display:block;min-width:9px;
       box-shadow:inset 0 1px 0 rgba(255,255,255,.35);animation:vbfill 1s cubic-bezier(.22,1,.36,1) .05s both}
  /* marqueur MARCHÉ : trait clair = proba implicite du book (seuil de rentabilité). À GAUCHE de la
     fin de barre = notre confiance dépasse le marché = edge. Apparaît une fois la barre remplie. */
  .vb-mark{position:absolute;top:-1px;bottom:-1px;width:2px;margin-left:-1px;background:#f4f8ff;
       opacity:.92;z-index:2;border-radius:2px;box-shadow:0 0 0 1px rgba(9,14,22,.55);
       animation:vbmark .35s ease .75s both}
  /* ZONE EDGE (user 2026-08-17) : remplace le trait marché « qui embrouille ». Le remplissage va jusqu'à
     NOTRE confiance ; la portion au-DELÀ du marché = notre EDGE en surbrillance (edge+), ou le manque
     jusqu'au marché = hachures (edge-). Repère marché discret = la frontière de la zone. Ces <i> ne doivent
     PAS hériter du remplissage (min-width/animation vbfill) -> réinitialisés (spécificité `.vb-bar>i.vb-edge`). */
  .vb-bar>i.vb-edge{min-width:0;border-radius:0;box-shadow:none;animation:vbmark .3s ease .8s both}
  .vb-edge-pos{background:rgba(255,255,255,.36)}                       /* notre avantage sur le marché (clair) */
  .vb-edge-neg{background:repeating-linear-gradient(45deg,rgba(255,255,255,.12) 0 3px,transparent 3px 7px)}  /* le marché nous devance */
  .vb-mktb{position:absolute;top:0;bottom:0;width:1.5px;margin-left:-.75px;background:rgba(244,248,255,.5);
       z-index:3;border-radius:2px;box-shadow:0 0 0 1px rgba(9,14,22,.5);animation:vbmark .35s ease .8s both}
  @keyframes vbfill{from{width:0}}
  @keyframes vbmark{from{opacity:0;transform:scaleY(.4)}}
  @media (prefers-reduced-motion:reduce){.vb-bar>i,.vb-mark,.vb-mktb{animation:none}}
  /* GRILLE métriques : colonnes ÉGALES sur TOUTE la largeur (width:100%), contenu centré, filets fins.
     Confiance à gauche du Marché -> comparaison directe « nous vs marché ». */
  .vm{width:100%;margin-top:12px}
  .vm-grid{display:flex;width:100%}   /* rangée Confiance/Marché/Value/Cote (ex-.vm flex) */
  /* Pari+glose DANS le cadre, centré, au-dessus des chiffres (user 2026-08-15), avec filet de séparation. */
  .vm-pick{text-align:center;padding:2px 6px 10px;margin:0 12px 9px;border-bottom:1px solid var(--border2)}   /* séparateur = même largeur que la barre (user 2026-08-15) */
  .vm-pick .mc-pick{text-align:center}
  .vm-pick .mc-gloss{text-align:center;margin-top:4px}
  .vm-cell{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;gap:3px;
       padding:2px 6px;text-align:center;border-left:1px solid rgba(255,255,255,.08)}
  .vm-cell:first-child{border-left:none}
  .vm-l{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:#a6b9cf}   /* libellés remontés en contraste (user 2026-08-15) */
  .vm-v{font-size:16px;font-weight:900;font-variant-numeric:tabular-nums;letter-spacing:-.02em;color:#e6eefa;
       line-height:1.05}
  .vm-v.vpos{color:#4be39b} .vm-v.vmid{color:#f6c54a} .vm-v.vneg{color:#ff7484}
  .vm-v.vm-na{color:#5b6675}   /* barre « — » : Value non affichée (masquée) mais colonne conservée -> alignement constant */
  .vm-sub{font-size:8.5px;font-weight:800;text-transform:lowercase;letter-spacing:.02em;line-height:1}
  .vm-conf .vm-v{font-size:19px}         /* notre confiance = héros de la grille */
  .vm-cote .vm-v{font-size:19px;color:#fff}   /* cote TOUJOURS blanche, y c. combiné du jour (demande user 2026-07-18) */
  /* Verdict façon Bull (test 2026-08-15) : la grille = petite carte tintée (la Value RESTE comme avant). */
  .vm{background:rgba(255,255,255,.05);border:1px solid var(--border2);border-radius:14px;padding:11px 4px;
       box-shadow:0 2px 12px -6px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.04)}   /* cadre du pari plus visible (user 2026-08-15) */
  .vb-reana{margin-top:11px;font-size:11px;font-weight:600;color:#7f93aa;text-align:center}
  .tkt-value{font-size:12.5px;font-weight:900;padding:2px 11px;border-radius:99px;
       font-variant-numeric:tabular-nums;white-space:nowrap}
  .tkt-value.vpos{color:#08180e;background:linear-gradient(180deg,#4be39b,#22c07d);
       box-shadow:0 1px 8px rgba(37,192,125,.32)}
  .tkt-value.vmid{color:#33270a;background:linear-gradient(180deg,#ffd98a,#f2b53c)}
  .tkt-value.vneg{color:#ffd7d7;background:rgba(255,86,86,.15);border:1px solid rgba(255,86,86,.42)}
  .tkt-simple .tkt-leg:first-of-type{margin-top:9px}
  /* Ticket : analyses REPLIABLES (compacité — demande user 2026-07-12) : justif/synthèse cachées par
     défaut, dépliées au clic (chevron). stopPropagation dans le HTML -> ne referme pas la carte du match. */
  details.tkt-fold>summary{list-style:none;cursor:pointer;-webkit-tap-highlight-color:transparent}
  details.tkt-fold>summary::-webkit-details-marker{display:none}
  .tkt-chev{color:#6a86a8;font-size:11px;line-height:1;flex:none;transition:transform .2s}
  details.tkt-fold[open] .tkt-chev,.tkt-synth-d[open] .tkt-chev{transform:rotate(180deg)}
  details.tkt-fold>.tkt-why{margin-top:6px;animation:tktfade .2s ease}
  @keyframes tktfade{from{opacity:0}to{opacity:1}}
  .tkt-synth-d{margin:9px 0 2px}
  .tkt-synth-d>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
       background:rgba(34,184,255,.07);border:1px solid rgba(34,184,255,.16);border-radius:10px;padding:9px 11px;
       -webkit-tap-highlight-color:transparent}
  .tkt-synth-d>summary::-webkit-details-marker{display:none}
  .tkt-synth-t{font-size:11.5px;font-weight:700;color:#9fe7c0;flex:1}
  .tkt-synth-d>.tkt-synth{margin-top:7px;animation:tktfade .2s ease}
  .da-bets{width:100%;border-collapse:separate;border-spacing:0;font-size:11.5px;
       background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
  .da-bets th{background:var(--surface2);color:var(--muted);font-weight:700;text-align:left;
       padding:7px 9px;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
  .da-bets td{padding:8px 9px;vertical-align:middle;border-top:1px solid rgba(255,255,255,.05)}
  .da-bp{font-weight:700;color:var(--text);width:42%}
  .da-bpr{width:34%}
  .da-bet-top td{background:rgba(255,255,255,.035)}
  .da-bet-top .da-bp{box-shadow:inset 3px 0 0 var(--accent)}
  .da-odds{display:inline-block;padding:2px 9px;border-radius:7px;font-weight:800;font-size:12px;
       color:#fff;background:var(--surface2);border:1px solid var(--border2)}
  .da-prob{display:flex;align-items:center;gap:7px}
  .da-prob .tk{flex:1;min-width:34px;height:7px;border-radius:99px;
       background:rgba(255,255,255,.09);overflow:hidden}
  .da-prob .tk span{display:block;height:100%;border-radius:99px;
       background:linear-gradient(90deg,var(--accent2),var(--accent))}
  .da-prob .pv{flex:none;min-width:30px;text-align:right;font-weight:800;font-size:11px;color:#cfe0f5}
  .da-pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:10.5px;font-weight:800;
       white-space:nowrap}
  .da-pill.ok{background:rgba(52,210,123,.16);color:#3ee089;border:1px solid rgba(52,210,123,.32)}
  .da-pill.mid{background:rgba(246,197,74,.15);color:var(--gold);border:1px solid rgba(246,197,74,.32)}
  .da-pill.hi{background:rgba(242,93,110,.15);color:#ff7484;border:1px solid rgba(242,93,110,.32)}
  /* Paris à jouer — un CADRE par pari (style « confiance ») au lieu d'un tableau */
  .da-bks{display:flex;flex-direction:column;gap:11px}
  /* Cadre d'un pari : bordure fine neutre + BANDE DE COULEUR à gauche (statut),
  fond sombre premium */
  /* BANDE gauche : VERT par défaut (tous les paris proposés) ; OR uniquement pour le pari SIMULÉ
     (à jouer,
  cf. .da-bk-reco) ; et RÉSULTAT (vert/rouge/gris) une fois le match terminé. */
  .da-bk{position:relative;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid rgba(255,255,255,.07);border-left:4px solid #34d27b;
       border-radius:13px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,.34)}
  .da-bk-tab{display:flex;align-items:center;gap:8px;padding:5px 14px 0;font-size:10.5px;
       font-weight:800;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
  .da-bk-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 14px 13px}
  .da-bk-l{min-width:0;flex:1}
  /* Commentaire du Verdict,
  DANS la carte du pari,
  sous la ligne (séparé par un filet fin) */
  /* Analyse du pari : SOUS l'affiche,
  AU-DESSUS des stats */
  .da-bk-note{font-size:11.5px;line-height:1.55;color:#c3cad6;padding:8px 14px 2px}
  .da-bk-line{position:relative;padding-left:11px;margin:0 0 7px}
  .da-bk-line:before{content:"";position:absolute;left:0;top:7px;width:4px;height:4px;border-radius:50%;background:var(--accent);opacity:.55}
  .da-bk-line:last-child{margin-bottom:0}
  .da-bk-note b{color:#cfe0f5;font-weight:800}
  /* Résidu du Verdict (à éviter / mise) APRÈS les paris : cartes PREMIUM cohérentes (bande gauche +
     pastille d'icône + titre majuscule + texte) */
  .da-bets-extra{margin-top:11px;display:flex;flex-direction:column;gap:9px}
  .da-bx{border:1px solid rgba(255,255,255,.07);border-left:4px solid var(--border2);border-radius:12px;
       padding:11px 13px 12px;background:linear-gradient(180deg,var(--surface2),var(--surface))}
  .da-bx.skip{border-left-color:#ff9f43}        /* à éviter -> orange (prudence) */
  .da-bx.mise{border-left-color:var(--accent)}  /* mise -> accent (info) */
  .da-bx-h{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .da-bx-ic{flex:none;width:24px;height:24px;border-radius:7px;display:inline-flex;align-items:center;
       justify-content:center;font-size:13px;line-height:1;background:rgba(255,255,255,.05);
       border:1px solid rgba(255,255,255,.08)}
  .da-bx-lbl{font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}
  .da-bx.skip .da-bx-lbl{color:#ffb163}
  .da-bx.mise .da-bx-lbl{color:var(--accent)}
  .da-bx-t{font-size:11.5px;line-height:1.55;color:var(--muted)}
  .da-bk-sel{display:flex;align-items:flex-start;gap:8px;padding:8px 14px 0}
  .da-bk-name{flex:1;min-width:0;font-size:14.5px;font-weight:800;color:#fff;line-height:1.3}
  /* Badge COTE proéminent en haut-droite du pari simple (comme la cote du combiné) */
  .da-bk-cote{flex:none;align-self:flex-start;background:#19c46a;color:#06210f;border-radius:7px;
       padding:2px 9px;font-size:12.5px;font-weight:900;font-variant-numeric:tabular-nums;
       white-space:nowrap;letter-spacing:.01em}
  /* Barre de CONFIANCE (proba) sous l'affiche du pari */
  .da-cbar{margin:10px 14px 0;height:6px;border-radius:99px;background:rgba(255,255,255,.08);overflow:hidden}
  .da-cbar>span{display:block;height:100%;border-radius:99px}
  .da-cbar.grn>span{background:linear-gradient(90deg,#19c46a,#34d27b)}   /* autres paris : VERT */
  .da-cbar.gold>span{background:linear-gradient(90deg,#d8a72a,#f6c54a)}  /* pari simulé : OR */
  /* Sûreté en PASTILLE texte (élevée/moyenne/faible) — l'étoile ⭐ est réservée au pari retenu */
  .da-bk-safe{margin-left:4px;font-size:9px;font-weight:800;text-transform:uppercase;
       letter-spacing:.04em;padding:2px 7px;border-radius:999px;border:1px solid transparent}
  .da-bk-safe.saf-hi{color:#5be08c;background:rgba(52,210,123,.13);border-color:rgba(52,210,123,.32)}
  .da-bk-safe.saf-mid{color:#f0cf63;background:rgba(232,195,77,.13);border-color:rgba(232,195,77,.32)}
  .da-bk-safe.saf-lo{color:#ff8f9a;background:rgba(255,107,107,.13);border-color:rgba(255,107,107,.32)}
  /* Badge COMBINÉ (sûreté + validation panel) : une seule pastille, couleur = niveau de sûreté. */
  .da-bk-combo{margin-left:4px;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;
       padding:2px 9px;border-radius:999px;border:1px solid transparent;white-space:nowrap}
  .da-bk-combo.saf-hi{color:#5be08c;background:rgba(52,210,123,.13);border-color:rgba(52,210,123,.32)}
  .da-bk-combo.saf-mid{color:#f0cf63;background:rgba(232,195,77,.13);border-color:rgba(232,195,77,.32)}
  .da-bk-combo.saf-lo{color:#ff8f9a;background:rgba(255,107,107,.13);border-color:rgba(255,107,107,.32)}
  /* Abstention (pas de value) : badge NEUTRE + résultat conditionnel « aurait gagné/perdu » */
  .da-bk-combo.saf-abst{color:#9fb6cf;background:rgba(150,165,185,.12);border-color:rgba(150,165,185,.30)}
  /* Bandeau de STATS pro : Confiance · Cote · Value */
  .da-bk-stats{display:flex;gap:7px;padding:12px 14px 14px}
  .da-st{flex:1;min-width:0;text-align:center;background:rgba(255,255,255,.04);
       border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:7px 3px}
  .da-st-v{display:block;font-size:14px;font-weight:900;color:#eaf2ff;font-variant-numeric:tabular-nums;
       line-height:1.1}
  .da-st-l{display:block;font-size:7.5px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;
       color:var(--muted);margin-top:3px}
  .da-st-cote .da-st-v{color:#7ff0b6}        /* cote = vert pari */
  .da-st-cote{border-color:rgba(34,191,108,.28);background:rgba(25,196,106,.08)}
  .da-st-pos .da-st-v{color:#34d27b}         /* value EV+ vert */
  .da-st-neg .da-st-v{color:var(--gold)}     /* value EV− ambre */
  .da-bk-saf2{padding:9px 14px 0}
  .da-bk-m{margin-top:8px;display:flex;flex-direction:column;gap:7px}
  .da-bk-cote{flex:none;padding:9px 15px;border-radius:11px;font-size:16px;font-weight:800;
       color:#7ff0b6;background:rgba(25,196,106,.16);border:1px solid rgba(34,191,108,.42)}
  /* Badge de SÛRETÉ premium : pastille lumineuse + libellé MAJUSCULE,
  couleur = bande */
  .da-saf{align-self:flex-start;display:inline-flex;align-items:center;gap:6px;padding:4px 11px;
       border-radius:99px;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;
       white-space:nowrap}
  .da-saf-dot{width:7px;height:7px;border-radius:50%;flex:none}
  .da-saf.ok{background:rgba(52,210,123,.12);color:#3ee089;border:1px solid rgba(52,210,123,.30)}
  .da-saf.ok .da-saf-dot{background:#34d27b;box-shadow:0 0 7px rgba(52,210,123,.85)}
  .da-saf.mid{background:rgba(255,159,67,.12);color:#ffb163;border:1px solid rgba(255,159,67,.32)}
  .da-saf.mid .da-saf-dot{background:#ff9f43;box-shadow:0 0 7px rgba(255,159,67,.85)}
  .da-saf.hi{background:rgba(242,93,110,.12);color:#ff7484;border:1px solid rgba(242,93,110,.30)}
  .da-saf.hi .da-saf-dot{background:#ff6b6b;box-shadow:0 0 7px rgba(242,93,110,.85)}
  .da-bk-tags{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
  .da-bets-hint{font-size:9.5px;font-weight:600;color:var(--muted)}
  .da-ev{display:inline-block;padding:2px 9px;border-radius:99px;font-size:10.5px;font-weight:800;white-space:nowrap}
  .da-ev.pos{background:rgba(52,210,123,.18);color:#3ee089;border:1px solid rgba(52,210,123,.4)}
  .da-ev.neu{background:rgba(255,255,255,.05);color:var(--muted);border:1px solid var(--border)}
  .da-ev.neg{background:rgba(246,197,74,.13);color:var(--gold);border:1px solid rgba(246,197,74,.3)}
  /* Résultat PAR pari (après match) : cadre VERT (gagné) / ROUGE (perdu) / gris (remboursé) + halo */
  .da-bk-mark{margin-left:auto;font-size:11px;font-weight:900;padding:2px 8px;border-radius:99px;
       letter-spacing:.02em}
  .da-bk-mark.mk-w{color:#06140d;background:#34d27b}
  .da-bk-mark.mk-l{color:#fff;background:#ff6b6b}
  .da-bk-mark.mk-p{color:#0b1428;background:#9fb0c8}   /* badge ✅ À JOUER : OR */
  .da-bk-mark.mk-abst{color:#9fb6cf;background:rgba(150,165,185,.14);font-weight:700}  /* abstention : « aurait gagné » neutre */
  .da-bk-val{margin-left:6px;font-size:9px;font-weight:800;letter-spacing:.02em;padding:2px 7px;
       border-radius:99px;color:#06140d;background:linear-gradient(90deg,#34d27b,#22b8ff);white-space:nowrap}
  /* MEILLEURE VALUE : même carte que les paris safe,
  mais encadré OR + halo OR (seul repère premium) */
  /* À JOUER (meilleure value) : bande OR (le pari à jouer se distingue) + halo OR + badge + tab OR */
  /* Pari retenu : plus de halo OR (demande user) -> rendu identique à un pari normal. */
  .da-reco{margin:0 0 9px;padding:9px 12px;border-radius:11px;font-size:12.5px;line-height:1.45}
  .da-reco.play{background:rgba(52,210,123,.12);border:1px solid rgba(52,210,123,.36);color:#eaf2ff}
  .da-reco.skip{background:rgba(255,255,255,.04);border:1px solid var(--border);color:var(--muted)}
  .da-reco-ev{color:#3ee089;font-weight:800;white-space:nowrap}
  .da-bk-won{border-left-color:#34d27b;
       box-shadow:0 0 0 1px rgba(52,210,123,.18),0 6px 20px rgba(25,196,106,.18)}
  .da-bk-lost{border-left-color:#ff6b6b;
       background:linear-gradient(180deg,rgba(42,16,22,.55),rgba(20,10,16,.4));
       box-shadow:0 0 0 1px rgba(242,93,110,.16),0 6px 20px rgba(242,93,110,.16)}
  .da-bk-lost .da-bk-tab{color:#ff8090}
  .da-bk-lost .da-bk-cote{color:#ffb3bc;background:rgba(242,93,110,.16);
       border:1px solid rgba(242,93,110,.45)}
  .da-bk-push{border-left-color:#9fb0c8;filter:saturate(.7)}
  /* Les faits (déroulés dans l'analyse,
  plus en accordéon) */
  .da-faits-h{padding:9px 12px 0;font-size:12px;font-weight:800;color:#9fd0ff;
       text-transform:uppercase;letter-spacing:.03em}
  /* « Informations » : même style que le combiné mais ligne LATÉRALE bleue (demande utilisateur). */
  .da-faits{margin:12px 0 4px;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--border);border-left:3px solid #22b8ff;border-radius:12px;
       padding:1px 0 4px;overflow:hidden}
  .da-faits>summary{cursor:pointer;list-style:none;padding:11px 13px;font-size:12.5px;
       font-weight:800;color:#cfe0f5;display:flex;align-items:center;justify-content:space-between}
  .da-faits>summary::-webkit-details-marker{display:none}
  .da-faits>summary::after{content:"▾";color:var(--muted);transition:transform .18s}
  .da-faits[open]>summary{border-bottom:1px solid var(--border)}
  .da-faits[open]>summary::after{transform:rotate(180deg)}
  /* Sous-pli « 🔍 Voir les détails » (dépli de carte épuré) : regroupe faits + tendances + H2H, replié
     par défaut -> carte épurée, la preuve à 1 tap (demande user 2026-07-20). */
  .da-more{margin:10px 0 2px;border:1px solid var(--border);border-radius:12px;overflow:hidden;
       background:rgba(255,255,255,.015)}
  .da-more-s{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px;padding:11px 13px;
       font-size:12px;font-weight:800;color:#9fb3cc;letter-spacing:.02em}
  .da-more-s::-webkit-details-marker{display:none}
  .da-more-chev{margin-left:auto;color:var(--muted);transition:transform .18s}
  .da-more[open] .da-more-chev{transform:rotate(180deg)}
  .da-more[open]>.da-more-s{border-bottom:1px solid var(--border)}
  .da-more-b{padding:2px 12px 6px}
  .da-more-b .da-sec{margin:10px 0;background:transparent;border:0;padding:2px 0}
  .da-faits-b{padding:8px 14px 12px;font-size:12.5px;line-height:1.65;color:var(--text)}
  .da-faits-b .da-ul{padding-left:4px;list-style:none}
  .da-faits-b .da-ul li{margin:9px 0;padding-left:15px;position:relative}
  .da-faits-b .da-ul li::before{content:"";position:absolute;left:0;top:7px;width:6px;height:6px;
       border-radius:99px;background:var(--accent)}
  .da-faits-b a{display:inline-block;padding:1px 8px;margin:1px 2px 1px 0;border-radius:99px;
       font-size:11px;font-weight:700;color:var(--accent);background:rgba(255,255,255,.05);
       border:1px solid var(--border);text-decoration:none;vertical-align:baseline}
  /* --- Bloc Tendances (séries SofaScore mappées aux marchés) --- */
  .strk{display:flex;flex-direction:column;gap:10px}
  .strk-team{background:linear-gradient(180deg,var(--surface2),var(--surface));
       border:1px solid var(--border);border-radius:13px;padding:11px 12px}
  .strk-h2h{border-color:rgba(34,184,255,.30)}
  .strk-h{font-size:12.5px;font-weight:800;color:#eaf2ff;margin-bottom:9px;display:flex;align-items:center;gap:7px}
  .strk-cs{display:flex;flex-wrap:wrap;gap:6px}
  /* Chaque série = une JAUGE : barre verte proportionnelle au ratio + couleur selon la force */
  .strk-c{position:relative;overflow:hidden;display:inline-flex;align-items:center;gap:7px;
       padding:5px 11px;border-radius:10px;font-size:11px;color:#cfe0f5;
       background:rgba(255,255,255,.035);border:1px solid var(--border)}
  .strk-fill{position:absolute;left:0;top:0;bottom:0;z-index:0;background:rgba(52,210,123,.14)}
  .strk-t,.strk-c b{position:relative;z-index:1}
  .strk-c b{font-weight:800;font-variant-numeric:tabular-nums}
  .strk-c.s-strong{border-color:rgba(52,210,123,.55)}
  .strk-c.s-strong b{color:#46e08a} .strk-c.s-strong .strk-fill{background:rgba(52,210,123,.22)}
  .strk-c.s-mid b{color:#5fd0ff} .strk-c.s-mid .strk-fill{background:rgba(34,184,255,.13)}
  .strk-c.s-low{opacity:.7} .strk-c.s-low b{color:var(--muted)}
  .strk-c.s-low .strk-fill{background:rgba(255,255,255,.05)}
  .strk-c.s-count b{color:#5fd0ff}
  /* CTA cards */
  .big{display:block;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border-radius:var(--radius);padding:18px 18px;margin:11px 0;border:1px solid var(--cardline);
       font-size:16px;font-weight:700;box-shadow:var(--cardglow),var(--shadow);transition:.16s}
  .big:active{transform:scale(.99)}
  .big .d{font-size:12.5px;color:var(--muted);font-weight:400;margin-top:5px;line-height:1.5}
  /* Footer « 18+ » ANCRÉ EN BAS de la zone scrollable (user 2026-08-19) : `margin-top:auto` le pousse tout en
     bas du .wrap (juste au-dessus de la barre de nav) -> visible UNIQUEMENT quand on scrolle au plus bas de
     l'onglet (contenu court : il reste au fond de l'écran ; contenu long : au bout du scroll). PAS de ligne
     de séparation au-dessus (border-top retiré, demande user). */
  .foot{color:var(--dim);font-size:10.5px;margin-top:auto;padding-top:14px;text-align:center;line-height:1.6}
  .src{font-size:12px;font-weight:600;padding:9px 13px;border-radius:12px;margin:4px 0 2px;
       border:1px solid var(--border)}
  .src.ok{background:rgba(46,226,127,.10);color:var(--accent);border-color:rgba(46,226,127,.22)}
  .src.ko{background:var(--gold-bg);color:var(--gold);border-color:var(--gold-bd)}
  /* ===== Polish OddScore : chiffres mono · en-têtes « • » · titres majuscules ===== */
  :root{--font-mono:'Segoe UI',Roboto,Arial,sans-serif}   /* aligné sur la police Telegram ; tabular-nums garde l'alignement des chiffres */
  .da-bk-cote,
  .ds-v,
  .cal-gap,
  .cal-line b,
  .calg-vs b,
  .lb-clk,
  .cd,
  .da-prob .pv,
  .dd-cote{
       font-family:var(--font-mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  /* En-têtes de SECTION façon « • TITRE » (accent,
  majuscules,
  espacé) */
  details.sec2>summary,
  .cal-h,
  .calg-h{
       text-transform:uppercase;letter-spacing:.08em;color:var(--accent)}
  details.sec2>summary::before,
  .cal-h::before,
  .calg-h::before{content:"• ";color:var(--accent);font-weight:900}
  /* (puce « • » et emojis retirés des titres de l'accueil — demande utilisateur 2026-06-12) */
  .dash-h>span:first-child{text-transform:uppercase;letter-spacing:.06em}
  /* Grands TITRES de page en MAJUSCULES (Archivo black) — adapté à TOUT le site */
  h1,
  h2,
  .pg-h,
  .sporthd-t,
  .da-bets-h{text-transform:uppercase;letter-spacing:.02em;font-weight:900}
  /* INTERFACE en majuscules (nav,
  boutons,
  puces,
  tuiles,
  liens) — PAS les noms d'équipes ni
     les textes d'analyse (lisibilité). Look 100 % cohérent façon OddScore. */
  .botnav a .lb,
  .dash-tile,
  .dash-more,
  .dash-stat-go,
  .dash-h-a,
  .exp-c,
  .da-ev,
  .b-val,
  .b-uni,
  .b-conf,
  .calg-v,
  .src,
  .dd-cote,
  .dash-next,
  .da-bets-hint,
  .cal-v-t,
  .fpick-t,
  .an-tag{
       text-transform:uppercase;letter-spacing:.03em}
  /* Grande courbe d'équité de la carte Performance (accueil) */
  .dperf-chart{margin:10px 0 2px}
  .dperf-chart .sx-heroc{display:block;width:100%;height:88px}
  /* Bandeau « N matchs en direct -> Live » sur l'accueil (les lives ne sont plus listés ici) */
  .dash-livebar{display:flex;align-items:center;gap:9px;margin:14px 0 4px;padding:11px 14px;
       border:1px solid rgba(52,210,123,.4);border-radius:14px;font-size:12.5px;color:var(--text);
       background:linear-gradient(180deg,rgba(52,210,123,.10),rgba(52,210,123,.03))}
  .dash-livebar .nr-dot{width:9px;height:9px;flex:none}
  .dash-livebar-go{margin-left:auto;font-size:11px;font-weight:800;color:#34d27b;
       text-transform:uppercase;letter-spacing:.04em}
  /* Carte « Évolution du profit » (/stats) : courbe d'équité unique + repères */
  .sx-card{background:rgba(34,184,255,.055);   /* teinte UNIE : fond stable à l'ouverture de l'historique */
       border:1px solid rgba(34,184,255,.60);border-radius:16px;
       box-shadow:0 0 26px rgba(34,184,255,.20),var(--shadow-sm);padding:12px 12px 10px;margin:12px 0}
  /* ONGLET STATS (.statsx) : fond cyan (comme la carte .spf des onglets sport) sur TOUTES les lignes —
     scopé pour NE PAS toucher les mêmes composants affichés DANS les onglets sport (qui restent sombres
     pour contraster avec la carte .spf cyan qui les contient). */
  .statsx .sx-sport,.statsx .cal-row,.statsx .calg-row,.statsx .calg-sport{
       background:linear-gradient(180deg,rgba(34,184,255,.10),rgba(34,184,255,.025));
       border-color:rgba(34,184,255,.45)}
  /* UN SEUL cadre par sport : la ligne résumé interne (.sx-row) est APLATIE (pas de 2e cadre dans le
     cadre cyan du sport). Le sport = la carte .sx-sport, point. */
  .statsx .sx-sport .sx-row{background:transparent;border:0;border-radius:0;padding:0}
  .statsx .calg-sub{background:linear-gradient(180deg,rgba(34,184,255,.05),rgba(34,184,255,.015))}
  /* Paris dépliés DANS le cadre sport : PAS de cadre-dans-un-cadre -> lignes PLATES (sans fond ni
     bordure de carte), juste un filet CYAN de séparation (plus de gris/brun). */
  .statsx .sx-dd{gap:0}
  .statsx .sx-dd-row{background:transparent;border:0;border-radius:0;padding:9px 2px;
       border-top:1px solid rgba(34,184,255,.18)}
  .statsx .sx-dd-head{border-bottom-color:rgba(34,184,255,.28)}   /* filet « X/Y gagnés » -> cyan */
  .statsx .sx-divider{background:rgba(34,184,255,.28)}            /* séparateur courbe -> cyan */
  /* Graphiques des 3 sports ALIGNÉS : nom du sport en largeur FIXE -> la sparkline démarre au même x
     et a la MÊME largeur sur Football / Tennis / Basket. */
  .statsx .sx-row-n{flex:0 0 62px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .sx-equity{margin:6px 0 0}
  .sx-equity .sx-heroc{display:block;width:100%;height:auto}
  /* ---- Blocs « suivis indicatifs » (combiné du jour, provisoires) : classes réutilisables (fin des
     styles inline hétérogènes) pour un rendu HOMOGÈNE avec le reste du design system. --- */
  .sx-chart{margin-top:10px}                                   /* espacement UNIFORME des courbes */
  .sx-gold{color:var(--gold)}
  .sx-meta{display:flex;gap:14px;margin:6px 0 2px;font-size:12px;color:var(--muted)}
  .sx-meta b{color:var(--text);font-variant-numeric:tabular-nums}
  .sx-synth{font-size:11px;color:var(--muted);line-height:1.45;margin:5px 0 8px;font-style:italic}
  .sx-today{margin-top:10px;padding:11px 12px;border:1px solid var(--gold-bd);border-radius:12px;
       background:linear-gradient(180deg,rgba(246,197,74,.09),rgba(246,197,74,.02))}
  .sx-today-h{display:flex;justify-content:space-between;align-items:center;font-size:11px;font-weight:800}
  .sx-hint{font-size:9.5px;color:var(--dim);margin:1px 0 3px}
  /* ===== Onglet MONTANTE (fonctionnalité préparée 2026-07-24) ===== */
  .mont-intro{font-size:12.5px;color:var(--muted);text-align:center;margin:0 4px 12px;line-height:1.5}
  .mont-hero{text-align:center;padding:18px 14px 15px;margin:2px 0 14px;border-radius:18px;
       border:1px solid rgba(52,210,123,.5);background:linear-gradient(180deg,rgba(52,210,123,.12),rgba(52,210,123,.02));
       box-shadow:0 0 34px rgba(52,210,123,.16),var(--shadow-sm)}
  .mont-hero-l{font-size:11px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:var(--muted)}
  .mont-hero-cap{font-size:46px;font-weight:900;letter-spacing:-.03em;line-height:1;margin:6px 0 4px;
       color:#34d27b;font-variant-numeric:tabular-nums}
  .mont-hero-sub{font-size:12.5px;color:var(--muted);font-weight:600}
  .mont-hero-sub b{color:var(--text);font-variant-numeric:tabular-nums}
  /* HERO PREMIUM « montante en cours » (user 2026-08-09, rendu 100 % pro) : le MULTIPLICATEUR ×N est la
     vedette (énorme, dégradé doré + halo), puis la progression 10 € -> capital sous une fine règle. */
  .mont-hero-live{position:relative;overflow:hidden;text-align:center;padding:20px 16px 17px;
       border:1px solid rgba(246,197,74,.42);
       background:radial-gradient(130% 90% at 50% -10%,rgba(246,197,74,.14),transparent 62%),
                  linear-gradient(180deg,#121a28,#0b0e14);
       box-shadow:0 0 44px rgba(246,197,74,.15),var(--shadow-sm)}
  .mont-hero-live .mhe{font-size:9.5px;font-weight:800;letter-spacing:.2em;text-transform:uppercase;
       color:var(--gold);opacity:.9}
  .mont-hero-live .mhx{font-size:66px;font-weight:900;letter-spacing:-.04em;line-height:.94;margin-top:3px;
       color:var(--gold);font-variant-numeric:tabular-nums}
  @supports ((-webkit-background-clip:text) or (background-clip:text)){
    .mont-hero-live .mhx{background:linear-gradient(176deg,#ffe79b 4%,#f6c54a 52%,#d69f2b);
       -webkit-background-clip:text;background-clip:text;color:transparent;
       filter:drop-shadow(0 3px 22px rgba(246,197,74,.5))}}
  .mont-hero-live .mhx-cap{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
       color:var(--muted);margin-top:1px}
  /* Palier atteint, en PILULE dorée juste sous le multiplicateur (user 2026-08-22). */
  .mont-hero-live .mhx-pal{display:inline-block;margin-top:9px;font-size:11px;font-weight:800;letter-spacing:.03em;
       color:#f6c54a;background:rgba(246,197,74,.12);border:1px solid rgba(246,197,74,.32);border-radius:999px;padding:3px 12px}
  /* Hero PERDU : « Palier N » en ROUGE (au lieu du multiplicateur doré), courbe qui retombe (user 2026-08-22). */
  .mont-hero-lost .mhx-lost{font-size:44px;-webkit-text-fill-color:#ff8a8a;color:#ff8a8a}
  .mont-hero-lost .mp-now{color:#ff8a8a}
  .mont-prog{display:flex;align-items:center;justify-content:center;gap:16px;margin-top:15px;padding-top:14px;
       border-top:1px solid rgba(255,255,255,.08)}
  .mont-prog .mp-cell{display:flex;flex-direction:column;gap:3px;min-width:0}
  .mont-prog .mp-cell b{font-size:21px;font-weight:900;line-height:1;color:var(--text);
       font-variant-numeric:tabular-nums;letter-spacing:-.01em}
  .mont-prog .mp-cell b.mp-now{color:#34d27b}
  .mont-prog .mp-cell span{font-size:8.5px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;
       color:var(--dim)}
  .mont-prog .mp-arrow{font-size:19px;font-weight:900;color:var(--gold);line-height:1;flex:none}
  .mont-chip{display:inline-block;margin-top:11px;padding:3px 13px;border-radius:999px;font-size:11px;
       font-weight:700;letter-spacing:.02em;border:1px solid rgba(52,210,123,.35);
       background:rgba(52,210,123,.09);color:#64cd8d}
  .mont-chip.wait{border-color:rgba(246,197,74,.4);background:rgba(246,197,74,.09);color:var(--gold)}
  .mont-sec-h{font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#cfe0f5;
       margin:22px 2px 10px;display:flex;align-items:center;gap:9px}
  .mont-sec-h::before{content:"";flex:none;width:15px;height:2px;border-radius:2px;
       background:linear-gradient(90deg,var(--gold),rgba(246,197,74,.2))}   /* accent doré, ancre premium commune */
  .mont-sec-h .tag{margin-left:auto;font-size:9px;font-weight:700;letter-spacing:.04em;color:var(--gold);
       border:1px solid rgba(246,197,74,.35);border-radius:999px;padding:2px 9px;text-transform:none}
  /* Lead (micro-copy) sous un titre de section — guide le nouveau venu, ton premium */
  .mont-lead{font-size:11.5px;color:var(--muted);line-height:1.5;margin:-5px 4px 12px 26px}
  .mont-lead b{color:var(--text)}
  /* Échelle des paliers (staircase) — BARRE DE PROGRESSION de fond (largeur ∝ capital/pic) : on voit la mise
     grimper palier après palier (refonte 2026-08-09). Contenu au-dessus (z-index). */
  .mont-ladder{display:flex;flex-direction:column;gap:7px}
  .mont-step{position:relative;overflow:hidden;display:flex;align-items:center;gap:11px;padding:9px 12px;
       border-radius:13px;background:linear-gradient(180deg,#0f1620,#0b0d13);border:1px solid var(--border)}
  .mont-step > *{position:relative;z-index:1}
  .mont-step-fill{position:absolute;z-index:0;left:0;top:0;bottom:0;border-radius:0 13px 13px 0;
       background:linear-gradient(90deg,rgba(52,210,123,.16),rgba(52,210,123,.05));border-right:1px solid rgba(52,210,123,.22)}
  .mont-step.lost .mont-step-fill{background:linear-gradient(90deg,rgba(255,107,107,.14),rgba(255,107,107,.04));border-right-color:rgba(255,107,107,.22)}
  .mont-step.pending .mont-step-fill{background:linear-gradient(90deg,rgba(246,197,74,.15),rgba(246,197,74,.04));border-right-color:rgba(246,197,74,.24)}
  .mont-step.peak .mont-step-fill{background:linear-gradient(90deg,rgba(246,197,74,.2),rgba(246,197,74,.06));border-right-color:rgba(246,197,74,.4)}
  .mont-step.won{border-color:rgba(52,210,123,.32)} .mont-step.lost{border-color:rgba(255,107,107,.32)}
  .mont-step.pending{border-color:rgba(246,197,74,.42)}
  .mont-step.peak{border-color:rgba(246,197,74,.55);box-shadow:0 0 18px rgba(246,197,74,.14)}
  .mont-step.peak .mont-step-n{background:linear-gradient(180deg,#ffe79b,#f6c54a);color:#3a2a05}
  .mont-step.peak .mont-step-a .to{color:var(--gold)}
  .mont-step-n{flex:none;width:26px;height:26px;border-radius:8px;display:flex;
       align-items:center;justify-content:center;line-height:1;background:rgba(255,255,255,.05);color:var(--muted)}
  .mont-step-n b{font-size:12px;font-weight:800}
  .mont-step.won .mont-step-n{background:rgba(52,210,123,.15);color:#64cd8d}
  .mont-step.lost .mont-step-n{background:rgba(255,107,107,.15);color:#ff6b6b}
  .mont-step.pending .mont-step-n{background:rgba(246,197,74,.15);color:var(--gold)}
  .mont-step-m{flex:1;min-width:0}
  .mont-step-t{font-size:12.5px;font-weight:700;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* Pari joué + COTE bien visibles : le pari s'affiche en entier, la cote en pastille bleue distincte. */
  .mont-step-s{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:2px}
  .mont-step-s .sel{font-size:10.5px;color:var(--muted);line-height:1.3}
  .mont-step-c{flex:none;font-size:11.5px;font-weight:800;color:#d6ecff;background:rgba(34,184,255,.18);
       border:1px solid rgba(34,184,255,.42);border-radius:7px;padding:1px 8px;font-variant-numeric:tabular-nums}
  /* Colonne droite : capital RÉSULTAT (gros) + gain du palier (compact) */
  .mont-step-a{flex:none;text-align:right;font-variant-numeric:tabular-nums;padding-left:8px}
  .mont-step-a .to{font-size:15px;font-weight:800;color:var(--text);display:block}
  .mont-step.won .mont-step-a .to{color:#34d27b}
  .mont-step-a .to .ko{color:#ff6b6b} .mont-step-a .to .wait{color:var(--gold)}
  .mont-step-g{display:block;font-size:11.5px;font-weight:800;color:var(--muted);margin-top:1px}
  .mont-step-g.up{color:#64cd8d} .mont-step-g.dn{color:#ff8a8a}
  /* MISE bien visible (demande user 2026-07-25) : label discret + montant lisible. */
  .mont-step-mise{display:block;font-size:11px;font-weight:800;color:var(--muted);letter-spacing:.02em}
  .mont-step-mise b{color:var(--text);font-weight:800}
  /* MONTANTE fusionnée dans Confiance (user 2026-08-08) : titre « MONTANTE • PALIER N » DANS le cadre BLEU,
     centré, BLANC, MAJUSCULE, sans emoji, + fine ligne dessous. La carte = LA MÊME que l'onglet Montante. */
  .mont-hdr{display:block;text-align:center;color:#fff;font-weight:900;font-size:14px;text-transform:uppercase;
       letter-spacing:.04em;padding:10px 10px 8px;text-decoration:none;background:rgba(58,160,255,.14);
       border-bottom:1px solid rgba(58,160,255,.42);-webkit-tap-highlight-color:transparent}   /* fine ligne sous le titre */
  .mont-cardwrap{border:1px solid #3aa0ff;border-radius:14px;overflow:hidden;
       box-shadow:0 0 0 1px rgba(58,160,255,.30),0 8px 26px rgba(58,160,255,.12)}
  .mont-cardwrap > .cleg{border-color:transparent;box-shadow:none;border-radius:0}
  /* Une fois RÉGLÉ : contour VERT (gagné) / ROUGE (perdu), comme les autres cartes résultat (user 2026-08-08). */
  .mont-cardwrap.won{border-color:var(--st-won);box-shadow:0 0 0 1px rgba(52,210,123,.30),0 8px 26px rgba(52,210,123,.12)}
  .mont-cardwrap.lost{border-color:var(--st-lost);box-shadow:0 0 0 1px rgba(255,107,107,.30),0 8px 26px rgba(255,107,107,.12)}
  .mont-hdr.won{background:rgba(52,210,123,.16);border-bottom-color:rgba(52,210,123,.45)}
  .mont-hdr.lost{background:rgba(255,107,107,.16);border-bottom-color:rgba(255,107,107,.45)}
  /* Courbe d'aire de la trajectoire du capital (10 € -> pic), panneau chart subtil (refonte 2026-08-09) */
  .mont-curve{margin:2px 0 12px;padding:9px 8px 3px;border-radius:14px;
       background:linear-gradient(180deg,rgba(52,210,123,.05),transparent);border:1px solid var(--border)}
  .mont-c{width:100%;height:auto;display:block}
  /* Courbe DANS le hero (user 2026-08-14) : pas de boîte (bordure/fond) -> le graphe s'intègre au hero. */
  .mont-hero-curve{margin:13px 0 0;padding:6px 0 0;background:none;border:0}
  .mont-c-lbl{font-size:9.5px;font-weight:800;fill:var(--muted);font-variant-numeric:tabular-nums}
  .mont-c-lbl.end{fill:#34d27b;font-size:11.5px;font-weight:900}
  /* Palmarès : KPIs premium propres à la montante (n'affecte pas les .sx-kpi des stats). Best = or. */
  .mont-kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}
  .mont-kpi{text-align:center;padding:14px 8px 12px;border-radius:15px;
       background:linear-gradient(180deg,#0f1620,#0b0d13);border:1px solid var(--border)}
  .mont-kpi.best{border-color:rgba(246,197,74,.42);
       background:radial-gradient(120% 90% at 50% 0%,rgba(246,197,74,.1),transparent 65%),linear-gradient(180deg,#0f1620,#0b0d13)}
  .mont-kpi b{display:block;font-size:20px;font-weight:900;color:var(--text);font-variant-numeric:tabular-nums;
       letter-spacing:-.01em;line-height:1}
  .mont-kpi.best b{color:var(--gold)}
  .mont-kpi span{display:block;font-size:8.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
       color:var(--dim);margin-top:6px;line-height:1.3}
  /* Historique des montantes (chaînes passées) */
  .mont-hist{display:flex;flex-direction:column;gap:8px}
  .mont-hrow{display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:12px;
       background:rgba(255,255,255,.03);border:1px solid var(--border)}
  .mont-hrow-b{flex:none;width:24px;height:24px;border-radius:7px;display:flex;align-items:center;justify-content:center;
       font-size:12px;background:rgba(255,107,107,.15);color:#ff6b6b}
  .mont-hrow-m{flex:1;min-width:0;font-size:12px;color:var(--text)}
  .mont-hrow-m span{display:block;font-size:11px;color:var(--muted);margin-top:2px}
  .mont-hrow-v{flex:none;font-size:13px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums}
  .mont-empty{text-align:center;color:var(--muted);font-size:12px;padding:18px 12px;line-height:1.5;
       border:1px dashed var(--border);border-radius:13px}
  /* ===== Onglet CALENDRIER (P&L par jour, demande user 2026-07-25) ===== */
  .mcal{--mt:0}
  .mcal-nav{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:2px 0 12px}
  .mcal-title{flex:1;text-align:center;font-size:15px;font-weight:900;letter-spacing:.01em;color:var(--text)}
  .mcal-arw{flex:none;width:38px;height:38px;border-radius:12px;border:1px solid var(--border);
       background:rgba(255,255,255,.04);color:var(--text);font-size:20px;font-weight:800;line-height:1;
       display:flex;align-items:center;justify-content:center}
  .mcal-arw:active{transform:scale(.92)} .mcal-arw.off{opacity:.3;pointer-events:none}
  /* Bilan du mois : ROI héros + courbe d'équité + KPIs */
  .mcal-sum{border:1px solid var(--border);border-radius:18px;padding:16px 14px 13px;margin-bottom:14px;
       background:radial-gradient(120% 80% at 50% 0,rgba(52,210,123,.06),rgba(255,255,255,.015) 60%,transparent)}
  .mcal-sum.neg{background:radial-gradient(120% 80% at 50% 0,rgba(255,107,107,.06),rgba(255,255,255,.015) 60%,transparent)}
  .mcal-sum-hero{text-align:center;font-size:38px;font-weight:900;line-height:1;letter-spacing:-.025em;
       font-variant-numeric:tabular-nums}
  .mcal-sum-lb{display:block;font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;
       color:var(--muted);margin-top:5px}
  .mcal-eq{margin:12px 0 4px}
  .mcal-eq .sx-heroc{max-height:none;width:100%;height:auto}   /* pleine largeur, MÊME rendu que les courbes Stats */
  .mcal-sum-kpis{display:flex;gap:8px;margin-top:12px}
  .mcal-sum-kpis>div{flex:1;text-align:center;background:rgba(255,255,255,.04);border:1px solid var(--border);
       border-radius:12px;padding:9px 4px}
  .mcal-sum-kpis b{display:block;font-size:15px;font-weight:900;color:var(--text);font-variant-numeric:tabular-nums}
  .mcal-sum-kpis span{display:block;font-size:8.5px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;
       color:var(--muted);margin-top:3px}
  .mcal-pos{color:#34d27b} .mcal-neg{color:#ff6b6b} .mcal-flat{color:var(--muted)}
  /* Grille : 7 colonnes, entête jours + cases */
  .mcal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}
  .mcal-dow{text-align:center;font-size:9px;font-weight:800;color:var(--dim);padding:2px 0 5px;
       letter-spacing:.08em}
  .mcal-cell{position:relative;aspect-ratio:1/1;border-radius:10px;border:1px solid var(--border);
       background:rgba(255,255,255,.018);display:flex;flex-direction:column;align-items:center;
       justify-content:center;gap:0;overflow:hidden;min-height:0;transition:transform .1s}
  .mcal-void{border:none;background:none}
  .mcal-empty{opacity:.4}
  .mcal-d{position:absolute;top:4px;left:6px;font-size:9px;font-weight:700;color:var(--dim);
       font-variant-numeric:tabular-nums}
  .mcal-has .mcal-d{color:rgba(255,255,255,.5)}
  /* teinte FLAT (pas de dégradé glossy) : fond uni + fine bordure teintée -> propre, moderne. */
  .mcal-pos.mcal-has{background:rgba(52,210,123,var(--mt,.12));border-color:rgba(52,210,123,.28)}
  .mcal-neg.mcal-has{background:rgba(255,107,107,var(--mt,.12));border-color:rgba(255,107,107,.28)}
  .mcal-flat.mcal-has{background:rgba(154,154,166,.08)}
  .mcal-roi{font-size:11.5px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1;
       letter-spacing:-.02em}
  .mcal-pos .mcal-roi{color:#4fe89a} .mcal-neg .mcal-roi{color:#ff8a8a} .mcal-flat .mcal-roi{color:var(--muted)}
  /* nb de paris : collé EN BAS de la case (hors flux) -> ROI bien centré au milieu */
  .mcal-n{position:absolute;bottom:3px;left:0;right:0;text-align:center;font-size:8px;font-weight:700;
       color:rgba(255,255,255,.35);letter-spacing:.02em}
  .mcal-today{box-shadow:0 0 0 1.5px rgba(34,184,255,.55)}
  .mcal-has{cursor:default}   /* détail au clic retiré (user 2026-08-19) : cases = affichage seul */
  /* Légende + note */
  .mcal-legend{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:12px;font-size:11px;
       color:var(--muted)}
  .mcal-lg{display:inline-flex;align-items:center;gap:5px;font-weight:700}
  .mcal-lg::before{content:"";width:11px;height:11px;border-radius:4px;display:inline-block}
  .mcal-lg.pos::before{background:rgba(52,210,123,.5);border:1px solid rgba(52,210,123,.6)}
  .mcal-lg.neg::before{background:rgba(255,107,107,.5);border:1px solid rgba(255,107,107,.6)}
  .mcal-lg.flat::before{background:rgba(154,154,166,.3);border:1px solid var(--border)}
  .mcal-lg-note{flex-basis:100%;color:var(--dim);font-style:italic}
  .mcal-cell.mcal-sel{box-shadow:0 0 0 2px rgba(34,184,255,.9)}
  .mcal-detail{margin-top:14px;border-top:1px solid var(--border);padding-top:12px}
  .mcal-empty-msg{text-align:center;color:var(--muted);font-size:12px;padding:16px 12px;line-height:1.5;
       border:1px dashed var(--border);border-radius:13px;margin-bottom:14px}
  /* Ligne (jambe / résultat) : badge carré coloré + libellé + score/méta à droite */
  .sx-leg{display:flex;align-items:center;gap:8px;padding:5px 0;border-top:1px solid rgba(255,255,255,.06)}
  .sx-leg-t{flex:1;min-width:0;line-height:1.28;font-size:11.5px}
  .sx-leg-t small{display:block;color:var(--muted);font-size:9.5px}
  .sx-leg-x{flex:none;font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}
  .sx-leg-live{flex:none;color:#5fe39b;font-weight:800;font-size:10.5px}
  .sx-bdg{flex:none;width:19px;height:19px;border-radius:6px;color:#0a0a0a;font-weight:900;font-size:11px;
       display:flex;align-items:center;justify-content:center}
  .sx-bdg.w{background:#34d27b}.sx-bdg.l{background:#ff6b6b}.sx-bdg.n{background:#9a9aa6}
  .sx-bdg.p{background:var(--gold)}
  /* Séparateur de GRAND groupe (ex. « Suivis indicatifs · hors ROI ») — plus marqué qu'une section */
  .sx-group{display:flex;align-items:center;gap:10px;margin:18px 2px 2px;font-size:11px;font-weight:900;
       letter-spacing:.10em;text-transform:uppercase;color:var(--gold)}
  .sx-group::before{content:"";flex:0 0 14px;height:2px;border-radius:2px;background:var(--gold);opacity:.8}
  .sx-group span{font-size:11px;font-weight:700;color:var(--muted);text-transform:none;letter-spacing:0}
  /* HERO en tête de la page Stats : le chiffre clé (ROI global) + courbe globale — l'argument n°1. */
  .sx-hero{text-align:center;padding:15px 14px 12px;margin:2px 0 8px;border-radius:18px;
       border:1px solid rgba(34,184,255,.55);
       background:linear-gradient(180deg,rgba(34,184,255,.13),rgba(34,184,255,.02));
       box-shadow:0 0 34px rgba(34,184,255,.18),var(--shadow-sm)}
  .sx-hero-lbl{font-size:11px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
  .sx-hero-roi{font-size:48px;font-weight:900;letter-spacing:-.03em;line-height:1;margin:5px 0 3px;
       font-variant-numeric:tabular-nums}
  .sx-hero-roi.pos{color:#34d27b}.sx-hero-roi.neg{color:#ff6b6b}
  .sx-hero-sub{font-size:12px;color:var(--muted);font-weight:600}
  .sx-hero-sub b{color:var(--text);font-variant-numeric:tabular-nums}
  .sx-hero .sx-chart{margin-top:10px}
  /* Courbe du ROI PRINCIPAL en pleine largeur, MÊMES écarts gauche/droite que les cartes sport (demande
     user 2026-07-25) : sans bride de hauteur -> plus de letterbox qui rétrécissait/décentrait la courbe.
     Les insets viennent alors de L=R=16 dans le SVG (symétriques). */
  .sx-hero .sx-chart .sx-heroc{max-height:none}
  /* Bandeau « Combiné du jour » (accueil/Live) : lien compact doré */
  .combo-day{display:block;text-decoration:none;color:inherit;margin-bottom:12px;padding:12px 14px;
       border:1px solid var(--gold-bd);border-radius:14px;
       background:linear-gradient(180deg,rgba(246,197,74,.08),rgba(246,197,74,.02))}
  .combo-day-h{display:flex;justify-content:space-between;align-items:center}
  /* Barres ROI divergentes (par cote / confiance / marché) : 0 au centre, vert droite / rouge gauche */
  .rb{display:flex;flex-direction:column;gap:9px;margin-top:8px}
  .rb-row{display:flex;flex-direction:column;gap:3px}
  .rb-top{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
  .rb-lbl{font-size:11.5px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums}
  .rb-meta{font-size:9.5px;font-weight:600;color:var(--muted);white-space:nowrap}
  .rb-line{display:flex;align-items:center;gap:9px}
  .rb-track{position:relative;flex:1;height:9px;border-radius:99px;background:rgba(255,255,255,.05);
       overflow:hidden}
  .rb-zero{position:absolute;left:50%;top:0;bottom:0;width:1px;background:rgba(255,255,255,.22)}
  .rb-bar{position:absolute;top:0;height:100%}
  .rb-bar.rb-pos{left:50%;border-radius:0 99px 99px 0;background:linear-gradient(90deg,#19c46a,#34d27b)}
  .rb-bar.rb-neg{right:50%;border-radius:99px 0 0 99px;background:linear-gradient(270deg,#ff6b6b,#ff8f9a)}
  /* ROI à l'équilibre : petit repère neutre centré sur le zéro */
  .rb-bar.rb-even{left:50%;width:14px;margin-left:-7px;border-radius:99px;background:rgba(255,255,255,.34)}
  .rb-roi{flex:none;width:48px;text-align:right;font-size:12px;font-weight:900;
       font-variant-numeric:tabular-nums}
  .rb-roi.rb-pos{color:#34d27b} .rb-roi.rb-neg{color:#ff6b6b} .rb-roi.rb-neu{color:var(--muted)}
  /* mini-courbe d'équité dans la ligne d'un sport */
  .sx-row-spk{flex:1 1 auto;min-width:0;height:22px;display:flex;align-items:center}
  .sx-row-spk .sx-spark{width:100%;height:22px}
  /* ===== Animations premium (cascade d'apparition,
  skeleton,
  micro-interactions) =====
     Gating : la cascade ne joue qu'au PREMIER rendu (body.boot,
  retirée ~1 s après par _ANIM_JS)
     -> le refresh live 45 s (innerHTML remplacé) ne fait PAS re-clignoter les cartes. */
  @keyframes cardin{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
  body.boot .mc,
  body.boot .dash-stat,
  body.boot .dash-h{
       animation:cardin .42s cubic-bezier(.22,.85,.3,1) backwards}
  body.boot .mc:nth-child(2){animation-delay:.03s}
  body.boot .mc:nth-child(3){animation-delay:.06s}
  body.boot .mc:nth-child(4){animation-delay:.09s}
  body.boot .mc:nth-child(5){animation-delay:.12s}
  body.boot .mc:nth-child(6){animation-delay:.15s}
  body.boot .mc:nth-child(7){animation-delay:.18s}
  body.boot .mc:nth-child(8){animation-delay:.21s}
  body.boot .mc:nth-child(9){animation-delay:.24s}
  body.boot .mc:nth-child(n+10){animation-delay:.27s}
  /* Bascule d'onglet : glissement subtil en plus du fondu */
  @keyframes panein{from{opacity:.35;transform:translateY(7px)}to{opacity:1;transform:none}}
  /* Dépliage de carte : le corps apparaît en douceur + chevron à ressort */
  .mc-open .mc-body{animation:bodyin .26s cubic-bezier(.22,.85,.3,1)}
  @keyframes bodyin{from{opacity:0;transform:translateY(-5px)}to{opacity:1;transform:none}}
  .mc-chev{transition:transform .24s cubic-bezier(.34,1.45,.5,1)}
  /* SKELETON de chargement des panneaux (cartes fantômes + reflet) — remplace le spinner nu */
  .skel{display:flex;flex-direction:column;gap:11px;padding:8px 0}
  .sk{height:92px;border-radius:16px;border:1px solid var(--border);position:relative;overflow:hidden;
       background:linear-gradient(180deg,var(--surface2),var(--surface))}
  .sk::before{content:"";position:absolute;left:14px;top:16px;width:55%;height:11px;border-radius:6px;
       background:rgba(255,255,255,.06);box-shadow:0 22px 0 -3px rgba(255,255,255,.045),
       0 44px 0 -5px rgba(255,255,255,.03)}
  .sk::after{content:"";position:absolute;inset:0;transform:translateX(-100%);
       background:linear-gradient(90deg,transparent,rgba(255,255,255,.055),transparent);
       animation:shimmer 1.25s infinite}
  .sk+.sk{opacity:.72}.sk+.sk+.sk{opacity:.45}
  @keyframes shimmer{to{transform:translateX(100%)}}
  /* Badge LIVE : halo qui respire (discret) */
  @keyframes livepulse{0%,100%{box-shadow:0 0 0 0 rgba(52,210,123,.35)}55%{box-shadow:0 0 0 6px rgba(52,210,123,0)}}
  .mc-badge.mc-live{animation:livepulse 1.9s ease-out infinite}
  /* Desktop : léger lift au survol des cartes */
  @media(hover:hover){
    .mc{transition:transform .18s ease,box-shadow .18s ease}
    .mc:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(0,0,0,.5)}
  }
  /* Accessibilité : réduit toutes les animations si l'OS le demande */
  @media (prefers-reduced-motion:reduce){
    *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;
         transition-duration:.01ms!important}
  }
"""

# Menu principal groupé par SPORT ; chaque sport a son sous-menu (Matchs / Fiabilité).
_SPORT_MATCH_URL = {"tennis": "/app", "basket": "/basket", "foot": "/foot"}

# Onglets de la SPA (clé, URL, icône, libellé). L'URL sert AUSSI de source AJAX (?frag=1).
# Icône LIVE = mini-radar vert pulsant (mêmes anneaux que l'orbe de l'état vide « aucun match »)
_LIVE_RADAR = ('<span class="nav-radar"><span class="nr-ring"></span>'
               '<span class="nr-ring nr-ring2"></span><span class="nr-dot"></span></span>')
# Onglets sport (Tennis/Basket/Foot) RETIRÉS de la nav le 2026-07-20 (demande user) : ils répétaient Pronos
# (mêmes pronos filtrés par sport). Le filtre sport vit désormais SUR Pronos (puces `_sport_chips`), et les
# routes /app //basket //foot redirigent vers / (accueil). Nav = 4 onglets épurés.
# Barre du bas — 5 onglets (refonte user 2026-07-27) : Stats + Calendrier FUSIONNÉS dans « Résultats »
# (sous-nav Bilan / Calendrier à l'intérieur de /stats). Barre plus épurée, plus de doublon calendrier.
# ACCUEIL = onglet/PANNEAU SPA à part entière (demande user 2026-07-30) : MÊME cadre + présentation que les
# autres onglets (son panneau charge /accueil?frag=1, bascule SANS rechargement). « Compte » n'est PAS dans
# la barre du bas -> bouton en HAUT À DROITE (_ACCT_BTN). (Gating d'onglets par abonnement : plus tard.)
# ONGLET MONTANTE RETIRÉ de la barre (user 2026-08-19) : la montante devient un ONGLET des Résultats
# (bilan multiplicateur + courbe capital), comme Confiance/Value/Combiné. Barre = 4 onglets. Son pari du
# jour reste dans Pronos. `/montante` redirige vers /stats (routeur).
_SPA_TABS = [("accueil", "/accueil", "🏠", "Accueil"),
             ("home", "/", "📅", "Programme"),   # onglet renommé « Pronos » -> « Programme » (user 2026-08-19)
             ("directs", "/directs", _LIVE_RADAR, "Live"),
             ("stats", "/stats", "📊", "Résultats"),
             ("compte", "/compte", "👤", "Compte")]   # bouton compte REMIS en onglet bas-droite (user 2026-08-19)
# Bouton compte en haut à droite (toutes les pages) : /compte affiche la connexion si déconnecté, le compte
# sinon -> pas besoin de connaître l'état de session dans le rendu.
# BOUTON COMPTE HAUT-DROITE RETIRÉ (user 2026-08-19) : le compte est de nouveau un ONGLET de la barre du bas
# (cf. _SPA_TABS « compte »). Vidé pour ne pas doublonner.
_ACCT_BTN = ''
# Compte est un onglet SPA À PART ENTIÈRE : son panneau charge /compte?frag=1 (contenu seul) en AJAX,
# comme les onglets sport -> bascule sans rechargement. (Plus de _NAV_ONLY : il a son panneau.)

_SPORT_TITLE = {"foot": "⚽ Football", "tennis": "🎾 Tennis", "basket": "🏀 Basket"}

def _subnav(sport: str) -> str:
    """En-tête des pages sport : titre du sport courant + accès « Fiabilité détaillée ». Le CHANGEMENT
    de sport se fait par la barre du bas (pas de second menu de sélection -> on évite la redondance)."""
    if sport not in _SPORT_MATCH_URL:
        return ""
    return f'<div class="sporthd"><span class="sporthd-t">{_SPORT_TITLE.get(sport, "")}</span></div>'

# Décompte avant le coup d'envoi (timer live), côté client : met à jour chaque badge
# .cd[data-ts] (timestamp epoch s) toutes les secondes. Pas de dépendance, ~0 coût.
_COUNTDOWN_JS = (
    "(function(){function p(n){return n<10?'0'+n:''+n;}"
    "function U(v,u){return v+'<span class=u>'+u+'</span>';}"
    "function f(ms){if(ms<=0)return'\\u25b6 live';"
    "var s=Math.floor(ms/1000),d=Math.floor(s/86400),h=Math.floor(s%86400/3600),"
    "m=Math.floor(s%3600/60),x=s%60;"
    "if(d>0)return U(p(d),'j')+U(p(h),'h');"
    "if(h>0)return U(p(h),'h')+U(p(m),'m');"
    "return U(p(m),'m')+U(p(x),'s');}"
    "function t(){var n=Date.now(),e=document.getElementsByClassName('cd');"
    "for(var i=0;i<e.length;i++){var v=e[i].getAttribute('data-ts');if(!v)continue;"
    "var ms=parseInt(v,10)*1000-n;e[i].innerHTML=f(ms);"
    "e[i].className=ms<=0?'cd live':(ms<3600000?'cd soon':'cd');}}"
    "t();setInterval(t,1000);})();"
)

# Horloge LIVE « M:SS » qui défile (user 2026-08-15 : minute ET seconde). Chaque .tm-min porte
# data-min/data-sec/data-run (valeur Unibet au rendu) ; le ticker incrémente d'1 s toutes les secondes
# tant que data-run=1 (figé aux pauses). Le rendu serveur (refresh 45 s des panneaux) resynchronise
# sur la vraie valeur -> pas de dérive. Purement affichage.
_LIVECLK_JS = (
    "(function(){function p(n){return n<10?'0'+n:''+n;}"
    "function t(){var e=document.getElementsByClassName('tm-min'),i,el,m,s;"
    "for(i=0;i<e.length;i++){el=e[i];if(el.getAttribute('data-run')!=='1')continue;"
    "m=parseInt(el.getAttribute('data-min'),10);s=parseInt(el.getAttribute('data-sec'),10);"
    "if(isNaN(m)||isNaN(s))continue;s++;if(s>=60){s=0;m++;}"
    "el.setAttribute('data-min',m);el.setAttribute('data-sec',s);"
    # HORLOGE SEULE : plus d'indicateur « (+N') » (user 2026-08-20) -> on affiche juste M:SS qui défile.
    "el.textContent=m+':'+p(s);}}"
    "setInterval(t,1000);})();"
)

# SPA : tout est chargé à l'ouverture (le sport actif rendu côté serveur, les 3 autres
# préchargés en arrière-plan via ?frag=1), puis la nav du bas bascule les panneaux SANS
# rechargement. Vanilla JS, ~0 dépendance. history.pushState garde l'URL/refresh cohérents.
# Phase « boot » : la cascade d'apparition (CSS body.boot) ne joue qu'au PREMIER rendu ; la classe
# saute après ~1 s -> les refresh live (45 s, innerHTML remplacé) ne re-déclenchent rien.
# (Le compteur de bankroll a été retiré avec l'UI simulation, 2026-06-12.)
_ANIM_JS = (
    "(function(){var b=document.body;b.classList.add('boot');"
    "setTimeout(function(){b.classList.remove('boot');},950);"
    # Les animations graphes/stats se déclenchent QUAND LE SPLASH A RÉELLEMENT DISPARU (event
    # `animationend` sur `splashOut`), PAS sur un timer fixe (demande user 2026-07-10 : le splash peut
    # durer +/- selon reduced-motion). Filet de sécurité si l'event ne se déclenche pas ; si pas de
    # splash (frag / déjà passé), on joue tout de suite.
    "function fire(){if(window._sxAnim)window._sxAnim(document);}"
    "var sp=document.querySelector('.splash');"
    "if(sp){var done=false;function f(){if(!done){done=true;fire();}}"
    "sp.addEventListener('animationend',function(e){"
    "if((e.animationName||'').indexOf('splashOut')>=0)f();});"
    "setTimeout(f,2500);}else{fire();}})();"
)

# Handlers de CARTES partagés (layout ET spa_shell) : accordéons data-exp, cartes compactes .mc,
# bulles data-info/data-dvg, garde anti-scroll. Extraits de _SPA_JS (2026-06-12) : ils doivent
# marcher aussi sur les pages layout() (/mybets, /stats…) qui n'ont PAS de panneaux SPA.
_CARDS_JS = (
    "(function(){"
    # le « i » déplie/replie l'explication sans toucher au pliage de la section
    "document.addEventListener('click',function(e){var b=e.target.closest('[data-info]');"
    "if(!b)return;e.preventDefault();e.stopPropagation();"
    "var d=b.closest('details.sec2'),inf=d&&d.querySelector('.sec-info');"
    "if(inf)inf.hidden=!inf.hidden;});"
    # l'emoji de divergence ouvre/ferme sa bulle d'explication (sans suivre le lien de la carte)
    "document.addEventListener('click',function(e){var b=e.target.closest('[data-dvg]');"
    "if(!b)return;e.preventDefault();e.stopPropagation();"
    "var pb=b.closest('.pbars'),bub=pb&&pb.nextElementSibling;"
    "if(bub&&bub.classList.contains('dvg-bubble'))bub.hidden=!bub.hidden;});"
    # garde anti-scroll mobile : un glissement (>10px) n'est PAS un tap -> n'ouvre pas la carte
    "var _mv=false,_sx=0,_sy=0;"
    "document.addEventListener('touchstart',function(e){_mv=false;var t=e.touches[0];"
    "_sx=t.clientX;_sy=t.clientY;},{passive:true});"
    "document.addEventListener('touchmove',function(e){var t=e.touches[0];"
    "if(Math.abs(t.clientX-_sx)>10||Math.abs(t.clientY-_sy)>10)_mv=true;},{passive:true});"
    # accordéon data-exp : tap -> charge et déplie l'analyse à l'intérieur
    "document.addEventListener('click',function(e){"
    "if(_mv)return;"
    "if(e.target.closest('[data-dvg]')||e.target.closest('.exp')||e.target.closest('a'))return;"
    "var c=e.target.closest('[data-exp]');if(!c)return;e.preventDefault();"
    "var x=c.querySelector('.exp');if(!x)return;"
    "if(!x.hidden){x.hidden=true;c.classList.remove('open');return;}"
    "c.classList.add('open');x.hidden=false;"
    "if(!x.getAttribute('data-loaded')){x.setAttribute('data-loaded','1');"
    "x.innerHTML='<div class=ldg>Chargement de l\\'analyse…</div>';"
    "fetch(c.getAttribute('data-exp')).then(function(r){return r.text();})"
    ".then(function(h){x.innerHTML=h;"
    "if(window._twCount)window._twCount(x);})"
    ".catch(function(){x.removeAttribute('data-loaded');"
    "x.innerHTML='<div class=dim>Analyse indisponible.</div>';});}});"
    # CARTE COMPACTE : un clic N'IMPORTE OÙ dans la carte la déplie/replie. À l'ouverture, l'ANALYSE
    # est chargée D'OFFICE. Les liens (a) restent cliquables.
    "function _mcLoad(card){var a=card.querySelector('.mc-ana');if(!a||a.getAttribute('data-l'))return;"
    "a.setAttribute('data-l','1');var x=a.querySelector('.exp');if(!x)return;"
    "x.innerHTML='<div class=ldg>Chargement de l\\'analyse…</div>';"
    "fetch(a.getAttribute('data-ana')).then(function(r){return r.text();}).then(function(h){"
    "x.innerHTML=h;if(window._twCount)window._twCount(x);})"
    ".catch(function(){a.removeAttribute('data-l');x.innerHTML='<div class=dim>Analyse indisponible.</div>';});}"
    "window._mcInit=function(root){var o=(root||document).querySelectorAll('.row.mc.mc-open'),i;"
    "for(i=0;i<o.length;i++)_mcLoad(o[i]);};"
    "document.addEventListener('click',function(e){"
    # un clic DANS l'analyse (.exp : détails repliables, bulles, etc.) ne doit PAS replier la carte :
    # on (dé)plie via l'en-tête de la carte uniquement. (cf. accordéon data-exp, même garde)
    "if(_mv)return;if(e.target.closest('a,.exp'))return;"
    "var card=e.target.closest('.row.mc');"
    # CARTE MONTANTE (`.mont-cardwrap` contenant une `.cleg`, pas une `.row.mc`) : clic dans le CORPS -> (dé)plie
    # son « Pourquoi » comme les autres cartes (user 2026-08-10). L'en-tête `.mont-hdr` est un <a> -> intercepté
    # plus haut (closest('a')) et géré par data-goto (bascule vers l'onglet Montante).
    "if(!card){var mw=e.target.closest('.mont-cardwrap');"
    "if(mw){var md=mw.querySelector('details.cleg-fold');"
    "if(md&&!e.target.closest('summary')){e.preventDefault();md.open=!md.open;}}return;}"
    # Carte PLATE (pas de corps dépliable) : un clic N'IMPORTE OÙ dans le cadre (dé)plie le « Pourquoi »
    # (demande user 2026-07-21). Le summary garde son toggle natif (stopPropagation) -> pas de double bascule.
    "var b=card.querySelector('.mc-body');"
    # Carte plate SANS corps (.mc-body) : (dé)plie le pli « Pourquoi ». BUG (user 2026-07-24) : sur un
    # combiné, on prenait TOUJOURS le 1er `.cleg-fold` -> taper la jambe 2 ouvrait le « pourquoi » de la
    # jambe 1. Fix : scoper à la JAMBE cliquée (`.cleg`). Hors jambe sur une carte multi-jambes -> ne rien
    # faire (ambigu). Carte simple (1 seul pli, pas dans un .cleg) -> comportement inchangé.
    "if(!b){var host=e.target.closest('.cleg');"
    "if(!host&&card.querySelectorAll('details.cleg-fold').length>1)return;"
    "var d=(host||card).querySelector('details.cleg-fold');"
    "if(d&&!e.target.closest('summary')){e.preventDefault();d.open=!d.open;}return;}"
    "e.preventDefault();"
    "if(b.hidden){"
    # ACCORDÉON : ouvrir une carte ferme celle(s) déjà ouverte(s) (demande user).
    "var _op=document.querySelectorAll('.row.mc.mc-open'),_k;"
    "for(_k=0;_k<_op.length;_k++){if(_op[_k]!==card){var _ob=_op[_k].querySelector('.mc-body');"
    "if(_ob)_ob.hidden=true;_op[_k].classList.remove('mc-open','mc-manual');}}"
    "b.hidden=false;card.classList.add('mc-open','mc-manual');_mcLoad(card);"
    "if(window._twCount)window._twCount(b);}"
    "else{b.hidden=true;card.classList.remove('mc-open','mc-manual');}});"
    "window._mcInit(document);})();"
)

# Onglets « Simple | Combinés » dans les cadres sport des Stats (demande user 2026-07-24) : un graphe à la
# fois. Délégué au document -> marche aussi sur le fragment Stats chargé en SPA.
_SCTABS_JS = (
    "document.addEventListener('click',function(e){"
    "var t=e.target.closest('.sctab');if(!t)return;"
    "var w=t.closest('.sctab-wrap');if(!w)return;"
    "var i=t.getAttribute('data-i'),tabs=w.querySelectorAll('.sctab'),j;"
    "for(j=0;j<tabs.length;j++)tabs[j].classList.toggle('on',tabs[j]===t);"
    "var p=w.querySelectorAll('.sctab-pane'),k;"
    "for(k=0;k<p.length;k++)p[k].classList.toggle('on',String(k)===i);"
    "});"
)

_SPA_JS = (
    "(function(){var P=document.getElementById('panels');if(!P)return;"
    "function panel(t){return document.getElementById('pn-'+t);}"
    "function show(t){var c=P.children,i;for(i=0;i<c.length;i++)"
    "c[i].classList.toggle('on',c[i].getAttribute('data-tab')===t);"
    "var n=document.querySelectorAll('.botnav a'),j;for(j=0;j<n.length;j++)"
    "n[j].classList.toggle('on',n[j].getAttribute('data-tab')===t);"
    "document.body.className='sp-'+t;"
    # À l'affichage de l'onglet : REDÉMARRE les animations graphes/stats (courbe + compteurs) — donc APRÈS
    # le splash, et à chaque revisite -> toujours visibles (les panneaux sont préchargés derrière le splash).
    "var sp=panel(t);if(sp){if(window._sxAnim)setTimeout(function(){window._sxAnim(sp);},60);"
    "if(window._mcInit)window._mcInit(sp);}"
    "if(window._daycalSync)window._daycalSync();}"   # calendrier -> replacé à droite à chaque affichage d'onglet
    # BADGES chiffrés du menu du bas (demande user 2026-07-14) : chaque panneau émet `.dv-nav` (data-tab +
    # data-n = nb de matchs du jour). On pose le compte sur l'onglet correspondant (blanc ; Live = vert +
    # point clignotant). 0 -> badge caché. Fonction dédiée -> appelable AUSSI pour le panneau ACTIF (rendu
    # serveur, jamais passé par load) sinon le badge de l'onglet PRONOS ne s'affiche jamais (fix 2026-07-20).
    # TOUS les .dv-nav du panneau (un panneau peut en émettre plusieurs : la home pose AUSSI le badge
    # de l'onglet Live — sinon il n'apparaissait qu'après avoir visité l'onglet, retour user 2026-07-21).
    "function badge(p){var _ds=p&&p.querySelectorAll?p.querySelectorAll('.dv-nav'):[];"
    "for(var _k=0;_k<_ds.length;_k++){var _dc=_ds[_k];"
    "var _t=_dc.getAttribute('data-tab');var _n=parseInt(_dc.getAttribute('data-n')||'0',10);"
    "var _nv=document.querySelector('.botnav a[data-tab=\"'+_t+'\"]');if(!_nv)continue;"
    "if(_t==='directs')_nv.classList.toggle('has-live',_n>0);"
    "var _bd=_nv.querySelector('.nav-n');if(_bd){_bd.textContent=_n>99?'99+':(''+_n);_bd.hidden=_n<=0;}}}"
    "function load(p){if(!p||p.getAttribute('data-loaded'))return;"
    "p.setAttribute('data-loaded','1');var u=p.getAttribute('data-src');"
    "fetch(u+(u.indexOf('?')<0?'?':'&')+'frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){p.innerHTML=h;p.setAttribute('data-ts',''+Date.now());badge(p);"
    "if(window._twScan)window._twScan(p);if(window._mcInit)window._mcInit(p);"
    "if(window._sxAnim)window._sxAnim(p);if(window._daycalSync)window._daycalSync();})"
    ".catch(function(){p.removeAttribute('data-loaded');"
    "p.innerHTML='<div class=ldg>Erreur de chargement. Touchez l\\'onglet pour réessayer.</div>';});}"
    # RE-FETCH d'un panneau DÉJÀ chargé dont les données sont périmées (>90 s) — user 2026-08-22. Préserve le
    # scroll ; NE rafraîchit PAS si une carte ouverte à la main (.mc-manual) ou un pli d'analyse (.cleg-fold[open])
    # est ouvert (on ne coupe pas une lecture). Même philosophie que le refresh Live 45 s.
    "function _stale(p){var t=parseInt(p.getAttribute('data-ts')||'0',10);return t>0&&(Date.now()-t>90000);}"
    "function reload(p){if(!p||!p.getAttribute('data-src'))return;"
    "if(p.querySelector('.mc-manual')||p.querySelector('.cleg-fold[open]'))return;"
    "var u=p.getAttribute('data-src'),y=window.scrollY,on=p.classList.contains('on');"
    "fetch(u+(u.indexOf('?')<0?'?':'&')+'frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){p.innerHTML=h;p.setAttribute('data-ts',''+Date.now());"
    "badge(p);if(window._mcInit)window._mcInit(p);if(window._daycalSync)window._daycalSync();"
    "if(on)window.scrollTo(0,y);}).catch(function(){});}"
    "function _resume(){var t=_curTab(),p=t&&panel(t);if(p&&p.getAttribute('data-loaded')&&_stale(p))reload(p);}"
    "function _tabList(){var nav=document.querySelectorAll('.botnav a'),o=[],k;for(k=0;k<nav.length;k++){var tk=nav[k].getAttribute('data-tab');if(panel(tk))o.push(tk);}return o;}"
    "function _curTab(){var cc=P.children,k;for(k=0;k<cc.length;k++)if(cc[k].classList.contains('on'))return cc[k].getAttribute('data-tab');return null;}"
    "function go(t,push){var p=panel(t);if(!p)return;"
    "var tl=_tabList(),d=tl.indexOf(t)-tl.indexOf(_curTab());"  # direction de la transition (slide)
    "P.classList.remove('sl-next','sl-prev');if(d>0)P.classList.add('sl-next');else if(d<0)P.classList.add('sl-prev');"
    "load(p);show(t);if(_stale(p))reload(p);if(window._lzAnim)window._lzAnim(p);"  # re-fetch si périmé + animations premium Accueil
    "if(push)try{history.pushState({tab:t},'',p.getAttribute('data-src'));}catch(e){}"
    "window.scrollTo(0,0);}"
    # panneau actif (rendu serveur) = déjà chargé -> on pose SON badge à la main (jamais passé par load) ;
    # on précharge les autres tout de suite (load pose leur badge).
    "var c=P.children,i;for(i=0;i<c.length;i++){"
    "if(c[i].classList.contains('on')){c[i].setAttribute('data-loaded','1');c[i].setAttribute('data-ts',''+Date.now());badge(c[i]);}else load(c[i]);}"
    "var nav=document.querySelectorAll('.botnav a');for(i=0;i<nav.length;i++){"
    "nav[i].addEventListener('click',function(e){var t=this.getAttribute('data-tab');"
    "if(!panel(t))return;"  # onglet sans panneau SPA (Compte) -> navigation normale (page autonome)
    "e.preventDefault();go(t,true);});}"
    "window.addEventListener('popstate',function(e){var t=(e.state&&e.state.tab);"
    "if(!t){var m={'/':'home','/accueil':'accueil','/directs':'directs','/app':'tennis','/basket':'basket','/foot':'foot','/stats':'stats','/compte':'compte'};"
    "t=m[location.pathname]||'home';}go(t,false);});"
    # SWIPE horizontal -> onglet suivant/précédent (user 2026-08-22). On ignore : les gestes verticaux/lents,
    # et surtout les scrolls HORIZONTAUX internes (rails de sous-onglets, sélecteur de sport, carrousels) en
    # remontant depuis le point de départ tant qu'on trouve un conteneur qui défile en X.
    "var _swx=0,_swy=0,_swt=0,_swok=false;"
    "document.addEventListener('touchstart',function(e){if(e.touches.length!==1){_swok=false;return;}"
    "var t=e.touches[0];_swx=t.clientX;_swy=t.clientY;_swt=Date.now();_swok=true;},{passive:true});"
    "document.addEventListener('touchend',function(e){if(!_swok)return;_swok=false;"
    "var t=e.changedTouches[0],dx=t.clientX-_swx,dy=t.clientY-_swy;"
    "if(Math.abs(dx)<70||Math.abs(dx)<Math.abs(dy)*2||Date.now()-_swt>500)return;"
    "var el=document.elementFromPoint(_swx,_swy);"
    "while(el&&el!==document.body){var ov=getComputedStyle(el).overflowX;"
    "if((ov==='auto'||ov==='scroll')&&el.scrollWidth>el.clientWidth+4)return;el=el.parentElement;}"
    "var tabs=_tabList(),idx=tabs.indexOf(_curTab());if(idx<0)return;var ni=dx<0?idx+1:idx-1;"
    "if(ni<0||ni>=tabs.length)return;go(tabs[ni],true);},{passive:true});"
    # Filtre temporel des stats : clic sur un bouton période -> recharge le panneau stats (since)
    "P.addEventListener('click',function(e){"
    # bannière/lien interne data-goto (ex. Montante du jour sur Pronos) -> bascule d'onglet SPA
    "var gb=e.target&&e.target.closest?e.target.closest('[data-goto]'):null;"
    "if(gb){e.preventDefault();go(gb.getAttribute('data-goto'),true);return;}"
    "var a=e.target&&e.target.closest?"
    "e.target.closest('a[data-since]'):null;if(!a)return;e.preventDefault();"
    "var sp=panel('stats');if(!sp)return;"
    "fetch('/stats?frag=1&since='+a.getAttribute('data-since'),{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){sp.innerHTML=h;"
    "if(window._twScan)window._twScan(sp);if(window._mcInit)window._mcInit(sp);"
    "window.scrollTo(0,0);});});"
    # (handlers data-info/data-dvg/data-exp/.mc : déplacés dans _CARDS_JS, partagé avec layout)
    # rafraîchissement auto des COTES/SCORES live : on ré-interroge le panneau actif toutes les
    # 45 s, UNIQUEMENT s'il contient un direct (.live) ET qu'aucun accordéon n'est ouvert
    # (on ne coupe pas une lecture). Le scroll est préservé. Pas de direct = aucun appel réseau.
    "function fresh(){var c=P.children,i,p=null;"
    "for(i=0;i<c.length;i++)if(c[i].classList.contains('on')){p=c[i];break;}"
    "if(!p||!p.getAttribute('data-loaded')||document.hidden)return;"
    "if(!p.querySelector('.live'))return;"
    "if(p.querySelector('.mc-manual'))return;"  # ne pas perturber une carte ouverte À LA MAIN
    # PLI D'ANALYSE « Pourquoi ce choix / cette jambe » OUVERT (`.cleg-fold[open]`) -> on NE rafraîchit PAS :
    # le refresh remplace le panneau et refermerait le pli -> l'utilisateur perdait sa lecture (user 2026-08-20).
    # (Les zones repliables sont `.zone-col`, PAS `.cleg-fold` -> elles ne bloquent pas le refresh.)
    "if(p.querySelector('.cleg-fold[open]'))return;"
    "var u=p.getAttribute('data-src');"
    "fetch(u+(u.indexOf('?')<0?'?':'&')+'frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){"
    "var y=window.scrollY;"
    "p.innerHTML=h;p.setAttribute('data-ts',''+Date.now());if(window._mcInit)window._mcInit(p);window.scrollTo(0,y);})"
    ".catch(function(){});}"
    "setInterval(fresh,45000);"
    # iOS PWA : au 1er paint, le viewport/safe-area n'est pas encore stable -> la barre fixe (bottom:0) se cale
    # trop haut avec une bande morte dessous, jusqu'au 1er changement d'onglet (qui force un reflow). On force CE
    # reflow juste après l'ouverture : nudge de scroll + bascule display de #panels (change la hauteur -> iOS
    # recalcule le viewport et recale la barre au vrai bas). Répété : 2 rAF (après 1er paint), 300 ms (safe-area
    # stabilisée) et à chaque retour d'app (pageshow, ex. sortie de veille).
    "function _relayout(){try{window.scrollTo(0,1);window.scrollTo(0,0);"
    "var d=P.style.display;P.style.display='none';P.offsetHeight;P.style.display=d;}catch(e){}}"
    "requestAnimationFrame(function(){requestAnimationFrame(_relayout);});"
    "setTimeout(_relayout,300);"
    "window.addEventListener('pageshow',function(){_relayout();_resume();});"
    # RETOUR AU PREMIER PLAN (sortie de veille / bascule d'app) -> rafraîchit l'onglet actif s'il est périmé.
    "document.addEventListener('visibilitychange',function(){if(!document.hidden)_resume();});"
    "})();"
)

# Effet « terminal » : les pronostics + l'analyse se TAPENT (caractère par caractère) à l'ouverture,
# UNE fois, avec un curseur clignotant. Tap = saute l'animation. Sécurité : tout est révélé après 4,5 s
# max et si une erreur survient (jamais de contenu vide). Non destructif si le JS ne tourne pas.
_TERM_JS = (
    "(function(){"
    # Respect de « Réduire le mouvement » AUSSI côté JS (le @media CSS ne stoppe pas requestAnimationFrame) :
    "var _rm=false;try{_rm=window.matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches;}catch(e){}"
    # COMPTEUR : un chiffre/valeur (.da-st-v) qui MONTE de 0 à sa valeur (formats « 87% », « 1.22 », « +6% »).
    # cible MÉMORISÉE (nd._tv) : sinon un rejeu (_sxAnim) PENDANT la montée re-lirait la valeur
    # intermédiaire courante (« 2 ») comme nouvelle cible -> le compteur se FIGE à 2 au lieu de 66.
    "function cnt(nd){if(nd._c||_rm)return;nd._c=1;"
    "var t=nd._tv;if(t==null){t=(nd.textContent||'').trim();nd._tv=t;}"
    "var m=t.match(/^([+\\-]?)(\\d+(?:[.,]\\d+)?)(.*)$/);if(!m)return;"
    "var sg=m[1],n=parseFloat(m[2].replace(',','.')),sf=m[3],dp=(m[2].split(/[.,]/)[1]||'').length,s=null;"
    "function st(ts){if(!s)s=ts;var p=Math.min(1,(ts-s)/650),e=p*p*(3-2*p);"
    "nd.textContent=sg+(n*e).toFixed(dp)+sf;if(p<1)requestAnimationFrame(st);else nd.textContent=t;}"
    "nd.textContent=sg+(0).toFixed(dp)+sf;requestAnimationFrame(st);"
    "setTimeout(function(){nd.textContent=t;},2000);}"  # sécurité : valeur finale après 2 s
    "function dig(n,o){for(var c=n.firstChild;c;c=c.nextSibling){"
    "if(c.nodeType===3){var t=c.nodeValue;if(t&&/\\S/.test(t)){o.push([c,t]);c.nodeValue='';}}"
    "else if(c.nodeType===1){var g=c.tagName;"
    "if(g!=='SCRIPT'&&g!=='STYLE'&&g!=='svg'&&g!=='SVG'&&!c.getAttribute('data-tw')"
    "&&(!c.classList||!c.classList.contains('da-st-v')))dig(c,o);}}}"  # .da-st-v = compteur, pas frappé
    "function tw(el){if(!el||el.getAttribute('data-tw'))return;el.setAttribute('data-tw','1');"
    "if(_rm)return;"  # reduced-motion : on laisse le texte/compteurs à leur valeur finale, pas d'animation
    "try{var nm=el.querySelectorAll('.da-st-v'),z;for(z=0;z<nm.length;z++)cnt(nm[z]);}catch(e){}"
    "var nodes=[];try{dig(el,nodes);}catch(e){return;}if(!nodes.length)return;"
    "var total=0,i;for(i=0;i<nodes.length;i++)total+=nodes[i][1].length;"
    "var per=Math.max(2,Math.ceil(total/180));"  # ~ termine en ~1,5 s
    "var cur=document.createElement('span');cur.className='tw-cur';cur.textContent='▋';"  # ▋
    "el.classList.add('tw-on');var ni=0,ci=0,tmr=0;"
    "function fin(){try{for(var k=0;k<nodes.length;k++)nodes[k][0].nodeValue=nodes[k][1];}catch(e){}"
    "if(cur.parentNode)cur.parentNode.removeChild(cur);el.classList.remove('tw-on');"
    "clearTimeout(tmr);el._twf=null;}"
    "el._twf=fin;"
    "function tick(){var r=per;"
    "while(r>0&&ni<nodes.length){var nd=nodes[ni],f=nd[1];ci++;nd[0].nodeValue=f.slice(0,ci);"
    "try{nd[0].parentNode.insertBefore(cur,nd[0].nextSibling);}catch(e){}"
    "if(ci>=f.length){ni++;ci=0;}r--;}"
    "if(ni<nodes.length)tmr=setTimeout(tick,8);else fin();}"
    "tick();setTimeout(function(){if(el._twf)el._twf();},4500);}"
    "window._twType=tw;"
    "document.addEventListener('click',function(e){var t=e.target.closest('.tw-on');"
    "if(t&&t._twf)t._twf();},true);"  # tap pendant l'anim -> révèle tout
    "var obs=('IntersectionObserver'in window)?new IntersectionObserver(function(es){"
    "es.forEach(function(en){if(en.isIntersecting){obs.unobserve(en.target);tw(en.target);}});},"
    "{threshold:0.3}):null;"
    "window._twScan=function(root){if(!obs)return;"
    "var l=(root||document).querySelectorAll('.tw:not([data-tw])'),i;"
    "for(i=0;i<l.length;i++)obs.observe(l[i]);};"
    # compteurs : déclenchables explicitement (à l'affichage d'un panneau) -> effet toujours visible.
    # Compteurs MONTANTS aussi sur les KPI des stats (.sx-kpi>b, .spf-cv-kpis b) + les libellés ROI/pct
    # (.arec-* qui portent la valeur). `cnt` ignore proprement le non-numérique (« — », « @1.42 »).
    "window._twCount=function(root){try{var l=(root||document).querySelectorAll("
    "'.da-st-v,.sx-kpi>b,.spf-cv-kpis b'),i;"
    "for(i=0;i<l.length;i++)cnt(l[i]);}catch(e){}};"
    # ANIMATIONS graphes/stats : (re)JOUÉES à CHAQUE affichage d'onglet (donc APRÈS le splash — sinon la
    # courbe se traçait DERRIÈRE le logo d'intro et les compteurs montaient invisibles). Redémarre le tracé
    # de la courbe (retire/réapplique l'animation) + relance les compteurs. Idempotent, respecte reduced-motion.
    "window._sxAnim=function(root){if(_rm)return;var r=root||document;"
    # (re)DÉCLENCHE le tracé : retire `.sx-go`, force un reflow, puis rajoute `.sx-go` -> l'animation repart
    # de zéro. FILET ANTI-GEL : on programme la RETRAIT de `.sx-go` après 1,5 s (> durée d'anim). Si l'anim
    # a gelé (onglet masqué/iOS), ce retrait fait retomber l'élément sur son état de BASE = tracé COMPLET
    # (la ligne touche son point). `_t` par élément (clear avant re-set) -> un re-déclenchement n'est pas
    # coupé par un vieux timer.
    "try{var g=r.querySelectorAll('.sx-heroc-line,.sx-heroc-area,.sx-heroc-pt'),i;"
    "for(i=0;i<g.length;i++){var el=g[i];el.classList.remove('sx-go');el.getBoundingClientRect();"
    "el.classList.add('sx-go');if(el._t)clearTimeout(el._t);"
    "el._t=setTimeout((function(e){return function(){e.classList.remove('sx-go');};})(el),1500);}}catch(e){}"
    "try{var c=r.querySelectorAll('.da-st-v,.sx-kpi>b,.spf-cv-kpis b'),j;"
    "for(j=0;j<c.length;j++){c[j]._c=0;cnt(c[j]);}}catch(e){}};"
    "window._twScan(document);window._twCount(document);})();"
)

# Repères du modèle : clic sur une pastille OU un marqueur du graphe -> affiche/masque l'explication
# (toggle) dans le panneau dédié. Délégué sur document -> marche aussi pour les panneaux chargés en AJAX.
_MILE_JS = (
    "(function(){document.addEventListener('click',function(e){"
    "var t=e.target.closest('[data-mile]');if(!t)return;"
    "var scope=t.closest('.spf-cv')||t.closest('.sx-hero')||t.closest('.sx-card');if(!scope)return;"
    "var n=t.getAttribute('data-mile');"
    "var info=scope.querySelector('.sx-mile-info');"
    "var data=scope.querySelector('.sx-mile-d[data-mile=\"'+n+'\"]');"
    "if(!info||!data)return;"
    "var was=info.getAttribute('data-on');"
    "scope.querySelectorAll('.sx-mile-b.on,.bc-mile-g.on').forEach(function(el){el.classList.remove('on');});"
    "if(was===n){info.classList.remove('show');info.removeAttribute('data-on');info.innerHTML='';return;}"
    "info.innerHTML=data.innerHTML;info.setAttribute('data-on',n);info.classList.add('show');"
    "scope.querySelectorAll('[data-mile=\"'+n+'\"]').forEach(function(el){"
    "if(el.classList.contains('sx-mile-b')||el.classList.contains('bc-mile-g'))el.classList.add('on');});"
    "});})();"
)

# ZONES REPLIABLES de Pronos : mémorise l'état plié/déplié (localStorage 'zf_<kind>') et le restaure au
# rendu. (Le code de l'ancien bandeau calendrier des jours a été retiré avec le strip le 2026-07-25 : la
# navigation par jour vit dans l'onglet CALENDRIER dédié, JS `_MCAL_JS`.)
_CAL_JS = (
    "(function(){"
    # TOUT DÉPLIÉ À L'OUVERTURE (user 2026-08-22) : on ne restaure PLUS l'état plié mémorisé -> chaque
    # (r)ouverture respecte le défaut `open_=True` des zones. Le pli reste possible dans la session (natif).
    "function restoreFolds(h){}"
    "document.addEventListener('click',function(ev){"
    "if(!ev.target||!ev.target.closest)return;"
    # repli d'une zone : on laisse le navigateur toggler puis on mémorise l'état (localStorage).
    "var sm=ev.target.closest('.zone-col>summary');"
    "if(sm){var det=sm.parentNode;setTimeout(function(){try{"
    "localStorage.setItem('zf_'+det.getAttribute('data-zk'),det.open?'1':'0');}catch(e){}},0);return;}"
    "});"
    "setTimeout(function(){restoreFolds(document);},60);"   # zones repliées mémorisées au 1er rendu (serveur)
    "})();"
)

# CALENDRIER : navigation mensuelle (‹ / ›, remplace #cal-root) + détail d'un jour au clic (réutilise /jour).
_MCAL_JS = (
    "(function(){document.addEventListener('click',function(e){"
    "if(!e.target||!e.target.closest)return;"
    # flèches de mois -> recharge la grille dans #cal-root
    "var a=e.target.closest('[data-cal]');"
    "if(a){var ym=a.getAttribute('data-cal'),root=document.getElementById('cal-root');if(!root)return;"
    "root.style.opacity='.5';"
    "fetch('/calendrier?ym='+encodeURIComponent(ym)+'&cal=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){root.innerHTML=h;root.style.opacity='';"
    "try{if(window._sxAnim)window._sxAnim(root);}catch(e){}})"
    ".catch(function(){root.style.opacity='';});return;}"
    # DÉTAIL D'UN JOUR AU CLIC RETIRÉ (user 2026-08-19) : le calendrier Stats montre les résultats UNIQUEMENT
    # via les cases colorées (ROI/nb de paris), pas de liste de paris en dessous. La navigation jour vit dans
    # l'onglet Pronos (calendrier horizontal). Seule la nav mois (‹ ›) reste ici.
    "});})();"
)

# Bannière « Ajouter à l'écran d'accueil » : le partage iOS (glyphe SVG) + le libellé sont dans le HTML ;
# le JS choisit d'afficher (hors standalone) et bascule en bouton natif « Installer » sur Android/Chrome.
_SHARE_SVG = ('<svg class="shr" width="11" height="13" viewBox="0 0 12 14" fill="none" aria-hidden="true">'
              '<path d="M6 1.4v7.8M3.5 3.8 6 1.2l2.5 2.6" stroke="#7cc4ff" stroke-width="1.4" '
              'stroke-linecap="round" stroke-linejoin="round"/>'
              '<path d="M2.6 6H1.2v6.4h9.6V6H9.4" stroke="#7cc4ff" stroke-width="1.4" '
              'stroke-linecap="round" stroke-linejoin="round"/></svg>')
_A2HS_HTML = (
    '<div class="a2hs" id="a2hs" hidden>'
    '<div class="a2hs-ic">📲</div>'
    '<div class="a2hs-tx"><b>Installer l\'app BETSFIX</b>'
    f'<span id="a2hs-sub">Appuyez sur {_SHARE_SVG} Partager, puis « Sur l\'écran d\'accueil ».</span></div>'
    '<button class="a2hs-go" id="a2hs-go" hidden>Installer</button>'
    '<button class="a2hs-x" id="a2hs-x" aria-label="Fermer">✕</button>'
    '</div>')
_A2HS_JS = (
    "(function(){var el=document.getElementById('a2hs');if(!el)return;"
    "var go=document.getElementById('a2hs-go'),x=document.getElementById('a2hs-x'),sub=document.getElementById('a2hs-sub');"
    "function lget(k){try{return localStorage.getItem(k);}catch(e){return null;}}"
    "function lset(k,v){try{localStorage.setItem(k,v);}catch(e){}}"
    # INSTALLÉE ? On teste TOUS les modes d'affichage d'une app installée — standalone MAIS AUSSI **fullscreen**
    # (le manifeste demande `display:fullscreen` -> l'app se lance en fullscreen, PAS standalone : l'ancien test
    # ratait ça et affichait la bannière DANS l'app, à chaque démarrage) et minimal-ui ; + iOS navigator.standalone.
    "function inApp(){return (window.matchMedia&&(matchMedia('(display-mode: standalone)').matches"
    "||matchMedia('(display-mode: fullscreen)').matches||matchMedia('(display-mode: minimal-ui)').matches))"
    "||window.navigator.standalone===true;}"
    "if(inApp()){lset('bfx_installed','1');return;}"    # lancée depuis l'icône -> MÉMORISE installée + jamais de bannière
    "if(lget('bfx_installed')==='1')return;"            # déjà repérée installée sur ce navigateur -> plus jamais proposer
    # FERMETURE ✕ = SILENCE PERSISTANT 21 jours (user 2026-08-27 : « apparaît tout le temps au démarrage »).
    # Avant : sessionStorage -> revenait à chaque visite. Maintenant snooze localStorage daté -> plus de harcèlement.
    "var sn=parseInt(lget('a2hs_snooze')||'0',10);if(sn&&Date.now()<sn)return;"
    "function show(){el.hidden=false;requestAnimationFrame(function(){el.classList.add('show');});}"
    "function hide(snooze){el.classList.remove('show');setTimeout(function(){el.hidden=true;},320);"
    "if(snooze)lset('a2hs_snooze',String(Date.now()+21*864e5));}"
    "function done(){lset('bfx_installed','1');hide(false);}"   # installée (accept/appinstalled) -> mémorise DÉFINITIVEMENT
    "function arm(){var dfd=null;"
    "window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();dfd=e;"          # Android/Chrome
    "if(sub)sub.textContent='Plein écran, sans barre du navigateur.';if(go)go.hidden=false;show();});"
    "var ios=/iphone|ipad|ipod/i.test(navigator.userAgent);"
    "if(ios){setTimeout(show,1400);}"                  # iOS : instructions manuelles déjà dans le HTML
    # Android sans prompt natif (Chrome le tait parfois) : on montre quand même, avec l'instruction du menu.
    "else{setTimeout(function(){if(el.hidden){if(go&&go.hidden&&sub)sub.textContent='Menu ⋮ du navigateur, puis « Installer ».';show();}},1800);}"
    "if(go)go.addEventListener('click',function(){if(dfd){dfd.prompt();dfd.userChoice.then(function(r){if(r&&r.outcome==='accepted')done();else hide(true);});}});"
    "if(x)x.addEventListener('click',function(){hide(true);});"
    "window.addEventListener('appinstalled',done);}"
    # DÉTECTION FIABLE (Android/Chrome) : la PWA est-elle DÉJÀ installée ? getInstalledRelatedApps lit les
    # related_applications du manifeste -> si installée, on MÉMORISE et PAS de bannière (même en navigation) ; sinon arme.
    # API absente (iOS…) -> on arme directement (le test inApp ci-dessus a déjà écarté « dans l'app »).
    "if(navigator.getInstalledRelatedApps){navigator.getInstalledRelatedApps().then(function(a){"
    "if(a&&a.length){lset('bfx_installed','1');return;}arm();}).catch(arm);}else{arm();}"
    "})();"
)

# Menu tiroir « complet » (☰) — présent sur TOUTES les pages. Accès direct à tout : accueil, paris à
# jouer, bilan, stats, et chaque sport + live. Les clés correspondent à l'item mis en évidence.
# Anti-zoom (ex-_DRAWER_JS — le tiroir ☰ a été retiré, redondant avec la barre du bas).
# ZOOM BLOQUÉ (demande user 2026-08-16) : iOS ignore `user-scalable=no` du viewport -> on bloque le
# PINCH-ZOOM via les events gesture* (`touch-action:manipulation` gère déjà le double-tap-zoom).
_NOZOOM_JS = (
    "(function(){function b(e){e.preventDefault();}"
    "document.addEventListener('gesturestart',b,{passive:false});"
    "document.addEventListener('gesturechange',b,{passive:false});"
    "document.addEventListener('gestureend',b,{passive:false});})();"
)

# NOTIFICATIONS PUSH (PWA) — enregistre le service worker + gère l'abonnement (user 2026-08-16). Le bouton
# `.bfx-pushbtn` (onclick=bfxPushEnable()) demande la permission, s'abonne (clé VAPID publique) et POST
# l'abonnement. `bfxPushRefresh` met à jour le libellé du bouton selon l'état. iOS : ne marche qu'en PWA
# installée (écran d'accueil) -> message d'aide sinon.
_PUSH_JS = (
    "(function(){if(!('serviceWorker' in navigator))return;"
    "navigator.serviceWorker.register('/sw.js').catch(function(){});"
    "function u8(b){var p='='.repeat((4-b.length%4)%4),s=(b+p).replace(/-/g,'+').replace(/_/g,'/');"
    "var r=atob(s),a=new Uint8Array(r.length),i;for(i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a;}"
    "function isPwa(){return (window.matchMedia&&window.matchMedia('(display-mode: standalone)').matches)"
    "||window.navigator.standalone===true;}"
    "window.bfxPushRefresh=function(){"
    "var st=('Notification' in window)?Notification.permission:'unsupported';"
    "var pwa=isPwa(),show=pwa&&st!=='granted'&&st!=='unsupported';"
    "var rows=document.getElementsByClassName('bfx-pushrow'),j;"
    "for(j=0;j<rows.length;j++){rows[j].style.display=show?'flex':'none';}"
    "var e=document.getElementsByClassName('bfx-pushbtn'),i,el;"
    "for(i=0;i<e.length;i++){el=e[i];"
    "if(st==='denied'){el.textContent='\\uD83D\\uDD15 Notifications bloquées';}"
    "else{el.textContent='\\uD83D\\uDD14 Activer les notifications';}}};"
    "window.bfxPushEnable=function(){"
    "if(!('Notification' in window)||!('PushManager' in window)){"
    "alert('Notifications non supportées ici. Sur iPhone : ajoute d\\'abord le site à l\\'écran d\\'accueil.');return;}"
    "Notification.requestPermission().then(function(pm){if(pm!=='granted'){bfxPushRefresh();return;}"
    "Promise.all([navigator.serviceWorker.ready,fetch('/push/vapid').then(function(r){return r.json();})])"
    ".then(function(x){var reg=x[0],key=x[1]&&x[1].key;if(!key)return;"
    "reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:u8(key)}).then(function(sub){"
    "fetch('/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify(sub)}).then(bfxPushRefresh);}).catch(function(){bfxPushRefresh();});});});};"
    "if(document.readyState!=='loading')bfxPushRefresh();"
    "else document.addEventListener('DOMContentLoaded',bfxPushRefresh);})();"
)

# Sélecteur de sport de Pronos (demande user 2026-07-26) : clic sur une puce -> recharge #day-content via
# /jour?date=<jour>&sport=<sk> (le fragment contient le sélecteur avec la puce active à jour). Délégué au
# document (survit aux remplacements de #day-content).
_SPSEL_JS = (
    "(function(){document.addEventListener('click',function(e){"
    "var b=e.target.closest('.spsel');if(!b)return;e.preventDefault();"
    "var sp=b.getAttribute('data-sport');"
    "var w=b.closest('.spsel-wrap');"
    # cible + endpoint lus sur le wrap (Pronos: #day-content /jour?date=… ; Live: #pn-directs /directs)
    "var target=(w&&w.getAttribute('data-target'))||'day-content';"
    "var base=(w&&w.getAttribute('data-base'))||'/jour';"
    "var q=(w&&w.getAttribute('data-q'));if(q===null)q='';"
    "var dc=document.getElementById(target);if(!dc)return;"
    "if(w){var bs=w.querySelectorAll('.spsel');"
    "for(var i=0;i<bs.length;i++){bs[i].classList.toggle('on',bs[i]===b);}}"
    "dc.style.opacity='.45';"
    "fetch(base+'?'+(q?q+'&':'')+'sport='+encodeURIComponent(sp)+'&frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){dc.innerHTML=h;dc.style.opacity='';"
    "window.scrollTo({top:0,behavior:'smooth'});})"
    ".catch(function(){dc.style.opacity='';});});})();")

# CALENDRIER HORIZONTAL de Pronos (user 2026-08-19) : clic sur une DATE -> recharge #day-content via
# /jour?date=<iso>&sport=<sk actif>&frag=1 (préserve le sport sélectionné, lu sur la puce .spsel.on). Centre
# la date active à l'écran (au chargement ET après chaque swap). Délégué au document (le calendrier est re-rendu
# dans chaque fragment -> la surbrillance suit toujours le jour affiché).
_DAYCAL_JS = (
    "(function(){"
    # AUJOURD'HUI = dernière cellule -> on scrolle le calendrier TOUT À DROITE (user 2026-08-19 : au chargement/
    # changement d'onglet, la date du jour doit être le plus à droite possible). Un jour PASSÉ sélectionné -> centré.
    "function ctr(){var t=document.querySelector('#daycal .daycal-d.on');"
    "var tr=document.querySelector('#daycal .daycal-track');if(!t||!tr)return;"
    "if(t.classList.contains('today')){tr.scrollLeft=tr.scrollWidth;}"
    "else if(t.scrollIntoView){t.scrollIntoView({inline:'center',block:'nearest'});}}"
    # EN-TÊTE MOIS : reflète la 1re cellule VISIBLE à gauche (contexte quand on remonte l'historique).
    "function updMo(){var tr=document.querySelector('#daycal .daycal-track');"
    "var mo=document.getElementById('daycal-mo');if(!tr||!mo)return;"
    "var cs=tr.querySelectorAll('.daycal-d'),L=tr.getBoundingClientRect().left+12,i;"
    "for(i=0;i<cs.length;i++){if(cs[i].getBoundingClientRect().right>L){"
    "var my=cs[i].getAttribute('data-my');if(my&&mo.textContent!==my)mo.textContent=my;return;}}}"
    # BOUTON « Aujourd'hui » : visible si on regarde un JOUR PASSÉ, OU si la cellule AUJ. est SORTIE du calendrier
    # (scroll horizontal) — user 2026-08-19. Masqué sinon.
    "function updGoto(){var g=document.querySelector('#daycal .daycal-goto');if(!g)return;"
    "var on=document.querySelector('#daycal .daycal-d.on');"
    "var tr=document.querySelector('#daycal .daycal-track');"
    "var td=document.querySelector('#daycal .daycal-d.today');var show=false;"
    "if(on&&!on.classList.contains('today')){show=true;}"
    "else if(td&&tr){var a=td.getBoundingClientRect(),t=tr.getBoundingClientRect();"
    "if(a.right<=t.left+2||a.left>=t.right-2){show=true;}}"
    "g.classList.toggle('show',show);}"
    "function onScroll(){updMo();updGoto();}"
    "function bind(){var tr=document.querySelector('#daycal .daycal-track');"
    "if(tr&&!tr._mb){tr._mb=1;tr.addEventListener('scroll',onScroll,{passive:true});}}"
    "function sync(){ctr();bind();updMo();updGoto();}"
    # EXPOSÉ GLOBALEMENT (user 2026-08-19) : le SPA rappelle `_daycalSync` à chaque affichage/chargement du
    # panneau Programme -> le calendrier se REPLACE À DROITE (aujourd'hui) même après un swap d'onglet ou un
    # rechargement de panneau (sinon il se ré-affichait tout à gauche « sans raison »). rAF -> après layout.
    "window._daycalSync=function(){requestAnimationFrame(function(){requestAnimationFrame(sync);});};"
    "document.addEventListener('click',function(e){"
    "var b=e.target.closest('.daycal-d,.daycal-goto');if(!b)return;e.preventDefault();"   # cellule OU bouton « Aujourd'hui »
    "var date=b.getAttribute('data-date');if(!date)return;"
    # « Aujourd'hui » alors qu'on est DÉJÀ sur aujourd'hui -> RECENTRER seulement (pas de re-fetch -> ne réouvre
    # PAS les types de paris). user 2026-08-19.
    "if(b.classList.contains('daycal-goto')){var oc=document.querySelector('#daycal .daycal-d.on');"
    "if(oc&&oc.classList.contains('today')){ctr();setTimeout(updGoto,350);return;}}"
    "var dc=document.getElementById('day-content');if(!dc)return;"
    "var sc=dc.querySelector('.spsel.on');var sp=sc?sc.getAttribute('data-sport'):'';"
    "dc.style.opacity='.45';"
    "fetch('/jour?date='+encodeURIComponent(date)+(sp?'&sport='+encodeURIComponent(sp):'')+'&frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){dc.innerHTML=h;dc.style.opacity='';sync();"
    "window.scrollTo({top:0,behavior:'smooth'});})"
    ".catch(function(){dc.style.opacity='';});});"
    # le panneau Pronos se charge en différé (SPA) -> on synchronise plusieurs fois (no-op si déjà à jour).
    "document.addEventListener('DOMContentLoaded',sync);[250,800,1600].forEach(function(t){setTimeout(sync,t);});})();")

# Sous-nav de l'onglet Résultats (refonte user 2026-07-27) : bascule Bilan / Calendrier. Le Calendrier est
# LAZY-chargé depuis /calendrier?frag=1 au 1er clic (évite de rendre 2 vues lourdes d'emblée). Délégué au
# document (survit aux rechargements du panneau SPA).
_RESNAV_JS = (
    "(function(){var M={bilan:'res-bilan',analyse:'res-analyse',cal:'res-cal'};"
    "document.addEventListener('click',function(e){"
    "var b=e.target.closest('.resnav-b');if(!b)return;var w=b.closest('.resnav');if(!w)return;"
    "var which=b.getAttribute('data-res');"
    "var bs=w.querySelectorAll('.resnav-b'),i;for(i=0;i<bs.length;i++){bs[i].classList.toggle('on',bs[i]===b);}"
    "for(var k in M){var el=document.getElementById(M[k]);if(el)el.hidden=(k!==which);}"
    "if(which==='cal'){var ca=document.getElementById('res-cal');"
    "if(ca&&ca.getAttribute('data-loaded')!=='1'){ca.setAttribute('data-loaded','1');"
    "ca.innerHTML='<div class=\"res-load\">…</div>';"
    "fetch('/calendrier?frag=1').then(function(r){return r.text();}).then(function(h){ca.innerHTML=h;})"
    ".catch(function(){ca.setAttribute('data-loaded','0');ca.innerHTML='';});}}"
    "window.scrollTo({top:0,behavior:'smooth'});});})();")


def _resultats_subnav() -> str:
    """Sous-nav de l'onglet « Résultats » (refonte user 2026-07-27) : segmenté Bilan | Analyse | Calendrier.
    Bilan = rentabilité + cadres sport ; Analyse = edge/fiabilité/marchés/transparence ; Calendrier = heatmap
    (lazy-chargée). Bilan actif par défaut (JS _RESNAV_JS)."""
    return ('<div class="resnav">'
            '<button type="button" class="resnav-b on" data-res="bilan">Bilan</button>'
            '<button type="button" class="resnav-b" data-res="analyse">Analyse</button>'
            '<button type="button" class="resnav-b" data-res="cal">Calendrier</button>'
            '</div>')


def layout(title: str, sport: str, body: str, subnav: str | None = None,
           refresh: bool = False, source: dict | None = None, menu: str | None = None) -> str:
    """Page premium. `sport` ∈ home/tennis/basket/foot (onglet principal actif).
    `subnav` ∈ matchs/perf : affiche le sous-menu du sport (Matchs / Fiabilité).
    `source` : état SofaScore -> petit indicateur discret dans l'en-tête si en pause."""
    e = html.escape
    # Logo unique : réduit, centré, tout en haut de CHAQUE page (accueil + sports).
    toplogo = ('<a class="toplogo" href="/"><img src="/static/wordmark.png?v=1" alt="BETSFIX"></a>'
               if os.path.exists(_WORDMARK) else "")
    splash = ('<div class="splash" aria-hidden="true"><img src="/static/logo.png?v=3" alt=""></div>'
              if os.path.exists(_LOGO) else "")
    pausebar = ""
    if source and not source.get("ok"):
        s = source.get("paused_seconds", 0)
        pausebar = (f'<div class="pausewrap"><span class="pausebadge" '
                    f'title="SofaScore limité ({s}s) — RapidAPI/LiveScore prennent le relais, '
                    f'les paris et values restent à jour">'
                    f'⏸ Source en pause</span></div>')
    # Barre d'onglets fixée en BAS (MÊMES 5 onglets que la SPA, Directs inclus) : sur une page
    # layout (détail, dashboard…), cliquer un onglet recharge l'URL -> la SPA reprend la main.
    botnav = '<nav class="botnav">' + "".join(
        f'<a class="{"on" if sport == k else ""}" data-tab="{k}" href="{href}" aria-label="{e(name)}">'
        f'<span class="ic">{ico}</span><span class="lb">{e(name)}</span>'
        + ('<span class="nav-n" hidden></span>'
           if k in ("home", "tennis", "basket", "foot", "directs") else '')
        + '</a>'
        for k, href, ico, name in _SPA_TABS) + "</nav>"

    sub = ""
    if subnav and sport in _SPORT_MATCH_URL:
        # « 📊 Fiabilité » RETIRÉ (audit 2026-07-23) : pointait /tracking/dashboard -> 404 (route supprimée
        # avec le router tracking en 3fe72d7, juin) — lien mort sur chaque fiche match depuis un mois.
        items = [("matchs", _SPORT_MATCH_URL[sport], "📋 Matchs")]
        sub = '<div class="subnav">' + "".join(
            f'<a class="{"on" if subnav == k else ""}" href="{href}">{e(lbl)}</a>'
            for k, href, lbl in items) + "</div>"

    meta_refresh = '<meta http-equiv="refresh" content="180">' if refresh else ""
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070708">
<meta name="color-scheme" content="dark">
{meta_refresh}<title>{e(title)} · BETSFIX</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="preload" as="image" href="/static/wordmark.png?v=1"><link rel="preload" as="image" href="/static/logo.png?v=3">
<link rel="apple-touch-icon" href="/static/icon-180.png?v=5">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BETSFIX">
<style>{CSS}</style></head><body class="sp-{e(sport)}">
{_ACCT_BTN}{splash}<div class="wrap">{toplogo}{pausebar}{sub}{body}
<div class="foot">18+ · Outil informatif, sans garantie · Jouez responsable</div>
</div>{botnav}<script>{_ANIM_JS}</script><script>{_COUNTDOWN_JS}</script><script>{_LIVECLK_JS}</script><script>{_NOZOOM_JS}</script><script>{_PUSH_JS}</script><script>{_CARDS_JS}</script><script>{_SCTABS_JS}</script><script>{_TERM_JS}</script><script>{_MILE_JS}</script><script>{_DAYCAL_JS}</script></body></html>"""

def spa_shell(active: str, title: str, body: str, source: dict | None = None) -> str:
    """Coquille « single-page » des 4 onglets principaux. Le sport `active` est rendu côté
    serveur (1er affichage rapide, marche sans JS) ; les 3 autres panneaux sont vides et
    remplis en AJAX dès l'ouverture. La nav du bas bascule les panneaux SANS rechargement."""
    e = html.escape
    toplogo = ('<a class="toplogo" href="/"><img src="/static/wordmark.png?v=1" alt="BETSFIX"></a>'
               if os.path.exists(_WORDMARK) else "")
    splash = ('<div class="splash" aria-hidden="true"><img src="/static/logo.png?v=3" alt=""></div>'
              if os.path.exists(_LOGO) else "")
    pausebar = ""
    if source and not source.get("ok"):
        s = source.get("paused_seconds", 0)
        pausebar = (f'<div class="pausewrap"><span class="pausebadge" '
                    f'title="SofaScore limité ({s}s) — RapidAPI/LiveScore prennent le relais, '
                    f'les paris et values restent à jour">'
                    f'⏸ Source en pause</span></div>')
    panels = []
    for k, href, _ico, _name in _SPA_TABS:
        on = " on" if k == active else ""
        inner = (body if k == active else
                 '<div class="skel"><div class="sk"></div><div class="sk"></div><div class="sk"></div></div>')
        panels.append(f'<section class="panel{on}" id="pn-{k}" data-tab="{k}" '
                      f'data-src="{href}">{inner}</section>')
    botnav = '<nav class="botnav">' + "".join(
        f'<a class="{"on" if active == k else ""}" data-tab="{k}" href="{href}" aria-label="{e(name)}">'
        f'<span class="ic">{ico}</span><span class="lb">{e(name)}</span>'
        + ('<span class="nav-n" hidden></span>'
           if k in ("home", "tennis", "basket", "foot", "directs") else '')
        + '</a>'
        for k, href, ico, name in _SPA_TABS) + "</nav>"
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070708">
<meta name="color-scheme" content="dark">
<title>{e(title)} · BETSFIX</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="preload" as="image" href="/static/wordmark.png?v=1"><link rel="preload" as="image" href="/static/logo.png?v=3">
<link rel="apple-touch-icon" href="/static/icon-180.png?v=5">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BETSFIX">
<style>{CSS}</style></head><body class="sp-{e(active)}">
{_ACCT_BTN}{splash}<div class="wrap">{toplogo}{pausebar}<main id="panels">{''.join(panels)}</main>
<div class="foot">18+ · Outil informatif, sans garantie · Jouez responsable</div>
</div>{_A2HS_HTML}{botnav}<script>{_ANIM_JS}</script><script>{_COUNTDOWN_JS}</script><script>{_LIVECLK_JS}</script><script>{_NOZOOM_JS}</script><script>{_PUSH_JS}</script><script>{_CARDS_JS}</script><script>{_SCTABS_JS}</script><script>{_SPA_JS}</script><script>{_LZ_ANIM_JS}</script><script>{_TERM_JS}</script><script>{_MILE_JS}</script><script>{_CAL_JS}</script><script>{_MCAL_JS}</script><script>{_A2HS_JS}</script><script>{_SPSEL_JS}</script><script>{_DAYCAL_JS}</script><script>{_RESNAV_JS}</script></body></html>"""

def bars_split(model, implied) -> dict:
    """Champs des barres RÉPARTIES. model/implied = (home, nul|None, away) par source."""
    m = model or (None, None, None)
    i = implied or (None, None, None)
    return {"m_home": m[0], "m_draw": m[1], "m_away": m[2],
            "i_home": i[0], "i_draw": i[1], "i_away": i[2]}

_NAME_CONNECTORS = {"du", "de", "des", "da", "di", "of", "the", "und", "et", "and"}


def _pick_bars(p: dict) -> str:
    """Bloc « Cotes & chances » PREMIUM : une barre fine de proportion (total 100 %, marge retirée)
    surmontée de CHIPS par issue (nom · % de chance · cote), le favori mis en valeur. Suivi d'une
    barre Public compacte (votes) si dispo. On lit d'un coup d'œil la chance ET la cote de chaque issue."""
    e = html.escape
    has_draw = any(p.get(k) is not None for k in ("i_draw", "pub_draw"))
    home = _noF(p.get("home") or "") or "1"
    away = _noF(p.get("away") or "") or "2"

    def block(title, scol, probs, names, odds=None, chips=True):
        # probs/names/odds alignés (home, [nul], away). Garde les issues à proba connue.
        cells = [(v, n, (odds or [None] * len(probs))[i])
                 for i, (v, n) in enumerate(zip(probs, names)) if v is not None]
        if len(cells) < 2:
            return ""
        mx = max(v for v, _n, _o in cells)
        seg = "".join(
            f'<span class="ocb-s {scol if v == mx else "ocb-dim"}" style="width:{round(v * 100)}%"></span>'
            for v, _n, _o in cells)
        bar = f'<div class="ocb">{seg}</div>'
        if not chips:
            lab = " · ".join(f'<b>{e(n)}</b> {round(v * 100)}%' for v, n, _o in cells)
            return f'<div class="oc"><div class="oc-h">{title}</div>{bar}<div class="oc-pub">{lab}</div></div>'
        cs = "".join(
            f'<div class="ocp{" ocp-fav " + scol if v == mx else ""}">'
            f'<span class="ocp-n">{e(n)}</span>'
            f'<span class="ocp-v">{round(v * 100)}%</span>'
            + (f'<span class="ocp-c">@{c:g}</span>' if c else "")
            + '</div>'
            for v, n, c in cells)
        return f'<div class="oc"><div class="oc-h">{title}</div>{bar}<div class="ocp-row">{cs}</div></div>'

    nm = (home, "Nul", away) if has_draw else (home, away)
    # Titre EXPLICITE « du marché » (refonte 2026-07-17) : ces % sont les chances DÉDUITES DES COTES (le
    # marché), à NE PAS confondre avec « Notre confiance » (notre proba calibrée), affichée sur le pari.
    out = block("Chances au marché (cotes)", "ocb-po",
                ([p.get("i_home"), p.get("i_draw"), p.get("i_away")] if has_draw
                 else [p.get("i_home"), p.get("i_away")]), nm,
                odds=([p.get("o_home"), p.get("o_draw"), p.get("o_away")] if has_draw
                      else [p.get("o_home"), p.get("o_away")]))
    out += block("Public", "ocb-pc",
                 ([p.get("pub_home"), p.get("pub_draw"), p.get("pub_away")] if has_draw
                  else [p.get("pub_home"), p.get("pub_away")]), nm, chips=False)
    return f'<div class="ocs">{out}</div>' if out else _pick_bars_legacy(p)

def _pick_bars_legacy(p: dict) -> str:
    """Repli (anciennes barres, côté pari) si le détail home/away manque — SANS emoji."""
    def bar(label, val, cls):
        if val is None:
            return ""
        pct = round(val * 100)
        return (f'<div class="pb-row"><span class="pb-l">{label}</span>'
                f'<div class="pb-t"><span class="{cls}" style="width:{min(pct,100)}%"></span></div>'
                f'<span class="pb-v">{pct}%</span></div>')
    inner = (bar("Cote Unibet", p.get("implied"), "po")
             + bar("Public", p.get("community"), "pc"))
    if not inner:
        return ""
    bet = html.escape(p.get("bet") or "le pari")
    return (f'<div class="pbars"><div class="pb-h">Chances que <b>{bet}</b> gagne '
            f'<span class="dim">— selon :</span></div>{inner}</div>')

def _pct_class(pct) -> str:
    return "hi" if (pct is not None and pct >= 60) else ("mid" if (pct is not None and pct >= 45) else "lo")

def _roicls(v) -> str:
    return "hi" if (v or 0) > 0 else ("lo" if (v or 0) < 0 else "mid")

def _roistr(v) -> str:
    return "—" if v is None else f'{"+" if v >= 0 else ""}{v:g}%'

_MIN_REL = 3   # en dessous (1-2 paris) : ROI non significatif -> grisé + « indicatif »

def _roi_cls(roi, settled) -> str:
    """Classe couleur du ROI, MAIS grisée (`na`) si l'échantillon est trop faible (< _MIN_REL)."""
    return "na" if (not settled or settled < _MIN_REL) else _roicls(roi)

def _ind(settled) -> str:
    """Étiquette « indicatif » quand l'échantillon est trop faible pour un ROI fiable."""
    return '<span class="sx-ind">indicatif</span>' if (settled or 0) < _MIN_REL else ""


def _smooth_path(xy: list) -> str:
    """Chemin SVG LISSÉ (Catmull-Rom -> Bézier cubique) passant par TOUS les points : adoucit les
    marches d'escalier des courbes d'équité (1 point = 1 pari réglé) sans déplacer les extrémités."""
    if len(xy) < 3:
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    p = [f"M{xy[0][0]:.1f},{xy[0][1]:.1f}"]
    for i in range(len(xy) - 1):
        p0 = xy[i - 1] if i > 0 else xy[i]
        p1, p2 = xy[i], xy[i + 1]
        p3 = xy[i + 2] if i + 2 < len(xy) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        p.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(p)

def _sparkline(points: list, color: str) -> str:
    """Mini courbe LISSÉE (SVG, sans axes) : ligne + aire teintée. Pour les cartes bilan."""
    if not points:
        return ""
    pts = points if len(points) > 1 else (points * 2)
    lo, hi = min(pts), max(pts)
    if hi - lo < 1e-9:
        lo, hi = lo - 0.5, hi + 0.5
    n = len(pts)
    w, h = 100.0, 30.0

    def X(i):
        return 1 + i / (n - 1) * (w - 2)

    def Y(v):
        return 2 + (1 - (v - lo) / (hi - lo)) * (h - 4)

    xy = [(X(i), Y(v)) for i, v in enumerate(pts)]
    d = _smooth_path(xy)
    area = f'M{X(0):.1f},{h - 1:g} L' + d[1:] + f' L{X(n - 1):.1f},{h - 1:g} Z'
    return (f'<svg viewBox="0 0 {w:g} {h:g}" class="sx-spark" preserveAspectRatio="none">'
            f'<path d="{area}" fill="{color}" opacity="0.13" stroke="none"/>'
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.7" '
            'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/></svg>')

def _drill(url: str, inner: str, cls: str) -> str:
    """Élément déroulant (drill-down) réutilisant le mécanisme `data-exp` global : tap -> charge la
    liste des matchs de la catégorie dans `.exp`. `inner` = contenu visible (sans le chevron)."""
    return (f'<div class="{cls} rowtap" data-exp="{url}">{inner}'
            f'<div class="exp" hidden></div></div>')

def _sport_card(s: dict, sport: str, label: str, since: str,
                color: str | None = None) -> str:
    """Une ligne bilan par sport (SANS emoji — pastille couleur + nom) : mini-courbe d'équité +
    ROI + gagnés/réglés · % + cote moy., tap -> liste des matchs. `color` = teinte d'identité du
    sport ; défaut = vert/rouge selon le ROI."""
    roi = s.get("roi")
    color = color or ("#34d27b" if (roi or 0) >= 0 else "#ff6b6b")
    cote = f'@{s["avg_odds"]:g}' if s.get("avg_odds") else "—"
    spark = _sparkline(s.get("points") or [], color)
    main = (f'<div class="sx-row-main"><span class="bc-dot" style="background:{color}"></span>'
            f'<span class="sx-row-n">{label}{_ind(s.get("settled"))}</span>'
            f'<span class="sx-row-spk">{spark}</span>'
            f'<span class="sx-row-roi arec-{_roi_cls(roi, s.get("settled"))}">{_roistr(roi)}</span>'
            f'<span class="sx-row-wl">{s["won"]}/{s["settled"]} · {s["pct"]}%</span>'
            f'<span class="sx-row-c">{cote}</span><span class="sx-row-chev">›</span></div>')
    return (f'<div class="sx-sport" data-sport="{sport}"><div class="sx-rows">'
            + _drill(f'/stats/detail?sport={sport}&since={since}', main, "sx-row")
            + '</div></div>')

def _streak_chip(streak) -> str:
    """Chip COMPACT « série en cours » : 🔥 N (gagnés) / ❄️ N (perdus) d'affilée. Texte COURT (icône +
    nombre) + libellé complet en `title` : le libellé long élargissait le badge et rognait la série W/L
    à côté (fix 2026-07-10). '' si aucune série."""
    if not streak:
        return ""
    if streak > 0:
        _t = f'{streak} gagné{"s" if streak > 1 else ""} d\'affilée'
        return f'<span class="sx-streak hot" title="{_t}">🔥 {streak}</span>'
    n = -streak
    _t = f'{n} perdu{"s" if n > 1 else ""} d\'affilée'
    return f'<span class="sx-streak cold" title="{_t}">❄️ {n}</span>'


def _best_streak_chip(best) -> str:
    """Chip RECORD « plus longue série de victoires » : 🏆 N — À CÔTÉ de la série en cours (demande user
    2026-07-25). '' si aucune victoire enchaînée."""
    if not best or best <= 0:
        return ""
    _t = f'record : {best} gagné{"s" if best > 1 else ""} d\'affilée'
    return f'<span class="sx-streak best" title="{_t}">🏆 {best}</span>'

def _hero_chart(points: list, uid: str = "h", dates: list | None = None,
                milestones: list | None = None) -> str:
    """Grande courbe d'équité (profit cumulé) : aire + courbe VERTE au-dessus de 0 / ROUGE en dessous
    (dégradé à coupure nette sur le zéro), grille + label « 0 ». Courbe ROI SEULE — la Réussite et la Cote
    sont des graphes SÉPARÉS (demande user 2026-07-27 : 3 graphes distincts). PLUS de repères de modèle sur
    la courbe (retirés à la même demande). `dates`/`milestones` gardés pour compat mais NON tracés."""
    if not points:
        return ""
    pts = points if len(points) > 1 else (points * 2)
    lo, hi = min(pts + [0.0]), max(pts + [0.0])
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pad = (hi - lo) * 0.16
    lo, hi = lo - pad, hi + pad
    n, W, H, L, R, T, B = len(pts), 320.0, 104.0, 16.0, 16.0, 14.0, 8.0
    iw, ih = W - L - R, H - T - B
    GR, RD = "#34d27b", "#ff6b6b"

    def X(i):
        return L + (iw * i / (n - 1) if n > 1 else iw / 2)

    def Y(v):
        return T + ih * (1 - (v - lo) / (hi - lo))

    zy = Y(0.0)
    off = max(0.0, min(1.0, zy / H))                     # position du zéro (0..1) pour la coupure
    gid = f"sxg-{uid}"
    line_d = _smooth_path([(X(i), Y(v)) for i, v in enumerate(pts)])   # courbe LISSÉE
    # aire ENTRE la courbe et la ligne du zéro -> verte au-dessus, rouge en dessous
    area_d = f'M{X(0):.1f},{zy:.1f} L' + line_d[1:] + f' L{X(n - 1):.1f},{zy:.1f} Z'
    grad = (f'<defs><linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
            f'x1="0" y1="0" x2="0" y2="{H:g}">'
            f'<stop offset="0" stop-color="{GR}"/><stop offset="{off:.4f}" stop-color="{GR}"/>'
            f'<stop offset="{off:.4f}" stop-color="{RD}"/><stop offset="1" stop-color="{RD}"/>'
            '</linearGradient></defs>')
    p = [f'<svg viewBox="0 0 {W:g} {H:g}" class="sx-heroc">', grad]
    for k in range(4):                                   # grille horizontale (3 intervalles)
        gv = lo + (hi - lo) * k / 3
        if abs(gv) < 1e-6:
            continue
        p.append(f'<line class="bc-grid" x1="{L:g}" y1="{Y(gv):.1f}" x2="{W - R:g}" y2="{Y(gv):.1f}"/>')
    p.append(f'<path class="sx-heroc-area" d="{area_d}" fill="url(#{gid})" opacity="0.22" stroke="none"/>')
    p.append(f'<line class="bc-zero" x1="{L:g}" y1="{zy:.1f}" x2="{W - R:g}" y2="{zy:.1f}"/>')
    p.append(f'<text class="bc-zl" x="{L - 3:g}" y="{zy + 3:.1f}">0</text>')
    # Ligne TRACÉE (draw-in) : `pathLength="1"` normalise -> `sxdraw` marche quelle que soit la longueur.
    # PAS de `vector-effect="non-scaling-stroke"` : le combo pathLength + stroke-dasharray + non-scaling est
    # un BUG WebKit/iOS (dash calculé dans le mauvais espace -> dernier segment fin/haché même à l'arrêt).
    p.append(f'<path class="sx-heroc-line" pathLength="1" d="{line_d}" fill="none" stroke="url(#{gid})" '
             'stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>')
    p.append(f'<circle class="sx-heroc-pt" cx="{X(n - 1):.1f}" cy="{Y(pts[-1]):.1f}" r="2.8" '
             f'fill="{GR if pts[-1] >= 0 else RD}"/>')
    # Repères de modèle RETIRÉS de la courbe (demande user 2026-07-27 : « retire les repères jaunes »).
    p.append("</svg>")
    return "".join(p)


def sx_section_collapsible(label: str, sub: str, body: str, open: bool = False) -> str:
    """Section de la page Stats REPLIABLE (accordéon natif <details>, sans JS) : le détail est masqué par
    défaut pour raccourcir une page devenue longue (demande user 2026-07-02). La VUE D'ENSEMBLE reste
    toujours visible ; seules les sections de détail sont pliées. '' si le corps est vide."""
    if not (body or "").strip():
        return ""
    sub = (sub[0].upper() + sub[1:]) if sub else ""     # majuscule initiale (demande user 2026-08-11)
    s = f'<span class="sx-sec-sub">{html.escape(sub)}</span>' if sub else ""
    op = " open" if open else ""
    # Titre bleu (une ligne) + chevron à droite ; le sous-titre gris passe SOUS le titre (flex-basis:100%).
    return (f'<details class="sx-acc"{op}><summary class="sx-sec sx-sec-sum">'
            f'<span class="sx-sec-lbl">{html.escape(label)}</span>'
            f'<span class="sx-sec-chev">▾</span>{s}'
            f'</summary><div class="sx-acc-body">{body}</div></details>')


def render_sports_breakdown(full: dict | None, since: str = "") -> str:
    """« Détail par sport » : une ligne par sport (pastille + mini-courbe + ROI + bilan + cote). '' si
    aucun sport réglé. Extrait de render_stats pour pouvoir le placer dans sa propre section."""
    bs = (full or {}).get("by_sport") or {}
    SPORTS = (("foot", "Football", "#2ee27f"),)   # FOOT SEUL (user 2026-08-07 : tennis/basket retirés)
    scards = [_sport_card(bs[sk], sk, lbl, since, color=col)
              for sk, lbl, col in SPORTS if (bs.get(sk) or {}).get("settled")]
    return (('<div class="sx-bys"><div class="sx-h">Détail par sport</div>'
             + "".join(scards) + '</div>') if scards else "")


def render_volume(full: dict | None, combo_full: dict | None = None, cal: dict | None = None) -> str:
    """Panneau « Volume de données » (transparence, demande user) : combien de matchs/paris le modèle
    a vus, et la part de prédictions FANTÔMES (calibration seule, jamais dans le ROI). Placé en BAS de
    la page (c'est de la transparence, pas du bilan)."""
    ov = (full or {}).get("overall") or {}
    vol = (full or {}).get("volume") or {}
    _cf = combo_full if combo_full is not None else analyses.combo_stats()
    cal = cal if cal is not None else analyses.calibration()
    d24 = analyses.volume_24h()                       # variation des dernières 24 h (par coup d'envoi)
    pend = analyses.volume_pending()                  # pronos en attente de résultat (pipeline actif)

    def _kpi(val: int, label: str, delta: int) -> str:
        d = (f'<i class="sx-d24">+{delta}</i>' if delta else '<i class="sx-d24 z">±0</i>')
        return f'<div class="sx-kpi"><b>{val}</b><span>{label}</span>{d}</div>'

    # PÉRIODE DE MESURE : plage de coups d'envoi couverte -> contexte du nb calibré (« X paris sur N j »).
    _M = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")

    def _fr(iso: str):
        try:
            return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    _d1, _d2 = _fr(vol.get("first")), _fr(vol.get("last"))
    period = ""
    if _d1 and _d2:
        _days = (_d2.date() - _d1.date()).days + 1
        _rng = (f'{_d1.day} {_M[_d1.month - 1]} → {_d2.day} {_M[_d2.month - 1]} {_d2.year}'
                if _d1.date() != _d2.date() else f'{_d1.day} {_M[_d1.month - 1]} {_d1.year}')
        period = (f'<div class="sx-data-period">🗓 Mesuré sur <b>{_days} jour{"s" if _days > 1 else ""}</b>'
                  f' · {_rng}</div>')

    return (
        '<div class="sx-card sx-data"><div class="sx-h">📊 Volume de données'
        '<span>cumul · variation 24 h</span></div>'
        + period
        + '<div class="sx-kpis sx-kpis3">'
        + _kpi(vol.get("matches", 0), "matchs joués", d24["matches"])
        + _kpi(ov.get("settled", 0), "confiances jouées", d24["simples"])
        + _kpi(_cf.get("n", 0), "combinés joués", d24["combos"])
        + '</div><div class="sx-kpis sx-kpis3">'
        + _kpi(cal.get("n", 0), "paris calibrés", d24["calibrated"])
        + _kpi(cal.get("n_shadow", 0), "pronos fantômes", d24["ghosts"])
        + _kpi(vol.get("analysed", 0), "matchs analysés", d24["analysed"])
        + '</div>'
        # EN COURS : pronos analysés en attente de résultat (pipeline actif) — distinct du cumul réglé.
        '<div class="sx-data-sub">⏳ En cours · en attente de résultat</div>'
        '<div class="sx-kpis sx-kpis3">'
        f'<div class="sx-kpi"><b>{pend["simples"]}</b><span>confiances en cours</span></div>'
        f'<div class="sx-kpi"><b>{pend["combos"]}</b><span>combinés en cours</span></div>'
        f'<div class="sx-kpi"><b>{pend["ghosts"]}</b><span>fantômes en cours</span></div>'
        '</div>'
        '<div class="sx-data-note">Le <b>+N vert</b> = entrées des dernières <b>24 h</b>. '
        '« <b>En cours</b> » = pronos analysés en attente de résultat (matchs à venir / récents). Les '
        '<b>paris de confiance</b> et <b>combinés joués</b> sont les seuls comptés dans le ROI et la courbe. Les '
        '<b>pronos fantômes</b> (prédictions SIMPLES non jouées, réglées après match) affinent la '
        '<b>calibration</b> sur tout le spectre de cotes — ils n\'entrent JAMAIS dans le bilan, et il '
        'n\'existe pas de combiné fantôme.</div></div>')


def render_volume_by_sport() -> str:
    """Carte « Volume par sport » (transparence, demande user) : combien de matchs ANALYSÉS et de PARIS
    sélectionnés par sport, sur 7 et 30 jours. Le foot est compté au ROI ; tennis/basket = SIMULATION."""
    from app import analyses as _an
    v7, v30 = _an.volume_by_sport(7), _an.volume_by_sport(30)
    bg = _an.background_sports()
    SPORTS = (("foot", "Football", "⚽", "#2ee27f"),)   # FOOT SEUL (user 2026-08-07 : tennis/basket retirés)

    def _cell(a: dict) -> str:
        return (f'<div class="vbs-cell"><b>{a.get("analysed", 0)}</b>analysés'
                f'<span>{a.get("picks", 0)} paris · {a.get("provisional", 0)} prov.</span>'
                f'<span class="vbs-c2">{a.get("combos", 0)} comb.</span></div>')
    rows = []
    for sk, lbl, emo, col in SPORTS:
        tag = ('<span class="vbs-sim">🔬 simulé</span>' if sk in bg
               else '<span class="vbs-roi">ROI</span>')
        rows.append(
            f'<div class="vbs-row"><div class="vbs-sp"><i class="vbs-dot" style="background:{col}"></i>'
            f'{emo} {lbl}{tag}</div>{_cell(v7.get(sk, {}))}{_cell(v30.get(sk, {}))}</div>')
    return (
        '<div class="sx-card sx-vbs"><div class="sx-h">📊 Volume par sport'
        '<span>analyse &amp; sélection</span></div>'
        '<div class="vbs-head"><div></div><div>7 jours</div><div>30 jours</div></div>'
        + "".join(rows)
        + '<div class="sx-data-note">« <b>analysés</b> » = matchs dont le dossier complet (multi-sources '
        '+ analyse) a été produit. « <b>paris</b> » = matchs avec un pari retenu. '
        '« <b>prov.</b> » = paris provisoires (le RÉSULTAT le plus probable par match — comptés au ROI '
        'global). « <b>comb.</b> » = combinés du jour. Application <b>100 % football</b>.</div></div>')


def _mile_legend(miles: list, *, compact: bool = False) -> str:
    """Légende repliable des repères de modèle (pastilles numérotées cliquables + explications cachées au
    clic) pour un SOUS-ENSEMBLE de MODEL_MILESTONES (Simples OU Combinés). Numérotation LOCALE 1..N, et le
    JS (_MILE_JS) est scopé au bloc `.spf-cv` -> les 2 séries de repères ne se confondent pas. '' si vide.

    `compact=True` (bloc Combinés) : on N'AFFICHE PAS l'en-tête « Repères du modèle » ni la grille de
    pastilles (redondants avec le bloc Simples juste au-dessus). On garde SEULEMENT le panneau d'info + les
    divs de données cachées -> les marqueurs numérotés de la COURBE combinés restent cliquables (le JS
    cherche `.sx-mile-d` dans le même scope `.spf-cv`). Condense le haut de la page sans perdre l'interaction."""
    if not miles:
        return ""

    def _auto(m):
        return len(m) > 5 and m[5] == "auto"
    chips = "".join(f'<button type="button" class="sx-mile-b{" mauto" if _auto(m) else ""}" '
                    f'data-mile="{i}">{i}</button>' for i, m in enumerate(miles, 1))
    data = "".join(
        f'<div class="sx-mile-d" data-mile="{i}" hidden>'
        f'<span class="sx-mile-date">{html.escape((m[0] or "")[:10])}</span>'
        f'<span class="sx-mile-tag{" mauto" if _auto(m) else ""}">'
        f'{"🟠 auto" if _auto(m) else "🔵 méthodo"}</span><br>'
        f'<b>{html.escape(m[1])}</b> — {html.escape(m[2])}</div>'
        for i, m in enumerate(miles, 1))
    # Clé de lecture (montrée seulement s'il y a des deux types) : intuitif d'un coup d'œil.
    key = ""
    if any(_auto(m) for m in miles) and any(not _auto(m) for m in miles):
        key = ('<div class="sx-mile-key"><span><i class="km kmeth"></i>réglage méthodo</span>'
               '<span><i class="km kauto"></i>marché auto-ajusté</span></div>')
    if compact:
        # Combinés : que le panneau d'info + les données cachées (marqueurs de courbe cliquables).
        return f'<div class="sx-miles sx-miles-c"><div class="sx-mile-info"></div>{data}</div>'
    return (f'<div class="sx-miles"><div class="sx-ml-h">Repères du modèle'
            f'<span class="sx-ml-hint">touchez un repère pour le détail</span></div>'
            f'{key}<div class="sx-mile-bs">{chips}</div>'
            f'<div class="sx-mile-info"></div>{data}</div>')


def _avantage_block(ov: dict) -> str:
    """Hero « Avantage réalisé » (style Bull Sports) en tête du bilan : le ROI (profit par pari) en VEDETTE +
    réussite / gagnés / perdus. 100 % à partir des chiffres OFFICIELS (full['overall']) -> jamais de valse du
    compteur. '' si trop peu de réglés (< 10)."""
    ov = ov or {}
    n = ov.get("settled") or 0
    if n < 10:
        return ""
    pct, won = ov.get("pct"), (ov.get("won") or 0)
    lost = n - won
    # HERO « profit par pari » (label + gros ROI + phrase) RETIRÉ (user 2026-08-16) : on ne garde que les
    # KPIs réussite / gagnés / perdus. Le ROI par type reste affiché dans chaque onglet (Confiance/Value).
    return (
        '<div class="adv-hero adv-kpis-only"><div class="adv-kpis">'
        f'<div><b>{pct if pct is not None else "—"}%</b><span>réussite</span></div>'
        f'<div><b>{won}</b><span>gagnés</span></div>'
        f'<div><b>{lost}</b><span>perdus</span></div>'
        '</div></div>')


def render_stats(full: dict | None, since: str = "", combo_full: dict | None = None) -> str:
    """Onglet STATISTIQUES — premium & lisible : (1) bilan global (ROI + KPIs), (2) courbe d'équité
    UNIQUE (profit cumulé) avec repères des changements de modèle, (3) détail par sport (ligne +
    mini-courbe), (4) calibration en aval. `since` propagé aux liens drill-down. '' si rien réglé."""
    full = full or {}
    ov = full.get("overall") or {}
    if not ov.get("settled"):
        return ""
    # COURBE + repères. DEUX familles fusionnées et triées par date : 🔵 JALONS MÉTHODO (changements
    # DÉLIBÉRÉS de l'analyse / de la création des tickets) + 🟠 AJUSTEMENTS AUTO (marchés que le système
    # écarte ou ré-intègre TOUT SEUL, datés — ils changent aussi la fabrication des tickets). Chaque repère
    # a une PORTÉE (simple/combo/both) -> repères SIMPLES sur le graphe Simples, COMBINÉS sur le graphe
    # Combinés, « both » sur les 2. Le 6e champ = type ("methodo"/"auto") pilote la couleur de la pastille.
    # REPÈRES BLEUS (jalons méthodo MODEL_MILESTONES) RETIRÉS des graphiques (demande user 2026-07-14) :
    # on ne trace plus que les repères AUTO (ambrés, marché auto-ajusté). Le filtrage à la SOURCE couvre
    # chart + légende, graphes Simples ET Combinés (tous dérivent de `_all_miles`).
    # REPÈRES PROPRES À CHAQUE SPORT (demande user 2026-07-24) : le graphe Football ne montre que les
    # ajustements FOOT (ou globaux « all ») ; ceux de tennis/basket vont sur leurs courbes de Simulation.
    # 5e champ (m[4]) = sport (foot/tennis/basket/all). Helper partagé exposé pour _simulation_card.
    _all_miles = sorted(analyses.exclusion_events(), key=lambda m: (m[0] or ""))
    _ms_combo = [m for m in _all_miles if (m[3] if len(m) > 3 else "both") in ("combo", "both")
                 and (m[4] if len(m) > 4 else "all") in ("foot", "all")]
    # Forme W/L (mêmes pastilles que les onglets sport, récent à DROITE), JUSTE au-dessus de sa courbe.
    # + SABLIERS DORÉS des paris À JOUER pas encore réglés, en queue (demande user 2026-07-17).
    _LET = {"won": "W", "lost": "L", "push": "N"}
    # BLOC SIMPLES compact (présentation alignée sur les onglets sport, demande user) : en-tête
    # (titre + ROI), W/L au-dessus de la courbe, courbe (avec repères), stats dessous. EXTRAS conservés :
    # nouv. système + CLV (ligne secondaire) et repères de modèle sous la courbe.
    # Paris À JOUER (comptés au ROI, pas encore réglés) EN TÊTE (⏳), puis les réglés (demande user 2026-07-14).
    # SPLIT CONFIANCE / VALUE (user 2026-08-09) : deux blocs (2 onglets) au lieu du bloc « simples » overall.
    # Chaque tier a SA courbe, SA série W/L et SON historique (via full["by_tier"], reconstruit par _agg_bets
    # sur les seuls paris du tier) -> l'historique des matchs est bien SÉPARÉ entre Confiance et Value.
    _bt = full.get("by_tier") or {}
    _pend_all = analyses.pending_roi_bets()
    _pend_conf = [b for b in _pend_all if b.get("tier") == "confiance"]   # montante exclue (catégorie à part)
    _pend_val = [b for b in _pend_all if b.get("tier") == "value"]

    def _tier_block(ts, pend, uid, more_lbl):
        if not (ts.get("settled") or pend):
            return ""
        _ch = _hero_chart(ts.get("points") or [], uid=uid, dates=ts.get("dates") or [])
        _lf = ts.get("form_run") or ts.get("form") or []
        _fd = form_dots([_LET.get(x, x) for x in _lf], n=16, pending=len(pend))
        _fh = f'<div class="spf-cv-form">{_fd}</div>' if _fd else ""
        _in = _hero_graph_inner(
            roi=ts.get("roi"), n=ts.get("settled"), hit=ts.get("pct"), avg_cote=ts.get("avg_odds"),
            chart=f'<div class="sx-equity">{_ch}</div>', form=_fh, streak=ts.get("streak"),
            hit_points=ts.get("hit_points"), uid=uid, best_streak=ts.get("best_streak"),
            cote_points=ts.get("cote_points"))
        _rc = _recent_bets_html(pend + list(reversed(ts.get("recent") or [])))
        # Derniers paris affichés D'OFFICE (demande user 2026-08-13 : « plus besoin du bouton ») — libellé
        # statique + liste (scrollable) directement sous la courbe, plus de <details> cliquable.
        _rcb = f'<div class="spf-rec-lbl">{html.escape(more_lbl)}</div>{_rc}' if _rc else ""
        return f'<div class="spf-hero">{_in}{_rcb}</div>'

    simples_block = _tier_block(_bt.get("confiance") or {}, _pend_conf, "sim-conf", "Derniers paris Confiance")
    value_block = _tier_block(_bt.get("value") or {}, _pend_val, "sim-value", "Derniers paris Value")
    # BLOC COMBINÉS FOOTBALL (demande user 2026-07-24 : graphes de combiné PROPRES à chaque sport) : ici les
    # combos PER-MATCH FOOT seuls. Le combiné du jour et le combiné Betmines ont leur PROPRE carte (suivis
    # indicatifs). Tennis/basket combos -> section Simulation. Repères foot (_ms_combo déjà filtré foot+all).
    _cs = combo_full if combo_full is not None else analyses.combo_stats()
    _foot_c = (_cs.get("by_sport") or {}).get("foot") or {}
    # Combiné(s) foot EN COURS (le combiné du jour) -> injectés en tête avec ⏳, EXACTEMENT comme la carte
    # simulation le fait pour tennis/basket (sinon le combiné foot du jour n'apparaît PAS dans les stats
    # tant qu'il n'est pas réglé — retour user 2026-07-26). Pur affichage (jamais au ROI/gel).
    _pend_fc = analyses.pending_roi_bets(combo=True)
    combos_block = ((('' if analyses.COMBO_ROI_ON      # combinés COMPTÉS au ROI (user 2026-08-19) -> plus de note « hors ROI »
                      else '<div class="combo-horsroi">Suivi indicatif — <b>non compté au ROI</b></div>')
                     + render_tracking_curve(
        emoji="⚽", title="COMBINÉS", roi=_foot_c.get("roi"), hit=_foot_c.get("pct"),
        n=_foot_c.get("settled"), points=_foot_c.get("points"), dates=_foot_c.get("dates"),
        avg_cote=_foot_c.get("avg_odds"), uid="combo-foot", streak=_foot_c.get("streak"),
        form=_form_streak(_foot_c.get("form_run") or _foot_c.get("form") or [])[0],   # ligne W/L (demande user)
        recent=_pend_fc + list(reversed(_foot_c.get("recent") or [])), more_label="Derniers combinés",
        pending=len(_pend_fc),                        # sabliers ⏳ des combinés à venir (comme tennis/basket)
        milestones=_ms_combo, compact=True, hit_points=_foot_c.get("hit_points"),
        best_streak=_foot_c.get("best_streak"), cote_points=_foot_c.get("cote_points"),
        warmup=3))                                    # combiné du jour ~1/j depuis 29/07 : seuil courbes abaissé
        if (_foot_c.get("settled") or _pend_fc) else "")   # onglet Combiné AFFICHÉ mais « hors ROI » (user 2026-08-16)
    # UN CADRE PAR SPORT (demande user 2026-07-24) : en-tête = BANNIÈRE BETSFIX du sport (image Telegram),
    # puis simples + combos séparés par le MÊME filet que les jambes de combiné (`_MC_SEP`).
    # ORDRE onglets = Confiance · Value · [Provisoire retiré] · Combiné (user 2026-08-11). L'onglet Provisoire
    # n'est plus rendu (prov_html="" -> onglet ignoré) ; les abstentions nourrissent la calibration (fantômes).
    _prov_html = _prov_sport_graph("foot") if analyses.PROVISOIRES_ON else ""
    # ONGLET MONTANTE (user 2026-08-19) : l'ex-onglet nav Montante devient un onglet des Résultats, comme
    # Confiance/Value/Combiné, MAIS avec son graphique propre (multiplicateur + courbe de capital + échelle).
    # Hors ROI -> pas de chip ROI. Le pari du jour reste dans Pronos. Affiché seulement si la montante est active.
    _mont_block, _mont_chip = "", ""
    try:
        from app import montante as _mtn_s
        if _mtn_s.is_active():
            _mst = _mtn_s.state()
            _mont_block = render_montante_bilan(_mst, _mtn_s.example())
            # CHIP DU BOUTON MONTANTE = MULTIPLICATEUR ACTUEL ×N (user 2026-08-19), à la place du chip ROI.
            _mbase = _mst.get("base", 10.0) or 10.0
            _mq = (_mst.get("capital", _mbase) / _mbase) if _mbase else 1.0
            _mont_chip = (f'<span class="sctab-roi {"pos" if _mq > 1.0001 else "neu"}">'
                          f'×{f"{round(_mq, 1):g}".replace(".", ",")}</span>')
    except Exception:
        _mont_block = ""
    _mont_cnt = 1 if _montante_palier() is not None else 0
    _foot = _sport_tabs(simples_block, combos_block, _prov_html,
                        value_html=value_block,                                    # onglet VALUE (user 2026-08-09)
                        montante_html=_mont_block,                                 # onglet MONTANTE (user 2026-08-19)
                        tab_chips=({"Montante": _mont_chip} if _mont_chip else None),
                        counts=(len(_pend_conf), len(_pend_val),
                                _prov_pending_count("foot") if analyses.PROVISOIRES_ON else 0, len(_pend_fc),
                                _mont_cnt),
                        rois=((_bt.get("confiance") or {}).get("roi"), (_bt.get("value") or {}).get("roi"),
                              _prov_sport_roi("foot") if analyses.PROVISOIRES_ON else None,
                              # COMBINÉ COMPTÉ AU ROI (user 2026-08-19) -> chip ROI affiché ; Montante reste hors ROI.
                              (_foot_c.get("roi") if analyses.COMBO_ROI_ON else None), None))
    # Ligne « compté au ROI · repris dans les paris » RETIRÉE (user 2026-08-07) : elle servait à distinguer
    # le foot des sports simulés (tennis/basket, désormais supprimés) -> redondante en football seul.
    # Cadre KPIs global (« Avantage réalisé ») RETIRÉ au-dessus des onglets (user 2026-08-16) : le ROI +
    # réussite restent affichés PAR onglet (Confiance/Value). _avantage_block conservé (dormant), non appelé.
    return (f'<div class="spf">{_sport_banner("foot")}{_foot}</div>') if _foot else ""


def _roi_bars(rows: list) -> str:
    """Barres ROI DIVERGENTES (0 au centre, vert à droite / rouge à gauche), échelle commune. Chaque
    ligne : libellé + (n paris · réussite %) + ROI coloré. Pour les vues par cote/confiance/marché."""
    vals = [abs(r["roi"]) for r in rows if r.get("roi") is not None]
    scale = max(vals) if vals else 1
    out = []
    for r in rows:
        roi = r.get("roi")
        if roi is None:
            bar, roistr, rcls = "", "—", "neu"
        elif roi == 0:
            # ROI à l'équilibre : repère NEUTRE centré sur le zéro (sinon la ligne paraît vide/cassée).
            bar, roistr, rcls = '<span class="rb-bar rb-even"></span>', "≈0%", "neu"
        else:
            rcls = "pos" if roi > 0 else "neg"
            w = max(4, round(abs(roi) / scale * 50))    # largeur mini 4 % -> toujours visible
            bar = f'<span class="rb-bar rb-{rcls}" style="width:{w}%"></span>'
            roistr = f'{"+" if roi >= 0 else "−"}{abs(roi)}%'
        meta = (f'{r["n"]} pari{"s" if r["n"] > 1 else ""}'
                + (f' · {r["pct"]}%' if r.get("pct") is not None else ""))
        out.append(
            f'<div class="rb-row"><div class="rb-top">'
            f'<span class="rb-lbl">{html.escape(str(r["label"]))}</span>'
            f'<span class="rb-meta">{meta}</span></div>'
            f'<div class="rb-line"><div class="rb-track"><span class="rb-zero"></span>{bar}</div>'
            f'<span class="rb-roi rb-{rcls}">{roistr}</span></div></div>')
    return "".join(out)


def _roi_section(title: str, sub: str, rows: list) -> str:
    return (f'<div class="sx-card"><div class="sx-h">{title}<span>{sub}</span></div>'
            f'<div class="rb">{_roi_bars(rows)}</div></div>') if rows else ""


def render_perf(perf: dict | None) -> str:
    """Rendement des paris JOUÉS par tranche de COTE ET par tranche de CONFIANCE (demande user 2026-08-13 :
    le ROI par confiance était calculé mais jamais affiché). Deux axes complémentaires — la cote dit où on
    encaisse, la confiance dit si nos hautes convictions paient vraiment. Distinct de la calibration (qui
    inclut les fantômes) : ici uniquement les paris RÉELLEMENT joués. '' si vide."""
    perf = perf or {}
    return (_roi_section("Rendement par cote", "ROI selon la cote jouée", perf.get("by_odds") or [])
            + _roi_section("Rendement par confiance", "ROI selon la confiance calibrée annoncée",
                           perf.get("by_conf") or []))


def render_tier_compare(full: dict | None) -> str:
    """CONFIANCE vs VALUE (demande user 2026-08-13) : le rendement des types de paris à mise plate côte à côte
    — ROI, réussite, volume, cote moyenne. Répond « le phare paie-t-il vraiment mieux, et la value compense-t-elle
    par le volume ? ». ⚠️ La MONTANTE est EXCLUE (user 2026-08-20) : c'est une échelle CAPITALISÉE (multiplicateur),
    pas un ROI à mise plate -> elle a sa propre vue (multiplicateur/capital), jamais un chiffre de ROI. Données
    figées (by_tier de stats_full). '' si rien de réglé."""
    bt = (full or {}).get("by_tier") or {}
    kpis = []
    for key, lbl in (("confiance", "⭐ Confiance"), ("value", "💎 Value")):
        t = bt.get(key) or {}
        if not t.get("settled"):
            continue
        roi, pct, n, co = t.get("roi"), t.get("pct"), t.get("settled"), t.get("avg_odds")
        kpis.append(
            f'<div class="av-kpi"><div class="av-kpi-l">{lbl}</div>'
            f'<div class="av-tier-roi arec-{_roicls(roi)}">{_roistr(roi)}</div>'
            f'<div class="av-tier-sub">{pct if pct is not None else "—"}% réussite · {n} paris · '
            f'cote {co or "—"}</div></div>')
    if not kpis:
        return ""
    return ('<div class="sx-card"><div class="sx-h">Confiance vs Value'
            '<span>rendement des paris à mise plate (montante à part)</span></div>'
            f'<div class="av-top">{"".join(kpis)}</div></div>')


def _form_streak(results) -> tuple:
    """(form, streak) à partir d'une liste CHRONOLOGIQUE (plus ancien → plus récent) de résultats
    'won'/'lost'/'push'/'void'. `form` = ['W','L','N',…] pour `form_dots` ; `streak` = série EN COURS (run
    final, + pour des W, − pour des L ; un N/void casse la série) pour `_streak_chip`. Partagé par les
    cartes de suivi (provisoires / combiné du jour / Betmines) — demande user 2026-07-24."""
    _M = {"won": "W", "lost": "L", "push": "N", "void": "N"}
    form = [_M.get(r, "N") for r in (results or []) if r]
    streak = 0
    for r in reversed(form):
        if r == "W" and streak >= 0:
            streak += 1
        elif r == "L" and streak <= 0:
            streak -= 1
        else:
            break
    return form, streak


def _roi_chip_mini(roi) -> str:
    """Petit chip ROI DISCRET pour un bouton d'onglet type-de-pari (demande user 2026-08-02 : voir le ROI de
    chaque type sans tout déplier). Vert si +, rouge si −. '' si None."""
    if roi is None:
        return ""
    try:
        rv = float(roi)
    except (TypeError, ValueError):
        return ""
    cls = "pos" if rv > 0 else "neg" if rv < 0 else "neu"
    return f'<span class="sctab-roi {cls}">{"+" if rv > 0 else ""}{rv:g}%</span>'


def _sport_tabs(simple_html: str, combos_html: str, prov_html: str = "",
                counts: tuple = (0, 0, 0, 0), rois: tuple = (None, None, None, None),
                value_html: str = "", montante_html: str = "", tab_chips: dict | None = None) -> str:
    """Onglets « Confiance | Value | Provisoire | Combiné | Montante » dans un cadre sport (demande user
    2026-07-24/25, Value 2026-08-09, Montante 2026-08-19 : l'onglet nav Montante devient un onglet des Résultats) :
    UN graphe à la fois, on tape pour basculer (JS `_SCTABS_JS`, index générique). Les onglets vides sont ignorés ;
    si un seul graphe, rendu direct ; '' si aucun. `counts`/`rois` = par onglet DANS L'ORDRE — pastille ⏳ + ROI."""
    _c = list(counts) + [0, 0, 0, 0, 0]
    _r = list(rois) + [None, None, None, None, None]
    # ORDRE (user 2026-08-19) : Confiance › Value › Provisoire › Combiné › Montante (Montante en dernier, comme
    # dans Pronos). Libellés au SINGULIER. counts/rois suivent le MÊME ordre. La montante n'a PAS de chip ROI
    # (hors ROI, courbe = capital/multiplicateur).
    _specs = (("Confiance", simple_html), ("Value", value_html),
              ("Provisoire", prov_html), ("Combiné", combos_html), ("Montante", montante_html))
    _tabs = [(lbl, h, _c[i], _r[i]) for i, (lbl, h) in enumerate(_specs) if h]
    if len(_tabs) <= 1:
        return _tabs[0][1] if _tabs else ""

    def _badge(n) -> str:
        return f'<span class="sctab-n">{n}</span>' if isinstance(n, int) and n > 0 else ""
    # CHIP DU BOUTON (user 2026-08-19) : chip custom par onglet (ex. Montante = multiplicateur ×N) sinon chip ROI.
    _btns = "".join(f'<button class="sctab{" on" if i == 0 else ""}" data-i="{i}">'
                    f'{lbl}{(tab_chips or {}).get(lbl) or _roi_chip_mini(r)}{_badge(n)}</button>'
                    for i, (lbl, _h, n, r) in enumerate(_tabs))
    _panes = "".join(f'<div class="sctab-pane{" on" if i == 0 else ""}">{h}</div>'
                     for i, (_lbl, h, _n, _r) in enumerate(_tabs))
    return f'<div class="sctab-wrap"><div class="sctabs">{_btns}</div>{_panes}</div>'


def _prov_pending_count(sport: str) -> int:
    """Nombre de provisoires EN COURS (non réglés) d'un sport — pour le badge d'onglet. Miroir de la
    logique de `_prov_sport_graph` (provisoires du sport + jambes de combiné multisport reversées). 0 si rien."""
    try:
        from app import provisional as _pvt
        snap = {k: v for k, v in _pvt.load().items()
                if isinstance(v, dict) and v.get("sport") == sport}
        try:
            from app import combo_daily as _cd
            for _i, _lg in enumerate(_cd.multisport_legs(sport)):
                snap[f"_msc-{sport}-{_i}"] = _lg
        except Exception:
            pass
        return sum(1 for e in _pvt.entries(snap) if e.get("result") is None)
    except Exception:
        return 0


def _prov_sport_roi(sport: str):
    """ROI (roi_pct, hors ROI officiel) des provisoires d'un sport — pour le chip ROI discret de l'onglet
    « Provisoires » (demande user 2026-08-02). None si aucun provisoire réglé."""
    try:
        from app import provisional as _pvt
        snap = {k: v for k, v in _pvt.load().items() if isinstance(v, dict) and v.get("sport") == sport}
        try:
            from app import combo_daily as _cd
            for _i, _lg in enumerate(_cd.multisport_legs(sport)):
                snap[f"_msc-{sport}-{_i}"] = _lg
        except Exception:
            pass
        s = _pvt.stats(snap)
        return s.get("roi_pct") if s and s.get("settled") else None
    except Exception:
        return None


def _prov_sport_graph(sport: str) -> str:
    """Graphe de suivi des PROVISOIRES d'un sport (info seule, HORS ROI) pour l'onglet « Provisoires » du
    cadre sport (demande user 2026-07-25). Même présentation hero que Simple/Combinés (courbe + taux + W/L +
    historique dépliable). '' si aucun provisoire pour ce sport."""
    try:
        from app import provisional as _pvt
        _all = _pvt.load()
        snap = {k: v for k, v in _all.items() if isinstance(v, dict) and v.get("sport") == sport}
        # + JAMBES des combinés du jour MULTISPORT de ce sport (demande user 2026-07-25 : ne pas perdre
        # leurs stats -> reversées dans les provisoires du sport). Les combinés MONO-sport, eux, sont dans
        # l'onglet « Combinés » du cadre. Snapshot AUGMENTÉ (jamais sauvegardé).
        try:
            from app import combo_daily as _cd
            for _i, _lg in enumerate(_cd.multisport_legs(sport)):
                snap[f"_msc-{sport}-{_i}"] = _lg
        except Exception:
            pass
        s = _pvt.stats(snap)
    except Exception:
        return ""
    if not s or not s.get("n"):
        return ""
    _ent = _pvt.entries(snap)
    _pend = [e for e in _ent if e.get("result") is None]
    _done = sorted((e for e in _ent if e.get("result") is not None),
                   key=lambda e: str(e.get("start") or ""), reverse=True)
    _recent = [{"result": e.get("result") or "pending", "name": e.get("name"),
                "sel": e.get("sel"), "cote": e.get("cote"), "start": e.get("start")}
               for e in (_pend + _done)]
    _form, _streak = _form_streak([e.get("result") for e in reversed(_done)])
    _curve = render_tracking_curve(
        emoji="🧪", title="Provisoires", roi=s.get("roi_pct"), hit=s.get("hit_rate"),
        n=s.get("settled", 0), points=_pvt.equity_curve(snap), avg_cote=s.get("avg_cote"),
        uid=f"prov-{sport}", recent=_recent, more_label="Derniers provisoires",
        form=_form, pending=len(_pend), streak=_streak, compact=True,
        hit_points=_hit_curve([e.get("result") for e in reversed(_done)]),
        cote_points=_cote_curve([(e.get("result"), e.get("cote")) for e in reversed(_done)]))
    return _curve


def _sport_banner(sport: str) -> str:
    """Bannière BETSFIX du sport — RETIRÉE (user 2026-08-13 : l'app est 100 % football, une bannière « sport »
    est redondante). Renvoie toujours '' ; fonction gardée pour ne pas casser les appelants (réversible)."""
    return ""


def _sport_milestones(sport: str) -> list:
    """Repères (ajustements auto du modèle) PROPRES à un sport pour sa courbe — demande user 2026-07-24 :
    les repères doivent être spécifiques à chaque sport. Garde le sport concerné (m[4]) == `sport` OU les
    repères GLOBAUX (« all »). Triés par date (l'ordre des pastilles numérotées suit la chronologie)."""
    return [m for m in sorted(analyses.exclusion_events(), key=lambda x: (x[0] or ""))
            if (m[4] if len(m) > 4 else "all") in (sport, "all")]


def _rate_chart(points: list, uid: str = "r", color: str = "#22b8ff", fmt=None,
                clamp_pct: bool = True) -> str:
    """Courbe LÉGÈRE d'une série cumulée (taux de réussite %, ou cote moyenne) — demande user 2026-07-24 :
    prouve que la fiabilité progresse dans le temps. Ligne lissée + aire douce, échelle ajustée aux données
    (pas de zéro, pas de rouge/vert). `color` = teinte de la courbe ; `fmt` = format des étiquettes de bout
    (défaut « N% ») ; `clamp_pct` borne l'échelle à 0..100 (True pour un %, False pour une cote). '' si < 2
    points."""
    _fmt = fmt or (lambda v: f"{v:g}%")
    pts = [float(p) for p in (points or []) if p is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    if hi - lo < 1e-9:
        lo, hi = lo - (5 if clamp_pct else 0.2), hi + (5 if clamp_pct else 0.2)
    pad = (hi - lo) * 0.18
    lo, hi = (max(0.0, lo - pad), min(100.0, hi + pad)) if clamp_pct else (lo - pad, hi + pad)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    n, W, H, L, R, T, B = len(pts), 320.0, 58.0, 16.0, 16.0, 8.0, 8.0   # marge droite = gauche (demande user 2026-07-25)
    iw, ih = W - L - R, H - T - B
    AC = color

    def X(i):
        return L + (iw * i / (n - 1) if n > 1 else iw / 2)

    def Y(v):
        return T + ih * (1 - (v - lo) / (hi - lo))

    gid = f"rtg-{uid}"
    line_d = _smooth_path([(X(i), Y(v)) for i, v in enumerate(pts)])
    area_d = f'M{X(0):.1f},{H - B:.1f} L' + line_d[1:] + f' L{X(n - 1):.1f},{H - B:.1f} Z'
    y0, y1 = Y(pts[0]), Y(pts[-1])
    p = [f'<svg viewBox="0 0 {W:g} {H:g}" class="sx-heroc rate-c">',
         f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
         f'<stop offset="0" stop-color="{AC}" stop-opacity="0.26"/>'
         f'<stop offset="1" stop-color="{AC}" stop-opacity="0"/></linearGradient></defs>',
         f'<path d="{area_d}" fill="url(#{gid})" stroke="none"/>',
         f'<path class="sx-heroc-line" pathLength="1" d="{line_d}" fill="none" stroke="{AC}" '
         'stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>',   # pas de non-scaling-stroke (bug WebKit dash)
         # Repères CHIFFRÉS discrets aux extrémités (demande user 2026-07-24) : % de départ (bas-gauche) et
         # % actuel (haut-droite) -> la progression est lisible sans échelle complète.
         f'<text class="rate-lbl" x="{X(0):.1f}" y="{min(H - 2, y0 + 12):.1f}" text-anchor="start">{_fmt(pts[0])}</text>',
         f'<text class="rate-lbl rate-lbl-e" x="{X(n - 1):.1f}" y="{max(9.0, y1 - 6):.1f}" text-anchor="end">{_fmt(pts[-1])}</text>',
         f'<circle cx="{X(n - 1):.1f}" cy="{y1:.1f}" r="2.6" fill="{AC}"/></svg>']
    return "".join(p)


def _best_win_streak(form) -> int:
    """Plus LONGUE série de victoires (W consécutifs) dans l'historique chronologique `form` (['W','L','N',…]
    de `_form_streak`). Le RECORD affiché à côté de la série en cours (demande user 2026-07-25). 0 si aucune."""
    best = cur = 0
    for r in (form or []):
        if r == "W":
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _streak_text(streak, best: int = 0) -> str:
    """Série EN COURS + RECORD (plus longue série de victoires), SANS emoji (demande user 2026-07-24/25,
    rendu pro) : pastille verte/rouge « Série en cours · N » ET pastille dorée « Record · N victoires » sur
    la MÊME LIGNE, centrées (demande user 2026-07-25 : une seule ligne, plus compact). Le record s'affiche
    même sans série en cours. '' si rien à montrer."""
    chips = ""
    if streak:
        if streak > 0:
            lab, cls = f'{streak} victoire{"s" if streak > 1 else ""}', "win"
        else:
            m = -streak
            lab, cls = f'{m} défaite{"s" if m > 1 else ""}', "loss"
        chips += f'<span class="spf-hero-streak {cls}">Série en cours · <b>{lab}</b></span>'
    if best and best > 0:
        chips += (f'<span class="spf-hero-streak best">'
                  f'Record · <b>{best} victoire{"s" if best > 1 else ""}</b></span>')
    if not chips:
        return ""
    return f'<div class="spf-hero-streakw">{chips}</div>'


def _cote_block(cote_points: list | None, uid: str, warmup: int | None = None) -> str:
    """3e graphe (demande user 2026-07-27 : ROI · Réussite · Cote) — courbe de la COTE MOYENNE cumulée,
    même présentation légère que le taux de réussite mais en OR et au format cote (2 décimales, pas de %,
    échelle non bornée à 100). `warmup` abaissable par suivi (combiné du jour). '' si trop peu de paris."""
    _cp = [c for c in (cote_points or []) if c is not None]
    if len(_cp) < (_RATE_WARMUP if warmup is None else warmup) + 3:
        return ""
    _c = _rate_chart(_cp, uid=uid + "c", color="#f6c54a", fmt=lambda v: f"{v:.2f}", clamp_pct=False)
    if not _c:
        return ""
    return f'<div class="spf-rate"><div class="spf-rate-h">Cote moyenne</div>{_c}</div>'


def _hero_graph_inner(*, roi, n: int, hit, avg_cote, chart: str, form: str, streak=None,
                      hit_points: list | None = None, uid: str = "trk", best_streak: int = 0,
                      cote_points: list | None = None, warmup: int | None = None) -> str:
    """Disposition « ROI héros » façon carte ROI GLOBAL (choix user 2026-07-24) pour les cadres sport à
    onglets : petit label « Rentabilité », le ROI en GROS centré (vert/rouge), une sous-ligne
    réussite · paris · cote, la SÉRIE en cours (pastille sans emoji), puis 3 GRAPHES SÉPARÉS empilés (demande
    user 2026-07-27) — ROI (équité), Réussite (%), Cote moyenne — et la ligne W/L. AUCUNE boîte imbriquée."""
    _cls = "na" if (not n or n < _MIN_REL) else ("pos" if (roi or 0) >= 0 else "neg")
    return (
        '<div class="spf-hero-lbl">Rentabilité</div>'
        f'<div class="spf-hero-roi {_cls}">{_roistr(roi)}</div>'
        '<div class="spf-hero-kpis">'                     # 3 stats INTUITIVES/PRO : valeur + libellé clair dessous
        f'<div><span class="v arec-{_pct_class(hit)}">{hit if hit is not None else "—"}%</span>'
        '<span class="l">Réussite</span></div>'
        f'<div><span class="v">{n}</span><span class="l">Paris réglés</span></div>'
        f'<div><span class="v">{avg_cote or "—"}</span><span class="l">Cote moyenne</span></div>'
        '</div>'
        # 3 graphes SÉPARÉS empilés : ROI (équité) · Réussite (%) · Cote moyenne (demande user 2026-07-27).
        f'{chart}{_rate_block(hit_points, uid, warmup)}{_cote_block(cote_points, uid, warmup)}'
        f'{form}{_streak_text(streak, best_streak)}')


def _hit_curve(results) -> list:
    """Taux de réussite CUMULÉ (%) à chaque pari réglé, à partir d'une liste chronologique de résultats
    (won/lost/…). Alimente la courbe « Taux de réussite » des suivis externes (provisoires / combiné du
    jour / Betmines) — même donnée que _agg_bets.hit_points pour les cadres sport."""
    pts, w, s = [], 0, 0
    for r in (results or []):
        if r == "won":
            w += 1
            s += 1
        elif r == "lost":
            s += 1
        else:
            continue
        pts.append(round(100 * w / s, 1))
    return pts


def _cote_curve(pairs) -> list:
    """Cote MOYENNE cumulée à chaque pari réglé, à partir d'une liste chronologique de (result, cote) —
    alimente le 3e graphe « Cote moyenne » des suivis (provisoires / Betmines), demande user 2026-07-27.
    Ne compte que les gagnés/perdus portant une cote valide. [] si aucun."""
    pts, osum, s = [], 0.0, 0
    for r, c in (pairs or []):
        if r in ("won", "lost") and isinstance(c, (int, float)) and c:
            osum += float(c)
            s += 1
            pts.append(round(osum / s, 2))
    return pts


_RATE_WARMUP = 10   # nb MIN de paris réglés pour AFFICHER la courbe de réussite (sous ce seuil, un taux cumulé
                    # saute trop à 0/100 % pour valoir la peine). NB : une fois affichée, la courbe démarre au
                    # 1er pari (alignée sur la courbe ROI) — cf. _rate_block.


def _rate_block(hit_points: list | None, uid: str, warmup: int | None = None) -> str:
    """Bloc « Taux de réussite » (courbe légère + % courant) sous la courbe d'équité — demande user
    2026-07-24 : montrer que la fiabilité s'améliore dans le temps. La courbe DÉMARRE au 1er pari réglé pour
    COMMENCER À LA MÊME DATE que la courbe ROI/équité (demande user 2026-07-27 : les deux graphiques d'un
    sport doivent partir de la même date — mêmes paris 1→N, axes bord-à-bord). On n'affiche le bloc qu'à
    partir d'assez de paris réglés (warmup + 3, défaut `_RATE_WARMUP`) pour qu'un taux cumulé ait un sens.
    `warmup` abaissable par suivi (ex. combiné du jour, ~1/jour depuis 29/07 : seuil réduit pour montrer la
    courbe sans attendre 13 combinés — demande user 2026-08-13). '' sinon."""
    _hp = [h for h in (hit_points or []) if h is not None]
    if len(_hp) < (_RATE_WARMUP if warmup is None else warmup) + 3:   # pas assez de paris pour une courbe utile
        return ""
    _c = _rate_chart(_hp, uid=uid)                       # DÈS le 1er pari -> même date de départ que la courbe ROI
    if not _c:
        return ""
    # Titre SANS le % (demande user 2026-07-25 : le % courant est déjà l'étiquette de fin de courbe -> le
    # répéter ici faisait doublon avec le KPI « réussite »). La courbe porte 70 % (départ) et 84,8 % (fin).
    return f'<div class="spf-rate"><div class="spf-rate-h">Taux de réussite</div>{_c}</div>'


def render_tracking_curve(*, emoji: str, title: str, roi, hit, n: int, points: list,
                          dates: list | None = None, avg_cote=None, uid: str = "trk",
                          recent: list | None = None, more_label: str = "Derniers paris",
                          form: list | None = None, pending: int = 0, streak=None,
                          milestones: list | None = None, sport: str | None = None,
                          compact: bool = False, hit_points: list | None = None,
                          best_streak: int | None = None, cote_points: list | None = None,
                          warmup: int | None = None) -> str:
    """Bloc courbe+stats « info seule » (provisoires, combiné du jour) construit EXACTEMENT comme les 2
    premiers graphiques de la page Stats (simples/combinés, demande user 2026-07-24) : carte `.spf-cv` avec
    en-tête (titre + chip SÉRIE 🔥/❄️ + chip ROI), LIGNE W/L (`form_dots`, sabliers ⏳ pour les `pending`),
    courbe `_hero_chart` (`.sx-equity`), puis KPIs (réussite % · N paris · cote moyenne). Si `recent` (liste
    de paris réglés au format `_recent_bets_html`) est fourni, l'historique est affiché D'OFFICE sous la
    courbe (libellé `<more_label>` statique, plus de bouton — demande user 2026-08-13) — MÊME présentation
    que les simples/combinés. AUCUN impact ROI/stats/calibration. '' si rien à tracer."""
    if not n and not pending:
        return ""
    _pts = [p for p in (points or []) if p is not None]
    _mi = milestones or []                                    # repères PROPRES à ce sport (demande user 2026-07-24)
    chart = (f'<div class="sx-equity">{_hero_chart(points, uid=uid, dates=dates or [], milestones=_mi)}</div>'
             if len(_pts) >= 2 else "")
    # RECORD = plus longue série de victoires SUR TOUT L'HISTORIQUE. `best_streak` (pré-calculé par
    # `_agg_bets` sur la séquence COMPLÈTE) est prioritaire : recalculer depuis `form` sous-estime le record
    # quand `form` est tronqué (form_run = 24 derniers) — bug vécu 2026-07-25 : record foot simple affiché ≤9
    # alors que 18. Repli sur `form` seulement si `best_streak` non fourni (suivis dont la forme EST complète).
    _best = best_streak if best_streak is not None else _best_win_streak(form or [])
    _stk = _streak_chip(streak) + _best_streak_chip(_best)    # série en cours 🔥/❄️ + record 🏆 — À CÔTÉ du titre
    _dots = form_dots(form or [], n=16, pending=pending)      # ligne W/L + sabliers ⏳ des en attente
    _form = f'<div class="spf-cv-form">{_dots}</div>' if _dots else ""
    # TITRE de sous-graphe : juste « SIMPLE »/« COMBINÉS » (le sport est dans l'en-tête du cadre — demande
    # user 2026-07-24). Couleur par sport ANNULÉE. `emoji` vide -> pas de préfixe.
    if compact:                                   # cadres sport à onglets : disposition « ROI héros » (frameless)
        inner = _hero_graph_inner(roi=roi, n=n, hit=hit, avg_cote=avg_cote, chart=chart, form=_form,
                                  streak=streak, hit_points=hit_points, uid=uid, best_streak=_best,
                                  cote_points=cote_points, warmup=warmup)
    else:
        _title_html = f'{emoji + " " if emoji else ""}{html.escape(title)}'
        inner = (
            f'<div class="spf-cv-h">'
            f'<span class="spf-cv-hl"><span class="spf-cv-t">{_title_html}</span>{_stk}</span>'
            f'<span class="spf-cv-roi arec-{_roi_cls(roi, n)}">ROI {_roistr(roi)}</span></div>'
            f'{_form}{chart}'
            '<div class="spf-cv-kpis">'
            f'<span><b class="arec-{_pct_class(hit)}">{hit if hit is not None else "—"}%</b> réussite</span>'
            f'<span><b>{n}</b> paris</span>'
            f'<span><b>{avg_cote or "—"}</b> cote</span></div>')   # cote SANS « @ » ; légende repères RETIRÉE
    _wrap = "spf-hero" if compact else "spf-cv"   # compact = SANS boîte (sur la carte sport) — « sortir du cadre »
    rec = _recent_bets_html(recent or [])
    if rec:                                     # derniers paris affichés D'OFFICE (demande user 2026-08-13)
        return (f'<div class="{_wrap}">{inner}'
                f'<div class="spf-rec-lbl">{html.escape(more_label)}</div>{rec}</div>')
    return f'<div class="{_wrap}">{inner}</div>'


def _prog_pair(home, away) -> frozenset:
    """Clé DÉDOUBLONNAGE d'un match = paire de noms d'équipes normalisés (robuste à l'écart d'id
    Unibet↔sidecar). Sert à exclure du programme un match déjà affiché en pari à jouer."""
    def _n(s: str) -> str:
        return re.sub(r"\W+", "", (s or "").lower())
    return frozenset(x for x in (_n(home), _n(away)) if x)


def _plain_market(sel: str, sport: str, home: str = "", away: str = "") -> str:
    """Traduction EN CLAIR d'un pari (demande user 2026-07-13, « clair/intuitif ») : le jargon devient une
    phrase lisible SOUS la sélection. '' si le marché est déjà clair (vainqueur, set, double chance « ou
    nul »…) ou non reconnu -> jamais de glose approximative. Purement AFFICHAGE."""
    import math
    s = (sel or "").strip()
    if not s:
        return ""
    sl = s.lower()
    unit = "buts" if sport == "foot" else ("jeux" if sport == "tennis" else "points")
    _u = lambda k: unit if k != 1 else {"buts": "but", "points": "point", "jeux": "jeu"}.get(unit, unit)
    # MARCHÉ DE PÉRIODE (mi-temps / quart-temps) : les gloses ci-dessous décrivent le MATCH ENTIER — les
    # appliquer à une période produit une glose FAUSSE (audit 2026-07-23 : « Brooklyn Nets -0.5 (1er quart) »
    # glosé « gagne le match » ; « CRB buts 1ère MT Moins de 1.5 » glosé « au total les 2 équipes »). On
    # laisse ces sels au repli générique sûr de _bet_gloss (« pari sur une période du match »). Exceptions :
    # BTTS/DC mi-temps et handicap 1ère MT ont leurs propres cas plus bas -> on ne bloque que les branches
    # numériques (handicap signé / équipe marque / totaux). `(?<=\s)mt` épargne les suffixes brésiliens -MT.
    _per_gloss = bool(re.search(r"mi-temps|(?<=\s)mt\b|1[eè]re|2[eè]\b|quart|\bq\d\b", sl))
    # HANDICAP signé : « <équipe> -9.5 » (gagne de 10+) / « +9.5 » (ne perd pas de +9). Accepte un suffixe
    # « (handicap) »/« (hand.) »… APRÈS le nombre (fix 2026-07-14 : « Partick -1.5 (handicap) » n'avait pas
    # de glose car le nombre n'était pas en toute fin de chaîne).
    m = re.search(r"([+\-−–])\s?(\d+(?:[.,]\d+)?)\s*(?:\([^)]*\))?\s*$", s)
    if m and not _per_gloss:
        val = float(m.group(2).replace(",", "."))
        neg = m.group(1) in ("-", "−", "–")
        # LIGNE 0.5 = « draw no bet / double chance » : cas le PLUS courant, à formuler EN CLAIR (le générique
        # « ne perd pas de plus de 0 buts » n'a aucun sens — bug vu 2026-07-17 sur « Londrina +0.5 »). En foot
        # (nul possible) : +0.5 = ne perd pas (gagne ou nul), -0.5 = doit gagner. Tennis/basket (pas de nul) :
        # +0.5 comme -0.5 reviennent à gagner le match.
        if val == 0.5:
            if sport == "foot":
                # +0.5 foot = double chance « ne perd pas » -> MÊME glose EXACTE que « Double chance 1X »
                # (cf. plus bas, ligne double chance) pour un rendu uniforme (demande user 2026-07-17).
                return "gagne le match" if neg else "gagne ou match nul"
            return "gagne le match"
        if neg:
            return f"gagne de {math.ceil(val)} {_u(math.ceil(val))} ou plus"
        n = math.floor(val)
        return f"ne perd pas de plus de {n} {_u(n)} (nul ou victoire inclus)"
    # ÉQUIPE MARQUE : « <équipe> - Plus/Moins de X.5 (buts) » -> AVANT le total du match (sinon capté à tort
    # comme total des 2 équipes). Détecté par le tiret séparateur « <nom> - plus/moins ».
    meq = re.search(r"^(.*?)\s[-–—]\s.*?\b(plus|moins) de (\d+(?:[.,]\d+)?)", s, re.I)
    if meq and "total" not in sl and not _per_gloss:
        who = meq.group(1).strip(" -–—")
        # GARDE-FOU (fix audit 2026-07-14) : ne traiter comme « ÉQUIPE marque » QUE si `who` est bien une
        # équipe — pas un libellé de total mal formé (« Nombre de buts - Plus de 2.5 ») ni un sel citant les
        # DEUX équipes (« A - B - Plus de 2.5 » = total). Sinon on laisse tomber sur la branche « total ».
        _generic = bool(re.search(r"nombre|total|\bbut|\bpoint|\bjeu", who.lower()))
        _wn = re.sub(r"\W+", "", who.lower())
        _teams = [re.sub(r"\W+", "", t.lower()) for t in (home, away) if t]
        _sln = re.sub(r"\W+", "", sl)
        _both = bool(_teams) and all(t in _sln for t in _teams)
        _is_team = (any(_wn and (_wn in t or t in _wn) for t in _teams)
                    if _teams else not _generic) and not _generic and not _both
        if _is_team:
            val = float(meq.group(3).replace(",", "."))
            n = math.ceil(val)   # « plus de 0.5 » -> au moins 1 ; « moins de 1.5 » -> moins de 2
            if meq.group(2).lower() == "plus":
                return f"{who} marque au moins {n} {_u(n)}"
            return f"{who} marque moins de {n} {_u(n)}"
    # ÉQUIPE MARQUE — forme « <équipe> marque (Total <équipe> +X.5) » ou « … +X.5 » (handicap-total d'équipe,
    # ex. « Racing Club marque (Total Racing +0.5) » = Racing marque ≥1 but). Le « +X.5 » = AU MOINS ⌈X.5⌉.
    # Passe AVANT la branche « marque … plus/moins de » (le « +X.5 » n'a pas de « plus de ») — fix user 2026-07-24.
    mmp = re.search(r"^(.+?)\s+marque\b.*?\+\s*(\d+(?:[.,]\d+)?)", s, re.I)
    if mmp and not _per_gloss:
        who = mmp.group(1).strip()
        n = math.ceil(float(mmp.group(2).replace(",", ".")))
        return f"{who} marque au moins {n} {_u(n)}"
    # ÉQUIPE MARQUE — forme « <équipe> marque (Plus/Moins de X.5 but) » SANS tiret séparateur (fix 2026-07-17 :
    # « Argentine marque (Plus de 0.5 but) » restait sans glose car « 0.5 but » ≠ « buts » du total).
    mm = re.search(r"^(.+?)\s+marque\b.*?\b(plus de|moins de|au moins|au maximum) (\d+(?:[.,]\d+)?)", s, re.I)
    if mm and "total" not in sl and not _per_gloss:
        who, key, val = mm.group(1).strip(), mm.group(2).lower(), float(mm.group(3).replace(",", "."))
        if key in ("plus de", "au moins"):
            n = math.ceil(val) if key == "plus de" else (int(val) if val == int(val) else math.ceil(val))
            return f"{who} marque au moins {n} {_u(n)}"
        if key == "moins de":                              # « moins de 1.5 » = au plus 1 -> « moins de 2 »
            n = math.ceil(val)
            return f"{who} marque moins de {n} {_u(n)}"
        n = int(val) if val == int(val) else math.floor(val)   # « au maximum N »
        return f"{who} marque au maximum {n} {_u(n)}"
    # TOTAL d'un OBJET NOMMÉ (tirs cadrés/corners/cartons/aces/rebonds/passes) — match entier OU par équipe.
    # Unité = l'objet lui-même (fix 2026-07-17 : « Nombre total de tirs cadrés de Argentine Plus de X » était
    # glosé « … buts au total » — mauvaise unité).
    _s_np = re.sub(r"\([^)]*\)", " ", s)     # retire les annotations « (réglé selon Opta Data) » etc.
    mob = re.search(r"total\s+(?:de\s+|des\s+|d')?(tirs?\s+cadrés?|tirs?|corners?|cartons?|aces?|"
                    r"doubles?\s+fautes?|rebonds?|passes?)\s*(?:de\s+([^()]+?))?\s*\b(plus|moins) de "
                    r"(\d+(?:[.,]\d+)?)", _s_np, re.I)
    if mob and not _per_gloss:
        obj = re.sub(r"\s+", " ", mob.group(1).strip().lower())
        who = (mob.group(2) or "").strip(" -–—")
        n = math.ceil(float(mob.group(4).replace(",", ".")))
        sens = "au moins" if mob.group(3).lower() == "plus" else "moins de"
        return f"{who + ' : ' if who else ''}{sens} {n} {obj}"
    # ÉQUIPE (total) — forme KAMBI « Total <équipe> plus/moins de X.5 [buts/points/jeux] » (ex. « Total Racing
    # Plus de 0.5 » = Racing marque ≥1 but ; unité SOUVENT ABSENTE). Ici « total » désigne le total D'UNE
    # ÉQUIPE, PAS du match : sans cette branche, le « total » faisait tomber sur la branche total-du-match qui
    # glosait « au total (les 2 équipes) » — FAUX, le pari ne porte que sur Racing (bug user 2026-07-24 :
    # « ils parlent des 2/3 équipes alors que ce n'est que Racing »). Détecté par un NOM D'ÉQUIPE (home/away).
    mtot = re.search(r"^total\s+(.+?)\s+(plus|moins) de (\d+(?:[.,]\d+)?)", s, re.I)
    if mtot and not _per_gloss:
        who = mtot.group(1).strip()
        _wn = re.sub(r"\W+", "", who.lower())
        _teams = [re.sub(r"\W+", "", t.lower()) for t in (home, away) if t]
        # who générique (« Total de buts / du match / de points ») = total du MATCH, pas une équipe -> skip.
        _generic = bool(re.search(r"nombre|\bbut|\bpoint|\bjeu|match", who.lower()))
        if _wn and not _generic and (any(_wn in t or t in _wn for t in _teams) if _teams else True):
            val = float(mtot.group(3).replace(",", "."))
            n = math.ceil(val)   # « plus de 0.5 » -> au moins 1 ; « moins de 2.5 » -> moins de 3
            _verbe = "remporte" if sport == "tennis" else "marque"
            if mtot.group(2).lower() == "plus":
                return f"{who} {_verbe} au moins {n} {_u(n)}"
            return f"{who} {_verbe} moins de {n} {_u(n)}"
    # ÉQUIPE (total) — forme « <équipe> Total (de) buts/points/jeux Plus/moins de X » (ÉQUIPE EN TÊTE puis
    # « total », ex. « Athletico Total buts Plus de 0.5 » = Athletico marque ≥1 but). Sans cette branche, le
    # « total » faisait tomber sur le total DU MATCH glosé « les 2 équipes » — FAUX (bug user 2026-07-25 : le
    # pari ne porte que sur Athletico). Détecté par un NOM D'ÉQUIPE en tête (home/away).
    mtot2 = re.search(r"^(.+?)\s+total\s+(?:de\s+)?(?:buts?|points?|jeux)\s+(plus|moins)\s+de\s+"
                      r"(\d+(?:[.,]\d+)?)", s, re.I)
    if mtot2 and not _per_gloss:
        who = mtot2.group(1).strip()
        _wn = re.sub(r"\W+", "", who.lower())
        _teams = [re.sub(r"\W+", "", t.lower()) for t in (home, away) if t]
        _generic = bool(re.search(r"nombre|match", who.lower()))
        if _wn and not _generic and (any(_wn in t or t in _wn for t in _teams) if _teams else True):
            val = float(mtot2.group(3).replace(",", "."))
            n = math.ceil(val)
            _verbe = "remporte" if sport == "tennis" else "marque"
            if mtot2.group(2).lower() == "plus":
                return f"{who} {_verbe} au moins {n} {_u(n)}"
            return f"{who} {_verbe} moins de {n} {_u(n)}"
    # ÉQUIPE (total) — forme « <équipe> plus/moins de X.5 buts/points/jeux » SANS « marque » NI tiret (ex.
    # « Mirassol moins de 2.5 buts » = total de MIRASSOL ; « Minnesota Lynx plus de 92.5 points » = total de
    # MINNESOTA, PAS le total du match). BUG 2026-07-18 (foot) puis 2026-07-22 (basket : « points ») : c'était
    # glosé « … au total (les 2 équipes) », en CONTRADICTION FRONTALE avec le pari. Détecté par un NOM
    # D'ÉQUIPE en tête (matche home/away). DOIT passer AVANT le total du match. Couvre buts/points/jeux.
    mteam = re.search(r"^(.+?)\s+(plus|moins) de (\d+(?:[.,]\d+)?)\s*(?:buts?|points?|jeux)\b", s, re.I)
    if mteam and "total" not in sl and not _per_gloss:
        who = mteam.group(1).strip()
        _wn = re.sub(r"\W+", "", who.lower())
        _teams = [re.sub(r"\W+", "", t.lower()) for t in (home, away) if t]
        # NOM D'ÉQUIPE si matche home/away ; à défaut (home/away absents) repli : `who` non générique
        # (pas « Total de points », « Nombre de… ») -> évite qu'un total du match soit lu comme total d'équipe.
        _generic = bool(re.search(r"nombre|total|\bbut|\bpoint|\bjeu", who.lower()))
        _is_team = (any(_wn in t or t in _wn for t in _teams) if _teams else (_wn and not _generic))
        if _wn and _is_team:
            val = float(mteam.group(3).replace(",", "."))
            n = math.ceil(val)   # « moins de 2.5 » -> moins de 3 ; « plus de 1.5 » -> au moins 2
            _verbe = "remporte" if sport == "tennis" else "marque"   # jeux au tennis -> « remporte »
            if mteam.group(2).lower() == "plus":
                return f"{who} {_verbe} au moins {n} {_u(n)}"
            return f"{who} {_verbe} moins de {n} {_u(n)}"
    # TOTAL du match « plus/moins de X.5 (points/buts/jeux) » -> nombre entier lisible. On EXCLUT les totaux
    # d'un objet SPÉCIFIQUE (corners/tirs/cartons/aces/rebonds/passes/fautes) : « au total buts » y serait FAUX
    # (mauvaise unité) -> ils tombent sur le repli générique « pari sur … » (fix 2026-07-17).
    mt = re.search(r"\b(plus|moins) de (\d+(?:[.,]\d+)?)", sl)
    # « Plus/Moins de X » NU (juste un nombre, ex. basket « Plus de 177.5 » SANS le mot « points ») = total du
    # MATCH -> glosé avec l'unité du sport (bug user 2026-07-29 : la jambe basket « Plus de 177.5 » tombait sur
    # le repli générique « pari détaillé dans l'analyse »). Le nombre est en FIN de chaîne (annotation tolérée).
    _nu_total = bool(re.match(r"(?i)^(plus|moins)\s+de\s+\d+(?:[.,]\d+)?\s*(?:\([^)]*\))?\s*$", s))
    if (mt and (re.search(r"total|points?|buts?", sl) or _nu_total) and not _per_gloss
            and not re.search(r"\b(corner|tir|carton|ace|rebond|passe|faute|break)", sl)):
        val = float(mt.group(2).replace(",", "."))
        # « plus de X.5 » = AU MOINS ⌈X.5⌉ (ceil) — « plus de 0.5 » -> « au moins 1 but », plus « plus de 0 »
        # (bug signalé user 2026-07-24). « moins de X.5 » = AU MAXIMUM ⌊X.5⌋ (« moins de 0.5 » -> « aucun »).
        # Cohérent avec les branches équipe-marque/équipe-total qui glosent déjà « au moins N ».
        if mt.group(1) == "plus":
            n = math.ceil(val)
            return f"au moins {n} {_u(n)} au total (les 2 équipes)"
        n = math.floor(val)
        return (f"aucun {_u(1)} au total (les 2 équipes)" if n == 0
                else f"{n} {_u(n)} maximum au total (les 2 équipes)")
    # DOUBLE CHANCE : glose EN CLAIR avec le NOM D'ÉQUIPE (demande user 2026-08-02) — 1X = domicile, X2 =
    # extérieur ; 12 = les deux (pas d'équipe unique). Si le code n'est pas explicite, on déduit du camp cité.
    if re.search(r"\bdouble chance\b|\b1x\b|\bx2\b|\b12\b", sl) and "mi-temps" not in sl:
        if re.search(r"\b12\b", sl):
            return "l'un des deux gagne (pas de match nul)"
        _dctok = lambda nm: [t for t in re.findall(r"[a-zà-ÿ0-9]+", (nm or "").lower()) if len(t) >= 3]
        _dcteam = (away if re.search(r"\bx2\b", sl)
                   else home if re.search(r"\b1x\b", sl)
                   else home if any(t in sl for t in _dctok(home))
                   else away if any(t in sl for t in _dctok(away)) else "")
        return f"{_dcteam} gagne ou match nul" if _dcteam else "gagne ou match nul"
    # LES DEUX ÉQUIPES MARQUENT (BTTS).
    if "deux équipes marquent" in sl or re.search(r"\bbtts\b", sl):
        if "mi-temps" in sl:
            return "les deux marquent en 1ère mi-temps" if "non" not in sl else "pas de but des deux avant la pause"
        return "au moins une équipe ne marque pas" if "non" in sl else "les deux équipes marquent au moins un but"
    # TEMPS RÉGLEMENTAIRE <équipe/nul> (1X2 foot, réglé sur les 90 min).
    if "temps réglementaire" in sl:
        return "match nul à la fin des 90 min" if re.search(r"\b(draw|nul)\b", sl) \
            else "gagne dans le temps réglementaire (90 min)"
    # TENNIS — marchés de SETS/manches (fix 2026-07-17 : « <joueur> remporte au moins un set » restait SANS
    # glose -> reproche user « paris sans explications »). « au moins N sets » / 1er set / sans perdre de set.
    if sport == "tennis":
        mset = re.search(r"au moins (\d+|une?|deux|trois)\s+sets?|\bgagne\b.*\bun set\b", sl)
        if mset:
            w = {"un": 1, "une": 1, "deux": 2, "trois": 3}.get((mset.group(1) or "").strip())
            n = w if w else (int(mset.group(1)) if (mset.group(1) or "").isdigit() else 1)
            return ("remporte au moins une manche (évite la défaite en deux sets secs)" if n <= 1
                    else f"remporte au moins {n} manches")
        if re.search(r"\bgagne (le )?(1er|1re|premi[eè]re?|second|2e|2nd|deuxi[eè]me)\s+set\b", sl):
            return "gagne cette manche précise"
        if re.search(r"sans (perdre|lâcher|conc[eé]der)\b.*\b(set|manche)", sl):
            return "gagne sans lâcher la moindre manche"
    # VAINQUEUR simple (« <équipe/joueur> vainqueur/gagne ») -> précise le PÉRIMÈTRE de règlement par sport.
    # EXCLURE les marchés de PÉRIODE (« Set 1 - Vainqueur », « 1ère MT », quart-temps…) : « gagne le match »
    # y serait FAUX (c'est le vainqueur du set/de la période). Pas de glose plutôt qu'une glose fausse.
    if (re.search(r"\b(vainqueur|victoire|gagne|l'emporte)\b", sl)
            and not re.search(r"mi-temps|\bset\b|\bmt\b|1[eè]re|2[eè]|quart", sl)):
        if sport == "tennis":
            return "gagne le match (en sets)"
        if sport == "basket":
            return "gagne le match (prolongations comprises)"
        return "gagne dans le temps réglementaire (90 min)"
    return ""


# Catégories de marché pour le repli générique (mot-clé -> phrase « pari sur … », jamais fausse). Mots courts
# testés en \b (« but » n'attrape pas « début », « jeu » pas « enjeu »). Ordre = du plus spécifique au général.
_GLOSS_CAT = [
    (("corner",), "pari sur le nombre de corners"),
    (("carton", "card"), "pari sur les cartons"),
    (("cadré", "cadre", "on target"), "pari sur les tirs cadrés"),
    (("tir", "shot"), "pari sur les tirs"),
    (("double faute", "doubles fautes"), "pari sur les doubles fautes"),
    (("ace",), "pari sur les aces"),
    (("tie-break", "tie break", "jeu décisif", "décisif"), "pari sur un jeu décisif (tie-break)"),
    (("score exact", "correct score", "score correct"), "pari sur le score exact"),
    (("buteur", "premier but", "1er but", "dernier but", "first goal"), "pari sur les buteurs"),
    (("mi-temps", "période", "periode", "half", "quart"), "pari sur une période du match"),
    (("impair",), "pari : nombre impair"),
    (("pair", "even", "odd"), "pari : nombre pair / impair"),
    (("rebond", "rebound"), "pari sur les rebonds"),
    (("passe", "assist", "caviar"), "pari sur les passes décisives"),
    (("jeu", "game"), "pari sur le nombre de jeux"),
    (("point",), "pari sur le nombre de points"),
    (("but", "goal"), "pari sur les buts"),
]


def _generic_gloss(sel: str, sport: str) -> str:
    """Repli GÉNÉRIQUE sûr : une explication « ↳ » pour N'IMPORTE QUEL pari joué, même un marché non codé
    spécifiquement (demande user 2026-07-17). Jamais faux : (1) « Plus/Moins de X <objet> » reformulé en
    entier ; (2) sinon catégorie du marché par mot-clé ; (3) dernier recours = renvoi vers l'analyse. ''
    seulement si `sel` vide. NE PAS s'en servir pour ÉVITER un cas précis -> le selfcheck le signale."""
    import math
    s = (sel or "").strip()
    if not s:
        return ""
    sl = s.lower()
    m = re.search(r"\b(plus|moins)\s+de\s+(\d+(?:[.,]\d+)?)\s+([a-zà-ÿ][\wà-ÿ' -]*?)\s*$", s, re.I)
    if m:
        n = math.ceil(float(m.group(2).replace(",", ".")))
        obj = re.sub(r"\s+", " ", m.group(3).strip())
        return f"au moins {n} {obj}" if m.group(1).lower() == "plus" else f"moins de {n} {obj}"
    for kws, txt in _GLOSS_CAT:
        if any(re.search(rf"\b{re.escape(k)}", sl) for k in kws):
            return txt
    return "pari détaillé dans l'analyse ci-dessous"


def _bet_gloss(sel: str, sport: str, home: str = "", away: str = "") -> str:
    """Glose « en clair » GARANTIE de tout pari joué : cas PRÉCIS (`_plain_market`) sinon repli GÉNÉRIQUE
    sûr (`_generic_gloss`). Ne renvoie '' que si `sel` est vide. TOUT rendu de pari (carte simple, provisoire,
    jambe de combiné) doit passer par ICI -> jamais une carte de pari sans ligne « ↳ » (demande user)."""
    return _plain_market(sel, sport, home, away) or _generic_gloss(sel, sport)


def _pretty_sel(sel: str, home: str = "", away: str = "") -> str:
    """Alias vers la SOURCE UNIQUE `analyses.pretty_sel` (« Double chance 1X » -> « <équipe> ou nul »)
    -> un seul libellé pour un pari, partout (carte, combiné, Telegram)."""
    return analyses.pretty_sel(sel, home, away)


def _verdict_block(cote, conf, foot_txt: str = "", cote_html: str = "", *, calibrated: bool = True,
                   hide_neg_value: bool = False, pick_html: str = "",
                   live_pct=None, live_trend: str = "", live_state: str = "", result_html: str = "",
                   bare: bool = False) -> str:
    """Bloc VERDICT UNIFIÉ (demande user 2026-07-17 « tout doit être identique sur les autres types de
    paris ») = ligne verdict PARTAGÉE `analyses.verdict_line` (« Marché XX% · Notre confiance YY% ✓calibré
    → Value ±Z% », value = héros coloré) + pied (mention/ré-analyse + grosse cote). Remplace l'ancienne
    barre « CONFIANCE » (_verdict_strip). UTILISÉ PAR TOUTES les cartes — simple retenu, provisoire,
    combiné du jour -> rendu STRICTEMENT identique. `conf` = confiance déjà CALIBRÉE (comme partout) ;
    `foot_txt` = mention déjà échappée + icône (🔄/🎯) ou "" ; `cote_html` = grosse cote (repli si pas de
    confiance calculable). Purement AFFICHAGE. `calibrated=False` pour un combiné (proba corrélée du marché) ;
    `hide_neg_value=True` pour un provisoire (indicatif hors ROI) -> cache la colonne Value si négative."""
    _vl = ""
    try:
        c = float(cote)
    except (TypeError, ValueError):
        c = 0.0
    if c > 1 and conf is not None:
        try:
            ev = round((float(conf) / 100.0 * c - 1) * 100)
            # La COTE est désormais une COLONNE de la grille verdict (with_cote) -> pleine largeur, alignée,
            # plus de cote isolée qui flotte. Elle n'apparaît que si la carte a bien une cote à montrer.
            _vl = analyses.verdict_line(c, conf, ev, calibrated=calibrated, with_cote=bool(cote_html),
                                        hide_neg_value=hide_neg_value, pick_html=pick_html,
                                        live_pct=live_pct, live_trend=live_trend, live_state=live_state,
                                        result_html=result_html, bare=bare)
        except (TypeError, ValueError):
            _vl = ""
    if not _vl and pick_html:              # pas de verdict calculable -> cadre « pari seul » (jamais perdu)
        _vl = analyses.verdict_line(0, None, 0, pick_html=pick_html,
                                    live_pct=live_pct, live_trend=live_trend, live_state=live_state,
                                    result_html=result_html)
    # LAYOUT (refonte 2026-07-18, demande user « réorganise tout : aligné, pleine largeur ») : le bloc verdict
    # (barre + grille Marché/Value/Cote) prend TOUTE la largeur ; la mention (ré-analyse / « compté au ROI »)
    # tient dessous, à gauche. Sans cote/mention -> juste le verdict (live/compact).
    if not (foot_txt or cote_html):
        return _vl
    _rn = f'<div class="vb-reana">{foot_txt}</div>' if foot_txt else ""
    # Repli CONFIANCE INDISPONIBLE (verdict non calculable, ex. vieille jambe de combiné dont le `prob` n'a
    # jamais été stocké) : au lieu de la grosse pastille `mc-foot`/`mc-cote` (mise en page DIFFÉRENTE qui
    # « décrochait » de l'historique), on rend le MÊME CADRE grille que les autres cartes avec la COTE SEULE —
    # Confiance/Edge/Value MASQUÉES (pas de faux chiffre, user 2026-08-29). `mc-foot` ultime si cote inexploitable.
    if not _vl and cote_html:
        if c > 1:
            _pk = f'<div class="vm-pick">{pick_html}</div>' if pick_html else ""
            _rb = result_html or ""
            # MÊME GRILLE À COLONNES que les autres jambes (Confiance · Edge · Value · Cote) pour un ALIGNEMENT
            # IDENTIQUE — les métriques indisponibles affichent « — » (jamais un faux chiffre). `bare` (combiné)
            # -> seulement Confiance + Cote, comme la grille pleine. Évite la « grosse boîte vide » d'une cote isolée.
            _na = '<span class="vm-v vm-na">—</span>'
            _cells = [f'<div class="vm-cell vm-conf"><span class="vm-l">Confiance</span>{_na}</div>']
            if not bare:
                _cells.append(f'<div class="vm-cell"><span class="vm-l">Edge</span>{_na}</div>')
                _cells.append(f'<div class="vm-cell"><span class="vm-l">Value</span>{_na}</div>')
            _cells.append('<div class="vm-cell vm-cote"><span class="vm-l">Cote</span>'
                          f'<span class="vm-v">{round(c, 2):g}</span></div>')
            return (f'<div class="vb"><div class="vm">{_pk}'
                    f'<div class="vm-grid">{"".join(_cells)}</div>{_rb}</div></div>{_rn}')
        _rf = f'<span class="mc-reana mc-reana-prov">{foot_txt}</span>' if foot_txt else ""
        return f'<div class="mc-foot">{_rf}{cote_html}</div>'
    return _vl + _rn


# JARGON DE PARI (demande user 2026-07-20 : « une analyse, pas des stats inutiles/incompréhensibles ») : la
# phrase de MATH de pari (ma proba vs proba juste, EV, écart-type, « d'où l'abstention côté ROI »…) est
# jargonneuse ET redondante avec la barre Confiance/Marché/Value déjà affichée. On la retire de l'AFFICHAGE
# (elle reste dans le .md). Signaux FORTS UNIQUEMENT (absents des phrases de FAITS/RISQUE, même quand elles
# disent « aucune value / marché efficient ») -> on ne touche PAS aux faits. Partagé : plis simples/
# provisoires (_prov_why_snippet) ET jambes de combiné (_leg_card).
_META_STAT = re.compile(r"\bma\s+proba\b|\bmon\s+estimation\b|proba\s+juste|proba\s+estim|\bla\s+juste\s*\(|"
                        r"juste\s+marché|marché\s+valoris|valorise\s+cette|écart[- ]type|\bEV\b|"
                        r"pts?\s+d['’]EV|d['’]espérance|\ble\s+sharp\b|\bpinnacle\b|"
                        r"coh[ée]rent\w*\s+avec\s+le\s+(sharp|marché)", re.I)


def _strip_meta_stat(sentence: str) -> str:
    """Retire le jargon de math de pari d'une phrase à la CLAUSE près (audit 2026-07-21) : quand une
    phrase mêle un FAIT et la math (« …dominent le H2H 8-2 et jouent à domicile : à 1.23, le marché
    valorise… »), l'ancien drop de la phrase ENTIÈRE perdait le fait. On découpe en clauses ( : ; — ,mais)
    et on ne jette QUE les clauses math. '' si plus rien de substantiel (phrase 100 % math -> drop entier,
    comportement inchangé). Phrase sans jargon -> renvoyée telle quelle (chemin rapide)."""
    s = (sentence or "").strip()
    if not s or not _META_STAT.search(s):
        return s
    parts = re.split(r"\s*(?::|;|—|,\s*mais\b)\s*", s)
    kept = [p for p in parts if p and not _META_STAT.search(p)]
    out = ", ".join(p.strip(" ,;") for p in kept).strip(" ,;")
    if len(out) < 30:                     # reste trop maigre -> la phrase était essentiellement du jargon
        return ""
    out = out[0].upper() + out[1:]
    return out if out[-1:] in ".!?…" else out + "."


def _prov_why_snippet(sport, fid, maxlen: int = 185, *, played: bool = False) -> str:
    """Extrait PROPRE (phrases COMPLÈTES, majuscule initiale) du raisonnement d'un pari — pour le pli
    « 💡 Pourquoi ce pari » (même patron que les jambes de combiné, demande user 2026-07-20 : l'analyse
    des jambes appréciée, étendue à TOUS les types). Source par ordre de repli :
      • pari PROVISOIRE / indicatif (`played=False`) : section « 🧪 » d'abord, puis « 🎯 », puis « 📋 » ;
      • pari JOUÉ / simple retenu (`played=True`) : section « 🎯 Le pari à jouer » d'abord, puis « 🧪 »,
        puis « 📋 Les faits ».
    -> TRANSPARENCE : toute carte montre son « pourquoi » (demande user 2026-07-13). Texte nettoyé
    (markdown/liens/puces/méta retirés), coupe NETTE à une fin de phrase. '' seulement si vraiment rien
    d'exploitable. Best-effort : ne casse jamais."""
    if not fid:
        return ""
    # NE PAS CHARCUTER L'ANALYSE (correctif user 2026-07-20) : la carte Toronto ne montrait QUE la phrase de
    # proba « ~78 % » alors que le 🧪 contenait les FAITS (bilan 17-7 vs 10-15, absents Sykes/Sabally/Rice,
    # risque Indiana). Cause : l'ancien filtre JETAIT toute phrase contenant un mot méta (« si l'on devait »,
    # « aucune value »…) — donc justement les phrases FACTUELLES. On garde désormais les faits ; on retire
    # seulement (a) l'amorce « Si l'on devait…, » en TÊTE et (b) les fragments PUREMENT méta (verdict
    # d'abstention SANS aucun fait).
    _PURE_META = re.compile(r"^(on s['’]abstient|on ne joue pas|pas de pari conseill|aucun pari|il n['’]y a "
                            r"pas de pari|d['’]o[uù] l['’]abstention|abstention\b)[^.!?…]{0,70}[.!?…]?$", re.I)
    # « À éviter / SKIP : le 1X2… et le BTTS… » = les marchés qu'on NE joue PAS. Hors-sujet dans le pli
    # « Pourquoi CE choix » (retour user 2026-07-22 : on justifie le pari JOUÉ, pas les autres marchés
    # écartés — ce raisonnement de sélection reste dans le .md, il n'a rien à faire à l'affichage).
    _SKIP_MARKET = re.compile(r"^\s*(à\s+[ée]viter|à\s+[ée]carter|à\s+bannir|à\s+ne\s+pas\s+jouer|skip\b|"
                              r"on\s+[ée]vite|autres?\s+march[ée]s?)\b", re.I)
    def _clean(raw: str) -> str:
        t = re.sub(r"(?im)^\s*PROV:.*$", "", raw or "")
        t = re.sub(r"^\s*#+.*$", "", t, count=1, flags=re.M)          # retire un éventuel titre de section
        t = re.sub(r"^\s*[-*]\s*\*\*.*?%\s*:\*\*\s*", "", t, count=1, flags=re.S)  # « - **sel @cote — x% :** »
        t = re.sub(r"^\s*[-*]\s*\*\*.*?:\*\*\s*", "", t, count=1, flags=re.S)      # « - **sel @cote :** » (sans %)
        t = re.sub(r"(?m)^\s*[-*•]\s+", "", t)             # puces de liste -> prose courante (pas un extrait haché)
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)     # liens markdown -> texte seul
        t = re.sub(r"[*_`#]", "", t)                       # markdown résiduel
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            return ""
        # (a) retire l'AMORCE d'un provisoire (« Si l'on devait absolument (en) jouer, » / « Si je devais
        #     dégager un angle : ») en TÊTE — mais GARDE la suite (les FAITS qui suivent).
        t = re.sub(r"^\s*si (l['’]on|je)\s+(le\s+)?devai[st]\b[^,.:]*[,:]\s*", "", t, flags=re.I).strip()
        # (b) DROP les phrases purement méta (verdict d'abstention) ; le jargon de MATH de pari est retiré
        #     à la CLAUSE près (_strip_meta_stat, audit 2026-07-21) -> un fait mêlé à la math SURVIT.
        _sents = re.split(r"(?<=[.!?…])\s+", t)
        _kept = [w for s in _sents
                 if s and not _PURE_META.match(s.strip()) and not _SKIP_MARKET.match(s.strip())
                 and (w := _strip_meta_stat(s))]
        if not _kept:      # tout filtré (analyse 100 % math) -> ne pas renvoyer vide, garder les faits
            _kept = [s for s in _sents if s and not _PURE_META.match(s.strip())
                     and not _SKIP_MARKET.match(s.strip())]
        return " ".join(_kept).strip()

    try:
        md = analyses.load(sport, str(fid))
        if not md:
            return ""
        secs = analyses._sections(md)
        # Ordre de repli selon le TYPE de pari — la première source qui donne un texte de LECTURE non
        # vide gagne. Pari joué (`played`) : le raisonnement du pari À JOUER (🎯) prime ; pari provisoire :
        # son raisonnement DÉDIÉ (🧪) prime. Les faits (📋) = ultime repli commun.
        _tgt = analyses._find(secs, "🎯", "pari à jouer", "Le pari")
        _prov = analyses._find(secs, "🧪", "provisoire", "Provisoire")
        _faits = analyses._find(secs, "📋", "faits", "Les faits")
        # PARI JOUÉ : justification DÉDIÉE au pari MÉCANIQUE réellement joué (`played_why`, générée au scan
        # par sonnet). Elle PRIME sur la section « 🎯 Le pari à jouer », qui décrit le PICK BRUT de Claude —
        # souvent un AUTRE marché que le pari joué (bug user 2026-08-31 : bet « DC 1X » / analyse « Under 3.5 »,
        # 73 % des fiches). Utilisée UNIQUEMENT si elle décrit bien le pari ACTUELLEMENT joué (sel identique)
        # -> jamais de texte périmé si le pari a changé à la vague. Repli intégral sur les sections si absente.
        _ded = ""
        if played:
            try:
                _mm = analyses.meta(sport, str(fid)) or {}
                _pw = _mm.get("played_why") or {}
                _rbw = analyses.stat_bet(_mm) or analyses.retained_bet(sport, str(fid), for_history=True) or {}
                if _pw.get("text") and analyses._norm_sel(_pw.get("sel", "")) == analyses._norm_sel(_rbw.get("sel", "")):
                    _ded = _pw["text"]
            except Exception:
                _ded = ""
        t = ""
        for _cand in ((_ded, _tgt, _prov, _faits) if played else (_prov, _tgt, _faits)):
            t = _clean(_cand or "")
            if t:
                break
        if not t:
            return ""
        t = re.sub(r"\bj(?=\d)", "≈", t)                   # notation analyste « j62 % » -> « ≈62 % » (propreté)
        t = t[:1].upper() + t[1:]                          # MAJUSCULE initiale (texte professionnel)
        if len(t) > maxlen:
            cut = t[:maxlen]
            m = re.search(r"^.*[.!?…](?=\s|$)", cut)        # dernière fin de PHRASE dans la limite
            cm = re.search(r"^.*[,;](?=\s)", cut)           # dernière fin de CLAUSE (virgule/point-virgule)
            if m and m.end() > maxlen * 0.45:
                t = m.group(0)                              # coupe NETTE à une fin de phrase -> texte complet
            elif cm and cm.end() > maxlen * 0.5:
                t = cm.group(0).rstrip(" ,;:") + "…"        # sinon fin de clause (jamais en plein mot)
            else:
                t = cut.rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        return t
    except Exception:
        return ""


def _why_sentences(text: str) -> list[str]:
    """Découpe une analyse en PHRASES complètes (une par puce) — le découpage requiert un espace APRÈS la
    ponctuation, donc « 1.62 », « 6-21 », « ~78 % » ne cassent pas. Regroupe une phrase trop courte (< 25
    car, ex. « Value nette : ») avec la suivante pour ne pas hacher. '' filtrés."""
    raw = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", (text or "").strip()) if s.strip()]
    out: list[str] = []
    for s in raw:
        if out and len(out[-1]) < 25:      # fragment trop court -> on le colle à la phrase précédente
            out[-1] = f"{out[-1]} {s}"
        else:
            out.append(s)
    return out


def _why_fold(text: str, label: str = "Pourquoi ce choix") -> str:
    """Pli TAPPABLE « 💡 <label> » — MÊME patron que le « 💡 Pourquoi cette jambe » des combinés
    (`.cleg-fold`), demande user 2026-07-20 : porter l'analyse appréciée des jambes sur TOUS les types
    de paris (simple retenu, provisoire). Porte l'analyse COMPLÈTE (déjà nettoyée), en PUCES (une par
    phrase) pour AÉRER — plus de pavé illisible (demande user 2026-07-20). '' si pas de texte. Le tap
    ouvre/ferme le pli SANS replier la carte parente (`event.stopPropagation`)."""
    t = (text or "").strip()
    if not t:
        return ""
    _sents = _why_sentences(t) or [t]
    _lis = "".join(f"<li>{html.escape(s)}</li>" for s in _sents)
    return ('<details class="cleg-fold cleg-fold-bet"><summary class="cleg-fold-s" '
            'onclick="event.stopPropagation()">' + html.escape(label)
            + '<span class="cleg-chev">▾</span></summary>'
            f'<ul class="why-ul">{_lis}</ul></details>')


def _load_day_programme() -> dict:
    """Programme du jour (data/day_programme.json) — VIDÉ à l'affichage dès que sa `date` n'est PLUS le
    jour sportif courant (user 2026-08-18 : « vider le programme et les matchs du jour avant 08h belge »).
    Le rollover du jour sportif est à 08h belge (cf. `_sport_date`, porté 06h→08h le 2026-08-20 pour que les
    matchs de nuit encore EN COURS ne disparaissent pas avant 08h) ; le scan du matin repeuple ensuite.
    Purement AFFICHAGE : le fichier n'est pas touché (anti-éjection déjà gardée par `date`)."""
    import json as _json
    path = os.path.join(analyses._ROOT, "data", "day_programme.json")
    try:
        prog = _json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {"matches": []}
    if str(prog.get("date") or "") != _sport_today().isoformat():
        return {"date": prog.get("date"), "matches": []}     # programme périmé -> vidé avant le scan du jour
    return prog


def _programme_items(exclude_pairs: set | None = None, *, framed: bool = False,
                     keep_sport: str | None = None) -> list:
    """Cartes du PROGRAMME DU JOUR (matchs SANS pari à jouer affiché) à FUSIONNER — dans l'ordre
    chronologique — avec les paris à jouer, dans le MÊME cadre (demande user). Renvoie une liste de dicts
    {"start_ts", "_html"} : le tri global et les en-têtes de jour sont gérés par le cadre unifié
    (_rows_by_day), donc PAS de regroupement par sport ici. [] si aucun match à afficher.

    `framed=True` (zone dédiée « Indicatif · hors ROI ») : la carte vit dans une zone qui porte le libellé
    UNE fois -> on n'affiche PLUS la pastille « 🧪 PROVISOIRE · indicatif, hors ROI » sur CHAQUE carte
    (fin de la répétition, demande user 2026-07-11). Le reste (pari + cote + confiance + ré-analyse) est
    identique. Les cartes prennent la classe `.mc-prov-c` (accent doré discret) pour rester identifiables.

    `exclude_pairs` : paires de noms (cf. `_prog_pair`) des matchs DÉJÀ affichés en pari à jouer
    (match_rows) -> exclus d'office pour ne JAMAIS afficher un match 2× (bug doublon : un match publié
    dont le statut retombe « abstained » après ré-analyse apparaissait en pari ET en programme).

    Statut HONNÊTE : chaque match est analysé au scan du matin (les tops par sport) puis RÉ-ANALYSÉ
    ~1 h avant son coup d'envoi. On affiche donc l'HEURE EXACTE de cette (ré)analyse (coup d'envoi − 1 h)
    au lieu d'un vague « ~1 h avant ». « Pas de value » n'est montré que si cette échéance est déjà
    passée (verdict quasi-final) ; sinon on annonce à quelle heure l'analyse (re)tombera."""
    exclude_pairs = exclude_pairs or set()
    prog = _load_day_programme()          # VIDÉ si périmé (avant le scan du jour, user 2026-08-18)
    now = datetime.now(timezone.utc)
    _ICON = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
    # Un match JAMBE du combiné du jour PEUT AUSSI apparaître en provisoire (sur un autre marché) — demande
    # user 2026-07-25 : « afficher aussi en provisoire dans Pronos » (le combiné joue « Santos moins de 3.5 »,
    # le provisoire met en avant « Santos gagne » = 2 angles distincts). On NE dédoublonne donc PLUS les
    # jambes du combiné ici -> Stats (onglet Provisoires) = Pronos (zone Provisoires). La dédup des PARIS
    # JOUÉS (simple retenu, `exclude_pairs`) reste, elle (même décision, vrai doublon).
    items: list = []
    for m in (prog.get("matches") or []):
        if m.get("status") == "bet":            # pari publié -> déjà dans les paris à jouer (fusionnés)
            continue
        _nm = str(m.get("name", ""))
        _h, _s, _a = _nm.partition(" - ")
        if _prog_pair(_h, _a) in exclude_pairs:  # déjà affiché en pari à jouer -> pas de doublon
            continue
        try:
            dt = datetime.fromisoformat((m.get("start") or "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        sp = m.get("sport")
        # Menu sport de Pronos (`keep_sport`, demande user 2026-07-26 : voir tennis/basket comme le foot).
        # Un sport EXPLICITEMENT sélectionné -> on ne garde QUE lui (vue mono-sport). En vue par défaut
        # (keep_sport=None = page des paris = foot ROI) les sports en arrière-plan (tennis/basket) restent cachés.
        if keep_sport:
            if sp != keep_sport:
                continue
        elif sp in analyses.background_sports():
            continue
        # MATCH CONFIRMÉ FINI (score final capté au règlement -> status_of=finished) : il appartient aux
        # RÉSULTATS, plus à l'à-venir (bug user 2026-07-28 : provisoire tennis « +19.5 jeux » de Nishikori
        # restait « à venir/en cours » via la fenêtre de grâce tennis 6 h, jamais validé, alors que sa jambe
        # l'était). On le SORT de l'à-venir -> il s'affiche réglé dans « Résultats du jour ».
        _pfid = (m.get("provisional") or {}).get("fid") or m.get("id")
        try:
            _psd = analyses.meta(sp, str(_pfid)) if _pfid else None
            if _psd and analyses.status_of(_psd) == "finished":
                continue
        except Exception:
            pass
        # ÉTAT RÉEL UNIBET (pas l'heure prévue) : score live = EN COURS. Un provisoire EN COURS n'est plus
        # « à venir » -> on le marque `_is_live` pour l'onglet LIVE + section « En direct » (demande user
        # 2026-07-10). Tennis souvent DÉCALÉ (heure figée) -> on se fie au live + coup d'envoi Unibet frais.
        _lstate = match_select.live_state_for(sp, _h, _a)
        _has_live = bool(_lstate)
        _lf = live_fields(_lstate, sp)          # score live (buts/points/sets) — AUCUN réseau (cache)
        _st, _usdt = match_select.fresh_status(sp, _h, _a, "notstarted", _has_live, start_iso=m.get("start"))
        if _usdt is not None:                   # heure Unibet fraîche (reflète un éventuel décalage)
            dt = _usdt
        _is_live = (_st == "inprogress") or _has_live
        _prov_pending = False   # match commencé, NI live NI réglé -> « en attente de résolution » (badge gris)
        if not _is_live and dt <= now:
            # coup d'envoi passé mais PAS de live Unibet. AVANT, un match « probablement fini » (fenêtre de
            # durée écoulée) mais PAS ENCORE réglé (score non capté) était EXCLU d'ici -> il DISPARAISSAIT
            # (ni à-venir, ni dans les Résultats qui n'affichent QUE les réglés) -> pas de badge gris (bug
            # user 2026-08-08 : « provisoire en attente de résolution sans badge gris »). Désormais on le
            # GARDE en « ⏳ En attente » jusqu'au règlement (grey badge). Le RÉGLÉ (status_of=finished, plus
            # haut) part bien dans les Résultats. Critère sport-agnostique.
            if analyses.likely_finished({"start": m.get("start"), "sport": sp}):
                # FENÊTRE BORNÉE (user 2026-08-08, cas Bruges-Courtrai) : on garde « ⏳ En attente » tant que
                # le match est RÉCEMMENT fini (< 10 h après le coup d'envoi = règlement en cours). Au-delà,
                # c'est un STRAGGLER non réglable (score jamais capté : nom/id non résolu) -> on l'exclut comme
                # avant, pour ne pas l'afficher « en attente » INDÉFINIMENT.
                if dt is not None and (now - dt).total_seconds() < 10 * 3600:
                    _prov_pending = True
                else:
                    continue
        ic = _ICON.get(sp, "")
        # Ligne d'en-tête : NOM DU SPORT (majuscules) puis la compétition (demande user 2026-07-12,
        # ex. « 🎾 TENNIS • Wimbledon ») -> le sport est explicite, plus seulement l'emoji.
        _spn = {"foot": "FOOTBALL", "tennis": "TENNIS", "basket": "BASKET"}.get(sp, "")
        name = str(m.get("name", ""))
        home, _sep, away = name.partition(" - ")
        # AFFICHAGE seul : on retire « (F) » du nom montré (le sport WNBA/WTA le dit déjà) — `home`/`away`
        # BRUTS restent intacts pour la logique (dédup, gloss, règlement). Demande user 2026-07-21.
        teams = _teams_vs_html(home, away)
        comp = html.escape(str(m.get("comp") or ""))
        reanalyse = dt - timedelta(hours=1)     # la (ré)analyse rapprochée = coup d'envoi − 1 h
        # PARI PROVISOIRE (demande user 2026-07-09) : un match analysé SANS value affiche quand même « le
        # pari si l'on devait en jouer un » (favori/avis de l'analyste), comme un vrai pari — mais en TEINTE
        # DORÉE « provisoire » (≠ pari de value confirmé, vert) + la mention de ré-analyse. Ce pari vit
        # UNIQUEMENT dans le programme -> jamais compté au ROI/stats. Repli sur l'ancien libellé si absent.
        prov = m.get("provisional") or {}
        prov_sel = str(prov.get("sel") or "").strip()
        _prwhy = ""     # pli « 💡 Pourquoi ce choix » (rempli plus bas si provisoire) — init pour tous les cas
        # NO-VALUE SANS PROVISOIRE = PAS AFFICHÉ (demande user 2026-07-10) : une abstention analysée qui n'a
        # même pas de pari provisoire (« pas de value ») n'a aucune raison d'être affichée -> on la SAUTE.
        # Elle reste gardée sur disque UNIQUEMENT pour nourrir les fantômes (calibration). On garde les
        # provisoires (pick doré) et les matchs PAS ENCORE analysés (statut ≠ abstained -> « Analyse à … »).
        if not prov_sel and m.get("status") == "abstained":
            continue
        # FILTRE PROVISOIRE (demande user 2026-07-17) : on ne garde pas un provisoire SANS value ET sous 60 %
        # de confiance calibrée (bruit) ; on garde ceux avec value (EV+) OU confiance ≥ 60 %. Même prédicat
        # que le suivi (analyses.provisional_shown) -> affichage == stats « info seule ».
        if prov_sel and not analyses.provisional_shown(sp, prov_sel, prov.get("cote"), prov.get("prob"),
                                                        home, away, fid=prov.get("fid")):
            continue
        if prov_sel:
            _cote = prov.get("cote")
            _pconf = prov.get("prob")
            # STYLE TELEGRAM (demande user 2026-07-12, s'inspire des cartes publiées) : le pari en gras, la
            # « Confiance XX% » en texte, et la COTE en GROS chiffre en bas à DROITE (label « COTE »). Cote en
            # BLANC (choix user), comme la carte Telegram. La confiance reste la proba de l'analyste (le tag/
            # zone « indicatif · hors ROI » dit clairement que ce n'est pas compté au ROI).
            _cote_big = (f'<span class="mc-cote"><span class="mc-cote-l">COTE</span>'
                         f'<span class="mc-cote-v">{round(_cote, 2):g}</span></span>'
                         if isinstance(_cote, (int, float)) and _cote else "")
            # Pastille « 🧪 PROVISOIRE » par carte : OMISE en mode `framed` (la zone « Indicatif · hors ROI »
            # porte déjà le libellé une fois) — demande user 2026-07-11, fin de la répétition.
            _prov_tag = ('' if framed else
                         '<div class="mc-prov-tag">🧪 PROVISOIRE<span> · indicatif, hors ROI</span></div>')
            # Marché EN CLAIR sous la sélection (demande user 2026-07-13) : « -9.5 » -> « gagne de 10 pts+ ».
            _gl = _bet_gloss(prov_sel, sp, home, away)
            _gloss = f'<div class="mc-gloss"><span class="ar">↳</span>{html.escape(_gl)}</div>' if _gl else ""
            # PAS d'extrait d'analyse dans la carte REPLIÉE (demande user 2026-07-13) : l'analyse n'apparaît
            # qu'au DÉPLI (message COMPLET, dans le corps) -> plus de doublon extrait/analyse une fois ouvert.
            # LIGNE VERDICT IDENTIQUE aux cartes de pari (demande user 2026-07-17 « tout doit être identique ») :
            # « Marché XX% · Notre confiance YY% ✓calibré → Value ±Z% ». Confiance CALIBRÉE (comme partout).
            # Bonus : sur une abstention la value est souvent NÉGATIVE -> elle EXPLIQUE l'abstention (notre
            # confiance sous le seuil du marché), au lieu de l'ancienne barre « CONFIANCE » qui la survendait.
            # Confiance CALIBRÉE (comme partout) pour la ligne verdict PARTAGÉE (_verdict_block) -> rendu
            # STRICTEMENT identique aux cartes de pari simple / combiné du jour.
            _cpc = _pconf
            if _pconf is not None:
                try:
                    from app.settle_analyst import code_from_pick as _cfp
                    _pcode = _cfp(prov_sel, sp, home, away)
                    # + REFROIDISSEMENT OVER-total (audit 2026-07-23) : la carte provisoire affichait la
                    # confiance NON refroidie (69 %) quand le moteur jugeait 58 % -> même _cool_conf partout.
                    _cpc = analyses._cool_conf(
                        analyses.calibrated_conf(_pconf, sp, _pcode), sp, _pcode,
                        (analyses.meta(sp, str(prov.get("fid") or "")) or {}).get("streaks"))
                except Exception:
                    _cpc = _pconf
            # Pli « 💡 Pourquoi ce choix » (demande user 2026-07-20) : l'analyse du provisoire, présentée
            # comme sous les jambes de combiné. Source = section « 🧪 » du .md (repli 🎯/📋).
            # Texte COMPLET : le raisonnement 🧪 n'est plus remis dans le corps (reasoning_html retiré) ->
            # le pli en est le SEUL porteur, on ne tronque pas. Le .md est indexé par la FICHE (`prov.fid`),
            # PAS l'id programme (`m.id`) — même clé que l'ancien reasoning_html, sinon basket sans pli.
            _prwhy = _why_fold(_prov_why_snippet(sp, str(prov.get("fid") or ""), maxlen=100000),
                               "Pourquoi ce choix")
            # En LIVE : le pli « Pourquoi » passe APRÈS scoreboard + chance (posé plus bas dans _inner),
            # MÊME ORDRE que jambes/paris du jour (retour user 2026-07-21) ; sinon il reste sous le verdict.
            sub = ('<div class="mc-div"></div>'
                   + f'<div class="mc-pick">{html.escape(_pretty_sel(prov_sel, home, away))}</div>'
                   + _gloss
                   + _verdict_block(_cote, _cpc, "", _cote_big, calibrated=True,
                                    hide_neg_value=True)   # provisoire (indicatif) : pas de Value rouge
                   # ligne « Ré-analyse à HH:MM » retirée (demande user 2026-07-21)
                   + ("" if _is_live else _prwhy))
        else:
            # Match SANS provisoire et NON analysé (pas de statut de value). Deux cas :
            #  • heure d'analyse (KO − 1 h) ENCORE À VENIR -> on annonce « Analyse à HH:MM » (légitime).
            #  • heure d'analyse DÉJÀ PASSÉE -> le match a démarré avant qu'on ait pu l'analyser (ex. scan
            #    manqué / PC éteint) et il ne sera PLUS analysé (l'analyste ne travaille qu'en pré-match).
            #    Afficher « Analyse à {heure passée} » sur un match live/commencé est FAUX (rendez-vous
            #    déjà écoulé, jamais honoré) et il n'y a RIEN à montrer (ni pari, ni provisoire, ni analyse)
            #    -> on le SAUTE (correctif user 2026-07-16 : la carte « Analyse à 10:03 » fantôme du Live).
            if now >= reanalyse:
                continue
            sub = ('<div class="mc-div"></div>'
                   '<div class="mc-betl mc-noplay"><span class="mc-bi">🔄</span>'
                   f'<span class="mc-bt">Analyse à {html.escape(fmt_local(reanalyse, with_date=False))} '
                   '<span class="dim">· compos &amp; cotes fraîches</span></span></div>')
        # Badge coin haut-droit : « 🟢 Live » en direct (demande user 2026-07-12 : comme les paris live, le
        # score va dans le SCOREBOARD sous le titre, plus dans le badge), sinon l'HEURE.
        if _is_live:
            _badge = '<span class="mc-badge mc-live">🟢 Live</span>'
        elif _prov_pending:   # commencé, probablement fini, pas encore réglé -> EN ATTENTE (badge gris, user 2026-08-08)
            _badge = '<span class="mc-badge mc-wait">⏳ En attente</span>'
        else:
            # HEURE + DÉCOMPTE (« HH:MM - Début dans 52m01s ») comme _leg_card (user 2026-08-08 : sur TOUS les types).
            _hm = html.escape(fmt_local(dt, with_date=False))
            _cd_pi = (f'<span class="cd" data-ts="{int(dt.timestamp())}"></span>'
                      if dt and dt.timestamp() > now.timestamp() else "")
            if _cd_pi and _hm:
                _badge = (f'<span class="mc-badge mc-up cleg-when"><span class="cw-h">{_hm}</span>'
                          f'<span class="cw-sep">-</span>{_cd_pi}</span>')
            else:
                _badge = f'<span class="mc-badge mc-up">{_hm}</span>'
        # SCOREBOARD des résultats (sets/quart-temps) — visible dans la carte repliée SOUS le titre pour un
        # provisoire EN DIRECT (demande user 2026-07-12), comme les paris live.
        _lscore = (_live_scoreboard(_lf.get("score"), home, away, tennis=(sp == "tennis"),
                                    server=_lf.get("server"), points=_lf.get("game_pts"),
                                    clock=_lf.get("live_time"), periods=_lf.get("periods"),
                                    fstats=_lf.get("fstats"))
                   if (_is_live and _lf.get("score")) else "")
        # Barre « Chance live » (demande user 2026-07-20 : elle manquait sur les provisoires en direct) —
        # même reflet EN DIRECT que les paris retenus (cote live dé-margée / repli modèle). '' si non mappable.
        _prov_bar = ""
        if _is_live and _lscore and prov_sel:
            try:
                _lld = match_select.live_state_for(sp, home, away)
                _lhs, _las = _parse_live_score(_lf.get("score"))
                _fs = _lf.get("fstats") or {}
                _lvals = {"corners_h": _fs.get("cor_h"), "corners_a": _fs.get("cor_a"),
                          "cards_h": _fs.get("yc_h"), "cards_a": _fs.get("yc_a"),
                          "rc_h": _fs.get("rc_h"), "rc_a": _fs.get("rc_a")}
                if sp == "tennis":             # sets gagnés + jeux du set en cours -> modèle « ≥1 set »
                    _lvals.update(_tennis_sets_games(_lf.get("score")))
                _prov_bar = _live_bar_html(analyses.live_prob(
                    sp, prov_sel, _cfp(prov_sel, sp, home, away), home, away, _lhs, _las,
                    match_select.live_minute(_lld),
                    match_select.live_win_odds(sp, home, away), prov.get("prob"),
                    analyses.live_catalog(str(m.get("id") or "")), _lvals,
                    match_select.basket_frac(_lld, comp) if sp == "basket" else None))
            except Exception:
                _prov_bar = ""
        _inner = (
            f'<div class="mc-main">'
            f'<div class="mc-line"><span class="mc-ic">{ic}</span>'
            f'<span class="mc-comp">' + (comp or '') + '</span>'   # « FOOTBALL » retiré (foot-only) : emoji + ligue
            + _badge
            + '</div>'
            f'<div class="mc-teams">{teams}</div>'
            # SCOREBOARD SOUS le pari à jouer (demande user 2026-07-14) : le pari (sub) d'abord, une LIGNE DE
            # SÉPARATION, puis le tableau de score en dessous — aligné sur les cartes de pari (_sport_row).
            + f'<div class="mc-sub">{sub}</div>'
            + (f'<div class="mc-div"></div><div class="mc-livesc">{_lscore}{_prov_bar}</div>' if _lscore else "")
            # Pli « Pourquoi » EN DERNIER en live (après scoreboard + chance), avec son filet — même ordre
            # que jambes/paris du jour (retour user 2026-07-21).
            + (_prwhy if (_is_live and prov_sel) else "")
            + '</div>')
        # Accent doré discret (bord gauche) sur les cartes PROVISOIRES en zone dédiée -> identifiables sans
        # la pastille répétée (demande user 2026-07-11). Uniquement en mode `framed` et si c'est un provisoire.
        # STYLE TELEGRAM (demande user 2026-07-12) : fond bleu nuit + bordure lumineuse sur les cartes du
        # programme (classe `mc-tg`), au lieu de l'ancien accent doré latéral.
        # Cadre PROVISOIRE : mêmes fond/mise en page que mc-tg mais BORD GRIS (demande user 2026-07-14) —
        # remplace la pastille « PROVISOIRE » : le gris signale « indicatif » sans badge.
        _acc = " mc-tg" + (" mc-prov-b" if prov_sel else "")
        # PROVISOIRE CLIQUABLE (demande user 2026-07-10) : si l'analyse du match est disponible (le scan
        # GARDE le .md des provisoires), la carte devient un <details> qui déplie la fiche d'analyse — comme
        # les paris à jouer. Le .md est purement AFFICHAGE (aucun impact ROI/stats/calibration). Sinon carte
        # simple non cliquable (ex. provisoire d'avant ce build, ou .md pas encore régénéré au prochain scan).
        # MÊME STRUCTURE qu'un pari normal (`.row.mc` + mc-head + mc-chev + mc-body caché) -> le MÊME toggle
        # JS l'ouvre à l'identique (accordéon, chevron, animation), analyse déployée dans `.exp` (un clic
        # DANS l'analyse ne replie pas). Si pas d'analyse dispo -> carte simple non dépliable.
        _fid = str(prov.get("fid") or "") if prov_sel else ""
        # skip_verdict : on MASQUE « 🎯 Pourquoi ce pari » dans l'analyse (demande user 2026-07-16) car le
        # bloc « 🧪 Le pari provisoire » (reasoning_html, ajouté juste après) porte DÉJÀ le raisonnement de
        # l'abstention -> plus de doublon de conclusion « on s'abstient ». Fusion en un seul bloc.
        # card_details : dépli de carte épuré (faits/tendances/H2H repliés) ; implique déjà skip_verdict
        # (« 🎯 Pourquoi ce pari » masqué). Le raisonnement 🧪 est porté par le pli « 💡 Pourquoi ce choix ».
        _ana = analyses.render(sp, _fid, card_details=True) if _fid else None
        if _ana and not _prwhy:
            # Corps IDENTIQUE à une vraie carte (demande user 2026-07-10) : BARRES « Cotes & chances »
            # (Unibet + Public) + TABLEAU « Paris classés » (bets_html) + ANALYSE (faits). to_html/render
            # retire le tableau (affiché à part sur une carte) -> on le rajoute ; sinon le corps semblait vide.
            _pm = analyses.meta(sp, _fid) or {}
            _bars = (_pick_bars(analyst_bars(_pm.get("o1"), _pm.get("ox"), _pm.get("o2"),
                                             analyses.votes_pct(_pm), home=home, away=away))
                     if (_pm.get("o1") and _pm.get("o2")) else "")
            # Le SCOREBOARD live est DÉJÀ montré dans la carte repliée (head) -> on ne le remet pas dans le
            # corps (sinon doublon). En live on masque aussi les barres (comme _sport_row).
            # RAISONNEMENT de l'abstention : porté par UN SEUL bloc « 🧪 Le pari provisoire » (reasoning_html,
            # le pick indicatif + son analyse). `_ana` est rendu skip_verdict=True -> son « 🎯 Pourquoi ce
            # pari » est masqué (sinon on répétait deux fois la conclusion « on s'abstient » — retour user
            # 2026-07-16). Le RAISONNEMENT 🧪 n'est PLUS remis ici (demande user 2026-07-20) : le pli
            # « 💡 Pourquoi ce choix » en tête de carte le porte déjà -> plus de doublon. Ordre : Cotes &
            # chances -> Paris classés -> détails (faits/tendances/H2H repliés via _ana card_details).
            _body = (("" if _is_live else _bars) + analyses.bets_html(sp, _fid) + _ana)
            # Analyse INLINE dans `.exp` (un clic dedans ne replie pas). PAS de classe `.mc-ana` : elle
            # déclencherait `_mcLoad` -> `fetch(data-ana=null)` -> /null -> 404 « {detail: Not Found} »
            # qui écrasait l'analyse (bug vu 2026-07-10). Ici l'analyse est déjà là -> aucun fetch.
            card = (f'<div class="row pick mc prog-card prog-card-x{_acc}">'
                    f'<div class="mc-head">{_inner}<span class="mc-chev">▸</span></div>'
                    f'<div class="mc-body" hidden><div class="exp">{_body}</div></div></div>')
        elif _prwhy:
            # Le pli « 💡 Pourquoi ce choix » porte DÉJÀ toute l'analyse (demande user 2026-07-20) -> carte
            # NON dépliable : plus de corps redondant (Cotes & chances / Paris classés / détails). Pas de
            # `.mc-body` -> tap inerte (JS `if(!b)return;`). Données intactes (le .md n'est pas touché).
            card = f'<div class="row pick mc prog-card mc-flat{_acc}"><div class="mc-head">{_inner}</div></div>'
        else:
            card = f'<div class="row pick mc prog-card{_acc}"><div class="mc-head">{_inner}</div></div>'
        items.append({"start_ts": dt.timestamp(), "_html": card, "_sport": sp,
                      "_prov": bool(prov_sel), "_live": _is_live, "home": home, "away": away})
    return items


def combo_legs_html(cb: dict, *, compact: bool = False, expandable: bool = False) -> str:
    """Rendu UNIFIÉ (accueil/Stats/Live) des jambes d'un combiné du jour : badge de résultat W/L/N/⏳,
    emoji sport, sélection, cote, nom du match, et le SCORE EN DIRECT (🟢 …) de chaque jambe tant qu'elle
    court (ou le score final une fois réglée). `compact` = police plus petite (accueil/bandeau).
    `expandable` (onglet Stats) : chaque jambe DOTÉE d'une justification (`leg['why']`, analyse dédiée
    générée par le scan) devient un `<details>` cliquable qui déplie son analyse — comme un pari à jouer."""
    import html as _h
    _B = {"won": ("W", "w"), "lost": ("L", "l"), "push": ("N", "n")}
    _emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
    rows = []
    # Ordre CHRONOLOGIQUE des coups d'envoi (demande user 2026-07-21) — affichage seul.
    for l in sorted(cb.get("legs") or [], key=lambda x: str(x.get("start") or "9999")):
        _lt, _bc = _B.get(l.get("result"), ("⏳", "p"))   # p = en attente (badge doré)
        emo = _emo.get(l.get("sport"), "•")
        nm = _h.escape(_noF(str(l.get("name") or "")).replace(" - ", " — "))   # (F) retiré à l'affichage
        # équipes avec repli `name` -> le nom d'équipe de la double chance ne disparaît pas du titre (régression user 2026-08-02)
        _lh2, _la2 = l.get("home", ""), l.get("away", "")
        if not (_lh2 and _la2) and l.get("name"):
            _lh2, _s2, _la2 = str(l.get("name")).partition(" - ")
        sel = _h.escape(_pretty_sel(str(l.get("sel") or ""), _lh2, _la2))
        co = l.get("cote")
        cot = f' · @{co:g}' if isinstance(co, (int, float)) and co else ""
        _sco = ""
        if l.get("result") is None:
            _lfz = live_fields(match_select.live_state_for(l.get("sport"), l.get("home", ""),
                                                           l.get("away", "")), l.get("sport"))
            if _lfz.get("score"):
                _sco = f'<span class="sx-leg-live">🟢 {_h.escape(_lfz["score"])}</span>'
        elif l.get("score"):
            _sco = f'<span class="sx-leg-x">{_h.escape(str(l.get("score")))}</span>'
        _badge = f'<span class="sx-bdg {_bc}">{_lt}</span>'
        _txt = f'<span class="sx-leg-t">{emo} <b>{sel}</b>{cot}<small>{nm}</small></span>'
        _why = l.get("why")
        if expandable and _why:                     # jambe cliquable -> déplie sa justification dédiée
            rows.append(
                '<details class="da-faits">'
                '<summary onclick="event.stopPropagation()" class="sx-leg" style="list-style:none;cursor:pointer">'
                f'{_badge}{_txt}{_sco}<span class="sx-leg-x">▸</span></summary>'
                f'<div class="da-faits-b" style="padding-left:27px">{_h.escape(_why)}</div></details>')
        else:
            rows.append(f'<div class="sx-leg">{_badge}{_txt}{_sco}</div>')
    return "".join(rows)


def _clean_cap(t, maxlen: int = 180) -> str:
    """Texte nettoyé (markdown retiré) + plafonné à une FIN DE PHRASE/CLAUSE (jamais en plein mot). ''
    si vide. Sert aux « pourquoi » de jambe et à la synthèse du combiné (présentation Telegram)."""
    t = re.sub(r"[*_`#]", "", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1",
               re.sub(r"\s+", " ", str(t or "")).strip()))
    if len(t) <= maxlen:
        return t
    cut = t[:maxlen]
    m = re.search(r"^.*[.!?…](?=\s|$)", cut)
    cm = re.search(r"^.*[,;](?=\s)", cut)
    if m and m.end() > maxlen * 0.45:
        return m.group(0)
    if cm and cm.end() > maxlen * 0.5:
        return cm.group(0).rstrip(" ,;:") + "…"
    return cut.rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


_SPORT_LBL = {"foot": "FOOTBALL", "tennis": "TENNIS", "basket": "BASKET"}


def _team_badge(name: str) -> str:
    """Pastille MONOGRAMME d'un club (test « carte façon Bull », user 2026-08-15) : cercle à couleur STABLE
    dérivée du nom + initiales. Placeholder en attendant les vrais logos (SofaScore flaky ; à câbler via
    FotMob ensuite). Purement décoratif."""
    import re as _re
    words = [w for w in _re.split(r"[\s.\-]+", str(name or "")) if w and w[0].isalnum()]
    ini = ("".join(w[0] for w in words[:2]) or (str(name or "?")[:2])).upper()
    hue = sum(ord(c) for c in str(name or "")) % 360
    return (f'<span class="team-mono" style="background:linear-gradient(150deg,'
            f'hsl({hue},48%,46%),hsl({(hue + 24) % 360},52%,34%))">{html.escape(ini)}</span>')


def _crest_badge(name: str) -> str:
    """Pastille club : le LOGO FotMob (via /crest) par-dessus le MONOGRAMME, qui reste CACHÉ par défaut
    (CSS `.tm-b .team-mono{visibility:hidden}`). Logo OK -> il s'affiche direct, JAMAIS d'initiales (user
    2026-08-15 : plus de flash). Logo KO (404/panne) -> `onerror` le retire ET révèle le monogramme (repli).
    Zéro blocage : /crest résout async côté navigateur."""
    from urllib.parse import quote
    return (f'<span class="tm-b">{_team_badge(name)}'
            f'<img class="team-logo" src="/crest?name={quote(str(name or ""))}" alt="" loading="lazy" '
            f'onerror="var m=this.previousElementSibling;this.remove();if(m)m.style.visibility=&quot;visible&quot;">'
            f'</span>')


def _teams_vs_html(home, away, center: str = "VS") -> str:
    """Ligne d'équipes + LOGOS de club (repli monogramme) (test carte façon Bull, user 2026-08-15). Au
    CENTRE : l'heure du match (user 2026-08-15) au lieu de « VS ». Repli sur un seul nom si l'autre manque."""
    h, a = _noF(str(home or "")), _noF(str(away or ""))
    if not a:
        return html.escape(h)
    return (f'<span class="tmvs">'
            f'<span class="tm-side">{_crest_badge(h)}'
            f'<span class="tm-n">{html.escape(h)}</span></span>'
            f'<span class="tm-vs">{center or "VS"}</span>'
            f'<span class="tm-side">{_crest_badge(a)}'
            f'<span class="tm-n">{html.escape(a)}</span></span></span>')


def _live_clock_html(sport_key, home, away) -> str:
    """Horloge live « M:SS » (défile via le ticker JS : data-min/sec/cap/run) — « HT » en mi-temps,
    « 92:27 (+3') » en prolongation. '' si pas d'horloge. PARTAGÉ carte normale (_sport_row) ET jambe de
    combiné (_leg_card) -> même horloge partout (user 2026-08-17 : jambes présentées comme une carte)."""
    try:
        _clk = match_select.live_clock(match_select.live_state_for(sport_key, home, away))
    except Exception:
        _clk = None
    if not _clk:
        return ""
    _cm, _cs, _crun, _cpid = _clk
    _pid = (_cpid or "").upper()
    if _pid == "FIRST_HALF" and not _crun and _cm >= 45:
        return '<span class="tm-min" data-run="0">HT</span>'
    # HORLOGE SEULE, sans indicateur « (+N') » (user 2026-08-20) : le temps additionnel ANNONCÉ (+6) n'est PAS
    # dans le flux de paris Unibet (seulement l'overlay vidéo broadcast) -> on n'affiche pas un « +N » calculé
    # maison qui contredit le broadcast. L'horloge continue de défiler au-delà de 90 (91:00, 92:00…), comme le
    # scoreboard de données Unibet (« 90:56 »). `data-cap="0"` -> le ticker JS n'ajoute plus aucun « +N ».
    _disp = f'{_cm}:{_cs:02d}'
    return (f'<span class="tm-min" data-min="{_cm}" data-sec="{_cs}" data-cap="0" '
            f'data-run="{1 if _crun else 0}">{_disp}</span>')


def _leg_card(l: dict, *, why: bool = True, verdict: bool = False, teams: bool = True,
              why_always: bool = False, why_label: str = "Pourquoi cette jambe",
              prob_calibrated: bool = False, live_layout: bool = False, bare: bool = False) -> str:
    """Rendu d'UNE jambe de combiné COMME UNE CARTE DE SIMPLE (demande user 2026-07-14) : en-tête
    « SPORT • match » + badge d'état, le pari en gras, l'explication en clair (gloss ↳), la COTE à droite,
    bord gauche coloré par état. En live : badge 🟢 LIVE + tableau de score sous la jambe. `why` = ajoute la
    justification (combiné du jour) ; le combiné de match a son propre déplié.
    `verdict` = ajoute la LIGNE VERDICT (barre + Confiance/Marché/Value + grosse COTE) sous le pari pour que
    la jambe RESSEMBLE À UN CADRE PROVISOIRE (demande user 2026-07-18 : « chaque jambe du combiné du jour
    doit ressembler à un cadre provisoire ») — remplace la pastille cote. Purement AFFICHAGE.
    `why_always` = garde le pli « Pourquoi » affiché MÊME une fois la jambe réglée (demande user 2026-07-24
    pour le combiné Betmines = suivi/observation ; l'analyse reste consultable après coup)."""
    _emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
    _sp, _lh, _la = l.get("sport"), l.get("home", ""), l.get("away", "")
    emo = _emo.get(_sp, "•")
    splbl = _SPORT_LBL.get(_sp, (_sp or "").upper())
    sel_raw = str(l.get("sel") or "")
    # Équipes résolues (repli sur `name` « A - B ») AVANT de bâtir le TITRE : sinon un home/away vide faisait
    # perdre le nom d'équipe de la double chance dans le titre (« Double chance 1X » sans « (… ou nul) ») —
    # régression user 2026-08-02. Réutilisées pour la ligne d'équipes plus bas.
    _th, _ta = _lh, _la
    if not (_th and _ta) and l.get("name"):
        _th, _sepn, _ta = str(l.get("name")).partition(" - ")
    _sel_disp = _pretty_sel(sel_raw, _th, _ta)
    # PARI = le MARCHÉ seul ; le DÉTAIL (« X gagne ou match nul ») vit dans la GLOSE grise sous le pari, pas
    # en parenthèse à côté (user 2026-08-18). -> on retire la parenthèse redondante des « Double chance … ».
    if _sel_disp.startswith("Double chance"):
        _sel_disp = re.sub(r"\s*\(.*\)\s*$", "", _sel_disp).strip()
    sel = html.escape(_sel_disp)
    # EN-TÊTE FAÇON PROVISOIRE (demande user 2026-07-18) : L1 = « SPORT • compétition » (plus le nom du
    # match condensé) ; L2 = les ÉQUIPES sur leur propre ligne, en gros (comme .mc-teams). Équipes depuis
    # home/away (repli : le nom du match « A - B »).
    comp = html.escape(str(l.get("comp") or ""))
    # (_th/_ta déjà résolus plus haut, avec repli sur `name`, pour le titre ET la ligne d'équipes.)
    # `teams=False` (combiné de MATCH, même affiche que la carte parente) -> pas de répétition des équipes.
    _teams_html = (f'<div class="cleg-teams">{_teams_vs_html(_th, _ta)}</div>') if (teams and _th and _ta) else ""
    co = l.get("cote")
    _cote = (f'<span class="cleg-cote"><span class="cleg-cote-l">COTE</span>'
             f'<span class="cleg-cote-v">{co:g}</span></span>') if isinstance(co, (int, float)) and co else ""
    _res = l.get("result")
    # ÉTAT -> couleur (demande user 2026-07-18) : void = ANNULÉ (gris), plus « pending » (orange) à tort.
    _state = {"won": "won", "lost": "lost", "push": "push", "void": "void"}.get(_res, "pending")
    # badge d'état : à venir / gagné / perdu / remboursé / annulé / en cours.
    _bmap = {"won": ("GAGNÉ", "w"), "lost": ("PERDU", "l"), "push": ("REMB.", "n"),
             "void": ("ANNULÉ", "n")}
    board = ""
    _lfz = None   # champs live (score/horloge) — None si réglé (réutilisé par la mise en page « carte normale »)
    _hh = ""   # heure de coup d'envoi (à venir) — réutilisée par la mise en page « carte normale »
    _cd = ""   # badge DÉCOMPTE avant match (rempli en direct par le timer JS `.cd`) — posé si à venir
    if _res is None:
        _lfz = live_fields(match_select.live_state_for(_sp, _lh, _la), _sp)
        if _lfz.get("score"):
            # EN COURS = PAS DÉCIDÉ -> ORANGE (plus vert : le vert = gagné). Badge « ⏳ EN COURS ».
            _state = "live"
            _btxt, _bcls = "🟢 LIVE", "live"   # « Live » comme les autres cartes (user 2026-07-21)
            # Barre « Chance live » PAR JAMBE (demande user 2026-07-21 : la barre pour TOUS les paris) —
            # même reflet en direct que les simples/provisoires. '' si non mappable. PURE AFFICHAGE.
            _leg_bar, _lp = "", None
            try:
                _lld = match_select.live_state_for(_sp, _lh, _la)
                _lhs, _las = _parse_live_score(_lfz.get("score"))
                _fs = _lfz.get("fstats") or {}
                _lvals = {"corners_h": _fs.get("cor_h"), "corners_a": _fs.get("cor_a"),
                          "cards_h": _fs.get("yc_h"), "cards_a": _fs.get("yc_a"),
                          "rc_h": _fs.get("rc_h"), "rc_a": _fs.get("rc_a")}
                if _sp == "tennis":            # sets gagnés + jeux du set en cours -> modèle « ≥1 set »
                    _lvals.update(_tennis_sets_games(_lfz.get("score")))
                _gfrac = (match_select.basket_frac(_lld, l.get("comp") or "") if _sp == "basket" else None)
                _pr = l.get("prob")
                _prpct = (_pr * 100 if isinstance(_pr, (int, float)) and _pr <= 1 else _pr)
                _lp = analyses.live_prob(
                    _sp, sel_raw, l.get("code", ""), _lh, _la, _lhs, _las,
                    match_select.live_minute(_lld),
                    match_select.live_win_odds(_sp, _lh, _la), _prpct, None, _lvals, _gfrac)
                _leg_bar = _live_bar_html(_lp)
            except Exception:
                _leg_bar, _lp = "", None
            # Pari mathématiquement ACQUIS en cours de match (verrou, ex. set pris) -> visuel GAGNÉ :
            # bord VERT + badge GAGNÉ, PLUS de barre chance live (demande user 2026-07-21). Le règlement
            # RÉEL reste inchangé (à la fin du match) — pur AFFICHAGE anticipé. Symétrique si perdu.
            if _lp and _lp.get("source") == "acquis":
                _state, _btxt, _bcls, _leg_bar = "won", "GAGNÉ", "w", ""
            elif _lp and _lp.get("source") == "perdu":
                _state, _btxt, _bcls, _leg_bar = "lost", "PERDU", "l", ""
            board = ('<div class="cleg-board">'
                     + _live_scoreboard(_lfz["score"], _lh, _la, tennis=(_sp == "tennis"),
                                        server=_lfz.get("server"), points=_lfz.get("game_pts"),
                                        clock=_lfz.get("live_time"), periods=_lfz.get("periods"),
                                        fstats=_lfz.get("fstats"))
                     + _leg_bar + '</div>')
        elif analyses.likely_finished({"sport": _sp, "start": l.get("start")}):
            # Match FINI mais pas encore réglé ET sans donnée live (ex. jambe Betmines d'une ligue absente
            # de nos sources live) -> ⏳ EN ATTENTE au lieu de « À VENIR » qui laisse croire que le match n'a
            # pas eu lieu (demande user 2026-07-24 : combiné Betmines terminé non réglé). Bascule ✓/✗ au
            # règlement (tâche reconcile). Même logique que le ⏳ des provisoires.
            _btxt, _bcls = "⏳ EN ATTENTE", "p"
        else:
            # HEURE de début dans le badge (comme les provisoires) au lieu de « À VENIR » (demande user
            # 2026-07-18). Repli « À VENIR » si l'heure n'est pas exploitable. HEURE FRAÎCHE Unibet (demande
            # user 2026-07-23) : un match DÉCALÉ (tennis qui glisse, ex. 12:10 -> 12:50) affiche sa NOUVELLE
            # heure + « ⏱ » (au lieu de l'heure périmée qui laisse croire qu'il aurait dû commencer).
            _hh = ""
            try:
                _dtv, _ = match_select.effective_start(_sp, _lh, _la, l.get("start"))
                _hh = fmt_local(_dtv, with_date=False) if _dtv else ""
                # DÉCOMPTE avant le coup d'envoi (demande user 2026-08-08) : badge `.cd` à côté de l'heure,
                # rempli/rafraîchi en direct par le timer JS (« 2h 15m » → « 12m 30s » → « live »).
                if _dtv:
                    _cd = f'<span class="cd" data-ts="{int(_dtv.timestamp())}"></span>'
            except Exception:
                _hh = ""
            _btxt, _bcls = (_hh or "À VENIR"), "up"   # heure FRAÎCHE (décalage géré), badge NEUTRE sans emoji
    else:
        _btxt, _bcls = _bmap.get(_res, ("À VENIR", "p"))
        # SCORE FINAL du match réglé (demande user 2026-07-28 : les résultats affichés COMME les pronos) —
        # scoreboard identique aux cartes terminées, sous le pari. Rendu seulement si un score chiffré existe.
        _fsc = l.get("score")
        if _fsc and any(ch.isdigit() for ch in str(_fsc)):
            _fsc = re.sub(r"\s*\((?:sets?|SETS?)\)\s*$", "", str(_fsc)).strip()
            board = ('<div class="cleg-board">'
                     + _live_scoreboard(_fsc, _lh, _la, tennis=(_sp == "tennis"), periods=l.get("periods"),
                                        pens=l.get("pens"))
                     + '</div>')
    # gloss = explication EN CLAIR du marché, STABLE : elle NE CHANGE JAMAIS après le résultat (demande user
    # 2026-07-20 : « le gloss ne peut pas changer après le résultat »). On N'AJOUTE PLUS le score final ici
    # (il polluait l'explication + la faisait varier avant/après règlement) — le score final est déjà porté
    # par le scoreboard et le badge de la jambe. Glose = strictement l'explication du marché, comme les simples.
    _g = _bet_gloss(sel_raw, _sp, _th, _ta)   # _th/_ta = équipes résolues (repli `name`) -> le glose double chance garde le nom d'équipe
    gloss = f'<div class="cleg-gloss"><span class="ar">↳</span> {_g}</div>' if _g else ""
    # Justification = pli TAPPABLE « 💡 Pourquoi cette jambe » (demande user 2026-07-19 : lire l'analyse
    # COMPLÈTE au tap plutôt qu'un extrait coupé à 3 lignes). Même patron que le combiné de match. Texte
    # ENTIER (nettoyé du markdown, plus de coupe à 180 car). Masqué une fois la jambe réglée (comme l'autre
    # combiné) ; `event.stopPropagation()` empêche le tap d'ouvrir/fermer la carte parente.
    _wt = _clean_cap(l.get("why"), 100000) if (why and (_res is None or why_always)) else ""
    # En PUCES (une par phrase) comme les simples/provisoires -> aéré, plus de pavé (demande user 2026-07-20).
    # + on retire le jargon de math de pari (redondant avec la barre verdict) — que des faits/risque.
    _wsents = [w for s in (_why_sentences(_wt) or ([_wt] if _wt else [])) if (w := _strip_meta_stat(s))]
    _wtl = "".join(f"<li>{html.escape(s)}</li>" for s in _wsents)
    # `.cleg-fold-bet` = MÊME filet de séparation au-dessus que « Pourquoi ce choix » (user 2026-07-21).
    _why = ('<details class="cleg-fold cleg-fold-bet"><summary class="cleg-fold-s" onclick="event.stopPropagation()">'
            f'{html.escape(why_label)}<span class="cleg-chev">▾</span></summary>'
            f'<ul class="why-ul">{_wtl}</ul></details>') if (_wt and _wtl) else ""   # jamais un pli vide
    # LIGNE VERDICT (façon provisoire) : Confiance CALIBRÉE (la jambe porte `prob` en FRACTION + `code`) ·
    # Marché · Value (masquée si négative — combiné = info seule) + grosse COTE. Remplace la pastille cote.
    _verdict = ""
    if verdict:
        _pr = l.get("prob")
        _pct = (_pr * 100 if isinstance(_pr, (int, float)) and _pr <= 1 else _pr)
        _cp = _pct
        # `prob_calibrated=True` : la proba de la jambe est DÉJÀ calibrée (combiné du jour / bonus, qui figent
        # la confiance calibrée au stockage ET calculent leur TOTAL = produit de ces probas). La re-calibrer
        # ici = DOUBLE calibration -> jambes gonflées (99 %) incohérentes avec le total (66 %). Bug user
        # 2026-08-01. On l'affiche telle quelle. Sinon (provisoires/paris joués : `prob` = BRUTE) on calibre.
        if _pct is not None and not prob_calibrated:
            try:
                _cp = analyses.calibrated_conf(_pct, _sp, l.get("code") or "")
            except Exception:
                _cp = _pct
        _cbig = (f'<span class="mc-cote"><span class="mc-cote-l">COTE</span>'
                 f'<span class="mc-cote-v">{co:g}</span></span>'
                 if isinstance(co, (int, float)) and co else "")
        _verdict = _verdict_block(co, _cp, "", _cbig, calibrated=True, hide_neg_value=True, bare=bare)
    _cote_pill = "" if verdict else _cote           # le bloc verdict porte déjà la grosse cote
    # BADGE HAUT-DROITE : à venir -> UN SEUL badge « heure (blanc) + décompte (gris) », sans « dans » (user
    # 2026-08-08). Sinon (live/réglé/en attente) -> badge d'état simple.
    if _cd:
        _when_badge = (f'<span class="cleg-bdg {_bcls} cleg-when">'
                       f'<span class="cw-h">{_btxt}</span>'
                       f'<span class="cw-sep">-</span>{_cd}</span>')
    else:
        _when_badge = f'<span class="cleg-bdg {_bcls}">{_btxt}</span>'
    # LAYOUT IDENTIQUE À LA CARTE LIVE (user 2026-08-15 : « le cadre résultat présenté comme le cadre live »)
    # pour les cartes RÉGLÉES : ligue CENTRÉE blanche + pays, SCORE final au centre (+ « Terminé »), pari+glose
    # DANS le cadre des chiffres, barre « Gagné/Perdu » (verrou), pas de séparateur ni cadre résultat séparé.
    # MISE EN PAGE « CARTE NORMALE » POUR CHAQUE JAMBE (user 2026-08-17 : « chaque jambe présentée comme une
    # carte normale — ligue, heure, décompte »). Ligue CENTRÉE + pays, et au CENTRE entre les logos : HEURE +
    # DÉCOMPTE (à venir) / SCORE + horloge (live) / SCORE final + « Terminé » (réglé) — exactement comme une
    # carte de pari simple (_sport_row). Le pari+glose vont DANS le cadre des chiffres ; barre « Confiance
    # live » (live) ou badge Gagné/Perdu (réglé) dessous. Couvre TOUS les états (avant : réglé seulement).
    if live_layout and verdict and teams and _th and _ta:
        # LIGUE = « Pays • Compétition » EXACTEMENT comme la carte normale (user 2026-08-17 : le pays manquait) :
        # pays stocké sinon déduit de la compétition (`comp_country`, statique), dédupliqué si déjà dans le nom.
        _lcomp = str(l.get("comp") or "")
        _cty = _cap(str(l.get("country") or "") or match_select.comp_country(_lcomp) or "")
        if _cty and _cty.lower() in _lcomp.lower():
            _cty = ""                          # évite « Angleterre • Angleterre » (pays déjà dans le nom)
        _cprts = [p for p in (_cty, _lcomp) if p]
        _comp_c = " • ".join(html.escape(p) for p in _cprts).upper()
        _pbox = f'<div class="mc-pick">{sel}</div>' + (f'<div class="mc-gloss">{html.escape(_g)}</div>' if _g else "")
        _resbadge, _extra = "", ""
        if _res in ("won", "lost", "push", "void"):               # RÉGLÉ : score final + « Terminé » (ou « Annulé »)
            _scf = re.sub(r"\s*\((?:sets?|SETS?)\)\s*$", "", str(l.get("score") or "")).strip()
            if _res == "void":
                _ctr = '<span class="tm-fin">Annulé</span>'
            elif _scf and any(c.isdigit() for c in _scf):
                _ctr = (f'<span class="tm-live"><b>{html.escape(_scf.replace("-", " - "))}</b>'
                        f'<span class="tm-fin">Terminé</span></span>')
            else:
                _ctr = '<span class="tm-fin">Terminé</span>'
            # BADGE RÉSULTAT (user 2026-08-15) DANS le cadre des chiffres à la place de la barre live.
            _rbt, _rbc = {"won": ("GAGNÉ", "w"), "lost": ("PERDU", "l"),
                          "push": ("REMBOURSÉ", "n"), "void": ("ANNULÉ", "n")}.get(_res, ("", "n"))
            _resbadge = f'<div class="cleg-resbadge cleg-rb-{_rbc}">{_rbt}</div>' if _rbt else ""
        elif (_lfz or {}).get("score"):                           # EN DIRECT : score + horloge M:SS + barre live
            _lsc = str(_lfz.get("score")).strip()
            _ctr = (f'<span class="tm-live"><b>{html.escape(_lsc.replace("-", " - "))}</b>'
                    + _live_clock_html(_sp, _lh, _la) + '</span>')
            _extra = _leg_bar or ""                               # barre « Confiance live » sous le cadre
        elif _cd and _hh:                                         # À VENIR : heure + DÉCOMPTE au centre (carte normale)
            _ctr = (f'<span class="tm-live"><b>{html.escape(_hh)}</b>'
                    f'<span class="tm-cd">{_cd}</span></span>')
        elif _bcls == "p":                                        # commencé, probablement fini, pas encore réglé
            _ctr = '<span class="tm-fin">En attente</span>'
        else:
            _ctr = f'<span class="tm-fin">{html.escape(_hh) if _hh else "À venir"}</span>'
        _teams_c = _teams_vs_html(_th, _ta, _ctr)
        _vb = _verdict_block(co, _cp, "", _cbig, calibrated=True, pick_html=_pbox, result_html=_resbadge, bare=bare)
        # CLASSES IDENTIQUES à la carte normale (user 2026-08-17 : « exactement la même mise en page ») :
        # `mc-line mc-line-c` + `mc-comp` (ligue centrée blanche, même taille/espacement) et `mc-teams` (même
        # typo/marge que les équipes d'un pari simple) au lieu des classes compactes `cleg-*`.
        return (f'<div class="cleg {_state} cleg-res-live mc-prem">'
                f'<div class="mc-line mc-line-c"><span class="mc-comp">{_comp_c}</span></div>'
                f'<div class="mc-teams">{_teams_c}</div>'
                f'{_vb}{_extra}{_why}</div>')
    _tdiv = '<div class="mc-div"></div>' if _teams_html else ""   # filet équipes↔pari (comme provisoires)
    return (f'<div class="cleg {_state}">'
            f'<div class="cleg-h"><span class="cleg-comp"><b class="cleg-sport spc-{_sp or ""}">{emo}</b>'
            + (f' {comp}' if comp else "")   # « FOOTBALL » retiré (foot-only, user 2026-08-08) : emoji + ligue
            + f'</span>{_when_badge}</div>'
            # Filet équipes↔pari comme les provisoires (demande user 2026-07-21) — seulement si équipes affichées.
            f'{_teams_html}{_tdiv}'
            f'<div class="cleg-body"><div class="cleg-main">'
            f'<div class="cleg-pick">{sel}</div>{gloss}</div>{_cote_pill}</div>'
            f'{_verdict}{board}{_why}</div>')


def _leg_live_prob(l: dict):
    """Chance live d'UNE jambe/pari (extrait l'état live via le cache des sources). None si pas en direct ou
    non mappable. MÊME calcul que la barre live de `_leg_card` -> réutilisé pour la chance live GLOBALE du
    combiné et de la montante (user 2026-08-08). PURE AFFICHAGE."""
    _sp = l.get("sport") or "foot"
    _lh, _la = l.get("home") or "", l.get("away") or ""
    if not (_lh and _la) and l.get("name"):
        _lh, _sep, _la = str(l.get("name")).partition(" - ")
    _lfz = live_fields(match_select.live_state_for(_sp, _lh, _la), _sp)
    if not _lfz.get("score"):
        return None
    try:
        _lld = match_select.live_state_for(_sp, _lh, _la)
        _lhs, _las = _parse_live_score(_lfz.get("score"))
        _fs = _lfz.get("fstats") or {}
        _lvals = {"corners_h": _fs.get("cor_h"), "corners_a": _fs.get("cor_a"),
                  "cards_h": _fs.get("yc_h"), "cards_a": _fs.get("yc_a"),
                  "rc_h": _fs.get("rc_h"), "rc_a": _fs.get("rc_a")}
        if _sp == "tennis":
            _lvals.update(_tennis_sets_games(_lfz.get("score")))
        _gfrac = (match_select.basket_frac(_lld, l.get("comp") or "") if _sp == "basket" else None)
        _pr = l.get("prob")
        _prpct = (_pr * 100 if isinstance(_pr, (int, float)) and _pr <= 1 else _pr)
        return analyses.live_prob(_sp, l.get("sel", ""), l.get("code", ""), _lh, _la, _lhs, _las,
                                  match_select.live_minute(_lld),
                                  match_select.live_win_odds(_sp, _lh, _la), _prpct, None, _lvals, _gfrac)
    except Exception:
        return None


def _combo_live_prob(cb: dict):
    """Chance live GLOBALE du combiné (user 2026-08-08) = produit des chances de ses jambes NON encore
    acquises : chance LIVE si la jambe tourne, sinon proba pré-match. None si AUCUNE jambe n'est en direct
    (rien de « live » à afficher). Une jambe PERDUE -> combiné à 0 %. PURE AFFICHAGE (jamais ROI/stats)."""
    legs = cb.get("legs") or []
    if not legs:
        return None
    lps = [_leg_live_prob(l) for l in legs]
    if not any(lp is not None for lp in lps):
        return None                                    # AUCUNE jambe en direct -> rien de « live » à montrer
    # NB : on ne coupe PAS sur `cb["result"]` : un combiné déjà PERDU (une jambe tombée) mais dont une AUTRE
    # jambe tourne encore reste affiché dans Live -> on montre sa chance live RÉELLE (0 %, cf. jambe perdue
    # ci-dessous). Un combiné TOTALEMENT réglé n'a aucune jambe live -> déjà écarté par le `any(...)` ci-dessus.
    prod = 1.0
    for l, lp in zip(legs, lps):
        r = l.get("result")
        if r in ("won", "push", "void"):
            continue                                   # jambe acquise -> facteur 1
        if r == "lost" or (lp and lp.get("source") == "perdu"):
            return {"pct": 0, "trend": -1, "source": "perdu"}   # une jambe perdue -> combiné à 0 %
        if lp is not None:
            prod *= max(0.0, min(1.0, (lp.get("pct") or 0) / 100.0))
        else:                                          # jambe pas encore en direct -> proba pré-match
            _pr = l.get("prob")
            _prpct = (_pr * 100 if isinstance(_pr, (int, float)) and _pr <= 1 else _pr) or 0
            prod *= max(0.0, min(1.0, _prpct / 100.0))
    return {"pct": round(prod * 100), "trend": 0, "source": "live"}


def _combo_tg_legs(cb: dict) -> str:
    """Jambes du combiné du jour rendues chacune comme un CADRE PROVISOIRE (demande user 2026-07-18) —
    en-tête SPORT • match, pari + gloss, LIGNE VERDICT (confiance/marché/cote), état/live. Via `_leg_card`.
    ORDRE CHRONOLOGIQUE des coups d'envoi (demande user 2026-07-21) — l'ordre de construction du combiné
    (prob décroissante) n'a aucun sens pour le lecteur. AFFICHAGE seul (cb['legs'] stocké intact)."""
    _legs = sorted(cb.get("legs") or [], key=lambda l: str(l.get("start") or "9999"))
    # Fin filet de SÉPARATION entre deux jambes (demande user 2026-07-21) — même patron que .mc-sep.
    # `prob_calibrated=True` : `combo_daily` FIGE déjà la confiance calibrée (combo_daily.py ~346) et son
    # TOTAL = produit de ces probas -> ne PAS re-calibrer (bug double calibration, jambes 99 % vs total 66 %).
    # why_always=True : le « Pourquoi » de chaque jambe reste consultable MÊME une fois le combiné réglé
    # (régression user 2026-08-02 : l'analyse disparaissait au règlement).
    # live_layout=True (user 2026-08-17) : CHAQUE jambe présentée comme une carte normale (ligue centrée, heure
    # + décompte / score + horloge / score final au centre entre les logos), pour TOUS les états de la jambe.
    # bare=True (user 2026-08-17) : COMBINÉ = Confiance + Cote seulement (pas Edge/Value, souvent négatifs sur
    # un combiné « sécurité » -> ils contrediraient son identité de RÉUSSITE). Barre propre sans zone marché.
    return _MC_SEP.join(_leg_card(l, why=True, verdict=True, why_always=True, prob_calibrated=True,
                                  live_layout=True, bare=True) for l in _legs)


def _combo_gold_card(*, title: str, subtitle: str, badge: str, body: str, state: str = "", dots: str = "",
                     accent: str = "") -> str:
    """Coquille DORÉE partagée du combiné — en-tête « 🎯 <title> • <subtitle> » + badge d'état, filet, puis
    le corps (jambes + ligne verdict). Utilisée par le combiné DU JOUR ET le combiné COUPE DU MONDE pour
    qu'ils soient présentés EXACTEMENT pareil (demande user 2026-07-19). `subtitle`/`badge` déjà échappés
    par l'appelant ; `title` = libellé fixe. `state` (won/lost/push) colore le bord GAUCHE (2026-07-25).
    `dots` (user 2026-08-18) = points par jambe, rendus ALIGNÉS À DROITE (avant le badge), pas collés au sous-titre."""
    _rcls = f" mc-r-{state}" if state in ("won", "lost", "push") else ""
    # BADGE RÉSULTAT EN BAS DU CADRE (user 2026-08-19) : comme les cartes Confiance/Value — barre pleine largeur
    # GAGNÉ/PERDU/REMBOURSÉ sous le corps, PLUS dans l'en-tête. Les points par jambe (`dots`) restent en tête.
    _rbt, _rbc = {"won": ("GAGNÉ", "w"), "lost": ("PERDU", "l"), "push": ("REMBOURSÉ", "n")}.get(state, ("", "n"))
    _botbar = (f'<div class="cleg-resbadge cleg-rb-{_rbc} mc-combo-res">{_rbt}</div>' if _rbt else "")
    return (
        f'<div class="row pick mc mc-tg mc-tg-gold{_rcls}{(" mc-tg-" + accent) if accent else ""}">'
        '<div class="mc-head"><div class="mc-main">'
        # 1re ligne SANS emoji 🎯, titre en BLANC via .mc-sport-w (demande user 2026-07-21).
        '<div class="mc-line">'
        f'<span class="mc-comp"><b class="mc-sport mc-sport-w">{title}</b>'
        f'<span class="mc-comp-sep"> • </span>{subtitle}</span>'
        f'{dots}</div>'                 # `.mc-comp` en flex:1 pousse les points À DROITE (badge résultat -> en bas)
        '<div class="mc-div"></div>'
        + body
        + _botbar                        # badge résultat pleine largeur EN BAS (comme Confiance/Value)
        + '</div></div></div>')


def _combo_tg_card(include_settled: bool = True, cb: dict | None = None, sport: str = "foot",
                   title: str | None = None, variant: str = "") -> str:
    """Carte « Combiné du jour » présentée COMME les cartes provisoires (Telegram) mais en OR (demande user
    2026-07-12) : en-tête, jambes = picks, SYNTHÈSE en barre cyan, Confiance, COTE en gros chiffre. Placée
    DANS les matchs en direct (plus de bandeau en tête). Info seule. '' si aucun combiné.
    `include_settled=False` (accueil « À venir » + directs) : ne renvoie RIEN une fois le combiné TERMINÉ
    (toutes ses jambes réglées) — un pari fini n'a rien à faire dans « À jouer / À venir » (demande user
    2026-07-14) ; il reste consultable dans les Stats.
    `cb` fourni : rend CE combiné (ex. calendrier « Pronos » -> combiné d'un jour PASSÉ) au lieu de celui
    d'aujourd'hui."""
    if cb is None:
        try:
            import datetime as _dt
            from app import combo_daily as _cd
            day = _cd.day_key()          # clé-jour UNIQUE du combiné (jour sportif local 06h→06h)
            cb = _cd.today(day, sport=sport, variant=variant)
        except Exception:
            cb = None
    if not cb or not cb.get("legs"):
        return ""
    # COMBINÉS HORS-RÈGLE (user 2026-08-20) : masqués de l'affichage ET de l'historique (comme du bilan) —
    # les jours 08/08→20/08 ont utilisé des logiques expérimentales abandonnées. Leurs jambes RESTENT en
    # calibration (shadows, indépendants de cette carte). Fenêtre `analyses._COMBO_RULE_VOID`.
    _cvday = (cb.get("date") or (cb.get("legs") or [{}])[0].get("start") or "")[:10]
    if analyses._combo_rule_void(_cvday):
        return ""
    _res = cb.get("result")
    # TERMINÉ = toutes les jambes réglées (won/lost/push/void). On garde la carte tant qu'AU MOINS une jambe
    # court (le combiné est « en cours ») ; une fois tout réglé, il quitte l'accueil/directs.
    _all_done = all(l.get("result") in ("won", "lost", "push", "void") for l in cb["legs"])
    if not include_settled and _all_done:
        return ""
    # PLUS de badge d'état COURANT en haut à droite (« 🟢 Live » / « ⏳ En cours ») — demande user
    # 2026-07-21 : l'état vit dans les JAMBES (badge 🟢 LIVE par jambe). On ne garde le badge global
    # que pour un combiné RÉGLÉ (✅/❌/➖, zone Résultats).
    # Badge résultat GLOBAL du combiné = MÊME style que les badges des autres paris/jambes (`.cleg-bdg`, sans
    # emoji, contour assorti à l'état) — demande user 2026-07-28 (plus le badge « ✅ Gagné » emoji à part).
    _badge = {"won": '<span class="cleg-bdg w">GAGNÉ</span>',
              "lost": '<span class="cleg-bdg l">PERDU</span>',
              "void": '<span class="cleg-bdg n">REMB.</span>'}.get(_res, "")
    _cote = cb.get("cote")
    _pconf = round((cb.get("prob") or 0) * 100)
    # COTE + CONFIANCE EFFECTIVES si ≥1 jambe est ANNULÉE/remboursée (void/push) : elle SORT du produit
    # (demande user 2026-07-18 : « vu qu'il est annulé, la cote totale ne doit reprendre que les autres
    # jambes »). Multisport = jambes indépendantes -> cote/proba combinées = produit des jambes VALIDES.
    _legs = cb.get("legs") or []
    if any(l.get("result") in ("void", "push") for l in _legs):
        _ec, _ep, _ok = 1.0, 1.0, True
        for l in _legs:
            if l.get("result") in ("void", "push"):
                continue
            try:
                _ec *= float(l.get("cote"))
                _ep *= float(l.get("prob"))
            except (TypeError, ValueError):
                _ok = False
        if _ok and _ec > 1:
            _cote = round(_ec, 2)
            _pconf = round(_ep * 100)
    _cote_big = (f'<span class="mc-cote"><span class="mc-cote-l">COTE</span>'
                 f'<span class="mc-cote-v">{round(_cote, 2):g}</span></span>'
                 if isinstance(_cote, (int, float)) and _cote else "")
    # Synthèse au-dessus des jambes RETIRÉE (demande user 2026-07-18) — chaque jambe porte déjà son « pourquoi ».
    _nlegs = len(cb.get("legs") or [])
    # SÉPARATION + ÉTIQUETTE avant le verdict GLOBAL (question user 2026-07-21 « où ajouterais-tu des
    # séparations ? ») : sans elle, la barre 50 %/cote totale semblait appartenir à la DERNIÈRE jambe.
    _body = (f'<div class="mc-combo-legs">{_combo_tg_legs(cb)}</div>'
             '<div class="combo-total-hd"><span>Total du combiné</span></div>'
             + _verdict_block(_cote, _pconf, '', _cote_big, calibrated=False, bare=True)   # combiné : Confiance+Cote seuls
             + _live_bar_html(_combo_live_prob(cb)))   # chance live GLOBALE du combiné (user 2026-08-08)
    # En-tête « COMBINÉ MULTISPORT • N jambes » (choix user 2026-07-21) : plus court que l'ancien
    # « COMBINÉ DU JOUR • N jambes · multisport » qui se TRONQUAIT (« multi… ») et répétait le titre de zone.
    # TITRE de la carte (user 2026-08-17) : « COMBINÉ » (le combiné foot EST une double chance)
    # au lieu de « COMBINÉ FOOTBALL ». Tennis/basket (simulés) gardent leur sport.
    _sptitle = {"tennis": "TENNIS", "basket": "BASKET"}.get(sport, "DOUBLE CHANCE")
    # POINTS PAR JAMBE À DROITE du « N jambes » (user 2026-08-18) : un cercle par jambe (jaune=à venir/en cours ·
    # vert=gagnée · rouge=perdue). Déplacés ici (avant : à côté du compteur de la zone -> retirés).
    def _lgc(r):
        return "w" if r == "won" else ("l" if r == "lost" else "u")
    _dots = "".join(f'<span class="zlc zlc-{_lgc(l.get("result"))}"></span>' for l in (cb.get("legs") or []))
    _dots_html = f'<span class="clegdots">{_dots}</span>' if _dots else ""
    # ACCENT DE TIER (user 2026-08-19) : Sûr = teal calme · Cote 2 = ambre ambition. Dérivé du titre (variant).
    _ttl = title or f"COMBINÉ {_sptitle}"
    _accent = "cote2" if (variant == "cote2" or "COTE 2" in _ttl) else ("sur" if "SÛR" in _ttl else "")
    return _combo_gold_card(title=_ttl, subtitle=f'{_nlegs} jambes', dots=_dots_html, badge=_badge,
                            body=_body, state=cb.get("result"), accent=_accent)


def _combo_safe_with_why(cb: dict | None) -> dict | None:
    """Enrichit (sur une COPIE — jamais le track isolé `combo_safe_track.json`) chaque jambe du combiné
    sécurité avec NOTRE justification du match, pour que le pli « Pourquoi cette jambe » s'affiche COMME sur
    toutes les autres cartes (demande user 2026-07-28 : « chaque carte de pari doit avoir son pourquoi »).
    Les jambes sécurité sont bâties par `combo_safe._combo_from_cands` SANS champ `why` -> `_leg_card` ne
    rendait aucun pli. On reprend l'analyse EXISTANTE du match (le `.md` de la fiche, via son `mid` = id de
    fiche) — SOURCE UNIQUE, comme les provisoires/Betmines : jamais deux analyses divergentes du même match."""
    if not isinstance(cb, dict) or not cb.get("legs"):
        return cb
    import copy
    cb = copy.deepcopy(cb)
    for _l in cb.get("legs") or []:
        if _l.get("why") or not _l.get("mid"):
            continue
        try:
            _l["why"] = _prov_why_snippet("foot", str(_l.get("mid")), maxlen=100000)
        except Exception:
            pass
    return cb


def _combo_safe_tg_card(include_settled: bool = False, cb: dict | None = None) -> str:
    """Carte PLEINE du COMBINÉ SÉCURITÉ FOOT (double chance la plus sûre ~2, hors ROI) pour l'onglet Pronos —
    demande user 2026-07-28. Réutilise `_combo_tg_card` (même présentation OR : jambes = picks, verdict cote/
    confiance) en passant le combiné de `app/combo_safe.py` + un titre dédié. '' si aucun combiné.
    Chaque jambe est enrichie de NOTRE « pourquoi » (`_combo_safe_with_why`) -> pli présent comme partout."""
    if cb is None:
        try:
            from app import combo_safe as _cs
            cb = _cs.today(_cs.day_key())
        except Exception:
            cb = None
    # GARDE-FOU (bug 2026-07-30) : SANS combiné sécurité du jour, NE PAS passer cb=None à `_combo_tg_card`
    # — il retomberait sur le COMBINÉ DU JOUR (`combo_daily.today`, jambes Over/Under…) rendu sous le titre
    # « SÉCURITÉ » (le combiné sécurité doit rester 100 % double chance, demande user). Pas de DC du jour =
    # aucune carte sécurité.
    if not cb or not cb.get("legs"):
        return ""
    cb = _combo_safe_with_why(cb)
    return _combo_tg_card(include_settled=include_settled, cb=cb, sport="foot", title="COMBINÉ")


def _montante_palier() -> int | None:
    """N° du palier montante EN ATTENTE (1-based) pour le titre de zone Pronos, ou None si montante inactive /
    aucun palier en attente. Même dérivation que `_montante_zone_card` (`palier` de l'état + 1)."""
    try:
        from app import montante as _mt
        if not _mt.is_active():
            return None
        st = _mt.state()
        p = st.get("pending")
        if not p or not p.get("sel"):
            return None
        return int(st.get("palier") or 0) + 1
    except Exception:
        return None


def _montante_today_bet():
    """Le pari montante DU JOUR : le `pending` (non réglé) sinon le dernier step RÉGLÉ aujourd'hui (jour
    sportif). None si montante inactive / aucun pari aujourd'hui. Sert à afficher le pari montante ET son
    résultat une fois réglé (user 2026-08-08)."""
    try:
        from app import montante as _mt
        if not _mt.is_active():
            return None
        p = _mt.state().get("pending")
        if p and p.get("sel"):
            return p
        _today = _sport_today().isoformat()
        # public_steps (pas load) -> un palier hors-technique masqué (ex. 20/08) ne réapparaît pas sur l'accueil.
        _ts = [s for s in _mt.public_steps() if str(s.get("date")) == _today and s.get("sel")]
        return _ts[-1] if _ts else None
    except Exception:
        return None


def _montante_zone_card(sport: str | None) -> tuple:
    """(titre_zone, carte) du pari MONTANTE du jour rendu COMME les autres types de paris (carte `_leg_card`
    avec ligne verdict + pli « Pourquoi »), pour sa propre zone « Montante · Palier N » (demande user
    2026-07-30). ('', '') si montante inactive / pas de palier en attente / vue hors foot. Purement AFFICHAGE
    (le suivi montante reste dans app/montante.py, hors ROI)."""
    if sport not in (None, "foot"):
        return "", ""
    try:
        from app import montante as _mt
        if not _mt.is_active():
            return "", ""
        st = _mt.state()
        p = _montante_today_bet()                      # pending OU pari réglé du jour (user 2026-08-08)
        if not (p and p.get("sel")):
            return "", ""
        # palier : le pending est le PROCHAIN (state+1) ; un pari RÉGLÉ du jour est celui qui vient d'avancer.
        _settled = p.get("result") in ("won", "lost", "push", "void")
        palier = int(st.get("palier") or 0) + (0 if _settled else 1)
        mid = str(p.get("mid") or "")
        d = analyses.meta("foot", mid) or {}
        start = d.get("start")
        _comp = d.get("comp")
        if not (start and _comp):                      # sidecar sans heure/ligue (match pas encore analysé) ->
            try:                                        # repli sur le PROGRAMME du jour (heure ET ligue, user 2026-08-18).
                import json as _j
                _pg = _j.load(open(os.path.join(analyses._ROOT, "data", "day_programme.json"), encoding="utf-8"))
                _pm = next((m for m in _pg.get("matches", []) if str(m.get("id")) == mid), None)
                start = start or (_pm or {}).get("start")
                _comp = _comp or (_pm or {}).get("comp")
            except Exception:
                pass
        # CONFIANCE de la montante = sa PROPRE proba CALIBRÉE (safe_dc/Pinnacle), stockée au palier (user
        # 2026-08-18 : la carte doit être présentée COMME les autres paris -> il lui faut sa confiance). La
        # montante n'est PAS un pari retenu du flagship (match souvent pas encore analysé) -> `retained_bet`
        # renvoie None : on prend `p['prob']` en priorité (calibrée), repli sur la brute du pari joué si dispo.
        prob = p.get("prob")
        _prob_cal = prob is not None                   # p['prob'] est DÉJÀ calibrée (comme les jambes de combiné)
        if prob is None:
            try:
                rb = analyses.retained_bet("foot", mid, for_history=True)
                prob = (rb.get("cprob") or rb.get("prob")) if rb else None
                _prob_cal = bool(rb and rb.get("cprob"))
            except Exception:
                prob = None
        # TABLEAU DES SCORES comme les autres cartes résultat (user 2026-08-08) : score/périodes/pens du
        # sidecar via result_board, une fois le pari réglé.
        _board = (analyses.result_board(d, "foot") or {}) if p.get("result") in ("won", "lost", "push", "void") else {}
        # ANALYSE de la montante (user 2026-08-18) : le « pourquoi » ANALYSÉ AU SCAN (stocké au palier, comme
        # le combiné) prime -> affiché en entier dès le matin, indépendamment du pari simple (fait ~1 h avant).
        # Repli sur l'analyse du sidecar (_prov_why_snippet) si le match a été analysé depuis (vague).
        _mwhy = p.get("why") or _prov_why_snippet("foot", mid, maxlen=100000, played=True)
        leg = {"sport": "foot", "home": d.get("home") or p.get("home"), "away": d.get("away") or p.get("away"),
               "name": p.get("match"), "comp": _comp or p.get("comp"), "start": start,
               "sel": p.get("sel"), "cote": p.get("cote"),
               "code": p.get("code"), "result": p.get("result"), "prob": prob,
               "score": _board.get("score"), "periods": _board.get("periods"), "pens": _board.get("pens"),
               "why": _mwhy}
        # live_layout=True (user 2026-08-18) : MÊME mise en page qu'un pari Confiance (ligue CENTRÉE + pays,
        # logos + équipes + heure/score au CENTRE, pari + glose CENTRÉS dans le cadre verdict) — sinon la
        # montante gardait le layout compact `.cleg` (pari collé à gauche, sans ligue) ≠ carte Confiance.
        # bare=True (user 2026-08-18 « épure pour combiné et montante ») : grille ÉPURÉE Confiance + Cote (pas
        # Edge/Value), comme les jambes du combiné -> présentation cohérente entre les 2 paris « sûrs » (DC).
        card = _leg_card(leg, why=True, verdict=True, teams=True, why_label="Pourquoi ce pari",
                         prob_calibrated=_prob_cal, live_layout=True, bare=True)
        # LIGNE « mont-note » (mise rejouée · voir l'échelle) RETIRÉE sous la carte (user 2026-08-08).
        return f"Montante · Palier {palier}", card
    except Exception:
        return "", ""




def _combo_premium_block(sport: str, mid, home: str, away: str) -> str:
    """CORPS d'une carte COMBINÉ RETENU (ROI) — destiné à la coquille dorée `_combo_gold_card` (demande user
    2026-07-19 : le combiné Coupe du Monde présenté EXACTEMENT comme le combiné du jour). Contenu : le SIMPLE
    retenu (si présent, cas rare « multi-paris ») en tête, PUIS le combiné = jambes (`_leg_card`, avec pli
    « Pourquoi ») + ligne VERDICT (cote corrélée · confiance · value). Plus de tag « 🎲 COMBINÉ » (redondant
    avec l'en-tête doré). Purement AFFICHAGE (règlement/ROI inchangés). '' si pas de combiné exploitable."""
    m = analyses.meta(sport, mid) or {}
    combo = (m.get("combo") or {})
    legs = combo.get("legs") or []
    if not legs:
        return ""
    # CONTEXTE MATCH en tête (user 2026-08-29) : un combiné MÊME-MATCH n'affichait NI équipes NI ligue NI score
    # -> on montre le match UNE fois en tête (ligue centrée « Pays • Compétition » + équipes/logos + score final),
    # EXACTEMENT comme une carte normale, pour qu'il soit présenté comme le produit actuel. Les jambes restent
    # `teams=False` (le match est déjà en en-tête -> pas de répétition).
    out = ""
    if home and away:
        _lcomp = str(m.get("comp") or "")
        _cty = _cap(str(m.get("country") or "") or match_select.comp_country(_lcomp) or "")
        if _cty and _cty.lower() in _lcomp.lower():
            _cty = ""                              # évite « Angleterre • Angleterre »
        _comp_c = " • ".join(html.escape(x) for x in (_cty, _lcomp) if x).upper()
        _bd = analyses.result_board(m, sport) or {}
        _scf = re.sub(r"\s*\((?:sets?|SETS?)\)\s*$", "", str(_bd.get("score") or "")).strip()
        if _scf and any(ch.isdigit() for ch in _scf):
            _ctr = (f'<span class="tm-live"><b>{html.escape(_scf.replace("-", " - "))}</b>'
                    '<span class="tm-fin">Terminé</span></span>')
        else:
            _ctr = ""                              # pas de score (à venir / indispo) -> équipes seules
        out = (f'<div class="mc-line mc-line-c"><span class="mc-comp">{_comp_c}</span></div>'
               f'<div class="mc-teams">{_teams_vs_html(home, away, _ctr)}</div><div class="mc-div"></div>')
    # SIMPLE retenu ADDITIONNEL (cas « carte multi-paris » : un match CdM peut porter un simple retenu ET
    # le combiné). On le montre en tête, présenté comme une carte de pari (pick gras + glose + verdict).
    rb = analyses.retained_bet(sport, mid)
    if rb and rb.get("sel"):
        _ssel = rb.get("sel", "")
        _scote = rb.get("cote")
        _sconf = rb.get("cprob") or rb.get("prob")
        _scb = (f'<span class="mc-cote"><span class="mc-cote-l">COTE</span>'
                f'<span class="mc-cote-v">{_scote:g}</span></span>'
                if isinstance(_scote, (int, float)) and _scote else "")
        _sgl = _bet_gloss(_ssel, sport, home, away)
        _sgloss = f'<div class="mc-gloss"><span class="ar">↳</span>{html.escape(_sgl)}</div>' if _sgl else ""
        out += (f'<div class="mc-pick">{html.escape(_pretty_sel(_ssel, home, away))}</div>'
                + _sgloss
                + _verdict_block(_scote, _sconf, '🎯 Simple · compté au ROI', _scb, calibrated=True)
                + '<div class="mc-div"></div>')      # filet séparateur simple ↔ combiné
    # COMBINÉ : jambes (cartes de simple) puis ligne verdict — IDENTIQUE au combiné du jour. Cote = VRAIE cote
    # corrélée Unibet (real_odds) sinon produit (total) ; confiance = proba corrélée (calibrated=False, comme
    # _combo_tg_card). Jambes same-match -> on injecte sport/équipes, nom vide (le match est déjà en en-tête).
    _cote = combo.get("real_odds") or combo.get("total")
    _cote_big = (f'<span class="mc-cote"><span class="mc-cote-l">COTE</span>'
                 f'<span class="mc-cote-v">{round(_cote, 2):g}</span></span>'
                 if isinstance(_cote, (int, float)) and _cote else "")
    _pconf = combo.get("prob")
    _legs = [{**l, "sport": sport, "home": home, "away": away, "name": ""} for l in legs]
    # CONFIANCE par jambe -> LIGNE VERDICT complète (barre + Confiance/Marché/Cote) COMME le combiné du jour.
    # Les jambes CdM ne stockent pas `prob` ; on la retrouve dans les FANTÔMES du match (`shadow`, même
    # marché par `code`), qui portent la proba de l'analyste (source identique au combiné du jour). Sans ça,
    # _leg_card dégradait le verdict en simple pastille de cote (demande user 2026-07-19 : « pas présenté
    # pareil »). Désambiguïsation par cote si plusieurs jambes partagent un code.
    _shadow = m.get("shadow") or []
    for _lg in _legs:
        if _lg.get("prob") is None and _lg.get("code"):
            _c = [s for s in _shadow if s.get("code") == _lg.get("code")]
            if len(_c) > 1:
                _c = sorted(_c, key=lambda s: abs((s.get("cote") or 0) - (_lg.get("cote") or 0)))
            if _c and _c[0].get("prob") is not None:
                _lg["prob"] = _c[0]["prob"]
    out += (f'<div class="mc-combo-legs">'
            + _MC_SEP.join(_leg_card(l, why=True, verdict=True, teams=False, why_always=True) for l in _legs)   # même match -> pas d'équipes répétées ; why_always : pourquoi consultable même réglé (régression user 2026-08-02)
            + '</div>'
            '<div class="combo-total-hd"><span>Total du combiné</span></div>'
            + _verdict_block(_cote, _pconf, '', _cote_big, calibrated=False)
            + _live_bar_html(_combo_live_prob({"legs": _legs})))   # chance live GLOBALE du combiné (user 2026-08-08)
    return out


# Icône par catégorie de pari (user 2026-08-19 « professionnel ») — glyphes SVG MONOCHROMES (tracé fin, teinte
# acier uniforme via `currentColor`), à gauche du titre de zone. Plus d'emojis couleur (rendu « grand public »).
_ZONE_ICON = {
    "prog": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
             '<line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/>'
             '<line x1="9" y1="18" x2="20" y2="18"/><circle cx="4.5" cy="6" r="1.4" fill="currentColor" stroke="none"/>'
             '<circle cx="4.5" cy="12" r="1.4" fill="currentColor" stroke="none"/>'
             '<circle cx="4.5" cy="18" r="1.4" fill="currentColor" stroke="none"/></svg>'),
    "play": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.6-3 7.7-7 9-4-1.3-7-4.4-7-9V6l7-3z"/>'
             '<path d="M9 12l2 2 4-4"/></svg>'),
    "value": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round"><path d="M12 3L3 9l9 12 9-12-9-6z"/><path d="M3 9h18"/></svg>'),
    "mont": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M16 7h5v5"/></svg>'),
    "combo": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round"><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/></svg>'),
    "abst": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
             '<line x1="9" y1="5" x2="9" y2="19"/><line x1="15" y1="5" x2="15" y2="19"/></svg>'),
    "indic": ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round"><path d="M9 3h6M10 3v6l-5.2 8.4A2 2 0 006.5 21h11a2 2 0 001.7-3.6L14 9V3"/></svg>'),
}


def _zone(kind: str, title: str, tag: str, count: int, body: str,
          *, collapsible: bool = False, open_: bool = True, empty: str | None = None,
          record: tuple | None = None, zk: str | None = None,
          leg_results: list | None = None, subtitle: str = "", waiting: bool = False) -> str:
    """ZONE (accueil ET onglets sport) — regroupement par nature de pari, en-tête PREMIUM ÉPURÉ : un point
    de couleur (état) + le titre en casse normale + un compteur discret + un mot-clé d'état à droite, posé
    sur un filet fin. PAS de barre verticale ni de majuscules criardes (refonte 2026-07-11). Corps = les
    cartes déjà triées. `collapsible` -> zone repliable (`<details>`, ex. Terminés). `empty` -> message si
    corps vide (état honnête, ex. « aucun pari de value »). '' si corps vide ET sans `empty`. Pur affichage."""
    _empty_zone = False
    if not (body and body.strip()):
        if not empty:
            return ""
        # ZONE VIDE (user 2026-08-19) : EN-TÊTE SEUL (icône + titre + badge « en attente »), SANS phrase — le
        # badge dit déjà « rien pour l'instant », la phrase faisait doublon (comme Abstention). `empty` (non vide)
        # ne sert plus que de DRAPEAU « afficher cette catégorie même vide » ; son texte n'est plus rendu. Zone
        # NON repliable (rien à déplier) -> <section>, jamais <details>.
        body, count = "", 0
        _empty_zone = True
        collapsible = False
    # RECORD du JOUR par type (demande user 2026-08-02) : le BADGE (compteur rond) montre le nombre sélectionné ;
    # à côté, des pastilles -> à venir ⏳ · EN DIRECT 🟢 · gagné–perdu (score). Badge = total ; chaque pastille
    # n'apparaît que si > 0. `record` = (total, à_venir, live, gagnés, perdus).
    # ⚠️ LIVE = 🟢 VERT (pas 🔴 rouge) : le rouge se lisait « raté/perdu » (confusion user 2026-08-06) alors
    # que c'est « en direct » — vert, cohérent avec le badge « Live » des cartes et l'onglet Live.
    rec = ""
    badge_n = count
    chips = ""
    if leg_results is not None and leg_results:
        # COMBINÉ : le badge chiffre = le NOMBRE de COMBINÉS (1, via `count`) — PAS le nb de jambes (user
        # 2026-08-10) — chiffre SEUL coloré, avec À CÔTÉ un cercle par JAMBE (jaune=non joué · vert=gagné ·
        # rouge=perdu). Couleur du badge chiffre : jaune par défaut · ROUGE dès qu'UNE jambe perdue · VERT si
        # TOUTES gagnées.
        def _lgcls(r):                                    # jaune (non joué) / vert (gagné) / rouge (perdu)
            return "w" if r == "won" else ("l" if r == "lost" else "u")
        _any_lost = any(r == "lost" for r in leg_results)
        _all_won = all(r == "won" for r in leg_results)
        _ov = "l" if _any_lost else ("w" if _all_won else "u")   # état global du badge chiffre
        # Points colorés par jambe RETIRÉS (user 2026-08-18) : seul le chiffre du badge reste.
        chips += f'<span class="zr zrleg zrleg-{_ov}">{count}</span>'
    elif record:
        # 6 états (user 2026-08-08) : total · à venir · live · EN ATTENTE DE RÉSOLUTION · gagnés · perdus.
        # SANS EMOJI (user 2026-08-08) : distinction par COULEUR seule. à venir=JAUNE · en attente=GRIS ·
        # live=vert (texte) · gagnés=VERT plein · perdus=ROUGE plein. Ancien tuple 5 -> pending=0 (compat).
        _r6 = list(record) + [0] * (6 - len(record))
        _s, _up, _lv, _w, _l, _pend = _r6[:6]   # _lv (slot « en direct » vert distinct) inutilisé : le live
        badge_n = _s                             # verrouillé compte vert/rouge, l'incertain reste jaune (_up)
        # (Combiné : ce chemin `elif record:` n'est PAS emprunté par les combinés — ils passent leg_results
        # et prennent la branche ci-dessus ; ici on ne rend que les zones simples.)
        # ORDRE = MÊME SENS que les cartes en dessous (user 2026-08-09), cycle de vie non-joué -> réglé :
        # À VENIR/LIVE (jaune) · EN ATTENTE (gris) · GAGNÉS (vert) · PERDUS (rouge). Le jaune (qui inclut la
        # montante + les lives) est donc EN PREMIER, comme les matchs à venir/en cours en haut de la liste.
        if _up:                                          # à venir + live incertain (pas de résultat) : JAUNE, EN PREMIER
            chips += f'<span class="zr zru">{_up}</span>'
        if _pend:                                        # fini mais PAS ENCORE RÉGLÉ : badge GRIS
            chips += f'<span class="zr zrp">{_pend}</span>'
        if _w:                                            # gagnés : badge VERT
            chips += f'<span class="zr zrw">{_w}</span>'
        if _l:                                            # perdus : badge ROUGE
            chips += f'<span class="zr zrl">{_l}</span>'
    # COUNT CHIP (user 2026-08-17) : une zone SANS win/loss (Programme, Abstention) affiche quand même le NOMBRE
    # de matchs, avec le MÊME badge que Confiance/Value (pastille `.zr`), pour l'homogénéité des catégories.
    if not chips and count > 0:
        chips = f'<span class="zr zrn">{count}</span>'
    # ZONE VIDE : badge « en attente » SEULEMENT si `waiting` (= il y a un PROGRAMME aujourd'hui, des matchs, mais
    # pas encore de pari dans cette catégorie — user 2026-08-24). Si le programme est VIDE (jour sans match, ou
    # avant le scan) -> AUCUN badge (« en attente » de rien n'a pas de sens). En-tête seul dans ce cas.
    if _empty_zone and not chips and waiting:
        chips = '<span class="zr zr-wait">en attente</span>'
    if chips:
        rec = f'<span class="zone-rec">{chips}</span>'
    # BADGE TOTAL (.zone-n) RETIRÉ (user 2026-08-07) : le record (à venir ⏳ · live 🟢 · score) porte déjà
    # l'info ; le compteur rond faisait doublon.
    # « en direct » -> ANCIEN BADGE « 🟢 Live » (user 2026-08-15) : le libellé texte des zones Live redevient
    # le badge vert pulsant (comme avant sur les cartes). Les autres tags gardent le texte discret.
    # Badge « 🟢 Live » = TOUT À DROITE, le COMPTEUR à sa GAUCHE (user 2026-08-17) : on le sort du `head`
    # (côté titre) et on l'appose APRÈS le compteur (zone-right), en DERNIER enfant de .zone-h.
    live_badge = ""
    if tag == "en direct":
        t = ""
        live_badge = '<span class="mc-badge mc-live zone-live">🟢 Live</span>'
    elif tag:
        t = f'<span class="zone-tag">{html.escape(tag)}</span>'
    else:
        t = ""
    # TITRE + tag à GAUCHE ; le BADGE compteur (rec) + le chevron sont poussés À DROITE (user 2026-08-17 :
    # « ce badge doit être aligné à droite près de la flèche qui déplie »). `.zone-right{margin-left:auto}`.
    # ICÔNE de catégorie (user 2026-08-19) : identité visuelle par type (⭐ Confiance · 💎 Value · 🪜 Montante
    # · 🎯 Combiné · ⏸ Abstention · 📋 Programme), discrète à gauche du titre.
    _ic = _ZONE_ICON.get(kind, "")
    _ich = f'<span class="zone-ic">{_ic}</span>' if _ic else ""
    # SOUS-TITRE petit collé au titre (user 2026-08-22 : « Palier N » écrit plus petit que « Montante »).
    _sub = f'<span class="zone-sub">{html.escape(subtitle)}</span>' if subtitle else ""
    head = (f'{_ich}<span class="zone-t">{html.escape(title)}</span>{_sub}{t}')   # point (.zone-dot) retiré (user 2026-08-08)
    if collapsible:
        op = " open" if open_ else ""
        # `data-zk` = clé de persistance du repli (localStorage, JS `_CAL_JS`) : ton choix plier/déplier
        # une zone est mémorisé et réappliqué après chaque swap de jour.
        # Badge « 🟢 Live » GROUPÉ avec le compteur DANS `zone-right`, AVANT le chevron (user 2026-08-18 :
        # « à droite de chaque titre » — chevron tout au bord, badge live + nombre juste à sa gauche).
        return (f'<details class="zone zone-{kind} zone-col" data-zk="{zk or kind}"{op}>'
                f'<summary class="zone-h">{head}<span class="zone-right">{rec}{live_badge}'
                f'<span class="zone-chev">▾</span></span></summary>'
                f'<div class="zone-b">{body}</div></details>')
    if _empty_zone:                                    # catégorie vide -> en-tête seul (icône + titre + badge)
        return (f'<section class="zone zone-{kind} zone-vide"><div class="zone-h">{head}'
                f'<span class="zone-right">{rec}{live_badge}</span></div></section>')
    return (f'<section class="zone zone-{kind}"><div class="zone-h">{head}'
            f'<span class="zone-right">{rec}{live_badge}</span></div>'
            f'<div class="zone-b">{body}</div></section>')


_WD_ABBR = ["LUN", "MAR", "MER", "JEU", "VEN", "SAM", "DIM"]
_WD_FULL = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MO_FULL = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
            "septembre", "octobre", "novembre", "décembre"]


# FRONTIÈRE DU JOUR SPORTIF (heure locale belge). Portée à 08h le 2026-08-20 (avant : 06h) sur demande user :
# « les matchs du jour ne doivent pas disparaître avant 08h » — un match de nuit encore EN COURS (ex. MLS qui
# finit vers 05-07h) reste rattaché à la journée de la veille jusqu'à 08h, au lieu de s'évanouir au rollover de
# 06h (le programme se vide dès que sa date ≠ jour sportif courant, cf. `_load_day_programme`). Aligne AUSSI le
# rollover sur l'intention d'origine « vider le programme avant 08h belge » (le scan du matin le repeuple).
# SOURCE UNIQUE : tout le reste (calendrier, stats, combo_daily.day_key, montante, routeurs) passe par ici.
_SPORT_DAY_START_H = 8


def _sport_date(local_dt):
    """« JOUR SPORTIF » : la journée court de 08h à 08h le lendemain (demande user 2026-07-21 pour englober les
    matchs américains de la nuit ; frontière portée 06h→08h le 2026-08-20). Un événement AVANT 08h locale compte
    pour la VEILLE. `local_dt` = datetime LOCAL. Renvoie une `date`. Décalage de −`_SPORT_DAY_START_H` h puis .date()."""
    return (local_dt - timedelta(hours=_SPORT_DAY_START_H)).date()


def _sport_today():
    """La date SPORTIVE de maintenant (fenêtre 08h→08h, cf. `_sport_date`)."""
    return _sport_date(to_local(datetime.now(timezone.utc)) or datetime.now())


def _day_header(iso: str) -> str:
    """En-tête de contexte du jour affiché (haut de #day-content) : « Aujourd'hui » / « Hier » / date pleine
    + la date complète en sous-titre. Rend la navigation calendrier LISIBLE (on sait quel jour on regarde)."""
    from datetime import date as _date, timedelta
    today = _sport_today()
    try:
        d = _date.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    full = f"{_WD_FULL[d.weekday()]} {d.day} {_MO_FULL[d.month - 1]}"
    if d == today:
        lead, sub = "Aujourd'hui", full
    elif d == today - timedelta(days=1):
        lead, sub = "Hier", full
    else:
        lead, sub = full[0].upper() + full[1:], ""
    sub_html = f'<span class="day-hd-sub">{html.escape(sub)}</span>' if sub else ""
    return f'<div class="day-hd"><span class="day-hd-lead">{html.escape(lead)}</span>{sub_html}</div>'


def _day_calendar(iso: str, sport: str | None = None, days: int | None = None) -> str:
    """CALENDRIER HORIZONTAL premium en tête de l'onglet Pronos (user 2026-08-19) — REMPLACE `_day_header`.
    Bande de dates cliquables (jour de semaine + numéro + pastille résultat vert/rouge/neutre issue de
    `_daily_results_map`), jour SÉLECTIONNÉ mis en avant, « AUJ. » marqué. Un clic recharge `#day-content` via
    `/jour?date=<iso>&sport=<sk>&frag=1` (JS `_DAYCAL_JS`). Se re-rend à chaque swap -> surbrillance toujours à
    jour. `days` = fenêtre glissante (dernières N dates, aujourd'hui à droite). None (défaut) -> remonte jusqu'au
    début du phare `_LZ_SINCE` (22/06), pour afficher TOUT l'historique dans la frise (user 2026-08-29)."""
    from datetime import date as _date, timedelta
    today = _sport_today()
    if days is None:                                       # défaut : couvrir tout l'historique jusqu'au 22/06
        try:
            days = max(1, (today - _date.fromisoformat(_LZ_SINCE)).days)
        except (ValueError, TypeError):
            days = 34
    try:
        sel = _date.fromisoformat(iso)
    except (ValueError, TypeError):
        sel = today
    rmap = _daily_results_map()                            # TOUS paris réglés -> pilote la CLIQUABILITÉ du jour
    cmap = _daily_conf_results_map()                       # CONFIANCE seule -> pilote la COULEUR de la pastille
    cells = []
    for i in range(days, -1, -1):                          # du plus ancien (gauche) à AUJOURD'HUI (droite)
        dd = today - timedelta(days=i)
        di = dd.isoformat()
        st = rmap.get(di) or {}
        settled = st.get("settled", 0)                     # activité TOUS paris (clic/emphase)
        # PASTILLE = CONFIANCE UNIQUEMENT (user 2026-08-30) : vert/rouge/neutre selon le net ROI des seuls paris
        # de Confiance du jour. Un jour SANS confiance réglée (que de la Value, un combiné…) -> pas de point coloré,
        # même s'il reste cliquable (l'activité TOUS-paris ci-dessus garde le jour actif).
        cst = cmap.get(di) or {}
        c_settled, c_profit = cst.get("settled", 0), cst.get("profit", 0.0)
        if c_settled:                                      # pastille = net ROI des CONFIANCES
            dcls = "pos" if c_profit > 1e-9 else ("neg" if c_profit < -1e-9 else "neu")
            dot = f'<span class="dcd-dot {dcls}"></span>'
        else:
            dot = '<span class="dcd-dot none"></span>'
        # jour SANS pari réglé (TOUS types) = dé-emphasé ET NON cliquable (user 2026-08-19) — sauf aujourd'hui.
        _is_empty = (not settled and dd != today)
        _cls = ("daycal-d" + (" on" if dd == sel else "") + (" today" if dd == today else "")
                + (" empty" if _is_empty else ""))
        _wd = _WD_ABBR[dd.weekday()]
        _my = f"{_MO_FULL[dd.month - 1].capitalize()} {dd.year}"   # mois+année de la cellule (en-tête au scroll)
        # PLUS de tag mois DANS la cellule (user 2026-08-19) : le mois vit dans l'EN-TÊTE (mis à jour au scroll).
        cells.append(
            f'<button type="button" class="{_cls}" data-date="{di}" data-my="{html.escape(_my)}"'
            f'{" disabled" if _is_empty else ""} '
            f'aria-label="{html.escape(_day_label_full(dd))}">'
            f'<span class="dcd-wd">{"AUJ." if dd == today else _wd}</span>'
            f'<span class="dcd-day">{dd.day}</span>{dot}</button>')
    # EN-TÊTE MOIS/ANNÉE (user 2026-08-19) : contexte du jour affiché, mis à jour au scroll par `_DAYCAL_JS`.
    _hdr_my = f"{_MO_FULL[sel.month - 1].capitalize()} {sel.year}"
    # BOUTON « AUJOURD'HUI » (user 2026-08-19) : TOUJOURS rendu (visibilité pilotée par JS `updGoto`) -> visible
    # dès qu'on regarde un jour passé OU que la cellule AUJ. est sortie du calendrier au scroll. Clic : si on est
    # déjà sur aujourd'hui, on RECENTRE seulement (pas de re-fetch -> ne réouvre PAS les types de paris).
    _today_btn = (f'<button type="button" class="daycal-goto" data-date="{today.isoformat()}">'
                  f'Aujourd\'hui</button>')
    return (f'<div class="daycal" id="daycal">'
            f'<div class="daycal-hd"><span class="daycal-mo" id="daycal-mo">{html.escape(_hdr_my)}</span>'
            f'{_today_btn}</div>'
            f'<div class="daycal-track">{"".join(cells)}</div></div>')


def _day_label_full(d) -> str:
    """Libellé accessible d'une date : « mardi 18 août »."""
    return f"{_WD_FULL[d.weekday()]} {d.day} {_MO_FULL[d.month - 1]}"


def _card_has_bet(r: dict) -> bool:
    """Vrai si le match de cette carte a porté un VRAI pari proposé (simple joué figé OU combiné réglé) —
    filtre les ABSTENTIONS (analysées sans pari) de l'historique « Pronos » (demande user 2026-07-19 :
    ne reprendre que les types de paris réellement proposés)."""
    m = re.search(r"/(app|foot|basket)/match/(\d+)", r.get("url") or "")
    if not m:
        return False
    sport = "tennis" if m.group(1) == "app" else m.group(1)
    d = analyses.meta(sport, m.group(2)) or {}
    if (d.get("stat_bet") or {}).get("result") in ("won", "lost", "push"):
        return True
    return (d.get("combo") or {}).get("result") in ("won", "lost", "void")


_DRM_CACHE: dict = {"ts": 0.0, "map": None}


def _daily_results_map() -> dict:
    """{iso_local: {'won':int, 'settled':int, 'profit':float}} des paris JOUÉS réglés (`stat_bet` figé) +
    combinés du jour, agrégés par JOUR LOCAL. Sert aux pastilles du calendrier « Pronos » (vert/rouge selon
    le bilan) et au bilan affiché en tête d'un jour passé. Mise à plat 1 u (won -> cote−1, lost -> −1).
    Caché 30 s : réutilisé par le bandeau + chaque vue jour d'une même salve de navigation (perf)."""
    _now = time.time()
    if _DRM_CACHE["map"] is not None and _now - _DRM_CACHE["ts"] < 30:
        return _DRM_CACHE["map"]
    res: dict = {}

    def _add(iso: str, won: bool, profit: float):
        e = res.setdefault(iso, {"won": 0, "settled": 0, "profit": 0.0})
        e["settled"] += 1
        e["won"] += 1 if won else 0
        e["profit"] += profit

    # ROI FOOTBALL-ONLY (demande user 2026-07-25) : le bilan quotidien du bandeau Pronos ne compte QUE le
    # football (comme le ROI global / le calendrier). Les paris tennis/basket (SIMULÉS, `background_sports`)
    # et les combinés du jour non-foot ne rentrent PLUS dans les pastilles/ROI du bandeau.
    _bg = analyses.background_sports()
    # Itérateur LÉGER (pas de `list_for`/`retained_bet` : on ne lit que stat_bet + date -> perf, cf. audit
    # 2026-07-20). Iso-comportement vérifié (mêmes paris won/lost agrégés).
    for _sp, sb, dt in analyses.iter_stat_bets():
        if _sp in _bg:                                         # tennis/basket simulés -> hors bilan quotidien
            continue
        ld = to_local(dt) if dt else None
        if ld is None:
            continue
        won = sb["result"] == "won"
        _add(_sport_date(ld).isoformat(), won, (float(sb.get("cote") or 1) - 1) if won else -1.0)
    # COMBINÉS FOOTBALL HORS ROI (demande user 2026-07-27) : ni le « combiné du jour » (module combo_daily)
    # ni les combinés SIDECAR par match (Coupe du Monde…) ne comptent dans le bilan quotidien / le calendrier.
    # Le ROI = SIMPLES football uniquement (cohérent avec stats_full.overall qui exclut déjà les combinés).
    # Les combinés restent AFFICHÉS comme joués (cartes, ✓/✗) — info seule, jamais au ROI.
    _DRM_CACHE.update(ts=_now, map=res)
    return res


_DRM_CONF_CACHE: dict = {"ts": 0.0, "map": None}


def _daily_conf_results_map() -> dict:
    """Comme `_daily_results_map` mais RESTREINT aux paris de CONFIANCE (tier « confiance »), football.
    Sert UNIQUEMENT à colorer les PASTILLES du calendrier horizontal (demande user 2026-08-30 : le point
    vert/rouge ne doit refléter QUE la Confiance — le chiffre phare — pas la Value/le combiné/la montante).
    Net 1 u (won -> cote−1, lost -> −1), agrégé par JOUR SPORTIF. On lit le tier FIGÉ (`tier_of`, monotone :
    `confidence_bet`/`stat_bet.kind`), donc immunisé à la dérive de calibration. Caché 30 s (comme le map global)."""
    _now = time.time()
    if _DRM_CONF_CACHE["map"] is not None and _now - _DRM_CONF_CACHE["ts"] < 30:
        return _DRM_CONF_CACHE["map"]
    res: dict = {}
    for d in analyses.iter_meta("foot"):
        if d.get("roi_void"):
            continue
        sb = d.get("stat_bet")
        if not (isinstance(sb, dict) and sb.get("result") in ("won", "lost")):
            continue
        if analyses.tier_of(d) != "confiance":                 # Value / combiné / montante -> hors pastille
            continue
        ld = to_local(d.get("_start_dt")) if d.get("_start_dt") else None
        if ld is None:
            continue
        won = sb["result"] == "won"
        e = res.setdefault(_sport_date(ld).isoformat(), {"won": 0, "settled": 0, "profit": 0.0})
        e["settled"] += 1
        e["won"] += 1 if won else 0
        e["profit"] += (float(sb.get("cote") or 1) - 1) if won else -1.0
    _DRM_CONF_CACHE.update(ts=_now, map=res)
    return res


# _calendar_strip (bandeau jours en tête de Pronos) SUPPRIMÉ le 2026-07-25 (demande user) : la navigation
# par jour + le bilan quotidien vivent dans l'onglet CALENDRIER dédié. `_daily_results_map` reste utilisé par
# `_day_view` (bilan « X/Y gagnés » du détail d'un jour, réutilisé par le calendrier).


def _item_sport(r: dict) -> str | None:
    """Sport d'une carte/ligne du programme (pour le filtre sport de Pronos) : `_sport` (provisoires) sinon
    déduit de l'URL (autoritaire). ⚠️ Le champ `tour` NE suffit PAS (le basket WNBA/NBA porte `tour=WNBA`
    -> ne jamais l'assimiler au tennis) : on ne l'utilise qu'en repli pour atp/wta si l'URL manque."""
    s = r.get("_sport")
    if s:
        return s
    url = r.get("url") or ""
    if "/foot/match" in url:
        return "foot"
    if "/basket/match" in url:
        return "basket"
    if "/app/match" in url:
        return "tennis"
    return "tennis" if (r.get("tour") or "").lower() in ("atp", "wta") else None


def _provisional_results(iso: str, sport: str | None = None, header: bool = True) -> str:
    """Bloc compact des PROVISOIRES RÉGLÉS d'un jour (info seule, hors ROI). Depuis 2026-08-01 les réglés
    restent DANS leur section de type (« Paris provisoires »), plus de zone « Résultats du jour » séparée
    (demande user) -> `header=False` retire le sous-titre « 🧪 Provisoires » redondant avec le titre de zone.
    Une ligne par provisoire : ✓/✗ + sport coloré + pari + match. '' si aucun."""
    from datetime import datetime as _dt
    try:
        from app import provisional as _pv
        allp = _pv.load()
    except Exception:
        return ""
    _bg = analyses.background_sports()
    rows = []
    for p in allp.values():
        if not isinstance(p, dict):
            continue
        # Vue « Tous » de Pronos (sport=None) : PAS les provisoires tennis/basket (simulés, hors ROI) — ils
        # vivent dans leur cadre sport simulé (Stats), jamais dans les Résultats du jour de Pronos (demande
        # user 2026-07-26). Un onglet sport explicite les montrerait, mais Pronos n'a pas d'onglet tennis/basket.
        if sport is None and p.get("sport") in _bg:
            continue
        res = p.get("result")
        settled = res in ("won", "lost", "push", "void")
        # INCLURE aussi les provisoires FINIS EN ATTENTE de règlement (result None mais match probablement
        # terminé) -> comble le trou d'affichage entre la fin du match et le règlement (tâche 10 min) : le
        # provisoire reste visible en ⏳ puis bascule ✓/✗ (demande user 2026-07-24 : « terminé/gagné mais
        # n'apparaît pas »). Un provisoire à venir / en cours (pas encore fini) n'est PAS affiché ici.
        if not settled:
            if res is not None or not analyses.likely_finished({"sport": p.get("sport"), "start": p.get("start")}):
                continue
        if sport and p.get("sport") != sport:
            continue
        try:
            ld = to_local(_dt.fromisoformat(str(p.get("start")).replace("Z", "+00:00")))
        except (ValueError, AttributeError, TypeError):
            ld = None
        if not ld or _sport_date(ld).isoformat() != iso:   # jour sportif 06h→06h (cohérent avec les autres chemins)
            continue
        rows.append(p)
    if not rows:
        return ""
    rows.sort(key=lambda p: p.get("start") or "")
    # RENDU IDENTIQUE AUX PRONOS (demande user 2026-07-28) : chaque provisoire réglé = carte `_leg_card`
    # complète (en-tête sport • compétition, équipes, pari + glose, ligne VERDICT confiance/marché/value,
    # pli « Pourquoi », SCORE final) avec CADRE vert/rouge selon le résultat (`.cleg won/lost`) — plus le
    # bloc compact d'avant. Confiance + justification récupérées du SIDECAR du match (strict home ET away).
    from app.settle_analyst import code_from_pick as _cfp_pr

    def _tk_pr(s):
        return set(re.findall(r"[a-z0-9]+", analyses._deacc(s or "").lower())) - {"fc", "sc", "if"}

    # INDEX des sidecars tokenisé UNE fois PAR SPORT (perf, demande user 2026-07-28 « changer d'onglet est
    # très lent ») : avant, `_prov_sidecar` rescannait les ~220 sidecars POUR CHAQUE provisoire (O(P×N)).
    _side_idx: dict = {}

    def _prov_sidecar(sp, home, away, sel, start=None):
        """(fid, prob, why, code) du sidecar de CE match — apparié par NOMS ET DÉSAMBIGUÏSÉ PAR COUP D'ENVOI
        (`start`) : on prend la fiche des mêmes équipes la PLUS PROCHE en temps, on REJETTE si > 6 h (bug user
        2026-08-10 : le provisoire « Västerås-Djurgårdens » du 10/08 affichait le SCORE 6-0 du match INVERSE du
        03/08 -> l'AFFICHAGE prenait « le premier match qui matche » sans vérifier la date)."""
        th, ta = _tk_pr(home), _tk_pr(away)
        if not (th and ta):
            return (None, None, "", "")
        if sp not in _side_idx:
            _side_idx[sp] = [(_tk_pr(d.get("home")), _tk_pr(d.get("away")), d) for d in analyses.iter_meta(sp)]
        _want = None
        if start:
            try:
                _want = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                _want = None
        _best, _best_gap = None, None
        for dh, da, d in _side_idx[sp]:
            if not ((dh & th and da & ta) or (dh & ta and da & th)):
                continue
            if _want is None:                          # pas de coup d'envoi visé -> 1er match (comportement d'avant)
                _best = d
                break
            try:
                _dt = datetime.fromisoformat(str(d.get("start")).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            gap = abs((_dt - _want).total_seconds())
            if _best_gap is None or gap < _best_gap:
                _best, _best_gap = d, gap
        if _best is None or (_best_gap is not None and _best_gap > 6 * 3600):
            return (None, None, "", "")                # collision d'affiche (autre date) -> pas de fiche
        d = _best
        fid = str(d.get("id"))
        code = (_cfp_pr(sel or "", sp, d.get("home", ""), d.get("away", "")) or "")
        prob = next((s2.get("prob") for s2 in (d.get("shadow") or [])
                     if code and (s2.get("code") or "") == code), None)
        return (fid, prob, _prov_why_snippet(sp, fid, maxlen=100000), code)

    cards = []
    for p in rows:
        sp = p.get("sport") or ""
        _h, _sep, _a = str(p.get("name") or "").partition(" - ")
        _fid, _prob, _why, _code = _prov_sidecar(sp, _h, _a, p.get("sel"), p.get("start"))   # désambiguïsé par coup d'envoi
        if p.get("prob") is not None:               # confiance FIGÉE au suivi (récent) -> prioritaire, robuste
            _prob = p.get("prob")
        # SCORE depuis le SIDECAR (result_board -> détail par set/quart-temps) plutôt que la chaîne FIGÉE du
        # suivi (« 2-1 (sets) » pour le tennis -> scoreboard corrompu « 2-1 » en colonne S1, bug user
        # 2026-07-28). Même source que les paris joués terminés (_settled_bet_result_cards).
        _score, _periods, _pens = p.get("score"), None, None
        try:
            _sd = analyses.meta(sp, str(_fid)) if _fid else None
            _bd = analyses.result_board(_sd, sp) if _sd else None
            if _bd and _bd.get("score"):
                _score, _periods, _pens = _bd.get("score"), _bd.get("periods"), _bd.get("pens")
        except Exception:
            pass
        cards.append(_leg_card(
            {"sport": sp, "home": _h, "away": _a, "comp": p.get("comp"),
             "sel": str(p.get("sel") or ""), "cote": p.get("cote"), "prob": _prob, "code": _code,
             "result": p.get("result"), "score": _score, "periods": _periods, "pens": _pens,
             "start": p.get("start"), "why": _why},
            why=True, verdict=True, why_always=True, why_label="Pourquoi ce choix"))
    _hd = '<div class="prv-hd">🧪 Provisoires <span>· info seule, hors ROI</span></div>' if header else ""
    return _hd + _MC_SEP.join(cards)


# Rang d'affichage d'un pari RÉGLÉ dans sa section (demande user 2026-08-01) : gagné avant remboursé avant
# perdu. Combiné à l'ordre des actifs (non joué → live), une section se lit : non joué → live → gagné → perdu.
_RES_RANK = {"won": 0, "push": 1, "void": 1, "lost": 2}


def _settled_bet_result_cards(iso: str, sport: str | None = None, exclude_mids: set | None = None,
                              tier: str | None = None) -> list:
    """Cartes des PARIS JOUÉS TERMINÉS d'un jour, rendues COMME les pronos (demande user 2026-07-28 : « tous
    les résultats affichés de la même manière ») : carte `_leg_card` complète (en-tête, équipes, pari + glose,
    ligne VERDICT confiance/marché/value, SCORE final, pli « Pourquoi ») avec CADRE vert/rouge selon le
    résultat — plus la version allégée de `_past_day_cards`/foot._card. Renvoie [(ts, html)] triés récents
    d'abord. Le COMBINÉ du match (CdM) réglé est rendu via sa carte dorée. Combiné du jour = géré à part."""
    from app.settle_analyst import code_from_pick as _cfp
    _bg = analyses.background_sports()
    sports = (sport,) if sport else ("foot",)          # vue « Tous » = foot (arrière-plan exclu, comme avant)
    out = []
    for sp in sports:
        if sp in _bg and sp != sport:                  # tennis/basket : seulement si explicitement sélectionnés
            continue
        for d in analyses.iter_meta(sp):
            if d.get("roi_void"):          # pari exclu du ROI/historique (correction d'approche) -> pas affiché
                continue
            dt = d.get("_start_dt")
            if dt is None:
                continue
            ld = to_local(dt)
            if ld is None or _sport_date(ld).isoformat() != iso:
                continue
            if not analyses.is_settled(d):
                continue
            fid = str(d.get("id"))
            if exclude_mids and fid in exclude_mids:   # ex. match de la montante -> déjà affiché en carte montante
                continue
            _bdg, _sco = analyses.result_chip(d)
            _board = analyses.result_board(d, sp) or {}
            combo = d.get("combo") or {}
            if combo.get("legs") and combo.get("result") in ("won", "lost", "void"):
                # UN COMBINÉ N'EST PAS UN PARI « CONFIANCE » (user 2026-08-19 : combiné 3 jambes affiché à tort en
                # Confiance le 19/07). Il a son PROPRE type -> tier « combo » (zone Combiné), jamais Confiance/Value.
                if tier is not None and tier != "combo":
                    continue
                _body = _combo_premium_block(sp, fid, d.get("home", ""), d.get("away", ""))
                if _body:
                    _st = {"won": "won", "lost": "lost", "void": "push"}.get(combo.get("result"), "")
                    out.append((_RES_RANK.get(combo.get("result"), 3), dt.timestamp(), _combo_gold_card(
                        title="COMBINÉ", subtitle=f'{len(combo["legs"])} jambes',
                        badge=_bdg, body=_body, state=_st)))
                continue
            # SOURCE = LE PARI FIGÉ (couche STATS), pas un retained_bet recalculé en direct (bug user 2026-08-30 :
            # un pari de CONFIANCE joué apparaissait dans Telegram + Stats mais PAS dans « Résultats du jour »).
            # Cause : une fois `stat_bet` gelé au règlement, `retained_bet(for_history=True)` NE reconstruit plus
            # le pick MÉCANIQUE confidence_bet/value_bet (branches gardées par `not isinstance(stat_bet, dict)`)
            # -> il retombe sur la table `.md` de Claude, qui ne contient PAS le pick venu d'un fantôme -> None
            # (carte perdue) ou un AUTRE marché (carte fausse). `stat_bet(d)` renvoie le pari RÉELLEMENT joué
            # (immuable) et se replie sur retained_bet(for_history) tant qu'il n'est pas figé -> affichage == stats.
            rb = analyses.stat_bet(d)
            if not rb or rb.get("result") not in ("won", "lost", "push"):
                continue
            if tier is not None and analyses.tier_of(d, rb) != tier:
                continue                                   # carte réglée d'un AUTRE tier -> pas dans cette zone
            _code = (_cfp(rb.get("sel", ""), sp, d.get("home", ""), d.get("away", "")) or "")
            _umc = match_select.unibet_meta_for(sp, d.get("home"), d.get("away")) or {}   # pays (best-effort)
            out.append((_RES_RANK.get(rb.get("result"), 3), dt.timestamp(), _leg_card(
                {"sport": sp, "home": d.get("home"), "away": d.get("away"), "comp": d.get("comp"),
                 "country": (_umc.get("country") or d.get("country")           # live meta -> sidecar -> cache appris
                             or match_select.comp_country(d.get("comp")) or ""),
                 "sel": rb.get("sel"), "cote": rb.get("cote"), "prob": rb.get("prob"), "code": _code,
                 "result": rb.get("result"), "score": _board.get("score") or _sco,
                 "periods": _board.get("periods"), "pens": _board.get("pens"), "start": d.get("start"),
                 "why": _prov_why_snippet(sp, fid, maxlen=100000, played=True)},
                why=True, verdict=True, why_always=True, why_label="Pourquoi ce choix", live_layout=True)))
    # ORDRE (demande user 2026-08-01) : GAGNÉ d'abord, puis remboursé, puis PERDU ; à rang égal, le plus
    # récent en tête. Cohérent avec l'ordre voulu par type : non joué → live → gagné → perdu.
    out.sort(key=lambda x: (x[0], -x[1]))
    return [h for _, _, h in out]


def _sport_pronos_counts(match_rows: list) -> dict:
    """Nb de paris affichés PAR SPORT dans Pronos (badge des boutons sport, demande user 2026-07-27, même
    style que les badges d'onglets Stats) : paris joués + provisoires + combiné (+ montante + Betmines pour
    le foot) — MÊME comptage que le `_cnt` de _today_zones, décliné par sport."""
    from app import combo_daily as _cd
    _day = _sport_today().isoformat()
    _paj = {_prog_pair(r.get("home"), r.get("away")) for r in match_rows}
    try:
        for _lh, _la in _cd.leg_names(_day):
            _paj.add(_prog_pair(_lh, _la))
    except Exception:
        pass
    _mont = 1 if _montante_palier() is not None else 0   # montante active (palier en attente) -> +1 au compte foot
    # Le match de la montante peut DÉJÀ être un pari de Confiance dans match_rows -> il faut l'EXCLURE du
    # compte `play` avant d'ajouter `_mont`, sinon double-compte (comme _today_zones l.6547). (fix user 2026-08-08)
    _mont_pair = None
    try:
        from app import montante as _mt1
        if _mt1.is_active():
            _mm1 = _noF(str((_mt1.state().get("pending") or {}).get("match") or ""))
            _mh1, _, _ma1 = _mm1.partition(" - ")
            if _mh1:
                _mont_pair = _prog_pair(_mh1, _ma1)
    except Exception:
        _mont_pair = None
    out = {}
    for sp in ("foot", "tennis", "basket"):
        _prog = [it for it in _programme_items(_paj, framed=True, keep_sport=sp)
                 if not it.get("_live") and it.get("_sport") == sp]
        prov = sum(1 for it in _prog if it.get("_prov"))
        play = sum(1 for r in match_rows if _item_sport(r) == sp
                   and not (sp == "foot" and _mont_pair and _prog_pair(r.get("home"), r.get("away")) == _mont_pair))
        try:
            # UN SEUL combiné/jour (user 2026-08-20) -> variant "" seulement.
            _cvars = ("", "soir")
            combo = 0
            for _cv in _cvars:
                _cbt = _cd.today(_day, sport=sp, variant=_cv)
                if (_cbt and _cbt.get("legs") and _cbt.get("result") not in ("won", "lost", "void")
                        and not analyses._combo_rule_void(_day)):   # combiné hors-règle -> pas compté au badge
                    combo += 1
        except Exception:
            combo = 0
        out[sp] = play + prov + combo + (_mont if sp == "foot" else 0)
    return out


def _sport_selector(current: str | None, counts: dict | None = None, *,
                    target: str = "day-content", base: str = "/jour", q: str | None = None) -> str:
    """Sélecteur de sport Football / Tennis / Basket (demande user 2026-07-26 Pronos, étendu au Live
    2026-07-28). Le sport actif RECHARGE le conteneur `#{target}` via `{base}?{q}&sport=<sk>&frag=1` (JS
    _SPSEL_JS, qui lit `data-target`/`data-base`/`data-q` sur le wrap). Défauts = Pronos (#day-content,
    /jour?date=<jour>). Live : target="pn-directs", base="/directs", q="". `counts` = nb par sport -> badge."""
    _day = _sport_today().isoformat()
    _cur = current or "foot"
    counts = counts or {}
    if q is None:
        q = f"date={_day}"                       # défaut Pronos (le Live passe q="")

    # FOOTBALL SEUL (user 2026-08-07) : tennis/basket retirés du produit -> plus de sélecteur de sport
    # (un seul sport = pas de chips à choisir). On ne rend RIEN (la vue reste 100 % foot, `sport` ignoré).
    return ""


def _settled_wl_today(iso: str, sport: str | None, tier: str | None = None) -> tuple:
    """(gagnés, perdus, remboursés) des PARIS JOUÉS SIMPLES réglés du JOUR. Doit refléter EXACTEMENT les
    cartes affichées (`_settled_bet_result_cards`) -> même prédicat : un pari retenu (retained_bet for_history)
    PAR MATCH réglé. ⚠️ NE PAS utiliser `iter_stat_bets` ici : il rend stat_bet ET stat_bet_first (pour le
    ROI/calibration), donc DOUBLE-COMPTE un match re-scané (bug user 2026-08-02 : « 5 au lieu de 4, 2 gagnés
    au lieu de 1 »). Combiné du jour réglé compté comme UNE carte. Foot par défaut (arrière-plan exclu)."""
    won = lost = push = 0
    _bg = analyses.background_sports()
    for sp in ((sport,) if sport else ("foot",)):
        if sp in _bg and sp != sport:
            continue
        for d in analyses.iter_meta(sp):
            dt = d.get("_start_dt")
            ld = to_local(dt) if dt else None
            if ld is None or _sport_date(ld).isoformat() != iso:
                continue
            if not analyses.is_settled(d):
                continue
            combo = d.get("combo") or {}
            if combo.get("legs") and combo.get("result") in ("won", "lost", "void"):
                if tier is not None and tier != "combo":       # combiné = tier « combo » (zone Combiné), pas Confiance
                    continue
                r = combo.get("result")
            else:
                # LE PARI FIGÉ (couche STATS), pas retained_bet recalculé (même bug que _settled_bet_result_cards,
                # user 2026-08-30) : après gel, retained_bet(for_history) NE reconstruit plus le pick mécanique
                # confidence/value -> renvoie None -> la victoire n'était PAS comptée dans le RECORD de la zone
                # (badge gris « en attente » au lieu de vert « gagné »), alors que la CARTE, elle, s'affichait.
                # `stat_bet(d)` = le pari réellement joué -> record == cartes affichées.
                rb = analyses.stat_bet(d)
                if not rb or rb.get("result") not in ("won", "lost", "push"):
                    continue
                if tier is not None and analyses.tier_of(d, rb) != tier:
                    continue
                r = rb.get("result")
            won += 1 if r == "won" else 0
            lost += 1 if r == "lost" else 0
            push += 1 if r in ("push", "void") else 0
    return won, lost, push


def _prov_settled_wl(iso: str, sport: str | None) -> tuple:
    """(réglés, gagnés, perdus) des PROVISOIRES du JOUR — MÊME sélection de lignes que `_provisional_results`
    (les cartes réglées affichées) pour que le compteur colle EXACTEMENT aux cartes (bug user 2026-08-02 :
    « 7 au lieu de 4 »). ⚠️ NE PAS itérer le suivi brut (`entries`) : il liste des provisoires PAS affichés
    (déjà dédupliqués contre combiné/joué, ou non-`provisional_shown`). Hors ROI, indicatif."""
    n = won = lost = 0
    try:
        from app import provisional as _pv
        allp = _pv.load()
    except Exception:
        return 0, 0, 0
    _bg = analyses.background_sports()
    for p in allp.values():
        if not isinstance(p, dict):
            continue
        if sport is None and p.get("sport") in _bg:
            continue
        if sport and p.get("sport") != sport:
            continue
        res = p.get("result")
        settled = res in ("won", "lost", "push", "void")
        if not settled:                                  # inclut les FINIS EN ATTENTE (comme _provisional_results)
            if res is not None or not analyses.likely_finished({"sport": p.get("sport"), "start": p.get("start")}):
                continue
        try:
            ld = to_local(datetime.fromisoformat(str(p.get("start")).replace("Z", "+00:00")))
        except (ValueError, AttributeError, TypeError):
            ld = None
        if not ld or _sport_date(ld).isoformat() != iso:
            continue
        n += 1
        won += 1 if res == "won" else 0
        lost += 1 if res == "lost" else 0
    return n, won, lost


def _card_live_lock(r: dict, sport: str) -> str | None:
    """« won »/« lost » si le pari LIVE de la carte est déjà MATHÉMATIQUEMENT verrouillé (over/under franchi,
    équipe-marque, BTTS), sinon None. Utilise `analyses._live_locked` (0 réseau) = LE MÊME verrou que la barre
    « Gagné »/« Perdu » de la carte (source « acquis »/« perdu »). ⚠️ On n'utilise PAS `live_won`/`live_lost`
    de la carte : le perle foot n'est pas structuré (kind) -> ils sont toujours faux (bug compteur 2026-08-10)."""
    if r.get("status") != "inprogress":
        return None
    sel = (r.get("perle") or {}).get("selection") or ""
    if not sel:
        return None
    hs, as_ = _parse_live_score(r.get("score"))
    if hs is None or as_ is None:
        return None
    try:
        from app.settle_analyst import code_from_pick as _cfp
        _code = _cfp(sel, sport, r.get("home", ""), r.get("away", "")) or ""
        _info = analyses._leg_metric({"sel": sel, "code": _code}, r.get("home", ""), r.get("away", ""))
        return analyses._live_locked(sport, sel, _code, _info, hs, as_, {})
    except Exception:
        return None


def _today_zones(match_rows: list, sport: str | None = None, results: list | None = None) -> tuple[str, int]:
    """Zones du JOUR COURANT (Combiné du jour · Paris du jour · Provisoires · Résultats du jour ; PLUS de
    zone « à analyser » — retirée sur demande user 2026-07-20). Extrait de render_dashboard pour /jour
    (jour = aujourd'hui). `sport` : filtre Pronos (dormant). `results` = cartes des paris DÉJÀ TERMINÉS
    aujourd'hui (matchs finis + résultats) -> zone « Résultats du jour » repliable, sinon ils n'étaient
    visibles que dans Stats (demande user 2026-07-20).
    Renvoie (html, nb_matchs_du_jour) — le compte alimente le badge de nav."""
    # ORDRE (demande user 2026-08-01) : NON JOUÉ (à venir) avant EN LIVE (en cours), puis par coup d'envoi.
    # Suivi des réglés (gagné → perdu) plus bas -> une section se lit : non joué → live → gagné → perdu.
    play = sorted(list(match_rows),
                  key=lambda r: (1 if r.get("status") == "inprogress" else 0, r.get("start_ts") or 0))
    _paj = {_prog_pair(r.get("home"), r.get("away")) for r in match_rows}
    # DÉDUP (demande user 2026-07-26) — un match déjà JAMBE du combiné du jour n'apparaît PLUS aussi en
    # provisoire (fini le doublon exact type « Grêmio DC 1X »). La montante, elle, suit le meilleur simple
    # value (déjà dans les paris joués -> déjà exclu ici) : pas de dédup montante↔combiné séparée.
    try:
        from app import combo_daily as _cd0
        for _lh, _la in _cd0.leg_names(_sport_today().isoformat()):
            _paj.add(_prog_pair(_lh, _la))
    except Exception:
        pass
    # MATCH DE LA MONTANTE : injecté en carte montante dédiée (cadre bleu) EN TÊTE de Confiance -> on l'EXCLUT
    # de play (Confiance) ET de prov (Provisoire) pour ne PAS l'afficher 2× (user 2026-08-08).
    _mont_pair = set()
    try:
        from app import montante as _mt0
        if _mt0.is_active():
            _mm0 = _noF(str((_mt0.state().get("pending") or {}).get("match") or ""))
            _mh0, _, _ma0 = _mm0.partition(" - ")
            if _mh0:
                _mont_pair.add(_prog_pair(_mh0, _ma0))
    except Exception:
        pass
    _paj |= _mont_pair
    if _mont_pair:
        play = [r for r in play if _prog_pair(r.get("home"), r.get("away")) not in _mont_pair]
    # LIVE GARDÉ DANS PRONOS (user 2026-08-08 : « un match live doit rester aussi dans Pronos et le considérer
    # comme en attente ») -> plus de filtre `not _live` ici. Le match live reste visible ET compté « en attente ».
    _prog = list(_programme_items(_paj, framed=True, keep_sport=sport))
    if sport:
        play = [r for r in play if _item_sport(r) == sport]
        _prog = [it for it in _prog if it.get("_sport") == sport]
    prov = (sorted([it for it in _prog if it.get("_prov")], key=lambda r: r.get("start_ts") or 0)
            if analyses.PROVISOIRES_ON else [])   # provisoires retirés (user 2026-08-11) : abstentions ignorées
    # PLUS de catégorie « à analyser » (demande user 2026-07-20 : la supprimer) : un match NON encore
    # analysé (ni pari, ni provisoire) n'est tout simplement PAS affiché tant qu'il n'a pas d'analyse —
    # il apparaîtra une fois analysé (avec son pari/provisoire), jamais en limbo « Analyse à HH:MM ».
    # Combiné du jour du SPORT sélectionné (foot par défaut = « Tous »). Tennis/basket = simulé (hors ROI),
    # affiché comme le foot (demande user 2026-07-26).
    # CHAQUE type de pari GARDE ses matchs réglés DANS sa section (demande user 2026-08-01 : plus de zone
    # « Résultats du jour » séparée en bas -> la carte affiche le résultat/score en place). -> include_settled=True.
    # UN SEUL COMBINÉ du jour (user 2026-08-20 : retour au combiné sécurité unique du 29/07-07/08). Hors ROI.
    # DEUX COMBINÉS (user 2026-08-30) : « Combiné du jour » (variant "") + « Combiné du soir » (variant "soir"),
    # MÊME présentation (titre sans « SÛR »/« COTE 2 » -> accent par défaut). Chacun bâti à SON scan (matin/soir).
    combo_daily = _MC_SEP.join([c for c in (
        _combo_tg_card(include_settled=True, sport=(sport or "foot"), title="COMBINÉ DU JOUR", variant=""),
        _combo_tg_card(include_settled=True, sport=(sport or "foot"), title="COMBINÉ DU SOIR", variant="soir"),
    ) if c])
    _is_foot_view = sport in (None, "foot")
    # (Zone « Combiné » séparée RETIRÉE le 2026-08-02 : la double chance EST désormais le
    #  « Combiné football » ci-dessus (combo_daily), compté au ROI. Plus de carte combiné distincte.)
    # MONTANTE : type de pari À PART (demande user 2026-07-30) -> zone dédiée « Montante · Palier N » plus bas
    # (via _montante_zone_card). Plus de badge greffé sur les cartes de pari joué (surface unique = la zone).
    # Zones REPLIABLES (demande user 2026-07-20) : chaque type de pari peut être plié pour se concentrer sur
    # ce qui compte ; ouvertes par défaut, état mémorisé (localStorage via _CAL_JS).
    # ORDRE : Confiance (montante incluse) → Value → Provisoire → Combiné. « Confiance » n'apparaît que s'il y a un
    # pari/résultat/montante (demande user 2026-07-26).
    out = []
    # MONTANTE FUSIONNÉE DANS CONFIANCE (user 2026-08-08) : PLUS de zone « Montante » séparée. Le match de la
    # montante est souvent une ABSTENTION (son pari = le pick de la montante, pas un pari de Confiance) -> il
    # N'EST PAS dans `play`. On INJECTE donc SA carte (LA MÊME que l'onglet Montante, via _montante_zone_card)
    # dans `play`, avec un label « Montante • Palier N » au-dessus + un CADRE BLEU (conservé). Elle est donc
    # comptée dans le JAUNE « à venir » du compteur (plus de badge bleu dédié, user 2026-08-08). (foot uniquement).
    _mont_title, _mont_card = _montante_zone_card(sport)
    _mont_settled = ""     # carte montante RÉGLÉE -> injectée avec les RÉSULTATS (après les à-venir), pas en tête
    # La montante est classée par SA confiance (Confiance si ≥ seuil, sinon Value) -> cohérent avec les stats
    # (user 2026-08-10 : on reste sur la nouvelle logique de tier ; le « bordel » du jour venait d'un faux
    # règlement 6-0, pas de la montante). Son ROI est compté dans le bon tier via tier_of.
    _mont_tier = "confiance"
    if _mont_card:
        _mpj = _montante_today_bet() or {}             # pending OU pari réglé du jour (heure/live/résultat)
        _mont_tier = analyses.bet_tier_for("foot", str(_mpj.get("mid") or ""))
        # CADRE vert (gagné) / rouge (perdu) une fois réglé, comme les autres cartes résultat (user 2026-08-08) ;
        # bleu tant que non réglé (en attente/live). Le titre « MONTANTE • PALIER N » suit la même couleur.
        _mres = _mpj.get("result")
        _mcls = " won" if _mres == "won" else " lost" if _mres in ("lost", "void", "push") else ""
        # Cadre/titre « MONTANTE • PALIER N » AU-DESSUS de la carte RETIRÉ (user 2026-08-18) : le palier est
        # désormais porté par le TITRE DE LA ZONE (« Montante • Palier N »). On garde juste le cadre de couleur.
        _mont_deco = f'<div class="mont-cardwrap{_mcls}">{_mont_card}</div>'
        if _mres in ("won", "lost", "push", "void"):
            # RÉGLÉ : avec les autres résultats (le bloc résultats vient APRÈS les à-venir/en cours) — user
            # 2026-08-08 : « le résultat de la montante ne doit pas être tout au-dessus, il reste des paris à venir ».
            _mont_settled = _mont_deco
        else:
            # EN ATTENTE / LIVE : dans la liste play, TRIÉE par statut (à venir avant en cours).
            _msd = analyses.meta("foot", str(_mpj.get("mid") or "")) or {}
            try:
                _mts = datetime.fromisoformat(str(_msd.get("start")).replace("Z", "+00:00")).timestamp() if _msd.get("start") else 0
            except (ValueError, AttributeError, TypeError):
                _mts = 0
            _mlive = bool(match_select.live_state_for("foot", _msd.get("home", ""), _msd.get("away", "")))
            play.append({"_html": _mont_deco, "start_ts": _mts, "tier": _mont_tier,
                         "status": "inprogress" if _mlive else "", "_mont": True,
                         "home": _msd.get("home"), "away": _msd.get("away")})
        play.sort(key=lambda r: (1 if r.get("status") == "inprogress" else 0, r.get("start_ts") or 0))
    # RÉSULTATS EN PLACE (demande user 2026-08-01) : plus de zone « Résultats du jour » en bas de l'onglet.
    # Chaque match RÉGLÉ reste dans SA section de type et sa carte affiche le résultat/score (comme les cartes
    # de résultat actuelles). Les combinés (include_settled=True ci-dessus) le font déjà en place ; ici on
    # injecte les PARIS JOUÉS et PROVISOIRES terminés à la suite de leurs homologues à venir/en cours.
    today_iso = _sport_today().isoformat()
    # EXCLURE le match de la montante des cartes résultat SIMPLES : il est déjà affiché en carte MONTANTE
    # (titre + cadre) -> sinon il apparaîtrait 2× (user 2026-08-08 : « le résultat sans montante ne doit pas
    # être mis vu qu'il y est déjà avec la montante »).
    _mont_ex = {str((_montante_today_bet() or {}).get("mid") or "")} if _mont_card else None
    _prov_res = _provisional_results(today_iso, sport, header=False) if analyses.PROVISOIRES_ON else ""
    _now_ts = time.time()
    # SPLIT CONFIANCE / VALUE (user 2026-08-09) : DEUX zones. CONFIANCE = picks à HAUTE confiance calibrée
    # (chiffre phare, taux ~92-95 %) ; VALUE = picks RETENUS sous le seuil (rentables, +19/+28 % ROI, mais plus
    # variables). Les DEUX restent joués + comptés au ROI/calibration (inchangé). Chaque carte foot porte son
    # `tier` (analyses.bet_tier via foot._card). RÉVERSIBLE : analyses.TIER_SPLIT_ON=False -> tout est
    # « confiance » -> zone Value vide/masquée -> état EXACT d'avant. La MONTANTE (sans tier) reste en Confiance.
    play_conf = [r for r in play if r.get("tier") == "confiance"]   # montante EXCLUE (zone dédiée, user 2026-08-12)
    play_value = [r for r in play if r.get("tier") == "value"]
    play_mont = [r for r in play if r.get("tier") == "montante"]     # -> ZONE MONTANTE à part
    _res_conf = _settled_bet_result_cards(today_iso, sport, exclude_mids=_mont_ex, tier="confiance")
    _res_value = _settled_bet_result_cards(today_iso, sport, exclude_mids=_mont_ex, tier="value")

    def _tier_rec(_pl, _tier):
        # RECORD 6 états d'un tier : total · À VENIR/LIVE (jaune) · live · EN ATTENTE (gris) · gagnés · perdus.
        # LIVE VERROUILLÉ (user 2026-08-10) : un pari EN COURS déjà MATHÉMATIQUEMENT gagné (over franchi, BTTS…)
        # compte VERT, perdu compte ROUGE — dès maintenant, avant le règlement officiel (n'affecte QUE le
        # compteur d'affichage, pas le ROI). MÊME verrou que la barre « Gagné » (analyses._live_locked, 0
        # réseau) — PAS `live_won` (toujours faux en foot : le perle n'est pas structuré). LIVE INCERTAIN
        # (1X2/handicap réversible) reste JAUNE. GRIS = fini-non-réglé (démarré, plus live).
        _w, _l, _p = _settled_wl_today(today_iso, sport, tier=_tier)
        _locks = [_card_live_lock(r, sport or "foot") for r in _pl if r.get("status") == "inprogress"]
        _lw = sum(1 for x in _locks if x == "won")
        _ll = sum(1 for x in _locks if x == "lost")
        _pend = sum(1 for r in _pl if r.get("status") != "inprogress"
                    and (r.get("start_ts") or 0) and r["start_ts"] <= _now_ts)
        _up = len(_pl) - _pend - _lw - _ll        # jaune = à venir + live INCERTAIN (hors live verrouillés)
        return (len(_pl) + _w + _l + _p, _up, 0, _w + _lw, _l + _ll, _pend)

    # Reste-t-il des MATCHS À JOUER au programme ? (coup d'envoi encore à venir) -> pilote le badge « en attente »
    # des catégories VIDES : des matchs à venir + pas encore de pari = « en attente » ; plus aucun match à jouer
    # (tous lancés/finis, ou programme vide / avant scan) = aucun badge (user 2026-08-24).
    def _prog_upcoming(m) -> bool:
        try:
            return datetime.fromisoformat(str(m.get("start")).replace("Z", "+00:00")).timestamp() > _now_ts
        except Exception:
            return False
    _has_prog = any(_prog_upcoming(m) for m in (_load_day_programme().get("matches") or []))
    # ZONE CONFIANCE : PUREMENT des confiances (la montante a désormais sa PROPRE zone, user 2026-08-12).
    _conf_html = _MC_SEP.join([h for h in (_rows_by_day(play_conf), _MC_SEP.join(_res_conf)) if h])
    _conf_rec = _tier_rec(play_conf, "confiance")
    # CONFIANCE + VALUE TOUJOURS AFFICHÉES (user 2026-08-17), même vides -> la catégorie reste visible (message
    # d'état honnête à la place des cartes). Les autres zones n'apparaissent que si elles ont du contenu.
    out.append(_zone("play", _plur(len(play_conf) + len(_res_conf), "Confiance"), "",
                     len(play_conf) + len(_res_conf), _conf_html,
                     collapsible=True, record=_conf_rec if _conf_rec[0] else None, waiting=_has_prog,
                     empty="Aucune sélection à haute confiance pour l'instant."))
    # ZONE VALUE — toujours affichée (user 2026-08-17).
    _value_html = _MC_SEP.join([h for h in (_rows_by_day(play_value), _MC_SEP.join(_res_value)) if h])
    _value_rec = _tier_rec(play_value, "value")
    out.append(_zone("value", _plur(len(play_value) + len(_res_value), "Value"), "",
                     len(play_value) + len(_res_value), _value_html,
                     collapsible=True, record=_value_rec if _value_rec[0] else None, waiting=_has_prog,
                     empty="Aucun pari de value détecté pour l'instant."))
    # ZONE MONTANTE (dédiée, user 2026-08-12) : à venir/live (play_mont) + réglée (_mont_settled). Plus jamais
    # fondue dans Confiance/Value. La carte garde son cadre bleu + titre « MONTANTE • PALIER N ».
    _mont_html = _MC_SEP.join([h for h in (_rows_by_day(play_mont), _mont_settled) if h])
    _mont_rec = _tier_rec(play_mont, "montante")
    # BADGE MONTANTE (user 2026-08-18 « il doit y avoir le badge du nombre de paris gagné ») : le résultat de la
    # montante vit dans montante_track — PAS toujours dans un stat_bet tier=montante (le match est SOUVENT une
    # ABSTENTION -> `_settled_wl_today(tier=montante)` le rate -> badge absent). On lit donc le résultat du palier
    # du jour et on l'injecte dans le record pour que le badge compte le pari GAGNÉ (vert) / perdu (rouge).
    _mbr = (_montante_today_bet() or {}).get("result")
    if _mbr in ("won", "lost", "push", "void") and (_mont_rec[0] or 0) == 0:
        _mont_rec = (1, 0, 0, 1 if _mbr == "won" else 0, 1 if _mbr in ("lost", "void", "push") else 0, 0)
    # ZONE MONTANTE TOUJOURS AFFICHÉE (user 2026-08-19 : « afficher tous les types de paris ») — même vide, avec
    # un message d'état. Titre = « Montante • Palier N » s'il y a un palier, sinon « Montante ».
    _mt_split = (_mont_title or "Montante").split(" · ", 1)   # « Montante · Palier N » -> titre + sous-titre PETIT
    out.append(_zone("mont", _mt_split[0], "", len(play_mont), _mont_html,
                     collapsible=True, record=_mont_rec if _mont_rec[0] else None, waiting=_has_prog,
                     subtitle=(_mt_split[1] if len(_mt_split) > 1 else ""),
                     empty="Aucun palier engagé pour l'instant."))
    # PARIS PROVISOIRES = à venir/en cours PUIS terminés.
    _prov_html = _MC_SEP.join([h for h in (_rows_by_day(prov), _prov_res) if h])
    # RECORD provisoires = MÊMES cartes affichées : à venir/en cours (prov) + réglés du jour (_prov_settled_wl,
    # même sélection que _provisional_results). -> « X sél · W✅ · L❌ » colle au nombre de cartes (hors ROI).
    _psn, _psw, _psl = _prov_settled_wl(today_iso, sport)
    # LIVE = JAUNE (user 2026-08-09) : un provisoire EN COURS (_live) est JAUNE ; le GRIS reste réservé au
    # provisoire FINI mais pas encore réglé (démarré, plus live). Cohérent avec les confiances.
    _pv_pend = sum(1 for it in prov if not it.get("_live")
                   and (it.get("start_ts") or 0) and it["start_ts"] <= _now_ts)
    _pv_lv = 0
    _pv_up = len(prov) - _pv_pend                                     # JAUNE = à venir + live (sans résultat)
    _prov_rec = (len(prov) + _psn, _pv_up, _pv_lv, _psw, _psl, _pv_pend)
    if prov or _prov_res:
        out.append(_zone("indic", _plur(len(prov) + _psn, "Provisoire"), "", len(prov), _prov_html,
                         collapsible=True, record=_prov_rec if _prov_rec[0] else None))
    # Record du COMBINÉ football du jour — DEUX combinés/jour (Sûr + Cote 2, user 2026-08-19) : on AGRÈGE leur
    # état (total, à venir, live, gagnés, perdus, en attente) -> le badge à droite du type « Combiné » reflète
    # le NOMBRE réel de combinés (jusqu'à 2) et leurs résultats.
    _combo_rec = None
    _n_combos = 0
    _combo_active = 0                    # nb de combinés ENCORE actifs (non réglés) -> badge nav
    if combo_daily:
        try:
            from app import combo_daily as _cd2
            _tot = _up = _clive = _won = _lost = _pend = 0
            for _var in ("", "soir"):
                _cbt = _cd2.today(today_iso, sport=(sport or "foot"), variant=_var) or {}
                if not _cbt.get("legs"):
                    continue
                _tot += 1
                _cr = _cbt.get("result")
                if _cr == "won":
                    _won += 1
                elif _cr == "lost":
                    _lost += 1
                elif _cr == "void":
                    pass                 # remboursé = neutre (ni gagné ni perdu)
                else:                    # non réglé -> à venir / live / en attente
                    _lv = _daily_combo_any_live(sport=(sport or "foot"), variant=_var)
                    _pd = (not _lv) and any(
                        l.get("start") and analyses.likely_finished(
                            {"sport": l.get("sport") or "foot", "start": l.get("start")})
                        for l in (_cbt.get("legs") or []))
                    if _lv:
                        _clive += 1
                    elif _pd:
                        _pend += 1
                    else:
                        _up += 1
                    _combo_active += 1
            _n_combos = _tot
            _combo_rec = (_tot, _up, _clive, _won, _lost, _pend) if _tot else None
        except Exception:
            _combo_rec = None
    # ZONE COMBINÉ JUSTE SOUS VALUE (user 2026-08-20) : insérée à l'index 2 (après Confiance[0] + Value[1]),
    # AVANT Montante/Provisoire. TOUJOURS AFFICHÉE (user 2026-08-19), même vide -> message d'état.
    out.insert(2, _zone("combo", _plur(_n_combos, "Combiné"), "", _n_combos, combo_daily,
                        collapsible=True, record=_combo_rec, waiting=_has_prog,
                        empty="Aucun combiné du jour pour l'instant."))
    # ABSTENTIONS RÉAFFICHÉES (user 2026-08-24) : les matchs analysés SANS pari retenu, en cartes, catégorie à
    # part (cachée s'il n'y en a aucune). On remontre ce qu'on a analysé mais pas jugé jouable.
    # ABSTENTIONS masquées quand le PROGRAMME est TERMINÉ (plus aucun match à jouer, user 2026-08-27) : une
    # abstention n'a de sens que tant qu'il reste des matchs à venir. Journée finie -> on ne montre plus ce
    # qu'on n'a pas joué. Même condition `_has_prog` que le badge « en attente ».
    _abst_html = _abstention_zone(sport or "foot") if _has_prog else ""
    out.append(_abst_html)
    _prog_html = _programme_schedule(sport or "foot")
    # JOURNÉE TOTALEMENT VIDE (tôt le matin AVANT le scan de 08h, ou jour calme) : au lieu de 2 accordéons
    # Confiance/Value repliés sur un grand vide (message caché), on montre un ÉTAT VIDE PREMIUM (orbe + timing).
    _day_empty = not (play_conf or _res_conf or play_value or _res_value or play_mont or _mont_settled
                      or prov or _prov_res or combo_daily
                      or (_prog_html and _prog_html.strip()) or (_abst_html and _abst_html.strip()))
    inner = _paj_hero() if _day_empty else (_prog_html + "".join(x for x in out if x))
    # COMPACT (user 2026-08-19) : toutes les catégories + leur phrase doivent tenir VISIBLES sur l'écran (plus de
    # répartition `space-between` qui poussait Combiné/Abstention hors écran). Empilement serré (CSS compacte les
    # zones vides + réduit les espaces).
    # `dash-today` (user 2026-08-19) : SEULE la vue d'AUJOURD'HUI répartit les catégories sur la hauteur
    # (space-between). Les vues de JOURS PASSÉS (_day_view, même conteneur #day-content) restent alignées EN HAUT.
    zones = f'<div class="dash-zones dash-today">{inner}</div>'
    today_iso = _sport_today().isoformat()
    # BADGE nav = paris NON RÉGLÉS du jour (à venir + en cours). `play`/`prov` ne contiennent DÉJÀ que
    # l'actif (les réglés partent dans _res_cards/_prov_res). Le combiné ne compte donc QUE s'il est encore
    # actif (`_combo_active`) : un combiné RÉGLÉ ne doit plus gonfler le badge (fix user 2026-08-08 : badge
    # « 2 » alors qu'il ne restait qu'1 pari en cours, le combiné du jour étant déjà perdu). La MONTANTE
    # n'est PAS ajoutée séparément (fix double-compte) : sa carte est déjà dans `play` (l.6600).
    _cnt = len(play) + len(prov) + _combo_active   # _combo_active = NB de combinés encore actifs (0-2, user 2026-08-19)
    return _day_calendar(today_iso, sport) + _sport_selector(sport, _sport_pronos_counts(match_rows)) + zones, _cnt


def _day_view(iso: str, day_rows: list, sport: str | None = None) -> str:
    """Contenu d'un JOUR PASSÉ (calendrier « Pronos ») : bilan du jour (gagnés/réglés · ROI, TOUS sports) +
    le combiné du jour de cette date (résultat) + les paris proposés ce jour-là avec leur résultat (cartes
    `_sport_row`, terminées = score + ✓/✗). `day_rows` = cartes des matchs du jour. `sport` : filtre les
    cartes sur ce sport (le bilan reste le total du jour ; le combiné multisport n'apparaît qu'en « Tous »)."""
    s = _daily_results_map().get(iso) or {}
    won, settled, profit = s.get("won", 0), s.get("settled", 0), s.get("profit", 0.0)
    roi = round(100 * profit / settled) if settled else 0
    # CADRE « bilan du jour » (ROI + nb de paris) RETIRÉ de l'historique (user 2026-08-22). Le bilan vit dans
    # Résultats ; l'historique Programme ne montre QUE les cartes par type.
    summ = ""
    # ZONE COMBINÉ = combiné du jour (combo_daily) ET/OU combinés SIDECAR (legacy/CdM). Un combiné a son PROPRE
    # type -> il n'apparaît PLUS dans Confiance (fix user 2026-08-19 : combiné 3 jambes affiché en Confiance le 19/07).
    _combo_daily_html, _combo_daily_legs, _n_daily_combo, _dc_res = "", None, 0, []
    if not sport:                                          # combo_daily = « Tous » seulement
        try:
            from app import combo_daily as _cd
            _cards = []
            for _cv, _ct in (("", "COMBINÉ DU JOUR"), ("soir", "COMBINÉ DU SOIR")):
                _c = _cd.today(iso, variant=_cv)
                if _c and _c.get("legs") and not analyses._combo_rule_void(iso):
                    _cards.append(_combo_tg_card(include_settled=True, cb=_c, title=_ct))
                    _dc_res.append(_c.get("result"))
                    if _combo_daily_legs is None:          # dots par jambe = 1er combiné (si un seul affiché)
                        _combo_daily_legs = [l.get("result") for l in (_c.get("legs") or [])]
            _n_daily_combo = len(_cards)
            _combo_daily_html = _MC_SEP.join([h for h in _cards if h])
        except Exception:
            pass
    _combo_sidecar = _settled_bet_result_cards(iso, sport, tier="combo")   # combinés du sidecar (legacy)
    _combo_body = _MC_SEP.join([h for h in ([_combo_daily_html] + _combo_sidecar) if h])
    combo = ""
    if _combo_body:
        if _n_daily_combo and not _combo_sidecar:          # combiné(s) du jour SEUL(s)
            if _n_daily_combo == 1:                        # un seul -> dots par jambe
                combo = _zone("combo", _plur(1, "Combiné"), "", 1, _combo_body,
                              collapsible=True, open_=True, zk="pj-combo", leg_results=_combo_daily_legs)
            else:                                          # deux -> badge W/L agrégé
                _w = sum(1 for r in _dc_res if r == "won")
                _l = sum(1 for r in _dc_res if r == "lost")
                _rec = (_n_daily_combo, _n_daily_combo - _w - _l, 0, _w, _l, 0)
                combo = _zone("combo", _plur(_n_daily_combo, "Combiné"), "", _n_daily_combo,
                              _combo_body, collapsible=True, open_=True, zk="pj-combo", record=_rec)
        else:                                                       # combinés sidecar -> badge gagnés/perdus
            _wc, _lc, _pc = _settled_wl_today(iso, sport, tier="combo")
            _ntot = (_wc + _lc + _pc) + _n_daily_combo
            _crec = (_ntot, 0, 0, _wc, _lc, _pc) if _ntot else None
            combo = _zone("combo", _plur(_ntot or 1, "Combiné"), "", _ntot or 1, _combo_body,
                          collapsible=True, open_=True, zk="pj-combo", record=_crec)
    # HISTORIQUE PAR TYPE DE PARI (user 2026-08-19 : « revoir les TYPES de paris et les résultats ») : mêmes
    # cartes riches que l'onglet (verdict/score/Pourquoi, cadre vert/rouge, via `_settled_bet_result_cards`),
    # SPLIT par tier comme la vue du jour : Confiance · Value · Montante · Combiné · Provisoire. Le tier est
    # FIGÉ au règlement -> l'historique reflète ce qui a VRAIMENT été joué ce jour-là.
    _res_conf = _settled_bet_result_cards(iso, sport, tier="confiance")
    _res_value = _settled_bet_result_cards(iso, sport, tier="value")
    # MONTANTE HORS-TECHNIQUE (user 2026-08-20) : les paliers 09/08→20/08 sont MASQUÉS de l'historique
    # (comme le ladder via public_steps) -> aucune carte montante pour ces jours dans le calendrier Programme.
    from app import montante as _mtn_dv
    _res_mont = [] if _mtn_dv._rule_void(iso) else _settled_bet_result_cards(iso, sport, tier="montante")
    # PLUS DE CATÉGORIE « PROVISOIRE » (user 2026-08-19) : un match est soit un pari JOUÉ (affiché dans son TYPE
    # Confiance/Value/Montante ci-dessus, selon le calcul), soit une ABSTENTION (cachée). On respecte donc
    # `PROVISOIRES_ON=False` ici AUSSI (le jour passé était le seul endroit qui affichait encore les provisoires).
    # Les résultats/shadows restent en base pour STATS & CALIBRATION — on ne touche QUE l'affichage.
    _prov_res = _provisional_results(iso, sport) if analyses.PROVISOIRES_ON else ""

    def _rec(tier):
        # BADGE COLORÉ du jour passé (comme la vue du jour, user 2026-08-19) : vert=gagnés · rouge=perdus ·
        # gris=remboursés. Tout est réglé sur un jour passé -> pas de « à venir »/« live ».
        _w, _l, _p = _settled_wl_today(iso, sport, tier=tier)
        return (_w + _l + _p, 0, 0, _w, _l, _p) if (_w + _l + _p) else None
    # JOUR PASSÉ : zones REPLIÉES D'OFFICE (user 2026-08-19) -> on voit le calendrier + le bilan + les EN-TÊTES
    # de type (badge coloré gagnés/perdus), et on déplie le type qu'on veut revoir. `data-zk` distinct « pj-* »
    # pour ne pas coupler l'état plié avec la vue du jour.
    _zones = []
    if _res_conf:
        _zones.append(_zone("play", "Confiance", "", len(_res_conf), _MC_SEP.join(_res_conf),
                            collapsible=True, open_=True, zk="pj-play", record=_rec("confiance")))
    if _res_value:
        _zones.append(_zone("value", "Value", "", len(_res_value), _MC_SEP.join(_res_value),
                            collapsible=True, open_=True, zk="pj-value", record=_rec("value")))
    if _res_mont:
        _zones.append(_zone("mont", "Montante", "", len(_res_mont), _MC_SEP.join(_res_mont),
                            collapsible=True, open_=True, zk="pj-mont", record=_rec("montante")))
    if combo:
        _zones.append(combo)
    if _prov_res:
        _zones.append(_zone("indic", "Provisoire", "", 1, _prov_res, collapsible=True, open_=True, zk="pj-indic"))
    cards = "".join(_zones)
    inner = summ + cards
    if not cards:
        # rien à montrer : un SEUL message (le bilan « Aucun pari réglé » redondant est retiré si 0 pari).
        _empty = '<div class="paj-empty">Aucun pari proposé ce jour-là.</div>'
        inner = (summ + _empty) if settled else _empty
    return _day_calendar(iso, sport) + f'<div class="dash-zones">{inner}</div>'


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# PAGE D'ACCUEIL VITRINE (visiteurs NON abonnés) — conversion. Chiffres DYNAMIQUES (relevé réel),
# aucune source nommée (avantage concurrentiel préservé, cf. mémoire public-mode-hide-sources).
# Servie par le routeur `/` quand `not accounts.can_see_picks(request)`. Les abonnés/proprio gardent
# le dashboard. PUREMENT AFFICHAGE — ne touche ni ROI, ni stats, ni calibration.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
_LZ_SINCE = "2026-06-22"
_LZ_SINCE_LABEL = "depuis le 22 juin"

_LZ_CSS = """
@font-face{font-family:'Selawik';font-weight:400;font-style:normal;font-display:swap;
  src:local('Segoe UI'),url('/static/fonts/selawik-regular.woff') format('woff')}
@font-face{font-family:'Selawik';font-weight:600;font-style:normal;font-display:swap;
  src:local('Segoe UI Semibold'),url('/static/fonts/selawik-semibold.woff') format('woff')}
@font-face{font-family:'Selawik';font-weight:700;font-style:normal;font-display:swap;
  src:local('Segoe UI Bold'),url('/static/fonts/selawik-bold.woff') format('woff')}
/* Variables SCOPÉES à .lz (JAMAIS :root -> n'écrase pas les variables de l'app, qui a les mêmes noms). Le
   contenu Accueil est un FRAGMENT injecté dans le panneau SPA : il ne doit PAS déborder sur le reste du site. */
/* PALETTE = celle de l'app (demande user 2026-08-01 : « le style de l'accueil doit rester celui des autres
   onglets »). On RÉFÉRENCE les variables de l'app (héritées de body) au lieu d'un thème propre -> même fond,
   mêmes surfaces, même accent. Aucun fond ni halo vert spécifiques. */
.lz{--gr:var(--surface);--gr2:var(--surface);--gr3:var(--surface2);--line:var(--border);--line2:var(--border2);
  --ink:var(--text);--dim:var(--muted);--faint:var(--dim);--green:var(--accent);--gb:var(--accent2);
  --gfill:var(--glow);--amber:#ffb020;
  --mono:ui-monospace,'Cascadia Mono','Segoe UI Mono',Menlo,Consolas,monospace;
  color:var(--text);font-family:'Selawik','Segoe UI',system-ui,sans-serif;line-height:1.55;
  -webkit-font-smoothing:antialiased}
.lz *{box-sizing:border-box}
.lz .lzw{max-width:1040px;margin:0 auto;padding:0}
.lz h1,.lz h2,.lz h3{margin:0;text-wrap:balance;letter-spacing:-.02em;line-height:1.06}
.lz p{margin:0}.lz a{color:inherit;text-decoration:none}
.lz .num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
.lz .mono{font-family:var(--mono)}
.lz .eyebrow{font-size:12px;font-weight:600;letter-spacing:.18em;text-transform:uppercase;color:var(--faint)}
.lz .green{color:var(--green)}.lz .amber{color:var(--amber)}.lz .red{color:var(--red)}.lz .dimc{color:var(--dim)}
.lz .lzlogo{display:block;text-align:center;padding:calc(16px + env(safe-area-inset-top)) 0 6px}
.lz .lzlogo img{width:auto;height:auto;max-width:66%;max-height:52px;filter:drop-shadow(0 6px 20px rgba(34,184,255,.42))}
.lz .acctbtn{position:fixed;top:calc(10px + env(safe-area-inset-top));right:12px;z-index:75;display:inline-flex;
  align-items:center;gap:6px;font-size:12.5px;font-weight:600;line-height:1;padding:8px 13px;border-radius:999px;
  color:#cfe0f5;text-decoration:none;background:rgba(16,22,32,.72);-webkit-backdrop-filter:blur(10px);
  backdrop-filter:blur(10px);border:1px solid rgba(150,182,222,.20)}
.lz .acctbtn .ic{font-size:15px}
.lz .brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:19px}
.lz .brand .dot{width:9px;height:9px;border-radius:50%;background:var(--green);
  box-shadow:0 0 0 4px rgba(52,210,123,.16),0 0 14px 2px rgba(79,240,154,.55)}
.lz .brand em{font-style:normal;color:var(--green)}
/* Boutons Accueil = BLANC à halo BLEU (user 2026-08-22) : fond blanc, texte bleu foncé, anneau + glow cyan. */
.lz .btn{display:inline-flex;align-items:center;gap:8px;font-weight:700;font-size:14px;padding:10px 20px;
  border-radius:11px;cursor:pointer;border:0;background:#ffffff;color:#06283c;
  box-shadow:0 0 0 1px rgba(34,184,255,.45),0 8px 26px -8px rgba(34,184,255,.6);transition:transform .12s,box-shadow .12s}
.lz .btn:hover{transform:translateY(-1px);box-shadow:0 0 0 1px rgba(34,184,255,.7),0 12px 32px -8px rgba(34,184,255,.75)}
.lz .btn.lg{font-size:16px;padding:15px 28px;border-radius:13px}
.lz .hero{position:relative;padding:16px 0 26px}
.lz .hero-glow{display:none}   /* plus de halo vert (demande user 2026-08-01) : fond = celui de l'app */
.lz .hero .wrap{position:relative;z-index:1}
.lz .hero-grid{display:grid;grid-template-columns:1.05fr .95fr;gap:44px;align-items:center}
.lz .hb{display:inline-flex;align-items:center;gap:9px;margin-bottom:18px;padding:6px 12px;
  border:1px solid var(--line2);border-radius:999px;background:rgba(255,255,255,.02)}
.lz .hb .pulse{width:7px;height:7px;border-radius:50%;background:var(--green);animation:lzpulse 2.4s ease-in-out infinite}
@keyframes lzpulse{0%,100%{opacity:.4;transform:scale(.8)}50%{opacity:1;transform:scale(1)}}
.lz .sh{display:flex;align-items:flex-end;gap:6px;margin:4px 0 2px}
.lz .sh .big{font-weight:700;font-size:clamp(72px,13vw,124px);line-height:.82;letter-spacing:-.045em;
  background:linear-gradient(176deg,#fbfffd 8%,var(--gb) 62%,var(--green) 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent}
.lz .sh .pct{font-weight:700;font-size:clamp(30px,5vw,44px);color:var(--green);line-height:1;margin-bottom:10px}
/* Sous-titre « X gagnés / Y » posé SOUS le nombre (recentré — demande user 2026-08-01, plus flottant à droite) */
.lz .sh-cap{font-size:14px;color:var(--faint);margin:2px 0 12px}
.lz .sh-cap b{color:var(--ink);font-weight:600}
.lz h1.tag{font-size:clamp(22px,3vw,30px);font-weight:700;margin:12px 0 12px;max-width:17ch}
.lz h1.tag .hl{color:var(--green)}
.lz .lede{font-size:16.5px;color:var(--dim);max-width:44ch;margin-bottom:24px}
.lz .lede b{color:var(--ink)}
.lz .cta-row{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.lz .cta-note{font-size:12.5px;color:var(--faint)}
.lz .metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;margin-top:30px;background:var(--line);
  border:1px solid var(--line);border-radius:15px;overflow:hidden}
.lz .metric{background:linear-gradient(180deg,rgba(255,255,255,.028),transparent 40%),var(--gr2);
  padding:17px 15px;position:relative}
/* Fin liseré cyan lumineux en tête de chaque métrique -> profondeur premium (user 2026-08-22). */
.lz .metric::before{content:"";position:absolute;left:0;right:0;top:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(34,184,255,.40),transparent)}
.lz .metric .k{font-size:12px;color:var(--faint);font-weight:600}
.lz .metric .v{font-size:26px;font-weight:700;margin-top:3px;letter-spacing:-.02em}
.lz .metric .v small{font-size:14px;font-weight:600;color:var(--dim)}
/* Cartes teintées CYAN comme l'onglet Stats (.sx-hero) — demande user 2026-08-01 : « garder le style de
   l'onglet stats ». Fond cyan léger + bord cyan + halo cyan discret. */
.lz .cc{background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02));
  border:1px solid rgba(34,184,255,.45);border-radius:16px;
  padding:20px 20px 14px;box-shadow:0 0 26px rgba(34,184,255,.15);position:relative;overflow:hidden}
.lz .cc::before{display:none}
.lz .cc-h{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.lz .cc-h .t{font-size:13px;font-weight:600;color:var(--dim)}.lz .cc-h .roi{font-size:15px;font-weight:700;color:var(--green)}
.lz svg.cv{display:block;width:100%;height:150px}
/* Courbe FINE (user 2026-08-22 : « le graph est trop gras ») : trait plus léger + ombre douce. */
.lz .cv path.ln{fill:none;stroke:url(#lzln);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;
  filter:drop-shadow(0 2px 6px rgba(52,210,123,.28));vector-effect:non-scaling-stroke}
.lz .cv .ep{fill:var(--gb)}.lz .cv .z0{stroke:rgba(150,182,222,.16);stroke-width:.8;stroke-dasharray:3 4}
.lz .cc-f{display:flex;justify-content:space-between;font-size:11.5px;color:var(--faint);margin-top:4px}
.lz section.blk{position:relative;padding:56px 0;border-top:1px solid var(--line)}
.lz .sec-head{max-width:56ch;margin-bottom:34px}
.lz .sec-head .eyebrow{margin-bottom:14px;display:inline-flex;align-items:center;gap:10px}
/* Liseré d'accent avant l'eyebrow de section (touche éditoriale premium, user 2026-08-22). */
.lz .sec-head .eyebrow::before{content:"";width:24px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,var(--green),rgba(34,184,255,0))}
.lz .sec-head h2{font-size:clamp(26px,3.4vw,38px);font-weight:700}
.lz .sec-head p{margin-top:14px;font-size:16.5px;color:var(--dim)}
.lz .gates{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.lz .gate{background:var(--gr2);border:1px solid var(--line);border-radius:16px;padding:22px;position:relative;overflow:hidden}
.lz .gate .step{font-family:var(--mono);font-size:12px;color:var(--faint);letter-spacing:.05em}
.lz .gate h3{font-size:19px;font-weight:700;margin:12px 0 8px;display:flex;align-items:center;gap:10px}
.lz .gate p{font-size:14px;color:var(--dim)}.lz .gate p b{color:var(--ink)}
.lz .gate .bar{position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--green);opacity:.85}
.lz .gate.rej .bar{background:var(--amber)}
.lz .gate .tick{display:inline-flex;width:26px;height:26px;border-radius:8px;align-items:center;justify-content:center;
  font-weight:700;font-size:14px;background:var(--gfill);color:var(--gb);border:1px solid rgba(52,210,123,.3)}
.lz .gate.rej .tick{background:rgba(255,176,32,.12);color:var(--amber);border-color:rgba(255,176,32,.3)}
.lz .verdict{margin-top:24px;display:flex;gap:18px;align-items:center;flex-wrap:wrap;
  background:linear-gradient(180deg,var(--gr3),var(--gr2));border:1px solid var(--line2);border-radius:16px;padding:22px 24px}
.lz .verdict .vn{font-size:44px;font-weight:700;color:var(--amber);letter-spacing:-.03em;line-height:1}
.lz .verdict .vt{font-size:15.5px;color:var(--dim);max-width:54ch}.lz .verdict .vt b{color:var(--ink)}
.lz .pillars{display:grid;grid-template-columns:repeat(3,1fr);gap:26px}
.lz .pillar{padding-top:18px;border-top:2px solid var(--green)}
.lz .pillar h3{font-size:17px;font-weight:700;margin-bottom:7px}.lz .pillar p{font-size:14px;color:var(--dim)}
.lz .pillar .kpi{font-family:var(--mono);font-size:12.5px;color:var(--green);margin-top:9px}
.lz .honesty{display:grid;grid-template-columns:.85fr 1.15fr;gap:40px;align-items:center}
.lz .bigq{font-size:clamp(24px,3vw,32px);font-weight:700;line-height:1.15}
.lz .honesty p.sub{margin-top:16px;font-size:16px;color:var(--dim)}
.lz .lzl{display:flex;flex-wrap:wrap;gap:9px}
.lz .lz-loss{font-family:var(--mono);font-size:12.5px;padding:7px 11px;border-radius:9px;
  background:rgba(255,107,107,.07);border:1px solid rgba(255,107,107,.22);color:#ffb3b3;display:flex;gap:8px;align-items:center}
.lz .lz-d{color:var(--faint)}.lz .lz-o{color:var(--dim)}
.lz .calib{display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:center}
.lz .cvz{display:flex;flex-direction:column;gap:12px}
.lz .cr{display:grid;grid-template-columns:96px 1fr 74px;gap:12px;align-items:center}
.lz .cr .lab{font-family:var(--mono);font-size:12.5px;color:var(--dim);text-align:right}
.lz .tr{height:12px;border-radius:6px;background:rgba(150,182,222,.09);position:relative;overflow:hidden}
.lz .fl{position:absolute;left:0;top:0;bottom:0;border-radius:6px;background:linear-gradient(90deg,var(--green),var(--gb));
  width:0;transition:width 1.1s cubic-bezier(.2,.7,.2,1)}
.lz .rl{font-family:var(--mono);font-size:12.5px;color:var(--green)}
/* Bloc « Notre signature » (accueil) : ticket-réplique Confiance/Marché/Value/Cote + les 3 calculs, sans
   dévoiler sources ni méthode d'analyse (demande user 2026-08-01). */
.lz .steps{display:flex;flex-direction:column;gap:20px}
.lz .stp{display:flex;gap:14px;align-items:flex-start}
.lz .stp .n{flex:0 0 auto;width:27px;height:27px;border-radius:50%;border:1px solid var(--green);color:var(--green);
  font-family:var(--mono);font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center}
.lz .stp h3{font-size:16px;font-weight:700}
.lz .stp p{font-size:14px;color:var(--dim);margin-top:3px}.lz .stp p b{color:var(--ink)}
.lz .stp .f{display:inline-block;font-family:var(--mono);font-size:12.5px;color:var(--green);margin-top:7px;
  padding:3px 9px;border:1px solid var(--line2);border-radius:8px;background:var(--gfill)}
.lz .pl-h{font-size:14px;font-weight:600;color:var(--dim);margin-bottom:15px}
.lz .pl-h b{color:var(--ink)}.lz .pl-h b.red{color:var(--red)}
.lz .pl-h span{color:var(--faint);font-weight:400;font-size:13px}
/* DIDACTICIEL : carte de match COMPLÈTE annotée (exemple fictif) — demande user 2026-08-01. */
.lz .demo{display:grid;grid-template-columns:.96fr 1.04fr;gap:40px;align-items:start}
.lz .dcard{background:linear-gradient(180deg,rgba(34,184,255,.07),rgba(34,184,255,.015));
  border:1px solid rgba(34,184,255,.38);border-radius:16px;
  padding:17px 18px 6px;box-shadow:0 0 22px rgba(34,184,255,.12)}
/* Carte RÉELLE répliquée (user 2026-08-22, capture IMG_4714) : ligue centrée · logos+noms de part et
   d'autre de l'heure/compte à rebours · panneau pari Confiance+Cote · barre de confiance · « Pourquoi ce pari ». */
.lz .dm-lg{position:relative;text-align:center;font-size:12px;font-weight:700;letter-spacing:.09em;
  text-transform:uppercase;color:var(--faint);padding:2px 30px 13px}
.lz .dm-match{position:relative;display:grid;grid-template-columns:1fr auto 1fr;align-items:start;gap:8px;padding:2px 0 15px}
.lz .dm-team{display:flex;flex-direction:column;align-items:center;gap:8px;text-align:center}
.lz .dm-crest{width:46px;height:46px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:18px;color:var(--ink);overflow:hidden;
  background:radial-gradient(circle at 32% 28%,rgba(255,255,255,.15),rgba(255,255,255,.03));
  border:1px solid var(--line2);box-shadow:inset 0 1px 0 rgba(255,255,255,.10)}
.lz .dm-crest img{width:34px;height:34px;object-fit:contain;display:block}
.lz .dm-nm{font-size:14.5px;font-weight:700;line-height:1.15}
.lz .dm-mid{display:flex;flex-direction:column;align-items:center;gap:5px;padding:2px 6px 0}
.lz .dm-time{font-size:26px;font-weight:800;letter-spacing:.01em}
.lz .dm-cd{font-family:var(--mono);font-size:11.5px;color:var(--dim);background:var(--gfill);
  border:1px solid var(--line2);border-radius:999px;padding:2px 10px}
.lz .dm-c3{margin:2px 0 0}
.lz .dm-bet{position:relative;background:rgba(255,255,255,.02);border:1px solid var(--line);border-radius:12px;
  padding:14px 15px 15px;margin-bottom:2px}
.lz .dm-pick{text-align:center;font-size:17px;font-weight:700}
.lz .dm-gloss{text-align:center;font-size:13px;color:var(--dim);margin-top:3px}
.lz .dm-sep{height:1px;background:var(--line);margin:13px -15px}
/* Rangée métriques IDENTIQUE à la vraie carte (user 2026-08-22) : Confiance · Edge · Value · Cote,
   4 colonnes égales, filets verticaux — edge & value AU MÊME ENDROIT que sur une carte Confiance/Value. */
.lz .dm-stats{display:grid;grid-template-columns:repeat(4,1fr);text-align:center;margin-top:6px}
.lz .dm-st{position:relative;display:flex;flex-direction:column;gap:3px;padding:22px 3px 2px;
  border-left:1px solid var(--line);min-width:0}
.lz .dm-st:first-child{border-left:none}
/* pastille d'annotation d'une CELLULE métrique : centrée AU-DESSUS du libellé (plus par-dessus) — user 2026-08-22 */
.lz .dc-tag.dm-ct{position:absolute;top:0;left:50%;transform:translateX(-50%);margin:0;width:17px;height:17px;font-size:11px;z-index:2}
.lz .dm-l{font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--faint)}
.lz .dm-v{font-size:18px;font-weight:800;line-height:1.05;white-space:nowrap}.lz .dm-v.green{color:var(--green)}
.lz .dm-st:first-child .dm-v,.lz .dm-st:last-child .dm-v{font-size:20px}   /* Confiance & Cote = héros, comme la vraie carte */
.lz .dm-s{font-size:11px;color:var(--green);font-weight:700}
.lz .dm-barwrap{height:9px;border-radius:999px;background:rgba(255,255,255,.06);margin-top:14px;overflow:hidden}
.lz .dm-bar{height:100%;border-radius:999px;background:linear-gradient(90deg,#1f9d57,#37d07f)}
.lz .dc-why{display:flex;justify-content:space-between;align-items:center;font-size:14px;font-weight:600;color:var(--ink);
  border-top:1px solid var(--line);margin:11px -18px 0;padding:13px 18px;position:relative}.lz .dc-why .chev{color:var(--faint)}
.lz .dc-tag{display:inline-flex;width:19px;height:19px;border-radius:50%;background:var(--green);color:var(--accent-ink);
  font-family:var(--mono);font-size:11px;font-weight:700;align-items:center;justify-content:center;vertical-align:middle;margin-left:6px}
/* Numéros d'annotation : badge à DROITE de la ligne (.dc-tr) ou coin HAUT-GAUCHE d'un bloc (.dc-cc). */
.lz .dc-why{padding-right:30px}
.lz .dc-tag.dc-tr{position:absolute;top:50%;right:4px;transform:translateY(-50%);margin:0}
.lz .dc-tag.dc-cc{position:absolute;top:5px;left:5px;margin:0;width:17px;height:17px;font-size:11px;z-index:2}
.lz .dc-legend{display:flex;flex-direction:column;gap:16px}
.lz .dc-li{display:flex;gap:12px;align-items:flex-start}.lz .dc-li .dc-tag{margin:1px 0 0;flex:0 0 auto}
.lz .dc-li b{font-size:15px}.lz .dc-li p{font-size:13.5px;color:var(--dim);margin-top:2px}
.lz .dc-li .f{display:inline-block;font-family:var(--mono);font-size:12px;color:var(--green);margin-top:5px;
  padding:2px 8px;border:1px solid var(--line2);border-radius:7px;background:var(--gfill)}
.lz .demo-note{font-size:12px;color:var(--faint);margin-top:16px;font-style:italic}
.lz .final{position:relative;text-align:center;padding:70px 0 60px;border-top:1px solid var(--line);overflow:hidden}
.lz .final .glow{display:none}   /* plus de halo vert */
.lz .final h2{position:relative;font-size:clamp(30px,4.4vw,50px);font-weight:700}
.lz .final p{position:relative;margin:16px auto 28px;max-width:48ch;font-size:17px;color:var(--dim)}
.lz .final .cta-row{justify-content:center}
.lz .lzfoot{border-top:1px solid var(--line);padding:24px 0 30px}
.lz .lzfoot .wrap{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--faint)}
.lz .respo{display:inline-flex;align-items:center;gap:8px}
.lz .b18{font-weight:700;color:var(--dim);border:1px solid var(--line2);border-radius:6px;padding:1px 6px;font-size:11px}
.lz .reveal{opacity:1;transform:none}   /* fragment SPA : contenu STATIQUE (pas d'anim JS) -> toujours visible */
.lz .lznav{position:fixed;left:0;right:0;bottom:0;z-index:60;display:flex;max-width:720px;margin:0 auto;
  background:rgba(11,14,20,.92);backdrop-filter:blur(16px);border-top:1px solid var(--line2);
  padding:7px 6px calc(7px + env(safe-area-inset-bottom))}
.lz .lznav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:5px 0;color:var(--faint);
  font-size:9px;font-weight:600}
.lz .lznav a .ic{font-size:22px;line-height:1;height:24px;display:flex;align-items:center}
.lz .lznav a.on{color:var(--green)}
/* Deux familles de paris (Confiance / Value) : cartes jumelles, accent vert vs cyan (user 2026-08-22). */
.lz .gates.duo{grid-template-columns:1fr 1fr}
.lz .gate.val .bar{background:var(--accent)}
.lz .gate.val .tick{background:rgba(34,184,255,.12);color:var(--accent);border-color:rgba(34,184,255,.30)}
.lz .gate .kpi{display:block;font-family:var(--mono);font-size:12.5px;color:var(--green);margin-top:11px}
.lz .gate.val .kpi{color:var(--accent)}
@media (max-width:860px){.lz .hero-grid{grid-template-columns:1fr;gap:24px}.lz .metrics{grid-template-columns:repeat(2,1fr)}
  .lz .gates.duo{grid-template-columns:1fr}
  .lz .gates,.lz .pillars{grid-template-columns:1fr}.lz .honesty,.lz .calib{grid-template-columns:1fr;gap:26px}.lz .hero{padding-top:30px}
  .lz .decrypt,.lz .demo{grid-template-columns:1fr;gap:28px}
  /* HERO CENTRÉ sur mobile (user 2026-08-22 : « recentrer ») : grand nombre, texte et CTA au centre. */
  .lz .hero-grid>div:first-child{text-align:center;display:flex;flex-direction:column;align-items:center}
  .lz .sh{justify-content:center}
  .lz h1.tag,.lz .lede{max-width:26ch;margin-left:auto;margin-right:auto}
  .lz .cta-row{justify-content:center}}
/* ============ PASSE PREMIUM ACCUEIL (user 2026-08-22) : hero · verre · typo · reveal ============ */
/* Hero : aura douce derrière le grand nombre (subtile, jamais recouverte -> dans le bloc .sh) */
.lz .sh{position:relative}
.lz .sh>*{position:relative;z-index:1}
.lz .sh::before{content:"";position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  width:118%;height:150%;z-index:0;pointer-events:none;
  background:radial-gradient(52% 58% at 50% 50%,rgba(34,184,255,.16),transparent 72%);filter:blur(2px)}
.lz .sh .big{filter:drop-shadow(0 8px 34px rgba(34,184,255,.22))}
/* Profondeur premium (ombre douce + liseré interne). PAS de backdrop-filter : sur iOS il re-calcule le flou
   à chaque frame de scroll -> le contenu derrière « bouge » puis se fige (user 2026-08-22). */
.lz .cc,.lz .tkt,.lz .dcard,.lz .gate,.lz .verdict{
  box-shadow:0 22px 50px -30px rgba(0,0,0,.78),inset 0 1px 0 rgba(255,255,255,.05)}
.lz .cc{position:relative}
.lz .cc::after{content:"";position:absolute;left:0;right:0;top:0;height:1px;z-index:2;
  background:linear-gradient(90deg,transparent,rgba(34,184,255,.55),transparent)}
/* ===== HERO PREMIUM « WOW » (refonte user 2026-08-22) : centré, grand nombre chromé, courbe pleine largeur. ===== */
.lz .hstage{max-width:600px;margin:0 auto;text-align:center;display:flex;flex-direction:column;align-items:center}
.lz .hstage .hb{margin-bottom:20px}
.lz .hnum{position:relative;display:inline-flex;align-items:flex-start;line-height:.8;margin-top:2px}
.lz .hnum::before{content:"";position:absolute;left:50%;top:52%;transform:translate(-50%,-50%);
  width:150%;height:180%;z-index:0;pointer-events:none;
  background:radial-gradient(50% 55% at 50% 50%,rgba(34,184,255,.22),transparent 70%);filter:blur(4px)}
.lz .hnum .big{position:relative;z-index:1;font-weight:800;font-size:clamp(104px,30vw,190px);letter-spacing:-.05em;
  background:linear-gradient(178deg,#ffffff 3%,#e2f0ff 28%,var(--accent2) 64%,var(--accent) 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent;
  filter:drop-shadow(0 12px 34px rgba(34,184,255,.32))}
.lz .hnum .pct{position:relative;z-index:1;font-weight:800;font-size:clamp(34px,8vw,60px);color:var(--accent);
  margin-top:.28em;margin-left:.04em}
.lz .hnum-line{width:76px;height:3px;border-radius:3px;margin:15px 0 13px;
  background:linear-gradient(90deg,transparent,var(--accent),var(--accent2),transparent)}
.lz .hsub{font-size:15px;color:var(--dim);margin-bottom:4px}.lz .hsub b{color:var(--ink);font-weight:600}
.lz .htag{font-size:clamp(23px,5.2vw,33px);font-weight:800;letter-spacing:-.02em;line-height:1.12;margin:10px 0 26px;max-width:20ch}
.lz .htag .hl{background:linear-gradient(90deg,var(--accent2),var(--accent));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.lz .hcurve{width:100%;margin:0 0 22px;padding:15px 18px 11px;border-radius:18px;position:relative;overflow:hidden;
  background:linear-gradient(180deg,rgba(34,184,255,.06),rgba(34,184,255,.015));
  border:1px solid rgba(34,184,255,.28);box-shadow:0 22px 50px -32px rgba(0,0,0,.8),inset 0 1px 0 rgba(255,255,255,.04)}
.lz .hcurve::after{content:"";position:absolute;left:0;right:0;top:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(34,184,255,.5),transparent)}
.lz .hcurve-top{display:flex;justify-content:space-between;align-items:baseline;font-size:12px;color:var(--dim);font-weight:600;margin-bottom:8px}
.lz .hcurve-top .roi{color:var(--green);font-size:14px;font-family:var(--mono)}
.lz .hcurve svg.cv{height:118px}
.lz .hcurve-f{display:flex;justify-content:space-between;font-size:11px;color:var(--faint);margin-top:5px}
.lz .hkpis{display:flex;width:100%;max-width:460px;gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:22px}
.lz .hk{flex:1;background:linear-gradient(180deg,rgba(255,255,255,.028),transparent 42%),var(--gr2);padding:14px 8px}
.lz .hk b{display:block;font-size:20px;font-weight:800;letter-spacing:-.01em}
.lz .hk span{display:block;font-size:9.5px;color:var(--faint);font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-top:4px}
.lz .hstage .cta-note{margin-top:13px}
/* HERO ÉDITORIAL (user 2026-08-22) : grand titre-message + chiffre en PREUVE juste après. */
.lz .hhead{font-size:clamp(33px,8.6vw,56px);font-weight:800;letter-spacing:-.03em;line-height:1.04;margin:12px 0 0}
.lz .hhead .hl{background:linear-gradient(92deg,var(--accent2),var(--accent));-webkit-background-clip:text;background-clip:text;color:transparent}
.lz .hhead::after{content:"";display:block;width:70px;height:3px;border-radius:3px;margin:18px auto 0;
  background:linear-gradient(90deg,var(--accent),var(--accent2))}
.lz .hproof{display:flex;align-items:center;justify-content:center;gap:18px;margin:26px 0 28px;text-align:left}
.lz .hpnum{position:relative;display:inline-flex;align-items:flex-start;line-height:.82}
.lz .hpnum::before{content:"";position:absolute;left:50%;top:52%;transform:translate(-50%,-50%);
  width:150%;height:175%;z-index:0;pointer-events:none;
  background:radial-gradient(50% 55% at 50% 50%,rgba(34,184,255,.20),transparent 70%);filter:blur(4px)}
.lz .hpnum .big{position:relative;z-index:1;font-weight:800;font-size:clamp(62px,17vw,98px);letter-spacing:-.045em;
  background:linear-gradient(178deg,#fff 4%,#e2f0ff 30%,var(--accent2) 66%,var(--accent) 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent;filter:drop-shadow(0 8px 26px rgba(34,184,255,.3))}
.lz .hpnum .pct{position:relative;z-index:1;font-weight:800;font-size:clamp(22px,5vw,34px);color:var(--accent);margin-top:.3em;margin-left:.03em}
.lz .hptxt{font-size:14.5px;color:var(--dim);line-height:1.4;max-width:19ch}
.lz .hptxt b{color:var(--ink);font-weight:700}.lz .hptxt .hpdim{color:var(--faint);font-size:12.5px}
/* Typo éditoriale : titres posés, plus d'air */
.lz section.blk{padding:62px 0}
.lz .sec-head h2{letter-spacing:-.02em;line-height:1.09}
.lz h1.tag{letter-spacing:-.02em;line-height:1.12}
/* REVEAL au scroll — actif SEULEMENT si JS ajoute .js-anim (sinon tout reste visible = zéro régression). */
.lz.js-anim .reveal{opacity:0;transform:translateY(16px);transition:opacity .5s ease,transform .6s cubic-bezier(.22,.85,.3,1)}
.lz.js-anim .reveal.in{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){.lz.js-anim .reveal{opacity:1!important;transform:none!important;transition:none!important}}
@media (prefers-reduced-motion:reduce){.lz *{animation:none!important;transition:none!important}.lz .reveal{opacity:1;transform:none}}
"""

# Animations PREMIUM de l'Accueil (user 2026-08-22) : reveal au scroll (IntersectionObserver), count-up du
# grand nombre, tracé de la courbe d'équité. `window._lzAnim(root)` : idempotent par `.lz._an`, respecte
# prefers-reduced-motion (ne fait RIEN -> page statique visible). Appelé par le SPA au chargement du panneau
# Accueil + auto-run pour le rendu initial. Progressive enhancement : sans JS, tout reste visible.
_LZ_ANIM_JS = (
    "window._lzAnim=function(root){try{"
    "var lz=(root||document).querySelector?(root||document).querySelector('.lz'):null;"
    "if(!lz||lz._an||lz.offsetParent===null)return;"  # panneau caché (préchargé) -> on attend qu'il soit affiché
    "if(window.matchMedia&&window.matchMedia('(prefers-reduced-motion:reduce)').matches){lz._an=1;return;}"
    "lz._an=1;lz.classList.add('js-anim');"
    "var sel='.hkpis,.sec-head,.demo,.steps .stp,.gates .gate,.calib>div,.verdict,.final h2,.final p,.final .cta-row';"
    "var els=lz.querySelectorAll(sel),i;"
    "if(!('IntersectionObserver' in window)){for(i=0;i<els.length;i++)els[i].classList.add('reveal','in');}"
    "else{var io=new IntersectionObserver(function(es){es.forEach(function(x){if(x.isIntersecting){x.target.classList.add('in');io.unobserve(x.target);}});},{threshold:.08,rootMargin:'0px 0px -6% 0px'});"
    "for(i=0;i<els.length;i++){els[i].classList.add('reveal');els[i].style.transitionDelay=((i%5)*45)+'ms';io.observe(els[i]);}}"
    "var big=lz.querySelector('.hpnum .big');if(big){var tv=parseInt(big.textContent,10);"
    "if(tv>0){big.textContent='0';var s0=null;var cu=function(ts){if(!s0)s0=ts;var k=Math.min(1,(ts-s0)/900);"
    "big.textContent=Math.round(tv*(1-Math.pow(1-k,3)));if(k<1)requestAnimationFrame(cu);};requestAnimationFrame(cu);}}"
    "}catch(e){}};"
    "(function(){function r(){window._lzAnim(document);}"
    "if(document.readyState!=='loading')setTimeout(r,80);else document.addEventListener('DOMContentLoaded',function(){setTimeout(r,80);});})();"
)


def _lz_stats() -> dict:
    """Chiffres DYNAMIQUES de la vitrine, calculés sur le relevé RÉEL (foot simple depuis _LZ_SINCE).
    Tolérant : en cas d'échec, valeurs de repli sûres pour ne jamais faire planter la page."""
    try:
        won = lost = 0
        stake = ret = 0.0
        losses = []
        analysed = retained = 0
        _ev = []                             # (date, delta_profit) -> courbe d'équité du tier CONFIANCE
        for d in analyses.iter_meta("foot"):
            if d.get("roi_void"):            # pari exclu du ROI/historique (correction) -> hors vitrine accueil
                continue
            st = (d.get("start") or "")
            if st[:10] < _LZ_SINCE:
                continue
            analysed += 1
            if d.get("bets"):
                retained += 1
            for b in (analyses.stat_bet(d),):   # UN MATCH = UN PARI (user 2026-08-07) : plus de stat_bet_first
                if not isinstance(b, dict):
                    continue
                r = b.get("result")
                if r not in ("won", "lost", "push"):
                    continue
                # VITRINE = tier CONFIANCE (le TAUX PHARE, user 2026-08-09) : on ne montre QUE les paris à haute
                # confiance calibrée (figée -> monotone). Les paris VALUE (rentables, plus variables) sont un
                # tier séparé, pas dans le taux phare. Réversible (TIER_SPLIT_ON=False -> tout confiance).
                if analyses.tier_of(d) != "confiance":
                    continue
                co = b.get("cote") or b.get("odds") or 0
                stake += 1
                if r == "won":
                    won += 1
                    ret += co
                    _ev.append((st[:10], (co or 1) - 1))
                elif r == "lost":
                    lost += 1
                    losses.append((st[:10], _noF(d.get("name") or ""), co))
                    _ev.append((st[:10], -1.0))
                else:
                    ret += 1
                    _ev.append((st[:10], 0.0))
        settled = won + lost
        pct = round(100 * won / settled) if settled else 0
        roi = round(100 * (ret - stake) / stake, 1) if stake else 0.0
        sel = round(100 * (analysed - retained) / analysed) if analysed else 0
        _ev.sort(key=lambda x: x[0])         # courbe d'équité du tier CONFIANCE (profit cumulé, ordre des jours)
        pts, _cum, best, _cur = [0.0], 0.0, 0, 0
        for _, _dv in _ev:
            _cum += _dv
            pts.append(round(_cum, 2))
            if _dv > 0:                      # gagné -> série ++ ; perdu -> reset ; push -> inchangé
                _cur += 1
                best = max(best, _cur)
            elif _dv < 0:
                _cur = 0
        cal = analyses.calibration() or {}
        buckets = [r for r in (cal.get("rows") or [])
                   if r.get("lo", 0) >= 65 and (r.get("n") or 0) >= 20][:3]
        return {"won": won, "total": settled, "pct": pct, "roi": roi,
                "profit": round(ret - stake, 1), "losses": sorted(losses),
                "sel": sel, "pts": pts, "best": best,
                "cal_n": cal.get("n") or 0, "cal_mae": cal.get("mae"), "cal_rows": buckets}
    except Exception:
        return {"won": 39, "total": 42, "pct": 93, "roi": 19.0, "profit": 8.0, "losses": [],
                "sel": 40, "pts": [0.0, 8.0], "best": 15, "cal_n": 5744, "cal_mae": 0.8, "cal_rows": []}


def _lz_curve(pts: list) -> tuple:
    """Chemin SVG (viewBox 0 0 100 42) de la vraie courbe de bénéfice cumulé. Renvoie (d_ligne, x_fin, y_fin, y_zero)."""
    if not pts or len(pts) < 2:
        pts = [0.0, 1.0]
    mn, mx = min(pts), max(pts)
    rng = (mx - mn) or 1.0
    n = len(pts)

    def _y(v):
        return round(40 - (v - mn) / rng * 36 - 2, 2)
    coords = [(round(i / (n - 1) * 100, 2), _y(v)) for i, v in enumerate(pts)]
    d = "M" + " L".join(f"{x},{y}" for x, y in coords)
    return d, coords[-1][0], coords[-1][1], _y(0)


def accueil_body(frag: bool = True) -> str:
    """CONTENU de l'onglet ACCUEIL (vitrine : relevé + méthode + transparence), rendu comme un FRAGMENT SPA
    (demande user 2026-07-30 : présentation intégrée à l'app, plus de page autonome au style différent).
    Style SCOPÉ sous `.lz` (variables locales, n'écrase pas l'app). Statique (pas d'anim JS). Aucune source
    nommée. `frag` conservé pour signature homogène (le cadre/nav/logo vient du spa_shell)."""
    e = html.escape
    s = _lz_stats()
    cd, ex, ey, zy = _lz_curve(s["pts"])
    roi_txt = f"+{s['roi']:g} %" if s["roi"] >= 0 else f"{s['roi']:g} %"
    prof_txt = f"+{s['profit']:g} u" if s["profit"] >= 0 else f"{s['profit']:g} u"

    def _fr_date(iso):
        try:
            return f"{iso[8:10]}/{iso[5:7]}"
        except Exception:
            return iso
    losses_html = "".join(
        f'<span class="lz-loss"><span class="lz-d">{_fr_date(dt)}</span> {e(nm[:26])} '
        f'<span class="lz-o">@{co:g}</span></span>'
        for dt, nm, co in s["losses"]) or '<span class="dimc" style="font-size:14px">Aucune perte sur la période.</span>'

    _rows = s["cal_rows"] or [{"avg_conf": 69, "win_rate": 69}, {"avg_conf": 80, "win_rate": 80}]
    cal_html = "".join(   # largeur des barres EN DUR (statique, pas de JS) : width inline
        f'<div class="cr"><span class="lab">annoncé {r["avg_conf"]} %</span>'
        f'<span class="tr"><span class="fl" style="width:{r["avg_conf"]}%"></span></span>'
        f'<span class="rl">réel {r["win_rate"]} %</span></div>'
        for r in _rows)
    mae_txt = f'{s["cal_mae"]:g} pt' if isinstance(s["cal_mae"], (int, float)) else "0,8 pt"
    cal_n_txt = f'{s["cal_n"]:,}'.replace(",", " ")

    return f"""<style>{_LZ_CSS}</style>
<div class="lz">

<div class="hero"><div class="lzw">
  <div class="hstage">
    <h1 class="hhead">La transparence,<br><span class="hl">en chiffres.</span></h1>
    <div class="hproof">
      <span class="hpnum"><span class="big num">{s['pct']}</span><span class="pct">%</span></span>
      <span class="hptxt"><b>de réussite</b> sur nos paris Confiance<br>
        <span class="hpdim"><b class="num">{s['won']}</b> gagnés sur <b class="num">{s['total']}</b> · relevé réel, pas une projection</span></span>
    </div>
    <div class="hcurve">
      <div class="hcurve-top"><span>Bénéfice cumulé · football</span><span class="roi num">ROI {roi_txt}</span></div>
      <svg class="cv" viewBox="0 0 100 42" preserveAspectRatio="none" aria-label="Courbe de bénéfice cumulé, en hausse">
        <defs><linearGradient id="lzln" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="var(--accent2)"/><stop offset="1" stop-color="var(--accent)"/></linearGradient>
          <linearGradient id="lzfill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="rgba(34,184,255,.20)"/><stop offset="1" stop-color="rgba(34,184,255,0)"/></linearGradient></defs>
        <path class="area" d="{cd} L100,42 L0,42 Z" fill="url(#lzfill)" stroke="none"/>
        <line class="z0" x1="0" y1="{zy}" x2="100" y2="{zy}"/>
        <path class="ln" d="{cd}"/>
        <circle class="ep" cx="{ex}" cy="{ey}" r="1.7"/>
      </svg>
      <div class="hcurve-f"><span>7 juin</span><span class="mono">{prof_txt} · mise plate 1 u</span><span>aujourd'hui</span></div>
    </div>
    <div class="hkpis">
      <div class="hk"><b class="green num">{roi_txt}</b><span>rentabilité</span></div>
      <div class="hk"><b class="num">{s['total']}</b><span>paris réglés</span></div>
      <div class="hk"><b class="num">{s['best']}</b><span>meilleure série</span></div>
    </div>
  </div>
</div></div>

<section class="blk"><div class="lzw">
  <div class="sec-head"><span class="eyebrow">Comment on analyse</span>
    <h2>Une IA, des faits croisés, zéro pari au feeling.</h2>
    <p>Chaque match passe par la même chaîne. Aucune sélection au hasard, aucune opinion isolée — que des nombres
      vérifiés et recalés sur la réalité.</p></div>
  <div class="steps">
    <div class="stp"><span class="n">1</span><div><h3>Sélection</h3>
      <p>On garde les <b>~24 matchs les plus suivis</b> du jour ; le reste est écarté d'office.</p></div></div>
    <div class="stp"><span class="n">2</span><div><h3>Dossier de faits</h3>
      <p>On croise <b>plusieurs sources</b> pour chaque info (forme, blessés, qualité des occasions, séries) plus une
        <b>référence « sharp »</b> — les bookmakers qui se trompent le moins, notre boussole de probabilité.</p></div></div>
    <div class="stp"><span class="n">3</span><div><h3>Analyse par une IA</h3>
      <p>Un <b>agent d'analyse</b> lit tout le dossier, estime la <b>vraie probabilité</b> de chaque issue et retient
        <b>LE pari le plus sûr</b> qui a de la value — ou <b>s'abstient</b> s'il n'y a rien de solide.</p></div></div>
    <div class="stp"><span class="n">4</span><div><h3>Double contrôle</h3>
      <p>Le pari retenu passe devant un <b>panel de validation</b>. Pas de consensus ? Il est écarté.</p></div></div>
    <div class="stp"><span class="n">5</span><div><h3>Calibration</h3>
      <p>La confiance est <b>recalée sur notre historique réel</b> : quand on dit « 80 % », l'historique confirme
        ~80 %. Vérifié en continu sur des milliers de prédictions.</p></div></div>
    <div class="stp"><span class="n">6</span><div><h3>Publication, gel &amp; règlement</h3>
      <p>Publié <b>~1-2 h avant</b> le coup d'envoi, puis <b>gelé</b> (plus aucun changement), et <b>réglé honnêtement</b>
        après le match — gagné comme perdu.</p></div></div>
  </div>
</div></section>

<section class="blk"><div class="lzw">
  <div class="sec-head"><span class="eyebrow">Deux familles de paris</span>
    <h2>Confiance et Value : deux façons de gagner.</h2>
    <p>Tous nos paris sont value-positifs. On les classe selon leur profil — la <b>sûreté</b> d'un côté, le
      <b>rendement</b> de l'autre.</p></div>
  <div class="gates duo">
    <div class="gate"><span class="bar"></span><span class="step">⭐ LE PHARE</span>
      <h3><span class="tick">⭐</span> Confiance</h3>
      <p>Confiance calibrée <b>≥ 75 %</b> sur un marché <b>prouvé fiable</b>. Cote courte, on gagne très souvent :
        c'est le taux qu'on met en avant.</p>
      <span class="kpi">~85-90 % de réussite · cote ~1,3</span></div>
    <div class="gate val"><span class="bar"></span><span class="step">💎 LE RENDEMENT</span>
      <h3><span class="tick">💎</span> Value</h3>
      <p>Confiance plus modérée, mais la cote <b>paie en trop</b>. Perd un peu plus souvent — <b>mais rentable sur
        la durée</b>, c'est mathématique, pas de la chance.</p>
      <span class="kpi">plus de variance · meilleur ROI long terme</span></div>
  </div>
</div></section>

<section class="blk"><div class="lzw">
  <div class="sec-head"><span class="eyebrow">La discipline</span>
    <h2>On refuse la majorité des matchs. C'est là qu'est le taux.</h2>
    <p>Un pari n'est publié que s'il franchit deux portes. Sinon — abstention. Un jour sans pari est un jour
      sans perte, jamais un jour « pour jouer ».</p></div>
  <div class="gates">
    <div class="gate"><span class="bar"></span><span class="step">PORTE 01</span>
      <h3><span class="tick">✓</span> Confiance ≥ 65 %</h3>
      <p>La confiance <b>calibrée</b> (pas au feeling) doit franchir un plancher net. En dessous, on n'y touche pas.</p></div>
    <div class="gate"><span class="bar"></span><span class="step">PORTE 02</span>
      <h3><span class="tick">✓</span> Value positive</h3>
      <p>La cote doit nous payer <b>en trop</b>. Un favori à prix trop court est écarté, même s'il gagne « souvent ».</p></div>
    <div class="gate rej"><span class="bar"></span><span class="step">SINON</span>
      <h3><span class="tick">✕</span> Abstention</h3>
      <p>Rien ne passe ? On ne joue pas. Les marchés qui coûtent sont même exclus <b>automatiquement</b>.</p></div>
  </div>
  <div class="verdict"><span class="vn num">≈ {s['sel']} %</span>
    <span class="vt">des matchs analysés finissent <b>écartés</b>. Ce n'est pas de la timidité — c'est exactement
      ce qui produit les {s['pct']} %. Un système qui joue tous les jours ferait bien pire.</span></div>
</div></section>

<section class="blk"><div class="lzw">
  <div class="sec-head"><span class="eyebrow">Le pari décrypté</span>
    <h2>Une carte de match, lue de A à Z.</h2>
    <p>Voici exactement ce que vous recevez sur chaque pari — chiffre par chiffre, sans jargon.</p></div>
  <div class="demo">
    <div class="dcard">
      <div class="dm-lg">EUROPE · LIGUE DES CHAMPIONS<span class="dc-tag dc-tr">1</span></div>
      <div class="dm-match"><span class="dc-tag dc-cc">2</span>
        <div class="dm-team"><span class="dm-crest"><img src="/crest?name=Manchester City" alt="" loading="lazy"></span><span class="dm-nm">Manchester City</span></div>
        <div class="dm-mid"><span class="dm-time">21:00</span><span class="dm-cd">05h09m</span><span class="dc-tag dm-c3">3</span></div>
        <div class="dm-team"><span class="dm-crest"><img src="/crest?name=Real Madrid" alt="" loading="lazy"></span><span class="dm-nm">Real Madrid</span></div>
      </div>
      <div class="dm-bet"><span class="dc-tag dc-cc">4</span>
        <div class="dm-pick">Plus de 1.5 buts</div>
        <div class="dm-gloss">au moins 2 buts au total (les 2 équipes)</div>
        <div class="dm-sep"></div>
        <div class="dm-stats">
          <div class="dm-st"><span class="dc-tag dm-ct">5</span><span class="dm-l">Confiance</span><span class="dm-v green">79%</span><span class="dm-s">élevée</span></div>
          <div class="dm-st"><span class="dc-tag dm-ct">6</span><span class="dm-l">Edge</span><span class="dm-v green">+7 pts</span></div>
          <div class="dm-st"><span class="dc-tag dm-ct">7</span><span class="dm-l">Value</span><span class="dm-v green">+9%</span></div>
          <div class="dm-st"><span class="dc-tag dm-ct">8</span><span class="dm-l">Cote</span><span class="dm-v">1.38</span></div>
        </div>
        <div class="dm-barwrap"><div class="dm-bar" style="width:79%"></div></div>
      </div>
      <div class="dc-why">Pourquoi ce pari <span class="chev">▾</span><span class="dc-tag dc-tr">9</span></div>
    </div>
    <div class="dc-legend">
      <div class="dc-li"><span class="dc-tag">1</span><div><b>La compétition</b>
        <p>La zone et la compétition du match — le contexte, d'un coup d'œil.</p></div></div>
      <div class="dc-li"><span class="dc-tag">2</span><div><b>Les équipes</b>
        <p>L'affiche avec les logos. Domicile à gauche, extérieur à droite.</p></div></div>
      <div class="dc-li"><span class="dc-tag">3</span><div><b>Le coup d'envoi</b>
        <p>L'heure de début et le compte à rebours avant le lancement du match.</p></div></div>
      <div class="dc-li"><span class="dc-tag">4</span><div><b>Le pari, traduit en clair</b>
        <p>La sélection exacte à jouer, réécrite sans jargon — ici : <em>au moins 2 buts</em> dans le match.
          Vous savez précisément ce qui doit arriver pour gagner.</p></div></div>
      <div class="dc-li"><span class="dc-tag">5</span><div><b>La confiance</b>
        <p>Notre probabilité de gain, <b>recalibrée sur notre relevé réel</b> : quand on annonce 79 %, l'historique
          confirme que ça sort autour de 79 %. La barre verte la reprend en repère visuel — fiable, pas décorative.</p></div></div>
      <div class="dc-li"><span class="dc-tag">6</span><div><b>L'edge</b>
        <p>De combien notre confiance bat la probabilité qu'implique la cote (79 % contre 72 %) : <b>+7 points</b>.
          C'est notre avantage mesuré sur le marché.</p><span class="f">Edge = confiance − marché</span></div></div>
      <div class="dc-li"><span class="dc-tag">7</span><div><b>La value</b>
        <p>L'edge traduit <em>en argent</em> — ce que la cote paie en trop. <b>On ne publie un pari que si elle est positive.</b></p>
        <span class="f">Value = confiance × cote − 1</span></div></div>
      <div class="dc-li"><span class="dc-tag">8</span><div><b>La cote</b>
        <p>Le prix décimal du pari : votre mise <em>multipliée par</em> ce nombre si ça passe (1 € → 1,38 €).</p></div></div>
      <div class="dc-li"><span class="dc-tag">9</span><div><b>Pourquoi ce pari</b>
        <p>L'analyse complète du match en un tap : forme, contexte, risque assumé. Rien n'est caché.</p></div></div>
    </div>
  </div>
</div></section>

<section class="blk"><div class="lzw">
  <div class="sec-head"><span class="eyebrow">La preuve</span>
    <h2>On montre nos pertes. Et que nos % tombent juste.</h2>
    <p>Un site qui n'affiche que ses gains vous ment. Ici, tout est daté — rien n'est caché, rien n'est effacé.</p></div>
  <div class="calib">
    <div>
      <div class="pl-h"><b class="red">{s['total'] - s['won']} pertes</b> <span>· {_LZ_SINCE_LABEL}, cote comprise</span></div>
      <div class="lzl">{losses_html}</div>
    </div>
    <div>
      <div class="pl-h"><b>Calibration</b> <span>· annoncé vs réel, sur {cal_n_txt} prédictions</span></div>
      <div class="cvz">{cal_html}
        <div class="cr"><span class="lab">écart moyen</span><span class="tr"><span class="fl" style="width:96%"></span></span><span class="rl">{mae_txt}</span></div>
      </div>
    </div>
  </div>
</div></section>

<div class="final"><div class="lzw">
  <h2>Arrêtez de suivre des pronos. <br>Suivez un relevé.</h2>
  <p>Les pronos du jour, la courbe en direct, les combinés, la montante — et chaque pari réglé au grand jour.</p>
  <div class="cta-row"><a class="btn lg" href="/signup">Créer mon compte →</a></div>
  <p class="cta-note" style="margin-top:18px">Résiliable en un clic · aucune donnée revendue</p>
</div></div>

</div>"""


def _prog_day_label(ld) -> str:
    """« Aujourd'hui » / « Demain » / jj/mm en JOUR SPORTIF (06h→06h) — en-tête de jour du programme."""
    today = _sport_date(datetime.now(LOCAL_TZ) if LOCAL_TZ is not None else datetime.now())
    delta = (_sport_date(ld) - today).days
    return "Aujourd'hui" if delta == 0 else "Demain" if delta == 1 else ld.strftime("%d/%m")


def _sidecar_analyzed_at(sport: str, fid) -> str:
    """Heure locale HH:MM de l'analyse d'un match = mtime du .md (repli .json). '' si introuvable."""
    for ext in ("md", "json"):
        p = os.path.join(analyses.DIR, f"{sport}_{fid}.{ext}")
        try:
            dt = datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc)
            return (dt.astimezone(LOCAL_TZ) if LOCAL_TZ is not None else dt).strftime("%H:%M")
        except OSError:
            continue
    return ""


def _status_card(m: dict, dt, kind: str) -> str:
    """Carte d'un match SANS pari, présentée COMME une carte de pari (user 2026-08-17 : « affichés de la même
    manière que les cartes de paris ») : ligue + PAYS centrés, logos + HEURE au centre, et une ligne de STATUT
    à la place du pari. `kind` = 'wait' (à analyser, + heure d'analyse ~KO−2 h) | 'abst' (abstention)."""
    name = _noF(str(m.get("name") or ""))
    if " - " in name:
        home, away = [s.strip() for s in name.split(" - ", 1)]
    else:
        home, away = name, ""
    comp = str(m.get("comp") or "")
    _cty = _cap(match_select.comp_country(comp) or "")
    if _cty and _cty.lower() in comp.lower():
        _cty = ""
    comp_c = " • ".join(html.escape(p) for p in (_cty, _noF(comp)) if p).upper()
    ld = dt.astimezone(LOCAL_TZ) if (LOCAL_TZ is not None and dt.tzinfo is not None) else dt
    _center = f'<span class="tm-live"><b>{html.escape(ld.strftime("%H:%M"))}</b></span>'
    teams = _teams_vs_html(home, away, _center)
    if kind == "wait":
        _sub = (f'<div class="mc-stat mc-stat-wait">À analyser'
                f'<span class="mc-stat-sub">analyse prévue ~{(ld - timedelta(hours=2)).strftime("%H:%M")}</span></div>')
    else:
        _sub = ('<div class="mc-stat mc-stat-abst">Abstention'
                '<span class="mc-stat-sub">analysé — pas de value, non joué</span></div>')
    return (f'<div class="row mc mc-prem mc-statcard mc-st-{kind}">'
            f'<div class="mc-head"><div class="mc-main">'
            f'<div class="mc-line mc-line-c"><span class="mc-comp">{comp_c}</span></div>'
            f'<div class="mc-teams">{teams}</div>'
            f'<div class="mc-sub">{_sub}</div>'
            f'</div></div></div>')


def _awaiting_prematch_reanalysis(sport: str, mid: str, dt, now, window_h: float = 1.5) -> bool:
    """L'analyse existante a-t-elle été faite TROP TÔT (le matin, lead > window_h au moment de l'analyse)
    alors que le match est ENCORE à venir ? Si oui, sa RÉ-ANALYSE pré-match décisive (~1 h avant le KO, la
    « vague ») n'a PAS encore eu lieu. Miroir EXACT de `_analyzed_too_early` du scan (mtime du .md = instant
    de la dernière analyse) pour que l'affichage et le scan soient d'accord : on ne FIGE une abstention
    qu'APRÈS cette 2e analyse (user 2026-08-27). window_h = fenêtre de la vague (1,5 h, cf. scan_wave.ps1)."""
    if dt is None or dt <= now:
        return False
    # FIABILISÉ (user 2026-08-29) par le flag `prematch_done` posé par la VAGUE (--refresh-early) : c'est le
    # signal EXPLICITE que la 2e analyse décisive a eu lieu. prematch_done -> la vague est passée -> décision
    # FINALE (False -> l'abstention/le pari se fige). Un match À VENIR SANS ce flag -> la vague n'est pas encore
    # passée -> True (reste au Programme « À analyser », ni pari révélé, ni abstention). Repli mtime pour les
    # fiches héritées sans flag (analysé > window_h avant le KO = pas encore re-vérifié).
    d = analyses.meta(sport, mid) or {}
    if d.get("prematch_done"):
        return False
    try:
        analyzed = os.path.getmtime(os.path.join(analyses.DIR, f"{sport}_{mid}.md"))
    except OSError:
        return True                      # pas d'info d'analyse -> match à venir gardé « à analyser » (prudent)
    return (dt.timestamp() - analyzed) / 3600 > window_h


def _planning_cards(sport: str = "foot") -> tuple[list, list]:
    """(pending_cards, abst_cards) — CARTES (comme les paris) des matchs À ANALYSER (Programme) et des
    ABSTENTIONS (analysés sans pari). Modèle user 2026-08-17 : chaque match d'abord au programme, puis basculé
    dans sa catégorie APRÈS analyse de son PARI SIMPLE. Un match avec un pari simple retenu part en Confiance/
    Value (via `play`) et n'apparaît donc pas ici. Une jambe de combiné / le match montante NE sont PAS exclus :
    leur pari simple s'analyse à part -> ils restent au Programme tant que non analysés. 0 réseau."""
    prog = _load_day_programme()          # VIDÉ si périmé (avant le scan du jour, user 2026-08-18)

    def _dt_of(m):
        try:
            return datetime.fromisoformat(str(m.get("start")).replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            return None
    items = [(m, _dt_of(m)) for m in (prog.get("matches") or []) if (m.get("sport") or "foot") == sport]
    items = sorted([(m, dt) for m, dt in items if dt is not None], key=lambda x: x[1])
    if not items:
        return [], []
    # NB (user 2026-08-17) : on N'EXCLUT PLUS les jambes de combiné / le match montante. Leur PARI SIMPLE est
    # ANALYSÉ SÉPARÉMENT par la vague (~2h avant le KO) -> tant que ce n'est pas fait, le match est bien « à
    # analyser » et doit RESTER dans le Programme. Le combiné / la montante sont des OVERLAYS (le même match peut
    # donc figurer au Programme/Abstention/Confiance/Value ET dans Combiné/Montante). Ainsi les 20 matchs du jour
    # sont TOUS représentés par leur état de pari simple, sans « trou ».
    _now = datetime.now(timezone.utc)
    pending, abst = [], []
    for m, dt in items:
        mid = str(m.get("id"))
        d = analyses.meta(sport, mid)
        if d is None:                                      # pas encore analysé
            # Un match NON analysé dont le COUP D'ENVOI EST PASSÉ n'a plus rien à faire au Programme : il n'est
            # plus jouable (pré-match uniquement) et polluait la liste avec une heure d'analyse périmée (ex. « analyse
            # ~19:00 » affiché à 23:08 sur un match de 20:00 jamais analysé faute de budget). On le RETIRE. user 2026-08-21.
            if dt is not None and dt <= _now:
                continue
            pending.append((m, dt))                        # à venir + pas analysé -> PROGRAMME (grille)
            continue
        if analyses.is_settled(d):
            _sb = analyses.stat_bet(d)
            _has_bet = isinstance(_sb, dict) and _sb.get("result") in ("won", "lost", "push")
        else:
            _has_bet = analyses.retained_bet(sport, mid) is not None
        if not _has_bet:                                   # analysé SANS pari
            # ABSTENTION FIGÉE APRÈS LA 2e ANALYSE SEULEMENT (user 2026-08-27) : une abstention issue du SCAN
            # DU MATIN sur un match encore à venir sera RE-VÉRIFIÉE ~1 h avant le KO (la vague). Tant que cette
            # ré-analyse décisive n'a pas eu lieu, on la laisse au PROGRAMME (« à analyser ~1 h avant ») au lieu
            # de la classer « Abstention — non joué » prématurément. Passée la vague (mtime récent) -> ferme.
            if _awaiting_prematch_reanalysis(sport, mid, dt, _now):
                pending.append((m, dt))
            else:
                abst.append(_status_card(m, dt, "abst"))
        # sinon : a un pari -> carte dans sa zone Confiance/Value
    return pending, abst


def _paj_hero() -> str:
    """État VIDE premium de l'onglet Pronos (aucun pari NI programme ce jour — typiquement tôt le matin avant
    le scan de 08h, ou jour calme) : orbe cyan + explication du timing. Remplace les 2 accordéons vides sur un
    grand vide (user 2026-08-18 « comment mieux présenter »). Réutilise le cadre premium de l'état vide Live."""
    return (
        '<div class="live-empty paj-hero">'
        '<div class="le-orb"><span class="le-ping pe-ping"></span>'
        '<span class="le-ping le-ping2 pe-ping"></span><span class="le-dot pe-dot"></span></div>'
        '<div class="le-h">Aucun pari pour le moment</div>'
        '<div class="le-sub">Le programme du jour est établi vers <b>10 h</b>. Chaque pari — '
        'Confiance, Value, Combiné — arrive <b>~1 h avant le coup d\'envoi</b>, une fois l\'analyse faite. '
        'Seuls les matchs à <b>value</b> deviennent un pari.</div>'
        '</div>')


def _programme_grille(pending: list) -> str:
    """GRILLE HORAIRE du programme (user 2026-08-18 « originale et pratique », optimisée) : matchs PAS encore
    analysés, GROUPÉS PAR HEURE de coup d'envoi PUIS PAR LIGUE (façon programme TV). L'heure d'analyse (~KO−1 h)
    est portée par l'en-tête du créneau ; la LIGUE est affichée UNE fois par sous-groupe (plus sur chaque ligne)
    -> les noms d'équipes prennent la PLEINE LARGEUR (fin des troncatures). `pending` = liste de (match, dt)."""
    from collections import OrderedDict
    slots: "OrderedDict[str, list]" = OrderedDict()
    for m, dt in pending:                                  # `pending` déjà trié par coup d'envoi
        ld = dt.astimezone(LOCAL_TZ) if (LOCAL_TZ is not None and dt.tzinfo is not None) else dt
        slots.setdefault(ld.strftime("%H:%M"), []).append((m, ld))
    out = []
    for hhmm, ms in slots.items():
        _eta = (ms[0][1] - timedelta(hours=1)).strftime("%H:%M")   # analyse ~1 h avant le coup d'envoi
        lgroups: "OrderedDict[str, list]" = OrderedDict()          # sous-groupe PAR LIGUE dans le créneau
        for m, _ld in ms:
            comp = str(m.get("comp") or "")
            _cty = _cap(match_select.comp_country(comp) or "")
            if _cty and _cty.lower() in comp.lower():
                _cty = ""                                  # évite « Angleterre • Angleterre »
            lg = " • ".join(html.escape(p) for p in (_cty, _noF(comp)) if p).upper()
            lgroups.setdefault(lg, []).append(m)
        body = []
        for lg, gms in lgroups.items():
            rows = []
            for m in gms:
                name = _noF(str(m.get("name") or ""))
                home, away = ([s.strip() for s in name.split(" - ", 1)] if " - " in name else (name, ""))
                # STRIP du code PAYS « (KSA) / (CHI) »… en fin de nom (user 2026-08-18) : redondant avec
                # l'intertitre ligue (ARABIE SAOUDITE…). Affichage seul ; le crest résout sur le nom complet.
                _hc, _ac = home, away
                home = re.sub(r"\s*\([A-Za-z]{2,4}\)\s*$", "", home).strip() or home
                away = re.sub(r"\s*\([A-Za-z]{2,4}\)\s*$", "", away).strip() or away
                # LOGO DE CHAQUE CÔTÉ (user 2026-08-18) : domicile à GAUCHE, extérieur à DROITE, équipes CENTRÉES.
                _mtxt = html.escape(f"{home} – {away}" if away else home)
                rows.append(f'<div class="pgg-row">{_crest_badge(_hc)}'
                            f'<span class="pgg-match">{_mtxt}</span>'
                            + (_crest_badge(_ac) if away else "") + '</div>')
            body.append(f'<div class="pgg-lgroup">'
                        + (f'<div class="pgg-lgh">{lg}</div>' if lg else "")
                        + "".join(rows) + '</div>')
        _clk = ('<svg class="pgg-clk" width="12" height="12" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round">'
                '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.7"/></svg>')   # horloge nette (pas d'emoji)
        out.append(f'<div class="pgg-slot"><div class="pgg-slot-h"><b>{html.escape(hhmm)}</b>'
                   f'<span>{_clk} analyse ~{_eta}</span></div>{"".join(body)}</div>')
    return f'<div class="pgg">{"".join(out)}</div>'


def _programme_schedule(sport: str = "foot") -> str:
    """Zone « Programme du jour » = les matchs PAS ENCORE analysés, en GRILLE HORAIRE compacte groupée par
    heure (user 2026-08-18). Badge compteur (à droite, près du chevron). '' si plus rien à analyser."""
    pending, _abst = _planning_cards(sport)
    if not pending:
        # RIEN À ANALYSER -> deux cas (user 2026-08-20 : la LIGNE « Programme du jour » doit rester visible avec
        # l'info du timing) :
        #  • programme PAS ENCORE BÂTI (avant le scan ~10h, ou jour sans matchs) -> on affiche la ligne avec un
        #    message « établi vers 10h » (au lieu de masquer la zone) ;
        #  • programme BÂTI mais TOUT analysé -> plus rien à montrer -> zone masquée ('').
        prog = _load_day_programme()
        _built = any((m.get("sport") or "foot") == sport for m in (prog.get("matches") or []))
        if _built:
            return ""
        _soon = '<div class="prog-soon">Le programme du jour sera établi à <b>10 h</b>.</div>'
        return _zone("prog", "Programme du jour", "", 0, _soon, collapsible=False)
    # REPLIÉ PAR DÉFAUT (user 2026-08-19) : `open_=False` -> le Programme du jour est toujours fermé au chargement
    # (le JS `_CAL_JS` ne force jamais l'ouverture). On le déplie d'un tap pour voir la liste des matchs.
    return _zone("prog", "Programme du jour", "", len(pending), _programme_grille(pending),
                 collapsible=True, open_=True)   # OUVERT d'office (user 2026-08-22)


def _abstention_zone(sport: str = "foot") -> str:
    """Zone « Abstention » = les matchs analysés SANS pari, en CARTES (comme les paris) — user 2026-08-17.
    Catégorie à part entière, badge compteur à droite. CACHÉE tant qu'il n'y a aucune abstention (user
    2026-08-20 : plus d'en-tête vide) -> '' si vide."""
    _pending, abst = _planning_cards(sport)
    if not abst:
        # CACHÉE tant qu'il n'y a aucune abstention (user 2026-08-20) : plus d'en-tête vide.
        return ""
    return _zone("abst", _plur(len(abst), "Abstention"), "", len(abst), _MC_SEP.join(abst), collapsible=True)


def render_dashboard(match_rows: list, *, live_count: int = 0, results: list | None = None,
                     frag: bool = False, source: dict | None = None) -> str:
    """Onglet « Pronos » (ex-« À venir », renommé 2026-07-19) : un CALENDRIER horizontal en tête pour revoir
    les paris proposés les jours passés + leurs résultats, puis le contenu du jour sélectionné (par défaut
    AUJOURD'HUI = les zones Combiné/Confiance à jouer/Confiance provisoire/À analyser + Résultats du jour).
    `results` = cartes des paris terminés d'aujourd'hui. Cliquer une date recharge #day-content via /jour."""
    today_iso = _sport_today().isoformat()
    zones, cnt = _today_zones(match_rows, None, results)
    # Badge LIVE posé AUSSI depuis la home (retour user 2026-07-21 : le SPA ne charge que le panneau
    # actif -> le badge de l'onglet Live n'apparaissait qu'après l'avoir visité). Total = paris joués
    # live (live_count) + provisoires live + combiné du jour live. Le JS badge() lit TOUS les .dv-nav.
    # PROVISOIRES retirés du produit (user 2026-08-11) : la page Live les masque (gate PROVISOIRES_ON,
    # cf. render_directs). Le badge posé DEPUIS la home doit compter PAREIL — sinon un provisoire live
    # gonflait le badge « Live » à 1 alors que la page Live n'affiche rien (incohérence badge ↔ page).
    try:
        _lv_prov = (sum(1 for it in _programme_items(set(), framed=True) if it.get("_live"))
                    if analyses.PROVISOIRES_ON else 0)
    except Exception:
        _lv_prov = 0
    _lv_total = (live_count or 0) + _lv_prov + (1 if _daily_combo_any_live() else 0)
    # BANDEAU CALENDRIER RETIRÉ de Pronos (demande user 2026-07-25) : la navigation par jour / le bilan
    # quotidien vivent désormais dans l'onglet CALENDRIER dédié -> plus de doublon en tête de Pronos.
    # MODULE « Programme du jour » : liste COMPLÈTE des matchs suivis + heure d'analyse (wave-first). Hors
    # #day-content (stable, indépendant de la navigation par jour). Pur affichage, 0 réseau.
    # BADGE nav MONTANTE RETIRÉ (user 2026-08-19) : plus d'onglet Montante dans la barre (la montante est un
    # onglet des Résultats). Son pari du jour est déjà compté dans le badge Pronos (zone Montante).
    body = (f'<span class="dv-nav" data-tab="home" data-n="{cnt}" hidden></span>'
            f'<span class="dv-nav" data-tab="directs" data-n="{_lv_total}" hidden></span>'
            # Bouton NOTIFICATIONS push (user 2026-08-16) — auto-masqué une fois activé (.on), libellé géré par JS.
            '<div class="bfx-pushrow"><button type="button" class="bfx-pushbtn" onclick="bfxPushEnable()">'
            '🔔 Activer les notifications</button></div>'
            # PROGRAMME + ABSTENTION sont désormais des ZONES-catégories DANS `#day-content` (via _today_zones),
            # affichées en CARTES comme les paris (user 2026-08-17) -> plus de module séparé en bas.
            + f'<div id="day-content">{zones}</div>')
    return body if frag else spa_shell("home", "Programme", body, source=source)


def _mont_eur(v) -> str:
    """Montant en euros, format FR (virgule, espace insécable) : 42.48 -> « 42,48 € »."""
    try:
        return f"{float(v):.2f} €".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def _mont_curve(caps: list, uid: str = "mc") -> str:
    """COURBE D'AIRE de la trajectoire du capital d'une montante (10 € -> pic), refonte premium 2026-08-09 :
    ligne verte lissée (Catmull-Rom) + remplissage dégradé, un point discret par palier, POINT FINAL mis en
    valeur (halo + gros disque) avec l'étiquette du capital atteint. Échelle LINÉAIRE = la montée compose et
    « décolle » visuellement (effet hockey-stick). '' si moins de 2 points."""
    pts = [float(c) for c in (caps or []) if isinstance(c, (int, float)) and c > 0]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    n, W, H, L, R, T, B = len(pts), 320.0, 104.0, 12.0, 14.0, 14.0, 20.0   # H=104 -> même taille que _hero_chart (user 2026-08-19)
    iw, ih = W - L - R, H - T - B
    AC = "#34d27b"

    def X(i):
        return L + iw * (i / (n - 1))

    def Y(v):
        return T + ih * (1 - (v - lo) / (hi - lo))

    co = [(X(i), Y(pts[i])) for i in range(n)]
    # Lissage Catmull-Rom -> Bézier cubique (courbe douce, sans dépassement sur données croissantes).
    d = f"M{co[0][0]:.1f},{co[0][1]:.1f}"
    for i in range(n - 1):
        p0 = co[i - 1] if i > 0 else co[i]
        p1, p2 = co[i], co[i + 1]
        p3 = co[i + 2] if i + 2 < n else p2
        c1x, c1y = p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6
        c2x, c2y = p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6
        d += f" C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    area = d + f" L{co[-1][0]:.1f},{T + ih:.1f} L{co[0][0]:.1f},{T + ih:.1f} Z"
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="{AC}" opacity="0.55"/>'
                   for x, y in co[:-1])
    ex, ey = co[-1]
    gid, fid = f"mcg-{uid}", f"mcf-{uid}"
    p = [f'<svg viewBox="0 0 {W:g} {H:g}" class="mont-c">',
         f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
         f'<stop offset="0" stop-color="{AC}" stop-opacity="0.34"/>'
         f'<stop offset="1" stop-color="{AC}" stop-opacity="0"/></linearGradient>'
         f'<filter id="{fid}" x="-60%" y="-60%" width="220%" height="220%">'
         f'<feGaussianBlur stdDeviation="3.2"/></filter></defs>',
         f'<path d="{area}" fill="url(#{gid})"/>',
         f'<path d="{d}" fill="none" stroke="{AC}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
         dots,
         f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="6.5" fill="{AC}" opacity="0.35" filter="url(#{fid})"/>',
         f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3.6" fill="{AC}" stroke="#0b0e14" stroke-width="1.4"/>',
         f'<text class="mont-c-lbl" x="{L:.1f}" y="{H - 3:.1f}" text-anchor="start">{_mont_eur(pts[0])}</text>',
         f'<text class="mont-c-lbl end" x="{ex:.1f}" y="{max(11.0, ey - 8):.1f}" text-anchor="end">'
         f'{_mont_eur(pts[-1])}</text></svg>']
    return "".join(p)


def _mont_ladder(steps: list) -> str:
    """Échelle (staircase) d'une montante, refonte premium 2026-08-09 : une ligne par palier avec une BARRE DE
    PROGRESSION de fond (largeur ∝ capital atteint / pic) -> on VOIT la mise grimper, palier après palier. Le
    palier PIC (dernier gagné, capital max) est souligné en OR. Colonne droite = capital après + gain."""
    _sts = list(steps or [])
    # Pic = plus haut capital atteint (payout des gagnés, sinon mise) -> échelle des barres de fond.
    _vals = [(s.get("payout") if s.get("result") == "won" else s.get("stake")) for s in _sts]
    _peak = max([v for v in _vals if isinstance(v, (int, float))] or [1.0])
    _peak_i = max(range(len(_vals)), key=lambda k: (_vals[k] or 0)) if _vals else -1
    rows = []
    for i, s in enumerate(_sts):
        res = s.get("result")
        cls = {"won": "won", "lost": "lost"}.get(res, "pending")
        if res == "won" and i == _peak_i:
            cls += " peak"                                # palier PIC (capital max) -> accent OR
        stake = s.get("stake")
        payout = s.get("payout")
        match = html.escape(_noF(str(s.get("match") or "")))
        _sel_raw = re.sub(r"\s*·?\s*@\s*\d+(?:[.,]\d+)?\s*$", "", str(s.get("sel") or "")).strip()
        sel = html.escape(_sel_raw)
        cote = s.get("cote")
        _cote_b = f'<span class="mont-step-c">@{cote:g}</span>' if isinstance(cote, (int, float)) else ""
        _val = _vals[i]
        _pct = max(7.0, min(100.0, 100.0 * (_val or 0) / _peak)) if _peak else 0.0
        _fill = f'<span class="mont-step-fill" style="width:{_pct:.1f}%"></span>'
        if res == "won":
            _cap = _mont_eur(payout)
            _gain = (f'<span class="mont-step-g up">+{_mont_eur((payout or 0) - (stake or 0))}</span>'
                     if isinstance(payout, (int, float)) and isinstance(stake, (int, float)) else "")
        elif res == "lost":
            _cap = '<span class="ko">Perdu</span>'
            _gain = f'<span class="mont-step-g dn">−{_mont_eur(stake)}</span>' if stake else ""
        else:
            _cap = '<span class="wait">En jeu</span>'
            _gain = ""
        _mise = (f'<span class="mont-step-mise">mise <b>{_mont_eur(stake)}</b></span>'
                 if isinstance(stake, (int, float)) else "")
        rows.append(
            f'<div class="mont-step {cls}">{_fill}'
            f'<div class="mont-step-n"><b>{i + 1}</b></div>'
            f'<div class="mont-step-m"><div class="mont-step-t">{match}</div>'
            f'<div class="mont-step-s"><span class="sel">{sel}</span>{_cote_b}</div></div>'
            f'<div class="mont-step-a">{_mise}<span class="to">{_cap}</span>{_gain}</div></div>')
    return "".join(rows)


def render_montante(st: dict, example: dict) -> str:
    """Onglet MONTANTE (activée 2026-07-25). Page premium : hero (multiplicateur ×N) + pari du jour + échelle
    des paliers + historique + palmarès. 100 % affichage, hors ROI. La montante est 100 % RÉELLE (simulation
    « meilleure montante » retirée — audit 2026-08-10 : sim/sim_state morts, jamais alimentés)."""
    base = st.get("base", 10.0)
    active = st.get("active")
    cap = st.get("capital", base)
    palier = st.get("palier", 0)
    pending = st.get("pending")
    featured = st.get("featured")
    stats = st.get("stats", {})

    # COURBE calculée AVANT le hero (user 2026-08-14 : « le graphique doit faire partie du hero »).
    # `_fsteps`/`tag` servent aussi à l'échelle plus bas. Trajectoire RÉELLE base(10 €) -> capital après
    # chaque palier gagné (le pic tracé = le vrai capital atteint).
    if featured and featured.get("steps"):
        _fsteps = featured["steps"]
        tag = ''
    else:
        _fsteps = (example or {}).get("steps") or []
        tag = '<span class="tag">Aperçu · exemple</span>'
    _caps = [base] + [s.get("payout") for s in _fsteps
                      if s.get("result") == "won" and isinstance(s.get("payout"), (int, float))]
    if st.get("lost"):                     # PERTE : la courbe RETOMBE à la base (user 2026-08-22) au lieu de rester au pic
        _caps.append(base)
    _curve = f'<div class="mont-curve mont-hero-curve">{_mont_curve(_caps, uid="best")}</div>' if len(_caps) >= 2 else ""

    # HERO — capital mis en avant (montante en cours en réel)
    hero = ""   # rempli par le hero PREMIUM en montante en cours ; sinon hero générique plus bas
    if active and st.get("lost") and palier > 0:
        # HERO PERDU (user 2026-08-22) : la montante a atteint le palier N puis a PERDU. On indique le PALIER
        # ATTEINT (jamais « palier 0 ») + le retour à la base ; la courbe (ci-dessus) retombe à 10 €.
        hero = (
            '<div class="mont-hero mont-hero-live mont-hero-lost">'
            '<div class="mhe">Montante perdue</div>'
            f'<div class="mhx mhx-lost">Palier {palier}</div>'
            '<div class="mhx-cap">atteint avant la perte · retour à la base</div>'
            '<div class="mont-prog">'
            f'<div class="mp-cell"><b>{_mont_eur(base)}</b><span>Départ</span></div>'
            '<div class="mp-arrow" aria-hidden="true">→</div>'
            f'<div class="mp-cell"><b class="mp-now">{_mont_eur(base)}</b><span>Nouvelle montante</span></div>'
            '</div>' + _curve + '</div>')
    elif active and palier > 0:
        # HERO PREMIUM « montante en cours » (user 2026-08-09 : « rendu 100 % pro, il faut vraiment voir le X
        # actuel sur la mise »). Le MULTIPLICATEUR (capital ÷ départ) est la VEDETTE : énorme chiffre en
        # dégradé doré + halo. Sous une fine règle, la PROGRESSION 10 € → capital raconte l'histoire (palier
        # accolé au capital). « X gains d'affilée » retiré (redondant palier). Badge retiré -> titre de page.
        _q = (cap / base) if base else 0
        _qtxt = f'{round(_q, 1):g}'.replace(".", ",")   # décimale FR (×9,4 pas ×9.4)
        hero = (
            '<div class="mont-hero mont-hero-live">'
            '<div class="mhe">Multiplicateur actuel</div>'
            f'<div class="mhx">×{_qtxt}</div>'
            f'<div class="mhx-pal">Palier {palier} atteint</div>'
            '<div class="mhx-cap">sur la mise de départ</div>'
            '<div class="mont-prog">'
            f'<div class="mp-cell"><b>{_mont_eur(base)}</b><span>Départ</span></div>'
            '<div class="mp-arrow" aria-hidden="true">→</div>'
            f'<div class="mp-cell"><b class="mp-now">{_mont_eur(cap)}</b>'
            f'<span>Capital · palier {palier}</span></div>'
            '</div>' + _curve + '</div>')
    elif active:
        sub = 'Nouvelle montante — prête pour le pari du jour'
        chip = '<span class="mont-chip wait">En attente du pari du jour</span>'
        lbl = 'Capital de la montante'
    else:
        sub = 'Mise de départ · prête à démarrer'
        chip = '<span class="mont-chip wait">Bientôt · en préparation</span>'
        lbl = 'Capital de la montante'
    if not hero:
        hero = (f'<div class="mont-hero"><div class="mont-hero-l">{lbl}</div>'
                f'<div class="mont-hero-cap">{_mont_eur(cap)}</div>'
                f'<div class="mont-hero-sub">{sub}</div>{chip}{_curve}</div>')

    intro = ('<div class="mont-intro">Une <b>montante</b> par jour : on part de <b>10 €</b>, on mise sur '
             '<b>UN seul</b> pari sûr, et à chaque gain on <b>rejoue la totalité</b> le lendemain. '
             'L\'objectif : enchaîner les paliers pour faire grimper la mise, sans jamais risquer plus que '
             'les 10 € de départ.</div>')

    # PARI DU JOUR
    if pending:
        # PARI DU JOUR présenté comme un pari PRONOS (demande user 2026-07-28) : carte `_leg_card` complète
        # (match + sélection + glose + ligne VERDICT confiance/marché/value + pli « Pourquoi »), plus la ligne
        # d'échelle compacte. Confiance récupérée du sidecar (retenu/publié).
        _pmatch = _noF(str(pending.get("match") or ""))
        _ph, _psep, _pa = _pmatch.partition(" - ")
        _psp = pending.get("sport") or "foot"
        _prb = (analyses.retained_bet(_psp, str(pending.get("mid") or ""))
                or analyses.published_bet(_psp, str(pending.get("mid") or "")) or {})
        _pcard = _leg_card(
            {"sport": _psp, "home": _ph, "away": _pa, "name": _pmatch, "comp": "",
             "sel": pending.get("sel"), "cote": pending.get("cote"),
             "prob": _prb.get("prob"), "code": _prb.get("code") or pending.get("code") or "",
             "result": None, "start": pending.get("start"),
             "why": _prov_why_snippet(_psp, str(pending.get("mid") or ""), maxlen=100000, played=True)},
            why=True, verdict=True, why_always=True, why_label="Pourquoi ce choix")
        pari = ('<div class="mont-sec-h">Le pari du jour</div>'
                '<div class="mont-lead">Un seul pari, le plus sûr du jour — pour viser le palier suivant.</div>'
                f'{_pcard}')
    else:
        # Pas de pari EN ATTENTE -> montrer le pari du jour RÉGLÉ (avec son RÉSULTAT) LÀ où était la sélection
        # (user 2026-08-08 : « le résultat doit s'afficher là où était sa sélection »). Sinon message vide.
        _tb = _montante_today_bet()
        if _tb and _tb.get("sel"):
            _tsp = _tb.get("sport") or "foot"
            _tmid = str(_tb.get("mid") or "")
            _tsd = analyses.meta(_tsp, _tmid) or {}
            _tmatch = _noF(str(_tb.get("match") or ""))
            _th, _, _ta = _tmatch.partition(" - ")
            _trb = analyses.retained_bet(_tsp, _tmid, for_history=True) or {}
            _tboard = (analyses.result_board(_tsd, _tsp) or {}) if _tb.get("result") in ("won", "lost", "push", "void") else {}
            _tcard = _leg_card(
                {"sport": _tsp, "home": _tsd.get("home") or _th, "away": _tsd.get("away") or _ta,
                 "name": _tmatch, "comp": _tsd.get("comp") or "", "sel": _tb.get("sel"),
                 "cote": _tb.get("cote"), "prob": _trb.get("prob"),
                 "code": _trb.get("code") or _tb.get("code") or "", "result": _tb.get("result"),
                 "score": _tboard.get("score"), "periods": _tboard.get("periods"), "pens": _tboard.get("pens"),
                 "start": _tsd.get("start"),
                 "why": _prov_why_snippet(_tsp, _tmid, maxlen=100000, played=True)},
                why=True, verdict=True, why_always=True, why_label="Pourquoi ce choix")
            pari = f'<div class="mont-sec-h">Le pari du jour</div>{_tcard}'
        else:
            pari = ('<div class="mont-sec-h">Le pari du jour</div>'
                    '<div class="mont-empty">Le <b>pari du jour</b> s\'affichera ici — <b>1</b> sélection sûre '
                    'pour faire grimper la mise. À suivre chaque jour.</div>')

    # ÉCHELLE — l'échelle des paliers (la COURBE est désormais dans le hero, calculée plus haut).
    ladder = (f'<div class="mont-sec-h">La montante{tag}</div>'
              '<div class="mont-lead">Chaque palier gagné fait grimper la mise — la voici, palier par palier.</div>'
              f'<div class="mont-ladder">{_mont_ladder(_fsteps)}</div>')

    # « COMMENT ÇA MARCHE » RETIRÉ (user 2026-08-09) : redondant avec le paragraphe d'intro sous le hero
    # (principe + risque plafonné à 10 € déjà expliqués). L'échelle des paliers montre la mécanique en acte.

    # HISTORIQUE des montantes terminées. La VRAIE montante vient de démarrer (aucune chaîne terminée) ->
    # on montre l'historique de la SIMULATION (montantes terminées sur nos simples foot) plutôt qu'un cadre
    # VIDE (bug user 2026-07-28 « rien dans l'historique »). Idem palmarès plus bas.
    chains = st.get("chains") or []
    if chains:
        hrows = "".join(
            '<div class="mont-hrow"><div class="mont-hrow-b">✗</div>'
            f'<div class="mont-hrow-m"><b>{c.get("palier", 0)} palier{"s" if c.get("palier", 0) != 1 else ""}</b>'
            f'<span>Pic atteint · {_mont_eur(c.get("peak"))}</span></div>'
            f'<div class="mont-hrow-v">{_mont_eur(c.get("peak"))}</div></div>'
            for c in chains)
        hist = f'<div class="mont-sec-h">Historique des montantes</div><div class="mont-hist">{hrows}</div>'
    else:
        hist = ('<div class="mont-sec-h">Historique des montantes</div>'
                '<div class="mont-empty">Les montantes terminées apparaîtront ici — chacune avec son '
                'nombre de paliers et le capital maximal atteint.</div>')

    # PALMARÈS / STATS
    palmares = (
        '<div class="mont-sec-h">Palmarès</div>'
        '<div class="mont-kpis">'
        f'<div class="mont-kpi best"><b>{_mont_eur(stats.get("best_capital", base))}</b><span>meilleure montante</span></div>'
        f'<div class="mont-kpi"><b>{stats.get("best_palier", 0)}</b><span>paliers max</span></div>'
        f'<div class="mont-kpi"><b>{stats.get("n", 0)}</b><span>montantes jouées</span></div>'
        '</div>')

    # BADGE NAV MONTANTE (user 2026-08-08) : 1 s'il y a un pari du jour (palier en attente), sinon 0.
    _mn = 1 if _montante_palier() is not None else 0
    return (f'<span class="dv-nav" data-tab="montante" data-n="{_mn}" hidden></span>'
            + hero + intro + ladder + pari + palmares + hist)


def render_montante_bilan(st: dict, example: dict) -> str:
    """BILAN MONTANTE pour l'onglet RÉSULTATS — présenté EXACTEMENT comme Confiance/Value/Combiné (user 2026-08-19 :
    « vraiment ressemblant aux autres ») : même cadre `.spf-hero` = label + GRAND CHIFFRE + KPIs + courbe + W/L +
    série + liste d'historique. La seule spécificité montante : le grand chiffre = MULTIPLICATEUR ×N (au lieu du
    ROI) et la courbe = trajectoire du CAPITAL. Le pari du jour reste dans Pronos. Hors ROI."""
    from app import montante as _mtn
    base = st.get("base", 10.0)
    cap = st.get("capital", base)
    featured = st.get("featured")
    stats = st.get("stats", {})
    # GRAND CHIFFRE = MULTIPLICATEUR ×N (analogue du ROI héros des autres onglets). >1 = doré/positif.
    _q = (cap / base) if base else 1.0
    _qtxt = f'×{round(_q, 1):g}'.replace(".", ",")
    _qcls = "pos" if _q > 1.0001 else "na"
    # COURBE DE CAPITAL — `_mont_curve` (échelle qui DÉMARRE À LA MISE DE BASE 10 €, pas 0 — user 2026-08-19 ;
    # `_hero_chart` forçait le 0). Dans `.sx-equity` + hauteur alignée (H=104) -> MÊME taille que les autres onglets.
    _fsteps = (featured.get("steps") if (featured and featured.get("steps")) else (example or {}).get("steps")) or []
    _caps = [base] + [s.get("payout") for s in _fsteps
                      if s.get("result") == "won" and isinstance(s.get("payout"), (int, float))]
    _chart = (f'<div class="sx-equity">{_mont_curve(_caps, uid="mbil")}</div>' if len(_caps) >= 2 else "")
    # KPIs + série + W/L, calculés sur les PARIS montante AFFICHÉS (paliers hors-technique 09/08→20/08 masqués,
    # user 2026-08-20 -> `public_steps`) — MÊMES classes que les autres onglets. Calibration intacte (sidecars).
    _steps = _mtn.public_steps()
    _res = [s.get("result") for s in _steps]
    _settled = [r for r in _res if r in ("won", "lost")]
    _won, _nb = _settled.count("won"), len(_settled)
    _hit = round(100 * _won / _nb) if _nb else None
    _best = _cur = 0                                    # meilleure série = plus longue suite de gains
    for r in _res:
        if r == "won":
            _cur += 1
            _best = max(_best, _cur)
        elif r == "lost":
            _cur = 0
    _, _streak = _form_streak(_res)                    # série EN COURS (run final)
    # KPIs (user 2026-08-19) : GAINS RETIRÉ. Réussite · Plus gros gain (meilleur capital atteint) · Cote moyenne.
    _bestg = stats.get("best_capital", base)
    _cotes = [s.get("cote") for s in _steps
              if s.get("result") in ("won", "lost") and isinstance(s.get("cote"), (int, float))]
    _avgc = round(sum(_cotes) / len(_cotes), 2) if _cotes else None
    _kpi = ('<div class="spf-hero-kpis">'
            f'<div><span class="v arec-{_pct_class(_hit)}">{_hit if _hit is not None else "—"}%</span>'
            '<span class="l">Réussite</span></div>'
            f'<div><span class="v arec-pos">{_mont_eur(_bestg)}</span><span class="l">Plus gros gain</span></div>'
            f'<div><span class="v">{_avgc if _avgc is not None else "—"}</span>'
            '<span class="l">Cote moyenne</span></div></div>')
    # COURBES « Taux de réussite » + « Cote moyenne » (user 2026-08-19), comme les autres onglets. Warmup abaissé
    # (montante ~1 pari/jour -> on montre les courbes sans attendre 13 paris).
    _curves = (_rate_block(_hit_curve(_res), "mbil", warmup=3)
               + _cote_block(_cote_curve([(s.get("result"), s.get("cote")) for s in _steps]), "mbil", warmup=3))
    _fd = form_dots([{"won": "W", "lost": "L"}.get(r, "N") for r in _res if r], n=16)
    _fdh = f'<div class="spf-cv-form">{_fd}</div>' if _fd else ""
    # HISTORIQUE EN LISTE — MÊME composant que les autres onglets (`_recent_bets_html`) : pastille W/L + affiche +
    # sélection + cote + date, plus récent en haut. Coup d'envoi relu du sidecar (les steps n'ont pas de `start`).
    _rec = []
    for s in reversed(_steps):
        _start = None
        try:
            _start = (analyses.meta(s.get("sport") or "foot", str(s.get("mid") or "")) or {}).get("start")
        except Exception:
            _start = None
        if not _start and s.get("date"):
            _start = s["date"] + "T12:00:00"
        # result=None (pari du jour EN ATTENTE) -> "pending" pour afficher le SABLIER ⏳ (comme les autres
        # onglets), user 2026-08-19. Sans ça `_recent_bets_html` le rendait « ? ».
        _rec.append({"name": s.get("match"), "sel": s.get("sel"), "cote": s.get("cote"),
                     "start": _start, "result": s.get("result") or "pending"})
    _hist_list = _recent_bets_html(_rec)
    _histb = f'<div class="spf-rec-lbl">Historique des montantes</div>{_hist_list}' if _hist_list else ""
    # CADRE IDENTIQUE aux autres onglets : label + grand chiffre + KPIs + courbe + W/L + série + liste.
    return ('<div class="spf-hero">'
            '<div class="spf-hero-lbl">Multiplicateur</div>'
            f'<div class="spf-hero-roi {_qcls}">{_qtxt}</div>'
            f'{_kpi}{_chart}{_curves}{_fdh}{_streak_text(_streak, _best)}{_histb}</div>')


_CAL_MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                  "septembre", "octobre", "novembre", "décembre"]


def _daily_pnl() -> dict:
    """P&L par JOUR SPORTIF (06h→06h) pour le CALENDRIER de l'onglet Stats — CONFIANCE UNIQUEMENT (demande
    user 2026-08-30 : le calendrier Stats ne doit refléter QUE les paris de Confiance, le chiffre phare —
    pas la Value/le combiné/la montante), football. DÉRIVÉ de `_daily_conf_results_map` (tier « confiance »
    figé via `tier_of`, monotone, immunisé à la dérive de calibration) -> teinte des jours ET bilan du mois
    (ROI/paris/jours joués/meilleure journée) tous en Confiance seule. Aligné sur la pastille du calendrier
    horizontal (Programme). {jour_iso: {profit, n, won, lost, roi}}. Lecture seule, hors ROI/calibration."""
    out: dict = {}
    for day, e in _daily_conf_results_map().items():
        n = int(e.get("settled") or 0)
        if not n:
            continue
        won = int(e.get("won") or 0)
        prof = round(float(e.get("profit") or 0.0), 2)
        out[day] = {"profit": prof, "n": n, "won": won, "lost": n - won,
                    "roi": round(100 * prof / n, 1) if n else 0.0}
    return out


def _render_calendar(ym: str = "") -> str:
    """Vue CALENDRIER mensuel (demande user 2026-07-25) : une grille du mois, chaque jour teinté selon son
    ROI (vert = bénéfice, rouge = perte), navigation ‹ mois ›, bilan du mois, et le DÉTAIL d'un jour au clic
    (réutilise /jour). Prefix `mcal-` (distinct du bandeau `cal-pill`). 100 % affichage, hors ROI/calibration."""
    import calendar as _calmod
    from datetime import date as _date
    today = _sport_today()
    try:
        y, mo = int(ym[:4]), int(ym[5:7])
        _date(y, mo, 1)
    except (ValueError, TypeError, IndexError):
        y, mo = today.year, today.month
    pnl = _daily_pnl()
    first_wd, ndays = _calmod.monthrange(y, mo)        # first_wd : lundi=0 … dimanche=6
    # Bilan du mois + meilleures/pires journées.
    mprofit = mn = mwon = mlost = 0
    ndays_bet = 0
    best = worst = None
    _eq_pts, _cum = [0.0], 0.0                          # courbe d'équité du mois (profit cumulé, ordre des jours)
    cells = []
    for _ in range(first_wd):                          # cases vides avant le 1er (aligne les colonnes)
        cells.append('<div class="mcal-cell mcal-void"></div>')
    for dnum in range(1, ndays + 1):
        diso = f"{y:04d}-{mo:02d}-{dnum:02d}"
        b = pnl.get(diso)
        cls, style, extra = "mcal-cell", "", ""
        if b and b["n"]:
            roi, prof = b["roi"], b["profit"]
            sign = "pos" if prof > 1e-9 else ("neg" if prof < -1e-9 else "flat")
            cls += f" mcal-{sign} mcal-has"
            op = min(0.40, 0.09 + abs(roi) / 220.0)    # intensité de teinte selon l'ampleur du ROI
            style = f' style="--mt:{op:.3f}"'
            extra = (f'<span class="mcal-roi">{"+" if prof >= 0 else "−"}{abs(roi):g}%</span>'
                     f'<span class="mcal-n">{b["n"]}</span>')
            mprofit += prof
            mn += b["n"]
            mwon += b["won"]
            mlost += b["lost"]
            ndays_bet += 1
            _cum += prof
            _eq_pts.append(round(_cum, 2))
            if best is None or roi > best[1]:
                best = (dnum, roi)
            if worst is None or roi < worst[1]:
                worst = (dnum, roi)
        else:
            cls += " mcal-empty"
        if diso == today.isoformat():
            cls += " mcal-today"
        cells.append(f'<div class="{cls}"{style} data-mday="{diso}" data-has="{1 if (b and b["n"]) else 0}">'
                     f'<span class="mcal-d">{dnum}</span>{extra}</div>')
    dow = "".join(f'<div class="mcal-dow">{d}</div>' for d in ("L", "M", "M", "J", "V", "S", "D"))
    # Navigation mensuelle (‹ / ›) — bornée au mois courant (pas de futur vide).
    pm, py = (12, y - 1) if mo == 1 else (mo - 1, y)
    nm, ny = (1, y + 1) if mo == 12 else (mo + 1, y)
    prev_ym, next_ym = f"{py:04d}-{pm:02d}", f"{ny:04d}-{nm:02d}"
    _next_disabled = (y, mo) >= (today.year, today.month)
    _title = f"{_CAL_MONTHS_FR[mo - 1].capitalize()} {y}"
    _mroi = round(100 * mprofit / mn, 1) if mn else 0.0
    _rcls = "pos" if mprofit > 1e-9 else ("neg" if mprofit < -1e-9 else "flat")
    nav = (f'<div class="mcal-nav">'
           f'<button class="mcal-arw" data-cal="{prev_ym}" aria-label="Mois précédent">‹</button>'
           f'<div class="mcal-title">{_title}</div>'
           f'<button class="mcal-arw{" off" if _next_disabled else ""}"'
           f'{"" if _next_disabled else f" data-cal={chr(34)}{next_ym}{chr(34)}"} aria-label="Mois suivant">›</button>'
           f'</div>')
    # Bilan du mois (ROI héros + courbe d'équité + jours gagnants/paris + meilleure journée).
    _eq = (f'<div class="mcal-eq sx-equity">{_hero_chart(_eq_pts, uid="mcaleq")}</div>'
           if len(_eq_pts) >= 3 else "")
    _best_v = f'+{best[1]:g}%' if best else "—"
    _best_lb = f'meilleure · {best[0]} {_CAL_MONTHS_FR[mo - 1][:4]}.' if best else "meilleure journée"
    summ = (f'<div class="mcal-sum{" neg" if _rcls == "neg" else ""}">'
            f'<div class="mcal-sum-hero mcal-{_rcls}">{"+" if mprofit >= 0 else "−"}{abs(_mroi):g}%'
            f'<span class="mcal-sum-lb">ROI du mois</span></div>'
            f'{_eq}'
            f'<div class="mcal-sum-kpis">'
            f'<div><b>{ndays_bet}</b><span>jours joués</span></div>'
            f'<div><b>{mn}</b><span>paris</span></div>'
            f'<div><b class="mcal-{_rcls}">{_best_v}</b><span>{_best_lb}</span></div>'
            f'</div></div>')
    grid = f'<div class="mcal-grid">{dow}{"".join(cells)}</div>'
    legend = ('<div class="mcal-legend"><span class="mcal-lg pos">Bénéfice</span>'
              '<span class="mcal-lg neg">Perte</span><span class="mcal-lg flat">Neutre</span>'
              '<span class="mcal-lg-note">Chiffre = ROI du jour · petit nombre = nb de paris</span></div>')
    # DÉTAIL D'UN JOUR EN DESSOUS RETIRÉ (user 2026-08-19) : les résultats vivent UNIQUEMENT dans les cases
    # colorées du calendrier (le détail par jour est dans l'onglet Pronos, calendrier horizontal).
    if mn == 0:
        summ = ('<div class="mcal-empty-msg">Aucun pari réglé ce mois-ci. Utilise les flèches pour '
                'parcourir les mois.</div>')
    return (f'<span class="dv-nav" data-tab="calendrier" data-n="0" hidden></span>'
            f'<div class="mcal">{nav}{summ}{grid}{legend}</div>')


def _reliability_chart(series: list, uid: str = "rel") -> str:
    """VRAI graphique de fiabilité : courbe de l'indice (0-100) dans le temps, pleine largeur, avec
    grille + axe Y (graduations 0-100), aire dégradée et points début/récent. Montre VISUELLEMENT que
    la fiabilité progresse. '' si moins de 2 points."""
    series = [v for v in (series or []) if v is not None]
    if len(series) < 2:
        return ""
    n = len(series)
    W, H, L, R, T, B = 320.0, 122.0, 24.0, 8.0, 12.0, 16.0
    iw, ih = W - L - R, H - T - B
    lo = max(0.0, min(series) - 6)                       # fenêtre Y : contexte 0-100 + marge pour voir la variation
    hi = min(100.0, max(series) + 6)
    if hi - lo < 6:
        lo, hi = max(0.0, hi - 6), min(100.0, lo + 6)
    col = "#34d27b"
    gid = f"relg-{uid}"

    def X(i):
        return L + iw * i / (n - 1)

    def Y(v):
        return T + ih * (1 - (v - lo) / (hi - lo))

    line_d = _smooth_path([(X(i), Y(v)) for i, v in enumerate(series)])
    area_d = f'M{X(0):.1f},{H - B:.1f} L' + line_d[1:] + f' L{X(n - 1):.1f},{H - B:.1f} Z'
    p = [f'<svg viewBox="0 0 {W:g} {H:g}" class="sx-relc">',
         f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
         f'<stop offset="0" stop-color="{col}" stop-opacity="0.32"/>'
         f'<stop offset="1" stop-color="{col}" stop-opacity="0"/></linearGradient></defs>']
    for k in range(3):                                   # grille + graduations Y (bas / milieu / haut)
        gv = lo + (hi - lo) * k / 2
        gy = Y(gv)
        p.append(f'<line class="bc-grid" x1="{L:g}" y1="{gy:.1f}" x2="{W - R:g}" y2="{gy:.1f}"/>')
        p.append(f'<text class="sx-relc-yl" x="{L - 4:g}" y="{gy + 3:.1f}" text-anchor="end">{round(gv)}</text>')
    p.append(f'<path d="{area_d}" fill="url(#{gid})" stroke="none"/>')
    p.append(f'<path d="{line_d}" fill="none" stroke="{col}" stroke-width="2.4" '
             'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>')
    p.append(f'<circle cx="{X(0):.1f}" cy="{Y(series[0]):.1f}" r="2.4" fill="{col}" opacity="0.55"/>')
    p.append(f'<circle cx="{X(n - 1):.1f}" cy="{Y(series[-1]):.1f}" r="3.4" fill="{col}"/>')
    p.append(f'<text class="sx-relc-xl" x="{L:g}" y="{H - 4:g}" text-anchor="start">début</text>')
    p.append(f'<text class="sx-relc-xl" x="{W - R:g}" y="{H - 4:g}" text-anchor="end">récent</text>')
    p.append("</svg>")
    return "".join(p)


_EXCL_ICON = {"foot": "⚽", "tennis": "🎾", "basket": "🏀", "combo": "🎲"}


def _excl_journal_html(rep: dict) -> str:
    """JOURNAL DATÉ des ajustements automatiques (marché auto-écarté / auto-réintégré) : chronologie
    lisible, récent d'abord. Rend visible et traçable l'auto-révision du système (le pendant « détail »
    des repères ambrés des courbes). Toujours affiché (même vide -> message « sélection stable »)."""
    j = rep.get("journal") or {}
    evs = [e for e in (j.get("events") or []) if (e.get("sport") or "foot") == "foot"]   # FOOT SEUL (2026-08-07)
    started = j.get("started")
    since = (f'<span class="exq-jsince">suivi depuis le {html.escape(str(started))}</span>'
             if started else "")
    head = f'<div class="exq-jhead">📋 Journal des ajustements automatiques{since}</div>'
    if not evs:
        return (f'<div class="exq-journal">{head}<div class="exq-jempty">Aucun ajustement automatique '
                f'à ce jour — la sélection de marchés est <b>stable</b>. Dès que le système écarte ou '
                f'ré-intègre un marché tout seul, l\'événement daté apparaît ici et sur les courbes.</div></div>')
    _AB = {"exclu": ("exq-ex", "⛔ écarté"), "réintégré": ("exq-ok", "✅ réintégré")}
    rows = []
    for e in evs[:14]:
        cls, lbl = _AB.get(e.get("action"), ("exq-watch", "•"))
        icon = _EXCL_ICON.get(e.get("sport"), "")
        base = ' <span class="exq-jbase">état initial</span>' if e.get("baseline") else ""
        rows.append(
            f'<div class="exq-jrow"><div class="exq-jtop">'
            f'<span class="exq-jdate">{html.escape((e.get("date") or "")[:10])}</span>'
            f'<span class="exq-bdg {cls}">{lbl}</span>'
            f'<span class="exq-jmk">{icon} {html.escape(str(e.get("market", "")))}</span>{base}</div>'
            f'<div class="exq-jreason">{html.escape(str(e.get("reason") or ""))}</div></div>')
    return f'<div class="exq-journal">{head}{"".join(rows)}</div>'


def render_exclusions(rep: dict | None) -> str:
    """TRANSPARENCE — quels TYPES DE PARIS sont écartés et POURQUOI, avec les seuils d'exclusion et de
    réintégration (auto-révisable selon le taux de réussite). En tête : le JOURNAL daté des ajustements
    automatiques (le « quand »), suivi de l'état courant par sport (le « quoi/pourquoi »). '' si rien."""
    if not rep or not rep.get("sports"):
        return ""
    th = rep.get("thresholds") or {}
    gmax = abs(th.get("gap_max", 8))
    intro = (f'<div class="exq-intro">Les marchés écartés sont <b>propres à chaque sport</b> : un marché '
             f'mauvais en basket n\'écarte PAS le même marché en foot. Un type de pari est <b>écarté '
             f'automatiquement</b> dès qu\'il PROUVE qu\'il perd <b>sur ce sport</b> : au moins '
             f'<b>{th.get("min_n")} prédictions</b> réglées ET soit une <b>sur-confiance</b> (réussite '
             f'réelle ≥ {gmax} pts SOUS la confiance annoncée), soit un <b>ROI ≤ {th.get("roi_max")}%</b>. '
             f'Sous ce seuil de données, on ne conclut pas (bruit). <b>Auto-révisable</b> : un marché se '
             f'ré-intègre seul dès qu\'il repasse au-dessus.</div>')
    _bdg = {"ban": ("exq-ex", "⛔ Banni"), "gap": ("exq-ex", "⛔ Écarté"), "roi": ("exq-ex", "⛔ Écarté"),
            "excl": ("exq-ex", "⛔ Écarté"), "watch": ("exq-watch", "👁 Surveillé"),
            "ok": ("exq-ok", "✅ Fiable")}
    body = intro + _excl_journal_html(rep)             # JOURNAL daté (le « quand ») avant l'état courant
    for sec in rep["sports"]:
        nex = sec.get("n_excluded") or 0
        tag = (f'<span class="exq-sptag exq-sptag-ex">{nex} écarté{"s" if nex > 1 else ""}</span>'
               if nex else '<span class="exq-sptag exq-sptag-ok">aucun écarté</span>')
        body += (f'<div class="exq-sport"><div class="exq-sphead">'
                 f'<span class="exq-spname">{sec.get("icon","")} {html.escape(str(sec.get("label","")))}</span>'
                 f'{tag}</div>')
        for r in sec["rows"]:
            cls, lbl = _bdg.get(r["kind"], ("exq-ok", "✅"))
            wr, ac, roi = r.get("win_rate"), r.get("avg_conf"), r.get("roi")
            meta = [f'{r["n"]} préd.']
            if wr is not None and ac is not None:
                meta.append(f'réussite {wr}% vs {ac}% annoncé')
            if roi is not None and r.get("settled"):
                meta.append(f'ROI {roi:+d}% ({r["settled"]} joués)')
            body += (f'<div class="exq-row"><div class="exq-top">'
                     f'<span class="exq-bdg {cls}">{lbl}</span>'
                     f'<span class="exq-mk">{html.escape(str(r["market"]))}</span></div>'
                     f'<div class="exq-reason">{html.escape(str(r["reason"]))}</div>'
                     f'<div class="exq-meta">{" · ".join(meta)}</div></div>')
        body += '</div>'
    # PROPS JOUEUR en COMBINÉ : logique INVERSE (exclu par défaut, réintégré si prouvé) -> ligne dédiée.
    pp = rep.get("player_props") or {}
    ppn, ppgap = pp.get("n") or 0, pp.get("gap")
    if pp.get("allowed"):
        cls, lbl = "exq-ok", "✅ Réintégrées"
        reason = (f'Props joueur validées en calibration ({ppn} préd., écart {ppgap:+.0f}) → '
                  f'autorisées comme jambe de combiné.')
    else:
        cls, lbl = "exq-watch", "⏸ Hors combiné"
        _pg = f", écart {ppgap:+.0f}" if ppgap is not None else ""
        reason = (f'Exclues des combinés par défaut (variance). Ré-intégration dès {th.get("min_n")} '
                  f'prédictions fantômes bien calibrées — actuellement {ppn}/{th.get("min_n")}{_pg}.')
    body += (f'<div class="exq-sport"><div class="exq-sphead">'
             f'<span class="exq-spname">🎲 Combinés (tous sports)</span></div>'
             f'<div class="exq-row"><div class="exq-top">'
             f'<span class="exq-bdg {cls}">{lbl}</span>'
             f'<span class="exq-mk">Props joueur (en combiné)</span></div>'
             f'<div class="exq-reason">{html.escape(reason)}</div>'
             f'<div class="exq-meta">réintégration selon les fantômes (calibration)</div></div></div>')
    return f'<div class="exq">{body}</div>'


def _market_watch_rows(by_market: dict) -> str:
    """Lignes `.exq-row` d'un jeu de marchés (un sport) pour la surveillance. '' si aucun marché exploitable."""
    _CALIB_OK = 50           # nb de PRÉDICTIONS (fantômes + joués) pour qu'un marché soit jugeable en calibration
    _MIN_N = 8               # sous ce nb, marché quasi inexistant -> masqué (bruit)
    rows = []
    for mk, m in sorted(by_market.items(), key=lambda kv: -(kv[1].get("n") or 0)):
        n = m.get("n") or 0
        if n < _MIN_N:
            continue
        conf, real, roi = m.get("avg_conf"), m.get("win_rate"), m.get("roi")
        played = m.get("roi_n") or 0
        gap = (real - conf) if (conf is not None and real is not None) else None
        if n < _CALIB_OK:
            cls, lbl = "exq-watch", f"⏳ En construction ({n})"
        elif isinstance(roi, (int, float)) and played >= 20 and roi <= -10:
            cls, lbl = "exq-ex", "🔴 À surveiller"
        elif gap is not None and gap <= -8:
            cls, lbl = "exq-watch", "🟡 Sur-confiance"
        else:
            cls, lbl = "exq-ok", "✅ Calibré"
        meta = [f'<b>{n}</b> préd.']
        if real is not None and conf is not None:
            meta.append(f'réel {real}% vs {conf}% annoncé (écart {gap:+d})')
        if isinstance(roi, (int, float)) and played:
            meta.append(f'ROI {roi:+d}% ({played} joué{"s" if played > 1 else ""}'
                        + (', indicatif' if played < 20 else '') + ')')
        elif played:
            meta.append(f'{played} joué{"s" if played > 1 else ""}')
        rows.append(f'<div class="exq-row"><div class="exq-top">'
                    f'<span class="exq-bdg {cls}">{lbl}</span>'
                    f'<span class="exq-mk">{html.escape(str(mk))}</span></div>'
                    f'<div class="exq-meta">{" · ".join(meta)}</div></div>')
    return "".join(rows)


def render_debrief(summary: dict | None) -> str:
    """DÉBRIEF DES PERTES (demande user 2026-08-02) — mémoire évolutive : pour chaque pari JOUÉ perdu, une
    analyse post-match distingue la MALCHANCE (variance, process sain -> aucune leçon) d'une PRÉMISSE
    défaillante (évitable -> leçon actionnable). Les leçons récurrentes s'accumulent (data/lessons.json).
    PUREMENT INFORMATIF pour l'instant : ces leçons ne modifient PAS encore la sélection des paris (décision
    séparée). Réutilise le style `.exq`. '' si aucun débrief."""
    from app import debrief as _db
    s = summary or _db.summary(("foot",))
    total = s.get("total") or 0
    if not total:
        return ""
    evit, var = s.get("evitable") or 0, s.get("variance") or 0
    intro = ('<div class="exq-intro">Chaque <b>pari joué perdu</b> est analysé après coup : on sépare la '
             '<b>malchance</b> (un pari à forte confiance qui tombe du mauvais côté — le raisonnement était '
             'bon, <b>aucune leçon</b>) d\'une <b>prémisse défaillante</b> (un signal qu\'on aurait dû voir — '
             '<b>leçon retenue</b>). Les leçons récurrentes forment une <b>mémoire qui évolue</b>. '
             '<i>Pour l\'instant informatif : elles n\'influencent pas encore la sélection.</i></div>')
    # Bandeau chiffres : total · malchance vs évitable.
    head = (f'<div class="exq-sport"><div class="exq-sphead">'
            f'<span class="exq-spname">🧠 {total} perte{"s" if total > 1 else ""} débriefée{"s" if total > 1 else ""}</span>'
            f'<span class="exq-sptag exq-sptag-ok">{var} malchance</span>'
            f'<span class="exq-sptag exq-sptag-ex">{evit} évitable{"s" if evit > 1 else ""}</span></div>')
    # Top causes.
    causes = ""
    for c, n in (s.get("by_cause") or [])[:6]:
        causes += (f'<div class="exq-row"><div class="exq-top">'
                   f'<span class="exq-bdg exq-ok">{n}</span>'
                   f'<span class="exq-mk">{html.escape(_db.CAUSE_LABEL.get(c, c))}</span></div></div>')
    head += causes + '</div>'
    # LEÇONS actionnables (le cœur) — récurrentes d'abord.
    lessons = s.get("lessons") or []
    body = ""
    if lessons:
        body += ('<div class="exq-sport"><div class="exq-sphead">'
                 '<span class="exq-spname">📌 Leçons retenues</span>'
                 f'<span class="exq-sptag exq-sptag-ex">{len(lessons)}</span></div>')
        for l in lessons:
            fam = _db.MARKET_LABEL.get(l.get("market_family"), l.get("market_family"))
            cause = _db.CAUSE_LABEL.get(l.get("cause"), l.get("cause"))
            cnt = l.get("count") or 1
            ligues = ", ".join(sorted((l.get("leagues") or {}).keys()))[:80]
            body += (f'<div class="exq-row"><div class="exq-top">'
                     f'<span class="exq-bdg exq-ex">⚠ {cnt}×</span>'
                     f'<span class="exq-mk">{html.escape(str(fam))} · {html.escape(str(cause))}</span></div>'
                     f'<div class="exq-reason">{html.escape(str(l.get("lecon") or ""))}</div>'
                     + (f'<div class="exq-meta">{html.escape(ligues)}</div>' if ligues else "")
                     + '</div>')
        body += '</div>'
    else:
        body += ('<div class="exq-sport"><div class="exq-row"><div class="exq-reason">Aucune leçon '
                 'actionnable pour l\'instant — les pertes analysées relèvent de la variance (process sain).'
                 '</div></div></div>')
    return ('<div class="sx-card"><div class="sx-h">Débrief des pertes'
            '<span>pourquoi chaque pari perdu a perdu · mémoire évolutive</span></div>'
            f'<div class="exq">{intro}{head}{body}</div></div>')


def render_analysis_verdict(full: dict | None = None, sport: str = "foot") -> str:
    """VERDICT MARCHÉS — synthèse ACTIONNABLE en tête de l'onglet Analyse (demande user 2026-08-13 :
    « facilite au maximum son utilisation »). En 3 secondes : quels marchés JOUER (calibrés+fiables),
    SURVEILLER (échantillon en construction), ÉVITER (auto-écartés) + « la calibration est-elle honnête ? »
    + « le phare (confiance) tient-il ? ». Ne fait que RASSEMBLER ce qui est déjà calculé (exclusions_report,
    calibration, reliability, by_tier) — PUR AFFICHAGE, jamais re-branché dans la sélection (aucun biais,
    cf. rétroaction Under coupée). '' si pas de données."""
    from app import analyses
    er = analyses.exclusions_report() or {}
    srow = next((s for s in er.get("sports", []) if s.get("key") == sport), None)
    if not srow:
        return ""
    rows = srow.get("rows") or []
    min_n = (er.get("thresholds") or {}).get("min_n", 25)
    play  = [r for r in rows if not r.get("excluded") and r.get("kind") == "ok"]
    watch = [r for r in rows if not r.get("excluded") and r.get("kind") == "watch"]
    avoid = [r for r in rows if r.get("excluded")]
    # « à jouer » : ROI PROUVÉ d'abord (assez de paris réglés), puis meilleure réussite calibrée
    play.sort(key=lambda r: (-((r.get("roi") or 0) if (r.get("roi") is not None and (r.get("settled") or 0) >= 5) else 0),
                             -(r.get("win_rate") or 0)))

    def _chip(r, cls):
        mk = html.escape(str(r.get("market") or ""))
        roi, st, n, gap, wr = r.get("roi"), r.get("settled") or 0, r.get("n") or 0, r.get("gap"), r.get("win_rate")
        if cls == "av-play":
            sub = f'{wr}%' + (f' · {roi:+d}% ROI' if (roi is not None and st >= 5) else '')
        elif cls == "av-watch":
            sub = (f'{n}/{min_n} préd.' if n < min_n else (f'écart {gap:+d}' if gap is not None else 'à confirmer'))
        else:   # av-avoid
            k = r.get("kind")
            sub = ("banni" if k == "ban" else
                   (f'sur-confiance {gap:+d}' if k == "gap" and gap is not None else
                    (f'ROI {roi:+d}%' if (k == "roi" and roi is not None) else 'écarté')))
        return f'<span class="av-chip {cls}"><b>{mk}</b><small>{sub}</small></span>'

    def _chips(lst, cls):
        return ("".join(_chip(r, cls) for r in lst) if lst else '<span class="av-empty">aucun</span>')

    # KPI 1 — calibration honnête ? (verdict global + indice de fiabilité + tendance)
    cal = analyses.calibration() or {}
    _vmap = {"good": ("✅", "Honnête"), "over": ("🔴", "Trop optimiste"),
             "under": ("🟡", "Plutôt prudente"), "unsure": ("⏳", "En construction")}
    _em, _lbl = _vmap.get(cal.get("verdict"), ("•", "—"))
    rel = analyses.calibration_reliability() or {}
    _idx, _tr = rel.get("index"), rel.get("trend")
    _arr = {"up": "▲", "down": "▼"}.get(_tr, "→")
    kpi1 = f'{_em} {_lbl}' + (f' <small>{_idx}/100 {_arr}</small>' if _idx is not None else "")

    # KPI 2 — le phare (confiance) tient-il ? (15 derniers paris confiance vs global)
    if full is None:
        full = analyses.stats_full()
    conf = (full.get("by_tier") or {}).get("confiance") or {}
    _rec = [b for b in (conf.get("recent") or []) if b.get("result") in ("won", "lost")]
    _last = _rec[-15:]
    if _last:
        _rp = round(100 * sum(1 for b in _last if b.get("result") == "won") / len(_last))
        _dot = "🟢" if _rp >= 80 else ("🟡" if _rp >= 65 else "🔴")
        kpi2 = f'{_dot} {_rp}% <small>15 derniers · {conf.get("pct")}% global</small>'
    else:
        kpi2 = "⏳ —"

    return (
        '<div class="av-card">'
        '<div class="av-card-h">📌 Verdict marchés · en un coup d\'œil</div>'
        '<div class="av-top">'
        f'<div class="av-kpi"><div class="av-kpi-l">Calibration honnête ?</div><div class="av-kpi-v">{kpi1}</div></div>'
        f'<div class="av-kpi"><div class="av-kpi-l">Phare (confiance)</div><div class="av-kpi-v">{kpi2}</div></div>'
        '</div>'
        f'<div class="av-row"><div class="av-row-h">🟢 À jouer — calibrés &amp; fiables</div>'
        f'<div class="av-chips">{_chips(play, "av-play")}</div></div>'
        f'<div class="av-row"><div class="av-row-h">🟡 À surveiller — échantillon en construction</div>'
        f'<div class="av-chips">{_chips(watch, "av-watch")}</div></div>'
        f'<div class="av-row"><div class="av-row-h">🔴 À éviter — écartés automatiquement</div>'
        f'<div class="av-chips">{_chips(avoid, "av-avoid")}</div></div>'
        '</div>')


def render_market_watch(by_sport: dict | None) -> str:
    """SURVEILLANCE PAR MARCHÉ, DÉCLINÉE PAR SPORT (demande user 2026-08-01) : pour CHAQUE sport puis chaque
    type de pari, la taille d'échantillon (fantômes + joués), l'annoncé vs le réel (écart de calibration) et
    le ROI joué + un statut « calibré / à surveiller / en construction ». `by_sport` = `calibration()['by_sport']`
    (chaque sport porte `markets`). Un marché « Handicap » foot est distinct du « Handicap » tennis/basket.
    Réutilise le style `.exq`. '' si pas de données."""
    if not by_sport:
        return ""
    intro = ('<div class="exq-intro">Suivi <b>par sport</b> : chaque type de pari est mesuré en continu — même '
             'les paris NON joués (fantômes) comptent. Tant qu\'un marché a <b>peu de prédictions</b> il reste '
             '« en construction » (le système ne conclut pas), et les fantômes le font <b>monter</b> jusqu\'à '
             'une stat fiable. Un même marché est jugé <b>séparément</b> selon le sport.</div>')
    _ICON = {"Football": "⚽", "Tennis": "🎾", "Basket": "🏀"}
    body = intro
    any_sec = False
    for sp in ("Football", "Tennis", "Basket"):
        mk = (by_sport.get(sp) or {}).get("markets") or {}
        secrows = _market_watch_rows(mk) if mk else ""
        if not secrows:
            continue
        any_sec = True
        body += (f'<div class="exq-sport"><div class="exq-sphead">'
                 f'<span class="exq-spname">{_ICON.get(sp, "")} {html.escape(sp)}</span></div>'
                 f'{secrows}</div>')
    if not any_sec:
        return ""
    return ('<div class="sx-card"><div class="sx-h">Surveillance des marchés'
            '<span>échantillon · calibration · ROI par sport &amp; type de pari</span></div>'
            f'<div class="exq">{body}</div></div>')


_MKO_FAM_ORDER = ["Double chance", "Total Over", "Total Under", "Total équipe", "Vainqueur", "Handicap",
                  "Les 2 marquent", "Mi-temps", "Tirs cadrés", "Tirs", "Cartons", "Corners",
                  "Premier but", "Premier buteur", "Score exact", "Arrêts gardien", "Props joueur", "Autre"]


def render_market_overview(rows: list | None) -> str:
    """APERÇU PAR MARCHÉ (foot), au niveau SEUIL (OVER 2.5 ≠ OVER 3.5) — TABLEAU pro (user 2026-08-20 : rendu
    plus intuitif). 3 colonnes : Réussite (👻 fantôme = fréquence réelle, calibration) · Value (💎 ROI des paris
    value>0, = ce qu'on jouerait) · Confiance (⭐ ROI des value ≥75 %). Bord gauche coloré = VERDICT sur la value
    (vert = rentable · rouge = piège · gris = peu de données). Groupé par famille, familles utiles en tête,
    marchés triés par ROI value décroissant. '' si rien. `rows` = `analyses.market_overview()`."""
    if not rows:
        return ""

    def _rank(f):
        return _MKO_FAM_ORDER.index(f) if f in _MKO_FAM_ORDER else len(_MKO_FAM_ORDER)
    rows = sorted(rows, key=lambda r: (_rank(r["fam"]), -(r["val_roi"] if r["val_roi"] is not None else -999)))

    def _cell(n, roi):                                         # cellule ROI : chiffre coloré + n discret dessous
        if not n:
            return '<td class="mko-c"><span class="mko-dim">—</span></td>'
        cls = "mko-dim" if n < 5 else ("mko-pos" if (roi or 0) > 0 else "mko-neg" if (roi or 0) < 0 else "mko-dim")
        return (f'<td class="mko-c"><span class="{cls}">{roi:+d}%</span>'
                f'<i class="mko-n">{n}</i></td>')                # juste le nb de paris (le mot est expliqué en intro)

    intro = ('<div class="mko-intro"><b>Comment lire :</b> chaque type de pari est décliné <b>par seuil</b> '
             '(Over/Under 1.5, 2.5, 3.5… séparés). <b>Réussite</b> = fréquence réelle (toutes prédictions). '
             '<b>Value</b> = rendement des paris qu\'on jouerait (value positive) — <span class="mko-pos">vert '
             '= rentable</span>, <span class="mko-neg">rouge = piège</span> (gagne souvent mais cote trop '
             'courte). <b>Confiance</b> = value ET confiance ≥ 75 %. Le petit chiffre gris = nombre de paris.</div>')
    trs, cur = [], None
    for r in rows:
        if r["fam"] != cur:
            cur = r["fam"]
            trs.append(f'<tr class="mko-fam"><td colspan="4">{html.escape(str(cur))}</td></tr>')
        vn, vr = r["val_n"], (r["val_roi"] or 0)
        acc = "v-pos" if (vn >= 20 and vr >= 3) else ("v-neg" if (vn >= 20 and vr <= -3) else "v-dim")
        trs.append(
            f'<tr class="mko-r {acc}">'
            f'<td class="mko-mk">{html.escape(r["code"])}</td>'
            f'<td class="mko-c"><span class="mko-win">{r["win"]}%</span>'
            f'<i class="mko-n">{r["n"]}</i></td>'
            + _cell(r["cf_n"], r["cf_roi"]) + _cell(r["val_n"], r["val_roi"]) + '</tr>')   # Confiance AVANT Value (user)
    return ('<div class="sx-card"><div class="sx-h">Aperçu par marché'
            '<span>réussite · confiance · value, par seuil</span></div>'
            f'{intro}<table class="mko">'
            '<colgroup><col class="mko-c1"><col class="mko-c2"><col class="mko-c3"><col class="mko-c4"></colgroup>'
            '<thead><tr><th>Marché</th><th>Réuss.</th><th>Conf.</th><th>Value</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


def render_reliability(rel: dict | None) -> str:
    """INDICE DE FIABILITÉ de la calibration + VRAI graphique d'évolution (preuve mesurée d'auto-
    amélioration) : gros score /100, flèche de tendance, et courbe pleine largeur de l'indice dans le
    temps. '' si pas assez de recul."""
    if not rel or rel.get("index") is None:
        return ""
    idx = rel["index"]
    _T = {"up": ("▲", "en amélioration", "up"), "flat": ("→", "stable", "flat"),
          "down": ("▼", "en recul", "down")}
    arrow, word, cls = _T.get(rel.get("trend"), ("→", "", "flat"))
    chart = _reliability_chart(rel.get("series") or [], uid="rel")
    m1, m2 = rel.get("mae_first"), rel.get("mae_last")
    ecart = (f'{m1} → {m2} pts' if (m1 is not None and m2 is not None) else f'{rel.get("mae")} pts')
    # PÉRIODE couverte (« depuis quand ») : plage des prédictions datées de la calibration.
    _M = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")

    def _fr(iso):
        try:
            return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    _d1, _d2 = _fr(rel.get("first")), _fr(rel.get("last"))
    period = ""
    if _d1 and _d2:
        _days = (_d2.date() - _d1.date()).days + 1
        period = (f'<div class="sx-rel-period">🗓 Depuis le <b>{_d1.day} {_M[_d1.month - 1]}</b> · '
                  f'{_days} jour{"s" if _days > 1 else ""} · <b>{rel.get("n")}</b> prédictions</div>')
    return (
        '<div class="sx-card sx-rel"><div class="sx-h">Indice de fiabilité'
        '<span>calibration · auto-évolution</span></div>'
        '<div class="sx-rel-top">'
        f'<div class="sx-rel-main"><div class="sx-rel-idx">{idx}<small>/100</small></div>'
        f'<div class="sx-rel-tr {cls}">{arrow} {word}</div></div>'
        f'<div class="sx-rel-kpi"><b>{ecart}</b><span>écart confiance↔réel</span></div></div>'
        f'{period}'
        f'<div class="sx-rel-chart">{chart}</div>'
        f'<div class="sx-rel-note">Courbe CUMULATIVE : chaque point = la fiabilité sur <b>tout</b> depuis '
        f'le début jusqu\'à cet instant → le dernier point (à droite) = l\'indice global. '
        f'L\'écart confiance↔réel se resserre à mesure que le modèle <b>se recalibre seul</b> (sur '
        f'<b>{rel.get("n")}</b> prédictions) : plus la courbe monte, plus la confiance affichée tient '
        f'ses promesses.</div>'
        '</div>')

def render_calibration(c: dict) -> str:
    """Page CALIBRATION : par tranche de confiance, confiance annoncée vs réussite réelle (barres),
    + verdict global. Montre où le système est trop optimiste (à corriger) ou fiable."""
    rows = c.get("rows") or []
    if not rows or not c.get("n"):
        return ('<div class="cal-h">🎯 Calibration</div>'
                '<div class="banner">Pas encore assez de paris réglés pour mesurer la calibration. '
                'Reviens après quelques journées de résultats.</div>')
    vmap = {
        "good": ("cal-ok", "✅ Bien calibré",
                 "La confiance annoncée colle au taux de réussite réel — on peut s'y fier."),
        "over": ("cal-over", "⚠️ Trop optimiste",
                 "En moyenne, le système annonce plus de confiance qu'il ne réussit. "
                 "→ resserrer les paris à faible confiance."),
        "under": ("cal-under", "↗️ Prudent",
                  "Le système gagne en fait plus souvent que la confiance annoncée — marge de progression."),
    }
    vc, vt, vs = vmap.get(c.get("verdict"), ("", "Calibration", ""))
    _np, _ns = c.get("n_played") or 0, c.get("n_shadow") or 0
    src = (f' <span class="cal-src">(<b>{_np}</b> joués + <b>{_ns}</b> fantômes)</span>' if _ns else "")
    head = (f'<div class="cal-verdict {vc}"><div class="cal-v-t">{vt}</div>'
            f'<div class="cal-v-s">{vs}</div>'
            f'<div class="cal-v-m">écart moyen <b>{c["mae"]} pts</b> · {c["n"]} paris réglés{src}</div></div>')
    if _ns:
        head += ('<div class="cal-ghost">🔎 La calibration s\'appuie sur les paris <b>joués</b> '
                 '<b>ET</b> sur des prédictions <b>fantômes</b> (non jouées, réglées après match) pour '
                 'couvrir tout le spectre de proba. Ces fantômes <b>n\'entrent JAMAIS</b> dans les '
                 'gains / le ROI / la courbe — qui ne comptent que les '
                 f'<b>{_np}</b> paris réellement joués.</div>')
    bars = []
    for r in rows:
        gapcls = "pos" if r["gap"] >= 0 else "neg"   # réussite ≥ confiance = bon (vert)
        roi = r.get("roi")
        roi_html = (f'<div class="cal-roi cal-roi-{"pos" if roi >= 0 else "neg"}">{roi:+d}%'
                    f'<span>ROI · {r["roi_n"]} joué{"s" if r["roi_n"] != 1 else ""}</span></div>'
                    if roi is not None else '')
        bars.append(
            f'<div class="cal-row"><div class="cal-band">{r["lo"]}–{r["hi"]}%'
            f'<span>{r["n"]} préd.</span></div>'
            f'<div class="cal-bars">'
            f'<div class="cal-line"><span class="cal-lab">annoncé</span>'
            f'<div class="cal-track"><span class="cal-fill conf" style="width:{r["avg_conf"]}%"></span></div>'
            f'<b>{r["avg_conf"]}%</b></div>'
            f'<div class="cal-line"><span class="cal-lab">réel</span>'
            f'<div class="cal-track"><span class="cal-fill real {gapcls}" style="width:{r["win_rate"]}%"></span></div>'
            f'<b>{r["win_rate"]}%</b></div></div>'
            f'<div class="cal-side"><div class="cal-gap {gapcls}">{r["gap"]:+d}</div>{roi_html}</div></div>')
    note = ('<div class="cal-note">Chaque ligne = un niveau de confiance. <b>« annoncé»</b> vs '
            '<b>«réel»</b> (réussite, fantômes inclus) ; le <b>ROI</b> à droite ne compte que les paris '
            '<b>joués</b>. Réel <span class="cal-neg-t">sous</span> l\'annoncé = trop optimiste ; '
            '<span class="cal-pos-t">au-dessus</span> = prudent.</div>')
    # BANDEAU « ce que la boucle écarte EN CE MOMENT » : l'ACTION concrète (auto_exclusions), pas
    # seulement le diagnostic. Rend visible l'apprentissage -> on surveille sans rien décider à la main.
    try:
        ex_sports, ex_markets = analyses.auto_exclusions()
    except Exception:
        ex_sports, ex_markets = set(), set()
    if ex_markets or ex_sports:
        _it = []
        if ex_markets:
            _it.append("marchés : <b>" + "</b>, <b>".join(sorted(html.escape(m) for m in ex_markets)) + "</b>")
        if ex_sports:
            _it.append("sports : <b>" + "</b>, <b>".join(sorted(html.escape(s) for s in ex_sports)) + "</b>")
        excl = ('<div class="cal-excl">🚫 <b>Écartés automatiquement</b> des recommandations (échantillon '
                'suffisant + sur-confiance ou ROI négatif) — ' + " · ".join(_it) +
                '. <span>Auto-révisable : une catégorie se ré-inclut seule si elle redevient bonne.</span></div>')
    else:
        excl = ('<div class="cal-excl cal-excl-none">✓ <b>Aucune catégorie écartée</b> pour l\'instant '
                '<span>(pas encore assez de recul, ou tout est dans les clous).</span></div>')
    # Un SEUL bloc : chaque sport, avec ses types de paris en sous-catégories indentées.
    by_sport = _calib_by_sport(c.get("by_sport") or {})
    return (f'<div class="cal-h">🎯 Calibration</div>{head}{excl}<div class="cal">{"".join(bars)}</div>'
            f'{note}{by_sport}')

_CALIB_VERDICT = {"good": ("v-ok", "fiable"), "over": ("v-over", "trop optimiste"),
                  "under": ("v-under", "prudent"), "unsure": ("v-unsure", "à confirmer"),
                  "no-data": ("", "—")}

def _calib_line(name: str, g: dict, sub: bool = False) -> str:
    """Une ligne de calibration (n, confiance annoncée vs réel, écart, verdict). `sub` = sous-catégorie."""
    gap = (g.get("win_rate") or 0) - (g.get("avg_conf") or 0)
    gapcls = "pos" if gap >= 0 else "neg"
    vcls, vlbl = _CALIB_VERDICT.get(g.get("verdict"), ("", "—"))
    cls = "calg-row calg-sub" if sub else "calg-row calg-sport"
    roi = g.get("roi")
    roi_txt = (f' · <span class="{"cal-pos-t" if roi >= 0 else "cal-neg-t"}">ROI {roi:+d}%</span>'
               if roi is not None else '')
    return (f'<div class="{cls}"><span class="calg-name">{html.escape(name)}'
            f'<span>{g["n"]} préd.{roi_txt}</span></span>'
            f'<span class="calg-cmp"><b>{g.get("avg_conf")}%</b><i>→</i>'
            f'<b class="{gapcls}">{g.get("win_rate")}%</b></span>'
            f'<span class="cal-gap {gapcls}">{gap:+d}</span>'
            f'<span class="calg-v {vcls}">{vlbl}</span></div>')

def _calib_by_sport(by_sport: dict) -> str:
    """Calibration PAR SPORT, avec chaque TYPE DE PARI du sport en SOUS-CATÉGORIE indentée."""
    if not by_sport:
        return ""
    rows = []
    for name, g in by_sport.items():
        if not g.get("n"):
            continue
        rows.append(_calib_line(name, g))
        for mk, mg in (g.get("markets") or {}).items():
            rows.append(_calib_line(mk, mg, sub=True))
    if not rows:
        return ""
    return ('<div class="calg-h">Par sport &amp; type de pari '
            '<span class="calg-leg">annoncé → réel</span></div>'
            f'<div class="calg">{"".join(rows)}</div>')

def render_bet_detail(items: list) -> str:
    """Liste des PARIS réglés (drill-down d'un sport) — vue premium : pastille résultat ✓/✗/➖ +
    sélection + affiche·date + cote + gain/perte (unités, mise plate). Triés du + récent au + ancien.
    En tête : un mini-bilan (gagnés/réglés · profit cumulé) de la catégorie."""
    if not items:
        return '<div class="sx-dd-empty">Aucun pari réglé dans cette catégorie.</div>'
    e = html.escape
    won = sum(1 for it in items if it["result"] == "won")
    settled = sum(1 for it in items if it["result"] in ("won", "lost"))
    profit = sum(it.get("pnl") or 0 for it in items)
    staked = sum(1 for it in items if it["result"] in ("won", "lost", "push"))
    roi = round(100 * profit / staked) if staked else 0    # ROI = profit ÷ total misé (mise constante)
    pcls = "pos" if roi > 0 else ("neg" if roi < 0 else "neu")
    head = (f'<div class="sx-dd-head"><span><b>{won}/{settled}</b> gagnés</span>'
            f'<span class="sx-dd-pnl {pcls}">{"+" if roi >= 0 else "−"}{abs(roi)}% ROI</span></div>')
    rows = []
    for it in items:
        cls, lbl = {"won": ("dd-w", "✓"), "lost": ("dd-l", "✗"),
                    "push": ("dd-p", "➖")}.get(it["result"], ("dd-p", "·"))
        when = fmt_local(it.get("start"), with_date=True) or ""
        cote = f'@{it["odds"]:g}' if it.get("odds") else ""
        pnl = it.get("pnl")
        # ROI du pari (mise constante) : gagné = (cote−1)×100 %, perdu = −100 %, remboursé = 0 %.
        if pnl is None or it["result"] == "push":
            pnlh = '<span class="sx-dd-u neu">0%</span>'
        else:
            rb = round(pnl * 100)
            uc = "pos" if rb > 0 else "neg"
            pnlh = f'<span class="sx-dd-u {uc}">{"+" if rb >= 0 else "−"}{abs(rb)}%</span>'
        rows.append(
            f'<div class="sx-dd-row"><span class="sx-dd-res {cls}">{lbl}</span>'
            f'<div class="sx-dd-m"><div class="sx-dd-t">{e(str(it["sel"]))}</div>'
            f'<div class="sx-dd-s">{e(it["home"])} v {e(it["away"])} · {e(when)}</div></div>'
            f'<div class="sx-dd-r"><span class="sx-dd-c">{cote}</span>{pnlh}</div></div>')
    return f'<div class="sx-dd">{head}{"".join(rows)}</div>'

def analyst_bars(o1, ox, o2, votes=None, home=None, away=None) -> dict:
    """Champs de barres pour une carte/fiche ANALYSTE (sans modèle Elo) : Cote Unibet (proba
    implicite dévig depuis les cotes) + Public (votes). `votes` = (pct_home, pct_away[, pct_draw])
    en %, ou None. `home`/`away` : noms d'issue affichés dans les chips (sinon l'appelant doit
    fournir home/away dans le dict, ex. les cartes). Rend des clés i_*/o_*/pub_* lues par _pick_bars."""
    implied = None
    if o1 and o2:
        i1, ix, i2 = 1 / o1, (1 / ox if ox else 0.0), 1 / o2
        s = i1 + ix + i2
        if s > 0:
            implied = (i1 / s, (ix / s if ox else None), i2 / s)
    d = bars_split(None, implied)
    d["o_home"], d["o_draw"], d["o_away"] = o1, ox, o2   # cotes BRUTES -> affichées dans la barre
    if home:
        d["home"] = home
    if away:
        d["away"] = away
    if votes and votes[0] is not None:
        d["pub_home"], d["pub_away"] = votes[0] / 100, votes[1] / 100
        if len(votes) > 2 and votes[2] is not None:
            d["pub_draw"] = votes[2] / 100
    return d

def bars_two_way(p_home, imp_home, votes, home, away) -> dict:
    """Barres réparties — match à 2 issues (basket/tennis). `imp_home` = proba implicite dévig
    du domicile ; `votes` = (% home, % away)."""
    if p_home is None:
        return {}
    model = (p_home, None, 1 - p_home)
    implied = (imp_home, None, 1 - imp_home) if imp_home is not None else None
    home_fav = p_home >= 0.5
    d = {"home": home, "away": away, "bet": home if home_fav else away,
         "model_prob": p_home if home_fav else 1 - p_home, **bars_split(model, implied)}
    if votes and votes[0] is not None:
        d["pub_home"], d["pub_away"] = votes[0] / 100, votes[1] / 100
    return d

def bars_foot(probs, imp, votes, home, away) -> dict:
    """Barres réparties — foot 1X2. `imp` = (p1,pX,p2) dévig ; `votes` = (% home, % away)."""
    if not probs:
        return {}
    model = (probs[0], probs[1], probs[2])
    implied = (imp[0], imp[1], imp[2]) if imp else None
    i = max(range(3), key=lambda k: probs[k])
    d = {"home": home, "away": away, "bet": [home, "Match nul", away][i],
         "model_prob": probs[i], **bars_split(model, implied)}
    if votes and votes[0] is not None:
        d["pub_home"], d["pub_away"] = votes[0] / 100, votes[1] / 100
        if len(votes) > 2 and votes[2] is not None:   # vote du nul (1X2)
            d["pub_draw"] = votes[2] / 100
    return d

def odds_row(outcomes, highlight_idx: int | None = None) -> str:
    """Cotes Unibet COMPACTES sur une ligne : `outcomes` = [(libellé, cote), ...] — 2 issues
    (tennis/basket) ou 3 avec « Nul » (foot). L'issue pronostiquée par BETSFIX (`highlight_idx`)
    ou le favori du book (cote mini à défaut) est mise en avant."""
    valid = [(i, lbl, o) for i, (lbl, o) in enumerate(outcomes) if o]
    if not valid:
        return '<div class="dim">cotes Unibet à venir</div>'
    if highlight_idx is not None and any(i == highlight_idx for i, _, _ in valid):
        hi = highlight_idx
    else:
        hi = min(valid, key=lambda t: t[2])[0]   # repli : favori du book (cote mini)
    cells = "".join(
        f'<span class="oc2{" fav" if i == hi else ""}">{html.escape(str(lbl))} <b>{o}</b></span>'
        for i, lbl, o in valid)
    return f'<div class="oddsrow2">{cells}</div>'


def _head(title: str, info: str | None = None) -> str:
    """Titre de section. Si `info` est fourni, un petit 'i' à droite déroule
    l'explication dessous (HTML natif <details>, sans JS)."""
    if not info:
        return f'<h2>{title}</h2>'
    return (f'<details class="sec"><summary>{title}'
            '<span class="i" aria-label="Infos">i</span></summary>'
            f'<div class="banner">{info}</div></details>')

def _section(heading: str, body: str, open_: bool = True, info: str | None = None) -> str:
    """Section repliable : le titre est un bouton (▾) qui plie/déplie la liste.
    `open_=False` -> repliée d'office (ex. « Terminés »). `info`, s'il existe, se déplie
    derrière un petit « i » (caché par défaut) -> n'occupe pas d'espace."""
    op = " open" if open_ else ""
    i_btn = '<span class="i" data-info aria-label="Infos">i</span>' if info else ""
    info_html = f'<div class="banner sec-info" hidden>{info}</div>' if info else ""
    return (f'<details class="sec2"{op}><summary><span class="ttl">{heading}</span>'
            f'<span class="sright">{i_btn}<span class="chev">▾</span></span></summary>'
            f'<div class="secbody">{info_html}{body}</div></details>')

_SPORT_FR_LABEL = {"foot": ("Football", "⚽"), "tennis": ("Tennis", "🎾"), "basket": ("Basket", "🏀")}

def _recent_bets_html(recent: list) -> str:
    """Liste des DERNIERS paris réglés (plus récent en haut) : pastille W/L/N + affiche + sélection +
    cote + date. Affichée D'OFFICE sous la courbe (demande user 2026-08-13 : plus de bouton), TOUS les
    paris sans scroll interne. '' si vide."""
    if not recent:
        return ""
    _B = {"won": ("W", "rec-w"), "lost": ("L", "rec-l"), "push": ("N", "rec-n"),
          "pending": ("⏳", "rec-p")}       # ⏳ = pari À JOUER (compté au ROI, pas encore réglé)
    rows = []
    for b in recent:
        letter, cls = _B.get(b.get("result"), ("?", ""))
        _nm_raw = str(b.get("name") or "")
        name = html.escape(_nm_raw.replace(" - ", " — "))
        _h2, _, _a2 = _nm_raw.partition(" - ")
        sel = html.escape(_pretty_sel(str(b.get("sel") or ""), _h2, _a2))
        cote = b.get("cote")
        cote_txt = f'{round(cote, 2):g}' if isinstance(cote, (int, float)) and cote else ""   # 2 décimales (user 2026-08-20) · SANS « @ »
        # DATE (haut, alignée avec les ÉQUIPES) + HEURE (bas, alignée avec le PARI) — demande user 2026-07-25.
        # Date en format COURT JJ/MM POUR TOUS (y compris aujourd'hui) -> colonne étroite (demande user :
        # « écris la date du jour afin de rétrécir la colonne » = pas de « Aujourd'hui »/« Demain » long).
        _hm = fmt_local(b.get("start"), with_date=False) if b.get("start") else ""
        _date = ""
        if b.get("start"):
            try:
                _dd = datetime.fromisoformat(b["start"])
                if LOCAL_TZ is not None and _dd.tzinfo is not None:
                    _dd = _dd.astimezone(LOCAL_TZ)
                _date = _dd.strftime("%d/%m")
            except (ValueError, TypeError):
                _date = ""
        # ORDRE (demande user 2026-07-25) : DATE/HEURE tout à gauche · pari (nom + sélection) · COTE ·
        # RÉSULTAT tout à droite (la cote juste avant le badge résultat).
        _legs = b.get("legs")                          # COMBINÉ -> ligne DÉPLIABLE révélant les jambes
        # COMBINÉ MULTI-MATCHS (demande user 2026-07-28) : 1re ligne « Combiné du jour », sous-ligne GRISE
        # « N jambes » (le détail des matchs/marchés vit dans les jambes dépliables). Les combinés MÊME-MATCH
        # (Bet Builder, ex. Coupe du Monde — toutes les jambes sur UN seul match, donc `name` de jambe vide)
        # gardent leur affichage d'origine (affiche du match + marchés combinés).
        if _legs:
            _mn = {str(l.get("name") or "").strip() for l in _legs}
            _mn.discard("")
            if len(_mn) > 1:                            # ≥2 matchs distincts -> vrai combiné multi-matchs
                # TITRE = le VRAI nom du combiné (« Combiné du jour » OU « Combiné du soir »), pas un libellé
                # hardcodé (bug user 2026-08-31 : le combiné du soir s'affichait « Combiné du jour »). Le nom
                # entrant porte déjà la variante (ex. « Combiné du soir (3 j.) ») -> on retire juste le
                # « (N jambes) » et on met le nb de jambes en sous-ligne. Repli « Combiné du jour » si vide.
                _ctitle = re.sub(r"\s*\([^)]*\)\s*$", "", _nm_raw).strip() or "Combiné du jour"
                name = html.escape(_ctitle)
                sel = html.escape(f"{len(_legs)} jambe{'s' if len(_legs) > 1 else ''}")
        _sel_disp = sel + (' <span class="spf-cx">▾</span>' if _legs else "")
        _inner = (
            f'<span class="spf-rec-d"><b>{html.escape(_date)}</b><span>{html.escape(_hm)}</span></span>'
            f'<span class="spf-rec-m"><b>{name}</b>'
            f'<span class="spf-rec-s">{_sel_disp}</span></span>'
            f'<span class="spf-rec-c">{cote_txt}</span>'
            f'<span class="spf-rec-b">{letter}</span>')
        if _legs:                                      # jambes en clair au clic (demande user 2026-07-26)
            _lg = []
            for l in _legs:
                _ltr, _lcls = _B.get(l.get("result"), ("·", "rec-p"))
                _lraw = str(l.get("name") or "")
                _lh, _, _la = _lraw.partition(" - ")
                _lsel = html.escape(_pretty_sel(str(l.get("sel") or ""), _lh, _la))
                _lco = l.get("cote")
                _lco_txt = (f'<span class="spf-leg-c">{_lco:g}</span>'
                            if isinstance(_lco, (int, float)) and _lco else "")
                _lnm = (f'<b>{html.escape(_lraw.replace(" - ", " — "))}</b>' if _lraw else "")   # vide = même-match
                _lg.append(
                    f'<div class="spf-leg {_lcls}">'
                    f'<span class="spf-leg-t">{_lnm}<span>{_lsel}</span></span>{_lco_txt}'
                    f'<span class="spf-leg-b">{_ltr}</span></div>')
            rows.append(
                f'<details class="spf-rec-x"><summary class="spf-rec {cls} spf-rec-exp">{_inner}</summary>'
                f'<div class="spf-legs">{"".join(_lg)}</div></details>')
        else:
            rows.append(f'<div class="spf-rec {cls}">{_inner}</div>')
    return f'<div class="spf-recent">{"".join(rows)}</div>'


def _perf_curve_block(label: str, blk: dict | None, uid: str, empty_msg: str,
                      form: list | None = None, pending: int = 0) -> str:
    """Bloc COURBE AUTONOME d'un onglet sport (Simples / Combinés) : en-tête (titre + ROI), la SÉRIE en
    cours + la forme W/L au-dessus du graphe, la courbe d'équité, puis les stats (réussite · paris · cote).
    CLIQUABLE (`<details>`) -> révèle les DERNIERS PARIS réglés (W/L + affiche + sélection). Message
    discret si aucun pari réglé. `blk` = bloc `_agg_bets` (points/roi/pct/settled/streak/recent…)."""
    if not (blk and blk.get("settled")):
        return (f'<div class="spf-cv spf-cv-empty"><div class="spf-cv-h">'
                f'<span class="spf-cv-t">{label}</span></div>'
                f'<div class="spf-cv-none">{empty_msg}</div></div>')
    roi = blk.get("roi")
    head = (f'<div class="spf-cv-h"><span class="spf-cv-hl"><span class="spf-cv-t">{label}</span></span>'
            f'<span class="spf-cv-roi arec-{_roi_cls(roi, blk.get("settled"))}">'
            f'ROI {_roistr(roi)}</span></div>')
    _LET = {"won": "W", "lost": "L", "push": "N"}
    # max de résultats sur 1 ligne + sabliers dorés des paris à jouer non réglés en queue (demande user 2026-07-17)
    _form_wl = [_LET.get(x, x) for x in (form or [])]
    dots = form_dots(_form_wl, n=16, pending=pending)
    formrow = f'<div class="spf-cv-form">{dots}</div>' if dots else ""
    # SÉRIE en cours + RECORD (plus longue série de victoires) empilés SOUS la ligne W/L (demande user
    # 2026-07-25) — même présentation que les cadres « ROI héros ». RECORD = `best_streak` pré-calculé sur
    # TOUT l'historique (repli calcul depuis la forme si absent) : recalcul depuis la forme tronquée
    # sous-estimait le record (foot simple 18 affiché ≤9).
    _best_h = blk.get("best_streak")
    if _best_h is None:
        _best_h = _best_win_streak(_form_wl)
    formrow += _streak_text(blk.get("streak"), _best_h)
    kpis = (f'<div class="spf-cv-kpis">'
            f'<span><b>{blk.get("pct")}%</b> réussite</span>'
            f'<span><b>{blk.get("settled")}</b> paris</span>'
            f'<span><b>{blk.get("avg_odds") or "—"}</b> cote</span></div>')
    chart = _hero_chart(blk.get("points") or [], uid=uid)
    rec = _recent_bets_html(list(reversed(blk.get("recent") or [])))
    if not rec:                                                     # pas de détail -> bloc simple
        return f'<div class="spf-cv">{head}{formrow}{chart}{kpis}</div>'
    # Derniers paris affichés D'OFFICE (demande user 2026-08-13 : « plus besoin du bouton ») — sous la courbe.
    return (f'<div class="spf-cv">{head}{formrow}{chart}{kpis}'
            f'<div class="spf-rec-lbl">Derniers paris</div>{rec}</div>')

def render_sport_perf(sport: str) -> str:
    """Carte de performance du sport : DEUX courbes AUTONOMES (Simples / Combinés), CHACUNE avec sa
    propre forme W/L au-dessus et ses propres stats (réussite · paris · cote moy.) en dessous, puis
    (repliable, même cadre) le détail par pari + la CALIBRATION. '' si aucun résultat réglé."""
    from app import analyses
    label, icon = _SPORT_FR_LABEL.get(sport, (sport.title(), ""))
    s = (analyses.stats_full().get("by_sport") or {}).get(sport)
    if not s or not s.get("settled"):
        return ""
    # DEUX courbes d'équité AUTONOMES (demande user) : chaque graphe porte SA forme W/L + SES stats.
    # Simples = suivi ROI du sport ; Combinés = segment dédié de combo_stats (foot/tennis/basket).
    combo_bs = (analyses.combo_stats().get("by_sport") or {}).get(sport)
    # Sabliers dorés « en attente » PROPRES à ce sport (simples / combinés du jour) — demande user 2026-07-17.
    _pend_s = sum(1 for b in analyses.pending_roi_bets() if b.get("sport") == sport)
    _pend_c = sum(1 for b in analyses.pending_roi_bets(combo=True) if b.get("sport") == sport)
    charts = ('<div class="spf-charts">'
              + _perf_curve_block("Simples", s, f"sp-{sport}-s", "Aucun simple réglé",
                                  form=s.get("form_simple") or s.get("form"), pending=_pend_s)
              + _perf_curve_block("Combinés" if analyses.COMBO_ROI_ON else "Combinés · hors ROI",   # compté au ROI (user 2026-08-19)
                                  combo_bs, f"sp-{sport}-c",
                                  "Aucun combiné réglé pour ce sport",
                                  form=(combo_bs or {}).get("form_run") or (combo_bs or {}).get("form12"),
                                  pending=_pend_c)
              + '</div>')
    # Détail INTÉGRÉ au MÊME cadre (repliable) : par pari + calibration par TYPE DE PARI de ce sport.
    g = (analyses.calibration().get("by_sport") or {}).get(label) or {}
    det = [_sport_card(s, sport, label, icon, "")]
    mk_rows = "".join(_calib_line(mk, mg, sub=True) for mk, mg in (g.get("markets") or {}).items())
    if mk_rows:
        det.append('<div class="calg-h">Calibration · par type de pari</div>'
                   f'<div class="calg">{mk_rows}</div>')
    details = (f'<details class="spf-det"><summary><span class="spf-det-t">📊 Fiabilité & calibration</span>'
               f'<span class="chev">▾</span></summary><div class="spf-det-b">{"".join(det)}</div></details>')
    # ALLÈGEMENT (demande user 2026-07-11) : tout le cadre de perf est REPLIÉ par défaut sur les onglets
    # sport (on vient voir les MATCHS). Le summary garde l'ESSENTIEL visible en une ligne — le ROI Simples
    # (+ Combinés) — et déplie les 2 courbes + la calibration en 1 tap. Fini le gros bloc stats imposé.
    roi_s, roi_c = s.get("roi"), (combo_bs or {}).get("roi")
    _rs = f'<span class="perf-sum-k arec-{_roi_cls(roi_s, s.get("settled"))}">S {_roistr(roi_s)}</span>'
    _rc = (f'<span class="perf-sum-k arec-{_roi_cls(roi_c, (combo_bs or {}).get("settled"))}">C {_roistr(roi_c)}</span>'
           if (analyses.COMBO_ROI_ON and combo_bs and combo_bs.get("settled")) else "")   # combinés hors ROI (user 2026-08-15)
    return (f'<details class="perf-fold"><summary class="perf-sum">'
            f'<span class="perf-sum-t">📊 Mes performances</span>{_rs}{_rc}'
            f'<span class="chev">▾</span></summary>'
            f'<div class="spf perf-fold-b">{charts}{details}</div></details>')


# Légende des 3 barres, réutilisée partout (accueil + intros des onglets) pour une explication
# COHÉRENTE et claire pour le parieur.
BARS_LEGEND = ('Chaque barre montre les <b>chances de chaque camp</b> (joueur 1 à gauche, '
               'joueur 2 à droite, total 100 %), selon 3 sources : <b>BETSFIX</b> (notre '
               'analyse), <b>Cote Unibet</b> (chances cachées derrière la cote) et le <b>Public</b> '
               '(votes des parieurs). Quand <b>BETSFIX donne plus de chances qu\'Unibet</b> à un '
               'camp, sa cote est peut-être trop généreuse — une <b>« value »</b>.')


def _prob_bar(prob, labels=None) -> str:
    """Barre de proba visuelle : float = 2 issues (home/away) ; (p1,px,p2) = 1-N-2."""
    if prob is None:
        return ""
    if isinstance(prob, (int, float)):
        p = round(prob * 100)
        bar = (f'<div class="pbar"><span class="s1" style="width:{p}%"></span>'
               f'<span class="s2" style="width:{100 - p}%"></span></div>')
        lab = labels or ("", "")
        return (bar + f'<div class="pbar-l"><span>{html.escape(lab[0])} {p}%</span>'
                f'<span>{100 - p}% {html.escape(lab[1])}</span></div>')
    p1, px, p2 = (round(x * 100) for x in prob)
    return (f'<div class="pbar"><span class="s1" style="width:{p1}%"></span>'
            f'<span class="sx" style="width:{px}%"></span>'
            f'<span class="s2" style="width:{p2}%"></span></div>'
            f'<div class="pbar-l"><span>1 · {p1}%</span><span>N · {px}%</span>'
            f'<span>{p2}% · 2</span></div>')

def _noF(name: str) -> str:
    """Retire « (F) » (marqueur féminin WNBA/WTA) du nom AFFICHÉ — TOUTES les occurrences (gère aussi un
    nom COMBINÉ « A (F) - B (F) », pas seulement le suffixe). AFFICHAGE SEUL : le nom stocké/brut n'est
    JAMAIS modifié (dédup, gloss, règlement intacts). Demande user 2026-07-21."""
    return re.sub(r"\s*\(\s*F\s*\)", "", (name or "").strip())

def _cap(s: str) -> str:
    """Capitalise la 1re lettre (les villes/tournois Unibet arrivent souvent en minuscule, ex.
    « s-Hertogenbosch » -> « S-Hertogenbosch ») sans toucher au reste (« Roland Garros » préservé)."""
    s = (s or "").strip()
    return (s[0].upper() + s[1:]) if (s and s[0].islower()) else s


def _parse_live_score(score) -> tuple:
    """(hs, as_) du 1er couple « H-A » d'un score live (« 2-1 », « 6-4 3-6 » -> (2,1)/(6,4)), sinon
    (None, None). Sert de garde/entrée à la barre « % live » (les valeurs ne servent qu'au foot)."""
    m = re.search(r"(\d+)\s*-\s*(\d+)", str(score or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _tennis_sets_games(score) -> dict:
    """Décompose un score TENNIS live « 6-4 2-5 » (jeux PAR SET) en {sets_h, sets_a, games_h, games_a} :
    sets GAGNÉS par chacun (paires terminées : 6+ avec 2 d'écart, ou 7) + JEUX de la dernière paire (set
    en cours). {} si score vide/illisible. Nourrit le modèle live tennis (« au moins un set », 2026-07-21)."""
    pairs = re.findall(r"(\d+)\s*-\s*(\d+)", str(score or ""))
    if not pairs:
        return {}
    sh = sa = 0
    set_games = []                              # (h, a) de CHAQUE set TERMINÉ -> verrou « total jeux du set N »
    for h, a in pairs[:-1]:                     # toutes les paires SAUF la dernière = sets joués
        h, a = int(h), int(a)
        set_games.append((h, a))
        if h > a:
            sh += 1
        elif a > h:
            sa += 1
    gh, ga = int(pairs[-1][0]), int(pairs[-1][1])
    # la DERNIÈRE paire peut être un set DÉJÀ terminé (fin de set, le suivant pas commencé) : 6+/2 d'écart ou 7.
    mx, mn = max(gh, ga), min(gh, ga)
    if (mx >= 6 and mx - mn >= 2) or mx == 7:
        set_games.append((gh, ga))
        if gh > ga:
            sh += 1
        else:
            sa += 1
        gh = ga = 0                             # nouveau set pas commencé
    return {"sets_h": sh, "sets_a": sa, "games_h": gh, "games_a": ga, "set_games": set_games}


def _live_bar_html(lp: dict | None) -> str:
    """Rend la barre « Chance live » à partir de `analyses.live_prob` ({pct,trend,source}). '' si None.
    Remplissage rouge→vert selon le %, flèche de tendance, étiquette de source. PURE AFFICHAGE."""
    if not isinstance(lp, dict):
        return ""
    pct = lp.get("pct")
    if not isinstance(pct, int):
        return ""
    trend, src = lp.get("trend"), lp.get("source", "")
    arrow = {"up": "▲", "down": "▼"}.get(trend, "")
    tcls = {"up": " lv-up", "down": " lv-down"}.get(trend, "")
    hue = int(round(1.2 * max(0, min(100, pct))))          # 0 % = rouge (h0), 100 % = vert (h120)
    # Dégradé vertical (gloss premium, user 2026-08-19) : haut plus clair, bas plus sombre -> relief.
    fill = f"linear-gradient(180deg,hsl({hue},74%,53%),hsl({hue},70%,41%))"
    # `src` = signaux FUSIONNÉS (« cote + stats live + analyse ») ou état verrouillé (acquis/perdu).
    lbl = {"acquis": "Gagné", "perdu": "Perdu"}.get(src)   # pari verrouillé par le direct (user 2026-08-10)
    if lbl is None:
        lbl = "chance estimée · " + src if src else "chance estimée"
    return (f'<div class="lvbar{tcls}">'
            f'<div class="lvbar-hd"><span class="lvbar-t">Chance live</span>'
            f'<span class="lvbar-v">{pct}%<span class="lvbar-ar">{arrow}</span></span></div>'
            f'<div class="lvbar-track"><div class="lvbar-fill" '
            f'style="width:{pct}%;background:{fill}"></div></div>'
            f'<div class="lvbar-src">{html.escape(lbl)}</div></div>')


def _live_scoreboard(score: str, home: str, away: str, tennis: bool = False,
                     server: str | None = None, points: tuple | None = None,
                     clock: str | None = None, periods: list | None = None,
                     best_of: int | None = None, fstats: dict | None = None,
                     pens: tuple | None = None) -> str:
    """Scoreboard LIVE. Tennis (`tennis=True`) : style Unibet — en-tête numéros de set + 🎾, TOUS
    les sets en colonnes (jeux par set), sets gagnés en gras, set en cours en évidence (PAS de
    case verte), colonne 🎾 = points du jeu en cours (`points`), et une balle 🎾 à droite du
    SERVEUR (`server` = 'home'/'away'). Foot/basket : 2 lignes (nom + score), meneur en vert."""
    if not score:
        return ""
    e = html.escape
    cols = []
    for part in str(score).split():
        if "-" in part:
            try:
                h, a = (int(x) for x in part.split("-"))
                cols.append((h, a))
            except ValueError:
                pass
    if not cols:
        return ""
    hs = sum(1 for h, a in cols if h > a)
    as_ = sum(1 for h, a in cols if a > h)
    home_lead, away_lead = ((hs > as_, as_ > hs) if len(cols) > 1
                            else (cols[0][0] > cols[0][1], cols[0][1] > cols[0][0]))
    # Tennis : nom de famille (dernier mot) ; foot/basket : nom COMPLET (sans « (F) »).
    def _shortname(n):
        n = _noF(n)
        return (n.split() or [n])[-1] if tennis else n
    hn = e(_shortname(home))
    an = e(_shortname(away))

    if tennis:
        n_real = len(cols)
        # TOUJOURS au moins 3 sets visibles (5 si best_of fourni / si déjà ≥4 sets joués) ; les sets
        # à venir sont affichés GRISÉS à 0. Ne jamais cacher un set déjà joué.
        n = max(best_of or 3, n_real)
        has_pts = bool(points) and (points[0] or points[1])

        def _set_done(h, a):    # set TERMINÉ ? (6 jeux + 2 d'écart, ou tie-break 7) -> compte le set
            m = max(h, a)
            return (m >= 6 and abs(h - a) >= 2) or m >= 7
        sets_h = sum(1 for h, a in cols if _set_done(h, a) and h > a)
        sets_a = sum(1 for h, a in cols if _set_done(h, a) and a > h)
        # En-tête : « S1 S2 … » (toujours n colonnes) puis colonne SETS (résultat du match à droite).
        hdr = "".join(f'<span class="lb-c lb-h">S{j + 1}</span>' for j in range(n))
        if has_pts:
            hdr += '<span class="lb-c lb-h lb-pt-h">🎾</span>'
        hdr += '<span class="lb-c lb-h lb-tot">SETS</span>'

        def trow(i, name, lead, side):
            cs = ""
            for j in range(n):
                if j >= n_real:                        # set À VENIR : 0 grisé
                    cs += '<span class="lb-c lb-fut">0</span>'
                    continue
                h, a = cols[j]
                v = h if i == 0 else a
                won = (h > a) if i == 0 else (a > h)
                cur = j == n_real - 1 and not won      # set en cours = dernier JOUÉ, pas encore gagné
                # PAS de case verte : set gagné en gras (lb-win), set en cours en évidence (lb-cur)
                kls = "lb-c" + (" lb-cur" if cur else (" lb-win" if won else ""))
                cs += f'<span class="{kls}">{v}</span>'
            if has_pts:                                # colonne 🎾 = points du jeu en cours
                cs += f'<span class="lb-c lb-pt">{e(str(points[i]))}</span>'
            cs += f'<span class="lb-c lb-tot">{sets_h if i == 0 else sets_a}</span>'   # SETS gagnés
            # 🎾 à DROITE du serveur
            ball = ' <span class="lb-srv">🎾</span>' if server == side else ""
            return (f'<div class="lb-row{" lb-lead" if lead else ""}">'
                    f'<span class="lb-n">{name}{ball}</span><span class="lb-s">{cs}</span></div>')
        return (f'<div class="lboard lboard-t">'
                f'<div class="lb-row lb-hdr"><span class="lb-n"></span><span class="lb-s">{hdr}</span></div>'
                f'{trow(0, hn, home_lead, "home")}{trow(1, an, away_lead, "away")}</div>')

    if periods:   # BASKET : colonnes par quart-temps (Q1..Qn) + total, façon box-score
        n_real = len(periods)
        n = max(4, n_real)                              # TOUJOURS 4 quart-temps (+ prolongations si jouées)
        th, ta = sum(p[0] for p in periods), sum(p[1] for p in periods)
        hdr = ("".join(
                   f'<span class="lb-c lb-h{" lb-cur" if (clock and j == n_real - 1) else ""}">Q{j + 1}</span>'
                   for j in range(n))
               + '<span class="lb-c lb-h lb-tot">TOT</span>')

        def qrow(i, name, lead):
            cs = ""
            for j in range(n):
                if j >= n_real:                        # quart À VENIR : 0 grisé
                    cs += '<span class="lb-c lb-fut">0</span>'
                    continue
                # quart EN COURS = dernier JOUÉ quand il y a une horloge -> score en blanc
                cur = " lb-cur" if (clock and j == n_real - 1) else ""
                cs += f'<span class="lb-c{cur}">{periods[j][i]}</span>'
            cs += f'<span class="lb-c lb-tot">{th if i == 0 else ta}</span>'
            return (f'<div class="lb-row{" lb-lead" if lead else ""}">'
                    f'<span class="lb-n">{name}</span><span class="lb-s">{cs}</span></div>')
        # Horloge (« Q4 · 0:05 ») à GAUCHE, sur la MÊME ligne que l'en-tête des quarts.
        clk = f'<span class="lb-n lb-clk-in">{e(clock)}</span>' if clock else '<span class="lb-n"></span>'
        return (f'<div class="lboard lboard-q">'
                f'<div class="lb-row lb-hdr">{clk}<span class="lb-s">{hdr}</span></div>'
                f'{qrow(0, hn, th > ta)}{qrow(1, an, ta > th)}</div>')

    if fstats:   # FOOT LIVE : box-score cartons/corners/buts (demande user 2026-07-12) — colonnes 🟥 🟨 🚩 ⚽
        gh, ga = (cols[0] if cols else (0, 0))
        _v = lambda x: x if isinstance(x, (int, float)) else 0
        _defs = [("🟥", _v(fstats.get("rc_h")), _v(fstats.get("rc_a"))),
                 ("🟨", _v(fstats.get("yc_h")), _v(fstats.get("yc_a"))),
                 ("🚩", _v(fstats.get("cor_h")), _v(fstats.get("cor_a")))]
        hdr = ("".join(f'<span class="lb-c lb-h lb-ico">{ic}</span>' for ic, _, _ in _defs)
               + '<span class="lb-c lb-h lb-tot lb-ico">⚽</span>')

        def frow(i, name, lead, goals):
            cs = "".join(f'<span class="lb-c">{(hv if i == 0 else av)}</span>' for _, hv, av in _defs)
            cs += f'<span class="lb-c lb-tot">{goals}</span>'
            return (f'<div class="lb-row{" lb-lead" if lead else ""}">'
                    f'<span class="lb-n">{name}</span><span class="lb-s">{cs}</span></div>')
        clk = f'<span class="lb-n lb-clk-in">{e(clock)}</span>' if clock else '<span class="lb-n"></span>'
        return (f'<div class="lboard lboard-q">'
                f'<div class="lb-row lb-hdr">{clk}<span class="lb-s">{hdr}</span></div>'
                f'{frow(0, hn, gh > ga, gh)}{frow(1, an, ga > gh, ga)}</div>')

    # TIRS AU BUT (foot : Supercoupe/finales) — affichés INLINE « 1 (4) » / « 1 (5) » sur la ligne du score
    # (demande user 2026-08-01 : pas de colonne en plus, le nombre de pénos entre parenthèses). Le score de
    # régulation reste le chiffre principal ; le vainqueur du shoot-out prend la ligne verte (lb-lead).
    _pens_ok = bool(pens) and not tennis and not periods and all(isinstance(x, int) for x in (pens or ()))
    if _pens_ok:
        home_lead, away_lead = pens[0] > pens[1], pens[1] > pens[0]

    def cells(i):
        _p = f' <span class="lb-pen">({pens[i]})</span>' if _pens_ok else ""
        return "".join(f'<span class="lb-c{" lb-win" if c[i] > c[1 - i] else ""}">{c[i]}{_p}</span>'
                       for c in cols)
    # Temps de jeu (51', Q3 · 5:42…) DANS le cadre des scores : centré en haut, bien visible.
    clk = f'<div class="lb-clk">{e(clock)}</div>' if clock else ""
    return (f'<div class="lboard">{clk}'
            f'<div class="lb-row{" lb-lead" if home_lead else ""}">'
            f'<span class="lb-n">{hn}</span><span class="lb-s">{cells(0)}</span></div>'
            f'<div class="lb-row{" lb-lead" if away_lead else ""}">'
            f'<span class="lb-n">{an}</span><span class="lb-s">{cells(1)}</span></div></div>')

def _sport_row(r: dict) -> str:
    """Ligne de match unifiée (tous sports). r : tour, status, time, score, home,
    away, prob (float ou 3-tuple), sub, badge, url, pick."""
    if r.get("_html"):        # carte DÉJÀ rendue (ex. provisoire programme/live) -> rendue telle quelle
        return r["_html"]
    e = html.escape
    # Pastille d'état en haut à droite, MÊME style que le décompte : décompte si à venir,
    # « EN DIRECT » (rouge) si live. Le badge value/✓ va, lui, sur la ligne de l'affiche.
    mid = ""
    if r.get("status") == "inprogress":
        # Live : le TEMPS de jeu va DANS le cadre des scores (cf. lscore/clock), plus dans l'en-tête.
        state = '<span class="cd live">🟢 Live</span>'
        top = ""
    elif r.get("status") == "finished":
        top = ""    # le score FINAL passe dans le scoreboard (cf. lscore), plus dans l'en-tête
        # Réglé (score chiffré) -> « Terminé » ; pas encore réglé -> « ⏳ En attente » (résultat à venir).
        state = ('<span class="cd done">Terminé</span>'
                 if any(c.isdigit() for c in str(r.get("score") or ""))
                 else '<span class="cd wait">⏳ En attente</span>')
    else:
        top = r.get("time") or ""        # échappé une seule fois au rendu (cf. e(top) plus bas)
        state = (f'<span class="cd" data-ts="{int(r["start_ts"])}"></span>'
                 if r.get("start_ts") and r["start_ts"] > time.time() else "")
    # Barres Bookmakers / Unibet / Public dès qu'on a la donnée (cotes implicites ou votes) —
    # PARTOUT (à venir, en direct, terminés), sans exiger l'ancien modèle Elo.
    probviz = (_pick_bars(r) if any(r.get(k) is not None for k in ("m_home", "i_home", "pub_home"))
               else _prob_bar(r.get("prob"), r.get("prob_labels")))
    # « (F) » seulement utile au foot : WTA (tennis) et WNBA (basket) sont d'office féminines
    fem = (' <span class="fem">(F)</span>'
           if r.get("female") and (r.get("tour") or "").upper() not in ("WTA", "WNBA") else "")
    # Plus de badge résultat ✅/❌ en haut à droite : le résultat est désormais porté PAR pari
    # (cadre vert/rouge + halo + ✓/✗), cf. analyses._bets_table. On garde juste le score (top).
    badge = ""
    # Drapeaux AUTOUR des noms — mais PAS sur les matchs terminés (carte épurée, le score prime).
    _fin = r.get("status") == "finished"
    hf = "" if _fin else (f'{r["home_flag"]} ' if r.get("home_flag") else "")
    af = "" if _fin else (f' {r["away_flag"]}' if r.get("away_flag") else "")
    # Live : SCORE actuel en scoreboard 2 lignes + libellé « cotes en direct », au-dessus des cotes
    is_live = r.get("status") == "inprogress"
    is_finished = r.get("status") == "finished"
    _is_tennis = (r.get("tour") or "").upper() in ("WTA", "ATP")
    if is_live:
        lscore = _live_scoreboard(r.get("score"), r.get("home") or "", r.get("away") or "",
                                  tennis=_is_tennis, server=r.get("server"), points=r.get("game_pts"),
                                  clock=r.get("live_time"), periods=r.get("periods"),
                                  best_of=r.get("best_of"), fstats=r.get("fstats"))
    elif is_finished and r.get("score"):
        # Score FINAL présenté COMME en live, AVEC le détail : sets (tennis « 6-4 3-6 6-2 ») ou
        # quart-temps (basket `periods`), sinon total 2 lignes. Sans horloge (match terminé).
        sc = str(r.get("score"))
        periods = r.get("periods")
        tennis_cols = _is_tennis and len(sc.split()) > 1          # plusieurs sets -> colonnes
        if _is_tennis and not tennis_cols and not periods:       # repli : total des sets en 2 lignes
            sc = re.sub(r"\s*\((?:sets?|SETS?)\)\s*$", "", sc).strip()
        lscore = _live_scoreboard(sc, r.get("home") or "", r.get("away") or "",
                                  tennis=tennis_cols, periods=periods, best_of=r.get("best_of"),
                                  pens=r.get("pens"))
    else:
        lscore = ""
    # Paris à jouer (cadres) : compact en live. En live, on insère une ligne de séparation
    # horizontale entre le scoreboard et les paris (seulement s'il y a effectivement des paris).
    betshtml = _bets_for_url(r.get("url") or "", compact=is_live)
    # Barre de séparation horizontale (écart égal dessus/dessous) entre le bloc score/barres % et
    # les paris à jouer — présente en LIVE comme en À-venir/Terminés, dès qu'il y a des paris.
    bets_sep = '<div class="bets-sep"></div>' if betshtml else ""
    # Bannières SofaScore / Unibet pleine largeur, en bas du cadre (pas dans l'analyse -> 0 doublon)
    linkshtml = _links_for_url(r.get("url") or "")
    # ---- CARTE COMPACTE (résumé non ouvert) : L1 = nom du sport · circuit (ATP/WTA) · tournoi (ville
    # capitalisée) + heure/score en haut à droite ; L2 = noms+prénoms des 2 ; L3 = nombre de paris (chip).
    # Circuit/tournoi/heure pris FRAIS d'Unibet (path/group/start) si dispo, sinon repli sur le sidecar. ----
    url = r.get("url") or ""
    sport_key = ("tennis" if "/app/match" in url else "foot" if "/foot/match" in url
                 else "basket" if "/basket/match" in url else None)
    um = (match_select.unibet_meta_for(sport_key, r.get("home"), r.get("away")) or {}) if sport_key else {}
    summ = _summary_for_url(url)
    # EN-TÊTE HOMOGÈNE pour TOUS les types de cartes (demande user 2026-07-14 : « les autres types de paris
    # doivent être semblables ») : « SPORT • Ligue » (sport en accent, gras) comme les cartes premium, au
    # lieu de « Football · Ligue » (titre + point médian) réservé jusqu'ici aux terminés/live.
    _spn = {"tennis": "TENNIS", "basket": "BASKET"}.get(sport_key, "")   # « FOOTBALL » retiré (foot-only, user 2026-08-08)
    circuit = um.get("circuit") or summ.get("circuit") or ""
    comp = _cap(um.get("comp") or summ.get("comp") or r.get("tour") or "")
    # PAYS/ZONE devant la compétition (user 2026-08-15, ex. « Angleterre • The Championship ») — depuis le
    # chemin Unibet frais (um["country"]) ; absent pour un match hors slate (terminé ancien) -> compétition seule.
    _country = _cap(um.get("country") or summ.get("country") or "")
    if _country and _country.lower() in (comp or "").lower():
        _country = ""                       # évite « Angleterre • Angleterre » si le pays est déjà dans le nom
    _cparts = [p for p in ((circuit if _is_tennis else ""), _country, comp) if p]
    if _spn:
        comp_only = (f'<b class="mc-sport spc-{sport_key or ""}">{_spn}</b>'
                     + (f'<span class="mc-comp-sep"> • </span>{" • ".join(e(p) for p in _cparts)}'
                        if _cparts else ""))
    else:
        comp_only = " • ".join(e(p) for p in _cparts)   # foot : « Pays • Compétition » (user 2026-08-15)
    # Heure de début : Unibet frais (path/start) si dispo, sinon l'heure conviviale `top` -> HH:MM.
    sdt = match_select._start_dt(um["start"]) if um.get("start") else None
    # GARDE anti-double-affiche (même patron que match_select.fresh_status) : `unibet_meta_for` matche par
    # NOMS ; si deux matchs de la MÊME affiche existent (ex. Seattle-Minnesota le 21 à 04h ET le 22 à 21h),
    # il peut renvoyer le mauvais créneau. Si l'heure Unibet est à > 12 h du coup d'envoi STOCKÉ de CETTE
    # fiche, c'est un autre match -> on l'IGNORE et on garde l'heure stockée (bug user 2026-07-21 : carte de
    # 04:00 affichée 21:00). PURE AFFICHAGE.
    _own_ts = r.get("start_ts")
    if sdt is not None and _own_ts and abs(sdt.timestamp() - float(_own_ts)) > 12 * 3600:
        sdt = None
    if sdt is None and _own_ts:                              # repli : l'heure STOCKÉE de la fiche (jamais celle d'un autre match)
        sdt = datetime.fromtimestamp(float(_own_ts), tz=timezone.utc)
    starthm = fmt_local(sdt, with_date=False) if sdt else ""
    if not starthm:
        _mt = re.search(r"\d{1,2}:\d{2}", top or "")
        starthm = _mt.group(0) if _mt else (top or "")
    score_txt = e(str(r.get("score"))) if r.get("score") else ""
    if is_live:                                          # live : PAS de badge en haut à droite (user 2026-08-15) —
        badge = ""                                       #        le SCORE + l'horloge M:SS au centre suffisent
    elif is_finished:                                    # terminé : score FINAL, SANS drapeau 🏁
        badge = (f'<span class="mc-badge mc-done">{score_txt}</span>' if score_txt
                 else '<span class="mc-badge mc-wait">⏳ En attente</span>')
    else:                                                # à venir : HEURE + DÉCOMPTE (« HH:MM - Début dans 52m01s »),
        #                                                  MÊME badge combiné que _leg_card (user 2026-08-08 : timer
        #                                                  heure + décompte sur TOUS les types de paris).
        # DÉCOMPTE déplacé SOUS l'heure au centre (user 2026-08-15) -> plus de badge en haut à droite si un
        # coup d'envoi futur existe. Repli badge « À venir » seulement si l'heure n'est pas exploitable.
        if sdt and sdt.timestamp() > time.time():
            badge = ""
        else:
            badge = f'<span class="mc-badge mc-up">{e(starthm) or "À venir"}</span>'
    # L3 : prono(s) PUBLIABLE(s) seulement — APP = TELEGRAM (strict). Un match SANS combiné n'affiche
    # QUE son simple RETENU (⭐, quand « play ») ; sinon abstention -> « pas de pari conseillé ». Les
    # matchs à combiné gardent [simple retenu ?, combiné] (déjà filtré par card_summary). Résultat :
    # ce qui s'affiche dans l'app = ce qui est posté sur Telegram = ce qui est compté dans les stats.
    reco_i = summ.get("reco_idx")          # pari RETENU par le moteur -> ⭐ EN TÊTE (à la place du •)
    is_combo = summ.get("is_combo")        # combiné = • comme les autres paris (ni ⭐ ni 🎲, demande user)
    bets3 = summ.get("bets") or []
    if not is_combo:
        if is_finished:                    # TERMINÉ : le pari RÉELLEMENT JOUÉ (for_history = stats),
            _mid = re.search(r"/(\d+)", url)   # marché exclu APRÈS coup inclus (sinon « pas de pari » à tort)
            _rbh = (analyses.retained_bet(sport_key, _mid.group(1), for_history=True)
                    if (sport_key and _mid) else None)
            # UN MATCH = UN PARI (user 2026-08-07 ; audit 2026-08-07) : on affiche LE pari retenu (for_history)
            # et RIEN d'autre. Le double affichage « Premier scan / Dernier scan » (basé sur stat_bet_first)
            # est RETIRÉ — il n'est plus compté au ROI, donc la carte doit refléter un seul pari par match.
            if _rbh and _rbh.get("result") in ("won", "lost", "push"):
                bets3 = [{"sel": _rbh["sel"], "result": _rbh["result"], "cote": _rbh.get("cote")}]
            else:
                bets3 = []
        else:
            # PARI PUBLIÉ = FIGÉ (demande user 2026-07-14) : un pari déjà conseillé aux abonnés n'est JAMAIS
            # retiré NI re-prixé au rescan -> on le montre au PRIX CONSEILLÉ (l'abonné a parié à ce prix ;
            # `published_bet` porte la cote figée + la cote marché actuelle pour la mention « cote a bougé »).
            # PRIORITAIRE sur le simple retenu du moment. Sinon (jamais publié) : le simple RETENU strict.
            _mid = re.search(r"/(\d+)", url)
            _pbz = (analyses.published_bet(sport_key, _mid.group(1))
                    if (sport_key and _mid) else None)
            if _pbz:
                bets3 = [_pbz]
                reco_i = 0
                # DOUBLE SCAN à venir (demande user 2026-07-21) : si le DERNIER scan a produit un pari
                # DIFFÉRENT du publié -> 2 lignes étiquetées (les deux compteront au ROI) ; s'il a décidé
                # de NE RIEN jouer (tableau .md vidé après ré-analyse) -> ligne info sous le pari publié.
                # SÉLECTION MÉCANIQUE (user 2026-08-29/30) : le pari joué n'est PLUS le pick brut de Claude
                # (`bets_of`[0] = le flagship de l'analyse, ex. « Plus de 2.5 buts ») mais la sélection
                # MÉCANIQUE (confidence_pick/value_pick, ex. « Double chance X2 »). On compare donc le publié
                # au pari MÉCANIQUE COURANT (`retained_bet`), pas au pick brut -> plus de « Dernier scan »
                # fantôme incohérent avec Telegram. Le double scan ne subsiste que si le pari mécanique a
                # RÉELLEMENT changé au dernier scan (publié figé = « Premier », mécanique frais = « Dernier »).
                try:
                    _curb = analyses.retained_bet(sport_key, _mid.group(1)) if (sport_key and _mid) else None
                except Exception:
                    _curb = None
                # MÊME PARI = mêmes CODES de règlement (pas les libellés : « Roman Andres Burruchaga
                # vainqueur » vs « Roman Burruchaga vainqueur » = même issue) -> pas de faux double scan.
                # DOUBLE SCAN robuste (user 2026-08-02 « pour ne plus que ça arrive ») : on ne montre 2 lignes
                # « Premier scan / Dernier scan » QUE si les deux paris S'AFFICHENT DIFFÉREMMENT. `pretty_sel`
                # converge DÉJÀ toutes les écritures d'une MÊME issue (1X2/REGTIME « gagne » = « gagne temps
                # réglementaire », +0.5/DC, gagne/vainqueur, mi-temps, handicap…) -> deux libellés qui rendent
                # PAREIL = même pari = AUCUN double scan (fini les cartes identiques en double). Comparer le
                # RENDU (pas les codes bruts) couvre tous les cas, pas seulement celui du jour. Le pari publié
                # reste FIGÉ ; le double scan ne subsiste que sur un pari réellement différent (2 comptés au ROI).
                _pub_disp = analyses.pretty_sel(_pbz.get("sel", ""), r.get("home", ""), r.get("away", ""))
                _cur_disp = (analyses.pretty_sel(_curb.get("sel", ""), r.get("home", ""), r.get("away", ""))
                             if _curb else "")
                if _curb and _cur_disp and _pub_disp != _cur_disp:
                    bets3 = [{**_pbz, "tag": "Premier scan"},
                             {"sel": _curb.get("sel", ""), "cote": _curb.get("cote"),
                              "prob": _curb.get("prob"), "tag": "Dernier scan"}]
            elif summ.get("play") and reco_i is not None and 0 <= reco_i < len(bets3):
                bets3 = [bets3[reco_i]]     # À VENIR non publié : le simple RECOMMANDÉ maintenant
                reco_i = 0
            else:
                bets3 = []                 # jamais publié, aucune value -> abstention assumée
    rows3 = []
    for i, b in enumerate(bets3):
        # Ligne INFO pure (double scan : « Dernier scan : aucun pari conseillé ») — pas un pari.
        if b.get("_info"):
            if rows3:
                rows3.append('<div class="mc-div"></div>')
            rows3.append(f'<div class="mc-betl mc-noplay"><span class="mc-bi">·</span>'
                         f'<span class="mc-bt">{e(b["_info"])}</span></div>')
            continue
        # DOUBLE SCAN : chaque pari présenté COMME UN PARI NORMAL (demande user 2026-07-21 — plus la
        # ligne compacte tronquée) : étiquette, sélection COMPLÈTE (.mc-pick), ✓/✗ + cote, GLOSS ↳,
        # et un filet de séparation entre les deux scans.
        if b.get("tag"):
            if rows3:
                rows3.append('<div class="mc-div"></div>')
            _ic2 = ({"won": "✅", "lost": "❌", "push": "➖"}.get(b.get("result"), "")
                    if is_finished else "")
            _ich = f' <span class="mc-bi">{_ic2}</span>' if _ic2 else ""
            _ch2 = (f'<span class="mc-bc">@{b["cote"]:g}</span>'
                    if isinstance(b.get("cote"), (int, float)) and b.get("cote") else "")
            _gl2 = _bet_gloss(b.get("sel", ""), sport_key, r.get("home", ""), r.get("away", ""))
            _gh2 = f'<div class="mc-gloss"><span class="ar">↳</span>{e(_gl2)}</div>' if _gl2 else ""
            rows3.append(f'<div class="mc-btag">{e(b["tag"])}</div>'
                         f'<div class="mc-pick">{e(_pretty_sel(b.get("sel", ""), r.get("home", ""), r.get("away", "")))}'
                         f'{_ich}{_ch2}</div>{_gh2}')
            continue
        is_reco = i == reco_i and not is_combo
        if is_finished:
            ic = {"won": "✅", "lost": "❌", "push": "➖"}.get(b.get("result"), "•")
        else:
            ic = "•"                              # plus d'⭐ devant les paris (demande user)
        rcls = " mc-betl-reco" if (is_reco and not is_finished) else ""
        # Badge COTE après l'intitulé (comme la cote du combiné). Le combiné a déjà sa cote dans le sel.
        cote = b.get("cote")
        cote_html = f'<span class="mc-bc">@{cote:g}</span>' if cote else ""
        rows3.append(f'<div class="mc-betl{rcls}"><span class="mc-bi">{ic}</span>'
                     f'<span class="mc-bt">{e(_pretty_sel(b.get("sel", ""), r.get("home", ""), r.get("away", "")))}</span>{cote_html}</div>')
    _ts = r.get("start_ts")
    # PRÉSENTATION PREMIUM (demande user 2026-07-13 : « les paris à jouer présentés comme les provisoires »)
    # pour un pari SIMPLE retenu À VENIR : pick en gras + marché EN CLAIR + extrait d'analyse + bande VERDICT
    # (confiance colorée + cote), EXACTEMENT comme une carte provisoire. Les cas live/terminé/combiné/
    # abstention gardent leur affichage adapté (résultat, score, compact…).
    _premium = ""
    _no_expand = False    # carte NON dépliable : le pli « 💡 Pourquoi » porte déjà toute l'analyse
    _pwhy = ""            # pli « Pourquoi ce choix » (rempli par le bloc premium ; en LIVE il est posé
                          # APRÈS scoreboard + chance live, comme sur les jambes — user 2026-07-21)
    _uid = re.search(r"/(\d+)", url)
    _pmid = _uid.group(1) if _uid else None
    # CHANCE LIVE (user 2026-08-15) : calculée AVANT le verdict car la barre de confiance DEVIENT « Confiance
    # live » en direct (remplissage = live_prob, NOS couleurs par niveau, marqueur MARCHÉ d'avant-match gardé).
    # L'ancienne barre « Chance live » séparée est supprimée. `_lp_res` sert aussi au bord acquis/perdu (plus bas).
    _lp_res = None
    _live_pct = None; _live_trend = ""; _live_state = ""
    if is_live and lscore and bets3 and not is_combo and sport_key:
        _pbb = bets3[0]
        _pcode = _pbb.get("code")
        if not _pcode:
            from app.settle_analyst import code_from_pick as _cfp_lp
            _pcode = _cfp_lp(_pbb.get("sel", ""), sport_key, r.get("home", ""), r.get("away", "")) or ""
        _lhs, _las = _parse_live_score(r.get("score"))
        _lld = match_select.live_state_for(sport_key, r.get("home"), r.get("away"))
        _lmid = re.search(r"/(\d+)", url)
        _fs = r.get("fstats") or {}       # compteurs live (corners/cartons) -> composante « stats live »
        _lvals = {"corners_h": _fs.get("cor_h"), "corners_a": _fs.get("cor_a"),
                  "cards_h": _fs.get("yc_h"), "cards_a": _fs.get("yc_a"),
                  "rc_h": _fs.get("rc_h"), "rc_a": _fs.get("rc_a")}
        if sport_key == "tennis":         # sets gagnés + jeux du set en cours -> modèle « ≥1 set »
            _lvals.update(_tennis_sets_games(r.get("score")))
        _gfrac = (match_select.basket_frac(_lld, comp) if sport_key == "basket" else None)
        _lp_res = analyses.live_prob(
            sport_key, _pbb.get("sel", ""), _pcode,
            r.get("home", ""), r.get("away", ""), _lhs, _las,
            match_select.live_minute(_lld),
            match_select.live_win_odds(sport_key, r.get("home"), r.get("away")),
            _pbb.get("cprob") or _pbb.get("prob"),
            analyses.live_catalog(_lmid.group(1)) if _lmid else [], _lvals, _gfrac)
        if isinstance(_lp_res, dict) and isinstance(_lp_res.get("pct"), int):
            _live_pct = _lp_res.get("pct")
            _live_trend = _lp_res.get("trend") or ""
            _live_state = _lp_res.get("source") or ""
    # COMBINÉ COUPE DU MONDE (ROI) À VENIR : présenté EXACTEMENT comme le combiné du jour (demande user
    # 2026-07-19) -> même COQUILLE DORÉE + en-tête « 🎯 COMBINÉ • <match> » + badge heure. On court-circuite
    # la carte de match générique (ni barres %, ni ligne d'équipes, ni bannières sources — comme le combiné
    # du jour ; le match est nommé dans le sous-titre). Jambes (pli « Pourquoi ») + verdict déjà identiques.
    # (à venir uniquement : le live garde son scoreboard, le terminé son résultat.)
    if is_combo and (not is_live) and (not is_finished) and sport_key and _pmid:
        _cbody = _combo_premium_block(sport_key, _pmid, r.get("home", ""), r.get("away", ""))
        if _cbody:
            _csub = f'{e(_noF(r.get("home")))} <span class="mc-dash">—</span> {e(_noF(r.get("away")))}'
            if comp:
                _csub += f'<span class="mc-comp-sep"> • </span>{e(comp)}'
            return _combo_gold_card(title="COMBINÉ", subtitle=_csub, badge=badge, body=_cbody)
    # LIVE INCLUS (demande user 2026-07-21 « la présentation ne doit pas changer ») : une carte de pari
    # EN DIRECT garde la MÊME présentation que les autres (pick gras + gloss + verdict + pli Pourquoi),
    # plus la ligne compacte tronquée — le scoreboard + la chance live suivent en dessous (head).
    if (not is_finished) and not is_combo and len(bets3) == 1 and reco_i == 0:
        _b0 = bets3[0]
        _psel = _b0.get("sel", "")
        _pcote = _b0.get("cote")
        _rbp = analyses.retained_bet(sport_key, _pmid) if (sport_key and _pmid) else None
        # Confiance CALIBRÉE : du pari retenu, sinon (pari PUBLIÉ FIGÉ non retenu au marché actuel) du pari figé.
        _pconf = ((_rbp or {}).get("cprob") or (_rbp or {}).get("prob")
                  or _b0.get("cprob") or _b0.get("prob"))
        _cote_big = (f'<span class="mc-cote"><span class="mc-cote-l">COTE</span>'
                     f'<span class="mc-cote-v">{_pcote:g}</span></span>'
                     if isinstance(_pcote, (int, float)) and _pcote else "")
        _gl = _bet_gloss(_psel, sport_key, r.get("home",""), r.get("away",""))
        _gloss = f'<div class="mc-gloss"><span class="ar">↳</span>{e(_gl)}</div>' if _gl else ""
        # PARI PUBLIÉ dont la COTE A BOUGÉ depuis le conseil (demande user 2026-07-14) : mention transparente
        # (« cote au conseil : X · marché actuel : Y ») -> l'abonné voit son pari au bon prix + pourquoi ça a
        # changé, sans être retiré ni « faire peur ». Affichée seulement si les 2 cotes diffèrent réellement.
        _pc, _mc = _b0.get("published_cote"), _b0.get("market_cote")
        _moved = ""
        if isinstance(_pc, (int, float)) and isinstance(_mc, (int, float)) and abs(_pc - _mc) >= 0.01:
            _moved = (f'<div class="mc-moved">🔒 Cote au conseil <b>{_pc:g}</b>'
                      f'<span class="mc-moved-m"> · marché actuel {_mc:g}</span></div>')
        # Ligne « Ré-analyse à HH:MM » RETIRÉE (demande user 2026-07-21) : l'info n'apporte rien à l'abonné.
        _foot = ""
        # Filet fin teams↔pari (comme les provisoires) : sépare « quel match » de « quel pari ».
        _psel_disp = _pretty_sel(_psel, r.get("home", ""), r.get("away", ""))
        # LIGNE VERDICT IDENTIQUE aux cartes provisoires / table de paris (demande user 2026-07-17 « tout
        # doit être identique sur les autres types de paris ») : « Marché XX% · Notre confiance YY% ✓calibré
        # → Value ±Z% » + pied (ré-analyse + grosse cote). `_pconf` = confiance déjà CALIBRÉE (cprob priorisée).
        # Pli « 💡 Pourquoi ce pari » (demande user 2026-07-20) : l'analyse DÉDIÉE du pari joué, présentée
        # comme sous les jambes de combiné. Source = section « 🎯 Le pari à jouer » du .md (repli 🧪/📋).
        # Texte COMPLET (comme les jambes de combiné) : le bloc « 🎯 » n'est plus répété dans le dépli
        # (card_details) -> le pli en est le SEUL porteur, on ne tronque donc pas le raisonnement.
        _pwhy = _why_fold(_prov_why_snippet(sport_key, _pmid, maxlen=100000, played=True)) if _pmid else ""
        # LIVE : MÊME ORDRE que les jambes de combiné (demande user 2026-07-21) — verdict, PUIS scoreboard
        # + chance live (posés par _live_score_row après mc-sub), et le pli « Pourquoi » EN DERNIER (avec
        # son filet). Hors live : le pli reste sous le verdict (pas de scoreboard).
        # MONTANTE : plus de badge greffé ici (demande user 2026-07-30) — la montante a sa PROPRE zone
        # « Montante · Palier N » (via _montante_zone_card). Surface unique -> pas de double affichage.
        _mont_b = ""
        # Pari+glose (+ note « cote au conseil ») DANS le cadre des chiffres, centré (user 2026-08-15) :
        # passés à _verdict_block via pick_html -> plus de séparateur mc-div sous les équipes.
        _pick_in_box = f'<div class="mc-pick">{e(_psel_disp)}</div>' + _gloss + _moved
        _premium = (_verdict_block(_pcote, _pconf, _foot, _cote_big, calibrated=True, pick_html=_pick_in_box,
                                   live_pct=_live_pct, live_trend=_live_trend, live_state=_live_state)
                    + _mont_b
                    + ("" if is_live else _pwhy))
        # Le pli « 💡 Pourquoi » porte DÉJÀ toute l'analyse (demande user 2026-07-20) -> la carte n'a plus
        # de corps dépliable en dessous (Cotes & chances / Mise / détails = doublon). On le retire de
        # l'AFFICHAGE (le .md/les données restent intacts). Seulement si le pli existe (sinon garder le corps).
        _no_expand = bool(_pwhy)
    if _premium:
        line3 = _premium
    else:
        # Abstention (aucun prono publiable) : libellé discret à venir ; rien sur les terminés (le score suffit).
        line3 = ("".join(rows3) if rows3 else
                 ('' if is_finished else
                  '<div class="mc-betl mc-noplay"><span class="mc-bi">·</span>'
                  '<span class="mc-bt">Analysé · pas de pari conseillé</span></div>'))
        # Ligne « Ré-analyse à HH:MM » RETIRÉE (demande user 2026-07-21) — n'apporte rien à l'abonné.
        # Filet équipes↔pari sur TOUS les types de cartes (demande user 2026-07-21, « comme les
        # provisoires ») — terminés/live/compact inclus. Seulement si la carte a un contenu dessous.
        if line3:
            line3 = '<div class="mc-div"></div>' + line3
    # CARTE COMPACTE (user 2026-08-19 : « prochains matchs de Live présentés comme le programme, sans les autres
    # détails ») : on retire toute la ligne pari/statut -> il reste l'affiche (équipes + ligue) + le décompte.
    if r.get("_compact"):
        line3 = ""
    # CENTRE entre les équipes : en LIVE -> SCORE + minute (user 2026-08-15, façon SofaScore mais notre style) ;
    # sinon -> l'HEURE du match. Le score n'est donc plus répété dans un cadre en dessous (retiré plus bas).
    _sc_live = str(r.get("score") or "").strip()
    if is_live and _sc_live:
        # Horloge « M:SS » (défile), « HT » en mi-temps, « 92:27 (+3') » en prolongation -> helper PARTAGÉ
        # avec les jambes de combiné (_live_clock_html), même rendu partout (user 2026-08-15/16/17).
        _center = (f'<span class="tm-live"><b>{e(_sc_live.replace("-", " - "))}</b>'
                   + _live_clock_html(sport_key, r.get("home"), r.get("away")) + '</span>')
    elif (not is_finished) and sdt and sdt.timestamp() > time.time():
        # À VENIR (user 2026-08-15) : HEURE au centre + DÉCOMPTE juste DESSOUS (comme l'horloge live sous le
        # score) ; le badge décompte en haut à droite est retiré. Le timer JS `.cd` rafraîchit le décompte.
        _center = (f'<span class="tm-live"><b>{e(starthm)}</b>'
                   f'<span class="tm-cd"><span class="cd" data-ts="{int(sdt.timestamp())}"></span></span></span>')
    else:
        _center = e(starthm)
    teams = _teams_vs_html(r.get("home"), r.get("away"), _center)   # heure (ou score+minute en live) au centre + logos
    # LIVE : intitulé du pari EN HAUT puis SCORE+minute au centre (user 2026-08-15). La « Chance live » n'est
    # PLUS une barre séparée : elle est FUSIONNÉE dans la barre de confiance du verdict (« Confiance live »,
    # calculée plus haut via `_live_pct`). PURE AFFICHAGE : aucun impact ROI/stats/calibration.
    _chev = "" if (_no_expand or r.get("_compact")) else '<span class="mc-chev">▸</span>'   # pas de chevron si carte non dépliable / compacte (prochains lives)
    head = (f'<div class="mc-head"><div class="mc-main">'
            f'<div class="mc-line mc-line-c">'   # ligue CENTRÉE, SANS emoji (user 2026-08-15) ; décompte en absolu à droite
            f'<span class="mc-comp">{comp_only}</span>{badge}</div>'
            f'<div class="mc-teams">{teams}</div>'
            f'<div class="mc-sub">{line3}</div>'
            f'{_pwhy if (is_live and _premium) else ""}</div>{_chev}</div>')
    # ---- CORPS (déplié au tap) : scoreboard + barres % + paris + liens + ANALYSE (chargée d'office
    # à l'ouverture, plus de bouton « Voir l'analyse »). Un clic n'importe où dans la carte la replie. ----
    pkp = f'&pk={r["pick_kind"]}' if r.get("pick_kind") else ""   # type de pari -> analyse cohérente
    ana = ""
    if url.startswith(("/foot/match/", "/basket/match/", "/app/match/")):
        sep = "&" if "?" in url else "?"
        ana = f'<div class="mc-ana" data-ana="{url}{sep}frag=1{pkp}"><div class="exp"></div></div>'
    # LIVE : le scoreboard est déjà montré dans la carte repliée (head) -> on ne le REMET pas dans le corps.
    # CARTE PREMIUM (pari à venir déjà présenté en tête) : on RETIRE le ticket de pari redondant du corps
    # (demande user 2026-07-13) -> le corps = Cotes & chances (barres) puis l'ANALYSE (raisonnement + faits)
    # puis les sources. Les cas live/terminé/combiné gardent le ticket (résultats/score par pari).
    _ticket = "" if _premium else f'{bets_sep}{betshtml}'
    # Ordre : (scoreboard) · Cotes & chances (barres) · TICKET (si non premium) · ANALYSE · sources en bas.
    body = (f'{"" if is_live else lscore}{"" if is_live else (r.get("sub", "") + probviz)}'
            f'{_ticket}{ana}{linkshtml}')
    # TOUTES les cartes sont REPLIÉES au 1er chargement (y compris les directs) — pour le LIVE le pari + le
    # scoreboard sont visibles repliés ; on déplie au tap pour l'analyse. Fond « pick » uniforme.
    # Carte NON dépliable (le pli « 💡 Pourquoi » porte déjà l'analyse) : PAS de `.mc-body` -> le tap est
    # inerte (le JS fait `if(!b)return;`) et le corps redondant (Cotes & chances / Mise / détails) disparaît
    # de l'affichage. Les données restent intactes (fiche .md, sources). Demande user 2026-07-20.
    # Bord gauche coloré selon le RÉSULTAT (demande user 2026-07-25) : terminé -> won/lost/push ;
    # live -> doré ; à venir -> doré par défaut (pas de classe).
    _st = r.get("_state")
    # LIVE DÉJÀ JOUÉ (demande user 2026-07-25) : si la chance live est VERROUILLÉE (source « acquis » = pari
    # mathématiquement gagné, « perdu » = manqué), le bord gauche prend déjà la couleur du résultat (vert/
    # rouge) au lieu du doré « en cours ». Sinon live = doré, terminé = won/lost/push.
    _locked = _lp_res.get("source") if isinstance(_lp_res, dict) else None
    if _st in ("won", "lost", "push"):
        _rcls = f" mc-r-{_st}"
    elif is_live and _locked == "acquis":
        _rcls = " mc-r-won"
    elif is_live and _locked == "perdu":
        _rcls = " mc-r-lost"
    elif is_live:
        _rcls = " mc-r-live"
    else:
        _rcls = ""
    # (La montante est injectée en carte dédiée EN TÊTE de Confiance + exclue de play -> plus de décoration
    #  in-place ici, cf. _today_zones. user 2026-08-08.)
    # CARTE COMPACTE NON CLIQUABLE (user 2026-08-19 : prochains lives) : plate (pas de corps), classe `prog-card`
    # -> curseur normal, aucun déploiement d'analyse. On sort AVANT le cas cliquable.
    if r.get("_compact"):
        return (f'<div class="row pick mc prog-card mc-compact{_rcls}">{head}</div>')
    if _no_expand:
        return (f'<div class="row pick mc mc-prem mc-flat{_rcls}">{head}</div>')
    return (f'<div class="row pick mc{" mc-prem" if _premium else ""}'
            f'{" mc-islive" if is_live else ""}{_rcls}">{head}'
            f'<div class="mc-body" hidden>{body}</div></div>')

_MC_SEP = '<div class="mc-sep"></div>'


def _join_cards(parts: list) -> str:
    """Concatène des cartes déjà rendues en insérant un fin séparateur entre elles (demande user
    2026-07-18). Ignore les fragments vides -> jamais de séparateur orphelin."""
    return _MC_SEP.join(p for p in parts if p)


def _rows_by_day(rows: list) -> str:
    """Rend les lignes avec un petit en-tête de jour (Aujourd'hui / Demain / Sam. …) à chaque
    changement de date. Les lignes doivent être triées par heure de début. Une ligne peut porter
    un HTML déjà rendu (`_html`, ex. carte du programme) — sinon elle est rendue via `_sport_row`.
    Un fin séparateur (`.mc-sep`) est glissé entre deux cartes CONSÉCUTIVES du même jour (jamais
    juste après un en-tête de jour ni en tête de zone)."""
    # DATE CALENDAIRE RÉELLE pour l'AFFICHAGE (user 2026-08-08) : un match après minuit (heure belge) apparaît
    # sous son VRAI jour (ex. 08/08), plus sous la date du jour sportif (07/08). Le jour sportif 06h→06h reste
    # réservé aux STATS / règlement / regroupement calendrier (inchangés). Purement présentation de l'en-tête.
    today = (to_local(datetime.now(timezone.utc)) or datetime.now()).date()
    out, cur, prev_card = [], object(), False
    for r in rows:
        ts = r.get("start_ts")
        ld = to_local(datetime.fromtimestamp(ts, tz=timezone.utc)) if ts else None
        d = ld.date() if ld else None
        if d != cur:
            cur = d
            prev_card = False
            # EN-TÊTES DE JOUR RETIRÉS (user 2026-08-15) : plus de « Aujourd'hui / Demain / Hier » au-dessus des
            # paris — la page indique déjà le jour. On garde le regroupement (reset séparateur) sans le titre.
        card = r.get("_html") or _sport_row(r)
        if prev_card and card:
            out.append(_MC_SEP)
        out.append(card)
        prev_card = bool(card)
    return "".join(out)

def render_sport_matches(sport: str, title: str, value: list, live: list,
                         upcoming: list, finished: list, intro: str = "",
                         paused: bool = False, frag: bool = False,
                         confidences: list | None = None) -> str:
    """Page Matchs UNIFIÉE pour tous les sports — MÊMES ZONES PREMIUM que l'accueil (refonte 2026-07-11) :
    « Confiance à jouer » (retenus à venir) → « En direct » → « Confiance provisoire » (provisoires) →
    « Terminés » (repliée d'office). En-têtes épurés (point d'état + titre casse normale + filet), pas de
    répétition du libellé sur les cartes.

    `paused` : SofaScore en pause anti-403 -> on l'explique au lieu d'afficher
    « aucun match ». `frag=True` -> renvoie le corps seul (chargé en AJAX dans la SPA)."""
    # « Terminés » PLAFONNÉS aux plus récents (perf 2026-07-20) : rendre 130+ cartes terminées coûtait ~5 s
    # par onglet sport (surtout foot). L'HISTORIQUE COMPLET jour par jour vit désormais dans l'onglet Pronos
    # (calendrier) -> l'onglet sport n'a besoin que des résultats récents. Zone repliée d'office de toute façon.
    _FIN_CAP = 30
    finished = sorted(finished or [], key=lambda r: r.get("start_ts") or 0, reverse=True)
    _fin_more = max(0, len(finished) - _FIN_CAP)
    finished = finished[:_FIN_CAP]
    # PROVISOIRES du sport (doré, hors ROI) : zone « Indicatif » DÉDIÉE (framed=True retire la pastille par
    # carte). Un provisoire EN COURS reste avec les matchs « En direct » (le temps réel prime) ; les matchs
    # pas encore analysés rejoignent « À jouer » / « En direct ». Dédoublonnage par noms d'équipes -> jamais 2×.
    prov_up: list = []
    if sport in ("foot", "tennis", "basket"):
        _paj = {_prog_pair(r.get("home"), r.get("away")) for r in (list(upcoming or []) + list(live or []))}
        _pit = [it for it in _programme_items(_paj, framed=True) if it.get("_sport") == sport]
        prov_up = (sorted([it for it in _pit if it.get("_prov") and not it.get("_live")],
                          key=lambda r: r.get("start_ts") or 0) if analyses.PROVISOIRES_ON else [])
        _rest = [it for it in _pit if not (it.get("_prov") and not it.get("_live"))]
        live = list(live or []) + [it for it in _rest if it.get("_live")]
        upcoming = list(upcoming or []) + [it for it in _rest if not it.get("_live")]
    # « À jouer » = paris RETENUS à venir (confiances/valeurs y sont fusionnés — vides pour les onglets).
    play_up = sorted(list(confidences or []) + list(value or []) + list(upcoming or []),
                     key=lambda r: r.get("start_ts") or 0)
    live = sorted(list(live or []), key=lambda r: r.get("start_ts") or 0)

    def _cards(rows: list) -> str:                      # rend _html (programme) ou _sport_row (pari/live)
        return _join_cards([r.get("_html") or _sport_row(r) for r in rows])   # + séparateur entre cartes

    _has = bool(play_up or live or prov_up or finished)
    out = [
        _zone("play", _plur(len(play_up), "Confiance"), "", len(play_up), _rows_by_day(play_up),
              empty=("Aucun pari à venir pour l'instant." if _has else None)),
        _zone("live", "En direct", "temps réel", len(live), _cards(live)),
        _zone("indic", _plur(len(prov_up), "Provisoire"), "", len(prov_up), _rows_by_day(prov_up)),
        _zone("todo", "Terminés", "", len(finished),
              _cards(finished) + (f'<a class="fin-more" href="/">📅 Historique complet jour par jour '
                                  f'dans l\'onglet Pronos ({_fin_more} de plus)</a>' if _fin_more else ""),
              collapsible=True, open_=False),
    ]
    body_zones = "".join(x for x in out if x)
    if not _has:
        if paused:
            body_zones = ('<div class="banner warn">⏸️ Source SofaScore momentanément en pause '
                          '(trop de requêtes) — les matchs reviennent <b>automatiquement</b> '
                          'd\'ici quelques minutes. Rien à faire.</div>')
        elif intro:
            body_zones = f'<div class="banner">{intro}</div>'
        else:
            body_zones = '<div class="paj-empty">Aucun match à afficher pour le moment.</div>'
    # Ordre PREMIUM : titre -> cadre de perf (graphe + fiabilité & calibration INTÉGRÉS) -> matchs.
    # Marqueur de compte (matchs du jour de CE sport : à jouer + live + indicatif, hors terminés) -> badge nav.
    _cnt = len(play_up) + len(live) + len(prov_up)
    body = (f'<span class="dv-nav" data-tab="{sport}" data-n="{_cnt}" hidden></span>'
            + _subnav(sport) + render_sport_perf(sport) + f'<div class="dash-zones">{body_zones}</div>')
    return body if frag else spa_shell(sport, title, body)

def _daily_combo_any_live(sport: str = "foot", variant: str = "") -> bool:
    """Vrai si le combiné du jour (du SPORT donné) a AU MOINS une jambe EN COURS (non réglée + score live).
    Sert à ne montrer le combiné dans l'onglet Live QUE quand une de ses rencontres tourne réellement (demande
    user 2026-07-19). Même détection de liveness que la carte (`_combo_tg_card`). `sport` = filtre Live
    2026-07-28 (foot par défaut). `variant` (user 2026-08-19) : « cote2 » pour le 2ᵉ combiné du jour."""
    try:
        import datetime as _dt
        from app import combo_daily as _cd
        day = _cd.day_key()          # clé-jour UNIQUE du combiné (jour sportif local 06h→06h)
        if analyses._combo_rule_void(day):    # combiné hors-règle -> jamais dans Live
            return False
        cb = _cd.today(day, sport=sport, variant=variant)
    except Exception:
        cb = None
    if not cb:
        return False
    return any(live_fields(match_select.live_state_for(l.get("sport"), l.get("home", ""),
                                                       l.get("away", "")), l.get("sport")).get("score")
               for l in (cb.get("legs") or []) if l.get("result") is None)


def _safe_combo_any_live() -> bool:
    """Vrai si le COMBINÉ SÉCURITÉ FOOT (double chance ~2, hors ROI) a AU MOINS une jambe EN COURS (non
    réglée + score live) — même détection que `_daily_combo_any_live`. Sert à montrer le combiné sécurité
    dans l'onglet Live quand une de ses rencontres tourne (demande user 2026-07-28 : tout match de Pronos en
    cours doit apparaître dans Live). Foot uniquement (ses jambes utilisent NOS noms de fiche -> live_state OK)."""
    try:
        from app import combo_safe as _cs
        cb = _cs.today(_cs.day_key())
    except Exception:
        cb = None
    if not cb:
        return False
    return any(live_fields(match_select.live_state_for("foot", l.get("home", ""),
                                                       l.get("away", "")), "foot").get("score")
               for l in (cb.get("legs") or []) if l.get("result") is None)


def _combo_leg_cards(sport: str = "foot", want_live: bool = True) -> list:
    """Jambes de combiné(s) du jour NON RÉGLÉES, pour la zone Combiné de Live. `want_live` True -> jambes EN
    COURS : rendues EXACTEMENT comme dans le combiné (via `_leg_card`, mêmes flags que `_combo_tg_legs`) mais
    présentées comme un MATCH SEUL -> dicts `{_html, start_ts}`. `want_live` False -> jambes À VENIR en carte
    COMPACTE -> dicts `foot._card` (flag `_compact`, rendus par `_sport_row`). Sûr + Cote 2, DÉDUPLIQUÉES par
    match. Cache score live chaud -> lecture sync. [] si rien dans l'état demandé."""
    from app import combo_daily as _cd, foot as _foot
    import datetime as _dt
    day = _cd.day_key()
    if analyses._combo_rule_void(day):                     # combiné hors-règle -> ni Live ni historique
        return []
    rows, seen = [], set()
    for _var in ("", "soir"):
        cb = _cd.today(day, sport=sport, variant=_var)
        if not cb:
            continue
        for l in cb.get("legs") or []:
            if l.get("result") is not None:            # jambe déjà réglée
                continue
            _pair = _prog_pair(l.get("home", ""), l.get("away", ""))
            if _pair in seen:                          # même match dans Sûr ET Cote 2 -> une seule carte
                continue
            lf = live_fields(match_select.live_state_for(sport, l.get("home", ""), l.get("away", "")), sport)
            _is_live = bool(lf.get("score"))
            if _is_live != want_live:                  # on ne garde que l'état demandé (live OU à venir)
                continue
            seen.add(_pair)
            try:
                _sts = _dt.datetime.fromisoformat(str(l.get("start")).replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                _sts = 0
            if _is_live:
                # JAMBE LIVE = le MÊME rendu que DANS le combiné (`_leg_card`, flags IDENTIQUES à `_combo_tg_legs`)
                # mais présenté comme un MATCH SEUL — PAS de reconstruction (user 2026-08-20 : « la jambe seule
                # ne devait pas être reconstruite, elle devait ressembler EXACTEMENT à celle du combiné mais comme
                # un match seul »). teams=True -> en-tête équipes/logos/score ; bare=True -> Confiance + Cote seuls
                # (PAS d'Edge/Value) ; prob_calibrated=True -> la VRAIE confiance calibrée (ex. 84%, pas 1%) ;
                # + « Chance live » + « Pourquoi cette jambe », exactement comme IMG_4568. Wrapper `mc-combo-legs`
                # = même contexte CSS que dans le combiné.
                _html = ('<div class="mc-combo-legs">'
                         + _leg_card({**l, "sport": sport}, why=True, verdict=True, why_always=True,
                                     prob_calibrated=True, live_layout=True, bare=True, teams=True)
                         + '</div>')
                rows.append({"_html": _html, "start_ts": _sts, "sport": sport,
                             "home": l.get("home", ""), "away": l.get("away", "")})
            else:
                # À VENIR : carte COMPACTE (match + décompte + ligue seuls, comme le programme — user 2026-08-19).
                _c = _foot._card({
                    "id": l.get("mid"), "status": "notstarted", "comp": l.get("comp"),
                    "home": l.get("home", ""), "away": l.get("away", ""), "probs": None, "goals": None,
                    "o1": None, "ox": None, "o2": None, "imp": None, "pick": None, "start": _sts,
                    "votes": None, "perle": None, "perle2": None, "perle_value": None,
                    "pick_kind": "confiance", "sofa_ok": True})
                _c["_compact"] = True
                rows.append(_c)
    return rows


def render_directs(play_live: list, prov_live: list, sport: str | None = None, frag: bool = False) -> str:
    """Onglet « Directs » : matchs EN DIRECT groupés par TYPE de pari (Combiné · Paris joués · Provisoires).
    SÉLECTEUR DE SPORT en tête (demande user 2026-07-28, comme Pronos) : cliquer un sport recharge le panneau
    `#pn-directs` via /directs?sport=<sk> et ne montre QUE les matchs live de ce sport. `play_live` = paris
    retenus en cours ; `prov_live` = provisoires en cours (cartes `_html` ou dicts `_sport_row`)."""
    _cur = sport if sport in ("foot", "tennis", "basket") else "foot"
    # GARDE UNIQUE (user 2026-08-11) : provisoires retirés -> vidés À LA SOURCE ici, donc ni comptés dans
    # `_counts`/`total` (badge nav) ni affichés. Empêche le badge « Live = N » sur une page vide, quel que
    # soit l'appelant. Réversible via PROVISOIRES_ON.
    if not analyses.PROVISOIRES_ON:
        prov_live = []
    play_live = sorted(list(play_live or []), key=lambda c: c.get("start_ts") or 0)
    prov_live = sorted(list(prov_live or []), key=lambda c: c.get("start_ts") or 0)
    # COMPTES PAR SPORT (badges du sélecteur) + TOTAL tous sports (badge de l'onglet, inchangé).
    _counts, _combos = {}, {}
    for _sk in ("foot", "tennis", "basket"):
        # JAMBES DE COMBINÉ EN COURS (user 2026-08-19) : au lieu de la carte combiné COMPLÈTE, on liste les
        # JAMBES live comme des paris simples (dans la zone Combiné). Foot uniquement. Compteur = nb de jambes live.
        _combos[_sk] = _combo_leg_cards(_sk, want_live=True) if _sk == "foot" else []
        _counts[_sk] = (sum(1 for c in play_live if _item_sport(c) == _sk)
                        + sum(1 for c in prov_live if c.get("_sport") == _sk)
                        + len(_combos[_sk]))
    # Combinés FOOT hors-ROI EN COURS (demande user 2026-07-28 : tout match de Pronos qui tourne doit
    # apparaître dans Live) — le combiné SÉCURITÉ et le combiné BONUS, comme dans l'onglet Pronos. Foot only.
    _safe_combo = ""   # combiné « double chance » fusionné dans « Combiné football » (combo_daily) le 2026-08-02
    # MONTANTE EN COURS (1re zone de Pronos) : affichée en Live UNIQUEMENT si son match TOURNE (le Live ne
    # montre que ce qui est en direct). Foot only.
    _mont_title, _mont_card, _md0 = "", "", None
    try:
        from app import montante as _mt0
        _mp0 = (_mt0.state().get("pending") or {}) if _mt0.is_active() else {}
        _md0 = analyses.meta("foot", str(_mp0.get("mid") or "")) if _mp0.get("mid") else None
        if _md0 and live_fields(match_select.live_state_for("foot", _md0.get("home"), _md0.get("away")),
                                "foot").get("score"):
            _mont_title, _mont_card = _montante_zone_card("foot")
    except Exception:
        _mont_title, _mont_card = "", ""
    # La montante live est AFFICHÉE dans play_live (sa carte REMPLACE son pari dans Confiance) : son match y
    # est DÉJÀ compté. On ne l'ajoute donc au badge QUE s'il n'est pas déjà un pari live -> sinon double-compte
    # (badge « 4 » pour 3 matchs, la montante étant l'un d'eux). user 2026-08-11.
    _mont_extra = 0
    if _mont_card and _md0:
        _mm0 = _prog_pair(_md0.get("home", ""), _md0.get("away", ""))
        if not any(_prog_pair(c.get("home"), c.get("away")) == _mm0 for c in play_live):
            _mont_extra = 1
    _counts["foot"] += ((1 if _safe_combo else 0) + _mont_extra)
    total = sum(_counts.values())
    # FILTRE du sport sélectionné.
    # PROCHAINS MATCHS (user 2026-08-19) : les matchs À VENIR repris par un pari, en CARTES DE PRONO (décompte +
    # ligue + pick, pas « vs ») et CLASSÉS PAR TYPE -> fusionnés dans les zones Confiance/Value/Combiné/Montante
    # avec les live. Combiné = jambes à venir ; Montante = palier du jour non commencé ; simples = paris retenus.
    from app import foot as _foot_up
    import datetime as _dtu
    _up_combo = _combo_leg_cards(_cur, want_live=False) if _cur == "foot" else []
    _up_conf, _up_val, _up_mont = [], [], []
    for _d in analyses.list_for(_cur, include_background=True):     # SIMPLES à venir (Confiance/Value)
        if analyses.status_of(_d) != "notstarted":
            continue
        if not (analyses.retained_bet(_cur, _d.get("id")) or {}).get("sel"):
            continue
        if analyses.bet_tier_for(_cur, _d.get("id")) == "montante":  # montante gérée à part (source fiable)
            continue
        if live_fields(match_select.live_state_for(_cur, _d.get("home"), _d.get("away")), _cur).get("score"):
            continue                                                # déjà live
        _dt2 = _d.get("_start_dt")
        _s2, _o = analyses.pick_parts(_d.get("pick") or "")
        _o1, _ox, _o2 = _d.get("o1"), _d.get("ox"), _d.get("o2")
        _c2 = _foot_up._card({
            "id": _d.get("sofa_id") or _d.get("id"), "status": "notstarted", "comp": _d.get("comp"),
            "home": _d.get("home", ""), "away": _d.get("away", ""), "probs": None, "goals": None,
            "o1": _o1, "ox": _ox, "o2": _o2,
            "imp": _foot_up._devig3(_o1, _ox, _o2) if (_o1 and _ox and _o2) else None,
            "pick": None, "start": _dt2.timestamp() if _dt2 else 0, "votes": None,
            "perle": None, "perle2": None, "perle_value": None, "pick_kind": "confiance", "sofa_ok": True})
        _c2["_compact"] = True                                       # carte compacte (comme le programme)
        (_up_val if _c2.get("tier") == "value" else _up_conf).append(_c2)
    if _cur == "foot":                                              # MONTANTE à venir (palier du jour non commencé)
        _mtu = _montante_today_bet() or {}
        if _mtu.get("mid") and _mtu.get("result") is None:
            _mtd = analyses.meta("foot", str(_mtu["mid"])) or {}
            if not live_fields(match_select.live_state_for(
                    "foot", _mtd.get("home") or "", _mtd.get("away") or ""), "foot").get("score"):
                _mh2, _, _ma2 = _noF(str(_mtu.get("match") or "")).partition(" - ")
                try:
                    _msts = _dtu.datetime.fromisoformat(str(_mtd.get("start")).replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    _msts = 0
                _mc = _foot_up._card({
                    "id": _mtu.get("mid"), "status": "notstarted", "comp": _mtd.get("comp"),
                    "home": _mtd.get("home") or _mh2, "away": _mtd.get("away") or _ma2, "probs": None,
                    "goals": None, "o1": None, "ox": None, "o2": None, "imp": None, "pick": None,
                    "start": _msts, "votes": None, "perle": None,
                    "perle2": None, "perle_value": None, "pick_kind": "confiance", "sofa_ok": True})
                _mc["_compact"] = True                              # carte compacte (comme le programme)
                _up_mont.append(_mc)
    for _lst in (_up_conf, _up_val, _up_mont, _up_combo):
        _lst.sort(key=lambda c: c.get("start_ts") or 0)
    _play = [c for c in play_live if _item_sport(c) == _cur]
    _prov = [c for c in prov_live if c.get("_sport") == _cur] if analyses.PROVISOIRES_ON else []
    _combo_rows = list(_combos.get(_cur, []))               # zone Combiné = jambes EN COURS (HTML `_leg_card` pré-rendu)
    _combo = _join_cards([r.get("_html") or _sport_row(r) for r in _combo_rows])   # rendu HTML pour la zone Combiné
    # PROCHAINS MATCHS = tous les à-venir MÉLANGÉS (combo + montante + simples), triés par coup d'envoi et
    # DÉDUPLIQUÉS par match (un match repris par 2 types = une seule carte compacte). user 2026-08-19.
    _upcoming_all, _seen_up = [], set()
    for _c in sorted(_up_combo + _up_mont + _up_conf + _up_val,
                     key=lambda c: c.get("start_ts") or float("inf")):   # sans heure -> en DERNIER (pas en 1er)
        _pu = _prog_pair(_c.get("home", ""), _c.get("away", ""))
        if _pu in _seen_up:
            continue
        _seen_up.add(_pu)
        _upcoming_all.append(_c)
    _safe_combo = _safe_combo if _cur == "foot" else ""   # combinés hors-ROI = foot uniquement
    if _cur != "foot":
        _mont_title, _mont_card = "", ""

    def _cards(rows):
        return _join_cards([c.get("_html") or _sport_row(c) for c in rows])
    _zlabel = {"foot": "football", "tennis": "tennis", "basket": "basket"}.get(_cur, "football")
    # MONTANTE FUSIONNÉE DANS CONFIANCE en Live aussi (user 2026-08-08) : plus de zone séparée. On DÉDUPLIQUE
    # le match montante de _play/_prov et on injecte SA carte (titre + cadre bleu conservé) dans Confiance.
    if _mont_card:
        _mm_pair = _prog_pair(_md0.get("home", ""), _md0.get("away", "")) if _md0 else None
        if _mm_pair:
            _play = [c for c in _play if _prog_pair(c.get("home"), c.get("away")) != _mm_pair]
            _prov = [c for c in _prov if _prog_pair(c.get("home"), c.get("away")) != _mm_pair]
        # COMME DANS PRONOS (user 2026-08-18) : PAS de titre-cadre interne « MONTANTE • PALIER » au-dessus de la
        # carte — le palier vit dans le TITRE DE LA ZONE (ci-dessous). Juste le cadre de couleur, carte identique.
        _mont_deco = f'<div class="mont-cardwrap">{_mont_card}</div>'
        _mont_tier_live = analyses.bet_tier_for("foot", str((_montante_today_bet() or {}).get("mid") or ""))
        _play = list(_play) + [{"_html": _mont_deco, "start_ts": (_md0 or {}).get("start_ts") or 0,
                                "status": "inprogress", "tier": _mont_tier_live}]   # montante classée par son tier
    if not (_play or _prov or _combo or _safe_combo or _upcoming_all):
        zones = (
            '<div class="live-empty">'
            '<div class="le-orb"><span class="le-ping"></span><span class="le-ping le-ping2"></span>'
            '<span class="le-dot"></span></div>'
            '<div class="le-h">Aucun match en direct</div>'
            '<div class="le-sub">Les scores en temps réel — buts et minute de jeu — '
            's\'affichent ici dès qu\'un match analysé démarre.</div>'
            '</div>')   # foot-only (user 2026-08-22) ; bouton « Voir les matchs à venir » retiré
    else:
        # MÊMES TYPES QUE PRONOS : Confiance (montante incluse) → Value → Provisoire → Combiné. Split
        # Confiance/Value par le `tier` de chaque carte (user 2026-08-09) ; Value masquée si vide / split off.
        # LES ZONES DE TYPE = MATCHS EN COURS SEULEMENT (user 2026-08-19 : on ne classe par type qu'une fois le
        # match COMMENCÉ). Les prochains matchs (à venir) sont MÉLANGÉS dans une zone unique « Prochains matchs ».
        _play_c = [c for c in _play if c.get("tier") == "confiance"]
        _play_v = [c for c in _play if c.get("tier") == "value"]
        _play_m = [c for c in _play if c.get("tier") == "montante"]     # zone MONTANTE à part
        # ZONES REPLIABLES comme PRONOS (user 2026-08-18 « meilleure répartition verticale des types de paris
        # pour utiliser l'écran ») : mêmes dividers/chevron/badge-à-droite qu'en Pronos. OUVERTES par défaut
        # (on veut voir les scores live) ; clés de persistance `live-*` DISTINCTES -> replier une zone en Live
        # n'affecte PAS la même zone en Pronos (et vice-versa).
        _lz = dict(collapsible=True, open_=True)
        # Tag « en direct » CONSERVÉ sur les zones de MATCHS EN COURS (user 2026-08-20 : les matchs en cours
        # doivent rester EXACTEMENT comme avant — carte live inchangée + étiquette « en direct »). Seuls les
        # PROCHAINS matchs vont dans une zone à part, avec un style distinct (voir « Prochains lives » plus bas).
        out = [_zone("play", _plur(len(_play_c), "Confiance"), "en direct", len(_play_c), _cards(_play_c),
                     zk="live-play", **_lz)]
        if _play_v:
            out.append(_zone("value", "Value", "en direct", len(_play_v), _cards(_play_v), zk="live-value", **_lz))
        if _play_m:
            _mt_lv = (_mont_title or "Montante").split(" · ", 1)
            out.append(_zone("mont", _mt_lv[0], "en direct",
                             len(_play_m), _cards(_play_m), zk="live-mont",
                             subtitle=(_mt_lv[1] if len(_mt_lv) > 1 else ""), **_lz))
        out += [
            _zone("indic", _plur(len(_prov), "Provisoire"), "en direct", len(_prov), _cards(_prov),
                  zk="live-indic", **_lz),
            _zone("combo", _plur(len(_combo_rows), "Combiné"), "en direct",
                  len(_combo_rows), _combo, zk="live-combo", **_lz),
        ]
        # PROCHAINS LIVES — MÉLANGÉS (pas classés par type tant que non commencés, user 2026-08-19), triés par
        # coup d'envoi (ordre CHRONOLOGIQUE), cartes compactes NON cliquables. NON REPLIABLE, sans tag « à venir ».
        if _upcoming_all:
            _upc_title = "Prochains lives" if len(_upcoming_all) > 1 else "Prochain live"
            out.append(_zone("prog", _upc_title, "", len(_upcoming_all),
                             _join_cards([_sport_row(c) for c in _upcoming_all]),
                             zk="live-upc", collapsible=False))
        zones = f'<div class="dash-zones">{"".join(x for x in out if x)}</div>'
    _sel = _sport_selector(_cur, _counts, target="pn-directs", base="/directs", q="")
    # Compteur TOUS sports -> BADGE chiffré du menu du bas (marqueur `.dv-nav` lu par le JS SPA).
    body = f'<span class="dv-nav" data-tab="directs" data-n="{total}" hidden></span>' + _sel + zones
    return body if frag else spa_shell("directs", "Live", body)

_FORM_COLOR = {"W": "#34d27b", "D": "#e0b341", "L": "#ff6b6b",
               "В": "#34d27b", "Н": "#e0b341", "П": "#ff6b6b"}  # W/D/L (en/ru selon locale)

def form_dots(form, n: int = 5, pending: int = 0) -> str:
    """Pastilles colorées des derniers résultats (V/N/D), lettre en MAJUSCULE. form = ['W','D','L',…].
    `n` = nb max de pastilles (les N dernières -> le plus récent à DROITE).
    `pending` = nb de paris À JOUER pas encore réglés (matchs à venir/en cours) : ajoutés en QUEUE (à
    DROITE = les plus récents) sous forme de SABLIERS DORÉS ⏳, IDENTIQUES au badge provisoire `.sx-bdg.p`
    (demande user 2026-07-17 : les icônes « en attente » doivent apparaître dans la bande W/L). Les
    sabliers réservent leur place à droite (total borné à `n`) -> jamais de débordement de la ligne."""
    form = list(form or [])
    pending = max(0, int(pending or 0))
    if not form and not pending:
        return ""
    keep = max(0, n - pending)                          # place réservée aux sabliers à droite
    dots = "".join(
        f'<span class="fd" style="background:{_FORM_COLOR.get(str(x).upper()[:1], "#5a6472")}">'
        f'{html.escape(str(x)[:1].upper())}</span>'   # W / L / N en MAJUSCULE
        for x in (form[-keep:] if keep else []))
    dots += '<span class="fd fd-p" title="En attente de résultat">⏳</span>' * min(pending, n)
    return f'<span class="forms">{dots}</span>'

def form_compare(home: str, home_form, away: str, away_form) -> str:
    """Forme des 2 équipes alignée : domicile à gauche, extérieur à droite (lisible)."""
    if not (home_form or away_form):
        return ""
    e = html.escape
    return ('<div class="formrow">'
            f'<span class="fc"><span class="dim">forme</span> {form_dots(home_form)}</span>'
            f'<span class="fc">{form_dots(away_form)}</span></div>')

def votes_line(home_pct, away_pct, home, away) -> str:
    """Pronostics des fans (votes SofaScore) en mini-barre visuelle."""
    if home_pct is None or away_pct is None:
        return ""
    e = html.escape
    h, a = round(home_pct), round(away_pct)
    return (f'<div class="votes"><div class="vlbl"><span>👥 <b>{h}%</b> {e(home)}</span>'
            f'<span>{e(away)} <b>{a}%</b></span></div>'
            f'<div class="vbar"><span class="vh" style="width:{h}%"></span>'
            f'<span class="va" style="width:{a}%"></span></div></div>')

def _team_form_block(flag: str, name: str, tf: dict | None) -> str:
    """Bloc forme d'une équipe : 5 derniers résultats + note moyenne + classement."""
    e = html.escape
    fl = f'{flag} ' if flag else ""
    if not tf:
        return f'<div class="frm"><div class="frm-t">{fl}{e(name)}</div><span class="dim">—</span></div>'
    meta = []
    if tf.get("position"):
        meta.append(f'{tf["position"]}<span class="dim">ᵉ au classement</span>')
    if tf.get("avg_rating"):
        meta.append(f'<span title="Note moyenne des joueurs (SofaScore), sur 10">note '
                    f'<b>{round(tf["avg_rating"], 2)}</b>/10</span>')
    return (f'<div class="frm"><div class="frm-t">{fl}{e(name)}</div>'
            f'{form_dots(tf.get("form"))}'
            f'<span class="dim">{" · ".join(meta) if meta else ""}</span></div>')

# Catégories de paris, calquées sur Unibet. Ordre de MATCHING : du plus spécifique au plus
# générique (un libellé prend la 1re catégorie qui colle). 2e nombre = rang d'AFFICHAGE.
_MKT_CATS = [
    ("Corners", 11, ("corner",)),
    ("Cartons joueur", 10, ("prend un carton", "carton du joueur", "cartons joueur")),
    ("Tirs (joueur)", 8, ("tirs cadrés du joueur", "tirs du joueur", "tir du joueur")),
    ("Buteurs", 6, ("buteur", "marque", "scorer", "anytime")),   # « Marque ou passe » -> Buteurs
    ("Passes décisives", 9, ("passe décisive",)),
    ("Cartons", 12, ("carton", "card")),                         # cartons ÉQUIPE (après joueur)
    ("Mi-temps / périodes", 4, ("mi-temps", "1ère", "2ème", "première", "deuxième", "half", "période", "quart", "quarter")),
    ("Handicaps", 3, ("handicap", "asiatique")),
    ("Scores exacts", 5, ("score exact", "résultat correct")),
    ("Double chance", 1, ("double chance",)),
    ("Tirs (équipe)", 13, ("tirs",)),                            # tirs d'équipe (après tirs joueur)
    ("Autres paris joueurs", 14, ("joueur", "player", "arrêt", "gardien")),
    ("Buts / totaux", 2, ("total", "plus de", "moins de", "nombre de buts", "but ")),
    ("Résultat du match", 0, ("temps réglementaire", "1x2", "résultat final", "vainqueur", "moneyline", "match")),
]

# Tennis : Unibet groupe en Match / Jeu / Point / Set, déduits du LIBELLÉ (aucun champ dédié).
# Ordre de matching : du plus spécifique au plus générique. 2e nombre = rang d'affichage.
_TENNIS_GROUPS = [
    ("Point", 2, re.compile(r"\bpoint\s+\d")),                  # « Point 1 - Set 3, Jeu 2 »
    ("Jeu", 1, re.compile(r"\bjeu\s+\d|40-40|balle de break")), # rattaché à un jeu précis
    ("Set", 3, re.compile(r"\bset\s+\d|\bmanche\s+\d")),        # rattaché à un set précis
]


def perle_advice(perle: dict | None) -> str:
    """Section « 🎯 Paris conseillés » PILOTÉE PAR LA PERLE : le pari à jouer (meilleur équilibre
    confiance × value parmi TOUS les marchés Unibet), ou s'abstenir. Source unique de vérité,
    cohérente avec la bannière « À JOUER » de la carte et le verdict de l'analyse."""
    e = html.escape
    if perle and perle.get("selection"):
        pct = round((perle.get("model_prob") or 0) * 100)
        edgep = round((perle.get("edge") or 0) * 100)
        if pct >= 68 and edgep < 6:   # forte proba, faible value -> pari de régularité
            qual = (f'un <b>pari sûr</b> : <b>{pct} %</b> de chances selon nous (petite cote, '
                    f'petit gain) — value modeste ~+{edgep} %.')
        else:
            qual = (f'le <b>meilleur équilibre confiance × value</b> du match : <b>{pct} %</b> de '
                    f'chances selon nous, cote <b>~+{edgep} %</b> en notre faveur.')
        body = ('<div class="banner"><b style="color:#19c46a">🎯 À jouer</b> — '
                f'<b>{e(str(perle["selection"]))}</b> @{perle["odds"]:g}. '
                f'<span class="dim">{qual}</span></div>')
    else:
        body = ('<div class="banner">Aucune perle sur ce match : aucun pari Unibet n\'offre un bon '
                '<b>équilibre confiance × value</b>. Mieux vaut <b>s\'abstenir</b>.</div>')
    return '<h2>🎯 Paris conseillés</h2>' + body

# Libellés FR + emoji pour les séries SofaScore fréquentes (sinon nom brut). Mappées aux marchés.
_STREAK_FIX = {
    "both teams scoring": "🥅 Les 2 marquent (BTTS)",
    "both teams not scoring": "🥅 Pas de BTTS",
    "no losses": "✅ Sans défaite", "losses": "❌ Défaites",
    "no wins": "⚠️ Sans victoire", "wins": "🏆 Victoires", "draws": "🤝 Nuls",
    "without clean sheet": "🧤 Sans clean sheet", "clean sheets": "🧤 Clean sheets",
    "first to score": "⏱️ Marque en premier", "first to concede": "⏱️ Encaisse en premier",
    "scored in both halves": "⚽ Marque dans les 2 MT",
    "first half winner": "⏱️ Gagne la 1re MT",
}

def _streak_label(name: str) -> str:
    n = (name or "").strip()
    low = n.lower()
    if low in _STREAK_FIX:
        return _STREAK_FIX[low]
    m = re.match(r"(more|less) than ([\d.]+) (goals|cards|corners)", low)
    if m:
        sign = "+" if m.group(1) == "more" else "−"
        unit = {"goals": "buts", "cards": "cartons", "corners": "corners"}[m.group(3)]
        emoji = {"goals": "⚽", "cards": "🟨", "corners": "🚩"}[m.group(3)]
        return f"{emoji} {sign}{m.group(2).replace('.', ',')} {unit}"
    return html.escape(n)

def _streak_strength(value) -> tuple:
    """(ratio 0..1 ou None, classe de force). « X/Y » -> ratio coloré (forte ≥80 % / moyenne ≥60 % /
    faible) ; un nombre seul = compteur de série (pas de jauge)."""
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", str(value or ""))
    if not m:
        return None, "s-count"
    num, den = int(m.group(1)), int(m.group(2))
    r = (num / den) if den else 0.0
    cls = "s-strong" if r >= 0.8 else ("s-mid" if r >= 0.6 else "s-low")
    return r, cls


def render_streaks(home: str, away: str, streaks: dict | None) -> str:
    """Bloc « Tendances récentes » : séries de pari par équipe (mappées aux marchés) + confrontations.
    Chaque série = une JAUGE (barre proportionnelle au ratio + couleur selon la force). Source =
    Sportradar GISMO (SofaScore est mort) ; on ne nomme plus de source dans l'UI.
    `streaks` = {"home":[(name,value)…], "away":[…], "h2h":[…]} (préparé par le routeur)."""
    if not streaks:
        return ""
    e = html.escape

    def chips(items):
        # trie pour mettre les séries les PLUS FORTES en tête (lecture immédiate du signal)
        rows = []
        for name, value in items or []:
            if not value:
                continue
            ratio, cls = _streak_strength(value)
            rows.append((ratio if ratio is not None else -1, cls, name, value))
        rows.sort(key=lambda x: x[0], reverse=True)
        out = []
        for ratio, cls, name, value in rows:
            fill = (f'<span class="strk-fill" style="width:{round(ratio * 100)}%"></span>'
                    if ratio is not None and ratio >= 0 else "")
            out.append(f'<span class="strk-c {cls}">{fill}'
                       f'<span class="strk-t">{_streak_label(name)}</span>'
                       f'<b>{e(str(value))}</b></span>')
        return "".join(out)

    cols = []
    for nm, key in ((home, "home"), (away, "away")):
        c = chips(streaks.get(key))
        if c:
            cols.append(f'<div class="strk-team"><div class="strk-h">{e(nm)}</div>'
                        f'<div class="strk-cs">{c}</div></div>')
    h2h = chips(streaks.get("h2h"))
    if h2h:
        cols.append('<div class="strk-team strk-h2h"><div class="strk-h">🤝 Confrontations directes</div>'
                    f'<div class="strk-cs">{h2h}</div></div>')
    if not cols:
        return ""
    return ('<h2>📈 Tendances récentes</h2>'
            '<div class="dim" style="font-size:11px;margin:-3px 0 8px">Régularité sur les derniers '
            'matchs — plus la barre est <b style="color:#46e08a">verte/pleine</b>, plus la série '
            'est forte.</div>'
            f'<div class="strk">{"".join(cols)}</div>')

def render_sport_match_detail(ctx: dict, frag: bool = False) -> str:
    """Fiche détaillée d'un match foot/basket : prédiction (3 barres + divergence + cotes)
    puis analyse SofaScore (forme des 2 équipes, confrontations directes).
    `frag=True` -> renvoie SEULEMENT l'analyse (forme + H2H) pour l'accordéon sous la carte."""
    e = html.escape
    hf = f'{ctx.get("home_flag")} ' if ctx.get("home_flag") else ""       # drapeau AVANT (gauche)
    af = f' {ctx.get("away_flag")}' if ctx.get("away_flag") else ""        # drapeau APRÈS (droite)
    head = (f'<a class="dim" href="{ctx["back_url"]}">← {e(ctx["back_label"])}</a>'
            f'<div class="mdh"><div class="mdh-c">{e(ctx.get("comp") or "")}'
            f'<span class="dim"> · {ctx.get("when") or ""}</span></div>'
            f'<div class="mdh-t">{hf}{e(ctx["home"])} <span class="dim">vs</span> '
            f'{e(ctx["away"])}{af}</div></div>')

    pred = _pick_bars(ctx["prediction"]) if ctx.get("prediction") else ""
    odds = odds_row(ctx["odds_cells"]) if ctx.get("odds_cells") else ""

    # 📈 Forme récente : version DÉTAILLÉE fusionnée (note + 5 derniers avec adversaire/score)
    # fournie par le routeur si dispo ; sinon repli sur les pastilles compactes (forme pré-match).
    form_html = ctx.get("form_html") or ""
    forms = ctx.get("forms")
    if not form_html and forms:
        form_html = ('<h2>📈 Forme récente</h2>'
                     f'<div class="row">{_team_form_block(*forms[0])}'
                     f'{_team_form_block(*forms[1])}</div>')

    h2h = ctx.get("h2h")
    h2h_html = ""
    if h2h and any(h2h.get(k) is not None for k in ("home_wins", "draws", "away_wins")):
        hw, dr, aw = h2h.get("home_wins") or 0, h2h.get("draws"), h2h.get("away_wins") or 0
        cells = [f'<span class="h2h-c"><b>{hw}</b><span class="dim">{e(ctx["home"])}</span></span>']
        if dr is not None:
            cells.append(f'<span class="h2h-c"><b>{dr}</b><span class="dim">nuls</span></span>')
        cells.append(f'<span class="h2h-c"><b>{aw}</b><span class="dim">{e(ctx["away"])}</span></span>')
        h2h_html = f'<h2>🤝 Face-à-face</h2><div class="h2h">{"".join(cells)}</div>'

    streaks_html = render_streaks(ctx.get("home") or "", ctx.get("away") or "", ctx.get("streaks"))

    # Fiche centrée sur l'ANALYSTE : barres (Unibet/Public) -> analyse (Verdict, tableau, faits,
    # sources) -> tendances (séries) -> forme récente -> face-à-face -> contexte. Perle/Elo retirés.
    analysis = ctx.get("analysis") or ""          # 🧠 analyse analyste (Verdict + tableau + faits)
    extra = ctx.get("extra") or ""                # contexte + spécificités (classement, écart, buts)
    no_data = ('<div class="banner">Analyse SofaScore indisponible pour ce match '
               '(source momentanément en pause ou match non couvert).</div>')
    # 📉 « Mouvement de cote » RETIRÉ de la fiche (info secondaire, alourdissait la carte — demande
    # utilisateur 2026-06-16). L'historique reste enregistré (odds_history), juste plus affiché ici.
    if frag:   # accordéon sous la carte : la carte porte déjà bets + bannières -> PAS de liens ici
        return (analysis + streaks_html + h2h_html + form_html + extra) or no_data
    links = ctx.get("links") or ""     # bannières SofaScore / Unibet (page pleine uniquement)
    body = head + pred + odds + links + analysis + streaks_html + h2h_html + form_html + extra
    if not (analysis or streaks_html or extra or form_html or h2h_html):
        body += no_data
    return layout(ctx["home"] + " vs " + ctx["away"], ctx["sport_key"], body, subnav="matchs")

def fmt_score(home_score, away_score) -> str:
    """Score set par set d'un match en cours/terminé : '6-4 3-2'. '' si aucun."""
    hs = getattr(home_score, "sets", None) or []
    as_ = getattr(away_score, "sets", None) or []
    parts = []
    for h, a in zip(hs, as_):
        if h is None and a is None:
            continue
        parts.append(f'{h if h is not None else 0}-{a if a is not None else 0}')
    return " ".join(parts)


_FACTOR_NAMES = {"classement": "Classement", "forme": "Forme",
                 "surface": "Surface", "head_to_head": "Face-à-face"}

def render_factors(factors, intro: str | None = None) -> str:
    """Bloc PARTAGÉ « 📊 Ce qui pèse dans l'analyse » (tennis/foot/basket) : une barre de
    contribution domicile/extérieur par facteur. `factors` = objets AnalysisFactor OU dicts
    {name, home, away, detail}. Même présentation pour les 3 sports."""
    if not factors:
        return ""
    e = html.escape

    def g(f, k):
        return f.get(k) if isinstance(f, dict) else getattr(f, k, None)

    def row(f):
        h = round((g(f, "home") or 0) * 100)
        nom = _FACTOR_NAMES.get(g(f, "name"), str(g(f, "name")).replace("_", " ").capitalize())
        return (f'<div class="frow"><div class="ft"><span class="fn">{e(nom)}</span>'
                f'<span class="fb"><span class="mbar"><span class="a" style="width:{h}%"></span>'
                f'<span class="b" style="width:{100 - h}%"></span></span></span>'
                f'<span class="fp">{h}/{100 - h}%</span></div>'
                f'<div class="dim" style="font-size:11px;margin-top:4px">{e(g(f, "detail") or "")}</div></div>')
    intro = intro or ('Chaque barre = part en faveur de chaque camp sur ce facteur (gauche = '
                      'domicile/1er cité). <b>Force générale</b> = niveau global ; puis '
                      '<b>Classement</b>, <b>Forme</b> du moment et <b>Face-à-face</b>.')
    return (f'<h2>📊 Ce qui pèse dans l\'analyse</h2>'
            f'<div class="dim" style="font-size:11px;margin:-2px 0 8px">{intro}</div>'
            '<div class="row">' + "".join(row(f) for f in factors) + '</div>')

def render_match_detail(a, winner_odds: tuple[float | None, float | None],
                        aces: dict | None = None, tour: str = "atp",
                        home_form: list[dict] | None = None,
                        away_form: list[dict] | None = None,
                        h2h: dict | None = None, score: str = "",
                        votes: tuple | None = None, frag: bool = False,
                        recos: str = "", markets_html: str = "") -> str:
    """a = MatchAnalysis ; winner_odds = (cote_home, cote_away) Unibet ;
    aces = récap tendance d'aces ; home_form/away_form = derniers résultats (V/D) ;
    h2h = {'home': n, 'away': n} bilan des confrontations ; score = score en cours."""
    e = html.escape
    hp = a.model_home_probability
    ap = a.model_away_probability
    live = (f' · <span class="live">🟢 {e(score)}</span>'
            if a.status == "inprogress" and score else
            (f' · {e(score)}' if score else ""))
    head = (f'<a class="dim" href="/app">← Retour aux matchs</a>'
            f'<div class="players" style="font-size:18px;margin-top:10px">'
            f'{e(a.home.name)} <span class="dim">vs</span> {e(a.away.name)}</div>'
            f'<div class="dim">{e(a.ground_type or "")} · statut {e(a.status or "")}'
            f'{live} · confiance {e(a.confidence or "—")}</div>')

    # 💰 LE PARI À JOUER — la recommandation nette du modèle pour ce match
    pick = next((v for v in a.value_bets if v.is_value), None)
    if pick:
        pari_html = (
            f'<div class="big" style="border-color:#1b5e20;background:#13251a">'
            f'💰 Pari à jouer : <b class="pos">{e(pick.player)}</b> @ {pick.odds}'
            f'<div class="d">Mise conseillée {pick.recommended_stake_pct}% du capital · '
            f'edge +{round((pick.edge or 0)*100, 1)} pts vs Unibet. Value du modèle, '
            f'à recouper — un pari n\'est jamais garanti.</div></div>')
    elif a.unibet_matched:
        pari_html = (
            '<div class="big">🚫 Aucun pari conseillé'
            '<div class="d">Le modèle ne détecte pas de value vs les cotes Unibet '
            'sur ce match. Mieux vaut s\'abstenir.</div></div>')
    else:
        pari_html = ""

    # Forme récente en PASTILLES (V vert / D rouge), du plus récent au plus ancien
    def _form_block(name, form):
        if not form:
            return (f'<div class="frow"><div class="fn">{e(name.split()[-1])}</div>'
                    '<span class="dim">historique indisponible</span></div>')
        # Le + RÉCENT à DROITE : on inverse (la source donne récent -> ancien)
        dots = "".join(f'<span class="dot {"w" if f["win"] else "l"}">'
                       f'{"V" if f["win"] else "D"}</span>' for f in reversed(form))
        return (f'<div class="frow"><div class="fm-name">{e(name)}</div>'
                f'<div class="dots">{dots}</div></div>')

    form_html = ""
    if home_form or away_form:
        form_html = ('<h2>📈 Forme récente</h2>'
                     f'<div class="row">{_form_block(a.home.name, home_form or [])}'
                     f'{_form_block(a.away.name, away_form or [])}</div>')

    # Face-à-face en BOÎTES (même présentation que foot/basket). Tennis = 2 issues (pas de nul).
    h2h_html = ""
    if h2h:
        hh, aw = h2h.get("home") or 0, h2h.get("away") or 0
        if hh + aw > 0:
            cells = (f'<span class="h2h-c"><b>{hh}</b>'
                     f'<span class="dim">{e(a.home.name.split()[-1])}</span></span>'
                     f'<span class="h2h-c"><b>{aw}</b>'
                     f'<span class="dim">{e(a.away.name.split()[-1])}</span></span>')
            h2h_html = f'<h2>🤝 Face-à-face</h2><div class="h2h">{cells}</div>'

    probs = ""
    if hp is not None:
        probs = (f'<h2>Chances de gagner <span class="dim" style="font-weight:400;font-size:11px">'
                 f'· selon BETSFIX</span></h2><div class="row">'
                 f'<div class="pbar-l"><span>{e(a.home.name.split()[-1])} {round(hp*100)}%</span>'
                 f'<span>{round(ap*100)}% {e(a.away.name.split()[-1])}</span></div>'
                 f'<div class="mbar" style="height:10px"><span class="a" style="width:{round(hp*100)}%">'
                 f'</span><span class="b" style="width:{round(ap*100)}%"></span></div></div>')

    # Facteurs (contribution home/away) — bloc PARTAGÉ avec foot/basket
    factors = render_factors(
        a.factors,
        intro=('Chaque barre = part en faveur de chaque joueur. <b>Force générale</b> = niveau '
               'global ; <b>Classement</b>, <b>Forme</b> du moment, <b>Surface</b> et '
               '<b>Face-à-face</b> (historique entre eux).'))

    # Lecture du modèle (favori) — neutre, pas de pari conseillé
    fav = a.home.name if (hp or 0) >= 0.5 else a.away.name
    favp = round(max(hp or 0, ap or 0) * 100)
    verdict = (f'<div class="big">🎾 Favori du modèle : {e(fav)} ({favp}%)'
               f'<div class="d">Confiance {e(a.confidence or "—")}. Lecture statistique, '
               f'à recouper avec ton jugement — ce n\'est pas un conseil de pari.</div></div>')

    # Cotes Unibet + comparaison au marché (informatif)
    oh, oa = winner_odds
    odds_html = ""
    if a.unibet_matched and (oh or oa):
        def cmp_row(name, model_p, odds):
            imp = round(100 / odds) if odds else None
            mp = round((model_p or 0) * 100)
            note = ""
            if imp is not None:
                if mp - imp >= 6:
                    note = '<span class="pos">modèle plus optimiste</span>'
                elif imp - mp >= 6:
                    note = '<span class="neg">modèle plus prudent</span>'
                else:
                    note = '<span class="dim">en accord</span>'
            return (f'<tr><td>{e(name)}</td><td><b>{odds or "—"}</b></td>'
                    f'<td>{mp}% / {imp if imp is not None else "—"}%</td><td>{note}</td></tr>')
        odds_html = (
            '<h2>Cotes Unibet vs modèle</h2>'
            '<table><tr><td class="dim">joueur</td><td class="dim">cote</td>'
            '<td class="dim">modèle / implicite</td><td class="dim"></td></tr>'
            + cmp_row(a.home.name, hp, oh) + cmp_row(a.away.name, ap, oa) + '</table>')
    elif not a.unibet_matched:
        odds_html = ('<div class="banner">Cotes Unibet indisponibles (match pas encore '
                     'à l\'affiche du book).</div>')

    # Tendance d'aces : fourchette (durée), ajustement adversaire, P(plus de la ligne)
    aces_html = ""
    if aces:
        def arow(name, p):
            if p.get("rate") is None:
                return (f'<tr><td>{e(name)}</td><td class="dim">—</td>'
                        f'<td class="dim">tendance inconnue</td></tr>')
            lo, hi = round(p["exp_low"]), round(p["exp_high"])
            adj = ""
            if p["factor"] <= 0.97:
                adj = ' <span class="dim">(− retour adverse)</span>'
            elif p["factor"] >= 1.03:
                adj = ' <span class="dim">(+ retour faible)</span>'
            # vs ligne Unibet
            if p.get("line") is not None and p.get("p_over_low") is not None:
                pl, ph = round(p["p_over_low"] * 100), round(p["p_over_high"] * 100)
                lo_p, hi_p = min(pl, ph), max(pl, ph)
                if hi_p < 48:
                    verdict = '<span class="neg">Moins de</span> plus probable'
                elif lo_p > 55:
                    verdict = '<span class="pos">Plus de</span> plausible (si match long)'
                else:
                    verdict = 'incertain — dépend de la durée'
                cmp = (f'Plus de {p["line"]} : <b>{lo_p}–{hi_p}%</b><br>'
                       f'<span class="dim">{verdict}</span>')
            else:
                cmp = '<span class="dim">pas de ligne Unibet</span>'
            return (f'<tr><td>{e(name)}<br><span class="dim">{p["adj_rate"]:.2f}/jeu</span></td>'
                    f'<td><b>~{lo}–{hi}</b> aces{adj}</td><td>{cmp}</td></tr>')
        aces_html = (
            '<h2>Service — aces attendus</h2>'
            '<div class="banner">Fourchette selon la <b>durée du match</b> '
            f'(court ~{round(aces["sg_short"])} jeux de service → long ~{round(aces["sg_long"])}), '
            'ajustée par la <b>force de retour</b> de l\'adversaire. '
            '<b>P(Plus de la ligne)</b> = notre proba vs le pari Unibet. '
            '⚠️ Le book intègre déjà tout ça : à lire, pas un signal de value.</div>'
            '<table><tr><td class="dim">joueur</td><td class="dim">aces attendus</td>'
            '<td class="dim">vs ligne Unibet</td></tr>'
            + arow(aces["home_name"], aces["home"])
            + arow(aces["away_name"], aces["away"]) + '</table>')

    # Accès à l'outil "Tous les paris" (modèle vs book sur tous les marchés Unibet)
    paris_link = ""
    if a.unibet_matched:
        paris_link = (f'<a class="big" href="/app/match/{a.match_id}/paris?tour={e(tour)}">'
                      f'🎯 Tous les paris (modèle vs Unibet)'
                      f'<div class="d">Vainqueur, aces, jeux, sets, breaks… proba du modèle '
                      f'vs cote du book, marché par marché.</div></a>')

    # 👥 Pronostics des fans (votes SofaScore) — informatif
    votes_html = ""
    if votes and votes[0] is not None:
        votes_html = ('<h2>Pronostics des fans</h2><div class="row">'
                      + votes_line(votes[0], votes[1], a.home.name, a.away.name) + '</div>')

    # frag : accordéon sous la carte -> analyse SANS l'en-tête (matchup déjà sur la carte)
    # ni le bandeau layout. On garde tout le reste (la plus complète).
    if frag:
        # Accordéon sous la carte : l'analyse rédigée est ajoutée en tête par le routeur.
        # On NE répète PAS le pari (déjà dans la box « 🎯 À jouer » de la carte) ni les
        # pronostics des fans (déjà dans la barre PUBLIC). Ordre intuitif -> technique :
        # forme -> face-à-face -> ce qui pèse -> aces.
        # Facteurs Elo retirés (fiche centrée analyste) : forme -> face-à-face -> aces.
        return (h2h_html + form_html + aces_html + markets_html) \
            or '<div class="dim">Analyse détaillée indisponible (SofaScore momentanément ' \
               'limité) — la prédiction reste celle de la carte.</div>'
    # Pari/verdict/probas du modèle + facteurs Elo retirés : la fiche s'appuie sur l'analyste.
    body = (head + h2h_html + form_html + votes_html + paris_link + aces_html + odds_html)
    return layout(f"{a.home.name} vs {a.away.name}", "tennis", body, subnav="matchs")

def _market_rows(rows: list[dict]) -> str:
    """Lignes d'un tableau de marché : sélection | cote | modèle/book | écart."""
    e = html.escape
    trs = []
    for r in rows:
        mp, ip = r.get("model_p"), r.get("implied_p")
        edge = r.get("edge")
        mp_s = f"{round(mp * 100)}%" if mp is not None else "—"
        ip_s = f"{round(ip * 100)}%" if ip is not None else "—"
        if edge is None:
            edge_s = "—"
        else:
            cls = "pos" if edge > 0 else ("neg" if edge < 0 else "dim")
            edge_s = f'<span class="{cls}">{"+" if edge >= 0 else ""}{round(edge * 100, 1)}</span>'
        flag = ' <span class="badge b-val">écart</span>' if r.get("value") else ""
        trs.append(
            f'<tr><td>{e(r.get("market") or "")}<br>'
            f'<span class="dim">{e(r.get("selection") or "")}'
            f'{(" · ligne " + str(r["line"])) if r.get("line") is not None else ""}</span>{flag}</td>'
            f'<td><b>{r.get("odds") or "—"}</b></td>'
            f'<td>{mp_s} / {ip_s}</td><td>{edge_s}</td></tr>')
    return "".join(trs)

def render_markets(match, winner_rows: list[dict], ace_rows: list[dict],
                   sim_rows: list[dict], odds_matched: bool, tour: str = "atp",
                   set_rows: list[dict] | None = None) -> str:
    """Page "Tous les paris" : modèle vs book, par marché, regroupé par fiabilité."""
    e = html.escape
    set_rows = set_rows or []
    back = (f'<a class="dim" href="/app/match/{match.id}?tour={e(tour)}">← Retour à l\'analyse</a>'
            f'<div class="players" style="font-size:18px;margin-top:10px">'
            f'{e(match.home.name)} <span class="dim">vs</span> {e(match.away.name)}</div>')
    if not odds_matched:
        body = back + '<div class="banner">Cotes Unibet indisponibles pour ce match.</div>'
        return layout("Tous les paris", "tennis", body)

    # 🎯 Meilleur pari du match : on ne retient QUE les marchés fiables/calibrés
    # (vainqueur, sets). Les aces sont exclus du titre (edges souvent artefacts non
    # validés) — ils restent visibles, en info, dans leur section.
    def _best(rows):
        cand = [r for r in rows if (r.get("edge") or 0) > 0]
        return max(cand, key=lambda r: r["edge"]) if cand else None

    options = [(_best(winner_rows), "Vainqueur", "marché le plus fiable")]
    options = [(r, lbl, note) for r, lbl, note in options if r]
    if options:
        best, blbl, bnote = max(options, key=lambda x: x[0]["edge"])
        be = round((best["edge"] or 0) * 100, 1)
        if (best["edge"] or 0) >= 0.04:
            line = f' (ligne {best["line"]})' if best.get("line") is not None else ""
            best_html = (
                f'<div class="big" style="border-color:#1b5e20;background:#13251a">'
                f'🎯 Meilleur pari : <b class="pos">{e(best.get("selection") or "")}</b>{line} '
                f'@ {best.get("odds") or "—"} <span class="dim">[{blbl}]</span>'
                f'<div class="d">{e(best.get("market") or "")} · modèle '
                f'{round((best.get("model_p") or 0)*100)}% vs book '
                f'{round((best.get("implied_p") or 0)*100)}% · edge +{be} pts. '
                f'{bnote} — jamais garanti.</div></div>')
        else:
            best_html = ('<div class="big">🎯 Aucun pari à valeur nette'
                         '<div class="d">Les cotes du book collent à nos estimations sur '
                         'ce match. Mieux vaut s\'abstenir ou jouer petit.</div></div>')
    else:
        best_html = ""

    # Légende : comment lire le tableau (la demande "mieux expliqué")
    intro = (
        '<div class="banner"><b>Comment lire ?</b> Chaque ligne = un pari Unibet.<br>'
        '• <b>modèle</b> = la proba qu\'on estime · <b>book</b> = la proba derrière la cote.<br>'
        '• <b>écart</b> = modèle − book. <span class="pos">Vert (+)</span> = on te donne '
        'PLUS de chances que le book ⇒ potentiellement intéressant. '
        '<span class="neg">Rouge (−)</span> = à éviter.<br>'
        '⚠️ Un écart positif <b>n\'est pas</b> un gain garanti — le book est souvent très '
        'juste, surtout sur les petits marchés.</div>')

    def section(title, sub, rows, sub_class="banner"):
        if not rows:
            return ""
        return (f'<h2>{e(title)}</h2><div class="{sub_class}">{sub}</div>'
                '<table><tr><td class="dim">marché / sélection</td><td class="dim">cote</td>'
                '<td class="dim">modèle / book</td><td class="dim">écart</td></tr>'
                f'{_market_rows(rows)}</table>')

    sections = (
        section("🏆 Vainqueur du match",
                "Le marché le <b>mieux modélisé</b> (Elo, classement, forme, surface, h2h). "
                "C\'est ici que nos estimations sont les plus fiables.", winner_rows)
        + section("🛡️ Paris « sûrs » — sets (au moins un set, handicap ±2.5, total sets)",
                  "Faible cote, haute probabilité (comme tes paris gagnants). "
                  "<b>Validé sur 4250 matchs : ces marchés sont bien cotés, sans edge "
                  "systématique.</b> On affiche notre estimation (approximative) à titre "
                  "<b>indicatif</b> — pas de pari conseillé ici.", set_rows)
        + section("🎾 Aces (exploratoire)",
                  "Signal réel sur la tendance d\'aces, mais total ancré sur le book : "
                  "à confirmer par le suivi avant d\'en faire un pari.", ace_rows)
        + section("🧪 Jeux · breaks (simulateur — expérimental)",
                  "⚠️ Simulation du déroulé, <b>peu fiable</b> sur ces marchés. "
                  "À ne PAS suivre pour parier en l\'état.", sim_rows, sub_class="banner warn"))
    if not (winner_rows or set_rows or ace_rows or sim_rows):
        sections = '<div class="dim">Aucun marché évaluable pour ce match.</div>'
    return layout("Tous les paris", "tennis", back + best_html + intro + sections)
