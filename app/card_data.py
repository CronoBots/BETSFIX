"""POINT UNIQUE de construction des DONNÉES de carte Telegram (prono / résultat) depuis un sidecar.

Avant, la même logique (branchement combo/simple/calibration, regex `… @cote`, mapping legs/mark/score)
était dupliquée à l'identique dans 3 endroits — le scan (`tools/generate_analyses.py`), le règlement
(`app/settle_analyst.py`) et le repost (`tools/renotify_cards.py`) — d'où un risque de désynchro du
gabarit. Ici on centralise la DONNÉE ; le RENDU image reste dans `tools/card_image.py`.

Une carte = un dict consommé par `card_image.render_card`. Les clés `_mid`/`_start` (privées) servent
au fil prono->résultat (reply Telegram) et au tri chronologique ; elles sont ignorées au rendu.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from app import analyses, branding


def _clean_why(w) -> str:
    """Explication PROFESSIONNELLE et fiable d'une sélection : même pipeline que l'app (retire la liste
    « Sources : … », met les mises en % de bankroll, remet en phrase propre) PUIS masque tout NOM DE
    SOURCE (dé-branding) — une carte prono ne doit jamais nommer une source. '' si vide."""
    t = analyses._sentence_case(analyses._units_to_pct(analyses._strip_sources(str(w or "")))).strip()
    return branding.debrand(t)


# Synthèse de combiné AUTO-générée (note technique interne, ex. « Combiné optimisé sur la VRAIE cote … —
# jambes variées peu corrélées, chance estimée X% ») : ce N'EST PAS une analyse -> on ne l'affiche pas.
# On ne garde que les vraies synthèses de CORRÉLATION rédigées par l'analyste.
_SYNTH_BOILER = re.compile(r"optimis\w+ sur la (?:vraie )?cote|jambes? vari[ée]es? peu corr[ée]l|"
                           r"chance estim[ée]e\s*\d", re.I)


def _clean_synth(w) -> str:
    """Synthèse de combiné, nettoyée + dé-brandée ; '' si vide OU si c'est la note technique auto-générée."""
    t = _clean_why(w)
    if not t or _SYNTH_BOILER.search(t):
        return ""
    return t


def _split_leg(sel: str, home: str, away: str) -> tuple[str, str]:
    """Sépare un libellé de pari en (MARCHÉ, SÉLECTION) pour l'afficher sur DEUX lignes (marché discret
    « … : » puis sélection en avant), ex. « Cotes du match - Prolongations incluses » + « Corée du Sud ».
    Marché = '' si le libellé est déjà atomique/court (« Syrie gagne », « Andorre -18.5 ») -> une ligne.
    Découpe fiable : nom d'équipe (home/away) en fin de libellé, sinon marqueur de total « Plus/Moins de »."""
    s = str(sel or "").strip()
    for team in (home, away):
        t = str(team or "").strip()
        if t and s != t and s.endswith(t):
            market = s[:-len(t)].strip(" -–—:·")
            if market:
                return market, t
    m = re.search(r"\b(plus de|moins de|over|under)\b", s, re.I)
    if m and m.start() > 0:
        market = s[:m.start()].strip(" -–—:·")
        pick = s[m.start():].strip()
        if market and pick:
            return market, pick[:1].upper() + pick[1:]
    return "", s


def _pick_why(d: dict, sel: str) -> str:
    """« Pourquoi » du pari SIMPLE retenu, extrait du Verdict de l'analyse (.md) — comme sur l'app
    (_verdict_notes + _assign_notes, déjà nettoyés/sans sources). '' si introuvable."""
    try:
        md = os.path.join(analyses.DIR, f"{d.get('sport')}_{d.get('id')}.md")
        if not sel or not os.path.exists(md):
            return ""
        notes, _ = analyses._verdict_notes(open(md, encoding="utf-8").read())
        if not notes:
            return ""
        assigned = analyses._assign_notes([sel], notes)      # {0: why} si apparié
        return _clean_why(assigned.get(0, ""))
    except Exception:
        return ""

