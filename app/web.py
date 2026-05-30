"""Plateforme de visionnage (front-end HTML rendu côté serveur).

Pages mobiles cohérentes au-dessus de l'API : accueil, liste des matchs,
détail/analyse d'un match. Thème sombre, nav commune. Aucun JS requis.
"""

from __future__ import annotations

import html
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Brussels")
except Exception:  # pragma: no cover
    LOCAL_TZ = None


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
    return dt.strftime("%d/%m %H:%M" if with_date else "%H:%M")

CSS = """
  *{box-sizing:border-box}
  body{margin:0;background:#0e0f13;color:#e8eaed;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
  a{color:inherit;text-decoration:none}
  .wrap{max-width:680px;margin:0 auto;padding:14px 16px 40px}
  .top{display:flex;align-items:center;gap:8px;padding:6px 0 2px}
  .top h1{font-size:18px;margin:0}
  .nav{display:flex;gap:8px;margin:10px 0}
  .nav a{flex:1;text-align:center;padding:10px;border-radius:11px;font-size:13px;
         font-weight:600;background:#1a1c22;color:#bdc1c6}
  .nav a.on{background:#1b5e20;color:#fff}
  h2{font-size:15px;margin:20px 0 8px;color:#bdc1c6}
  .grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:12px 0}
  .card{background:#1a1c22;border-radius:14px;padding:12px;text-align:center}
  .lbl{font-size:11px;color:#9aa0a6;text-transform:uppercase;letter-spacing:.4px}
  .val{font-size:22px;font-weight:700;margin:4px 0}
  .sub{font-size:11px;color:#9aa0a6}
  .row{display:block;background:#1a1c22;border-radius:12px;padding:12px 14px;margin:8px 0;
       border:1px solid #23262e}
  .row:active{background:#23262e}
  .rowtop{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#9aa0a6}
  .players{font-size:15px;font-weight:600;margin:6px 0 2px}
  .badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700}
  .b-val{background:#13351c;color:#5ed88a}
  .b-dim{background:#23262e;color:#9aa0a6}
  .bar{height:10px;border-radius:6px;background:#ea4335;overflow:hidden;margin:6px 0}
  .bar > span{display:block;height:100%;background:#34a853}
  table{width:100%;border-collapse:collapse;font-size:13px}
  td{padding:9px 8px;border-bottom:1px solid #23262e;vertical-align:top}
  .dim{color:#9aa0a6;font-size:12px}
  .pos{color:#34a853;font-weight:600}.neg{color:#ea4335;font-weight:600}
  .banner{background:#2a2410;border:1px solid #5c4a00;color:#f4c84a;border-radius:10px;
          padding:10px 12px;font-size:12px;margin:10px 0}
  .big{display:block;background:#1a1c22;border-radius:14px;padding:18px;margin:10px 0;
       border:1px solid #23262e;font-size:16px;font-weight:600}
  .big .d{font-size:12px;color:#9aa0a6;font-weight:400;margin-top:4px}
  .foot{color:#5f6368;font-size:11px;margin-top:22px;text-align:center}
  .src{font-size:12px;padding:8px 12px;border-radius:10px;margin:4px 0 2px}
  .src.ok{background:#13351c;color:#5ed88a}
  .src.ko{background:#2a2410;color:#f4c84a}
"""


def layout(title: str, active: str, body: str, refresh: bool = False) -> str:
    e = html.escape
    nav_items = [("home", "/", "🏠 Accueil"), ("matches", "/app", "🎾 Matchs"),
                 ("perf", "/tracking/dashboard", "📊 Perf")]
    nav = '<div class="nav">' + "".join(
        f'<a class="{"on" if active==k else ""}" href="{href}">{e(lbl)}</a>'
        for k, href, lbl in nav_items) + "</div>"
    meta_refresh = '<meta http-equiv="refresh" content="180">' if refresh else ""
    return f"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{meta_refresh}<title>{e(title)} · BetsFix</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><h1>🎾 BetsFix</h1></div>{nav}{body}
<div class="foot">Données SofaScore + Unibet BE · informatif, sans garantie · jouez responsable</div>
</div></body></html>"""


def render_home(rep: dict, source: dict | None = None) -> str:
    e = html.escape
    roi = rep.get("value_roi")
    roi_txt = "—" if roi is None else f"{'+' if roi >= 0 else ''}{round(roi*100,1)}%"
    if source and source.get("ok"):
        src = '<div class="src ok">🟢 Source : SofaScore OK</div>'
    elif source:
        src = (f'<div class="src ko">🟠 SofaScore en pause '
               f'({source.get("paused_seconds", 0)}s) — LiveScore prend le relais</div>')
    else:
        src = ""
    body = f"""{src}"""
    body += f"""
<a class="big" href="/app">🎾 Matchs à venir & analyses
  <div class="d">Voir les matchs du jour, le favori du modèle et les value picks</div></a>
<a class="big" href="/tracking/dashboard">📊 Performance du modèle
  <div class="d">ROI value : {e(roi_txt)} · {rep.get('value_paris_regles',0)} paris réglés · {rep.get('matchs_suivis',0)} suivis</div></a>
<a class="big" href="/docs">🛠️ API & documentation
  <div class="d">Tous les endpoints (matchs, stats, joueurs, cotes…)</div></a>
