"""Analyses « analyste » pré-générées (par tools/generate_analyses.py via Claude headless).

Chargement depuis data/analyses/{sport}_{id}.md (id = clé du store = id Unibet) + rendu
markdown -> HTML pour l'affichage en fiche match.

Rendu STRUCTURÉ quand l'analyse suit le gabarit analyste (## 🎯 Verdict + ## 📊 tableau) :
carte Verdict en tête -> tableau des paris (barre proba + pastille risque) -> faits repliables.
Repli sur un rendu markdown générique pour tout autre format (templated, ancien). Aucune
dépendance externe ; la teinte vient de var(--accent) (défini par sport via body.sp-*).
"""

from __future__ import annotations

import glob
import html
import json
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import lru_cache

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(_ROOT, "data", "analyses")

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LIST = re.compile(r"^\s*([-*]|\d+[.)])\s+")
_BLOCK = re.compile(r"^(#{1,6}\s|\s*[-*]\s|\s*\d+[.)]\s|>|\|)")
_MAX_BETS = 1   # UN SEUL pari par match : le PLUS PROBABLE de tout le marché (qualité > quantité,
#                 choix utilisateur 2026-06-16). Le tableau étant ordonné du + au − probable, on garde
#                 la 1re ligne. (Hors combiné CdM, géré à part.)


_FID_CACHE: dict = {}   # sport -> (signature_dossier, {sofa_id: fid}) — index mis en cache


def _dir_sig() -> tuple:
    """Signature (noms + mtimes ns) de TOUS les sidecars — clé d'invalidation des caches agrégés
    (calibration, stats). Un scandir par appel : exact et bien moins cher que re-parser N JSON."""
    try:
        return tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(DIR)
                            if e.name.endswith(".json")))
    except OSError:
        return ()


def _fid_index(sport: str) -> dict:
    """Index {sofa_id: id_de_fichier} d'un sport, MIS EN CACHE et invalidé dès que le dossier change
    (noms + mtimes). Évite de globber+charger TOUS les sidecars à CHAQUE résolution (O(cartes×fichiers)
    -> O(fichiers) une fois)."""
    try:
        entries = sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(DIR)
                         if e.name.startswith(f"{sport}_") and e.name.endswith(".json"))
    except OSError:
        return {}
    sig = tuple(entries)
    hit = _FID_CACHE.get(sport)
    if hit and hit[0] == sig:
        return hit[1]
    pre, idx = len(sport) + 1, {}
    for name, _ in entries:
        d = _meta_load(os.path.join(DIR, name))
        sid = str((d or {}).get("sofa_id") or "")
        if sid:
            idx[sid] = name[pre:-5]
    _FID_CACHE[sport] = (sig, idx)
    return idx


def _resolve_fid(sport: str, fiche_id):
    """Vrai id de FICHIER sidecar pour un id de fiche (qui peut être l'id direct OU un `sofa_id`).
    Les cartes foot mettent parfois le sofa_id dans l'URL alors que le fichier est `foot_{id}` ->
    on retombe sur le bon fichier via l'index `sofa_id`. Renvoie l'id tel quel si rien trouvé."""
    if fiche_id is None:
        return None
    if os.path.exists(os.path.join(DIR, f"{sport}_{fiche_id}.json")):
        return fiche_id
    return _fid_index(sport).get(str(fiche_id), fiche_id)


# Caches mtime PAR FICHIER : les pages relisent les MÊMES sidecars/analyses des dizaines de fois par
# requête (boards, cartes, stats, simulation). Tant que le fichier n'a pas changé sur disque, on évite
# re-open + re-parse (gros gain CPU/IO, invalidation automatique dès qu'un scan/règlement réécrit).
_MD_CACHE: dict[str, tuple[float, str]] = {}     # path -> (mtime, markdown)
_META_CACHE: dict[str, tuple[float, object]] = {}  # path -> (mtime, dict parsé)


def _md_read(path: str) -> str | None:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _MD_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return None
    _MD_CACHE[path] = (mtime, txt)
    return txt


def load(sport: str, match_id) -> str | None:
    """Markdown de l'analyse pour ce match (None si absente). Résout aussi les id `sofa_id`."""
    if match_id is None:
        return None
    txt = _md_read(os.path.join(DIR, f"{sport}_{match_id}.md"))
    if txt is not None:
        return txt
    rid = _resolve_fid(sport, match_id)
    if rid is None or str(rid) == str(match_id):
        return None
    return _md_read(os.path.join(DIR, f"{sport}_{rid}.md"))


def _meta_load(path: str) -> dict | None:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _META_CACHE.get(path)
    if hit and hit[0] == mtime:
        d = hit[1]
    else:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        _META_CACHE[path] = (mtime, d)
    # copie superficielle : les appelants posent des clés de travail (`_start_dt`…) sur le dict
    # retourné — elles ne doivent pas polluer le cache partagé.
    return dict(d) if isinstance(d, dict) else d


def meta(sport: str, fiche_id) -> dict | None:
    """Métadonnées (sidecar JSON) d'une analyse : équipes, compétition, cotes, id Sofa. None si absent.
    Cherche par id de fiche OU par id Sofa (la fiche peut être ouverte par l'un ou l'autre)."""
    if fiche_id is None:
        return None
    direct = _meta_load(os.path.join(DIR, f"{sport}_{fiche_id}.json"))
    if direct:
        return direct
    rid = _fid_index(sport).get(str(fiche_id))   # repli par id Sofa (index caché, pas de glob/carte)
    return _meta_load(os.path.join(DIR, f"{sport}_{rid}.json")) if rid else None


_DUR_MIN = {"foot": 130, "basket": 150, "tennis": 210}   # durée ~ d'un match (min) par sport
# Durée MINIMALE plausible d'un match : au-delà, un « en cours » SANS live Unibet est considéré FINI
# (le live a disparu = match terminé ; ou nom non matché mais assez de temps écoulé) -> il bascule en
# « Terminés » (résultat « en attente » s'il n'est pas encore réglé) au lieu de rester invisible.
_MIN_DONE_MIN = {"foot": 105, "basket": 110, "tennis": 80}


def likely_finished(d: dict, now=None) -> bool:
    """Un match « en cours » par l'horloge a-t-il probablement fini (assez de temps écoulé) ? Sert à
    sortir de l'invisibilité un match terminé non réglé quand Unibet n'a pas/plus de live data.
    Parse `start` en repli si `_start_dt` absent (cas du règlement qui charge le sidecar brut)."""
    dt = d.get("_start_dt")
    if dt is None and d.get("start"):
        try:
            dt = datetime.fromisoformat(str(d["start"]).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            dt = None
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 60 >= _MIN_DONE_MIN.get(d.get("sport"), 105)


def status_of(d: dict, now=None) -> str:
    """Statut d'un match analysé d'après son coup d'envoi (sans appel réseau) :
    'notstarted' (à venir) / 'inprogress' (en cours) / 'finished' (terminé)."""
    st = d.get("start")
    dt = d.get("_start_dt")
    if dt is None and st:
        try:
            dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            dt = None
    if dt is None:
        return "notstarted"
    now = now or datetime.now(timezone.utc)
    if now < dt:
        return "notstarted"
    if is_settled(d):                    # réglé = réellement terminé (sans attendre la fenêtre de durée)
        return "finished"
    if now < dt + timedelta(minutes=_DUR_MIN.get(d.get("sport"), 150)):
        return "inprogress"
    return "finished"


def votes_pct(d: dict) -> tuple | None:
    """Votes du sidecar (fractions pub_*) -> (%home, %away, %draw) pour la barre Public. None sinon."""
    ph = (d or {}).get("pub_home")
    if ph is None:
        return None
    pa, pd = d.get("pub_away"), d.get("pub_draw")
    return (ph * 100, (pa * 100 if pa is not None else None), (pd * 100 if pd is not None else None))


def pick_parts(pick: str) -> tuple[str, float | None]:
    """Découpe « Sélection @ 1.13 » (ou « … à 1.13 ») -> ('Sélection', 1.13). Cote None si absente."""
    m = re.search(r"(.+?)\s*(?:@|à)\s*([\d]+[.,][\d]+)", pick or "")
    if not m:
        return (pick or "").strip(), None
    try:
        return m.group(1).strip(), float(m.group(2).replace(",", "."))
    except ValueError:
        return m.group(1).strip(), None


_WC_TOURNEYS = ("coupe du monde", "world cup")   # Coupe du Monde 2026 (FR + EN)


def _is_world_cup(d: dict) -> bool:
    """Le match est-il un match de Coupe du Monde ? Les matchs CdM sont EXCLUS de TOUTES les stats
    (suivi, ROI, calibration, perf, drill-down) — décision produit : on continue à AFFICHER leurs
    combinés sur la fiche, mais ils ne pèsent dans AUCUN agrégat. Clé sur la compétition (robuste :
    couvre même un éventuel match CdM sans combiné). Rétroactif : s'applique aux matchs déjà joués."""
    return any(t in (d.get("comp") or "").lower() for t in _WC_TOURNEYS)


def is_settled(d: dict) -> bool:
    """Le match a-t-il un résultat COMPTÉ dans les stats ? (pari « le plus sûr » réglé OU au moins un
    pari réglé). Sert à garder ces matchs visibles dans « Terminés » même longtemps après la fin."""
    if (d.get("result") or {}).get("pick_result") is not None:
        return True
    if (d.get("combo") or {}).get("result") in ("won", "lost", "push"):   # combiné CdM réglé = terminé
        return True
    return any(b.get("result") in ("won", "lost", "push") for b in (d.get("bets") or []))


def list_for(sport: str) -> list[dict]:
    """Liste des matchs ANALYSÉS (sidecars) à venir / récents, triés par coup d'envoi.
    C'est la SOURCE du board : seuls les matchs analysés avec la nouvelle technique y figurent."""
    now = datetime.now(timezone.utc)
    out = []
    for p in glob.glob(os.path.join(DIR, f"{sport}_*.json")):
        d = _meta_load(p)
        if not d:
            continue
        st = d.get("start")
        try:
            dt = datetime.fromisoformat(st.replace("Z", "+00:00")) if st else None
        except (ValueError, AttributeError):
            dt = None
        # on garde l'à-venir, l'en-cours ET les terminés. Les matchs RÉGLÉS (présents dans les stats)
        # restent visibles indéfiniment dans « Terminés » ; on ne jette que les NON réglés trop vieux
        # (> ~6 h après le coup d'envoi : match fini depuis longtemps sans résultat exploitable).
        if dt is not None and dt < now - timedelta(hours=6) and not is_settled(d):
            continue
        # Mode strict : un match analysé SANS AUCUN pari ≥ seuil (SKIP assumé) n'apparaît PLUS dans
        # l'app (demande utilisateur 2026-06-12) — on ne montre que ce qui se joue. (Le sidecar et
        # le .md restent sur disque : cache du scan, pas de re-analyse inutile.)
        if not is_settled(d) and load(sport, d.get("id")) is not None \
                and not bets_of(sport, d.get("id")):
            continue
        d["_start_dt"] = dt
        out.append(d)
    out.sort(key=lambda d: (d["_start_dt"] is None, d.get("_start_dt") or now))
    return out


def _inline(s: str) -> str:
    s = html.escape(s)
    s = _BOLD.sub(r"<b>\1</b>", s)
    s = _LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


def _strip(md: str) -> str:
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)          # vire l'en-tête commentaire
    md = re.sub(r"^---+\s*$", "", md, flags=re.M)            # séparateurs ---
    md = re.sub(r"^\s*PICK:.*$", "", md, flags=re.M)         # ligne technique de règlement (cachée)
    return md


