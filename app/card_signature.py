"""Style de carte « signature » — grand public (flag BETSFIX_CARD_STYLE=signature).

⚠️ ISOLÉ & GATÉ : rien n'importe ce module tant que `CARD_STYLE != "signature"`. Le site live
(défaut `ticket`) n'est pas affecté. Revert = ne pas poser le flag.

Principes (maquette validée 2026-09-05, cf. artifact « grand public ») :
- UN seul héros par carte = LE PARI (gros). La cote se fait discrète (petit, en haut à droite).
- Zéro jargon : « nos chances » (confiance calibrée) vs « cote du marché » (1/cote), gains en %.
- Jauge d'edge : part MARCHÉ (grise) + notre EDGE (colorée) = la value se lit d'un coup d'œil.
- Tag qualitatif dérivé : « Bonne value » (value forte) / « Pari sûr » (sinon).
- Réglé : l'edge cède la place au SCORE + GAIN en % (+65 % / −100 %).
- Classes `.sg-*` + variables CSS scopées dans `.sg-card` (AUCUNE collision avec le reste du site).
- Icônes SVG INLINE (pas de sprite global). Logo en filigrane via /static/logo.png.
"""
from __future__ import annotations
import html as _html
import re as _re

# ── icônes (inline, viewBox obligatoire — sinon formes coupées) ─────────────────
_ICONS = {
    "shield": '<path d="M12 3l7 2.6v5.1c0 4.3-2.9 7.2-7 8.8-4.1-1.6-7-4.5-7-8.8V5.6z"/>',
    "gem":    '<path d="M12 3l4.5 5L12 21 7.5 8z"/><path d="M7.5 8h9"/>',
    "layers": '<path d="M12 3l8.5 4.2L12 11.4 3.5 7.2z"/><path d="M4 11.5l8 4 8-4"/><path d="M4 15.5l8 4 8-4"/>',
    "clock":  '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/>',
    "check":  '<path d="M4.5 12.5l4.5 4.5L19.5 6.5"/>',
    "cross":  '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "star":   '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
}


def _ic(name: str) -> str:
    return f'<svg class="sg-ic" viewBox="0 0 24 24">{_ICONS.get(name, "")}</svg>'


def _num(cote) -> float | None:
    try:
        c = float(cote)
        return c if c > 1 else None
    except (TypeError, ValueError):
        return None


def _pct(x) -> str:
    return f"{int(round(x))} %"


def _tier_of(conf_i, cote) -> tuple[str, str, str]:
    """(icône, tag_texte, classe_carte) dérivés — value forte => 'Bonne value', sinon 'Pari sûr'.
    Purement qualitatif (l'affiliation de tier réelle est portée par la section du site)."""
    c = _num(cote)
    val = None
    if isinstance(conf_i, (int, float)) and c:
        val = (conf_i / 100.0 * c - 1) * 100
    if val is not None and val >= 8:
        return "gem", "Bonne value", "value"
    return "shield", "Pari sûr", ""


def _edge_block(conf_i, cote, *, lab: str = "Nos chances estimées") -> str:
    """Bloc estimation : tag + jauge (marché gris + edge coloré) + « cote du marché X % · nos chances Y % ».
    '' si pas de confiance/cote exploitable."""
    c = _num(cote)
    if not isinstance(conf_i, (int, float)) or not c:
        return ""
    ours = max(0.0, min(100.0, float(conf_i)))
    mkt = max(0.0, min(100.0, 100.0 / c))
    value = (conf_i / 100.0 * c - 1) * 100
    if value >= 8:
        # VALUE : on montre l'EDGE vs marché (part marché grise + notre edge colorée) — c'est le point.
        edge = max(0.0, ours - mkt)
        return (
            '<div class="sg-est">'
            f'<div class="sg-est-top"><span class="sg-est-lab">{_html.escape(lab)}</span>'
            f'<span class="sg-qtag">{_ic("star")}Bonne value</span></div>'
            f'<div class="sg-eg"><span class="sg-mkt" style="width:{mkt:.0f}%"></span>'
            f'<span class="sg-edg" style="width:{edge:.0f}%"></span></div>'
            f'<div class="sg-egk"><span>Cote du marché : <b>{_pct(mkt)}</b></span>'
            f'<span class="sg-us">Nos chances : {_pct(ours)}</span></div>'
            '</div>'
        )
    # SÛRETÉ (Confiance / Combiné) : JUSTE notre proba de gagner, PAS de comparaison au marché — sinon un favori
    # court (ou un combiné sûr sous le plancher) afficherait « nos chances < marché » et se dévaloriserait.
    # Badge « Pari sûr » RETIRÉ (user 2026-09-06) : on met le % de réussite À SA PLACE (haut-droite), et on
    # retire la ligne du bas (devenue redondante) -> label « Nos chances de gagner » + « 86 % » + jauge.
    return (
        '<div class="sg-est">'
        '<div class="sg-est-top"><span class="sg-est-lab">Confiance</span>'
        f'<span class="sg-qpct">{_pct(ours)}</span></div>'
        f'<div class="sg-eg"><span class="sg-edg" style="width:{ours:.0f}%"></span></div>'
        '</div>'
    )