# Date courte FR + maps sport (centralisées ici — étaient copiées dans 3-4 fichiers).
_FR_J = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_FR_M = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
SPORT_EMOJI = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}
SPORT_NAME = {"foot": "Football", "tennis": "Tennis", "basket": "Basket"}


def fr_date(dt) -> str:
    return f"{_FR_J[dt.weekday()]} {dt.day} {_FR_M[dt.month - 1]}"


def _cat(d: dict) -> str:
    sport = d.get("sport")
    sn = SPORT_NAME.get(sport, sport or "")
    return f"{sn} · {d['comp']}" if d.get("comp") else sn


def _local(dt):
    """Convertit un datetime AWARE (UTC) en HEURE BELGE (Europe/Brussels, gère l'heure d'été) pour
    l'AFFICHAGE. La carte stockait `start` en UTC (`...Z`) et le formatait tel quel -> elle affichait
    l'heure UTC (ex. 15:00) au lieu de l'heure locale (17:00). On localise ici, à la source, pour la
    date ET l'heure de TOUTES les cartes (prono + résultat). Explicite Europe/Brussels (pas .astimezone()
    sans arg) -> correct même si un jour hébergé hors du fuseau belge."""
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Brussels")) if dt else dt
    except Exception:
        return dt


def _dt(d):
    try:
        return _local(datetime.fromisoformat((d.get("start") or "").replace("Z", "+00:00")))
    except ValueError:
        return None


def is_settled(d: dict) -> bool:
    return bool((d.get("result") or {}).get("pick_result")) or bool((d.get("combo") or {}).get("result"))


def _simple_common(d: dict, rb: dict, sport, home: str, away: str) -> dict:
    """Champs COMMUNS du pari simple façon SITE — PARTAGÉS par la carte PRONO et la carte RÉSULTAT (user
    2026-08-17 : la carte résultat doit être la MÊME carte complète que le site, pas un layout minimal).
    Rend : pari lisible + glose « en clair », confiance CALIBRÉE, edge/value, cote, tier (Confiance/Value),
    pays + compét., logos des 2 équipes, et l'analyse COMPLÈTE (mêmes puces que « Pourquoi ce choix »)."""
    from app import match_select as _ms, crest as _cr
    _sel = str(rb.get("sel", ""))
    # CONFIANCE PUBLIÉE = confiance CALIBRÉE (décision user 2026-07-17) ; repli brute si pas de cprob.
    _conf = round(rb["cprob"]) if rb.get("cprob") is not None else rb.get("prob")
    _cote = rb.get("cote")
    _edge = _val = None
    try:
        _cf, _c = float(_conf), float(_cote)
        _edge = round(_cf - 100.0 / _c)            # edge = confiance − proba implicite
        _val = round((_cf / 100.0 * _c - 1) * 100)  # value = conf×cote − 1
    except (TypeError, ValueError):
        pass
    try:
        from app import web as _web
        _gloss = _web._bet_gloss(_sel, sport, home, away)
    except Exception:
        _gloss = ""
    _comp = str(d.get("comp") or "")
    try:
        _tier = analyses.bet_tier_for(sport, str(d.get("id")))
    except Exception:
        _tier = "confiance"
    # « POURQUOI » = analyse COMPLÈTE (toutes les puces), IDENTIQUE au pli « Pourquoi ce choix » du site.
    try:
        from app import web as _web2
        _why = _web2._prov_why_snippet(sport, str(d.get("id")), maxlen=100000, played=True)
    except Exception:
        _why = ""
    if not _why:
        _why = _pick_why(d, _sel)
    return {"pick": analyses.pretty_sel(_sel, home, away), "gloss": _gloss,
            "cote": (f"{_cote:g}" if _cote else ""), "conf": _conf, "edge": _edge, "value": _val,
            "tier": _tier, "country": _ms.comp_country(_comp), "comp": _comp,
            "home_logo": (_cr.logo_url(_cr.team_id(home)) or ""),
            "away_logo": (_cr.logo_url(_cr.team_id(away)) or ""), "why": _why}


