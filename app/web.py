"""Plateforme de visionnage (front-end HTML rendu côté serveur).

Pages mobiles cohérentes au-dessus de l'API : accueil, liste des matchs,
détail/analyse d'un match. Thème sombre, nav commune. Aucun JS requis.
"""

from __future__ import annotations

import html
import os
import time
from datetime import datetime

_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "static", "logo.png")

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Brussels")
except Exception:  # pragma: no cover
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
    --bg:#080a0f;--bg2:#0c0f16;--surface:#13161f;--surface2:#1a1e2a;
    --border:#252a37;--border2:#2f3545;--text:#eef1f7;--muted:#9099a8;--dim:#646c7c;
    --accent:#2ee27f;--accent2:#19c46a;--accent-ink:#04130a;--glow:rgba(46,226,127,.30);
    --gold:#f6c54a;--gold-bg:#231d09;--gold-bd:#4a3c0c;
    --red:#f25d6e;--green:#34d27b;--brand:#2e9bff;
    --radius:16px;--shadow:0 6px 22px rgba(0,0,0,.40);--shadow-sm:0 2px 8px rgba(0,0,0,.30);
  }
  /* Identité couleur par sport : home bleu · tennis jaune · basket orange · foot vert */
  body.sp-home{--accent:#2e9bff;--accent2:#1f80e6;--accent-ink:#02122b;--glow:rgba(46,155,255,.32)}
  body.sp-tennis{--accent:#d7e64a;--accent2:#aac72f;--accent-ink:#16180a;--glow:rgba(190,210,60,.30)}
  body.sp-basket{--accent:#ff9f43;--accent2:#f08000;--accent-ink:#1a0e00;--glow:rgba(240,128,0,.30)}
  body.sp-foot{--accent:#2ee27f;--accent2:#19c46a;--accent-ink:#04130a;--glow:rgba(46,226,127,.30)}
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;color:var(--text);font-size:15px;line-height:1.45;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
       -webkit-user-select:none;user-select:none;-webkit-touch-callout:none;
       -webkit-tap-highlight-color:transparent;touch-action:manipulation;
       background:var(--bg);}
  /* Thème premium UNIQUE : halo bleu via un calque FIXE collé au viewport (et non au
     body) -> identique sur tous les onglets, quelle que soit la hauteur de la page.
     (évite le bug iOS où background-attachment:fixed est ignoré.) */
  body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
       background:
         radial-gradient(1100px 620px at 50% -4%,rgba(46,155,255,.12),transparent 60%),
         radial-gradient(820px 520px at 100% 102%,rgba(46,155,255,.05),transparent 60%);}
  a{color:inherit;text-decoration:none;-webkit-tap-highlight-color:transparent}
  .wrap{max-width:720px;margin:0 auto;
        padding:calc(8px + env(safe-area-inset-top)) 16px calc(86px + env(safe-area-inset-bottom))}
  /* Logo unique centré tout en haut de chaque page + pastille de pause */
  .toplogo{display:block;text-align:center;margin:0 0 16px}
  .toplogo img{height:80px;width:auto;filter:drop-shadow(0 5px 18px rgba(46,155,255,.40))}
  .pausewrap{text-align:right;margin:-10px 0 8px}
  .pausebadge{display:inline-flex;align-items:center;gap:4px;font-size:9.5px;font-weight:600;
              color:var(--dim);background:transparent;border:1px solid var(--border2);
              padding:2px 8px;border-radius:20px;opacity:.8}
  /* Barre d'onglets fixée en bas (style app native) */
  .botnav{position:fixed;left:0;right:0;bottom:0;z-index:60;display:flex;gap:4px;
          padding:7px 10px calc(7px + env(safe-area-inset-bottom));max-width:720px;margin:0 auto;
          background:linear-gradient(0deg,rgba(10,12,17,.97),rgba(10,12,17,.86));
          backdrop-filter:saturate(160%) blur(16px);-webkit-backdrop-filter:saturate(160%) blur(16px);
          border-top:1px solid var(--border)}
  .botnav a{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;
            padding:6px 0 4px;border-radius:14px;color:var(--muted);font-size:10px;
            font-weight:700;transition:.15s}
  .botnav a .ic{font-size:24px;line-height:1}
  .botnav a:active{transform:scale(.93)}
  .botnav a.on{color:var(--accent-ink);background:linear-gradient(180deg,var(--accent),var(--accent2))}
  .botnav a.on .ic{transform:scale(1.06)}
  /* SPA : panneaux par onglet (tout chargé à l'ouverture, bascule sans rechargement) */
  .panel{display:none}
  .panel.on{display:block;animation:fadein .18s ease}
  @keyframes fadein{from{opacity:.4}to{opacity:1}}
  .ldg{color:var(--dim);text-align:center;padding:40px 0;font-size:13px}
  .ldg::before{content:"";display:block;width:22px;height:22px;margin:0 auto 12px;border-radius:50%;
    border:2px solid var(--border2);border-top-color:var(--accent2);animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  /* Header sticky premium */
  .hdr{position:sticky;top:0;z-index:50;
       background:linear-gradient(180deg,rgba(12,15,22,.92),rgba(12,15,22,.78));
       backdrop-filter:saturate(160%) blur(14px);-webkit-backdrop-filter:saturate(160%) blur(14px);
       border-bottom:1px solid var(--border)}
  .hdr-in{max-width:720px;margin:0 auto;padding:12px 16px 10px}
  .brand{display:flex;align-items:center;gap:6px;font-size:20px;font-weight:800;
         letter-spacing:-.02em}
  .brand .logo{font-size:22px;filter:drop-shadow(0 2px 7px rgba(46,155,255,.5))}
  .brand img.logo{height:30px;width:auto;display:block}
  .brand img.wm{height:21px;width:auto;display:block;margin-left:-1px}
  .hero{text-align:center;padding:18px 0 6px}
  .hero-logo{max-width:230px;width:62%;height:auto;
             filter:drop-shadow(0 6px 22px rgba(46,155,255,.35))}
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
  .row{display:block;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border-radius:var(--radius);padding:14px 15px;margin:10px 0;border:1px solid var(--border);
       box-shadow:var(--shadow-sm);transition:.16s}
  .row:active{transform:scale(.99);border-color:var(--border2)}
  .row.pick{border-color:rgba(46,226,127,.45);
            background:linear-gradient(180deg,rgba(46,226,127,.10),rgba(46,226,127,.03));
            box-shadow:0 4px 18px rgba(46,226,127,.14)}
  .live{color:var(--red);font-weight:800;letter-spacing:.02em}
  .fem{color:#ff7ab8;font-weight:800}
  .rowtop{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11px;
          color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
  .rowtop > span:first-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* en-tête match : compétition tronquable + date toujours visible */
  .rt-l{display:flex;align-items:center;min-width:0;flex:1;overflow:hidden}
  .rt-comp{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
  .rt-when{white-space:nowrap;flex:none}
  .players{font-size:16px;font-weight:700;margin:7px 0 3px;letter-spacing:-.01em}
  /* Ligne du pari : nom+cote à gauche, badge value à droite (toujours sur une ligne) */
  .betline{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:7px 0 3px}
  .betline .bn{font-size:16px;font-weight:700;letter-spacing:-.01em;min-width:0;
               overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* affiche (équipes) + badge à droite, badge aligné en haut, le matchup peut wraper */
  .mrow{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
  .mrow .players{flex:1;min-width:0}
  .bdg{flex:none}
  .bdg .badge{white-space:nowrap}
  .badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:800;
         letter-spacing:.02em}
  .b-val{background:rgba(46,226,127,.14);color:var(--accent);border:1px solid rgba(46,226,127,.25)}
  .b-dim{background:var(--surface);color:var(--muted);border:1px solid var(--border)}
  .b-uni{background:rgba(46,155,255,.14);color:#56b0ff;border:1px solid rgba(46,155,255,.30)}
  .b-conf{background:rgba(46,155,255,.16);color:#6cbcff;border:1px solid rgba(46,155,255,.32)}
  details.sec{margin:26px 0 11px}
  details.sec > summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;
    font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
  details.sec > summary::-webkit-details-marker{display:none}
  details.sec > summary::before{content:"";width:3px;height:14px;border-radius:3px;flex:none;
    background:linear-gradient(var(--accent),var(--accent2))}
  details.sec .i{margin-left:auto;width:21px;height:21px;border-radius:50%;flex:none;
    border:1px solid var(--border2);display:inline-flex;align-items:center;justify-content:center;
    font:italic 800 12px Georgia,serif;text-transform:none;color:var(--muted)}
  details.sec[open] .i{color:#fff;border-color:var(--accent2);background:rgba(46,155,255,.16)}
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
  details.sec2 .i{width:22px;height:22px;border-radius:50%;flex:none;border:1px solid var(--border2);
    display:inline-flex;align-items:center;justify-content:center;font:italic 800 12px Georgia,serif;
    text-transform:none;color:var(--muted);cursor:pointer}
  details.sec2 .i:active{transform:scale(.92)}
  details.sec2 .sec-info{margin:8px 0 4px}
  details.sec2 > .secbody{margin-top:4px}
  .b-soon{background:var(--surface);color:var(--muted);border:1px solid var(--border);font-weight:700}
  /* badge décompte (timer avant le coup d'envoi), en haut à droite de la carte.
     Texte BLANC, unités jour/heure/minute bien distinctes. */
  .rt-r{display:inline-flex;align-items:center;gap:6px;margin-left:auto}
  .cd{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:800;
      font-variant-numeric:tabular-nums;letter-spacing:.02em;background:rgba(255,255,255,.10);
      color:#fff;border:1px solid rgba(255,255,255,.20);white-space:nowrap}
  .cd .u{color:rgba(255,255,255,.55);font-weight:700;margin:0 1px 0 1px}
  .cd.soon{background:rgba(224,179,65,.16);color:#ffd061;border-color:rgba(224,179,65,.40)}
  .cd.live{background:rgba(242,93,110,.18);color:#ff7a88;border-color:rgba(242,93,110,.38)}
  .formrow{display:flex;justify-content:space-between;align-items:center;margin-top:7px}
  .fc{display:inline-flex;align-items:center;gap:5px;font-size:11px}
  .forms{display:inline-flex;gap:3px;vertical-align:middle;margin-left:4px}
  .fd{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
      border-radius:4px;font-size:9px;font-weight:800;color:#08110a}
  .pbars{margin-top:10px;display:flex;flex-direction:column;gap:6px}
  .pb-h{font-size:12px;color:var(--text);margin-bottom:2px}
  .pb-row{display:flex;align-items:center;gap:9px;font-size:11px}
  .pb-l{width:84px;flex:none;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;
        font-weight:700;font-size:10px}
  .pb-t{flex:1;height:8px;border-radius:99px;background:var(--surface);overflow:hidden}
  .pb-t > span{display:block;height:100%;border-radius:99px}
  .pb-v{width:36px;flex:none;text-align:right;font-weight:800}
  /* Barres comparatives : couleurs FIXES (identiques tous sports/onglets) ->
     BETSFIX bleu, BOOKMAKER gris, PUBLIC jaune. Ne pas thématiser par sport. */
  .pm{background:linear-gradient(90deg,#1f80e6,#2e9bff)}
  .po{background:#8a93a3}
  .pc{background:#e0b341}
  /* Barre de cotes : une cellule par issue (joueur 1 / Nul / joueur 2) ; favori (cote la
     plus basse) mis en avant en bleu. Nom au-dessus, cote dessous. */
  .oddsrow{display:flex;gap:6px;margin-top:9px}
  .oc{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:1px;
      background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:6px 6px}
  .oc.fav{border-color:var(--accent2);background:rgba(46,155,255,.10)}
  .ocn{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;
       max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .oc.fav .ocn{color:#9fd0ff}
  .ocv{font-size:14.5px;font-weight:800;font-variant-numeric:tabular-nums}
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
  .dots{display:flex;gap:5px;flex-wrap:wrap}
  .dot{width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;
       justify-content:center;font-size:11px;font-weight:800}
  .dot.w{background:var(--green);color:#04130a}
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
  /* Banners — info discret par défaut, ambre seulement pour les vraies alertes (.warn) */
  .banner{background:var(--surface);border:1px solid var(--border);
          border-left:3px solid var(--border2);color:var(--muted);border-radius:12px;
          padding:11px 14px;font-size:12.5px;line-height:1.55;margin:11px 0}
  .banner b{color:var(--text)}
  .banner.warn{background:linear-gradient(180deg,var(--gold-bg),rgba(35,29,9,.45));
          border:1px solid var(--gold-bd);border-left:3px solid var(--gold);color:var(--gold)}
  .banner.warn b{color:#ffd877}
  /* CTA cards */
  .big{display:block;background:linear-gradient(180deg,var(--surface2),var(--surface));
       border-radius:var(--radius);padding:18px 18px;margin:11px 0;border:1px solid var(--border);
       font-size:16px;font-weight:700;box-shadow:var(--shadow);transition:.16s}
  .big:active{transform:scale(.99)}
  .big .d{font-size:12.5px;color:var(--muted);font-weight:400;margin-top:5px;line-height:1.5}
  .foot{color:var(--dim);font-size:10.5px;margin-top:22px;text-align:center;line-height:1.6}
  .src{font-size:12px;font-weight:600;padding:9px 13px;border-radius:12px;margin:4px 0 2px;
       border:1px solid var(--border)}
  .src.ok{background:rgba(46,226,127,.10);color:var(--accent);border-color:rgba(46,226,127,.22)}
  .src.ko{background:var(--gold-bg);color:var(--gold);border-color:var(--gold-bd)}
"""


# Menu principal groupé par SPORT ; chaque sport a son sous-menu (Matchs / Fiabilité).
_SPORT_MATCH_URL = {"tennis": "/app", "basket": "/basket", "foot": "/foot"}

# Onglets de la SPA (clé, URL, icône, libellé). L'URL sert AUSSI de source AJAX (?frag=1).
_SPA_TABS = [("home", "/", "🏠", "Accueil"), ("tennis", "/app", "🎾", "Tennis"),
             ("basket", "/basket", "🏀", "Basket"), ("foot", "/foot", "⚽", "Foot")]


def _subnav(sport: str) -> str:
    """Sous-menu d'un sport (Matchs / Fiabilité), inclus dans le corps du fragment."""
    if sport not in _SPORT_MATCH_URL:
        return ""
    items = [("matchs", _SPORT_MATCH_URL[sport], "📋 Matchs"),
             ("perf", f"/tracking/dashboard?sport={sport}", "📊 Fiabilité")]
    return '<div class="subnav">' + "".join(
        f'<a class="{"on" if k == "matchs" else ""}" href="{href}">{html.escape(lbl)}</a>'
        for k, href, lbl in items) + "</div>"


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
_SPA_JS = (
    "(function(){var P=document.getElementById('panels');if(!P)return;"
    "function panel(t){return document.getElementById('pn-'+t);}"
    "function show(t){var c=P.children,i;for(i=0;i<c.length;i++)"
    "c[i].classList.toggle('on',c[i].getAttribute('data-tab')===t);"
    "var n=document.querySelectorAll('.botnav a'),j;for(j=0;j<n.length;j++)"
    "n[j].classList.toggle('on',n[j].getAttribute('data-tab')===t);"
    "document.body.className='sp-'+t;}"
    "function load(p){if(!p||p.getAttribute('data-loaded'))return;"
    "p.setAttribute('data-loaded','1');var u=p.getAttribute('data-src');"
    "fetch(u+(u.indexOf('?')<0?'?':'&')+'frag=1',{headers:{'X-Frag':'1'}})"
    ".then(function(r){return r.text();}).then(function(h){p.innerHTML=h;})"
    ".catch(function(){p.removeAttribute('data-loaded');"
    "p.innerHTML='<div class=ldg>Erreur de chargement. Touchez l\\'onglet pour réessayer.</div>';});}"
    "function go(t,push){var p=panel(t);if(!p)return;load(p);show(t);"
    "if(push)try{history.pushState({tab:t},'',p.getAttribute('data-src'));}catch(e){}"
    "window.scrollTo(0,0);}"
    # panneau actif (rendu serveur) = déjà chargé ; on précharge les autres tout de suite
    "var c=P.children,i;for(i=0;i<c.length;i++){"
    "if(c[i].classList.contains('on'))c[i].setAttribute('data-loaded','1');else load(c[i]);}"
    "var nav=document.querySelectorAll('.botnav a');for(i=0;i<nav.length;i++){"
    "nav[i].addEventListener('click',function(e){e.preventDefault();"
    "go(this.getAttribute('data-tab'),true);});}"
    "window.addEventListener('popstate',function(e){var t=(e.state&&e.state.tab);"
    "if(!t){var m={'/':'home','/app':'tennis','/basket':'basket','/foot':'foot'};"
    "t=m[location.pathname]||'home';}go(t,false);});"
    # le « i » déplie/replie l'explication sans toucher au pliage de la section
    "document.addEventListener('click',function(e){var b=e.target.closest('[data-info]');"
    "if(!b)return;e.preventDefault();e.stopPropagation();"
    "var d=b.closest('details.sec2'),inf=d&&d.querySelector('.sec-info');"
    "if(inf)inf.hidden=!inf.hidden;});})();"
)


def layout(title: str, sport: str, body: str, subnav: str | None = None,
           refresh: bool = False, source: dict | None = None) -> str:
    """Page premium. `sport` ∈ home/tennis/basket/foot (onglet principal actif).
    `subnav` ∈ matchs/perf : affiche le sous-menu du sport (Matchs / Fiabilité).
    `source` : état SofaScore -> petit indicateur discret dans l'en-tête si en pause."""
    e = html.escape
    # Logo unique : réduit, centré, tout en haut de CHAQUE page (accueil + sports).
    toplogo = ('<a class="toplogo" href="/"><img src="/static/logo.png?v=2" alt="BETSFIX"></a>'
               if os.path.exists(_LOGO) else "")
    pausebar = ""
    if source and not source.get("ok"):
        s = source.get("paused_seconds", 0)
        pausebar = (f'<div class="pausewrap"><span class="pausebadge" '
                    f'title="SofaScore en pause ({s}s) — LiveScore prend le relais">'
                    f'⏸ Source en pause</span></div>')
    nav_items = [("home", "/", "🏠", "Accueil"), ("tennis", "/app", "🎾", "Tennis"),
                 ("basket", "/basket", "🏀", "Basket"), ("foot", "/foot", "⚽", "Foot")]
    # Barre d'onglets fixée en BAS (style app native) : icône + petit label.
    botnav = '<nav class="botnav">' + "".join(
        f'<a class="{"on" if sport == k else ""}" href="{href}" aria-label="{e(name)}">'
        f'<span class="ic">{ico}</span><span class="lb">{e(name)}</span></a>'
        for k, href, ico, name in nav_items) + "</nav>"

    sub = ""
    if subnav and sport in _SPORT_MATCH_URL:
        items = [("matchs", _SPORT_MATCH_URL[sport], "📋 Matchs"),
                 ("perf", f"/tracking/dashboard?sport={sport}", "📊 Fiabilité")]
        sub = '<div class="subnav">' + "".join(
            f'<a class="{"on" if subnav == k else ""}" href="{href}">{e(lbl)}</a>'
            for k, href, lbl in items) + "</div>"

    meta_refresh = '<meta http-equiv="refresh" content="180">' if refresh else ""
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#080a0f">
{meta_refresh}<title>{e(title)} · BETSFIX</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/icon-180.png?v=2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BETSFIX">
<style>{CSS}</style></head><body class="sp-{e(sport)}">
<div class="wrap">{toplogo}{pausebar}{sub}{body}
<div class="foot">18+ · Outil informatif, sans garantie · Jouez responsable</div>
</div>{botnav}<script>{_COUNTDOWN_JS}</script></body></html>"""


def spa_shell(active: str, title: str, body: str, source: dict | None = None) -> str:
    """Coquille « single-page » des 4 onglets principaux. Le sport `active` est rendu côté
    serveur (1er affichage rapide, marche sans JS) ; les 3 autres panneaux sont vides et
    remplis en AJAX dès l'ouverture. La nav du bas bascule les panneaux SANS rechargement."""
    e = html.escape
    toplogo = ('<a class="toplogo" href="/"><img src="/static/logo.png?v=2" alt="BETSFIX"></a>'
               if os.path.exists(_LOGO) else "")
    pausebar = ""
    if source and not source.get("ok"):
        s = source.get("paused_seconds", 0)
        pausebar = (f'<div class="pausewrap"><span class="pausebadge" '
                    f'title="SofaScore en pause ({s}s) — LiveScore prend le relais">'
                    f'⏸ Source en pause</span></div>')
    panels = []
    for k, href, _ico, _name in _SPA_TABS:
        on = " on" if k == active else ""
        inner = body if k == active else '<div class="ldg">Chargement…</div>'
        panels.append(f'<section class="panel{on}" id="pn-{k}" data-tab="{k}" '
                      f'data-src="{href}">{inner}</section>')
    botnav = '<nav class="botnav">' + "".join(
        f'<a class="{"on" if active == k else ""}" data-tab="{k}" href="{href}" aria-label="{e(name)}">'
        f'<span class="ic">{ico}</span><span class="lb">{e(name)}</span></a>'
        for k, href, ico, name in _SPA_TABS) + "</nav>"
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#080a0f">
<title>{e(title)} · BETSFIX</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/static/icon-180.png?v=2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="BETSFIX">
<style>{CSS}</style></head><body class="sp-{e(active)}">
<div class="wrap">{toplogo}{pausebar}<main id="panels">{''.join(panels)}</main>
<div class="foot">18+ · Outil informatif, sans garantie · Jouez responsable</div>
</div>{botnav}<script>{_COUNTDOWN_JS}</script><script>{_SPA_JS}</script></body></html>"""


def _pick_bars(p: dict) -> str:
    """3 barres = proba que LE PARI passe, selon Modèle (l'app) / Officiel (cote) / Communauté.

    Toutes mesurent la même chose (chances du pari) -> comparables. Le vote communauté
    est la part des fans sur ce côté (le reste va à l'adversaire : total 100%)."""
    def bar(label, val, cls):
        if val is None:
            return ""
        pct = round(val * 100)
        return (f'<div class="pb-row"><span class="pb-l">{label}</span>'
                f'<div class="pb-t"><span class="{cls}" style="width:{min(pct,100)}%"></span></div>'
                f'<span class="pb-v">{pct}%</span></div>')
    inner = (bar("BETSFIX", p.get("model_prob"), "pm")
             + bar("Bookmaker", p.get("implied"), "po")
             + bar("Public", p.get("community"), "pc"))
    if not inner:
        return ""
    bet = html.escape(p.get("bet") or "le pari")
    return (f'<div class="pbars"><div class="pb-h">Chances que <b>{bet}</b> gagne '
            f'<span class="dim">— selon :</span></div>{inner}</div>')


def bars_two_way(p_home, imp_home, votes, home, away) -> dict:
    """Champs des 3 barres (BETSFIX/Bookmaker/Public) côté favori — match à 2 issues
    (basket/tennis). `imp_home` = proba implicite dévig du domicile ; `votes` = (% home, % away)."""
    if p_home is None:
        return {}
    home_fav = p_home >= 0.5
    implied = (imp_home if home_fav else 1 - imp_home) if imp_home is not None else None
    community = None
    if votes and votes[0] is not None:
        community = (votes[0] if home_fav else votes[1]) / 100
    return {"model_prob": p_home if home_fav else 1 - p_home,
            "implied": implied, "community": community, "bet": home if home_fav else away}


def bars_foot(probs, imp, votes, home, away) -> dict:
    """Champs des 3 barres côté issue favorite — foot 1X2. `imp` = (p1,pX,p2) dévig."""
    if not probs:
        return {}
    i = max(range(3), key=lambda k: probs[k])
    implied = imp[i] if imp else None
    community = None
    if votes and votes[0] is not None and i in (0, 2):   # pas de vote 'communauté' pour le nul
        community = (votes[0] if i == 0 else votes[1]) / 100
    return {"model_prob": probs[i], "implied": implied, "community": community,
            "bet": [home, "Match nul", away][i]}


def odds_row(outcomes) -> str:
    """Barre de cotes Unibet claire : `outcomes` = [(libellé, cote), ...] — 2 issues
    (tennis/basket) ou 3 avec « Nul » (foot). La cote la plus basse (favori du book) est
    mise en avant en bleu. Chaque cellule : nom au-dessus, cote dessous."""
    valid = [(lbl, o) for lbl, o in outcomes if o]
    if not valid:
        return '<div class="dim">cotes Unibet à venir</div>'
    best = min(o for _, o in valid)
    cells = "".join(
        f'<span class="oc{" fav" if o == best else ""}">'
        f'<span class="ocn">{html.escape(str(lbl))}</span>'
        f'<span class="ocv">{o}</span></span>'
        for lbl, o in valid)
    return f'<div class="oddsrow">{cells}</div>'


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


def _pick_card(p: dict, badge: str) -> str:
    """Carte d'un pari pour l'accueil (value OU confiance), avec les 3 barres."""
    e = html.escape
    odds = f' <span class="dim">@{p.get("odds")}</span>' if p.get("odds") else ""
    cd = (f'<span class="cd" data-ts="{int(p["start_ts"])}"></span>'
          if p.get("start_ts") and p["start_ts"] > time.time() else "")
    fem = ' <span class="fem">(F)</span>' if p.get("female") else ""
    # Coin haut-droit = pastille d'état (même style) : décompte si à venir, « EN DIRECT » si
    # live. Le badge value descend toujours sur la ligne du pari.
    state = cd if cd else ('<span class="cd live">🔴 EN DIRECT</span>' if p.get("live") else "")
    bdg = f'<span class="bdg">{badge}</span>' if badge else ""
    oddsrow = odds_row(p["odds_cells"]) if p.get("odds_cells") else ""
    return (f'<a class="row pick" href="{p["url"]}">'
            f'<div class="rowtop"><span>{p["icon"]} {e(p["sport"])}{fem} · {e(p.get("time") or "")}</span>'
            f'<span class="rt-r">{state}</span></div>'
            f'<div class="betline"><span class="bn">{e(p.get("bet") or "")}{odds}</span>{bdg}</div>'
            f'<div class="dim">{e(p.get("home") or "")} vs {e(p.get("away") or "")}</div>'
            f'{_pick_bars(p)}{oddsrow}</a>')


def render_home(rep: dict, source: dict | None = None,
                picks: list[dict] | None = None,
                conf_picks: list[dict] | None = None, frag: bool = False) -> str:
    # l'état SofaScore (pause) s'affiche désormais discrètement dans l'en-tête (cf. layout).
    picks = picks or []
    conf_picks = conf_picks or []
    bars_legend = ('Les 3 barres = <b>chance que le pari gagne</b> selon <b>BETSFIX</b> (l\'app), '
                   'le <b>Bookmaker</b> (cote Unibet) et le <b>Public</b> (votes SofaScore).')

    # 🔥 CONFIANCES du jour : favori NET du modèle (forte proba) — pas forcément une value
    if conf_picks:
        rows = "".join(_pick_card(p, "") for p in conf_picks)  # pas de badge % (déjà dans la barre)
        conf_html = _section(f'🔥 Confiances du jour ({len(conf_picks)})', rows, open_=True,
                             info='Matchs où <b>BETSFIX</b> voit un <b>favori net</b> (forte proba de '
                                  'gagner). Plus « sûr » mais souvent à <b>petite cote</b> — donc '
                                  'rarement une value. Badge = proba du modèle.')
    else:
        conf_html = _section('🔥 Confiances du jour (0)',
                             '<div class="banner">Aucun favori net à venir pour le moment.</div>')

    # 💎 VALEURS du jour : edge vs cote (le book sous-évalue le pari) — souvent des outsiders
    if picks:
        rows = "".join(_pick_card(
            p, '<span class="badge b-val" title="Avantage estimé sur la cote">'
               f'+{round((p.get("edge") or 0)*100, 1)} pts</span>') for p in picks)
        val_html = _section(f'💎 Valeurs du jour ({len(picks)})', rows, open_=True,
                            info='Paris où <b>BETSFIX</b> estime la cote <b>sous-évaluée</b> (edge). '
                                 'Souvent des outsiders : gros gain potentiel mais ça passe rarement — '
                                 'c\'est du <b>+EV</b>, pas une certitude. Badge <b>+X pts</b> = edge. '
                                 f'{bars_legend} Value = quand BETSFIX &gt; Bookmaker.')
    else:
        val_html = _section('💎 Valeurs du jour (0)',
                            '<div class="banner">Aucune value détectée pour le moment '
                            '(les cotes Unibet apparaissent à l\'approche des matchs).</div>')

    # Confiances AU-DESSUS des valeurs (favori net d'abord, puis les value/outsiders).
    body = f'{conf_html}{val_html}'
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


def _sport_row(r: dict) -> str:
    """Ligne de match unifiée (tous sports). r : tour, status, time, score, home,
    away, prob (float ou 3-tuple), sub, badge, url, pick."""
    e = html.escape
    # Pastille d'état en haut à droite, MÊME style que le décompte : décompte si à venir,
    # « EN DIRECT » (rouge) si live. Le badge value/✓ va, lui, sur la ligne de l'affiche.
    if r.get("status") == "inprogress":
        top = f'<span class="dim">{e(r["score"])}</span>' if r.get("score") else ""
        state = '<span class="cd live">🔴 EN DIRECT</span>'
    elif r.get("status") == "finished":
        top = e(r.get("score") or "terminé")
        state = ""
    else:
        top = e(r.get("time") or "")
        state = (f'<span class="cd" data-ts="{int(r["start_ts"])}"></span>'
                 if r.get("start_ts") and r["start_ts"] > time.time() else "")
    # 3 barres (BETSFIX / Bookmaker / Public) comme sur l'accueil si on a les données,
    # sinon la barre de proba simple (favori + %).
    probviz = _pick_bars(r) if r.get("model_prob") is not None else \
        _prob_bar(r.get("prob"), r.get("prob_labels"))
    fem = ' <span class="fem">(F)</span>' if r.get("female") else ""
    badge = f'<span class="bdg">{r["badge"]}</span>' if r.get("badge") else ""
    # En-tête : la compétition (souvent longue) se tronque, la date/heure (rt-when) reste visible.
    when = f' · {top}' if top else ""
    inner = (f'<div class="rowtop"><span class="rt-l">'
             f'<span class="rt-comp">{e(r.get("tour") or "")}{fem}</span>'
             f'<span class="rt-when">{when}</span></span>'
             f'<span class="rt-r">{state}</span></div>'
             f'<div class="mrow"><div class="players">{e(r.get("home") or "")} '
             f'<span class="dim">vs</span> {e(r.get("away") or "")}</div>{badge}</div>'
             f'{probviz}{r.get("sub", "")}')
    cls = "row pick" if r.get("pick") else "row"
    if r.get("url"):
        return f'<a class="{cls}" href="{r["url"]}">{inner}</a>'
    return f'<div class="{cls}">{inner}</div>'


def render_sport_matches(sport: str, title: str, value: list, live: list,
                         upcoming: list, finished: list, intro: str = "",
                         paused: bool = False, frag: bool = False) -> str:
    """Page Matchs UNIFIÉE pour tous les sports, sections REPLIABLES dans l'ordre logique :
    Valeurs → En direct → À venir → Terminés (Terminés replié d'office).

    `paused` : SofaScore en pause anti-403 -> on l'explique au lieu d'afficher
    « aucun match ». `frag=True` -> renvoie le corps seul (chargé en AJAX dans la SPA)."""
    out = []
    # (heading, rows, ouvert d'office ?) — « Terminés » plié par défaut.
    sections = [("💎 Valeurs du jour", value, True), ("🔴 En direct", live, True),
                ("📅 À venir", upcoming, True), ("✅ Terminés", finished, False)]
    info_done = False
    for heading, rows, open_ in sections:
        if not rows:
            continue
        info = intro if (intro and not info_done) else None
        info_done = info_done or bool(info)
        out.append(_section(f'{heading} ({len(rows)})',
                            "".join(_sport_row(r) for r in rows), open_=open_, info=info))

    if not (value or live or upcoming or finished):
        if intro:
            out.append(f'<div class="banner">{intro}</div>')
        if paused:
            out.append('<div class="banner warn">⏸️ Source SofaScore momentanément en pause '
                       '(trop de requêtes) — les matchs reviennent <b>automatiquement</b> '
                       'd\'ici quelques minutes. Rien à faire.</div>')
        else:
            out.append('<div class="dim">Aucun match à afficher pour le moment.</div>')
    body = _subnav(sport) + "".join(out)
    return body if frag else spa_shell(sport, title, body)


def perf_toggle(active: str) -> str:
    """Bascule de sport sur la page Perf (suivis séparés)."""
    tabs = [("tennis", "🎾 Tennis"), ("basket", "🏀 Basket"), ("foot", "⚽ Foot")]
    return ('<div class="subnav" style="margin-top:0">' + "".join(
        f'<a class="{"on" if active == k else ""}" '
        f'href="/tracking/dashboard?sport={k}">{html.escape(lbl)}</a>'
        for k, lbl in tabs) + "</div>")


_FORM_COLOR = {"W": "#34d27b", "D": "#e0b341", "L": "#f25d6e",
               "В": "#34d27b", "Н": "#e0b341", "П": "#f25d6e"}  # W/D/L (en/ru selon locale)


def form_dots(form) -> str:
    """Pastilles colorées des derniers résultats (V/N/D). form = ['W','D','L',...]."""
    if not form:
        return ""
    dots = "".join(
        f'<span class="fd" style="background:{_FORM_COLOR.get(str(x).upper()[:1], "#5a6472")}">'
        f'{html.escape(str(x)[:1])}</span>'
        for x in form[:5])
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
        status = f'<span class="live">🔴 EN DIRECT</span>{sc}'
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

    # 🔴 En direct
    if live:
        out.append(f'<h2>🔴 En direct ({len(live)})</h2>')
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


def render_match_detail(a, winner_odds: tuple[float | None, float | None],
                        aces: dict | None = None, tour: str = "atp",
                        home_form: list[dict] | None = None,
                        away_form: list[dict] | None = None,
                        h2h: dict | None = None, score: str = "",
                        votes: tuple | None = None) -> str:
    """a = MatchAnalysis ; winner_odds = (cote_home, cote_away) Unibet ;
    aces = récap tendance d'aces ; home_form/away_form = derniers résultats (V/D) ;
    h2h = {'home': n, 'away': n} bilan des confrontations ; score = score en cours."""
    e = html.escape
    hp = a.model_home_probability
    ap = a.model_away_probability
    live = (f' · <span class="live">🔴 {e(score)}</span>'
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
        dots = "".join(f'<span class="dot {"w" if f["win"] else "l"}">'
                       f'{"V" if f["win"] else "D"}</span>' for f in form)
        return (f'<div class="frow"><div class="players" style="font-size:14px;margin:0 0 6px">'
                f'{e(name)}</div><div class="dots">{dots}</div></div>')

    form_html = ""
    if home_form or away_form:
        form_html = ('<h2>Forme récente <span class="dim">(récent → ancien)</span></h2>'
                     f'<div class="row">{_form_block(a.home.name, home_form or [])}'
                     f'{_form_block(a.away.name, away_form or [])}</div>')

    # Face-à-face en BARRE
    h2h_html = ""
    if h2h:
        hh, aw = h2h.get("home") or 0, h2h.get("away") or 0
        if hh + aw > 0:
            ph = round(hh / (hh + aw) * 100)
            h2h_html = (
                f'<h2>Face-à-face</h2><div class="row">'
                f'<div class="pbar-l"><span>{e(a.home.name.split()[-1])} {hh}</span>'
                f'<span>{aw} {e(a.away.name.split()[-1])}</span></div>'
                f'<div class="mbar"><span class="a" style="width:{ph}%"></span>'
                f'<span class="b" style="width:{100-ph}%"></span></div>'
                f'<div class="dim" style="margin-top:6px">{hh + aw} confrontation'
                f'{"s" if hh + aw > 1 else ""}</div></div>')

    probs = ""
    if hp is not None:
        probs = (f'<h2>Probabilités du modèle</h2><div class="row">'
                 f'<div class="pbar-l"><span>{e(a.home.name.split()[-1])} {round(hp*100)}%</span>'
                 f'<span>{round(ap*100)}% {e(a.away.name.split()[-1])}</span></div>'
                 f'<div class="mbar" style="height:10px"><span class="a" style="width:{round(hp*100)}%">'
                 f'</span><span class="b" style="width:{round(ap*100)}%"></span></div></div>')

    # Facteurs en MINI-BARRES (contribution home/away par facteur)
    def _factor_row(f):
        h = round((f.home or 0) * 100)
        return (f'<div class="frow"><div class="ft"><span class="fn">{e(f.name)}</span>'
                f'<span class="fb"><span class="mbar"><span class="a" style="width:{h}%"></span>'
                f'<span class="b" style="width:{100-h}%"></span></span></span>'
                f'<span class="fp">{h}/{100-h}%</span></div>'
                f'<div class="dim" style="font-size:11px;margin-top:4px">{e(f.detail or "")}</div></div>')
    factors = (f'<h2>Facteurs du modèle</h2><div class="row">'
               + "".join(_factor_row(f) for f in a.factors) + '</div>') if a.factors else ""

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

    body = (head + pari_html + verdict + form_html + h2h_html + votes_html + paris_link
            + probs + factors + aces_html + odds_html)
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