def _result_strip(cote, result: str, *, tally_html: str = "") -> str:
    """Bandeau réglé : GAIN +X % (vert) / PERTE −100 % (rouge) + cote encaissée. `tally_html` = repère à
    droite (ex. « 3/3 jambes » pour un combiné)."""
    c = _num(cote)
    won = result == "won"
    lost = result == "lost"
    if won and c:
        pnl_l, pnl_v, pnl_cls = "Gain", f"+{int(round((c - 1) * 100))} %", "g"
    elif lost:
        pnl_l, pnl_v, pnl_cls = "Perte", "−100 %", "r"
    else:                                              # push / void = remboursé
        pnl_l, pnl_v, pnl_cls = "Remboursé", "0 %", ""
    cote_lab = "Cote encaissée" if won else "Cote"
    cote_txt = f"{c:g}" if c else "—"
    return (
        '<div class="sg-result">'
        f'<div class="sg-pnl"><span class="sg-l">{pnl_l}</span><span class="sg-v {pnl_cls}">{pnl_v}</span></div>'
        f'<div class="sg-rside"><span class="sg-t">{cote_lab}</span><span class="sg-num">{_html.escape(cote_txt)}</span>{tally_html}</div>'
        '</div>'
    )


_STATUS = {"soon": "À venir", "live": "Live", "won": "Gagné", "lost": "Perdu", "push": "Remboursé"}


def _status_pill(rk: str, compact: bool = False) -> str:
    icon = {"won": _ic("check"), "lost": _ic("cross"), "live": '<span class="sg-dl"></span>'}.get(rk, "")
    # compact (user 2026-09-06) : badge de RÉSULTAT en haut-droite = pastille ICÔNE SEULE (✓/✕), sans le mot
    # « Gagné »/« Perdu » (redondant). Réservé à won/lost ; push (remboursé, pas d'icône) garde son libellé.
    if compact and rk in ("won", "lost"):
        return f'<span class="sg-st sg-st-ic" aria-label="{_STATUS.get(rk)}">{icon}</span>'
    return f'<span class="sg-st">{icon}{_STATUS.get(rk, "À venir")}</span>'


def _why_fold(text: str, right_html: str = "") -> str:
    """« Pourquoi ce pari » repliable (puces) — remplit le bas-gauche de la carte + restaure l'analyse.
    '' si pas de texte. Découpe en phrases courtes (≤5)."""
    t = (text or "").strip()
    if not t:
        return ""
    parts = [p.strip() for p in _re.split(r"(?<=[.!?])\s+", t) if p.strip()][:5]
    if not parts:
        return ""
    lis = "".join(f"<li>{_html.escape(p)}</li>" for p in parts)
    # Le <details> EST le pied (pleine largeur) : summary = libellé (gauche) + `right_html` = pastille statut
    # (droite) sur la MÊME ligne ; les puces s'étalent sur TOUTE la largeur dessous (user 2026-09-06 : le
    # « pourquoi » déplié ne prenait pas toute la largeur car il partageait la ligne flex avec la pastille).
    return ('<details class="sg-why sg-foot-why"><summary onclick="event.stopPropagation()">'
            '<span class="sg-why-lab">Pourquoi ce pari<span class="sg-chev">▾</span></span>'
            f'{right_html}</summary><ul>{lis}</ul></details>')


def _rk_of(rcls: str, is_live: bool, is_finished: bool, result: str = "") -> str:
    if result in ("won", "lost", "push", "void") or any(k in (rcls or "") for k in ("mc-r-won", "mc-r-lost", "mc-r-push")):
        r = result or ("won" if "mc-r-won" in rcls else "lost" if "mc-r-lost" in rcls else "push")
        return "push" if r == "void" else r
    return "live" if is_live else "soon"