# --------------------------------------------------------------- rendu générique
def _table(rows: list, cap: int | None = None) -> str:
    """`rows` = lignes « | a | b | » ; la 2e ligne est le séparateur |---| (ignoré)."""
    def cells(r):
        return [c.strip() for c in r.strip().strip("|").split("|")]
    if len(rows) < 2:
        return ""
    head = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    if cap is not None:
        body = body[:cap]
    th = "".join(f"<th>{_inline(c)}</th>" for c in head)
    trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return f'<table class="da-tbl"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def _render_blocks(md: str) -> str:
    """Markdown -> HTML (titres, gras, listes, tableaux, citations, liens). Sous-ensemble analyste."""
    out, i, lines = [], 0, md.splitlines()
    n = len(lines)
    while i < n:
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        if ln.lstrip().startswith("|"):                     # tableau
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            out.append(_table(rows))
            continue
        m = re.match(r"(#{1,6})\s+(.*)", ln)                 # titre
        if m:
            lvl = min(len(m.group(1)), 3)
            out.append(f'<div class="da-h da-h{lvl}">{_inline(m.group(2))}</div>')
            i += 1
            continue
        if ln.lstrip().startswith(">"):                     # citation
            out.append(f'<div class="da-quote">{_inline(ln.lstrip("> ").strip())}</div>')
            i += 1
            continue
        if _LIST.match(ln):                                 # liste
            items = []
            while i < n and _LIST.match(lines[i]):
                items.append("<li>" + _inline(_LIST.sub("", lines[i]).rstrip()) + "</li>")
                i += 1
            out.append("<ul class='da-ul'>" + "".join(items) + "</ul>")
            continue
        para = [ln]                                         # paragraphe
        i += 1
        while i < n and lines[i].strip() and not _BLOCK.match(lines[i]):
            para.append(lines[i].rstrip())
            i += 1
        out.append("<p class='da-p'>" + _inline(" ".join(para)) + "</p>")
    return "".join(out)


# --------------------------------------------------------------- rendu structuré
def _sections(md: str) -> dict:
    """Découpe par titres `## ...` -> {titre: corps}. Le pré-texte (avant tout ##) sous la clé ''."""
    secs, cur = {"": []}, ""
    for ln in md.splitlines():
        m = re.match(r"##\s+(.*)", ln.strip())
        if m:
            cur = m.group(1).strip()
            secs[cur] = []
        else:
            secs.setdefault(cur, []).append(ln)
    return {k: "\n".join(v).strip() for k, v in secs.items()}


def _find(secs: dict, *needles: str) -> str:
    for title, body in secs.items():
        low = title.lower()
        if any(nd in title or nd in low for nd in needles):
            return body
    return ""


def _bullets(body: str) -> list[str]:
    return [_LIST.sub("", ln).strip() for ln in body.splitlines() if _LIST.match(ln.strip())]


def _vc_icon(label: str) -> tuple[str, str]:
    """(emoji, classe) selon le type de puce du Verdict."""
    low = label.lower()
    if "plus s" in low:
        return "🎯", "safe"
    if "compromis" in low or "rendement" in low or "rapport" in low:
        return "⚖️", "mid"
    if "évit" in low or "skip" in low or "éviter" in low:
        return "🚫", "skip"
    return "•", "mid"


def _odds_in(text: str) -> str:
    m = re.search(r"@\s*\*?\*?\s*([\d]+[.,]\d+)", text)
    return m.group(1) if m else ""


def _verdict_card(verdict: str, mise: str) -> str:
    """Carte Verdict premium : « le plus sûr » en HÉRO (pick large + cote + raison), puis les
    autres lignes avec icône. Teinte sport via var(--accent)."""
    items = _bullets(verdict)
    if not items:
        return ""
    rows = []
    for k, it in enumerate(items):
        label, _, content = it.partition(":")
        label = re.sub(r"\*", "", label).strip()
        content = re.sub(r"\*", "", content).strip() or label   # gras markdown retiré (re-stylé)
        icon, kind = _vc_icon(label)
        odds = _odds_in(content)
        odds_html = f'<span class="da-vc-odds">{html.escape(odds)}</span>' if odds else ""
        if k == 0:   # le plus sûr -> héro
            pick = re.split(r"\s*@", content)[0].strip().rstrip("(").strip()
            why = re.sub(r"^.*?@\s*[\d.,]+\s*", "", content).strip(" ().—–-").strip()
            rows.append(
                '<div class="da-vc-top">'
                f'<div class="da-vc-lbl">{icon} {html.escape(label)}</div>'
                f'<div class="da-vc-pick">{_inline(pick)}{odds_html}</div>'
                + (f'<div class="da-vc-why">{_inline(why)}</div>' if why else "")
                + '</div>')
        else:
            disp = re.sub(r"\s*@\s*[\d.,]+", "", content).strip() if odds else content
            rows.append(
                f'<div class="da-vc-row da-vc-{kind}"><span class="da-vc-ic">{icon}</span>'
                f'<span><b>{html.escape(label)}</b> {_inline(disp)}{odds_html}</span></div>')
    mise_html = (f'<div class="da-mise"><span class="da-mise-ic">💰</span>'
                 f'<span>{_inline(_strip(mise).strip())}</span></div>'
                 if mise.strip() else "")
    return ('<div class="da-vc"><div class="da-vc-h">🎯 Verdict</div>'
            + "".join(rows) + mise_html + "</div>")


_RISK = (("🟢", "ok"), ("🟠", "mid"), ("🔴", "hi"))
_SAFETY = {"ok": "Sûreté élevée", "mid": "Sûreté moyenne", "hi": "Sûreté faible"}
_BET_LABELS = ("Pari 1", "Pari 2", "Pari 3")


def _norm_sel(s: str) -> str:
    """Clé de correspondance d'une sélection de pari (insensible casse/espaces/gras markdown)."""
    return re.sub(r"\s+", " ", _BOLD.sub(r"\1", s or "")).strip().lower()


def _parse_bets(body: str) -> list[dict]:
    """Tableau markdown des paris -> liste STRUCTURÉE et ORDONNÉE (pari 1/2/3) :
    [{sel, cote(float|None), cote_txt, prob(int|None), risk_cls}]. Filtre cotes < 1.10 et 🔴
    risqué, plafonne à `_MAX_BETS`. Source unique pour le rendu ET le règlement par pari."""
    # mémoïsé : le même corps markdown est re-parsé des dizaines de fois par page (cartes, reco,
    # règlement). Copie par appel : les dicts retournés ne doivent pas être partagés/mutés.
    return [dict(b) for b in _parse_bets_cached(body)]


@lru_cache(maxsize=512)
def _parse_bets_cached(body: str) -> tuple:
    rows = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return ()

    def cells(r):
        return [c.strip() for c in r.strip().strip("|").split("|")]
    out = []
    for c in (cells(r) for r in rows[2:] if set(r.replace("|", "").strip()) - set("-: ")):
        cote_txt = c[1] if len(c) > 1 else ""
        m = re.search(r"[\d]+[.,][\d]+", cote_txt)
        cote = None
        if m:
            try:
                cote = float(m.group(0).replace(",", "."))
            except ValueError:
                cote = None
        risk_cell = c[3] if len(c) > 3 else ""
        # On écarte cotes < 1.10 (gain négligeable) ET paris 🔴 risqué (pas une vraie reco).
        if (cote or 9) < 1.10 or "🔴" in risk_cell:
            continue
        prob_cell = c[2] if len(c) > 2 else ""
        pm = re.search(r"(\d{1,3})", prob_cell)
        # Ligne SANS cote NI proba = pas un pari (ex. note « aucun pari ne franchit le seuil »
        # écrite dans le tableau par l'analyste en mode strict) -> ignorée.
        if cote is None and not pm:
            continue
        out.append({
            "sel": c[0] if len(c) > 0 else "", "cote": cote, "cote_txt": cote_txt,
            "prob": min(int(pm.group(1)), 100) if pm else None,
            "risk_cls": next((cls for emo, cls in _RISK if emo in risk_cell), "mid"),
        })
    return tuple(out[:_MAX_BETS])


_SAFE_EMO = {"ok": "🟢", "mid": "🟠", "hi": "🔴"}


def _ev_chip(prob, cote) -> str:
    """Indicateur EV/VALUE d'un pari = proba × cote − 1. C'est CE qui fait grimper le ROI à long
    terme (≠ « sûreté »). 📈 vert = value (EV+) ; ≈ marché ; ⚠️ ambre = cote chère (EV−, sûr mais
    perdant à long terme). '' si proba/cote manquante."""
    if not prob or not cote or cote <= 1:
        return ""
    ev = round((prob / 100 * cote - 1) * 100)
    if ev >= 3:
        return f'<span class="da-ev pos">📈 Value +{ev}%</span>'
    if ev <= -3:
        return f'<span class="da-ev neg">⚠️ EV {ev}%</span>'
    return '<span class="da-ev neu">≈ marché</span>'


_MIN_CONF = 65   # seuil de confiance MINI pour recommander (calibration réelle : sous 65 %, le système
#                  est sur-confiant et perd ; à partir de 65 % il est fiable). Pas de repli en-dessous.


_BAD_MARKETS = {"Total +/-", "Total équipe"}   # ROI mesuré -16% / -30% (n≥25) -> hors recommandation ⭐


def _recommend(data: list, ok: set | None = None, cprobs: list | None = None,
               codes: list | None = None) -> dict:
    """Choisit LE pari à jouer pour faire grimper le portefeuille : meilleure VALUE (EV = proba×cote−1)
    parmi les paris VRAIMENT fiables (proba ≥ 65 %, cf. _MIN_CONF — calibré sur l'historique). Joue si
    EV ≥ +3 %, sinon SKIP. `stake_pct` = mise conseillée en % de bankroll (¼ Kelly plafonné à 3 %).
    `ok` (optionnel) = indices des paris RÉGLABLES : si fourni, on ne recommande QUE ceux-là (un pari
    qu'on ne sait pas régler ne doit jamais entrer en simulation — sinon le track-record est faux).
    `cprobs` (optionnel) : confiances RECALIBRÉES par bet (boucle de feedback) -> EV/Kelly calculés
    dessus ; à défaut, la confiance brute de l'analyste. Renvoie {idx, ev, verdict, stake_pct}."""
    def _cp(i, b):       # confiance recalibrée si fournie, sinon brute
        return cprobs[i] if (cprobs and i < len(cprobs) and cprobs[i] is not None) else b["prob"]
    scored = [(i, _cp(i, b) / 100 * b["cote"] - 1, _cp(i, b))
              for i, b in enumerate(data)
              if b.get("prob") and b.get("cote") and (ok is None or i in ok)]
    # Confiance ≥ 65 % EXIGÉE (sinon on s'abstient). GARDE-FOUS mesurés (perf_breakdown 2026-06-15) :
    #  • cote 1.70-2.00 = ROI -32 % -> on exige 72 % de confiance recalibrée dans cette zone ;
    #  • cote ≥ 2.00 = ROI -13 % -> exclue de la reco (les grosses cotes saignent) ;
    #  • marchés « Total +/- » (-16 %) et « Total équipe » (-30 %) -> exclus (`_BAD_MARKETS`).
    def _mk(i):
        return market_of(codes[i]) if (codes and i < len(codes) and codes[i]) else None
    pool = [s for s in scored
            if s[2] >= _MIN_CONF
            and (data[s[0]].get("cote") or 0) < 2.00
            and ((data[s[0]].get("cote") or 0) < 1.70 or s[2] >= 72)
            and _mk(s[0]) not in _BAD_MARKETS]
    if not pool:
        return {"idx": None, "verdict": "skip", "ev": None, "stake_pct": 0.0}
    i, ev, _prob = max(pool, key=lambda s: s[1])
    if ev < 0.03:
        return {"idx": None, "verdict": "skip", "ev": round(ev * 100), "stake_pct": 0.0}
    b = data[i]["cote"] - 1
    kelly = ev / b if b > 0 else 0.0                        # Kelly complet
    stake_pct = round(max(0.0, min(kelly * 0.25, 0.03)) * 100, 1)   # ¼ Kelly, plafond 3 %
    return {"idx": i, "verdict": "play", "ev": round(ev * 100), "stake_pct": stake_pct}