<div class="banner">Outil personnel d'aide à la décision. Le modèle est en cours de
 validation (voir Performance). N'engagez que de petites mises, et seulement ce que
 vous pouvez vous permettre de perdre.</div>"""
    return layout("Accueil", "home", body, refresh=True)


def _bar(pct: float | None) -> str:
    p = round((pct or 0) * 100)
    return f'<div class="bar"><span style="width:{p}%"></span></div>'


def render_matches(groups: list[tuple[str, list[dict]]], fallback: bool = False) -> str:
    """groups: liste de (titre, [match_dict]). match_dict: id,tour,home,away,time,status,
    fav,favp,confidence,value,clickable."""
    e = html.escape
    out = []
    if fallback:
        out.append('<div class="banner">⚠️ SofaScore momentanément indisponible — scores '
                   'affichés via LiveScore (repli). L\'analyse détaillée revient dès que '
                   'SofaScore répond.</div>')
    else:
        out.append('<div class="banner">Touchez un match pour son analyse détaillée. '
                   'Heures en fuseau belge. Une "value" = avis du modèle, à confirmer par le suivi.</div>')
    total = 0
    for title, ms in groups:
        if not ms:
            continue
        out.append(f"<h2>{e(title)} ({len(ms)})</h2>")
        for m in ms:
            total += 1
            badge = (f'<span class="badge b-val">VALUE · {e(m["value"])}</span>'
                     if m.get("value") else '<span class="badge b-dim">—</span>')
            status = "🔴 en cours" if m["status"] == "inprogress" else e(m.get("time") or "")
            inner = (
                f'<div class="rowtop"><span>{e(m["tour"].upper())} · {status}</span>{badge}</div>'
                f'<div class="players">{e(m["home"])} <span class="dim">vs</span> {e(m["away"])}</div>'
                f'<div class="dim">favori modèle : {e(m.get("fav") or "—")} {e(m.get("favp") or "")}'
                f' · confiance {e(m.get("confidence") or "—")}</div>')
            if m.get("clickable", True):
                out.append(f'<a class="row" href="/app/match/{m["id"]}?tour={m["tour"]}">{inner}</a>')
            else:
                out.append(f'<div class="row">{inner}</div>')
    if not total:
        out.append('<div class="dim">Aucun match à venir pour le moment.</div>')
    return layout("Matchs", "matches", "".join(out), refresh=True)


def render_match_detail(a, winner_odds: tuple[float | None, float | None]) -> str:
    """a = MatchAnalysis ; winner_odds = (cote_home, cote_away) Unibet."""
    e = html.escape
    hp = a.model_home_probability
    ap = a.model_away_probability
    head = (f'<a class="dim" href="/app">← Retour aux matchs</a>'
            f'<div class="players" style="font-size:18px;margin-top:10px">'
            f'{e(a.home.name)} <span class="dim">vs</span> {e(a.away.name)}</div>'
            f'<div class="dim">{e(a.ground_type or "")} · statut {e(a.status or "")} '
            f'· confiance {e(a.confidence or "—")}</div>')

    probs = ""
    if hp is not None:
        probs = (f'<h2>Probabilités du modèle</h2>'
                 f'<div class="dim">{e(a.home.name)} {round(hp*100)}% · '
                 f'{e(a.away.name)} {round(ap*100)}%</div>{_bar(hp)}')

    frows = "".join(
        f'<tr><td>{e(f.name)}</td><td>{round((f.home or 0)*100)}%</td>'
        f'<td>{round((f.away or 0)*100)}%</td><td class="dim">{e(f.detail or "")}</td></tr>'
        for f in a.factors)
    factors = (f'<h2>Facteurs</h2><table><tr><td class="dim">facteur</td>'
               f'<td class="dim">{e(a.home.name.split()[-1])}</td>'
               f'<td class="dim">{e(a.away.name.split()[-1])}</td><td class="dim">détail</td></tr>'
               f'{frows}</table>') if a.factors else ""

    # Cotes Unibet vainqueur + value
    oh, oa = winner_odds
    odds_html = ""
    if a.unibet_matched and (oh or oa):
        odds_html = (f'<h2>Cotes Unibet (vainqueur)</h2>'
                     f'<table><tr><td>{e(a.home.name)}</td><td><b>{oh or "—"}</b></td></tr>'
                     f'<tr><td>{e(a.away.name)}</td><td><b>{oa or "—"}</b></td></tr></table>')

    values = [v for v in a.value_bets if v.is_value]
    if values:
        v = max(values, key=lambda x: x.edge or 0)
        verdict = (f'<div class="big" style="border-color:#1b5e20">✅ VALUE : {e(v.player)} @ {v.odds}'
                   f'<div class="d">edge +{round((v.edge or 0)*100,1)} pts · mise conseillée '
                   f'{v.recommended_stake_pct}% de bankroll (¼-Kelly)</div></div>')
    elif a.unibet_matched:
        verdict = ('<div class="big">⏸️ Abstention<div class="d">Cotes Unibet conformes au '
                   'modèle : pas de value nette.</div></div>')
    else:
        verdict = ('<div class="big">Cotes Unibet indisponibles<div class="d">Match pas '
                   'encore à l\'affiche du book.</div></div>')

    body = head + verdict + probs + factors + odds_html
    return layout(f"{a.home.name} vs {a.away.name}", "matches", body)