def sig_bet_card(*, league: str = "", match_txt: str = "", when_txt: str = "", time_txt: str = "",
                 sel_txt: str = "", cote_txt: str = "", cote=None, conf_i=None, rcls: str = "",
                 is_live: bool = False, is_finished: bool = False, score_txt: str = "",
                 start=None, abst: bool = False, why_text: str = "", **_ignore) -> str:
    """Carte PARI SIMPLE (à-venir / live / réglé), style signature grand public. Drop-in : accepte les mêmes
    kwargs que `_tk_bet_card` (extras ignorés)."""
    e = _html.escape
    if cote is None and cote_txt:
        cote = _num(cote_txt)
    rk = _rk_of(rcls, is_live, is_finished)
    settled = rk in ("won", "lost", "push")
    icon, _, cls = _tier_of(conf_i, cote)
    # en-tête : icône (tier dérivé) + compétition + COTE discrète
    _cote_h = (f'<div class="sg-cote"><span class="sg-k">COTE</span>'
               f'<span class="sg-v">{e(cote_txt)}</span></div>') if cote_txt else ""
    _eye = e(league) if league else ""
    _time = time_txt or (when_txt or "")
    # « Aujourd'hui » est REDONDANT (le calendrier/la section du jour font déjà foi) -> on ne garde que
    # l'heure. « Hier »/« Demain »/dates explicites sont conservés (info utile hors du jour). User 2026-09-05.
    _time = _re.sub(r"^\s*[Aa]ujourd['’]hui\s*", "", _time).strip()
    # icône (coche/croix/horloge) retirée devant les équipes (user 2026-09-06) : le badge + la couleur de
    # carte portent déjà le statut -> ligne match/heure épurée. SOON : l'heure passe dans la pastille du pied
    # (au lieu du badge « À venir », user 2026-09-06) -> on la RETIRE du méta pour ne pas la dupliquer.
    _meta_time = "" if rk == "soon" else _time
    _meta = ('<div class="sg-meta">'
             f'<span class="sg-mtxt">{e(match_txt)}{(" · " + e(_meta_time)) if _meta_time else ""}</span></div>')
    # héros = le pari (+ score si réglé). Plus de pastille ronde ✓/✕ devant le pari (user 2026-09-06) : le
    # badge GAGNÉ/PERDU en haut-droite + la couleur de carte + la coche du méta suffisent -> ligne épurée.
    _sc = f'<span class="sg-score">{e(score_txt)}</span>' if (settled and score_txt) else ""
    _pick = f'<div class="sg-pick{" abst" if abst else ""}">{e(sel_txt)}{_sc}</div>'
    _mid = _result_strip(cote, rk) if settled else _edge_block(conf_i, cote)
    # RÉGLÉ (user 2026-09-05) : la COTE en haut à droite fait DOUBLON avec « Cote encaissée » du bandeau
    # résultat -> on la remplace par le badge GAGNÉ/PERDU/REMBOURSÉE, et on SUPPRIME le pied (plus de badge
    # en double, carte plus courte). À-venir/live : COTE en haut (utile) + badge (+ « pourquoi ») en pied.
    _head_right = _status_pill(rk, compact=True) if settled else _cote_h
    # SOON : au lieu du badge « À venir », on affiche l'HEURE du match (user 2026-09-06).
    _foot_right = (f'<span class="sg-st sg-st-time">{_ic("clock")}{e(_time)}</span>'
                   if (rk == "soon" and _time) else _status_pill(rk))
    if settled:
        _foot = ""
    else:
        # « Pourquoi ce pari » = <details> PLEINE LARGEUR (la pastille est dans son summary, à droite).
        _wf = "" if abst else _why_fold(why_text, right_html=_foot_right)
        _foot = _wf or f'<div class="sg-foot sg-foot-bare">{_foot_right}</div>'
    _wm = '<div class="sg-wmk"></div>'
    body = f'<div class="sg-in">{_head_row(_eye, icon, _head_right)}{_meta}{_pick}{_mid}{_foot}</div>'
    # CLIC N'IMPORTE OÙ SUR LA CARTE -> déploie/replie « Pourquoi ce pari » (user 2026-09-06). Le <summary>
    # garde son stopPropagation -> pas de double bascule quand on clique précisément dessus. No-op si pas de fold.
    _toggle = "var d=this.querySelector('details.sg-why');if(d){d.open=!d.open}"
    return f'<div class="sg-card {cls} {rk}" onclick="{_toggle}">{_wm}{body}</div>'