# Mots GÉNÉRIQUES de marché (ne discriminent PAS un pari d'un autre) -> on matche sur les ENTITÉS
# (noms de joueur/équipe) + les NOMBRES (lignes de total/handicap).
_GEN_WORDS = {"plus", "moins", "over", "under", "total", "buts", "but", "points", "point", "jeux",
              "jeu", "set", "sets", "corners", "corner", "cartons", "carton", "vainqueur", "match",
              "remporte", "gagne", "double", "chance", "handicap", "temps", "exact", "score", "tie",
              "break", "les", "des", "une", "pour", "avec", "dans", "sur", "par", "entre", "premier",
              "first", "moitie", "mitemps"}


def _deacc(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def _strip_sources(s: str) -> str:
    """Retire la liste « Sources : … » en fin de texte (clutter sur les cartes ; les sources restent
    dans « Les faits » de l'analyse)."""
    return re.sub(r"\s*(?:[·\-—–]\s*)?sources?\s*:.*$", "", s or "", flags=re.I | re.S).strip()


def _units_to_pct(s: str) -> str:
    """Exprime les mises en % DE BANKROLL et JAMAIS en « unités »/« u » (exigence utilisateur).
    Couvre les analyses déjà générées au rendu ; le prompt de scan est aligné pour le futur."""
    if not s:
        return s
    t = s
    # « 1 u = 2% de bankroll (fixe) » -> garde le %, supprime la définition d'unité
    t = re.sub(r"\d+(?:[.,]\d+)?\s*u\.?\s*=\s*(\d+(?:[.,]\d+)?\s*%)\s*(?:fixe\s+)?de\s+(?:la\s+)?bankroll",
               r"\1 de la bankroll", t, flags=re.I)
    # « 1 u = % fixe de bankroll » (sans chiffre) -> « un % fixe de la bankroll »
    t = re.sub(r"\d+(?:[.,]\d+)?\s*u\.?\s*=\s*%\s*fixe\s+de\s+(?:la\s+)?bankroll",
               "un % fixe de la bankroll", t, flags=re.I)
    # « 0,5 u », « 1 u », « 2 unités » restants -> « X % de la bankroll »
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*(?:u\.?|unit[ée]s?)\b", r"\1 % de la bankroll", t, flags=re.I)
    # « unité(s) » isolé -> « % de la bankroll »
    t = re.sub(r"\bunit[ée]s?\b", "% de la bankroll", t, flags=re.I)
    return t


def _sentence_case(s: str) -> str:
    """Majuscule en DÉBUT de phrase : 1re lettre + après . ! ? (texte d'analyse bien mis en forme)."""
    s = (s or "").strip()
    if not s:
        return s
    s = s[0].upper() + s[1:]
    return re.sub(r"([.!?]\s+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), s)


def _keys(s: str) -> tuple[set, set]:
    """(entités, nombres) significatifs d'une sélection -> pour rapprocher un commentaire Verdict du
    bon pari. Entités = noms (joueur/équipe), nombres = lignes (2.5, 9…)."""
    toks = re.findall(r"[a-z]{3,}|\d+(?:[.,]\d+)?", _deacc(s).lower())
    names = {t for t in toks if not t[0].isdigit() and t not in _GEN_WORDS}
    nums = {t.replace(",", ".") for t in toks if t[0].isdigit()}
    return names, nums


def _assign_notes(sels: list, notes: list) -> dict:
    """Affecte chaque commentaire du Verdict (dans l'ordre) à SON pari : meilleur score (entités×2 +
    nombres×2 -> matche aussi bien « X vainqueur » que « Plus de 166 pts »), sinon repli sur l'ORDRE
    (les puces Verdict suivent l'ordre des paris). Renvoie {index_pari: pourquoi}."""
    if not notes:
        return {}
    keyed = [_keys(s) for s in sels]
    assigned, used = {}, set()
    for nsel, why in notes:
        nn, nx = _keys(nsel)
        best_i, best_s = None, -1
        for i in range(len(sels)):
            if i in used:
                continue
            bn, bx = keyed[i]
            sc = 2 * len(bn & nn) + 2 * len(bx & nx)   # ex æquo (souvent score 0) -> 1er libre = ordre
            if sc > best_s:
                best_s, best_i = sc, i
        if best_i is None:
            break
        assigned[best_i] = why
        used.add(best_i)
    return assigned


def _verdict_notes(md: str) -> tuple[list, str]:
    """Découpe le Verdict : (notes, residu_html). `notes` = [(sélection, pourquoi)] des PICKS (le plus
    sûr, compromis…) à coller SOUS le pari correspondant ; `residu_html` = « à éviter / SKIP » + « Mise »
    (conseil général, sans pari précis) rendu en petit, à mettre APRÈS les cartes de pari."""
    secs = _sections(_strip(md))
    verdict = _find(secs, "🎯", "Verdict")
    mise = _find(secs, "💰", "Mise")
    notes, resid = [], []
    for it in _bullets(verdict):
        label, _, content = it.partition(":")
        label = re.sub(r"\*", "", label).strip()
        content = re.sub(r"\*", "", content).strip() or label
        why = re.sub(r"^.*?@\s*[\d.,]+\s*", "", content).strip(" ().—–-").strip()
        low = label.lower()
        if "évit" in low or "skip" in low or "evit" in low:
            resid.append(("⛔", "À éviter / Skip", _sentence_case(_units_to_pct(_strip_sources(why or content))), "skip"))
        else:                                            # pick : « X @cote — pourquoi »
            sel = re.split(r"\s*@", content)[0].strip().rstrip("(").strip()
            if why and why != content:
                notes.append((sel, _sentence_case(_units_to_pct(_strip_sources(why)))))
    if mise.strip():
        resid.append(("💰", "Mise conseillée",
                      _sentence_case(_units_to_pct(_strip_sources(_strip(mise).strip()))), "mise"))
    resid_html = ""
    if resid:
        rows = "".join(
            f'<div class="da-bx {cls}"><div class="da-bx-h"><span class="da-bx-ic">{ic}</span>'
            f'<span class="da-bx-lbl">{html.escape(lbl)}</span></div>'
            f'<div class="da-bx-t">{_inline(txt)}</div></div>'
            for ic, lbl, txt, cls in resid)
        resid_html = f'<div class="da-bets-extra">{rows}</div>'
    return notes, resid_html


def _bets_table(body: str, results: dict | None = None, compact: bool = False,
                notes: list | None = None, residual: str = "",
                sport: str | None = None, home: str = "", away: str = "",
                validation: dict | None = None) -> str:
    """Paris à jouer : un CADRE par pari (style « confiance ») = label + sélection + barre de
    probabilité + indice de sûreté + cote. `results` = {sélection normalisée: 'won'/'lost'/'push'/
    None} -> cadre VERT/ROUGE + halo + ✓/✗ selon le résultat de CE pari (chaque pari réglé à part)."""
    data = _parse_bets(body)
    if not data:
        return ""
    results = results or {}
    cprobs = codes = ok = None    # confiances RECALIBRÉES + codes + indices réglables (≈ card_summary)
    if sport:
        from app.settle_analyst import code_from_pick
        ex_sports, ex_markets = auto_exclusions()
        codes = [code_from_pick(b.get("sel", ""), sport, home, away) for b in data]
        cprobs = [calibrated_conf(b.get("prob"), sport, codes[i]) for i, b in enumerate(data)]
        ok = set() if sport in ex_sports else {
            i for i, c in enumerate(codes) if c and market_of(c) not in ex_markets}
    reco = _recommend(data, ok=ok, cprobs=cprobs, codes=codes)
    note_by_idx = _assign_notes([b["sel"] for b in data], notes)   # commentaire Verdict -> bon pari
    cards = []
    # Sûreté en PASTILLE TEXTE (≠ étoiles, réservées au pari retenu ⭐) : élevée/moyenne/faible.
    _safe_cls = {"ok": "saf-hi", "mid": "saf-mid", "hi": "saf-lo"}
    for k, b in enumerate(data):
        pari = _inline(b["sel"])
        cv, prob, rcls = b["cote"], b["prob"], b["risk_cls"]
        is_reco = reco.get("idx") == k          # LE pari simulé pour la bankroll (= « à jouer »)
        # Bandeau de STATS : Confiance % · Cote. (« Value »/EV RETIRÉ de l'affichage le 2026-06-13 :
        # déductible de conf×cote, et un EV négatif sur un pari sûr est déroutant. Le moteur l'utilise
        # toujours en interne pour choisir le pari ⭐ retenu.)
        conf_v = f"{prob}%" if prob is not None else "—"
        cote_v = f"{cv:g}" if cv is not None else (_inline(b["cote_txt"]) if b["cote_txt"] else "—")
        strip = (
            '<div class="da-bk-stats">'
            f'<div class="da-st"><span class="da-st-v">{conf_v}</span><span class="da-st-l">Confiance</span></div>'
            f'<div class="da-st da-st-cote"><span class="da-st-v">{cote_v}</span><span class="da-st-l">Cote</span></div></div>')
        # SÛRETÉ = étoiles dans l'en-tête (★ pleines = niveau), libellé au survol — plus de pastille.
        safe = (f'<span class="da-bk-safe {_safe_cls.get(rcls, "saf-mid")}">'
                f'{_SAFETY.get(rcls, "Sûreté moyenne")}</span>')
        tab = _BET_LABELS[k] if k < len(_BET_LABELS) else f"Pari {k + 1}"
        res = results.get(_norm_sel(b["sel"]))
        rescls = " da-bk-won" if res == "won" else (" da-bk-lost" if res == "lost"
                                                    else (" da-bk-push" if res == "push" else ""))
        mark = ('<span class="da-bk-mark mk-w">✓ Gagné</span>' if res == "won"
                else '<span class="da-bk-mark mk-l">✗ Perdu</span>' if res == "lost"
                else '<span class="da-bk-mark mk-p">➖ Remboursé</span>' if res == "push" else "")
        # BANDE gauche : OR pour le pari RETENU par le moteur (ex-« mode bankroll », UI retirée
        # 2026-06-12) ; le repère est désormais une ⭐ à DROITE du nom du pari (demande utilisateur).
        recocls = " da-bk-reco" if is_reco else ""
        recostar = ' <span class="da-bk-star" title="Pari retenu par le moteur">⭐</span>' if is_reco else ""
        # Badge VALIDATION (panel de 3 agents) sur le pari retenu : ✓ Validé n/N + consensus.
        valbadge = ""
        if is_reco and validation and validation.get("n_ok") is not None:
            no, nt = validation["n_ok"], validation.get("n", 3)
            cp = validation.get("consensus_prob")
            tip = " · ".join(f'{v.get("emoji", "")}{v.get("verdict", "")[:3]}' for v in validation.get("votes", []))
            valbadge = (f'<span class="da-bk-val" title="Validé par {no}/{nt} agents — {html.escape(tip)}">'
                        f'✓ Validé {no}/{nt}{f" · {cp}%" if cp else ""}</span>')
        # Commentaire du Verdict déplacé SOUS le pari correspondant, DANS la même carte.
        note = note_by_idx.get(k)
        note_html = f'<div class="da-bk-note">{_inline(note)}</div>' if note else ""
        cards.append(
            f'<div class="da-bk{recocls}{rescls}">'
            f'<div class="da-bk-tab">{tab}{safe}{valbadge}{mark}</div>'
            f'<div class="da-bk-sel">{pari}{recostar}</div>'
            f'{note_html}{strip}</div>')   # affiche -> ANALYSE -> stats
    # LIVE (compact) : on ne garde QUE les cartes de paris (ni titre, ni légende, ni verdict ;
    # le repère « meilleure value » est déjà porté par le cadre OR + badge ✅ de la carte).
    if compact:
        return '<div class="da-bks">' + "".join(cards) + "</div>"
    # NON-live : titre simple « Les paris à jouer » + les cartes (chacune avec son commentaire Verdict),
    # puis le résidu du Verdict (à éviter / mise). La barre de séparation est ajoutée par web._sport_row.
    return ('<div class="da-bets-h">📊 Les paris à jouer</div>'
            '<div class="da-bks">' + "".join(cards) + "</div>" + (residual or ""))


def _structured(md: str) -> str | None:
    """Rendu gabarit analyste, ou None si le format ne correspond pas (-> repli générique)."""
    secs = _sections(md)
    verdict = _find(secs, "🎯", "Verdict")
    bets = _find(secs, "📊", "Paris class")
    if not verdict and not bets:
        return None
    faits = _find(secs, "📋", "Les faits", "faits")
    mise = _find(secs, "💰", "Mise")
    parts = []
    # Le VERDICT n'est PLUS rendu ici : son commentaire est déplacé SOUS chaque pari (dans la carte,
    # cf. analyses._verdict_notes + _bets_table) et le résidu (à éviter / mise) suit les paris. Les
    # « paris à jouer » ne sont pas non plus rendus ici (ils sont SUR la carte). `verdict`/`bets`/`mise`
    # restent dans `known` pour ne pas être re-rendus par la boucle « sections imprévues » ci-dessous.
    if faits:
        parts.append('<div class="da-faits"><div class="da-faits-h">📋 Les faits</div>'
                     f'<div class="da-faits-b">{_render_blocks(faits)}</div></div>')
    # toute autre section non prévue : rendue à la suite (sécurité, ne rien perdre)
    known = {"", verdict, bets, faits, mise}
    for title, b in secs.items():
        if b and b not in known and title not in ("", ):
            if title in (t for t in ("Verdict", "Paris", "faits", "Mise")):
                continue
            parts.append(f'<div class="da-h da-h2">{_inline(title)}</div>{_render_blocks(b)}')
    return '<div class="da">' + "".join(parts) + "</div>"


def _bets_section(md: str) -> str:
    return _find(_sections(_strip(md)), "📊", "Paris class", "paris")


def bets_of(sport: str, match_id) -> list[dict]:
    """Liste STRUCTURÉE et ordonnée des paris affichés d'un match (pari 1/2/3), pour le règlement
    par pari. [] si pas d'analyse / pas de tableau."""
    md = load(sport, match_id)
    if not md:
        return []
    body = _bets_section(md)
    return _parse_bets(body) if body else []


def bets_html(sport: str, match_id, compact: bool = False) -> str:
    """Cadres « paris à jouer » d'un match (depuis le .md), pour affichage SUR la carte sous les
    barres % (HORS analyse dépliée). Chaque cadre est coloré VERT/ROUGE selon le résultat réglé de
    CE pari (sidecar `bets`). '' si pas d'analyse ou pas de section paris."""
    md = load(sport, match_id)
    if not md:
        return ""
    body = _bets_section(md)
    if not body:
        return ""
    m = meta(sport, match_id) or {}      # sofa_id-aware -> résultats par pari du bon sidecar
    results = {_norm_sel(b.get("sel", "")): b.get("result") for b in (m.get("bets") or [])}
    notes, residual = _verdict_notes(md)   # commentaire Verdict -> sous chaque pari ; résidu après
    return _bets_table(body, results, compact=compact, notes=notes, residual=residual,
                       sport=sport, home=m.get("home", ""), away=m.get("away", ""),
                       validation=m.get("validation"))


# ------------------------------------------------------------- combiné : métrique + statut par jambe
# Le CODE d'une jambe (ex. « TEAMTOT HOME OVER 22.5 ») ne dit PAS sur quoi porte la ligne (buts ? tirs ?
# tirs cadrés ?). La MÉTRIQUE se lit donc sur le TEXTE. Source UNIQUE pour le live ET le règlement final
# -> les deux sont toujours d'accord. Marchés mi-temps / handicap / buteur = non verrouillables ici.
_METRIC_BASE = {   # métrique -> préfixe de clé dans le dict de valeurs (suffixe « _1h » pour la 1ère MT)
    "goals": "goals", "shots": "shots", "sot": "sot",
    "corners": "corners", "cards": "cards", "redcards": "rc",
}
_STATS_1H = {"shots", "sot", "corners", "cards", "redcards"}   # dispo en 1ère MT via df_st (PAS les buts)


def _to_float(s):
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _as_int(s):
    """Compteur live en entier (Unibet renvoie le score en str ; df_st déjà en int). None si illisible."""
    if isinstance(s, bool):
        return None
    if isinstance(s, int):
        return s
    m = re.search(r"-?\d+", str(s or ""))
    return int(m.group()) if m else None


def _leg_side(text: str, home: str, away: str) -> str | None:
    """HOME / AWAY / None selon l'équipe nommée dans `text` (jetons distinctifs, jetons communs ignorés)."""
    names = lambda s: [w for w in re.findall(r"[a-zà-ÿ]+", (s or "").lower()) if len(w) >= 4]
    h_all, a_all = names(home), names(away)
    shared = set(h_all) & set(a_all)
    h = [w for w in h_all if w not in shared] or h_all
    a = [w for w in a_all if w not in shared] or a_all
    t = (text or "").lower()
    hin, ain = any(w in t for w in h), any(w in t for w in a)
    return "HOME" if (hin and not ain) else ("AWAY" if (ain and not hin) else None)


def _leg_metric(leg: dict, home: str = "", away: str = "") -> dict:
    """Décrit une jambe pour l'évaluation : {metric, side, dir, line, scope, live_ok}. `live_ok` = la
    jambe se valide sur un simple compteur (métrique connue, sur le match entier) -> verrouillable au
    fil du jeu ET réglable proprement. Sinon (mi-temps, handicap, buteur, ligne illisible) : laissée à
    l'ancien règlement par code (`live_ok=False`)."""
    sel = leg.get("sel") or ""
    t = sel.lower()
    code = (leg.get("code") or "").upper()
    if "carton rouge" in t or ("rouge" in t and "carton" in t):
        metric = "redcards"
    elif "carton" in t or "card" in t:
        metric = "cards"
    elif "corner" in t:
        metric = "corners"
    elif "cadré" in t or "cadre" in t or "on target" in t:
        metric = "sot"
    elif "buteur" in t or "premier but" in t:
        metric = "special"
    elif "tir" in t or "shot" in t:
        metric = "shots"
    elif "but" in t or "goal" in t:
        metric = "goals"
    else:
        metric = "special"
    if "deux mi-temps" in t or "2 mi-temps" in t or "both halves" in t:
        scope = "both"
    elif any(k in t for k in ("1ère mi", "1re mi", "1ere mi", "première mi", "mt1", "1ère mt",
                              "1re mt", "1st half", "1ère période", "1ere periode")):
        scope = "1H"
    elif any(k in t for k in ("2ème mi", "2eme mi", "2nde mi", "seconde mi", "2e mi", "2nd half")):
        scope = "2H"
    else:
        scope = "match"
    handicap = "handicap" in t
    side = direction = line = None
    parts = code.split()
    if parts:
        k = parts[0]
        if k in ("OVER", "UNDER") and len(parts) >= 2:
            direction, line = k, _to_float(parts[1])
        elif k == "TEAMTOT" and len(parts) >= 4:
            side, direction, line = parts[1], parts[2], _to_float(parts[3])
        elif k in ("CARDS", "REDCARDS", "CORNERS"):
            rest = parts[1:]
            if rest and rest[0] in ("HOME", "AWAY"):
                side = rest.pop(0)
            if len(rest) >= 2 and rest[0] in ("OVER", "UNDER"):
                direction, line = rest[0], _to_float(rest[1])
    if direction is None:
        if "moins" in t or "under" in t:
            direction = "UNDER"
        elif "plus" in t or "over" in t or "+" in t:
            direction = "OVER"
    if line is None:
        # la LIGNE suit « plus/moins de X » ou « +/-X » — NE PAS attraper le « 1 » de « 1ère mi-temps »
        # ni le « 2 » de « 2ème », etc. (sinon ligne fausse sur les marchés mi-temps à code vide).
        mnum = re.search(r"(?:plus|moins|over|under)\s+(?:de\s+)?(\d+(?:[.,]\d+)?)", t)
        line = _to_float(mnum.group(1)) if mnum else None
    if side is None:
        side = _leg_side(sel, home, away)
    # « But dans les deux mi-temps Oui/Non » : marché OUI/NON (pas une ligne) -> métrique dédiée,
    # réglée sur les buts PAR mi-temps (un but dans CHAQUE période). Réglable au final (df_su).
    if scope == "both" and metric == "goals":
        return {"metric": "bothhalves", "side": None, "dir": None, "line": None,
                "scope": "both", "handicap": False,
                "yes": not re.search(r"\bnon\b", t), "live_ok": True}
    # Handicap (corners/cartons/tirs) : réglé sur le DIFFÉRENTIEL d'une équipe (mien + ligne signée vs
    # autre). Suivable en live (marge courante). Ligne signée « +5 » / « -5 » lue ici (pas de plus/moins).
    if handicap and metric in _METRIC_BASE:
        mh = re.search(r"([+\-−])\s*(\d+(?:[.,]\d+)?)", t)
        hline = (-1 if (mh and mh.group(1) in "-−") else 1) * _to_float(mh.group(2)) if mh else None
        return {"metric": metric, "side": side, "dir": "HCAP", "line": hline, "scope": scope,
                "handicap": True, "live_ok": bool(side and hline is not None and scope == "match")}
    base_ok = direction in ("OVER", "UNDER") and line is not None and not handicap
    if scope == "match":
        live_ok = base_ok and metric in _METRIC_BASE
    elif scope == "1H":                                  # 1ère MT : seulement les métriques du df_st
        live_ok = base_ok and metric in _STATS_1H
    else:                                                # 2H : non couvert ici
        live_ok = False
    return {"metric": metric, "side": side, "dir": direction, "line": line,
            "scope": scope, "handicap": handicap, "live_ok": live_ok}


def _eval_leg(info: dict, vals: dict, final: bool = False):
    """(statut, valeur_courante) d'une jambe. statut = 'won'/'lost'/'pending' (live) ou
    'won'/'lost'/'push'/None (final). `vals` = compteurs {goals_h, sot_a, corners_h, …}. Logique de
    verrouillage : Plus de X -> gagné dès cur > X ; Moins de X -> perdu dès cur > X. None/pending si
    la jambe n'est pas verrouillable ici ou si la valeur manque encore."""
    if not info or not info.get("live_ok"):
        return (None if final else "pending"), None
    if info.get("dir") == "HCAP":                          # handicap reformulé en ÉCART vs SEUIL
        base = _METRIC_BASE.get(info["metric"])
        suffix = "_1h" if info.get("scope") == "1H" else ""
        hv, av = _as_int(vals.get(f"{base}_h{suffix}")), _as_int(vals.get(f"{base}_a{suffix}"))
        if hv is None or av is None or base is None:
            return (None if final else "pending"), None
        mine, other = (hv, av) if info.get("side") == "HOME" else (av, hv)
        ln = info["line"]
        # +L (coussin) = « l'adversaire ne mène pas de plus de L » (UNDER L sur l'écart adverse) ;
        # -L (à battre) = « mon équipe mène de plus de L » (OVER L sur mon écart).
        val = (other - mine) if ln >= 0 else (mine - other)      # écart courant (compteur affiché)
        line, over = abs(ln), ln < 0
        if not final:                                      # l'écart peut encore bouger -> en cours
            return "pending", val
        if val == line:
            return "push", val
        return ("won" if ((val > line) == over) else "lost"), val
    if info["metric"] == "bothhalves":                     # « but dans les deux mi-temps » (Oui/Non)
        g1, g2 = _as_int(vals.get("goals_1h_total")), _as_int(vals.get("goals_2h_total"))
        yes = info.get("yes", True)
        if (final or g2 is not None) and g1 == 0:          # 1ère MT FINIE sans but -> « Oui » impossible
            return ("lost" if yes else "won"), None        # (verrouillé : 2e MT entamée ou match fini)
        if not final:
            return "pending", None                         # « Oui » ne se confirme qu'au coup de sifflet
        if g1 is None or g2 is None:
            return None, None                              # données mi-temps absentes -> non réglable ici
        both = g1 > 0 and g2 > 0
        return ("won" if (both == yes) else "lost"), None
    base = _METRIC_BASE[info["metric"]]
    suffix = "_1h" if info.get("scope") == "1H" else ""    # 1ère MT -> clés *_1h du df_st
    hv, av = _as_int(vals.get(f"{base}_h{suffix}")), _as_int(vals.get(f"{base}_a{suffix}"))
    if hv is None or av is None:                           # valeur absente OU non numérique -> on attend
        return (None if final else "pending"), None
    side = info.get("side")
    cur = hv if side == "HOME" else (av if side == "AWAY" else hv + av)
    line, over = info["line"], info["dir"] == "OVER"
    if cur > line:
        return ("won" if over else "lost"), cur
    if cur < line:
        return (("lost" if over else "won") if final else "pending"), cur
    return ("push" if final else "pending"), cur            # cur == line


def _hcap_adjusted(info: dict, vals: dict):
    """(mon score AJUSTÉ du handicap, score adverse) ou None — pour l'affichage « 9-1 » (ex. corners
    4 + handicap +5 = 9, vs 1). Le pari est gagné si le score ajusté dépasse celui de l'adversaire."""
    base = _METRIC_BASE.get(info.get("metric"))
    if base is None:
        return None
    suffix = "_1h" if info.get("scope") == "1H" else ""
    hv, av = _as_int(vals.get(f"{base}_h{suffix}")), _as_int(vals.get(f"{base}_a{suffix}"))
    if hv is None or av is None:
        return None
    mine, other = (hv, av) if info.get("side") == "HOME" else (av, hv)
    return (mine + info["line"], other)


def combo_live_status(d: dict, vals: dict) -> dict | None:
    """Statut LIVE d'un combiné : par jambe (won/lost/pending + valeur courante) et global. Le combiné
    est PERDU dès qu'UNE jambe saute, GAGNÉ quand TOUTES sont acquises, sinon en cours. None si pas de
    combiné."""
    combo = (d or {}).get("combo")
    if not combo or not combo.get("legs"):
        return None
    home, away = d.get("home", ""), d.get("away", "")
    legs, any_lost, n_won = [], False, 0
    for leg in combo["legs"]:
        info = _leg_metric(leg, home, away)
        status, cur = _eval_leg(info, vals, final=False)
        if info.get("dir") == "HCAP":                      # handicap -> score AJUSTÉ « 9-1 »
            adj = _hcap_adjusted(info, vals)
            disp = f"{adj[0]:g}-{adj[1]:g}" if adj else ""
        elif cur is None:                                  # rien à afficher (jambe non suivable / sans valeur)
            disp = ""
        elif info.get("line") is not None:                 # over/under -> compteur « courant/seuil »
            disp = f"{cur:g}/{info['line']:g}"
        else:
            disp = f"{cur:g}"
        legs.append({"sel": leg.get("sel", ""), "cote": leg.get("cote"), "status": status,
                     "cur": cur, "line": info.get("line"), "disp": disp})
        if status == "won":
            n_won += 1
        elif status == "lost":
            any_lost = True
    overall = "lost" if any_lost else ("won" if n_won == len(legs) else "pending")
    return {"legs": legs, "status": overall, "n_won": n_won, "n": len(legs)}


_COMBO_VALS_CACHE: dict = {}    # clé match -> (ts, stats Flashscore)


def _combo_live_vals(d: dict) -> dict:
    """Valeurs LIVE d'un match foot en cours : {goals_h/a (Unibet, déjà en cache, sans réseau),
    shots_h/a, sot_h/a, corners_h/a, cards_h/a, rc_h/a, goals_*_total (Flashscore df_st/df_su)}.
    ⚠️ LECTURE SEULE du cache `_COMBO_VALS_CACHE` — AUCUN appel réseau ici (cette fonction tourne dans
    le handler async au rendu). Le cache est pré-rempli hors boucle par `warm_combo_vals` (cf. main)."""
    from app import match_select   # import local (évite les cycles au chargement)
    home, away = d.get("home", ""), d.get("away", "")
    vals: dict = {}
    try:
        ld = match_select.live_state_for(d.get("sport", "foot"), home, away) or {}
        sc = ld.get("score") or {}
        gh, ga = _as_int(sc.get("home")), _as_int(sc.get("away"))   # Unibet renvoie le score en str
        if gh is not None and ga is not None:
            vals["goals_h"], vals["goals_a"] = gh, ga
    except Exception:
        pass
    hit = _COMBO_VALS_CACHE.get(f"{home}|{away}")     # pur lookup (pas de fetch synchrone bloquant)
    for k, v in ((hit[1] if hit else {}) or {}).items():   # corners_h, …, variantes 1ère MT (corners_h_1h…)
        if v is not None:
            vals[k] = v
    return vals


def warm_combo_vals(home: str, away: str, start: str | None) -> None:
    """Remplit le cache stats Flashscore d'un match (corners/cartons/tirs/buts par mi-temps). À appeler
    HORS du handler (via asyncio.to_thread depuis une boucle de fond) — l'appel urllib est bloquant."""
    from app import flashscore
    try:
        st = flashscore.foot_match_stats_by_names(home, away, start) or {}
    except Exception:
        st = {}
    _COMBO_VALS_CACHE[f"{home}|{away}"] = (time.time(), st)


def combo_html(sport: str, match_id) -> str:
    """Cadre « 🎲 Combiné » (grand tournoi) d'un match, depuis le sidecar `combo`. Chaque jambe + cote,
    cote combinée, et résultat réglé (par jambe + global) si présent. EN COURS de match : statut live
    par jambe (✅ acquise / ❌ perdue / ⏳ en cours + compteur). '' si pas de combiné."""
    import html as _h
    m = meta(sport, match_id) or {}
    combo = m.get("combo")
    if not combo or not combo.get("legs"):
        return ""
    res = combo.get("result")            # 'won'/'lost'/None (global, posé au règlement post-match)
    # EN COURS de match et pas encore réglé : on calcule le statut LIVE par jambe (best-effort).
    live = None
    if res is None and status_of(m) == "inprogress":
        try:
            live = combo_live_status(m, _combo_live_vals(m))
        except Exception:
            live = None
    rows = []
    for i, leg in enumerate(combo["legs"]):
        lr = leg.get("result")                       # résultat FINAL réglé (post-match) s'il existe
        ls = prog = ""
        if lr is None and live:                      # sinon, statut live
            ll = live["legs"][i]
            ls = ll["status"]
            if ll.get("disp"):                       # compteur courant/seuil (ou marge handicap)
                prog = f'<span class="da-cl-p">{ll["disp"]}</span>'
        st = lr or (ls if ls in ("won", "lost") else "")
        cls = (" da-cl-won" if st == "won" else " da-cl-lost" if st == "lost"
               else " da-cl-live" if ls == "pending" else "")
        # En cours : PAS d'icône (le compteur cur/line + le badge d'en-tête « ● n/N en direct » suffisent) ;
        # seules les jambes ACQUISES (✅) ou PERDUES (❌) portent une icône.
        mark = ("✅" if st == "won" else "❌" if st == "lost" else "")
        mk = f'<span class="da-cl-mk">{mark}</span>' if mark else ""
        try:
            cote = f"{float(leg.get('cote')):.2f}"
        except (TypeError, ValueError):
            cote = "?"
        # 2 colonnes : sélection (gauche, wrap propre) | bloc insécable cote · compteur · statut (droite)
        rows.append(f'<div class="da-cl{cls}">'
                    f'<span class="da-cl-sel">{_h.escape(str(leg.get("sel", "")))}</span>'
                    f'<span class="da-cl-meta"><b>@{cote}</b>{prog}{mk}</span></div>')
    # En-tête : résultat FINAL prioritaire ; sinon, en live, état du combiné (perdu dès qu'une jambe saute).
    lv = live["status"] if live else None
    hcls = (" da-combo-won" if res == "won" else " da-combo-lost" if res == "lost"
            else " da-combo-lost" if lv == "lost" else " da-combo-live" if live else "")
    if res == "won":
        badge = ' <span class="da-combo-b won">GAGNÉ</span>'
    elif res == "lost":
        badge = ' <span class="da-combo-b lost">PERDU</span>'
    elif lv == "lost":
        badge = ' <span class="da-combo-b lost">PERDU (live)</span>'
    elif live:
        badge = f' <span class="da-combo-b live">● {live["n_won"]}/{live["n"]} en direct</span>'
    else:
        badge = ""
    try:
        total = f"{float(combo.get('total')):.2f}"
    except (TypeError, ValueError):
        total = "?"
    return (f'<div class="da-combo{hcls}"><div class="da-combo-h">🎲 Combiné '
            f'<span class="da-combo-c">cote {total}</span>{badge}</div>{"".join(rows)}</div>')


def card_summary(sport: str, match_id) -> dict:
    """Résumé COMPACT d'un match pour la ligne repliée (carte compacte) : nb de paris, meilleure
    confiance, s'il y a un pari ✅ À JOUER (même règle que la simulation : ≥65 %, EV≥+3 %, réglable),
    et le résultat réglé du pari joué (terminés). {} si pas d'analyse."""
    m0 = meta(sport, match_id) or {}
    combo = m0.get("combo")
    if combo and combo.get("legs"):
        # COMBINÉ (CdM) = LE pari du match -> résumé compact basé sur le combiné (1 pari, son résultat),
        # cohérent avec l'affichage (le combiné remplace les paris simples).
        res = combo.get("result")
        sel = f"🎲 Combiné ({len(combo['legs'])} jambes) @{combo.get('total')}"
        return {"n": 1, "best_conf": None, "comp": m0.get("comp"), "circuit": m0.get("circuit"),
                "play": res is None, "ev": None, "reco_idx": 0 if res != "lost" else None,
                "won": 1 if res == "won" else 0, "lost": 1 if res == "lost" else 0,
                "settled": 1 if res in ("won", "lost", "push") else 0,
                "play_result": res, "bets": [{"sel": sel, "result": res}], "is_combo": True}
    bets = bets_of(sport, match_id)
    if not bets:
        return {}
    out = {"n": len(bets)}
    confs = [b.get("prob") for b in bets if b.get("prob") is not None]
    out["best_conf"] = max(confs) if confs else None
    m = meta(sport, match_id) or {}
    out["comp"] = m.get("comp")            # tournoi/ville (tennis : ville ; foot/basket : ligue)
    out["circuit"] = m.get("circuit")      # tennis : WTA/ATP (capté au scan ; None sur d'anciennes analyses)
    # reco À JOUER (⭐) : filtre réglable/calibration -> LE pari mis en avant sur la carte du match
    try:
        from app.settle_analyst import code_from_pick
        ex_sports, ex_markets = auto_exclusions()
        if sport in ex_sports:
            reco = {"verdict": "skip", "idx": None, "ev": None}
        else:
            ok, cprobs, codes = set(), [], []
            for i, b in enumerate(bets):
                code = code_from_pick(b.get("sel", ""), sport, m.get("home", ""), m.get("away", ""))
                codes.append(code)
                cprobs.append(calibrated_conf(b.get("prob"), sport, code))   # confiance recalibrée
                if code and market_of(code) not in ex_markets:
                    ok.add(i)
            reco = _recommend(bets, ok, cprobs, codes)
    except Exception:                                    # règlement indispo -> EV brut sans filtre
        reco = _recommend(bets)
    out["play"] = reco.get("verdict") == "play" and reco.get("idx") is not None
    out["ev"] = reco.get("ev")
    out["reco_idx"] = reco.get("idx")
    # résultats réglés (terminés) : par sélection
    results = {_norm_sel(b.get("sel", "")): b.get("result") for b in (m.get("bets") or [])}
    rl = [results.get(_norm_sel(b.get("sel", ""))) for b in bets]
    rl = [x for x in rl if x]
    out["won"] = sum(1 for x in rl if x == "won")
    out["lost"] = sum(1 for x in rl if x == "lost")
    out["settled"] = len(rl)
    if out["reco_idx"] is not None:                      # résultat du pari EFFECTIVEMENT joué
        out["play_result"] = results.get(_norm_sel(bets[out["reco_idx"]].get("sel", "")))
    # LISTE des paris (intitulé + résultat) pour le résumé compact (une ligne par pari, sans détail).
    out["bets"] = [{"sel": b.get("sel", ""), "result": results.get(_norm_sel(b.get("sel", "")))}
                   for b in bets]
    return out


def to_html(md: str) -> str:
    """Markdown analyste -> HTML : structuré si gabarit reconnu, sinon rendu générique."""
    md = _strip(md)
    structured = _structured(md)
    if structured is not None:
        return structured
    return '<div class="da">' + _render_blocks(md) + "</div>"


_RESULT_CHIP = {"won": "✅ Réussi", "lost": "❌ Perdu", "push": "➖ Remboursé"}


def result_chip(d: dict) -> tuple[str, str]:
    """(badge court ✅/❌/➖, score) du pari réglé pour les cartes « Terminés ». ('', '') si non réglé."""
    res = (d or {}).get("result") or {}
    return (_RESULT_CHIP.get(res.get("pick_result"), ""), res.get("score") or "")


def result_board(d: dict, sport: str) -> dict:
    """Score FINAL + détail par set/quart-temps (depuis `result.raw.periods`, capté au règlement),
    au format attendu par web._live_scoreboard -> {score, periods}. Permet d'afficher les terminés
    AVEC le détail (sets tennis / quart-temps basket) comme en live. Repli sur le total si pas de détail."""
    raw = ((d or {}).get("result") or {}).get("raw") or {}
    items = []
    for k, v in (raw.get("periods") or {}).items():
        try:
            items.append((int(k), int(v[0]), int(v[1])))      # JSON : clés str, tuples -> listes
        except (ValueError, TypeError, IndexError):
            continue
    items.sort()
    plist = [(h, a) for _n, h, a in items]
    if sport == "tennis":
        if plist:                                             # jeux par set -> « 6-4 3-6 6-2 »
            return {"score": " ".join(f"{h}-{a}" for h, a in plist), "periods": None}
        sh, sa = raw.get("sets_home"), raw.get("sets_away")
        return {"score": (f"{sh}-{sa}" if sh is not None and sa is not None else ""), "periods": None}
    if sport == "basket":                                     # points par quart-temps -> box-score
        h, a = raw.get("home"), raw.get("away")
        total = (f"{h}-{a}" if h is not None and a is not None
                 else (f"{sum(x for x, _ in plist)}-{sum(y for _, y in plist)}" if plist else ""))
        return {"score": total, "periods": plist or None}
    h, a = raw.get("home"), raw.get("away")                   # foot : total simple
    return {"score": (f"{h}-{a}" if h is not None and a is not None else ""), "periods": None}


_BET_KEYS = ("pari1", "pari2", "pari3")   # positions de pari pour les stats (= ordre d'affichage)

# Caches d'AGRÉGATS invalidés par la signature du dossier (cf. _dir_sig) : home/stats/reco refont
# ces agrégations à chaque rendu alors que les sidecars ne changent qu'au scan/règlement.
_STATS_CACHE: dict = {}    # "full" -> (sig, stats_full())
_CALIB_RES_CACHE: dict = {}  # min_conf -> (sig, calibration()) — uniquement pour since_days=None
_PERF_CACHE: dict = {}     # "v" -> (sig, perf_breakdown()) — ROI par cote/marché/confiance


# JALONS du modèle : dates (UTC) où la LOGIQUE de sélection a changé -> repères verticaux sur les
# courbes d'équité (pour corréler une inflexion de ROI avec un changement). Garder COURT (s'affiche
# sur un petit graphe) et N'AJOUTER qu'un vrai changement de POLITIQUE de paris (pas l'UI).
MODEL_MILESTONES = [
    ("2026-06-09", "Seuil ≥65 %"),
    ("2026-06-12", "Mode strict"),
    ("2026-06-16", "1 pari/match"),   # suivi = pari recommandé + garde-fous marchés/cote + 1 pari le + probable
]


def _agg_bets(events: list) -> dict:
    """Agrège une liste de paris (start, result, odds) -> bloc stats complet : courbe de profit
    cumulé (démarre à 0), won/lost/push/settled, % réussite, profit (u), ROI (%), cote moyenne.
    Mise plate 1 u : gagné +(cote-1), perdu -1, remboursé 0. ROI = profit ÷ total misé.
    `dates` = coup d'envoi de chaque pari, ALIGNÉ sur points[1:] (points[0]=0 avant tout pari) ->
    sert à placer les jalons MODEL_MILESTONES sur la courbe."""
    events = sorted(events, key=lambda x: x[0] or "")
    cum, osum = 0.0, 0.0
    pts, dates, won, lost, push = [0.0], [], 0, 0, 0
    for _start, res, odds in events:
        if res == "won":
            cum += (float(odds) - 1) if odds else 0.0
            won += 1
            osum += float(odds) if odds else 0.0
        elif res == "lost":
            cum -= 1.0
            lost += 1
            osum += float(odds) if odds else 0.0
        else:
            push += 1
        pts.append(round(cum, 3))
        dates.append(_start or "")
    settled, staked = won + lost, won + lost + push
    # Série EN COURS (signée) : nb de gagnés (+) ou perdus (-) consécutifs en fin de période.
    seq = [res for _s, res, _o in events if res in ("won", "lost")]
    streak = 0
    if seq:
        last, c = seq[-1], 0
        for r in reversed(seq):
            if r != last:
                break
            c += 1
        streak = c if last == "won" else -c
    # Meilleure série gagnante (plus longue suite de gagnés) -> momentum « historique ».
    best_streak = run = 0
    for r in seq:
        run = run + 1 if r == "won" else 0
        best_streak = max(best_streak, run)
    _all_form = [res for _s, res, _o in events]
    form = _all_form[-5:]            # 5 derniers (lignes par sport, compactes)
    form12 = _all_form[-12:]         # 12 derniers (bandeau d'accueil des stats)
    # Drawdown MAX : pire repli pic -> creux de la courbe d'équité (en unités).
    peak, dd = pts[0], 0.0
    for v in pts:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return {"points": pts, "dates": dates, "won": won, "lost": lost, "push": push,
            "settled": settled,
            "pct": (round(100 * won / settled) if settled else None), "profit": round(cum, 2),
            "roi": (round(100 * cum / staked, 1) if staked else None),
            "avg_odds": (round(osum / settled, 2) if settled else None),
            "streak": streak, "best_streak": best_streak, "form": form, "form12": form12,
            "max_dd": round(dd, 2),
            "dd_pct": (round(100 * dd / staked, 1) if staked else None)}


def _reco_event(d: dict, path: str, ex_sports: set, ex_markets: set) -> dict | None:
    """Le pari RECOMMANDÉ (⭐ « à jouer ») et RÉGLÉ d'un match = ce que l'utilisateur jouerait vraiment,
    avec EXACTEMENT le filtre de prod (≥65 % recalibré, EV≥+3 %, marché réglable/non exclu, garde-fous
    cote). -> {start, result, odds, prob, code, idx} ou None. Sert au SUIVI : 1 seul event par match
    (et non pari1/2/3), pour que le bilan affiché reflète le système suivi, pas les paris secondaires."""
    sport = d.get("sport")
    if not sport or sport in ex_sports:
        return None
    mid = os.path.basename(path)[len(sport) + 1:-5]
    bets = bets_of(sport, mid)
    if not bets:
        return None
    from app.settle_analyst import code_from_pick
    home, away = d.get("home", ""), d.get("away", "")
    codes = [code_from_pick(b.get("sel", ""), sport, home, away) for b in bets]
    cprobs = [calibrated_conf(b.get("prob"), sport, codes[i]) for i, b in enumerate(bets)]
    ok = {i for i, c in enumerate(codes) if c and market_of(c) not in ex_markets}
    reco = _recommend(bets, ok, cprobs, codes)
    if reco.get("verdict") != "play" or reco.get("idx") is None:
        return None
    ri = reco["idx"]
    results = {_norm_sel(b.get("sel", "")): b.get("result") for b in (d.get("bets") or [])}
    res = results.get(_norm_sel(bets[ri].get("sel", "")))
    if res not in ("won", "lost", "push"):
        return None
    return {"start": d.get("start") or "", "result": res, "odds": bets[ri].get("cote"),
            "prob": bets[ri].get("prob"), "code": codes[ri], "idx": ri}


def stats_full(since_days: int | None = None) -> dict:
    """Suivi pour l'accueil = LE PARI RECOMMANDÉ par match (le ⭐ « à jouer »), pas pari1/2/3 : le bilan
    reflète le système RÉELLEMENT suivi (les paris secondaires <65 % que l'outil dit de ne PAS jouer ne
    le plombent plus). 3 niveaux : `overall`, `by_pari` (recommandés ventilés par position 1/2/3),
    `by_sport`. Chaque bloc = `_agg_bets` (courbe + ROI + réussite + cote moy. + série + drawdown).
    `since_days` : ne garde que les matchs dont le coup d'envoi est dans les N derniers jours."""
    sig = _dir_sig() if since_days is None else None   # cache UNIQUEMENT la vue complète (pas de cutoff)
    if sig is not None:
        hit = _STATS_CACHE.get("full")
        if hit and hit[0] == sig:
            return hit[1]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)) if since_days else None
    ex_sports, ex_markets = auto_exclusions()
    all_ev: list = []
    by_pari: dict = {i: [] for i in range(len(_BET_KEYS))}
    by_sport: dict = {}
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        sport = d.get("sport")
        start = d.get("start") or ""
        if _is_world_cup(d):       # Coupe du Monde EXCLUE de toutes les stats (affichée mais non comptée).
            continue
        if cutoff is not None:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except (ValueError, AttributeError):
                dt = None
            if dt is None or dt < cutoff:
                continue
        e = _reco_event(d, p, ex_sports, ex_markets)    # UN seul event/match : le pari recommandé
        if not e:
            continue
        i = min(e["idx"], len(_BET_KEYS) - 1)
        ev = (start, e["result"], e["odds"])
        all_ev.append(ev)
        by_pari[i].append(ev)
        by_sport.setdefault(sport, {}).setdefault(i, []).append(ev)
    out = {"overall": _agg_bets(all_ev),
           "by_pari": {_BET_KEYS[i]: _agg_bets(by_pari[i]) for i in range(len(_BET_KEYS))},
           "by_sport": {}}
    for sport, byi in by_sport.items():
        merged = [e for lst in byi.values() for e in lst]
        out["by_sport"][sport] = {**_agg_bets(merged),
                                  "paris": {_BET_KEYS[i]: _agg_bets(byi[i]) for i in sorted(byi)}}
    if sig is not None:
        _STATS_CACHE["full"] = (sig, out)
    return out