def build_prono_card(d: dict) -> dict | None:
    """Données de la carte PRONO d'un match (avant-match) depuis le sidecar. None si « calibration
    seule » (rien à afficher). Combiné = combiné seul ; sinon le pari simple retenu / le plus sûr."""
    sport = d.get("sport")
    combo = d.get("combo") or {}
    has_combo = bool(combo.get("legs"))
    pick = d.get("pick") or ""
    rb = analyses.retained_bet(sport, str(d.get("id")))
    # On ne publie un SIMPLE que s'il est RETENU (passe confiance+EV+garde-fous) — combiné OU non.
    # Sinon Telegram postait des paris (favoris sans value, ex. @1.14) que les stats ne comptent PAS
    # -> incohérence. Désormais : posté = compté. Si rien n'est retenu, on s'abstient (pas de carte).
    pick_shown = bool(rb)

    dt = _dt(d)
    meta = f"{fr_date(dt)} · {dt.strftime('%H:%M')}" if dt else ""
    card = {"emoji": SPORT_EMOJI.get(sport, "•"), "_mid": str(d.get("id")), "_sport": sport,
            "_start": str(d.get("start") or ""), "cat": _cat(d),
            "match": str(d.get("name", "")).replace(" - ", " — "), "meta": meta}
    home, away = str(d.get("home", "")), str(d.get("away", ""))
    if has_combo:
        cote = (f"{combo['real_odds']:.2f}" if combo.get("real_odds") else f"{combo.get('total', '?')}")
        # ANALYSE PAR JAMBE (comme l'app) : chaque sélection porte son « pourquoi » sérieux + la synthèse
        # du combiné (corrélation). Uniquement sur la carte de PUBLICATION (pas la carte résultat).
        # La sélection est scindée en (marché, pick) pour un affichage sur 2 lignes.
        _legs = []
        for l in combo["legs"]:
            mkt, pk = _split_leg(l.get("sel", ""), home, away)
            _legs.append((mkt, pk, str(l.get("cote", "")), _clean_why(l.get("why"))))
        card.update(type="combo", cote=cote, legs=_legs, synth=_clean_synth(combo.get("why")))
    elif pick_shown and rb:
        # Champs « design du site » (pari + glose + confiance/edge/value + tier + logos + analyse complète).
        card.update(type="simple", market="", home=home, away=away,
                    **_simple_common(d, rb, sport, home, away))
    elif pick_shown:
        m = re.search(r"(.+?)\s*@\s*([\d]+[.,][\d]+)", pick)
        card.update(type="simple", pick=(m.group(1).strip() if m else pick),
                    cote=(m.group(2).replace(",", ".") if m else ""), conf=None)
    else:
        return None
    return card