def _head_row(eye: str, icon: str, cote_h: str) -> str:
    # icône de tier retirée devant la ligue (user 2026-09-06) : la section (CONFIANCES/VALUE) + la couleur
    # de carte portent déjà le tier -> ligne de ligue épurée. `icon` gardé en param (compat appelants).
    return (f'<div class="sg-h"><div class="sg-cat">'
            f'<span class="sg-t">{eye}</span></div>{cote_h}</div>')


def sig_result_card(*, league: str = "", match_txt: str = "", sel_txt: str = "", cote=None, cote_txt: str = "",
                    result: str = "", conf_i=None, score_txt: str = "", when_txt: str = "",
                    start=None, **_ignore) -> str:
    """Carte RÉSULTAT simple — délègue à `sig_bet_card` en mode réglé."""
    return sig_bet_card(league=league, match_txt=match_txt, when_txt=when_txt, sel_txt=sel_txt,
                        cote=cote, cote_txt=cote_txt or (f"{_num(cote):g}" if _num(cote) else ""),
                        conf_i=conf_i, is_finished=True, score_txt=score_txt,
                        rcls={"won": "mc-r-won", "lost": "mc-r-lost"}.get(result, "mc-r-push"),
                        start=start)


def sig_combo_card(cb: dict, *, title: str = "Combiné", sport: str = "foot", pretty=None, when_fmt=None) -> str:
    """Combiné signature : jambes compactes (pari + cote + confiance/jambe + heure), edge sur le TOTAL (à venir)
    ou score + gain % (réglé). `pretty(sel, home, away)` = formateur d'intitulé (web._pretty_sel) ;
    `when_fmt(start, with_date=False)` = formateur d'heure LOCALE (web.fmt_local) si fournis."""
    e = _html.escape
    legs = sorted(cb.get("legs") or [], key=lambda l: str(l.get("start") or "~"))
    if not legs:
        return ""
    res = cb.get("result")
    all_done = all(l.get("result") in ("won", "lost", "push", "void") for l in legs)
    any_s = any(l.get("result") in ("won", "lost", "push", "void") for l in legs)
    rk = ("won" if res == "won" else "lost" if res == "lost" else "push" if res in ("void", "push")
          else "live" if (any_s and not all_done) else "soon")
    settled = rk in ("won", "lost", "push")
    total = _num(cb.get("cote"))
    # proba combinée = produit des probas par jambe (fractions)
    prod, have = 1.0, True
    for l in legs:
        p = l.get("prob")
        if not isinstance(p, (int, float)):
            have = False
            break
        prod *= (p if p <= 1 else p / 100.0)
    ours = prod * 100 if have else None
    cote_txt = f"{total:g}" if total else ""
    cote_h = (f'<div class="sg-cote"><span class="sg-k">COTE</span>'
              f'<span class="sg-v">{e(cote_txt)}</span></div>') if cote_txt else ""
    won_n = sum(1 for l in legs if l.get("result") == "won")
    _legs_html = ""
    for i, l in enumerate(legs):
        lr = l.get("result")
        lk = "push" if lr == "void" else (lr if lr in ("won", "lost", "push") else "soon")
        lh, la = l.get("home", ""), l.get("away", "")
        lsel = pretty(l.get("sel", ""), lh, la) if pretty else str(l.get("sel", ""))
        lc = l.get("cote")
        lct = f"{float(lc):g}" if isinstance(lc, (int, float)) and lc else ""
        lscore = _re.sub(r"\s*\((?:sets?|SETS?)\)\s*$", "", str(l.get("score") or "")).strip()
        lp = l.get("prob")
        lconf = (round(lp * 100) if isinstance(lp, (int, float)) and lp <= 1
                 else (round(lp) if isinstance(lp, (int, float)) else None))
        odds = f'<span class="sg-odds"><i>@</i><b>{e(lct)}</b></span>' if lct else ""
        mk = ('<span class="sg-mk ok">✓</span>' if lk == "won"
              else '<span class="sg-mk no">✕</span>' if lk == "lost" else "")
        if settled or lk in ("won", "lost", "push"):
            sc = (f'<span class="sg-score">{e(lscore.replace("-", " - "))}</span>'
                  if lscore and any(ch.isdigit() for ch in lscore) else "")
            _legs_html += (f'<div class="sg-cleg-top">{mk}<span class="sg-cleg-sel">{e(lsel)}</span>{sc}{odds}</div>')
        else:
            _lwhen = ""
            if when_fmt:
                try:
                    _lwhen = when_fmt(l.get("start"), with_date=False) or ""
                except Exception:
                    _lwhen = ""
            _tt = f"{lh} — {la}" if (lh and la) else ""
            if _lwhen:
                _tt += (" · " if _tt else "") + _lwhen
            teams = e(_tt)
            cf = (f'<span class="sg-cleg-cf">{_ic("shield")}{lconf} %</span>'
                  if lconf is not None else "")
            # jambe présentée COMME UNE CARTE SIMPLE : méta (heure/équipes + cote) au-dessus, PARI en héros dessous
            _legs_html += (
                f'<div class="sg-cleg-r1">{_ic("clock")}<span class="sg-cleg-teams">{teams}</span>{odds}</div>'
                f'<div class="sg-cleg-r2"><span class="sg-cleg-sel">{e(lsel)}</span>{cf}</div>')
        if i < len(legs) - 1:
            _legs_html += '<div class="sg-div thin"></div>'
    _nb = f'{len(legs)} sélection{"s" if len(legs) > 1 else ""}'
    _meta = (f'<div class="sg-meta">{_ic("check" if rk == "won" else "cross" if rk == "lost" else "clock")}'
             f'<span class="sg-mtxt">{_nb}{" · terminé" if settled else ""}</span></div>')
    # value du combiné (EV) : décide le cadrage. Un combiné 3 jambes multiplie vers ~50 % -> NE JAMAIS titrer le
    # « X % combiné » sur un combiné de sûreté (ça le fait passer pour un pile-ou-face). La confiance PAR JAMBE
    # (déjà affichée) porte le message. On ne montre l'edge/marché QUE si le combiné a une vraie value (>=8 %).
    _cv = ((ours / 100.0 * total - 1) * 100) if (ours and total) else None
    if settled:
        _tally = f'<span class="sg-tally">{_ic("check")}{won_n} / {len(legs)} jambes</span>' if rk == "won" else ""
        _mid = _result_strip(total, rk, tally_html=_tally)
    elif _cv is not None and _cv >= 8:
        _mid = _edge_block(ours, total, lab="Chances du combiné")     # vraie value -> edge vs marché
    else:                                                             # sûreté -> juste le tag, PAS de % combiné
        _mid = ('<div class="sg-est"><div class="sg-est-top">'
                f'<span class="sg-est-lab">{len(legs)} sélections sûres cumulées</span>'
                f'<span class="sg-qtag">{_ic("shield")}Pari sûr</span></div></div>')
    _foot = f'<div class="sg-foot sg-foot-bare">{_status_pill(rk)}</div>'
    _head = (f'<div class="sg-h"><div class="sg-cat">{_ic("layers")}'
             f'<span class="sg-t">{e(title)}</span></div>{cote_h}</div>')
    body = f'<div class="sg-in">{_head}{_meta}<div class="sg-div"></div>{_legs_html}{_mid}{_foot}</div>'
    _vcls = "value" if (not settled and _cv is not None and _cv >= 8) else ""
    return f'<div class="sg-card {_vcls} {rk}"><div class="sg-wmk"></div>{body}</div>'