_CALIB_BANDS = [(45, 55), (55, 65), (65, 75), (75, 85), (85, 101)]

_MARKET_FAMILY = {   # 1er token du code -> famille de marché lisible (pour la calibration par marché)
    "1X2": "Vainqueur", "WIN": "Vainqueur", "DC": "Double chance",
    "OVER": "Total +/-", "UNDER": "Total +/-", "BTTS": "Les 2 marquent",
    "HCAP": "Handicap", "SETHCAP": "Handicap", "TEAMTOT": "Total équipe",
    "SET": "Sets", "SETWIN": "Sets", "SETSCORE": "Sets", "SETSTOT": "Sets",
    "SETGAMES": "Jeux", "TOTGAMES": "Jeux", "HOLD1": "Jeux",
    "CARDS": "Cartons", "REDCARDS": "Cartons", "CORNERS": "Corners",
    "FIRSTTO": "Premier à X pts",
}


# Exclusions calibrées AUTOMATIQUES : on n'écarte un sport/marché des recommandations QUE s'il a fait
# ses preuves dans le MAUVAIS sens — assez de paris ET un écart nettement négatif. Sinon (petit
# échantillon = bruit), on NE conclut PAS : le pari reste éligible, protégé par le seuil de confiance.
# Auto-révisable : si une catégorie redevient bonne avec plus de données, elle se ré-inclut seule.
CALIB_MIN_N = 25     # nb mini de paris réglés avant d'oser exclure une catégorie (sous ça = bruit)
CALIB_GAP_MAX = -8   # réussite réelle au moins 8 pts SOUS la confiance annoncée = sur-confiance nette
_SPORT_FR = {"Football": "foot", "Tennis": "tennis", "Basket": "basket"}