def build_result_card(d: dict) -> dict | None:
    """Données de la carte RÉSULTAT d'un sidecar réglé (score + verdict par jambe/global). None si rien
    de réglé à montrer. Le simple n'est inclus que s'il est AFFICHÉ (cohérence avec la carte prono)."""
    sport = d.get("sport")
    combo = d.get("combo") or {}
    has_combo = bool(combo.get("legs"))
    res = d.get("result") or {}
    pick_result = res.get("pick_result")
    combo_result = combo.get("result")
    # Cohérent avec la carte prono (posté = réglé) : `build_prono_card` n'affiche QUE le combiné quand il
    # y en a un (le simple est masqué). La carte résultat doit REFLÉTER ça -> le simple n'est montré que
    # s'il N'Y A PAS de combiné ET qu'il était RETENU. Sinon on ajoutait un pari simple JAMAIS proposé
    # (souvent un doublon d'une jambe du combiné) -> incohérence vécue Suisse-Colombie (« Moins de 2.5 »
    # affiché en simple ET en jambe 1). Un simple non retenu n'a pas de carte prono -> pas de résultat non plus.
    simple_shown = (not has_combo) and analyses.retained_bet(sport, str(d.get("id"))) is not None

    card_simple = card_combo = None
    _simple_extra: dict = {}
    _home, _away = str(d.get("home", "")), str(d.get("away", ""))
    if pick_result and simple_shown:
        rb = analyses.retained_bet(sport, str(d.get("id"))) or {}
        # MÊME carte complète que le site (user 2026-08-17) : on enrichit avec TOUS les champs du prono
        # (grille Confiance/Edge/Value/Cote, tier, analyse) -> la carte résultat = la carte de pari + le
        # verdict Gagné/Perdu, pas un layout minimal. Le TYPE de pari (Confiance/Value) reste la signature.
        _simple_extra = _simple_common(d, rb, sport, _home, _away)
        card_simple = {"label": _simple_extra["pick"] or "Pari simple", "gloss": _simple_extra["gloss"],
                       "tier": _simple_extra["tier"], "cote": _simple_extra["cote"], "mark": pick_result}
    if combo_result:
        cco = combo.get("real_odds") or combo.get("total")
        card_combo = {"cote": (f"{cco:.2f}" if isinstance(cco, float) else str(cco or "")),
                      "mark": combo_result,
                      "legs": [(str(l.get("sel", "")), l.get("result"), l.get("cote") or "")
                               for l in combo.get("legs", [])]}
    if not (card_simple or card_combo):
        return None
    dt = _dt(d)
    meta = f"terminé · {fr_date(dt)} · {dt.strftime('%H:%M')}" if dt else "terminé"
    # `_simple_extra` (design site : conf/edge/value/cote/tier/logos/pays/analyse) est fusionné au TOP-LEVEL
    # -> la carte RÉSULTAT SIMPLE se rend comme la carte de pari. Vide (combiné) -> pas de champs simples.
    return {"emoji": SPORT_EMOJI.get(sport, "•"), "_mid": str(d.get("id")), "cat": _cat(d),
            "match": str(d.get("name", "")).replace(" - ", " — "), "meta": meta,
            "type": "result", "score": res.get("score") or "",
            "home": _home, "away": _away,
            "simple": card_simple, "combo": card_combo, **_simple_extra}


def build_combo_daily_card(combo: dict, *, result: bool = False) -> dict | None:
    """Carte du COMBINÉ DU JOUR (combo_daily) pour Telegram — construite EXACTEMENT comme les autres paris
    (user 2026-08-18) : signature « COMBINÉ », puis CHAQUE jambe rendue comme une mini-carte de pari complète
    (ligue, logos + équipes + heure/score, pari + glose, grille Confiance/Edge/Value/Cote, analyse ou verdict),
    et la COTE combinée. `result=True` -> verdict Gagné/Perdu par jambe + global, sans analyse. None si vide."""
    if not combo or not combo.get("legs"):
        return None
    from app import match_select as _ms, crest as _cr
    try:
        from app import web as _web
    except Exception:
        _web = None
    legs = []
    for l in combo["legs"]:
        home, away = str(l.get("home", "")), str(l.get("away", ""))
        _comp = str(l.get("comp") or "")
        _sel = str(l.get("sel", ""))
        _pr = l.get("prob")
        _conf = (round(_pr * 100) if isinstance(_pr, (int, float)) and _pr <= 1
                 else (round(_pr) if isinstance(_pr, (int, float)) else None))
        _cote = l.get("cote")
        # COMME LE SITE (user 2026-08-18) : les jambes de combiné n'affichent QUE Confiance + Cote (Edge/Value
        # masqués — la DC sûre a un edge ~neutre/négatif, hors-sujet sur un combiné sécurité). -> edge/value None.
        _dt2 = None
        try:
            _dt2 = _local(datetime.fromisoformat(str(l.get("start") or "").replace("Z", "+00:00")))
        except ValueError:
            pass
        try:
            _gloss = _web._bet_gloss(_sel, "foot", home, away) if _web else ""
        except Exception:
            _gloss = ""
        legs.append({
            "home": home, "away": away,
            "home_logo": (_cr.logo_url(_cr.team_id(home)) or ""),
            "away_logo": (_cr.logo_url(_cr.team_id(away)) or ""),
            "lg": " • ".join(x for x in (_ms.comp_country(_comp), _comp) if x).upper(),
            "time": (_dt2.strftime("%H:%M") if _dt2 else ""),
            "score": (str(l.get("score") or "") if result else ""),
            "pick": analyses.pretty_sel(_sel, home, away), "gloss": _gloss,
            "conf": _conf, "edge": None, "value": None, "cote": (f"{_cote:g}" if _cote else ""),
            "why": ("" if result else _clean_why(l.get("why"))),   # pas d'analyse sur le résultat
            "mark": (l.get("result") if result else None),
        })
    _cote = combo.get("real_odds") or combo.get("cote") or combo.get("total")
    return {"emoji": "⚽", "type": ("combo_result" if result else "combo"), "combo_title": "COMBINÉ",
            "cote": (f"{_cote:.2f}" if isinstance(_cote, float) else str(_cote or "")),
            "legs": legs,
            "synth": ("" if result else _clean_synth(combo.get("synth") or combo.get("why"))),
            "combo_mark": (combo.get("result") if result else None)}