def sig_live_card(*, league: str = "", match_txt: str = "", minute: str = "", sel_txt: str = "",
                  cote_txt: str = "", cote=None, score_txt: str = "", chance_live=None,
                  why_text: str = "", start=None, **_ignore) -> str:
    """Carte LIVE signature (onglet Directs) : CONSERVE le score EN DIRECT + la barre « chance live » (proba
    que le pari passe maintenant), en habillage grand public or/ambre. `minute` = horloge (« 63' »),
    `chance_live` = % (0-100), `score_txt` = « 1 - 0 »."""
    e = _html.escape
    _cote_h = (f'<div class="sg-cote"><span class="sg-k">COTE</span>'
               f'<span class="sg-v">{e(cote_txt)}</span></div>') if cote_txt else ""
    _mtxt = e(match_txt) + ((" · " + e(minute)) if minute else "")
    _meta = f'<div class="sg-meta">{_ic("bolt")}<span class="sg-mtxt">{_mtxt}</span></div>'
    _pick = f'<div class="sg-pick">{e(sel_txt)}</div>'
    _score = (f'<span class="sg-live-score"><span class="sg-dl"></span>{e(score_txt)}</span>'
              if score_txt else "")
    _ch = None
    try:
        _ch = max(0.0, min(100.0, float(chance_live)))
    except (TypeError, ValueError):
        _ch = None
    if _ch is not None:
        _est = ('<div class="sg-est">'
                '<div class="sg-est-top"><span class="sg-est-lab">Nos chances en direct</span>'
                f'{_score}</div>'
                f'<div class="sg-eg"><span class="sg-edg" style="width:{_ch:.0f}%"></span></div>'
                f'<div class="sg-egk"><span></span><span class="sg-us">{int(round(_ch))} % que ça passe</span></div>'
                '</div>')
    else:                                            # pas de chance calculée -> au moins le score en direct
        _est = (f'<div class="sg-est"><div class="sg-est-top">'
                '<span class="sg-est-lab">Score en direct</span>' + (_score or "") + '</div></div>')
    _wf = _why_fold(why_text, right_html=_status_pill("live"))   # <details> pleine largeur (pastille dans le summary)
    _foot = _wf or f'<div class="sg-foot sg-foot-bare">{_status_pill("live")}</div>'
    _head = _head_row(e(league), "shield", _cote_h)
    body = f'<div class="sg-in">{_head}{_meta}{_pick}{_est}{_foot}</div>'
    _toggle = "var d=this.querySelector('details.sg-why');if(d){d.open=!d.open}"
    return f'<div class="sg-card live" onclick="{_toggle}"><div class="sg-wmk"></div>{body}</div>'