def market_of(code: str) -> str:
    """Famille de marché lisible déduite du code de règlement (ex. 'UNDER 163' -> 'Total +/-')."""
    return _MARKET_FAMILY.get((code or "").split()[0] if code else "", "Autre")


_CALIB_MAP_CACHE = {"ts": 0.0, "map": {}}
_CALIB_SHRINK_K = 25     # force du a priori : la correction reste DOUCE tant qu'on manque de données
_CALIB_ADJ_MIN_N = 20    # paris MINI par catégorie avant TOUTE recalibration (sous ça : échantillon
#                          pas représentatif -> on ne touche pas. Filet dur auto_exclusions = 25.)


def _calib_map() -> dict:
    """Carte {(sport, marché): {n, win_rate, avg_conf}} pour la RECALIBRATION des confiances. Caché
    120 s (calibration() globe tous les sidecars)."""
    now = time.time()
    if _CALIB_MAP_CACHE["map"] and now - _CALIB_MAP_CACHE["ts"] < 120:
        return _CALIB_MAP_CACHE["map"]
    c = calibration(min_conf=0)
    m = {}
    for sport_label, g in (c.get("by_sport") or {}).items():
        sp = _SPORT_FR.get(sport_label, sport_label.lower())
        m[(sp, "_SPORT_")] = {"n": g.get("n", 0), "won": g.get("won", 0),   # agrégat sport (repli)
                              "win_rate": g.get("win_rate"), "avg_conf": g.get("avg_conf")}
        for mk, mg in (g.get("markets") or {}).items():
            m[(sp, mk)] = {"n": mg.get("n", 0), "won": mg.get("won", 0),
                           "win_rate": mg.get("win_rate"), "avg_conf": mg.get("avg_conf")}
    _CALIB_MAP_CACHE.update(ts=now, map=m)
    return m