def build_montante_card(step: dict, *, result: bool = False) -> dict | None:
    """Carte MONTANTE pour Telegram (user 2026-08-18) : MÊME carte qu'un pari simple (signature « MONTANTE »),
    ÉPURÉE (Confiance + Cote, pas Edge/Value — comme le combiné). `step` = palier montante (montante_track).
    `result=True` -> carte RÉSULTAT (score + Gagné/Perdu). None si vide."""
    if not step or not step.get("sel"):
        return None
    from app import match_select as _ms, crest as _cr
    home, away = str(step.get("home") or ""), str(step.get("away") or "")
    if not (home and away) and step.get("match"):
        home, _sep, away = str(step.get("match")).partition(" - ")
        home, away = home.strip(), away.strip()
    _sel = str(step.get("sel", ""))
    _comp = str(step.get("comp") or "")
    _pr = step.get("prob")
    _conf = (round(_pr * 100) if isinstance(_pr, (int, float)) and _pr <= 1
             else (round(_pr) if isinstance(_pr, (int, float)) else None))
    _cote = step.get("cote")
    try:
        from app import web as _web
        _gloss = _web._bet_gloss(_sel, "foot", home, away)
    except Exception:
        _gloss = ""
    _pretty = analyses.pretty_sel(_sel, home, away)
    common = {"emoji": "⚽", "_mid": str(step.get("mid") or ""),
              "cat": (f"Football · {_comp}" if _comp else "Football"),
              "match": (step.get("match") or f"{home} - {away}").replace(" - ", " — "),
              "home": home, "away": away, "country": _ms.comp_country(_comp), "comp": _comp,
              "home_logo": (_cr.logo_url(_cr.team_id(home)) or ""),
              "away_logo": (_cr.logo_url(_cr.team_id(away)) or ""),
              "tier": "montante", "conf": _conf, "edge": None, "value": None,   # ÉPURÉ : Confiance + Cote
              "cote": (f"{_cote:g}" if _cote else "")}
    if result:
        return {**common, "type": "result", "score": str(step.get("score") or ""),
                "pick": _pretty, "gloss": _gloss,
                "simple": {"label": _pretty, "gloss": _gloss, "tier": "montante",
                           "cote": common["cote"], "mark": step.get("result")}}
    return {**common, "type": "simple", "pick": _pretty, "gloss": _gloss, "why": step.get("why") or ""}