def sig_css() -> str:
    """CSS du style signature — variables SCOPÉES dans `.sg-card` (zéro fuite / collision globale)."""
    return _SIG_CSS + _SIG_COMBO_CSS + _SIG_LIVE_CSS


_SIG_CSS = """
  .sg-card{--st:#5ab6f0;--st2:#2f7fc4;--cyan:#5fd0ff;--green:#3fd684;--red:#ff9d9d;
    --txt:#f2f7fc;--muted:#aebfd2;--dim:#7d92a9;--line:rgba(255,255,255,.07);
    --logo:url('/static/logo.png');--num:"tnum" 1,"lnum" 1;
    position:relative;border-radius:16px;overflow:hidden;isolation:isolate;margin:11px 0;
    font-family:Selawik,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--txt);
    background:linear-gradient(180deg,#101f30,#0a1420) padding-box,
      linear-gradient(160deg,color-mix(in srgb,var(--st) 46%,transparent),color-mix(in srgb,var(--st2) 30%,transparent)) border-box;
    /* dégradé de bordure UNIFORME (plus de fondu vers transparent) -> le cadre/halo est IDENTIQUE en replié
       et déplié (user 2026-09-06 : le halo changeait quand le « pourquoi » s'étirait sur une carte plus haute) */
    border:1px solid transparent;box-shadow:0 1px 1px rgba(0,0,0,.5),0 22px 46px -28px rgba(0,0,0,.98),inset 0 1px 0 rgba(255,255,255,.06)}
  .sg-card.value{--st:#3fd684;--st2:#22a866}
  .sg-card.live{--st:#ffcf5a;--st2:#f6a11e}
  .sg-card.won{--st:#5be79b;--st2:#25b264}.sg-card.lost{--st:#ff9d9d;--st2:#e14a4a}
  .sg-card.soon{--st:#f4c24c;--st2:#e0a42c}   /* À VENIR = JAUNE (user 2026-09-06) — confiance ET value ; après .value pour gagner la cascade */
  .sg-card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;z-index:3;
    background:linear-gradient(180deg,var(--st),var(--st2));box-shadow:0 0 14px color-mix(in srgb,var(--st) 55%,transparent)}
  .sg-card:has(details.sg-why){cursor:pointer}   /* clic n'importe où -> déploie le « pourquoi » (user 2026-09-06) */
  .sg-wmk{position:absolute;top:0;left:0;right:0;height:172px;z-index:0;opacity:.028;background:var(--logo) center/140px no-repeat;filter:grayscale(.35) brightness(1.35)}  /* hauteur FIXE ancrée en haut -> le filigrane ne bouge PLUS quand le « pourquoi » se déplie (user 2026-09-06) */
  .sg-in{position:relative;z-index:2;padding:15px 16px 14px 18px}
  .sg-ic{width:1em;height:1em;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex:none;vertical-align:-.12em}
  .sg-h{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  .sg-cat{display:flex;align-items:center;gap:6px;min-width:0}.sg-cat .sg-ic{font-size:13px;color:var(--st)}
  .sg-cat .sg-t{font-size:11px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sg-cote{display:inline-flex;align-items:baseline;gap:6px;flex:none;white-space:nowrap}   /* COTE + valeur sur UNE seule ligne (user 2026-09-06) */
  .sg-cote .sg-k{font-size:9.5px;font-weight:800;letter-spacing:.12em;color:var(--dim)}
  .sg-cote .sg-v{font-size:17px;font-weight:800;color:var(--txt);font-feature-settings:var(--num);line-height:1}   /* valeur MIEUX indiquée : plus grande + plus claire */
  .sg-meta{margin-top:3px;display:flex;gap:6px;align-items:center;font-size:12px;font-weight:600;color:var(--dim);min-width:0}
  .sg-meta .sg-ic{font-size:12px;opacity:.8}
  .sg-mtxt{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sg-pick{margin-top:12px;font-size:17.5px;font-weight:800;color:#fff;letter-spacing:-.01em;line-height:1.24;overflow-wrap:anywhere}  /* pari LONG -> retour à la ligne (plus d'ellipse, user 2026-09-06) ; taille réduite légèrement (user 2026-09-06) */
  .sg-pick.abst{color:var(--muted);font-weight:700}
  .sg-mk{display:inline-flex;align-items:center;justify-content:center;width:19px;height:19px;border-radius:50%;font-size:11px;font-weight:900;margin-right:8px;vertical-align:-2px}
  .sg-mk.ok{color:#06210f;background:var(--green)}.sg-mk.no{color:#2a0d0d;background:#ff8a8a}
  .sg-score{margin-left:8px;font-size:12px;font-weight:800;color:var(--muted);font-feature-settings:var(--num);padding:2px 8px;border-radius:6px;background:rgba(255,255,255,.05);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08);vertical-align:2px}
  .sg-est{margin-top:13px}
  .sg-est-top{display:flex;align-items:center;justify-content:space-between;gap:10px}
  .sg-est-lab{font-size:10.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--dim)}
  .sg-qtag{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:800;padding:4px 11px;border-radius:99px;
    color:var(--st);background:color-mix(in srgb,var(--st) 13%,transparent);border:1px solid color-mix(in srgb,var(--st) 32%,transparent)}
  .sg-qtag .sg-ic{font-size:12px}
  .sg-qpct{flex:none;font-size:17px;font-weight:800;color:var(--st);font-feature-settings:var(--num);line-height:1}  /* % de réussite (remplace le badge « Pari sûr », user 2026-09-06) */
  .sg-eg{margin-top:9px;height:9px;border-radius:5px;background:rgba(255,255,255,.05);overflow:hidden;display:flex}
  .sg-eg .sg-mkt{height:100%;background:rgba(174,191,210,.28)}
  .sg-eg .sg-edg{height:100%;background:linear-gradient(90deg,var(--st2),var(--st));box-shadow:0 0 10px color-mix(in srgb,var(--st) 50%,transparent)}
  .sg-egk{margin-top:8px;display:flex;justify-content:space-between;font-size:12px;font-weight:700;color:var(--dim);font-feature-settings:var(--num)}
  .sg-egk .sg-us{color:var(--st);font-weight:800}.sg-egk b{font-weight:800}
  .sg-result{margin-top:12px;padding:11px 14px;border-radius:12px;display:flex;align-items:center;justify-content:space-between;gap:12px;background:color-mix(in srgb,var(--st) 10%,transparent)}
  .sg-pnl{display:flex;flex-direction:column;gap:1px}
  .sg-pnl .sg-l{font-size:8.5px;font-weight:800;letter-spacing:.13em;text-transform:uppercase;color:var(--dim)}
  .sg-pnl .sg-v{font-size:22px;font-weight:800;font-feature-settings:var(--num);line-height:1}
  .sg-pnl .sg-v.g{color:var(--green)}.sg-pnl .sg-v.r{color:var(--red)}
  .sg-rside{display:flex;flex-direction:column;align-items:flex-end;gap:2px;font-feature-settings:var(--num)}
  .sg-rside .sg-t{font-size:8.5px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
  .sg-rside .sg-num{font-size:15px;font-weight:800;color:var(--muted)}
  .sg-rside .sg-tally{display:inline-flex;align-items:center;gap:5px;color:var(--st);font-weight:800;font-size:11px;margin-top:1px}
  .sg-foot{margin-top:13px;padding-top:11px;border-top:1px solid var(--line);display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  .sg-foot.sg-foot-bare{border-top:none;padding-top:0;margin-top:14px;justify-content:flex-end}
  .sg-why.sg-foot-why{margin-top:13px;padding-top:11px;border-top:1px solid var(--line)}   /* le <details> EST le pied, pleine largeur */
  .sg-why>summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:12px}
  .sg-why-lab{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:800;color:var(--st);white-space:nowrap}
  .sg-why>summary::-webkit-details-marker{display:none}
  .sg-chev{font-size:10px;transition:transform .18s}.sg-why[open] .sg-chev{transform:rotate(180deg)}
  .sg-why ul{margin:8px 0 0;padding:0;list-style:none}
  .sg-why li{position:relative;padding-left:15px;margin:5px 0;font-size:12.5px;line-height:1.5;color:#c4d3e6;font-weight:500}
  .sg-why li::before{content:"";position:absolute;left:0;top:8px;width:5px;height:5px;border-radius:50%;background:var(--st)}
  .sg-foot .sg-why>summary{padding:5px 0}
  .sg-st{display:inline-flex;align-items:center;gap:6px;font-size:10px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;padding:5px 11px;border-radius:99px;
    color:var(--st);background:color-mix(in srgb,var(--st) 13%,transparent);border:1px solid color-mix(in srgb,var(--st) 36%,transparent)}
  .sg-st .sg-ic{font-size:12px}
  .sg-st.sg-st-ic{padding:6px;border-radius:99px}.sg-st.sg-st-ic .sg-ic{font-size:14px}
  .sg-st.sg-st-time{text-transform:none;font-feature-settings:var(--num);letter-spacing:.02em}   /* heure du match (remplace « À venir », user 2026-09-06) */
  .sg-dl{width:6px;height:6px;border-radius:50%;background:var(--st);box-shadow:0 0 8px var(--st)}
"""