def calibrated_conf(prob, sport: str, code: str):
    """Confiance RECALIBRÉE par la boucle de feedback : applique le BIAIS historique (réel − annoncé)
    de la catégorie (sport × marché), atténué par un lissage bayésien (poids n/(k+n)) -> doux sur
    petits échantillons, plus fort à mesure que les données s'accumulent. Sert au MOTEUR de reco
    (EV/Kelly), pas à l'affichage de la confiance de l'analyste."""
    if prob is None:
        return prob
    cmap = _calib_map()
    m = cmap.get((sport, market_of(code or "")))
    # Repli niveau-SPORT quand le marché précis est trop maigre (ex. tennis : biais global net mais
    # éclaté sur Sets/Jeux/Vainqueur, chacun n<min -> sans repli, la sur-confiance globale passe).
    if not m or not m.get("n") or m["n"] < _CALIB_ADJ_MIN_N \
            or m.get("win_rate") is None or m.get("avg_conf") is None:
        m = cmap.get((sport, "_SPORT_"))
    if not m or not m.get("n") or m.get("win_rate") is None or m.get("avg_conf") is None:
        return prob
    # PRUDENCE 1 : assez de paris dans la catégorie (sinon échantillon non représentatif -> on ne touche pas).
    if m["n"] < _CALIB_ADJ_MIN_N:
        return prob
    # PRUDENCE 2 : on ne corrige (donc on ne risque d'écarter) un type QUE si son biais est
    # statistiquement ÉTABLI (Wilson 90%, plus strict que l'affichage à 80%). Tant que l'échantillon
    # n'est pas représentatif, le taux annoncé reste DANS la fourchette -> aucune correction.
    lo, hi = _wilson(m.get("won", 0), m["n"], z=1.64)
    if lo <= m["avg_conf"] / 100.0 <= hi:
        return prob
    bias = m["win_rate"] - m["avg_conf"]                 # pts (réel − annoncé), seulement si significatif
    return max(1.0, min(99.0, prob + bias * m["n"] / (_CALIB_SHRINK_K + m["n"])))


