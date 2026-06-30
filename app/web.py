"""Plateforme de visionnage (front-end HTML rendu côté serveur).

Pages mobiles cohérentes au-dessus de l'API : accueil, liste des matchs,
détail/analyse d'un match. Thème sombre, nav commune. Aucun JS requis.
"""

from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timezone

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
    return f"{minute}'" if minute is not None else ""

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
    # Dates conviviales : Aujourd'hui / Demain / jour abrégé, sinon jj/mm.
    today = (datetime.now(LOCAL_TZ).date() if LOCAL_TZ is not None else datetime.now().date())
    delta = (dt.date() - today).days
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
    --border:#2a2a31;--border2:#3b3b44;--text:#f4f5f7;--muted:#9a9aa6;--dim:#65656e;
    /* ACCENT principal — UN SEUL endroit à changer pour reskin (cf. candidats en bas) */
    --accent:#22b8ff;--accent2:#1496f0;--accent-ink:#001321;--glow:rgba(34,184,255,.28);
    --halo:rgba(34,184,255,.09);
    --gold:#f6c54a;--gold-bg:#231d09;--gold-bd:#4a3c0c;
    --red:#ff6b6b;--green:#a6e22e;--brand:var(--accent);
    --cardline:rgba(34,184,255,.30);--cardglow:0 0 24px rgba(34,184,255,.10);
    --radius:16px;--shadow:0 8px 26px rgba(0,0,0,.55);--shadow-sm:0 2px 8px rgba(0,0,0,.4);
  }
  /* Home & Live = accent principal (hérité de :root). Les sports gardent leur teinte d'identité
     (néon sur fond noir) : tennis lime-jaune · basket orange · foot vert. */
  body.sp-tennis{--accent:#d7e64a;--accent2:#aac72f;--accent-ink:#16180a;--glow:rgba(190,210,60,.30)}
  body.sp-basket{--accent:#ff9f43;--accent2:#f08000;--accent-ink:#1a0e00;--glow:rgba(240,128,0,.30)}
  body.sp-foot{--accent:#2ee27f;--accent2:#19c46a;--accent-ink:#04130a;--glow:rgba(46,226,127,.30)}
  *{box-sizing:border-box}
  /* Fond html = COULEUR DE LA NAV (#0b0d12) : la zone du home-indicator iPhone (PWA standalone), non
     couverte par body/nav, montrait sinon un TROU NOIR sous la barre du bas. Là elle se fond dedans. */
  html{-webkit-text-size-adjust:100%;overflow:hidden;overscroll-behavior:none;background:#0b0d12}
  /* Coquille NON-scrollante en COLONNE FLEX,
  hauteur = viewport DYNAMIQUE (100dvh) : le contenu
     scrolle DANS .wrap (flex:1) et la barre du bas est un enfant flex STATIQUE collé au bas. Sur iOS
     ça supprime le « saut » de la barre fixe quand la toolbar Safari apparaît/disparaît (dvh suit la
     toolbar -> la barre reste toujours au bas visible) et le pied de page redevient atteignable. */
  body{margin:0;color:var(--text);font-size:14.5px;line-height:1.45;width:100%;
       height:100vh;height:100dvh;display:flex;flex-direction:column;overflow:hidden;overscroll-behavior:none;
       font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
       -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
       -webkit-user-select:none;user-select:none;-webkit-touch-callout:none;
       -webkit-tap-highlight-color:transparent;touch-action:manipulation;
       /* Thème premium : halos bleus POSÉS DIRECTEMENT sur le fond du body (au-dessus de --bg).
          AVANT ils étaient sur un body::before en z-index:-1 ; mais depuis que html a son propre
          fond (#0b0d12, fix safe-area iOS), le fond du body ne se propage plus au canvas et le
          pseudo z-index:-1 passait DERRIÈRE le fond opaque -> halos masqués (page toute noire).
          Sur le body même, ils s'affichent toujours. Le body ne scrolle pas (.wrap scrolle) ->
          le dégradé reste fixe visuellement. */
       background:
         radial-gradient(1100px 640px at 50% -6%,var(--halo),transparent 60%),
         radial-gradient(820px 520px at 100% 104%,var(--halo),transparent 72%),
         var(--bg);}
  a{color:inherit;text-decoration:none;-webkit-tap-highlight-color:transparent}
  /* Zone de contenu = SEUL élément qui scrolle (flex:1). La barre du bas étant désormais un frère
     statique en dessous,
  plus besoin de réserver ~86px en bas : un petit espace suffit. */
  .wrap{flex:1 1 auto;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;width:100%;
        position:relative;
        max-width:720px;margin:0 auto;display:flex;flex-direction:column;
        padding:calc(8px + env(safe-area-inset-top)) 16px 22px}
  /* Logo unique centré tout en haut de chaque page + pastille de pause */
  .toplogo{display:block;text-align:center;margin:20px 0 12px}
  .toplogo img{height:auto;width:auto;max-width:72%;max-height:46px;filter:drop-shadow(0 5px 18px rgba(34,184,255,.40))}
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
  /* Barre d'onglets en bas (style app native). PLUS de position:fixed : c'est un enfant flex STATIQUE
     de <body> (flex:0 0 auto),
  donc toujours collé au bas du viewport DYNAMIQUE sans « sauter » sur
     iOS. Centrée à 720px ; fond OPAQUE ; padding bas = safe-area (encoche/home-bar). */
  .botnav{flex:0 0 auto;width:100%;max-width:720px;margin:0 auto;z-index:60;touch-action:none;
          display:flex;gap:4px;
          padding:7px 10px calc(7px + env(safe-area-inset-bottom));
          background:#0b0d12;border-top:1px solid var(--border)}
  .botnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;
            padding:6px 0 4px;border-radius:14px;color:var(--muted);font-size:10px;
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
       border-radius:50%;border:2px solid rgba(52,210,123,.6);animation:navradar 1.9s ease-out infinite}
  .nr-ring2{animation-delay:.95s}
  @keyframes navradar{0%{transform:scale(.4);opacity:.95}100%{transform:scale(1);opacity:0}}
  /* SPA : panneaux par onglet (tout chargé à l'ouverture,
  bascule sans rechargement) */
  .panel{display:none}
  .panel.on{display:block;animation:panein .22s cubic-bezier(.22,.85,.3,1)}
  @keyframes fadein{from{opacity:.4}to{opacity:1}}
  .ldg{color:var(--dim);text-align:center;padding:40px 0;font-size:13px}
  .ldg::before{content:"";display:block;width:22px;height:22px;margin:0 auto 12px;border-radius:50%;
    border:2px solid var(--border2);border-top-color:var(--accent2);animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  /* État VIDE premium de l'onglet Live (aucun match en cours) : orbe « radar » + CTA */
  /* EXACTEMENT le cadre d'une carte de match (.row.pick) : bordure cyan .60 + dégradé CYAN + glow cyan
     -> uniforme avec les onglets sport (demande user). Mêmes valeurs littérales que .row.pick. */
  .live-empty{position:relative;overflow:hidden;text-align:center;margin:18px 0;padding:48px 22px 42px;
       border:1px solid rgba(34,184,255,.60);border-radius:var(--radius);display:flex;flex-direction:column;
       align-items:center;box-shadow:0 0 26px rgba(34,184,255,.20);
       background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02))}
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
  .brand .hdot{font-size:10px;font-weight:800;color:var(--gold);white-space:nowrap;letter-spacing:.02em}
  .brand .tag{font-size:10px;font-weight:700;letter-spacing:.12em;
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
       background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02))}
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
  .lbl{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;font-weight:700}
  .val{font-size:25px;font-weight:800;margin:5px 0;letter-spacing:-.02em;
       font-variant-numeric:tabular-nums}
  .sub{font-size:11px;color:var(--muted)}
  /* Rows / list cards */
  /* En-tête de jour dans les listes (regroupement par date) */
  .dayhdr{display:flex;align-items:center;gap:9px;margin:16px 2px 4px;font-size:10.5px;
          font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
  .dayhdr::after{content:"";flex:1;height:1px;background:var(--border)}
  .row{display:block;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border-radius:var(--radius);padding:12px 14px;margin:15px 0;border:1px solid var(--cardline);
       box-shadow:var(--cardglow),var(--shadow-sm);transition:.16s}
  .row:active{transform:scale(.99);border-color:var(--border2)}
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
  .row.pick{border-color:rgba(34,184,255,.60);
            background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02));
            box-shadow:0 0 26px rgba(34,184,255,.20)}
  /* CARTE COMPACTE : en-tête toujours visible (statut + équipes + résumé) + corps replié au tap.
     Liste dense -> peu de scroll ; on déplie un match pour voir paris/barres/liens/analyse. */
  .row.mc{padding:0;margin:9px 0;overflow:hidden}
  /* mc-head : colonne d'infos pleine largeur + chevron en ABSOLU (centré vertical) -> l'heure peut
     aller dans le COIN haut-droit sans être décalée par la flèche. */
  .mc-head{position:relative;padding:11px 14px;cursor:pointer;-webkit-tap-highlight-color:transparent}
  .mc-line{display:flex;align-items:center;gap:7px}
  .mc-ic{flex:none;font-size:13px;line-height:1}                 /* emoji sport DISCRET (plus petit) */
  /* L1 : nom du sport · circuit (ATP/WTA) · tournoi (ville capitalisée) — contextuel,
  discret. */
  .mc-comp{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
       font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.02em}
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
  /* L2 : équipes (noms + prénoms complets) — ligne principale. */
  .mc-teams{font-size:13.5px;font-weight:800;color:var(--text);margin-top:4px;
       white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mc-teams .dim{color:var(--dim);font-weight:600}
  /* L3 : LISTE des paris (intitulés,
  1/ligne) — masquée une fois DÉPLIÉE (les paris détaillés s'affichent).
     padding-right pour libérer le chevron en bas à droite. */
  .mc-sub{margin-top:6px;padding-right:20px}
  .mc-open .mc-sub{display:none}
  /* Ligne de pari de la carte : le libellé passe à la LIGNE s'il est trop long (plus de troncature) */
  .mc-betl{display:flex;align-items:baseline;gap:6px;font-size:11px;font-weight:600;color:#cfe0f5}
  .mc-betl + .mc-betl{margin-top:3px}
  .mc-bi{flex:none;font-size:10px}
  .mc-bt{min-width:0;flex:1;overflow-wrap:anywhere;line-height:1.3}
  .mc-bc{flex:none;align-self:center;background:rgba(25,196,106,.14);color:#7ff0b6;border-radius:6px;
       padding:1px 7px;font-size:10.5px;font-weight:900;font-variant-numeric:tabular-nums;white-space:nowrap}
  /* pari RETENU (⭐ en tête) : libellé mis en avant */
  .mc-betl-reco .mc-bt{color:#fff;font-weight:800}
  .mc-noplay .mc-bt,.mc-noplay .mc-bi{color:var(--muted);font-weight:600;font-style:italic;opacity:.85}
  .mc-body{padding:2px 14px 13px}
  .mc-body[hidden]{display:none}
  /* Moins d'espace entre les équipes et le bloc « BOOKMAKERS » une fois déplié. */
  .mc-open .mc-head{padding-bottom:5px}
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
  .lboard{background:rgba(255,255,255,.05);border:1px solid var(--cardline);border-radius:10px;
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
  /* Tennis : MÊME style que le box-score basket (taille,
  gap,
  baseline,
  colonne résultat) */
  .lboard-t{position:relative}
  .lboard-t .lb-s{gap:6px;align-items:baseline}
  .lboard-t .lb-c{width:18px;min-width:18px;font-size:12px}
  .lboard-t .lb-n{font-size:12.5px}
  .lboard-t .lb-hdr .lb-c{font-size:10px}
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
  .lboard-q .lb-hdr .lb-c{font-size:10px}       /* Q1..Qn + TOT : en-tête discret (plus petit) */
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
  .lb-srv{font-size:10px;vertical-align:middle;margin-left:1px}
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
  /* Match commencé : on garde le type (vert/bleu) mais sans halo « action » + mention discrète */
  .perle-pre{box-shadow:none;opacity:.9}
  .pl-pre{font-size:9.5px;font-weight:700;font-style:italic;color:var(--muted);white-space:nowrap}
  /* 🟢 Pari déjà GAGNÉ en live : halo vert prononcé + badge ✓ (prime sur conf/value) */
  .perle-won{border-color:rgba(25,196,106,.9)!important;
             box-shadow:0 0 18px rgba(25,196,106,.5)!important;
             background:linear-gradient(90deg,rgba(25,196,106,.2),rgba(25,196,106,.07))!important}
  .pl-won{font-size:10px;font-weight:800;color:#19c46a;background:rgba(25,196,106,.18);
          padding:2px 6px;border-radius:6px;white-space:nowrap}
  /* 🔴 Pari déjà RATÉ en live : halo rouge prononcé + badge ✗ */
  .perle-lost{border-color:rgba(244,73,73,.9)!important;
              box-shadow:0 0 18px rgba(244,73,73,.45)!important;
              background:linear-gradient(90deg,rgba(244,73,73,.18),rgba(244,73,73,.06))!important}
  .perle-lost .pl-o{color:#ff8a8a!important}
  .pl-lost{font-size:10px;font-weight:800;color:#ff6b6b;background:rgba(244,73,73,.18);
          padding:2px 6px;border-radius:6px;white-space:nowrap}
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
  /* Lien Compte (en-tête) */
  .acct{position:absolute;top:10px;right:12px;z-index:5;display:inline-flex;align-items:center;gap:5px;
    text-decoration:none;font-size:11px;font-weight:800;color:#9fb6cf;background:rgba(255,255,255,.05);
    border:1px solid rgba(255,255,255,.12);border-radius:20px;padding:5px 11px}
  .acct:active{background:rgba(34,184,255,.14)}
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
        font-size:10px;padding:3px 8px}
  .cd.wait{background:rgba(246,197,74,.12);color:var(--gold);border-color:rgba(246,197,74,.32);
        font-size:10px;padding:3px 8px}
  .formrow{display:flex;justify-content:space-between;align-items:center;margin-top:7px}
  .fc{display:inline-flex;align-items:center;gap:5px;font-size:11px}
  .forms{display:inline-flex;gap:3px;vertical-align:middle;margin-left:4px}
  .fd{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
      border-radius:4px;font-size:9px;font-weight:800;color:#08110a;line-height:1;
      text-transform:uppercase;text-align:center;padding-top:1px}
  .pbars{margin-top:7px;display:flex;flex-direction:column;gap:5px}
  .pb-h{font-size:12px;color:var(--text);margin-bottom:2px}
  /* TABLEAU « Chances de gagner » : sources en LIGNES,
  issues en COLONNES + fine barre/ligne */
  /* Barres PLEINES : source au-dessus,
  % dans chaque segment (favori = couleur source) */
  /* ===== Bloc « Cotes & chances » PREMIUM : barre fine de proportion + chips par issue ===== */
  .ocs{margin:10px 0 2px;display:flex;flex-direction:column;gap:11px}
  .oc-h{font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;
        color:var(--muted);margin-bottom:6px}
  .oc{width:100%}
  .ocb{display:flex;width:100%;gap:2px;height:6px;border-radius:99px;overflow:hidden;margin-bottom:8px}
  .ocb-s{height:100%;border-radius:99px}
  .ocb-po{background:linear-gradient(90deg,#19c46a,#34d27b)}
  .ocb-pc{background:linear-gradient(90deg,#d8a93a,#e8c34d)}
  .ocb-dim{background:rgba(255,255,255,.13)}
  .ocp-row{display:flex;width:100%;gap:6px}
  .ocp{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:1px;
        padding:8px 5px 7px;border-radius:12px;text-align:center;
        background:rgba(255,255,255,.035);border:1px solid var(--border)}
  .ocp-n{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
        font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.01em}
  .ocp-v{font-size:16px;font-weight:900;color:var(--text);line-height:1.05;
        font-variant-numeric:tabular-nums}
  .ocp-c{font-size:10px;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums}
  /* chip FAVORI : liseré + fond teintés de la source, valeur en couleur */
  .ocp-fav.ocb-po{border-color:rgba(52,210,123,.5);
        background:linear-gradient(180deg,rgba(52,210,123,.16),rgba(52,210,123,.04))}
  .ocp-fav.ocb-po .ocp-v{color:#5be08c} .ocp-fav.ocb-po .ocp-n{color:#cdeecf}
  .ocp-fav.ocb-pc{border-color:rgba(232,195,77,.5);
        background:linear-gradient(180deg,rgba(232,195,77,.16),rgba(232,195,77,.04))}
  .ocp-fav.ocb-pc .ocp-v{color:#f0cf63} .ocp-fav.ocb-pc .ocp-n{color:#efe2b4}
  /* barre Public compacte : libellés sous la barre fine */
  .oc-pub{font-size:10.5px;color:var(--muted);font-weight:600}
  .oc-pub b{color:#cfe0f5;font-weight:800}
  /* Barre « Bookmakers » : 1 segment par issue (cote seule),
  parts ÉGALES. Les 3 ont le
     MÊME fond que le segment le plus faible (non-favori) des autres barres -> navy .pba. */
  .sb-bar.ocbar .seg{flex:1 1 0;min-width:0;gap:5px;padding:0 7px}
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
  .ocn{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;
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
  .pbar-l{display:flex;justify-content:space-between;font-size:10px;color:var(--dim);
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
  .an-note{font-size:10px;color:var(--muted);margin-top:9px;border-top:1px solid var(--border);
          padding-top:7px}
  /* « Preuve » — tableau unique (1 ligne/sport,
  colonnes alignées) façon tableau de bord */
  .ptab{border:1px solid var(--cardline);border-radius:14px;overflow:hidden;margin:8px 0;
          background:linear-gradient(180deg,var(--surface2),var(--surface));
          box-shadow:var(--cardglow)}
  .ptab-h,
  .ptab-row{display:grid;grid-template-columns:1fr 1.4fr .8fr .8fr;gap:5px;
          align-items:center;padding:11px 12px}
  .ptab-h{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.01em;
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
  .ptab-sub{display:block;font-size:10px;font-weight:600;color:var(--muted)}
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
  .ptab-pct{display:block;font-size:10px;font-weight:700;color:var(--muted);margin-top:1px}
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
  .spc-sample{font-size:10px;color:var(--muted);font-weight:600;margin:1px 0 7px;
       display:flex;justify-content:space-between;align-items:center;gap:8px}
  /* Badge de tendance récente (7 j) par sport */
  .spc-trend{font-size:10px;font-weight:800;white-space:nowrap}
  .spc-trend.up{color:var(--green)} .spc-trend.down{color:var(--red)} .spc-trend.flat{color:var(--muted)}
  .spc-trend-l{font-weight:600;color:var(--muted);opacity:.8}
  .spc-foot{font-size:10px;color:var(--muted);text-align:center;margin-top:4px;line-height:1.5}
  .spc-foot b.pos{color:var(--green)} .spc-foot b.neg{color:var(--red)}
  .spc-tot{font-weight:800}   /* P&L Total mis en avant */
  /* Analyse « analyste » (markdown rendu) en fiche match */
  .da{font-size:13px;line-height:1.55;color:#e8eaed}
  .da-h{font-weight:800;color:#e8eaed;margin:13px 0 5px}
  .da-h1{font-size:15px} .da-h2{font-size:13.5px} .da-h3{font-size:12.5px;color:#cfe0f5}
  .da-p{margin:6px 0}
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
  .arec-na{color:var(--muted)}   /* ROI peu fiable (échantillon trop faible) -> grisé */
  /* Graphiques performance PAR PARI (SVG,
  courbes de profit cumulé) */
  .bcharts{margin:2px 0 14px;display:flex;flex-direction:column;gap:10px}
  .bcharts-h{font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;
       color:#cfe0f5;display:flex;align-items:baseline;justify-content:space-between;gap:8px}
  .bcharts-sub{font-size:10px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0}
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
  .bc-mile-g{cursor:pointer}
  .bc-mile-g .bc-mile-c{transition:r .12s}
  .bc-mile-g.on .bc-mile-c{fill:#46e08a;stroke:#bdf6d4}
  .bc-mile-g.on .bc-mile{stroke:rgba(70,224,138,.7)}
  .bc-mile-c{fill:#1496f0;stroke:#bfe2ff;stroke-width:.8}
  .bc-mile-n{fill:#fff;font-size:7px;font-weight:900;pointer-events:none}
  /* Repères ALLÉGÉS : pastilles cliquables + panneau d'info au clic (page plus légère) */
  .sx-miles{margin-top:10px}
  .sx-ml-h{font-size:9px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
       opacity:.85;display:flex;align-items:baseline;gap:8px}
  .sx-ml-hint{font-size:9px;font-weight:600;letter-spacing:0;text-transform:none;opacity:.7}
  .sx-mile-bs{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
  .sx-mile-b{width:24px;height:24px;border-radius:50%;border:1px solid rgba(34,184,255,.4);
       background:rgba(34,184,255,.10);color:#9fd2ff;font-family:inherit;font-size:11px;font-weight:900;
       cursor:pointer;display:inline-flex;align-items:center;justify-content:center;padding:0}
  .sx-mile-b.on{background:#46e08a;border-color:#46e08a;color:#04220f}
  .sx-mile-info{font-size:11px;line-height:1.4;color:var(--muted);margin-top:0;max-height:0;overflow:hidden;
       transition:max-height .18s ease,margin-top .18s ease}
  .sx-mile-info.show{max-height:120px;margin-top:9px}
  .sx-mile-info b{color:var(--text);font-weight:800}
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
  .sx-hero-r{display:flex;flex-direction:column;align-items:flex-end;gap:7px}
  .sx-formrow{display:flex;align-items:center;gap:6px;justify-content:flex-end}
  .sx-formk{font-size:9px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);white-space:nowrap}
  .sx-streak{font-size:10.5px;font-weight:800;padding:4px 9px;border-radius:99px;white-space:nowrap}
  .sx-streak.hot{color:#3ee089;background:rgba(52,210,123,.14);border:1px solid rgba(52,210,123,.30)}
  .sx-streak.cold{color:#ff7484;background:rgba(242,93,110,.13);border:1px solid rgba(242,93,110,.30)}
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
  .sx-rel-kpi span{font-size:10px;color:var(--muted);font-weight:600}
  .sx-rel-chart{margin-top:12px}
  .sx-relc{width:100%;height:auto;display:block}
  .sx-relc-yl{fill:var(--muted);font-size:8px;font-weight:700;opacity:.8}
  .sx-relc-xl{fill:var(--muted);font-size:8px;font-weight:700;opacity:.7;text-transform:uppercase;letter-spacing:.04em}
  .sx-rel-note{font-size:10.5px;color:var(--muted);font-weight:600;line-height:1.45;margin-top:11px;
       padding-top:10px;border-top:1px solid var(--border)}
  .sx-rel-note b{color:var(--text)}
  .sx-data .sx-kpis:first-of-type{border-top:0;padding-top:0;margin-top:11px}
  .sx-data-note{font-size:10.5px;color:var(--muted);font-weight:600;line-height:1.45;margin-top:11px;
       padding-top:10px;border-top:1px solid var(--border)}
  .sx-data-note b{color:var(--text)}
  /* En-tête de SECTION (hiérarchie pro de la page Stats) : libellé majuscule accentué + sous-titre */
  .sx-sec{display:flex;align-items:baseline;gap:9px;margin:8px 2px 0;padding-top:6px;
       font-size:11px;font-weight:900;letter-spacing:.10em;text-transform:uppercase;color:var(--accent)}
  .sx-sec::before{content:"";flex:0 0 14px;height:2px;border-radius:2px;background:var(--accent);
       align-self:center;opacity:.85}
  .sx-sec span{font-size:10px;font-weight:700;letter-spacing:.01em;text-transform:none;color:var(--muted)}
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
       text-transform:uppercase;color:#cfe0f5}
  .sx-h span{font-size:9.5px;font-weight:600;color:var(--muted);text-transform:none;letter-spacing:0}
  .sx-sub{font-size:10px;color:var(--muted);line-height:1.35;padding:2px 2px 6px}
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
  .dsp-l{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
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
  .sx-dd-s{font-size:10px;color:var(--muted);font-weight:600;white-space:nowrap;overflow:hidden;
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
  .da-vc-lbl{font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
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
       border:1px solid var(--border);border-left:3px solid #ffb020;border-radius:12px;padding:10px 12px}
  .da-combo-won{border-left-color:#34d27b}
  .da-combo-lost{border-left-color:#ff6b6b}
  .da-combo-h{font-size:12px;font-weight:800;color:#ffd98a;display:flex;align-items:center;gap:8px;
       margin-bottom:7px;text-transform:uppercase;letter-spacing:.03em}
  .da-combo-n{font-weight:700;color:#cdb98a;opacity:.85}     /* « · N jambes » à côté de Combiné */
  .da-combo-c{margin-left:auto;background:#ffb020;color:#1a1200;border-radius:6px;padding:1px 7px;font-weight:800}  /* cote totale : coin haut-droite */
  .da-combo-b{font-size:10px;border-radius:5px;padding:1px 7px;font-weight:800}
  .da-combo-b.won{background:#34d27b;color:#04220f}
  .da-combo-b.lost{background:#ff6b6b;color:#2a0606}
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
  .da-cl-live{color:#ffd98a}
  .da-cl-p{font-variant-numeric:tabular-nums;font-size:10.5px;color:#9fb0c8;
       background:rgba(255,255,255,.06);border-radius:5px;padding:1px 5px}
  .da-cl-pr{font-size:10px;font-weight:800;padding:1px 7px;border-radius:999px;border:1px solid;
       font-variant-numeric:tabular-nums}                    /* pastille CHANCE de la jambe */
  .da-cl-pr.hi{color:#2ec98a;border-color:rgba(46,201,138,.45);background:rgba(46,201,138,.12)}
  .da-cl-pr.mid{color:#22b8ff;border-color:rgba(34,184,255,.45);background:rgba(34,184,255,.12)}
  .da-cl-pr.lo{color:#ffb020;border-color:rgba(255,176,32,.45);background:rgba(255,176,32,.12)}
  .da-cl-why{font-size:11px;line-height:1.5;color:#b9c2cf;padding:3px 0 0 2px}   /* pourquoi DE LA JAMBE (complet) */
  .da-combo-why{font-size:11px;line-height:1.55;color:#cfe0f5;font-style:italic;margin:0 0 9px}   /* synthèse (intro en tête) */
  .da-combo-live{border-left-color:#ffb020}
  .da-combo-b.live{background:#ffb020;color:#1a1200;animation:combopulse 1.6s ease-in-out infinite}
  @keyframes combopulse{0%,100%{opacity:1}50%{opacity:.55}}
  .da-bets{width:100%;border-collapse:separate;border-spacing:0;font-size:11.5px;
       background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
  .da-bets th{background:var(--surface2);color:var(--muted);font-weight:700;text-align:left;
       padding:7px 9px;font-size:10px;text-transform:uppercase;letter-spacing:.04em}
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
  .da-bk-ok,
  .da-bk-mid,
  .da-bk-hi{border-left-color:#34d27b}   /* sûreté ne colore PLUS la bande (étoiles) */
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
  .da-bx-lbl{font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}
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
  .da-bk-mark{margin-left:auto;font-size:10px;font-weight:900;padding:2px 8px;border-radius:99px;
       letter-spacing:.02em}
  .da-bk-mark.mk-w{color:#06140d;background:#34d27b}
  .da-bk-mark.mk-l{color:#fff;background:#ff6b6b}
  .da-bk-mark.mk-p{color:#0b1428;background:#9fb0c8}   /* badge ✅ À JOUER : OR */
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
  .da-faits-b{padding:8px 14px 12px;font-size:12.5px;line-height:1.65;color:var(--text)}
  .da-faits-b .da-ul{padding-left:4px;list-style:none}
  .da-faits-b .da-ul li{margin:9px 0;padding-left:15px;position:relative}
  .da-faits-b .da-ul li::before{content:"";position:absolute;left:0;top:7px;width:6px;height:6px;
       border-radius:99px;background:var(--accent)}
  .da-faits-b a{display:inline-block;padding:1px 8px;margin:1px 2px 1px 0;border-radius:99px;
       font-size:10px;font-weight:700;color:var(--accent);background:rgba(255,255,255,.05);
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
  /* Footer ancré EN BAS de la zone scrollable (margin-top:auto) : plus de gros vide sous le contenu
     court -> le « 18+ » occupe le bas, juste au-dessus de la barre. Contenu long : padding = espacement. */
  /* Pas de `margin-top:auto` : sinon, dans le .wrap flex-column étiré, le pied de page est POUSSÉ tout
     en bas et laisse un GROS VIDE sous la liste quand elle ne remplit pas l'écran. Il suit le contenu. */
  .foot{color:var(--dim);font-size:10.5px;margin-top:22px;padding-top:14px;text-align:center;line-height:1.6;
        border-top:1px solid rgba(255,255,255,.05)}
  .src{font-size:12px;font-weight:600;padding:9px 13px;border-radius:12px;margin:4px 0 2px;
       border:1px solid var(--border)}
  .src.ok{background:rgba(46,226,127,.10);color:var(--accent);border-color:rgba(46,226,127,.22)}
  .src.ko{background:var(--gold-bg);color:var(--gold);border-color:var(--gold-bd)}
  /* ===== Polish OddScore : chiffres mono · en-têtes « • » · titres majuscules ===== */
  :root{--font-mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
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
  /* ⭐ pari RETENU par le moteur (ex-mode bankroll,
  UI retirée) : étoile à droite du nom du pari,
  sur le CADRE déplié ET sur la ligne de la carte repliée */
  .da-bk-star{font-size:13px;vertical-align:1px;
       filter:drop-shadow(0 0 6px rgba(246,197,74,.65))}
  .mc-star{font-size:10px;filter:drop-shadow(0 0 5px rgba(246,197,74,.6))}
  /* Grande courbe d'équité de la carte Performance (accueil) */
  .dperf-chart{margin:10px 0 2px}
  .dperf-chart .sx-heroc{display:block;width:100%;height:88px}
  /* Bandeau « N matchs en direct -> Live » sur l'accueil (les lives ne sont plus listés ici) */
  .dash-livebar{display:flex;align-items:center;gap:9px;margin:14px 0 4px;padding:11px 14px;
       border:1px solid rgba(52,210,123,.4);border-radius:14px;font-size:12.5px;color:var(--text);
       background:linear-gradient(180deg,rgba(52,210,123,.10),rgba(52,210,123,.03))}
  .dash-livebar .nr-dot{width:9px;height:9px;flex:none}
  .dash-livebar-go{margin-left:auto;font-size:10px;font-weight:800;color:#34d27b;
       text-transform:uppercase;letter-spacing:.04em}
  /* Carte « Évolution du profit » (/stats) : courbe d'équité unique + repères */
  .sx-card{background:linear-gradient(180deg,rgba(34,184,255,.09),rgba(34,184,255,.02));
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
_SPA_TABS = [("home", "/", "📅", "À venir"), ("stats", "/stats", "📊", "Stats"),
             ("tennis", "/app", "🎾", "Tennis"), ("basket", "/basket", "🏀", "Basket"),
             ("foot", "/foot", "⚽", "Foot"), ("directs", "/directs", _LIVE_RADAR, "Live")]

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
    "if(d>0)return U(p(d),'j')+' '+U(p(h),'h')+' '+U(p(m),'m');"
    "if(h>0)return U(p(h),'h')+' '+U(p(m),'m');"
    "return U(p(m),'m')+' '+U(p(x),'s');}"
    "function t(){var n=Date.now(),e=document.getElementsByClassName('cd');"
    "for(var i=0;i<e.length;i++){var v=e[i].getAttribute('data-ts');if(!v)continue;"
    "var ms=parseInt(v,10)*1000-n;e[i].innerHTML=f(ms);"
    "e[i].className=ms<=0?'cd live':(ms<3600000?'cd soon':'cd');}}"
    "t();setInterval(t,1000);})();"
)

# SPA : tout est chargé à l'ouverture (le sport actif rendu côté serveur, les 3 autres
# préchargés en arrière-plan via ?frag=1), puis la nav du bas bascule les panneaux SANS
# rechargement. Vanilla JS, ~0 dépendance. history.pushState garde l'URL/refresh cohérents.
# Phase « boot » : la cascade d'apparition (CSS body.boot) ne joue qu'au PREMIER rendu ; la classe
# saute après ~1 s -> les refresh live (45 s, innerHTML remplacé) ne re-déclenchent rien.
# (Le compteur de bankroll a été retiré avec l'UI simulation, 2026-06-12.)
_ANIM_JS = (
    "(function(){var b=document.body;b.classList.add('boot');"
    "setTimeout(function(){b.classList.remove('boot');},950);})();"
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
    "var card=e.target.closest('.row.mc');if(!card)return;e.preventDefault();"
    "var b=card.querySelector('.mc-body');if(!b)return;"
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

_SPA_JS = (
    "(function(){var P=document.getElementById('panels');if(!P)return;"
    "function panel(t){return document.getElementById('pn-'+t);}"
    "function show(t){var c=P.children,i;for(i=0;i<c.length;i++)"
    "c[i].classList.toggle('on',c[i].getAttribute('data-tab')===t);"
    "var n=document.querySelectorAll('.botnav a'),j;for(j=0;j<n.length;j++)"
    "n[j].classList.toggle('on',n[j].getAttribute('data-tab')===t);"
    "document.body.className='sp-'+t;"
    "var sp=panel(t);if(sp){if(window._twCount)setTimeout(function(){window._twCount(sp);},50);"
    "if(window._mcInit)window._mcInit(sp);}}"
    "function load(p){if(!p||p.getAttribute('data-loaded'))return;"
    "p.setAttribute('data-loaded','1');var u=p.getAttribute('data-src');"
    "fetch(u+(u.indexOf('?')<0?'?':'&')+'frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){p.innerHTML=h;"
    # onglet Directs : on n'allume le rouge clignotant QUE s'il y a du live dans le panneau
    "if((u||'').indexOf('/directs')>=0){var nv=document.querySelector('.botnav a[data-tab=\"directs\"]');"
    "if(nv)nv.classList.toggle('has-live',h.indexOf('🟢 Live')>=0);}"
    "if(window._twScan)window._twScan(p);if(window._mcInit)window._mcInit(p);})"
    ".catch(function(){p.removeAttribute('data-loaded');"
    "p.innerHTML='<div class=ldg>Erreur de chargement. Touchez l\\'onglet pour réessayer.</div>';});}"
    "function go(t,push){var p=panel(t);if(!p)return;load(p);show(t);"
    "if(push)try{history.pushState({tab:t},'',p.getAttribute('data-src'));}catch(e){}"
    "var sc=document.querySelector('.wrap');if(sc)sc.scrollTop=0;else window.scrollTo(0,0);}"
    # panneau actif (rendu serveur) = déjà chargé ; on précharge les autres tout de suite
    "var c=P.children,i;for(i=0;i<c.length;i++){"
    "if(c[i].classList.contains('on'))c[i].setAttribute('data-loaded','1');else load(c[i]);}"
    "var nav=document.querySelectorAll('.botnav a');for(i=0;i<nav.length;i++){"
    "nav[i].addEventListener('click',function(e){e.preventDefault();"
    "go(this.getAttribute('data-tab'),true);});}"
    "window.addEventListener('popstate',function(e){var t=(e.state&&e.state.tab);"
    "if(!t){var m={'/':'home','/directs':'directs','/app':'tennis','/basket':'basket','/foot':'foot','/stats':'stats'};"
    "t=m[location.pathname]||'home';}go(t,false);});"
    # Filtre temporel des stats : clic sur un bouton période -> recharge le panneau stats (since)
    "P.addEventListener('click',function(e){var a=e.target&&e.target.closest?"
    "e.target.closest('a[data-since]'):null;if(!a)return;e.preventDefault();"
    "var sp=panel('stats');if(!sp)return;"
    "fetch('/stats?frag=1&since='+a.getAttribute('data-since'),{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){sp.innerHTML=h;"
    "if(window._twScan)window._twScan(sp);if(window._mcInit)window._mcInit(sp);"
    "var sc=sp.querySelector('.wrap')||document.querySelector('.wrap');if(sc)sc.scrollTop=0;});});"
    # (handlers data-info/data-dvg/data-exp/.mc : déplacés dans _CARDS_JS, partagé avec layout)
    # rafraîchissement auto des COTES/SCORES live : on ré-interroge le panneau actif toutes les
    # 45 s, UNIQUEMENT s'il contient un direct (.live) ET qu'aucun accordéon n'est ouvert
    # (on ne coupe pas une lecture). Le scroll est préservé. Pas de direct = aucun appel réseau.
    "function fresh(){var c=P.children,i,p=null;"
    "for(i=0;i<c.length;i++)if(c[i].classList.contains('on')){p=c[i];break;}"
    "if(!p||!p.getAttribute('data-loaded')||document.hidden)return;"
    "if(!p.querySelector('.live'))return;"
    "if(p.querySelector('.mc-manual'))return;"  # ne pas perturber une carte ouverte À LA MAIN
    "var u=p.getAttribute('data-src');"
    "fetch(u+(u.indexOf('?')<0?'?':'&')+'frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){"
    "var sc=document.querySelector('.wrap');var y=sc?sc.scrollTop:window.scrollY;"
    "p.innerHTML=h;if(window._mcInit)window._mcInit(p);if(sc)sc.scrollTop=y;else window.scrollTo(0,y);})"
    ".catch(function(){});}"
    "setInterval(fresh,45000);})();"
)

# Effet « terminal » : les pronostics + l'analyse se TAPENT (caractère par caractère) à l'ouverture,
# UNE fois, avec un curseur clignotant. Tap = saute l'animation. Sécurité : tout est révélé après 4,5 s
# max et si une erreur survient (jamais de contenu vide). Non destructif si le JS ne tourne pas.
_TERM_JS = (
    "(function(){"
    # Respect de « Réduire le mouvement » AUSSI côté JS (le @media CSS ne stoppe pas requestAnimationFrame) :
    "var _rm=false;try{_rm=window.matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches;}catch(e){}"
    # COMPTEUR : un chiffre/valeur (.da-st-v) qui MONTE de 0 à sa valeur (formats « 87% », « 1.22 », « +6% »).
    "function cnt(nd){if(nd._c||_rm)return;nd._c=1;var t=(nd.textContent||'').trim();"
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
    "window._twCount=function(root){try{var l=(root||document).querySelectorAll('.da-st-v'),i;"
    "for(i=0;i<l.length;i++)cnt(l[i]);}catch(e){}};"
    "window._twScan(document);window._twCount(document);})();"
)

# Repères du modèle : clic sur une pastille OU un marqueur du graphe -> affiche/masque l'explication
# (toggle) dans le panneau dédié. Délégué sur document -> marche aussi pour les panneaux chargés en AJAX.
_MILE_JS = (
    "(function(){document.addEventListener('click',function(e){"
    "var t=e.target.closest('[data-mile]');if(!t)return;"
    "var scope=t.closest('.sx-hero')||t.closest('.sx-card');if(!scope)return;"
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

# Menu tiroir « complet » (☰) — présent sur TOUTES les pages. Accès direct à tout : accueil, paris à
# jouer, bilan, stats, et chaque sport + live. Les clés correspondent à l'item mis en évidence.
# Anti-zoom (ex-_DRAWER_JS — le tiroir ☰ a été retiré, redondant avec la barre du bas).
# Le PINCH-ZOOM est VOLONTAIREMENT autorisé (accessibilité WCAG 1.4.4) -> on ne bloque plus les
# events gesture*. `touch-action:manipulation` neutralise déjà le double-tap-zoom accidentel.
_NOZOOM_JS = ""

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
        f'<span class="ic">{ico}</span><span class="lb">{e(name)}</span></a>'
        for k, href, ico, name in _SPA_TABS) + "</nav>"

    sub = ""
    if subnav and sport in _SPORT_MATCH_URL:
        items = [("matchs", _SPORT_MATCH_URL[sport], "📋 Matchs"),
                 ("perf", f"/tracking/dashboard?sport={sport}", "📊 Fiabilité")]
        sub = '<div class="subnav">' + "".join(
            f'<a class="{"on" if subnav == k else ""}" href="{href}">{e(lbl)}</a>'
            for k, href, lbl in items) + "</div>"

    meta_refresh = '<meta http-equiv="refresh" content="180">' if refresh else ""
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070708">
{meta_refresh}<title>{e(title)} · BETSFIX</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="apple-touch-icon" href="/static/icon-180.png?v=5">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BETSFIX">
<style>{CSS}</style></head><body class="sp-{e(sport)}">
{splash}<div class="wrap"><a class="acct" href="/compte">👤 Compte</a>{toplogo}{pausebar}{sub}{body}
<div class="foot">18+ · Outil informatif, sans garantie · Jouez responsable</div>
</div>{botnav}<script>{_ANIM_JS}</script><script>{_COUNTDOWN_JS}</script><script>{_NOZOOM_JS}</script><script>{_CARDS_JS}</script><script>{_TERM_JS}</script><script>{_MILE_JS}</script></body></html>"""

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
        f'<span class="ic">{ico}</span><span class="lb">{e(name)}</span></a>'
        for k, href, ico, name in _SPA_TABS) + "</nav>"
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#070708">
<title>{e(title)} · BETSFIX</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="apple-touch-icon" href="/static/icon-180.png?v=5">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BETSFIX">
<style>{CSS}</style></head><body class="sp-{e(active)}">
{splash}<div class="wrap"><a class="acct" href="/compte">👤 Compte</a>{toplogo}{pausebar}<main id="panels">{''.join(panels)}</main>
<div class="foot">18+ · Outil informatif, sans garantie · Jouez responsable</div>
</div>{botnav}<script>{_ANIM_JS}</script><script>{_COUNTDOWN_JS}</script><script>{_NOZOOM_JS}</script><script>{_CARDS_JS}</script><script>{_SPA_JS}</script><script>{_TERM_JS}</script><script>{_MILE_JS}</script></body></html>"""

def bars_split(model, implied) -> dict:
    """Champs des barres RÉPARTIES. model/implied = (home, nul|None, away) par source."""
    m = model or (None, None, None)
    i = implied or (None, None, None)
    return {"m_home": m[0], "m_draw": m[1], "m_away": m[2],
            "i_home": i[0], "i_draw": i[1], "i_away": i[2]}

_NAME_CONNECTORS = {"du", "de", "des", "da", "di", "of", "the", "und", "et", "and"}

def _abbr_team(name: str, maxlen: int = 11) -> str:
    """Abrège un nom d'équipe trop long pour l'en-tête (1 ligne) : d'abord retire les connecteurs
    (du, de, of…) -> « Corée du Sud » devient « Corée Sud ». Si encore trop long (clubs : « New
    York Liberty »), garde le DERNIER mot, souvent le plus distinctif -> « Liberty »."""
    name = str(name).strip()
    if len(name) <= maxlen:
        return name
    words = [w for w in name.split() if w.lower() not in _NAME_CONNECTORS]
    short = " ".join(words)
    if len(short) <= maxlen or not words:
        return short or name
    return words[-1]

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
    out = block("Cotes & chances", "ocb-po",
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

def _form_dots(form: list) -> str:
    """Forme = 5 derniers résultats en pastilles (vert gagné / rouge perdu / gris remboursé)."""
    if not form:
        return ""
    return ('<span class="sx-form">'
            + "".join(f'<span class="sx-fd {r}"></span>' for r in form) + "</span>")

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
    """Chip « série en cours » : 🔥 N gagnés / ❄️ N perdus d'affilée. '' si aucune série."""
    if not streak:
        return ""
    if streak > 0:
        return f'<span class="sx-streak hot">🔥 {streak} gagné{"s" if streak > 1 else ""} d\'affilée</span>'
    n = -streak
    return f'<span class="sx-streak cold">❄️ {n} perdu{"s" if n > 1 else ""} d\'affilée</span>'

def _hero_chart(points: list, uid: str = "h", dates: list | None = None,
                milestones: list | None = None) -> str:
    """Grande courbe d'équité (profit cumulé) : aire + courbe VERTE au-dessus de 0 / ROUGE en dessous
    (dégradé à coupure nette sur le zéro), grille + label « 0 ». Si `dates` (coup d'envoi aligné sur
    points[1:]) et `milestones`=[(iso,label)] sont fournis, trace des REPÈRES verticaux NUMÉROTÉS aux
    dates de changement de modèle (la légende texte est rendue à côté, hors SVG)."""
    if not points:
        return ""
    pts = points if len(points) > 1 else (points * 2)
    lo, hi = min(pts + [0.0]), max(pts + [0.0])
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pad = (hi - lo) * 0.16
    lo, hi = lo - pad, hi + pad
    n, W, H, L, R, T, B = len(pts), 320.0, 104.0, 16.0, 8.0, 14.0, 8.0
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
    p.append(f'<path d="{area_d}" fill="url(#{gid})" opacity="0.22" stroke="none"/>')
    p.append(f'<line class="bc-zero" x1="{L:g}" y1="{zy:.1f}" x2="{W - R:g}" y2="{zy:.1f}"/>')
    p.append(f'<text class="bc-zl" x="{L - 3:g}" y="{zy + 3:.1f}">0</text>')
    p.append(f'<path d="{line_d}" fill="none" stroke="url(#{gid})" stroke-width="2.2" '
             'vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>')
    p.append(f'<circle cx="{X(n - 1):.1f}" cy="{Y(pts[-1]):.1f}" r="2.8" '
             f'fill="{GR if pts[-1] >= 0 else RD}"/>')
    # REPÈRES de modèle : trait vertical + pastille numérotée en haut (placés à l'index du 1er pari
    # postérieur à la date du jalon). La correspondance numéro -> nom est dans la légende texte.
    for num, ms in enumerate(milestones or [], 1):
        iso = ms[0]
        day = (iso or "")[:10]
        k = sum(1 for d in (dates or []) if d and d[:10] < day)
        if k <= 0 or k >= n:
            continue
        mx = X(k)
        # groupe cliquable (data-mile) : trait + pastille numérotée + ZONE DE TAP large transparente
        p.append(f'<g class="bc-mile-g" data-mile="{num}">')
        p.append(f'<line class="bc-mile" x1="{mx:.1f}" y1="{T - 4:g}" x2="{mx:.1f}" y2="{H - B:g}"/>')
        p.append(f'<circle class="bc-mile-hit" cx="{mx:.1f}" cy="{T - 5:g}" r="11" fill="transparent"/>')
        p.append(f'<circle class="bc-mile-c" cx="{mx:.1f}" cy="{T - 5:g}" r="5.4"/>')
        p.append(f'<text class="bc-mile-n" x="{mx:.1f}" y="{T - 2.6:g}" text-anchor="middle">{num}</text>')
        p.append('</g>')
    p.append("</svg>")
    return "".join(p)

def sx_section(label: str, sub: str = "") -> str:
    """En-tête de SECTION de la page Stats (hiérarchie pro : vue d'ensemble → détail → fiabilité →
    transparence). Petit libellé majuscule accentué + sous-titre discret, posé au-dessus d'un groupe."""
    s = f'<span>{html.escape(sub)}</span>' if sub else ""
    return f'<div class="sx-sec">{html.escape(label)}{s}</div>'


def render_sports_breakdown(full: dict | None, since: str = "") -> str:
    """« Détail par sport » : une ligne par sport (pastille + mini-courbe + ROI + bilan + cote). '' si
    aucun sport réglé. Extrait de render_stats pour pouvoir le placer dans sa propre section."""
    bs = (full or {}).get("by_sport") or {}
    SPORTS = (("foot", "Football", "#2ee27f"), ("tennis", "Tennis", "#d7e64a"),
              ("basket", "Basket", "#ff9f43"))
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
        + _kpi(ov.get("settled", 0), "simples joués", d24["simples"])
        + _kpi(_cf.get("n", 0), "combinés joués", d24["combos"])
        + '</div><div class="sx-kpis sx-kpis3">'
        + _kpi(cal.get("n", 0), "paris calibrés", d24["calibrated"])
        + _kpi(cal.get("n_shadow", 0), "pronos fantômes", d24["ghosts"])
        + _kpi(vol.get("analysed", 0), "matchs analysés", d24["analysed"])
        + '</div>'
        # EN COURS : pronos analysés en attente de résultat (pipeline actif) — distinct du cumul réglé.
        '<div class="sx-data-sub">⏳ En cours · en attente de résultat</div>'
        '<div class="sx-kpis sx-kpis3">'
        f'<div class="sx-kpi"><b>{pend["simples"]}</b><span>simples en cours</span></div>'
        f'<div class="sx-kpi"><b>{pend["combos"]}</b><span>combinés en cours</span></div>'
        f'<div class="sx-kpi"><b>{pend["ghosts"]}</b><span>fantômes en cours</span></div>'
        '</div>'
        '<div class="sx-data-note">Le <b>+N vert</b> = entrées des dernières <b>24 h</b>. '
        '« <b>En cours</b> » = pronos analysés en attente de résultat (matchs à venir / récents). Les '
        '<b>simples</b> et <b>combinés joués</b> sont les seuls comptés dans le ROI et la courbe. Les '
        '<b>pronos fantômes</b> (prédictions SIMPLES non jouées, réglées après match) affinent la '
        '<b>calibration</b> sur tout le spectre de cotes — ils n\'entrent JAMAIS dans le bilan, et il '
        'n\'existe pas de combiné fantôme.</div></div>')


def render_stats(full: dict | None, since: str = "", combo_full: dict | None = None) -> str:
    """Onglet STATISTIQUES — premium & lisible : (1) bilan global (ROI + KPIs), (2) courbe d'équité
    UNIQUE (profit cumulé) avec repères des changements de modèle, (3) détail par sport (ligne +
    mini-courbe), (4) calibration en aval. `since` propagé aux liens drill-down. '' si rien réglé."""
    full = full or {}
    ov = full.get("overall") or {}
    if not ov.get("settled"):
        return ""
    sc = full.get("since_change") or {}
    # KPI à SUIVRE : nouveau système (1 pari/match + 3 agents). Libellé COURT (pas de retour à la ligne).
    nv_val = _roistr(sc.get("roi")) if sc.get("settled") else "—"
    nv_cls = _roi_cls(sc.get("roi"), sc.get("settled")) if sc.get("settled") else "hi"
    new_kpi = (f'<div class="sx-kpi" title="Nouveau système ({sc.get("settled") or 0} paris réglés)">'
               f'<b class="arec-{nv_cls}">{nv_val}</b><span>nouv. système</span></div>')
    # COURBE + repères (≥1 explication d'1 ligne par mise à jour du modèle).
    miles = list(analyses.MODEL_MILESTONES)
    chart = _hero_chart(ov.get("points") or [], uid="all",
                        dates=ov.get("dates") or [], milestones=miles)
    # Repères ALLÉGÉS : pastilles numérotées cliquables (+ marqueurs du graphe) ; l'explication ne
    # s'affiche QU'AU CLIC dans un panneau dédié (toggle, JS délégué _MILE_JS). Données en DOM caché.
    mchips = "".join(f'<button type="button" class="sx-mile-b" data-mile="{i}">{i}</button>'
                     for i in range(1, len(miles) + 1))
    mdata = "".join(f'<div class="sx-mile-d" data-mile="{i}" hidden>'
                    f'<b>{html.escape(lab)}</b> — {html.escape(desc)}</div>'
                    for i, (_iso, lab, desc) in enumerate(miles, 1))
    mlegend = (f'<div class="sx-miles"><div class="sx-ml-h">Repères du modèle'
               f'<span class="sx-ml-hint">touchez un repère pour le détail</span></div>'
               f'<div class="sx-mile-bs">{mchips}</div>'
               f'<div class="sx-mile-info"></div>{mdata}</div>') if miles else ""
    chart_block = (f'<div class="sx-divider"></div>'
                   f'<div class="sx-h sx-h2">📈 Simples<span>évolution du rendement</span></div>'
                   f'<div class="sx-equity">{chart}</div>{mlegend}') if chart else ""
    # UN SEUL cadre : ROI + forme (≥10 bulles) + KPIs + courbe + repères expliqués.
    # Forme = 10 dernières (mêmes pastilles W/L que les onglets sport, lettre majuscule, récent à DROITE).
    _LET = {"won": "W", "lost": "L", "push": "N"}
    # DEUX lignes de forme distinctes (demande user) : SIMPLES et COMBINÉS, chacune labellisée. On
    # n'affiche une ligne que si elle a des résultats. Repli : ancienne ligne unique si aucune des deux.
    _fs = form_dots([_LET.get(x, x) for x in (ov.get("form_simple") or [])], n=8)
    _fc = form_dots([_LET.get(x, x) for x in (ov.get("form_combo") or [])], n=8)
    # KPI CLV (Closing Line Value) : se remplit à mesure que de nouveaux paris RÉSULTAT se règlent.
    # >0 = on prend en moyenne de meilleures cotes que la clôture = edge réel. '—' tant que vide.
    from app import clv as _clvmod
    _cs = _clvmod.clv_stats()
    if _cs.get("n"):
        _cv = _cs["avg_pct"]
        clv_kpi = (f'<div class="sx-kpi" title="CLV : cote prise vs cote de clôture du marché. '
                   f'&gt;0 = on bat le marché. {_cs.get("beat_pct")}% des paris au-dessus, sur '
                   f'{_cs["n"]} paris résultat.">'
                   f'<b class="arec-{_roi_cls(_cv, _cs["n"])}">{"+" if (_cv or 0) >= 0 else ""}{_cv}%</b>'
                   f'<span>CLV ({_cs["n"]})</span></div>')
    else:
        clv_kpi = ('<div class="sx-kpi" title="CLV (Closing Line Value) : battre la cote de clôture du '
                   'marché = juge d\'edge le plus rapide. Se remplit dès que des paris résultat se règlent.">'
                   '<b>—</b><span>CLV</span></div>')
    # Forme PROPRE à chaque bloc : simples dans le hero simples, combinés dans le hero combinés.
    _simples_form = (f'<div class="sx-formrow"><span class="sx-formk">Forme</span>{_fs}</div>' if _fs
                     else form_dots([_LET.get(x, x) for x in (ov.get("form12") or ov.get("form") or [])], n=10))
    _combo_form = f'<div class="sx-formrow"><span class="sx-formk">Forme</span>{_fc}</div>' if _fc else ""
    hero = (
        '<div class="sx-hero"><div class="sx-hero-top">'
        f'<div class="sx-hero-main"><div class="sx-hero-roi arec-{_roi_cls(ov.get("roi"), ov.get("settled"))}">'
        f'{_roistr(ov.get("roi"))}</div><div class="sx-hero-lbl">ROI · paris simples {_ind(ov.get("settled"))}</div></div>'
        f'<div class="sx-hero-r">{_simples_form}</div></div>'
        '<div class="sx-kpis">'
        f'<div class="sx-kpi"><b>{ov["settled"]}</b><span>simples réglés</span></div>'
        f'<div class="sx-kpi"><b class="arec-{_pct_class(ov["pct"])}">{ov["pct"]}%</b><span>réussite</span></div>'
        f'<div class="sx-kpi"><b>{ov.get("avg_odds") or "—"}</b><span>cote moy.</span></div>'
        f'{new_kpi}'
        f'{clv_kpi}'
        '</div>'
        f'{chart_block}'
        '</div>')
    # BLOC COMBINÉS = MIROIR du bloc simples (même style : gros ROI + forme + KPIs + courbe), JUSTE EN
    # DESSOUS (demande user). render_combos renvoie un hero complet identique en structure.
    equity = render_combos(combo_full if combo_full is not None else analyses.combo_stats(), _combo_form)
    # render_stats = la VUE D'ENSEMBLE seule (Simples + Combinés). Le détail par sport, le rendement
    # par cote, la calibration et le volume de données sont ajoutés en SECTIONS distinctes par la route
    # (_home_stats) -> hiérarchie pro : synthèse → détail → fiabilité → transparence.
    return f'{hero}{equity}'


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
    """Rendement par tranche de COTE (axe unique, absent de la calibration). Le ROI par CONFIANCE et
    par MARCHÉ a été FUSIONNÉ dans la calibration (une seule vue par axe, non redondante). '' si vide."""
    perf = perf or {}
    return _roi_section("Rendement par cote", "ROI selon la cote jouée", perf.get("by_odds") or [])


def render_combos(cs: dict, form_html: str = "") -> str:
    """Bloc COMBINÉS = MIROIR EXACT du bloc simples (même style : gros ROI + forme + KPIs + courbe),
    affiché JUSTE EN DESSOUS (demande user). Vraie cote, ROI séparé des simples, + réussite par nb de
    jambes en info supplémentaire."""
    if not cs or not cs.get("n"):
        return ""
    roi = cs.get("roi")
    wr = cs.get("win_rate")
    pts = cs.get("points") or []
    chart = (f'<div class="sx-equity">{_hero_chart(pts, uid="combos")}</div>'
             if len([p for p in pts if p]) else "")
    prof = cs.get("profit")
    kpis = (
        f'<div class="sx-kpi"><b>{cs["n"]}</b><span>combinés réglés</span></div>'
        f'<div class="sx-kpi"><b class="arec-{_pct_class(wr)}">{wr if wr is not None else "—"}%</b>'
        f'<span>réussite</span></div>'
        f'<div class="sx-kpi"><b>{cs.get("avg_odds") or "—"}</b><span>cote moy.</span></div>'
        f'<div class="sx-kpi"><b>{cs.get("avg_shave") if cs.get("avg_shave") is not None else "—"}%</b>'
        f'<span>rabot moyen</span></div>'
        + (f'<div class="sx-kpi"><b class="arec-{_roi_cls(prof, cs["n"])}">{prof:+.1f}u</b>'
           f'<span>profit</span></div>' if prof is not None else ''))
    # réussite par nombre de jambes (info en plus, propre aux combinés)
    legrows = ""
    for k, g in sorted((cs.get("by_legs") or {}).items()):
        w = g.get("wr")
        legrows += (f'<div class="sx-leg"><span>{k} jambes</span>'
                    f'<span class="sx-leg-n">{g["n"]} combiné{"s" if g["n"] > 1 else ""}</span>'
                    f'<b>{w if w is not None else "—"}%</b></div>')
    return (
        '<div class="sx-hero"><div class="sx-hero-top">'
        f'<div class="sx-hero-main"><div class="sx-hero-roi arec-{_roi_cls(roi, cs["n"])}">'
        f'{_roistr(roi)}</div><div class="sx-hero-lbl">ROI · combinés {_ind(cs["n"])}</div></div>'
        f'<div class="sx-hero-r">{form_html}</div></div>'
        f'<div class="sx-kpis">{kpis}</div>'
        '<div class="sx-divider"></div>'
        '<div class="sx-h sx-h2">🎲 Combinés<span>évolution · vraie cote</span></div>'
        f'{chart}'
        + (f'<div class="sx-legs">{legrows}</div>' if legrows else '')
        + '</div>')


def render_dashboard(match_rows: list, *, live_count: int = 0,
                     frag: bool = False, source: dict | None = None) -> str:
    """ACCUEIL épuré (2026-06-13) : UNIQUEMENT les matchs À VENIR (format compact, tous sports
    mélangés, triés par coup d'envoi) + un petit bandeau « N en direct → Live ». Les statistiques
    sont passées dans leur propre onglet 📊 de la barre du bas."""
    livebar = ((f'<a class="dash-livebar" href="/directs"><span class="nr-dot"></span>'
                f'<b>{live_count} match{"s" if live_count > 1 else ""} en direct</b>'
                '<span class="dash-livebar-go">suivre dans Live →</span></a>')
               if live_count else "")
    if match_rows:
        matches = ('<div class="dash-h"><span>Prochains matchs</span>'
                   f'<span class="dash-h-a">{len(match_rows)}</span></div>'
                   + _rows_by_day(match_rows))
    else:
        matches = ('<div class="dash-h"><span>Prochains matchs</span></div>'
                   '<div class="paj-empty">Aucun match analysé à venir pour l\'instant.</div>')
    body = livebar + matches
    return body if frag else spa_shell("home", "Accueil", body, source=source)

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
    return (
        '<div class="sx-card sx-rel"><div class="sx-h">Indice de fiabilité'
        '<span>calibration · auto-évolution</span></div>'
        '<div class="sx-rel-top">'
        f'<div class="sx-rel-main"><div class="sx-rel-idx">{idx}<small>/100</small></div>'
        f'<div class="sx-rel-tr {cls}">{arrow} {word}</div></div>'
        f'<div class="sx-rel-kpi"><b>{ecart}</b><span>écart confiance↔réel</span></div></div>'
        f'<div class="sx-rel-chart">{chart}</div>'
        f'<div class="sx-rel-note">Courbe de l\'indice dans le temps (gauche = début, droite = récent). '
        f'L\'écart entre la confiance annoncée et la réussite réelle se resserre : le modèle '
        f'<b>se recalibre seul</b> à chaque résultat (rétrécissement bayésien sur <b>{rel.get("n")}</b> '
        f'prédictions) et écarte tout seul les marchés perdants. Plus la courbe monte, plus la confiance '
        f'affichée tient ses promesses.</div>'
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

def odds_bar(outcomes, highlight_idx: int | None = None, label: str = "Bookmakers") -> str:
    """Cotes Unibet présentées comme une BARRE (même style que BETSFIX/Unibet/Public), placée
    EN PREMIER. Un segment par issue avec UNIQUEMENT la cote (l'issue se lit par sa position,
    alignée sur les barres du dessous) ; le pari/favori surligné en bleu. `label` = intitulé de
    la barre (« Bookmakers », ou « Bookmakers live » pour les cotes en direct).
    `outcomes` = [(libellé, cote), ...] ; `highlight_idx` = issue pronostiquée par BETSFIX."""
    lab = html.escape(label)
    valid = [(i, lbl, o) for i, (lbl, o) in enumerate(outcomes) if o]
    if not valid:
        return (f'<div class="sb"><span class="sb-l">{lab}</span>'
                '<div class="sb-bar ocbar"><span class="seg pba">à venir</span></div></div>')
    # Segments en navy .pba ; la MEILLEURE cote (la plus basse = le favori du book) ressort en
    # BLEU BETSFIX .pm (l'ancien bleu du modèle), pour la mettre en avant.
    def _f(o):
        try:
            return float(o)
        except (TypeError, ValueError):
            return float("inf")
    best_i = min(valid, key=lambda t: _f(t[2]))[0]
    segs = "".join(
        f'<span class="seg {"pm" if i == best_i else "pba"}"><b>{o}</b></span>'
        for i, _, o in valid)
    return (f'<div class="sb"><span class="sb-l">{lab}</span>'
            f'<div class="sb-bar ocbar">{segs}</div></div>')

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

def render_sport_perf(sport: str) -> str:
    """Carte PREMIUM UNIQUE de performance du sport, SOUS le titre, dans UN SEUL cadre : ROI géant +
    forme + courbe d'équité + KPIs, puis (intégré au même cadre, repliable) le détail PAR PARI et la
    CALIBRATION. '' si aucun résultat réglé pour ce sport (rien à montrer)."""
    from app import analyses
    label, icon = _SPORT_FR_LABEL.get(sport, (sport.title(), ""))
    s = (analyses.stats_full().get("by_sport") or {}).get(sport)
    if not s or not s.get("settled"):
        return ""
    roi = s.get("roi")
    # Forme en 2 lignes (Simples / Combinés) comme le graphe principal. Combinés = foot (CdM) seulement
    # -> la ligne ne s'affiche que si elle a des résultats ; repli sur l'ancienne ligne unique sinon.
    _LET = {"won": "W", "lost": "L", "push": "N"}
    _fs = form_dots([_LET.get(x, x) for x in (s.get("form_simple") or [])], n=8)
    _fc = form_dots([_LET.get(x, x) for x in (s.get("form_combo") or [])], n=8)
    _rows = []
    if _fs:
        _rows.append(f'<div class="sx-formrow"><span class="sx-formk">Simples</span>{_fs}</div>')
    if _fc:
        _rows.append(f'<div class="sx-formrow"><span class="sx-formk">Combinés</span>{_fc}</div>')
    forms = f'<div class="spf-forms">{"".join(_rows)}</div>' if _rows else form_dots(s.get("form"))
    chart = _hero_chart(s.get("points") or [], uid=f"sp-{sport}")

    def kpi(v, lbl):
        return f'<div class="spf-k"><span class="spf-kv">{v}</span><span class="spf-kl">{lbl}</span></div>'
    kpis = (kpi(f'{s["pct"]}%', "Réussite") + kpi(s["settled"], "Paris")
            + kpi(f'@{s.get("avg_odds") or "—"}', "Cote moy."))
    # Détail INTÉGRÉ au MÊME cadre (repliable) : par pari + calibration par TYPE DE PARI de ce sport.
    g = (analyses.calibration().get("by_sport") or {}).get(label) or {}
    det = [_sport_card(s, sport, label, icon, "")]
    mk_rows = "".join(_calib_line(mk, mg, sub=True) for mk, mg in (g.get("markets") or {}).items())
    if mk_rows:
        det.append('<div class="calg-h">Calibration · par type de pari</div>'
                   f'<div class="calg">{mk_rows}</div>')
    details = (f'<details class="spf-det"><summary><span class="spf-det-t">📊 Fiabilité & calibration</span>'
               f'<span class="chev">▾</span></summary><div class="spf-det-b">{"".join(det)}</div></details>')
    return (f'<div class="spf">'
            f'<div class="spf-top"><div class="spf-roi-wrap">'
            f'<span class="spf-roi arec-{_roi_cls(roi, s.get("settled"))}">{_roistr(roi)}</span>'
            f'<span class="spf-roi-l">ROI {_ind(s.get("settled"))}</span></div>{forms}</div>'
            f'{chart}<div class="spf-kpis">{kpis}</div>{details}</div>')

def _pick_card(p: dict, badge: str) -> str:
    """Carte d'un pari pour l'accueil (value OU confiance), avec le tableau des chances.
    Titre = l'AFFICHE (les 2 équipes) ; le pari/cote n'est PAS répété (la cote pariée est
    surlignée en bleu dans la ligne de cotes du dessous)."""
    e = html.escape
    cd = (f'<span class="cd" data-ts="{int(p["start_ts"])}"></span>'
          if p.get("start_ts") and p["start_ts"] > time.time() else "")
    # « (F) » seulement au foot : tennis WTA / basket WNBA sont d'office féminins
    fem = (' <span class="fem">(F)</span>'
           if p.get("female") and p.get("sport") not in ("Tennis", "Basket") else "")
    state = cd if cd else ('<span class="cd live">🟢 Live</span>' if p.get("live") else "")
    bdg = f'<span class="bdg">{badge}</span>' if badge else ""
    # surligne l'issue pariée (cohérent avec les barres), pas le favori du book
    _hi = {"1": 0, "X": 1, "2": 2, "home": 0, "away": 1}.get(p.get("side"))
    oddsrow = odds_bar(p["odds_cells"], highlight_idx=_hi) if p.get("odds_cells") else ""
    hf = f'{p["home_flag"]} ' if p.get("home_flag") else ""      # gauche : drapeau AVANT le nom
    af = f' {p["away_flag"]}' if p.get("away_flag") else ""       # droite : drapeau APRÈS le nom
    # « perle rare » : le pari à jouer (meilleur équilibre confiance×value parmi TOUS les
    # marchés Unibet), mis en avant au-dessus des barres de contexte.
    # Le « pari à jouer » (perle + barre de confiance) va SOUS les cotes — différencié conf/value.
    sport_lbl = e(p["sport"]) + (f' · {e(p["league"])}' if p.get("league") else "")
    inner = (f'<div class="rowtop"><span>{p["icon"]} {sport_lbl}{fem} · {e(p.get("time") or "")}</span>'
             f'<span class="rt-r">{state}</span></div>'
             f'<div class="mrow"><div class="players">{hf}{e(_noF(p.get("home")))} '
             f'<span class="dim">vs</span> {e(_noF(p.get("away")))}{af}</div>{bdg}</div>'
             f'{oddsrow}{_pick_bars(p)}'
             # Les « paris à jouer » (cadres) remplacent la bannière perle « Confiance », SOUS les barres.
             f'{_bets_for_url(p.get("url") or "")}')
    url = p.get("url") or ""
    # On passe le TYPE de pari (confiance/value) à l'analyse -> elle recommande LA MÊME perle
    # que la carte (sinon l'analyse parlait d'un pari et la carte en jouait un autre).
    pkp = f'&pk={p["pick_kind"]}' if p.get("pick_kind") else ""
    # Comme les onglets : tap -> déplie l'analyse DANS le cadre, sans changer de vue.
    if url.startswith(("/foot/match/", "/basket/match/", "/app/match/")):
        sep = "&" if "?" in url else "?"
        return (f'<div class="row pick rowtap" data-exp="{url}{sep}frag=1{pkp}">{inner}'
                f'<div class="exp-c"><span class="exp-chev">▾</span> Voir l\'analyse</div>'
                f'<div class="exp" hidden></div></div>')
    return f'<a class="row pick" href="{url}">{inner}</a>'

# Légende des 3 barres, réutilisée partout (accueil + intros des onglets) pour une explication
# COHÉRENTE et claire pour le parieur.
BARS_LEGEND = ('Chaque barre montre les <b>chances de chaque camp</b> (joueur 1 à gauche, '
               'joueur 2 à droite, total 100 %), selon 3 sources : <b>BETSFIX</b> (notre '
               'analyse), <b>Cote Unibet</b> (chances cachées derrière la cote) et le <b>Public</b> '
               '(votes des parieurs). Quand <b>BETSFIX donne plus de chances qu\'Unibet</b> à un '
               'camp, sa cote est peut-être trop généreuse — une <b>« value »</b>.')

def render_home(rep: dict, source: dict | None = None,
                picks: list[dict] | None = None,
                conf_picks: list[dict] | None = None, frag: bool = False,
                proof_html: str = "") -> str:
    # l'état SofaScore (pause) s'affiche désormais discrètement dans l'en-tête (cf. layout).
    picks = picks or []
    conf_picks = conf_picks or []
    bars_legend = BARS_LEGEND

    # 🎯 MATCHS ANALYSÉS — une SEULE liste, triée par COUP D'ENVOI le plus proche (tous sports
    # mélangés). Plus de regroupement par sport : on veut « le prochain match » en haut.
    if conf_picks:
        ordered = sorted(conf_picks,
                         key=lambda p: (p.get("start_ts") is None, p.get("start_ts") or 0))
        rows = "".join(_pick_card(p, "") for p in ordered)
        conf_html = _section(f'🎯 Prochains matchs ({len(conf_picks)})', rows, open_=True)
    else:
        conf_html = _section('🎯 Matchs analysés (0)',
                             '<div class="banner">Aucune analyse à venir pour le moment — '
                             'les prochaines arrivent au prochain scan.</div>')

    # 💎 VALEURS du jour : edge vs cote (le book sous-évalue le pari) — souvent des outsiders.
    # NB : pas de badge value en haut à droite — l'edge est déjà dans la bannière « À JOUER »
    # (« value +X% ») et dans l'analyse (Paris conseillés). Le cadre haut-droite reste épuré.
    if picks:
        rows = "".join(_pick_card(p, "") for p in picks)
        val_html = _section(f'💎 Valeurs ({len(picks)})', rows, open_=True,
                            info='Même analyse que les Confiances (tous les paris Unibet du match), '
                                 'mais on garde ici la <b>perle au plus gros edge</b> : le pari où la '
                                 '<b>cote est la plus trop généreuse</b> (Unibet donne moins de chances '
                                 'que <b>BETSFIX</b>). Ça <b>gagne moins souvent mais rapporte plus</b> : '
                                 'rentable <b>sur la durée</b>, jamais garanti sur un match. Le badge '
                                 f'<b>value +X%</b> = notre avantage estimé sur la cote. {bars_legend}')
    else:
        val_html = _section('💎 Valeurs (0)',
                            '<div class="banner">Aucune value détectée pour le moment '
                            '(les cotes Unibet apparaissent à l\'approche des matchs).</div>')

    # Accueil analyste : la section « Valeurs » (moteur Elo) n'apparaît que si des picks value
    # sont fournis (plus le cas en mode analyste) ; sinon on ne montre que les matchs analysés.
    body = f'{proof_html}{conf_html}' + (val_html if picks else "")
    return body if frag else spa_shell("home", "Accueil", body, source=source)

def _bar(pct: float | None) -> str:
    p = round((pct or 0) * 100)
    return f'<div class="bar"><span style="width:{p}%"></span></div>'

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
    """Retire le suffixe « (F) » (féminin, WNBA/WTA) du nom d'équipe AFFICHÉ."""
    return re.sub(r"\s*\(\s*F\s*\)\s*$", "", (name or "").strip())

def _cap(s: str) -> str:
    """Capitalise la 1re lettre (les villes/tournois Unibet arrivent souvent en minuscule, ex.
    « s-Hertogenbosch » -> « S-Hertogenbosch ») sans toucher au reste (« Roland Garros » préservé)."""
    s = (s or "").strip()
    return (s[0].upper() + s[1:]) if (s and s[0].islower()) else s

def _short_team(name: str, tennis: bool) -> str:
    """Nom AFFICHÉ compact : au tennis -> nom de famille (dernier mot) pour tenir sur une ligne ;
    foot/basket -> nom complet (sans « (F) »)."""
    n = _noF(name or "")
    return (n.split() or [n])[-1] if tennis else n

def _live_scoreboard(score: str, home: str, away: str, tennis: bool = False,
                     server: str | None = None, points: tuple | None = None,
                     clock: str | None = None, periods: list | None = None,
                     best_of: int | None = None) -> str:
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

    def cells(i):
        return "".join(f'<span class="lb-c{" lb-win" if c[i] > c[1 - i] else ""}">{c[i]}</span>'
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
                                  best_of=r.get("best_of"))
    elif is_finished and r.get("score"):
        # Score FINAL présenté COMME en live, AVEC le détail : sets (tennis « 6-4 3-6 6-2 ») ou
        # quart-temps (basket `periods`), sinon total 2 lignes. Sans horloge (match terminé).
        sc = str(r.get("score"))
        periods = r.get("periods")
        tennis_cols = _is_tennis and len(sc.split()) > 1          # plusieurs sets -> colonnes
        if _is_tennis and not tennis_cols and not periods:       # repli : total des sets en 2 lignes
            sc = re.sub(r"\s*\((?:sets?|SETS?)\)\s*$", "", sc).strip()
        lscore = _live_scoreboard(sc, r.get("home") or "", r.get("away") or "",
                                  tennis=tennis_cols, periods=periods, best_of=r.get("best_of"))
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
    sport_name = {"tennis": "Tennis", "foot": "Football", "basket": "Basket"}.get(sport_key, "")
    circuit = um.get("circuit") or summ.get("circuit") or ""
    comp = _cap(um.get("comp") or summ.get("comp") or r.get("tour") or "")
    parts = [p for p in (sport_name, circuit if _is_tennis else "", comp) if p]
    comp_only = " · ".join(e(p) for p in parts)
    # Heure de début : Unibet frais (path/start) si dispo, sinon l'heure conviviale `top` -> HH:MM.
    sdt = match_select._start_dt(um["start"]) if um.get("start") else None
    starthm = fmt_local(sdt, with_date=False) if sdt else ""
    if not starthm:
        _mt = re.search(r"\d{1,2}:\d{2}", top or "")
        starthm = _mt.group(0) if _mt else (top or "")
    score_txt = e(str(r.get("score"))) if r.get("score") else ""
    if is_live:                                          # live : score actuel
        badge = f'<span class="mc-badge mc-live">🟢 {score_txt or "LIVE"}</span>'
    elif is_finished:                                    # terminé : score FINAL, SANS drapeau 🏁
        badge = (f'<span class="mc-badge mc-done">{score_txt}</span>' if score_txt
                 else '<span class="mc-badge mc-wait">⏳ En attente</span>')
    else:                                                # à venir : HEURE DE DÉBUT seule (HH:MM)
        badge = f'<span class="mc-badge mc-up">{e(starthm) or "À venir"}</span>'
    # L3 : prono(s) PUBLIABLE(s) seulement — APP = TELEGRAM (strict). Un match SANS combiné n'affiche
    # QUE son simple RETENU (⭐, quand « play ») ; sinon abstention -> « pas de pari conseillé ». Les
    # matchs à combiné gardent [simple retenu ?, combiné] (déjà filtré par card_summary). Résultat :
    # ce qui s'affiche dans l'app = ce qui est posté sur Telegram = ce qui est compté dans les stats.
    reco_i = summ.get("reco_idx")          # pari RETENU par le moteur -> ⭐ EN TÊTE (à la place du •)
    is_combo = summ.get("is_combo")        # combiné = • comme les autres paris (ni ⭐ ni 🎲, demande user)
    bets3 = summ.get("bets") or []
    if not is_combo:                       # hors combiné : on ne montre QUE le simple retenu (s'il y en a)
        if summ.get("play") and reco_i is not None and 0 <= reco_i < len(bets3):
            bets3 = [bets3[reco_i]]
            reco_i = 0
        else:
            bets3 = []                     # aucun pari retenu -> abstention assumée
    rows3 = []
    for i, b in enumerate(bets3):
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
                     f'<span class="mc-bt">{e(b.get("sel", ""))}</span>{cote_html}</div>')
    # Abstention (aucun prono publiable) : libellé discret à venir ; rien sur les terminés (le score suffit).
    line3 = ("".join(rows3) if rows3 else
             ('' if is_finished else
              '<div class="mc-betl mc-noplay"><span class="mc-bi">·</span>'
              '<span class="mc-bt">Analysé · pas de pari conseillé</span></div>'))
    teams = (f'{hf}{e(_noF(r.get("home")))} <span class="dim">vs</span> '
             f'{e(_noF(r.get("away")))}{fem}{af}')
    head = (f'<div class="mc-head"><div class="mc-main">'
            f'<div class="mc-line"><span class="mc-ic">{r.get("icon", "")}</span>'
            f'<span class="mc-comp">{comp_only}</span>{badge}</div>'
            f'<div class="mc-teams">{teams}</div>'
            f'<div class="mc-sub">{line3}</div></div>'
            f'<span class="mc-chev">▸</span></div>')
    # ---- CORPS (déplié au tap) : scoreboard + barres % + paris + liens + ANALYSE (chargée d'office
    # à l'ouverture, plus de bouton « Voir l'analyse »). Un clic n'importe où dans la carte la replie. ----
    pkp = f'&pk={r["pick_kind"]}' if r.get("pick_kind") else ""   # type de pari -> analyse cohérente
    ana = ""
    if url.startswith(("/foot/match/", "/basket/match/", "/app/match/")):
        sep = "&" if "?" in url else "?"
        ana = f'<div class="mc-ana" data-ana="{url}{sep}frag=1{pkp}"><div class="exp"></div></div>'
    body = (f'{lscore}{"" if is_live else (r.get("sub", "") + probviz)}'
            f'{bets_sep}{betshtml}{linkshtml}{ana}')
    # TOUTES les cartes sont REPLIÉES au 1er chargement (y compris les directs) — le score live reste
    # visible dans le badge ; on déplie au tap. Fond « pick » uniforme pour toutes les cartes.
    return (f'<div class="row pick mc">{head}'
            f'<div class="mc-body" hidden>{body}</div></div>')

def _rows_by_day(rows: list) -> str:
    """Rend les lignes avec un petit en-tête de jour (Aujourd'hui / Demain / Sam. …) à chaque
    changement de date. Les lignes doivent être triées par heure de début."""
    today = (to_local(datetime.now(timezone.utc)) or datetime.now()).date()
    out, cur = [], object()
    for r in rows:
        ts = r.get("start_ts")
        ld = to_local(datetime.fromtimestamp(ts, tz=timezone.utc)) if ts else None
        d = ld.date() if ld else None
        if d != cur:
            cur = d
            if d is not None:
                out.append(f'<div class="dayhdr">{html.escape(day_label(d, today))}</div>')
        out.append(_sport_row(r))
    return "".join(out)

def render_sport_matches(sport: str, title: str, value: list, live: list,
                         upcoming: list, finished: list, intro: str = "",
                         paused: bool = False, frag: bool = False,
                         confidences: list | None = None) -> str:
    """Page Matchs UNIFIÉE pour tous les sports, sections REPLIABLES dans l'ordre logique :
    Confiances → Valeurs → En direct → À venir → Terminés (Terminés replié d'office).

    `paused` : SofaScore en pause anti-403 -> on l'explique au lieu d'afficher
    « aucun match ». `frag=True` -> renvoie le corps seul (chargé en AJAX dans la SPA)."""
    # Terminés : les PLUS RÉCENTS en HAUT (coup d'envoi le plus récent d'abord).
    finished = sorted(finished or [], key=lambda r: r.get("start_ts") or 0, reverse=True)
    out = []
    # Info (bouton « i ») PROPRE à chaque section, comme sur l'accueil.
    conf_info = ('Pour chaque match, BETSFIX analyse <b>tous les paris Unibet</b> et garde la '
                 '<b>perle la plus probable</b> : le pari le plus <b>sûr</b> à une cote qui paie. '
                 'C\'est le 🛡️ <b>CONFIANCE</b> (vert). ' + BARS_LEGEND)
    val_info = ('La <b>perle au plus gros edge</b> : la cote où Unibet est le plus <b>trop '
                'généreux</b> vs BETSFIX. Ça <b>gagne moins souvent mais rapporte plus</b> '
                '(badge 💎 <b>VALUE +X%</b>), rentable sur la durée. ' + BARS_LEGEND)
    # (heading, rows, ouvert d'office ?, regrouper par jour ?, info) — « Terminés » plié par défaut ;
    # « À venir » regroupé par jour (Aujourd'hui / Demain / …) pour se repérer dans la liste.
    sections = [("🔥 Confiances", confidences or [], True, False, conf_info),
                ("💎 Valeurs", value, True, False, val_info),
                ("🟢 En direct", live, True, False, intro or None),
                ("📅 À venir", upcoming, True, True, None),
                ("✅ Terminés", finished, False, False, None)]
    for heading, rows, open_, by_day, info in sections:
        if not rows:
            continue
        content = _rows_by_day(rows) if by_day else "".join(_sport_row(r) for r in rows)
        out.append(_section(f'{heading} ({len(rows)})', content, open_=open_, info=info))

    if not (value or live or upcoming or finished):
        if intro:
            out.append(f'<div class="banner">{intro}</div>')
        if paused:
            out.append('<div class="banner warn">⏸️ Source SofaScore momentanément en pause '
                       '(trop de requêtes) — les matchs reviennent <b>automatiquement</b> '
                       'd\'ici quelques minutes. Rien à faire.</div>')
        else:
            out.append('<div class="paj-empty">Aucun match à afficher pour le moment.</div>')
    # Ordre PREMIUM : titre -> cadre de perf (graphe + fiabilité & calibration INTÉGRÉS) -> matchs.
    body = _subnav(sport) + render_sport_perf(sport) + "".join(out)
    return body if frag else spa_shell(sport, title, body)

def render_directs(sections: list, frag: bool = False) -> str:
    """Onglet « Directs » : tous les matchs EN DIRECT regroupés par sport (ils restent aussi
    dans leur onglet respectif). `sections` = [(libellé, icône, cartes _sport_row), ...]."""
    out, total = [], 0
    for label, icon, cards in sections:
        if not cards:
            continue
        total += len(cards)
        cards = sorted(cards, key=lambda c: c.get("start_ts") or 0)
        out.append(_section(f'{icon} {html.escape(label)} ({len(cards)})',
                            "".join(_sport_row(c) for c in cards), open_=True))
    if not total:
        out.append(
            '<div class="live-empty">'
            '<div class="le-orb"><span class="le-ping"></span><span class="le-ping le-ping2"></span>'
            '<span class="le-dot"></span></div>'
            '<div class="le-h">Aucun match en direct</div>'
            '<div class="le-sub">Les scores en temps réel — set par set, quart-temps — '
            's\'affichent ici dès qu\'une rencontre analysée démarre.</div>'
            '<div class="le-cta">'
            '<a class="le-btn le-btn-p" href="/">📅 Voir les matchs à venir</a>'
            '</div></div>')
    body = "".join(out)
    return body if frag else spa_shell("directs", "Live", body)

def perf_toggle(active: str) -> str:
    """Bascule de sport sur la page Perf (suivis séparés)."""
    tabs = [("tennis", "🎾 Tennis"), ("basket", "🏀 Basket"), ("foot", "⚽ Foot")]
    return ('<div class="subnav" style="margin-top:0">' + "".join(
        f'<a class="{"on" if active == k else ""}" '
        f'href="/tracking/dashboard?sport={k}">{html.escape(lbl)}</a>'
        for k, lbl in tabs) + "</div>")

_FORM_COLOR = {"W": "#34d27b", "D": "#e0b341", "L": "#ff6b6b",
               "В": "#34d27b", "Н": "#e0b341", "П": "#ff6b6b"}  # W/D/L (en/ru selon locale)

def form_dots(form, n: int = 5) -> str:
    """Pastilles colorées des derniers résultats (V/N/D), lettre en MAJUSCULE. form = ['W','D','L',…].
    `n` = nb max de pastilles (les N dernières -> le plus récent à DROITE)."""
    if not form:
        return ""
    dots = "".join(
        f'<span class="fd" style="background:{_FORM_COLOR.get(str(x).upper()[:1], "#5a6472")}">'
        f'{html.escape(str(x)[:1].upper())}</span>'   # W / L / N en MAJUSCULE
        for x in form[-n:])
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

def _tennis_market_category(label: str) -> tuple[str, int]:
    s = (label or "").lower()
    for name, rank, rx in _TENNIS_GROUPS:
        if rx.search(s):
            return name, rank
    return "Match", 0   # cotes du match, pari de set, handicap du jeu, total de jeux… = niveau match

def _market_category(label: str, mtype: str, sport: str | None = None) -> tuple[str, int]:
    if (sport or "").lower() in ("tennis", "atp", "wta"):
        return _tennis_market_category(label)
    s = f'{label or ""} {mtype or ""}'.lower()
    for name, rank, keys in _MKT_CATS:
        if any(k in s for k in keys):
            return name, rank
    return "Autres marchés", 99

def _oc_label(o) -> str:
    """Libellé d'un choix « comme Unibet » : nom du participant si dispo (sinon « Nul » pour X),
    avec la ligne -> handicap signé (+0.5 / -0.5), total juste le seuil (Plus de 27.5)."""
    raw = (o.label or "").strip()
    name = o.participant or ("Nul" if raw.upper() == "X" else raw)
    if o.line is not None:
        if o.participant:                       # handicap rattaché à un camp -> signe explicite
            name = f"{name} {'+' if o.line > 0 else ''}{o.line:g}"
        else:                                   # total (Plus de / Moins de N) -> juste le seuil
            name = f"{name} {o.line:g}"
    return name.strip()

def render_unibet_markets(markets, title: str = "💰 Tous les paris Unibet",
                          sport: str | None = None, result_only: bool = False) -> str:
    """Tous les marchés Unibet, REGROUPÉS par catégorie (comme l'app Unibet) en sections
    repliables : un gros match a 500+ marchés -> on affiche les catégories + leur nombre,
    et on déplie pour voir les cotes. Cap par catégorie pour garder un poids raisonnable."""
    e = html.escape
    # 1) FUSION « comme Unibet » : tous les betOffers d'un même marché (criterion.label) sont
    #    regroupés en UN SEUL bloc rassemblant toutes leurs lignes (ex. « Handicap » = 1 marché
    #    avec ses 48 lignes, et non 48 marchés). Le compte par catégorie colle alors à Unibet.
    merged: dict = {}
    order: list = []
    main_keys: set = set()
    for m in (markets or []):
        outs = [o for o in (m.outcomes or []) if o.odds]
        if not outs:
            continue
        key = (m.label or m.type or "Marché").strip()
        if key not in merged:
            merged[key] = []
            order.append(key)
        merged[key].extend(outs)
        if m.main:
            main_keys.add(key)   # marché principal -> remontera en tête de sa catégorie
    cats: dict = {}
    for idx, key in enumerate(order):
        outs = merged[key]
        name, rank = _market_category(key, "", sport)
        cells = []
        for o in outs[:30]:
            cells.append(f'<span class="oc"><span class="ocn">{e(_oc_label(o))}</span>'
                         f'<span class="ocv">{o.odds}</span></span>')
        if len(outs) > 30:
            cells.append(f'<span class="oc dim"><span class="ocn">+{len(outs)-30} lignes</span></span>')
        block = (f'<div class="mkt"><div class="mkt-l">{e(key)}</div>'
                 f'<div class="oddsrow oddsrow-wrap">{"".join(cells)}</div></div>')
        # tri intra-catégorie : marché principal d'abord, puis ordre d'apparition Unibet
        sort_key = (0 if key in main_keys else 1, idx)
        cats.setdefault((rank, name), []).append((sort_key, block))
    if not cats:
        return ""
    if result_only:
        # On garde TOUS les marchés en mémoire (pour l'analyse/la value), mais on n'AFFICHE que
        # le « résultat du match » (rang 0) : titre du pari + source Unibet, sans repli.
        res = {k: v for k, v in cats.items() if k[0] == 0}
        if not res:
            return ""
        blocks = sorted(res.items())[0][1]
        block_html = "".join(b for _, b in sorted(blocks, key=lambda x: x[0]))
        return f'<h2>💰 Cote Unibet</h2>{block_html}'
    total = sum(len(v) for v in cats.values())
    out = [f'<h2>{title} <span class="dim">({total})</span></h2>']
    for (rank, name), blocks in sorted(cats.items()):
        ordered = [b for _, b in sorted(blocks, key=lambda x: x[0])]
        shown = ordered[:40]
        more = (f'<div class="dim" style="padding:4px 2px">+{len(ordered)-40} autres marchés '
                "sur Unibet</div>") if len(ordered) > 40 else ""
        op = " open" if rank == 0 else ""   # « Résultat du match » ouvert d'office
        out.append(f'<details class="mktcat"{op}><summary>{e(name)} '
                   f'<span class="mktcat-n">{len(blocks)}</span></summary>'
                   f'<div class="mktcat-b">{"".join(shown)}{more}</div></details>')
    return "".join(out)

def recommended_bets(value=None, confidence=None) -> str:
    """Section « 🎯 Paris conseillés » : la value (cote sous-évaluée) et/ou la confiance
    (favori net du modèle), ou « aucun pari safe » si rien. `value`=(libellé,cote,edge) ;
    `confidence`=(libellé,proba,cote|None)."""
    e = html.escape
    cards = []
    if value:
        lbl, od, edge = value
        cards.append('<div class="banner"><b class="pos">💎 Value</b> — '
                     f'pari sur <b>{e(str(lbl))}</b> @{od}. <span class="dim">Unibet lui donne '
                     f'<b>trop peu de chances</b>, donc sa cote est <b>un peu trop généreuse</b> '
                     f'(~+{round((edge or 0)*100,1)}% en notre faveur). Ça gagne <b>moins souvent '
                     "mais rapporte plus</b> : rentable sur la durée, jamais garanti sur un seul "
                     "match.</span></div>")
    if confidence:
        lbl, prob, od = confidence
        cards.append('<div class="banner"><b style="color:#6cbcff">🔥 Confiance</b> — '
                     f'<b>{e(str(lbl))}</b> est <b>grand favori</b> selon nous : '
                     f'<b>{round((prob or 0)*100)}%</b> de chances de gagner'
                     f'{f" @{od}" if od else ""}. <span class="dim">Le pari le plus <b>sûr</b>, '
                     "mais comme c'est le favori la cote est petite : <b>petit gain</b>.</span></div>")
    if not cards:
        cards.append('<div class="banner">Aucun pari intéressant ici : ni <b>grand favori</b> '
                     "(≥ 65 % de chances), ni <b>cote trop généreuse</b>. Mieux vaut "
                     "<b>passer ce match</b>.</div>")
    return '<h2>🎯 Paris conseillés</h2>' + "".join(cards)

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

def _match_row(m: dict) -> str:
    """Ligne standard d'un match (à venir ou en direct). Cliquable -> détail."""
    e = html.escape
    if m["status"] == "inprogress":
        sc = f' <span class="dim">{e(m["score"])}</span>' if m.get("score") else ""
        status = f'<span class="live">🟢 Live</span>{sc}'
    else:
        status = e(m.get("time") or "")
    inner = (
        f'<div class="rowtop"><span>{e(m["tour"].upper())} · {status}</span></div>'
        f'<div class="players">{e(m["home"])} <span class="dim">vs</span> {e(m["away"])}</div>'
        f'<div class="dim">favori modèle : {e(m.get("fav") or "—")} {e(m.get("favp") or "")}'
        f' · confiance {e(m.get("confidence") or "—")}</div>')
    if m.get("clickable", True):
        return f'<a class="row" href="/app/match/{m["id"]}?tour={m["tour"]}">{inner}</a>'
    return f'<div class="row">{inner}</div>'

def render_matches(groups: list[tuple[str, list[dict]]], live: list[dict] | None = None,
                   finished: list[dict] | None = None,
                   value_picks: list[dict] | None = None, fallback: bool = False) -> str:
    """Page Matchs en sections : paris de confiance, en direct, à venir, terminés.

    groups : [(titre_jour, [match])] pour les matchs à venir. live/finished/value_picks :
    listes de dicts (cf. routeur). match_dict à venir/live : id,tour,home,away,time,status,
    fav,favp,confidence,clickable.
    """
    e = html.escape
    live, finished, value_picks = live or [], finished or [], value_picks or []
    out = []
    if fallback:
        out.append('<div class="banner warn">⚠️ SofaScore momentanément indisponible — scores '
                   'affichés via LiveScore (repli). L\'analyse détaillée revient dès que '
                   'SofaScore répond.</div>')

    # 💎 Paris de confiance (value détectées) — tout en haut
    if value_picks:
        out.append(f'<h2>💎 Paris de confiance ({len(value_picks)})</h2>')
        out.append('<div class="banner">Matchs où le modèle voit une <b>value</b> vs Unibet. '
                   'Avis du modèle, à confirmer — un désaccord n\'est pas un pari gagnant.</div>')
        for v in value_picks:
            edge = round((v.get("edge") or 0) * 100, 1)
            badge = f'<span class="badge b-val">VALUE +{edge} pts</span>'
            inner = (
                f'<div class="rowtop"><span>{e(v["tour"].upper())} · {e(v.get("time") or "")}</span>'
                f'{badge}</div>'
                f'<div class="players">{e(v["home"])} <span class="dim">vs</span> {e(v["away"])}</div>'
                f'<div class="dim">pari : <b class="pos">{e(v.get("player") or "")}</b> '
                f'@{v.get("odds") or "—"} · mise {v.get("stake") if v.get("stake") is not None else "—"}% '
                f'· confiance {e(v.get("confidence") or "—")}</div>')
            out.append(f'<a class="row pick" href="/app/match/{v["id"]}?tour={v["tour"]}">{inner}</a>')

    # 🟢 En direct
    if live:
        out.append(f'<h2>🟢 En direct ({len(live)})</h2>')
        out.extend(_match_row(m) for m in live)

    # À venir (groupés par jour)
    total_up = sum(len(ms) for _, ms in groups)
    if total_up:
        out.append('<div class="banner">Touchez un match pour son analyse détaillée '
                   '(favori, stats, cotes). Heures en fuseau belge.</div>')
        for title, ms in groups:
            if not ms:
                continue
            out.append(f"<h2>{e(title)} ({len(ms)})</h2>")
            out.extend(_match_row(m) for m in ms)
    elif not live and not value_picks:
        out.append('<div class="dim">Aucun match à venir pour le moment.</div>')

    # ✅ Récemment terminés (vs favori du modèle)
    if finished:
        out.append(f'<h2>✅ Récemment terminés ({len(finished)})</h2>')
        for f in finished:
            mark = ('<span class="pos">✓ modèle ok</span>' if f.get("ok")
                    else '<span class="neg">✗ raté</span>')
            inner = (
                f'<div class="rowtop"><span>{e(f["tour"].upper())} · terminé</span>{mark}</div>'
                f'<div class="players">{e(f["home"])} <span class="dim">vs</span> {e(f["away"])}</div>'
                f'<div class="dim">favori modèle : {e(f.get("fav") or "—")} {e(f.get("favp") or "")} '
                f'· vainqueur : <b>{e(f.get("winner_name") or "")}</b></div>')
            out.append(f'<a class="row" href="/app/match/{f["id"]}?tour={f["tour"]}">{inner}</a>')

    return layout("Matchs", "tennis", "".join(out), subnav="matchs", refresh=True)

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