_SIG_COMBO_CSS = """
  .sg-div{height:1px;background:var(--line);margin:11px 0}.sg-div.thin{margin:15px 0}
  .sg-cleg-top{display:flex;align-items:center;gap:10px}
  .sg-cleg-r1{display:flex;align-items:center;gap:7px;min-width:0}
  .sg-cleg-r1 .sg-ic{font-size:11.5px;color:var(--dim);opacity:.8}
  .sg-cleg-r2{margin-top:5px;display:flex;align-items:center;gap:10px}
  .sg-cleg-sel{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:16.5px;font-weight:800;color:#fff;letter-spacing:-.005em}
  .sg-cleg-sub{margin-top:4px;display:flex;align-items:center;gap:10px}
  .sg-cleg-teams{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px;font-weight:600;color:var(--dim)}
  .sg-cleg-cf{flex:none;display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:800;color:var(--green);font-feature-settings:var(--num)}
  .sg-cleg-cf .sg-ic{font-size:11px}
  .sg-odds{flex:none;display:inline-flex;align-items:baseline;gap:2px;color:var(--muted);font-feature-settings:var(--num)}
  .sg-odds i{font-style:normal;font-size:11px;font-weight:700;opacity:.55}.sg-odds b{font-size:14px;font-weight:800}
"""

_SIG_LIVE_CSS = """
  .sg-live-score{flex:none;display:inline-flex;align-items:center;gap:7px;font-size:16px;font-weight:800;color:var(--st);
    font-feature-settings:var(--num);letter-spacing:.02em;padding:3px 11px;border-radius:8px;
    background:color-mix(in srgb,var(--st) 12%,transparent);border:1px solid color-mix(in srgb,var(--st) 32%,transparent)}
  .sg-live-score .sg-dl{width:6px;height:6px;border-radius:50%;background:var(--st);box-shadow:0 0 8px var(--st);animation:sgpulse 1.4s ease-in-out infinite}
  @keyframes sgpulse{50%{opacity:.4}}
  @media (prefers-reduced-motion:reduce){.sg-live-score .sg-dl{animation:none}}
"""