def auto_exclusions() -> tuple[set, set]:
    """(sports exclus, marchés exclus) déduits de la calibration des PARIS RÉELLEMENT JOUABLES
    (confiance ≥ seuil _MIN_CONF), et uniquement quand c'est statistiquement défendable
    (n ≥ CALIB_MIN_N et écart ≤ CALIB_GAP_MAX). On juge donc une catégorie sur les paris qu'on jouerait
    vraiment — pas sur les paris faibles déjà écartés par le seuil. Vide tant qu'on manque de recul."""
    c = calibration(min_conf=_MIN_CONF)
    sports, markets = set(), set()
    for name, g in (c.get("by_sport") or {}).items():
        gap = (g.get("win_rate") or 0) - (g.get("avg_conf") or 0)
        if (g.get("n") or 0) >= CALIB_MIN_N and gap <= CALIB_GAP_MAX:
            sports.add(_SPORT_FR.get(name, name.lower()))
    for name, g in (c.get("by_market") or {}).items():
        gap = (g.get("win_rate") or 0) - (g.get("avg_conf") or 0)
        if (g.get("n") or 0) >= CALIB_MIN_N and gap <= CALIB_GAP_MAX:
            markets.add(name)
    return sports, markets


def _wilson(won: int, n: int, z: float = 1.28) -> tuple:
    """Intervalle de Wilson (fourchette réaliste d'une proportion) — robuste sur petits échantillons.
    z=1.28 ≈ 80%. Sert à ne pas conclure sur du bruit (ex. 4 paris gagnés ≠ « fiable à 100% »)."""
    if n <= 0:
        return (0.0, 1.0)
    p = won / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, center - half), min(1.0, center + half))


def _calib_agg(pairs: list) -> dict:
    """pairs = [(prob, won_bool)] -> tranches de confiance avec confiance annoncée vs réussite réelle,
    écart moyen pondéré (mae) et verdict (good ≤6 pts, sinon over/under selon le signe dominant)."""
    buckets = {b: {"n": 0, "won": 0, "conf": 0.0} for b in _CALIB_BANDS}
    for prob, won in pairs:
        band = next(((lo, hi) for lo, hi in _CALIB_BANDS if lo <= prob < hi), None)
        if not band:
            continue
        bk = buckets[band]
        bk["n"] += 1
        bk["conf"] += prob
        bk["won"] += 1 if won else 0
    rows, total, mae_num = [], 0, 0.0
    for lo, hi in _CALIB_BANDS:
        bk = buckets[(lo, hi)]
        n = bk["n"]
        if not n:
            continue
        wr, conf = round(100 * bk["won"] / n), round(bk["conf"] / n)
        rows.append({"lo": lo, "hi": hi, "n": n, "won": bk["won"], "win_rate": wr,
                     "avg_conf": conf, "gap": wr - conf})
        total += n
        mae_num += abs(wr - conf) * n
    mae = round(mae_num / total, 1) if total else None
    won_total = sum(r["won"] for r in rows)
    wr = round(100 * won_total / total) if total else None
    ac = round(sum(r["avg_conf"] * r["n"] for r in rows) / total) if total else None
    # VERDICT BASÉ SUR LA SIGNIFICATIVITÉ (Wilson 80%) : on ne tranche TROP OPTIMISTE/PRUDENT que si la
    # confiance annoncée tombe HORS de la fourchette réaliste du taux réel. Sinon « à confirmer »
    # (échantillon trop petit) ou « fiable » (dans la fourchette + assez de données).
    if not total:
        verdict = "no-data"
    else:
        lo, hi = _wilson(won_total, total)
        conf = (ac or 0) / 100.0
        if conf > hi:
            verdict = "over"          # annoncé > réel de façon significative -> trop optimiste
        elif conf < lo:
            verdict = "under"         # annoncé < réel de façon significative -> prudent
        elif total >= 15 and (mae or 99) <= 6:
            verdict = "good"          # dans la fourchette + assez de recul -> fiable
        else:
            verdict = "unsure"        # pas assez concluant -> à confirmer
    return {"rows": rows, "n": total, "won": won_total, "mae": mae, "verdict": verdict,
            "win_rate": wr, "avg_conf": ac}


def calibration(since_days: int | None = None, min_conf: int = 0) -> dict:
    """CALIBRATION : la confiance annoncée tient-elle ses promesses ? Compare la confiance MOYENNE
    annoncée au taux de réussite RÉEL (paris réglés gagné/perdu ; remboursés exclus), GLOBAL + par
    SPORT + par MARCHÉ -> on voit si l'edge est réel et OÙ il est. `prob` lue dans le sidecar (settle
    v7+), repli sur l'analyse parsée. `min_conf` : ne garder que les paris ≥ ce seuil (pour juger la
    population réellement jouée). Renvoie {rows,n,mae,verdict,…, by_sport:{}, by_market:{}}."""
    sig = _dir_sig() if since_days is None else None   # cache UNIQUEMENT la vue complète (pas de cutoff)
    if sig is not None:
        hit = _CALIB_RES_CACHE.get(min_conf)
        if hit and hit[0] == sig:
            return hit[1]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)) if since_days else None
    items: list = []   # (prob, won_bool, sport, market)
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        start = d.get("start") or ""
        if cutoff is not None:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except (ValueError, AttributeError):
                dt = None
            if dt is None or dt < cutoff:
                continue
        if _is_world_cup(d):         # Coupe du Monde EXCLUE de la calibration (combiné non calibrable +
            continue                 # décision produit : la CdM ne pèse dans aucune stat).
        stored = d.get("bets") or []
        if not stored:
            continue
        sport = d.get("sport") or ""
        mid = os.path.basename(p)[len(sport) + 1:-5]
        parsed = None
        for i, b in enumerate(stored):
            res = b.get("result")
            if res not in ("won", "lost"):
                continue
            prob = b.get("prob")
            if prob is None:
                if parsed is None:
                    parsed = bets_of(sport, mid)
                prob = parsed[i].get("prob") if i < len(parsed) else None
            if prob is None or prob < min_conf:
                continue
            mkt = _MARKET_FAMILY.get((b.get("code") or "").split()[0] if b.get("code") else "", "Autre")
            items.append((prob, res == "won", sport, mkt))

    out = _calib_agg([(p, w) for p, w, _s, _m in items])
    _SPL = {"foot": "Football", "tennis": "Tennis", "basket": "Basket"}
    by_sport = {}            # par SPORT, avec les TYPES DE PARIS du sport en SOUS-CATÉGORIE (`markets`)
    for sp in ("foot", "tennis", "basket"):
        sub = [(p, w) for p, w, s, _m in items if s == sp]
        if not sub:
            continue
        agg = _calib_agg(sub)
        mkts = {}            # sous-catégories : chaque type de pari DE CE SPORT (≥3 paris)
        for mk in sorted({m for _p, _w, s, m in items if s == sp}):
            msub = [(p, w) for p, w, s, m in items if s == sp and m == mk]
            if len(msub) >= 3:
                mkts[mk] = _calib_agg(msub)
        agg["markets"] = mkts
        by_sport[_SPL[sp]] = agg
    by_market = {}           # par FAMILLE (tous sports) -> sert à l'optimisation (auto_exclusions)
    for mk in sorted({m for _p, _w, _s, m in items}):
        sub = [(p, w) for p, w, _s, m in items if m == mk]
        if len(sub) >= 3:        # marché avec trop peu de paris -> pas affiché (bruit)
            by_market[mk] = _calib_agg(sub)
    out["by_sport"] = by_sport
    out["by_market"] = by_market
    if sig is not None:
        _CALIB_RES_CACHE[min_conf] = (sig, out)
    return out


def bet_detail(sport: str | None = None, pari: int | None = None,
               since_days: int | None = None) -> list[dict]:
    """Liste des PARIS réglés (pour le drill-down) filtrés par sport / position de pari / période.
    Trié du plus récent au plus ancien. Chaque entrée : start, home, away, comp, pari (n°), sel,
    result, odds."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)) if since_days else None
    out = []
    for p in glob.glob(os.path.join(DIR, f"{sport}_*.json" if sport else "*.json")):
        d = _meta_load(p)
        if not d or (sport and d.get("sport") != sport):
            continue
        start = d.get("start") or ""
        if cutoff is not None:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except (ValueError, AttributeError):
                dt = None
            if dt is None or dt < cutoff:
                continue
        if _is_world_cup(d):         # Coupe du Monde EXCLUE du drill-down (non comptée dans les stats).
            continue
        for i, b in enumerate(d.get("bets") or []):
            if i >= len(_BET_KEYS) or (pari is not None and i != pari):
                continue
            res = b.get("result")
            if res in ("won", "lost", "push"):
                od = b.get("odds")
                pnl = (round(float(od) - 1, 2) if (res == "won" and od)
                       else (-1.0 if res == "lost" else 0.0))   # gain/perte mise plate 1u
                out.append({"start": start, "home": d.get("home", ""), "away": d.get("away", ""),
                            "comp": d.get("comp", ""), "sport": d.get("sport"), "pari": i + 1,
                            "sel": b.get("sel", ""), "result": res, "odds": od, "pnl": pnl})
    out.sort(key=lambda x: x["start"] or "", reverse=True)
    return out


_ODDS_BUCKETS = ((1.0, 1.30, "1.00–1.30"), (1.30, 1.50, "1.30–1.50"), (1.50, 1.70, "1.50–1.70"),
                 (1.70, 2.00, "1.70–2.00"), (2.00, 99.0, "2.00 +"))
_CONF_BANDS = ((0, 65, "< 65 %"), (65, 70, "65–70 %"), (70, 75, "70–75 %"),
               (75, 80, "75–80 %"), (80, 101, "80 % +"))


def perf_breakdown(since_days: int | None = None) -> dict:
    """ANALYSES ACTIONNABLES (lecture seule) pour piloter l'amélioration du système : ROI + réussite
    par TRANCHE DE COTE, par MARCHÉ et par TRANCHE DE CONFIANCE. Mise plate 1u ; ROI = profit ÷ misé.
    N'altère AUCUNE donnée — sert l'affichage des stats. Caché par signature du dossier."""
    sig = _dir_sig() if since_days is None else None
    if sig is not None:
        hit = _PERF_CACHE.get("v")
        if hit and hit[0] == sig:
            return hit[1]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)) if since_days else None
    # DIAGNOSTIC : on prend TOUS les paris réglés (pas seulement le recommandé) — c'est l'outil qui
    # montre OÙ le système fuit (zones de cote, marchés perdants) ; il a besoin de tout l'échantillon.
    # (Le suivi/courbe `stats_full`, lui, ne compte QUE le pari recommandé = « ce que tu jouerais ».)
    items = []   # (odds, prob, market, result)
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        start = d.get("start") or ""
        if cutoff is not None:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except (ValueError, AttributeError):
                dt = None
            if dt is None or dt < cutoff:
                continue
        if _is_world_cup(d):         # Coupe du Monde EXCLUE de la perf par marché (non comptée).
            continue
        for i, b in enumerate(d.get("bets") or []):
            if i >= len(_BET_KEYS):
                break
            res = b.get("result")
            if res in ("won", "lost", "push"):
                items.append((b.get("odds"), b.get("prob"), market_of(b.get("code") or ""), res))

    def agg(label, lst):
        won = sum(1 for _o, r in lst if r == "won")
        lost = sum(1 for _o, r in lst if r == "lost")
        push = sum(1 for _o, r in lst if r == "push")
        staked = won + lost + push
        profit = sum((float(o) - 1) if (r == "won" and o) else (-1.0 if r == "lost" else 0.0)
                     for o, r in lst)
        sett = won + lost
        return {"label": label, "n": len(lst), "won": won, "settled": sett,
                "pct": (round(100 * won / sett) if sett else None),
                "roi": (round(100 * profit / staked) if staked else None),
                "profit": round(profit, 2)}

    def bucketize(bands, key):
        groups = {lbl: [] for *_, lbl in bands}
        for od, prob, _mk, res in items:
            v = od if key == "odds" else prob
            if v is None:
                continue
            for lo, hi, lbl in bands:
                if lo <= v < hi:
                    groups[lbl].append((od, res))
                    break
        return [agg(lbl, lst) for lbl, lst in groups.items() if lst]

    mkg = {}
    for od, _prob, mk, res in items:
        mkg.setdefault(mk, []).append((od, res))
    by_market = sorted((agg(mk, lst) for mk, lst in mkg.items() if len(lst) >= 3),
                       key=lambda x: (x["roi"] is None, -(x["roi"] or 0)))
    out = {"by_odds": bucketize(_ODDS_BUCKETS, "odds"),
           "by_conf": bucketize(_CONF_BANDS, "conf"),
           "by_market": by_market}
    if sig is not None:
        _PERF_CACHE["v"] = (sig, out)
    return out


def _result_badge(res: dict | None) -> str:
    """Bandeau résultat du pari « le plus sûr » après match : ✅ réussi / ❌ perdu / ➖ remboursé,
    ou simplement le score si non vérifiable. '' si pas encore réglé."""
    if not res:
        return ""
    pr, sc = res.get("pick_result"), res.get("score") or ""
    cls, txt = {"won": ("win", "✅ Pari réussi"), "lost": ("lose", "❌ Pari perdu"),
                "push": ("push", "➖ Pari remboursé")}.get(pr, ("nv", "Résultat connu"))
    sco = f'<span class="da-res-sc">{html.escape(sc)}</span>' if sc else ""
    return f'<div class="da-res da-res-{cls}">{txt} {sco}</div>'


def _links_bar(m: dict | None) -> str:
    """Bannières PLEINE LARGEUR SofaScore / Unibet (depuis les URLs du sidecar), côte à côte sur
    la largeur du cadre. '' si aucune. Portées PAR LA CARTE (cf. web._links_for_url) ; plus dans
    l'analyse dépliée -> pas de doublon au clic « analyse »."""
    m = m or {}
    btns = []
    if m.get("sofa_url"):
        # PAS de target="_blank" : un universal link iOS ouvre l'app SofaScore ; en nouvel onglet
        # ça laisserait un onglet « about:blank » orphelin. Navigation même onglet -> 0 page vierge.
        btns.append(f'<a class="lnk-bn lnk-bn-sofa" href="{html.escape(m["sofa_url"])}" '
                    'rel="noopener" aria-label="Voir sur SofaScore" title="Voir sur SofaScore">'
                    '<span class="lnk-dot"></span>SofaScore<span class="lnk-arr">↗</span></a>')
    if m.get("unibet_url"):
        # Unibet : NOUVEL onglet -> BETSFIX reste ouvert derrière (l'utilisateur ne quitte pas l'app).
        btns.append(f'<a class="lnk-bn lnk-bn-uni" href="{html.escape(m["unibet_url"])}" target="_blank" '
                    'rel="noopener" aria-label="Jouer sur Unibet" title="Jouer sur Unibet">'
                    '<span class="lnk-dot"></span>Unibet<span class="lnk-arr">↗</span></a>')
    return f'<div class="da-links">{"".join(btns)}</div>' if btns else ""


def links_html(sport: str, match_id) -> str:
    """Bannières SofaScore / Unibet d'un match (depuis le sidecar), à poser SUR la carte."""
    return _links_bar(meta(sport, match_id))


def render(sport: str, match_id) -> str | None:
    """HTML prêt à afficher de l'analyse de ce match, ou None si pas d'analyse. En tête : bandeau
    résultat ✓/✗ (si réglé). Les bannières SofaScore/Unibet ne sont PLUS ici : elles sont portées
    par la carte (cf. web._links_for_url) pour éviter un doublon à l'ouverture de l'analyse."""
    md = load(sport, match_id)
    if not md:
        return None
    m = meta(sport, match_id) or {}
    return _result_badge(m.get("result")) + to_html(md)
