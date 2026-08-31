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


_DIR_SIG_CACHE: list = [0.0, ()]   # [ts_monotone, sig] : cache TRÈS court (anti-scandir répété intra-rendu)


def _dir_sig() -> tuple:
    """Signature (noms + mtimes ns) de TOUS les sidecars — clé d'invalidation des caches agrégés
    (calibration, stats). Cache 2 s : `_dir_sig` était appelé ~7×/rendu (chacun scandir+stat ~460 fichiers
    = tempête de ~3500 stats, ~0,4 s) ; 2 s suffit pour partager UN scandir sur tout un rendu sans risque
    (les agrégats lag déjà ~15 s via le warmer ; selfcheck lit à part). Un scandir vaut mieux que re-parser N JSON."""
    now = time.monotonic()
    if now - _DIR_SIG_CACHE[0] < 2.0:
        return _DIR_SIG_CACHE[1]
    try:
        sig = tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(DIR)
                           if e.name.endswith(".json")))
    except OSError:
        sig = ()
    _DIR_SIG_CACHE[0], _DIR_SIG_CACHE[1] = now, sig
    return sig


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
    # SCORE FINAL CAPTÉ = match TERMINÉ, même si le PICK n'est pas « réglé » (cas provisoire/abstention : le
    # score + les périodes sont écrits au règlement des fantômes, mais pas de pick_result -> is_settled False).
    # Sans ça, un match FINI restait « inprogress » tant que la fenêtre de durée n'était pas écoulée (tennis
    # 210 min) -> le provisoire n'était jamais validé alors que sa jambe l'était (bug user 2026-07-28,
    # Nishikori « +19.5 jeux »). Le score n'est écrit QUE sur un match confirmé fini (cf. settle-never-on-live).
    _res = d.get("result") or {}
    if (_res.get("raw") or {}).get("periods") or \
            (_res.get("score") and any(c.isdigit() for c in str(_res.get("score")))):
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


# Au-delà de N h APRÈS le coup d'envoi PROGRAMMÉ, un match NON réglé est jeté du board (fini depuis
# longtemps sans résultat exploitable). Fenêtre PAR SPORT : le tennis (best-of-5) + les démarrages
# TARDIFS (ordre des courts à Wimbledon : un match « prévu 13h40 » peut débuter à 16h30) dépassent 6 h
# EN PLEIN JEU -> il faut plus large, sinon un match en direct disparaît du site (bug Auger-Aliassime–
# Djokovic). Le live COLLANT (match_select.sticky_live) prolonge encore tant que le flux renvoie un score.
_STALE_AFTER_H = {"foot": 6, "basket": 6, "tennis": 9}


def list_for(sport: str, include_background: bool = False) -> list[dict]:
    """Liste des matchs ANALYSÉS (sidecars) à venir / récents, triés par coup d'envoi.
    C'est la SOURCE du board : seuls les matchs analysés avec la nouvelle technique y figurent.
    ⚠️ Un sport en ARRIÈRE-PLAN (tennis/basket) renvoie [] par défaut : il est analysé + tracé (ROI simulé)
    mais JAMAIS affiché dans la vue « Tous » de la page des paris (demande user 2026-07-24). `include_background
    =True` force son inclusion — utilisé UNIQUEMENT quand ce sport est EXPLICITEMENT sélectionné dans Pronos
    (vue mono-sport), pour que ses paris joués y apparaissent COMME son combiné du jour (demande user 2026-07-29)."""
    if sport in background_sports() and not include_background:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for p in glob.glob(os.path.join(DIR, f"{sport}_*.json")):
        d = _meta_load(p)
        if not d:
            continue
        # ABSTENTION shadow-only (sidecar `abstained` = méta + fantômes SEULS, aucun pari) : JAMAIS au board
        # (demande user 2026-07-10 : un match sans value n'est ni retenu ni affiché). Il nourrit UNIQUEMENT
        # la calibration (fantômes). Garde-fou : un stat_bet figé le garderait (ne devrait pas arriver).
        if (d.get("abstained") and not isinstance(d.get("stat_bet"), dict)
                and not (isinstance(d.get("confidence_bet"), dict) and d["confidence_bet"].get("code"))
                and not (isinstance(d.get("value_bet"), dict) and d["value_bet"].get("code"))):
            continue    # abstention pure -> cachée ; SAUF si elle porte un pari de CONFIANCE ou de VALUE mécanique
        st = d.get("start")
        try:
            dt = datetime.fromisoformat(st.replace("Z", "+00:00")) if st else None
        except (ValueError, AttributeError):
            dt = None
        # on garde l'à-venir, l'en-cours ET les terminés. Les matchs RÉGLÉS (présents dans les stats)
        # restent visibles indéfiniment dans « Terminés » ; on ne jette que les NON réglés trop vieux
        # (fenêtre PAR SPORT après le coup d'envoi : match fini depuis longtemps sans résultat exploitable)
        # — SAUF s'il a été VU EN DIRECT très récemment (démarrage tardif encore en cours -> live collant).
        if (dt is not None and dt < now - timedelta(hours=_STALE_AFTER_H.get(sport, 6))
                and not is_settled(d)):
            # « Live collant » borné : on ne sauve un match périmé QUE s'il a été vu en direct récemment
            # ET que son coup d'envoi date de MOINS de 15 h. Sans ce plafond, sticky_live (clé par NOMS
            # d'équipes seulement) ressusciterait un ANCIEN match d'une série (mêmes équipes) qu'un match
            # ULTÉRIEUR en direct fait apparaître « vu récemment » (bug audit). 15 h couvre un démarrage
            # très tardif + un match long, mais exclut la manche de la veille.
            _still_live = False
            if dt > now - timedelta(hours=15):
                try:
                    from app import match_select as _ms
                    _still_live = _ms.sticky_live(sport, d.get("home"), d.get("away"))
                except Exception:
                    _still_live = False
            if not _still_live:
                continue
        # Mode strict RENFORCÉ (demande user 2026-07-01) : un match sans pari PUBLIABLE n'apparaît
        # PLUS DU TOUT dans l'app — même terminé. « Publiable » = un COMBINÉ, OU un simple qui a (ou
        # aurait) été RETENU (≥65 % + value positive + garde-fous). Un favori sans value = ABSTENTION
        # -> caché (avant : on vérifiait juste qu'un pari EXISTE, donc les abstentions passaient). On
        # ne montre QUE ce sur quoi on mise vraiment. (Sidecar/.md gardés sur disque : cache du scan.)
        # Mode selon l'état : TERMINÉ -> pari JOUÉ (for_history = ce qui est dans les stats, marchés
        # exclus après coup INCLUS) ; À VENIR -> pari RECOMMANDÉ maintenant (publication = ce qu'on
        # poste, marchés exclus retirés). -> liste = bannière = carte = Telegram = stats, cohérent.
        _has_combo = bool((d.get("combo") or {}).get("legs"))
        if not _has_combo and load(sport, d.get("id")) is not None \
                and retained_bet(sport, d.get("id"), for_history=is_settled(d)) is None:
            # SAUF si le pari a DÉJÀ été COMPTÉ (stat_bet figé, survit à un reset du canal ET à la dérive de
            # calibration ; ancre ROBUSTE des terminés — bug vécu Auger-Aliassime–Djokovic) OU si un prono a
            # été PUBLIÉ et que le match n'est PAS ENCORE RÉGLÉ (cohérence Telegram=site pour l'à-venir).
            # RÈGLE (demande user 2026-07-09) : un match RÉGLÉ mais NON COMPTÉ (stat_bet vide) ne s'affiche
            # PLUS, même s'il a été publié — le site ne montre QUE ce qui est dans les stats. Un pari publié
            # mais non retenu au règlement (ex. EV pile au seuil) n'a aucun intérêt affiché (« pas de pari ») ;
            # il reste réglé + calibré en coulisses (fantômes). Évite les cartes « pas de pari » sur terminés.
            _kept = isinstance(d.get("stat_bet"), dict)
            if not _kept and not is_settled(d):
                try:
                    from app import notify as _notify
                    # PUBLIÉ (à venir) -> gardé SEULEMENT s'il reste un pari à AFFICHER (figé for_history).
                    # Si les bets ont été VIDÉS au rescan (abstention pure), on ne garde PAS ici : le
                    # PROVISOIRE doré l'affichera à la place (comme les autres abstentions), au lieu d'une
                    # carte « Analysé · pas de pari conseillé » = bruit (retour user 2026-07-11). Le cas
                    # « publié + pari encore figé » (Auger-Aliassime–Djokovic) reste gardé car
                    # retained_bet(for_history=True) renvoie le pari publié.
                    _kept = (bool(_notify.get_prono(str(d.get("id"))))
                             and retained_bet(sport, d.get("id"), for_history=True) is not None)
                except Exception:
                    _kept = False
            if not _kept:
                continue
        d["_start_dt"] = dt
        out.append(d)
    out.sort(key=lambda d: (d["_start_dt"] is None, d.get("_start_dt") or now))
    return out


def iter_stat_bets():
    """Itère les sidecars RÉGLÉS portant un pari FIGÉ (`stat_bet` won/lost), tous sports — SANS le filtrage
    lourd de `list_for` (qui appelle `retained_bet` par sidecar). Rend `(sport, stat_bet, dt_utc)`. Sert au
    bilan `_daily_results_map` du calendrier (perf : évite ~138 `retained_bet` inutiles par calcul). Lecture
    via `_meta_load` (caché par mtime) -> bon marché en répétition. Iso-comportement : mêmes entrées que
    `list_for` filtrées ensuite sur `stat_bet.result`."""
    for sport in ("foot", "tennis", "basket"):
        for p in glob.glob(os.path.join(DIR, f"{sport}_*.json")):
            d = _meta_load(p)
            if not d:
                continue
            if d.get("roi_void"):        # pari EXCLU du ROI/historique (correction d'approche, user 2026-08-07)
                continue                 # -> hors stats/série ; reste en CALIBRATION (shadows intacts)
            # UN MATCH = UN PARI (user 2026-08-07) : on ne rend QUE le pari retenu (stat_bet). Le pari du 1er
            # scan (`stat_bet_first`) N'EST PLUS compté (fini le double-comptage d'un match re-scané).
            sb = d.get("stat_bet")
            if not (isinstance(sb, dict) and sb.get("result") in ("won", "lost")):
                continue
            st = d.get("start")
            try:
                dt = datetime.fromisoformat(st.replace("Z", "+00:00")) if st else None
            except (ValueError, AttributeError):
                dt = None
            yield sport, sb, dt


def iter_meta(sport: str):
    """Itère les sidecars BRUTS d'un sport (méta chargée + `_start_dt` posé), SANS le filtrage lourd de
    `list_for` (pas de `retained_bet`/`load` par sidecar). Pour les appelants qui font DÉJÀ leur PROPRE
    filtrage plus strict (ex. `_past_day_cards` : date + is_settled + a un pari) -> iso-résultat, sans le
    coût de list_for (perf 2026-07-20)."""
    for p in glob.glob(os.path.join(DIR, f"{sport}_*.json")):
        d = _meta_load(p)
        if not d:
            continue
        st = d.get("start")
        try:
            d["_start_dt"] = datetime.fromisoformat(st.replace("Z", "+00:00")) if st else None
        except (ValueError, AttributeError):
            d["_start_dt"] = None
        yield d


def _inline(s: str) -> str:
    s = html.escape(s)
    s = _BOLD.sub(r"<b>\1</b>", s)
    s = _LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s


def _strip(md: str) -> str:
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)          # vire l'en-tête commentaire
    md = re.sub(r"^---+\s*$", "", md, flags=re.M)            # séparateurs ---
    md = re.sub(r"^\s*PICK:.*$", "", md, flags=re.M)         # ligne technique de règlement (cachée)
    md = re.sub(r"^\s*(?:POOL|CALIB|COMBO|PROV):.*$", "", md, flags=re.M)  # lignes techniques (vivier/calib/combo/prov)
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



_RISK = (("🟢", "ok"), ("🟠", "mid"), ("🔴", "hi"))
_SAFETY = {"ok": "Sûreté élevée", "mid": "Sûreté moyenne", "hi": "Sûreté faible"}
_BET_LABELS = ("Pari 1", "Pari 2", "Pari 3")


def _norm_sel(s: str) -> str:
    """Clé de correspondance d'une sélection de pari (insensible casse/espaces/gras markdown)."""
    return re.sub(r"\s+", " ", _BOLD.sub(r"\1", s or "")).strip().lower()


def pretty_sel(sel: str, home: str = "", away: str = "") -> str:
    """Normalise l'AFFICHAGE d'un intitulé de DOUBLE CHANCE pour qu'un MÊME pari s'affiche PAREIL partout
    (demande user 2026-07-13). On GARDE la mention technique « 1X / X2 / 12 » (demandée) ET on la PRÉCISE
    avec les équipes -> « Double chance 1X (<domicile> ou nul) ». Marche dans les 2 sens : la forme code
    (« Double chance 1X ») ET la forme explicite (« Double chance <équipe> ou nul ») donnent le même
    libellé. SOURCE UNIQUE (web/combo/notify). Renvoie le libellé tel quel si ce n'est pas une double chance."""
    s = re.sub(r"\s+", " ", (sel or "").strip())
    if not s:
        return s
    # COTE COLLÉE À L'INTITULÉ : certains sels sont stockés « <pari> @1.36 » (bug user 2026-07-23 : « Moins de
    # 3.5 buts @1.36 ») → la cote est DÉJÀ dans la colonne COTE, donc redondante dans le titre. On la retire de
    # l'AFFICHAGE (le `sel` stocké reste intact → règlement/code inchangés).
    s = re.sub(r"\s*@\s*\d+(?:[.,]\d+)?\s*$", "", s).strip()
    # UNIFORMISATION AMONT (demande user 2026-07-23, IMPÉRATIVE « ça ne doit plus JAMAIS arriver ») : deux
    # écritures d'une MÊME issue doivent converger. Normalisations génériques AVANT les cas précis :
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)                               # décimale FR « 2,5 » -> « 2.5 »
    s = re.sub(r"\s*\((?:1x2|temps r[ée]glementaire\s*\d?|hcap[^)]*|setwin[^)]*)\)\s*", " ",
               s, flags=re.I).strip()                                    # suffixe technique « (1X2) / (Temps régl. 1) »
    s = re.sub(r"\s*[—–:(]\s*oui\s*\)?\s*$", "", s, flags=re.I)         # suffixe affirmatif redondant « : Oui / (Oui) »
    s = re.sub(r"\s*[—–:]\s*(non)\b", r" \1", s, flags=re.I)            # séparateur avant « Non » -> espace (BTTS)
    s = re.sub(r"\b(gagne|vainqueur)\w*\s+(?:le|du)\s+match\b", r"\1", s, flags=re.I)  # « gagne le match » -> « gagne »
    s = re.sub(r"\s+dans le match\s*$", "", s, flags=re.I)              # « … jeux dans le match » -> « … jeux »
    s = re.sub(r"\bvainqueur(?:e|es|s)\b", "vainqueur", s, flags=re.I)  # « vainqueure/vainqueurs » -> « vainqueur »
    # TENNIS « remporte au moins un set » : converge toutes les variantes (au moins 1 set / ≥ 1 set / gagne au
    # moins un set / suffixes Hcap +1.5 set…) vers UNE forme. Le « au moins un set » = handicap +1.5 set déguisé.
    if re.search(r"(remporte|gagne).{0,12}(au moins|≥).{0,4}(1|un)\s+set|\+\s?1[.,]5\s+set", s, re.I):
        _who = re.split(r"\s+(?:remporte|gagne)\b|\s+\+\s?1[.,]5", s, flags=re.I)[0].strip(" -–—:(")
        if _who and not re.search(r"nombre|total|score|\bset\s+\d", _who.lower()):
            return f"{_who} remporte au moins un set"
    # TOTAL DU MATCH : « Nombre total de buts – Moins de 2.5 » -> « Moins de 2.5 buts » (forme unique). Cible
    # buts/points/jeux uniquement (les objets nommés corners/cartons ont leur propre glose).
    _mtot = re.match(r"^(?:nombre\s+)?total\s+(?:de\s+|d')?(buts?|points?|jeux)\s*[—–:\-]*\s+"
                     r"(plus|moins)\s+de\s+(\d+(?:\.\d+)?)", s, re.I)
    if _mtot:
        _u = {"but": "buts", "point": "points"}.get(_mtot.group(1).lower().rstrip("s"),
                                                    _mtot.group(1).lower())
        _u = "buts" if _u.startswith("but") else ("points" if _u.startswith("point") else "jeux")
        return f"{_mtot.group(2).capitalize()} de {_mtot.group(3)} {_u}"
    low = s.lower()
    # MARCHÉ DE PÉRIODE (mi-temps / quart) : ne JAMAIS le normaliser vers son équivalent MATCH ENTIER (audit
    # 2026-07-23 : « Double Chance - 1ère mi-temps X2 » devenait « Double chance X2 (<équipe> ou nul) » = une
    # AUTRE issue). Les branches DC / +0.5→DC / handicap ci-dessous sont réservées au match entier ; un sel de
    # période est rendu TEL QUEL. `(?<=\s)mt` : ne matche PAS les suffixes d'État brésiliens (« Cuiabá-MT »).
    _periode = bool(re.search(r"mi-temps|(?<=\s)mt\b|1[eè]re|2[eè]\b|quart", low))
    # VAINQUEUR simple : « <équipe> victoire » / « <équipe> gagne » / « <équipe> l'emporte » = MÊME pari que
    # « <équipe> vainqueur » (2 écritures d'une victoire sèche). L'intitulé doit être IDENTIQUE partout
    # (demande user 2026-07-20 : « l'intitulé du pari doit toujours être fait de la même manière ») -> on
    # uniformise le verbe d'affichage vers « vainqueur » (forme Unibet). PUREMENT AFFICHAGE (le `sel` stocké
    # reste intact -> règlement/codes inchangés). On NE touche PAS un marché plus précis (set/MT/quart/
    # handicap/score/double chance) où « victoire » est qualifiée.
    # Tolère un suffixe « (temps réglementaire) » : en FOOT « <équipe> gagne (temps réglementaire) » (code
    # REGTIME) = « <équipe> vainqueur » (code 1X2) — MÊME issue (gagner en 90 min) -> MÊME intitulé (bug user
    # 2026-07-23 : Bolívar « gagne (temps réglementaire) » vs Corinthians « vainqueur »). La glose « gagne dans
    # le temps réglementaire (90 min) » porte déjà la précision 90 min. « (prol. incl.) » N'est PAS toléré ici
    # (marché distinct des matchs KO) -> reste tel quel.
    _mw = re.match(r"^(.+?)\s+(?:victoire|vainqueur|gagne|l['’]emporte)"
                   r"\s*(?:\(?\s*temps\s+r[ée]glementaire\s*\)?)?\s*$", s, re.I)
    if _mw and not re.search(r"mi-temps|\bset\b|\bmt\b|1[eè]re|2[eè]|quart|handicap|[+\-]\s?\d|"
                             r"\bou nul\b|double chance|prol|\d\s*[-–]\s*\d", low):
        return f"{_mw.group(1).strip()} vainqueur"
    # +0.5 SUR UNE ÉQUIPE = « ne perd pas » = DOUBLE CHANCE (foot). On UNIFORMISE vers la forme double chance
    # pour qu'un « <équipe> +0.5 (ne perd pas / DC 1X) » et un « Double chance 1X (… ou nul) » s'affichent À
    # L'IDENTIQUE partout — titre ET glose (demande user 2026-07-17 : « uniformiser ce genre de paris »). On ne
    # convertit QUE si le contexte est bien une double chance (annotation DC / 1X / X2 / « ne perd pas ») -> ne
    # touche pas un +0.5 tennis/basket (= victoire, pas de nul). PUREMENT AFFICHAGE (le `sel` stocké intact).
    _m05 = re.search(r"^(.*?)\s*\+\s?0[.,]5\b", s)
    if _m05 and not _periode and re.search(r"\bdc\b|double chance|ne perd pas|\b1x\b|\bx2\b", low):
        _t05 = _m05.group(1).strip(" -–—()·:")
        if _t05:
            if re.search(r"\bx2\b", low):
                _c05 = "X2"
            elif re.search(r"\b1x\b", low):
                _c05 = "1X"
            else:                                       # pas de code explicite -> déduire du camp cité
                _tok05 = lambda nm: [t for t in re.findall(r"[a-zà-ÿ0-9]+", (nm or "").lower()) if len(t) >= 3]
                _c05 = ("1X" if any(t in _t05.lower() for t in _tok05(home))
                        else "X2" if any(t in _t05.lower() for t in _tok05(away)) else "1X")
            if home and away:
                _e05 = {"1X": f"{home} ou nul", "X2": f"{away} ou nul"}[_c05]
                return f"{_e05} ({_c05})"          # format « <équipe> ou nul (1X) » (user 2026-08-31)
            return f"Double chance {_c05}"
    # HANDICAP d'équipe : normaliser l'AFFICHAGE vers UNE forme unique « Handicap <type> <équipe> <±N> »
    # (demande user 2026-07-14 : « Handicap St Johnstone -1.5 » et « Partick -1.5 (handicap) » = MÊME pari,
    # 2 écritures). Le TYPE n'est PAS toujours « asiatique » (correctif demande user 2026-07-16) :
    #   • DEMI-POINT (−1.5 / −2.5…) SANS mention 3 voies -> « asiatique » : sur Unibet ces lignes sont dans
    #     le marché « Handicap Asiatique » (pas de nul possible) -> même libellé qu'Unibet.
    #   • LIGNE ENTIÈRE (+2 / −1…) OU mention explicite « 3 voies / 3-way / européen » -> « 3 voies » : c'est
    #     un handicap à 3 issues (le nul compte), PAS un asiatique. Écrire « asiatique » y était FAUX et
    #     s'auto-contredisait (« asiatique … 3 voies »).
    # PUREMENT AFFICHAGE : le `sel` STOCKÉ ne change pas, donc `code_from_pick`/le règlement restent intacts.
    # None-safe.
    if ("handicap" in low or re.search(r"\bhand\.?\b", low)) and not _periode:
        # HANDICAP EUROPÉEN à SCORE DE RÉFÉRENCE « (X-Y) » (« 3-Way Handicap (3-0) <équipe> ») : le « -Y »
        # que capturerait _mh est un MORCEAU du score, pas une ligne (audit 2026-07-23 : rendait « Handicap
        # 3 voies 3 ) Nouvelle Zélande -0 » = une autre issue). Non normalisable proprement -> TEL QUEL.
        if re.search(r"\(\s*\d+\s*[-–]\s*\d+\s*\)", s):
            return s
        # Annotations parenthésées ENTIÈRES retirées AVANT extraction (« (hand., prol. incl.) », « (prol.
        # incluses) », « (3-Way Handicap 1-0) »…) : l'ancien nettoyage au mot laissait des débris « (. » /
        # « (F » affichés en prod (audit 2026-07-23 : « Portland Fire (. +11.5 »). « (F) » est PRÉSERVÉ
        # (ne contient ni hand/prol/incl).
        _sh = re.sub(r"\(\s*[^)]*(?:hand|prol|incl)[^)]*\)", " ", s, flags=re.I)
        _mh = re.search(r"([+\-−–]\s?\d+(?:[.,]\d+)?)", _sh)
        if _mh:
            _sign = re.sub(r"\s+", "", _mh.group(1)).replace("−", "-").replace("–", "-")
            _team = (_sh[:_mh.start()] + " " + _sh[_mh.end():])
            _team = re.sub(r"\(?\s*handicap\s*(?:asiatique|europ\w*|3\s*voies|3-?way)?\s*\)?"
                           r"|\bhand\.?\b|\b3\s*voies\b|\b3-?way\b", "", _team, flags=re.I)
            # strip SANS « () » : un strip de parenthèses en bordure mutilait « … (F) » -> « … (F » (audit).
            _team = re.sub(r"\s+", " ", _team).strip(" -–—·:")
            # Retire une SÉLECTION VERBEUSE résiduelle d'un handicap 3 voies : le nom d'équipe s'arrête à la
            # 1ère virgule ou au 1er verbe de résultat (bug user 2026-07-22 : « Handicap 3 voies Botafogo-RJ ,
            # ne perd pas par 2+ +1 » illisible → « Handicap 3 voies Botafogo-RJ +1 »). La glose (_plain_market)
            # porte déjà l'explication en clair. Le `sel` stocké reste intact (règlement inchangé).
            _team = re.split(r"\s*,|\s+(?:ne\s+perd\s+pas|gagne|perd\b|l['’]emporte|remporte)",
                             _team, flags=re.I)[0].strip(" -–—·:")
            # GARDE-FOU (audit 2026-07-23) : parenthèses DÉSÉQUILIBRÉES dans le libellé reconstruit -> on
            # renvoie le sel BRUT (jamais un débris affiché).
            if _team and _team.count("(") == _team.count(")"):
                _half = bool(re.search(r"[.,]5(?!\d)", _sign))          # ligne en demi-point ?
                # mention « 3 voies » lue sur le texte NETTOYÉ (_sh) : une annotation retirée « (3-Way
                # Handicap 1-0) » ne doit pas re-qualifier une ligne demi-point (-1.5 = 2 issues) en 3 voies.
                _three = bool(re.search(r"3\s*voies|3-?way|europ", _sh.lower()))
                _kind = "asiatique" if (_half and not _three) else "3 voies"
                return f"Handicap {_kind} {_team} {_sign}"
            return s
    if _periode or "double chance" not in low:   # période -> tel quel (jamais normalisé en DC match entier)
        return s
    m = re.search(r"\b(1x|x2|12)\b", low)
    code = m.group(1).upper() if m else None
    if code == "12" and "nul" in low:              # « 12 » + « ou nul » = contradictoire (nul inclus -> 1X/X2)
        code = None                                # -> re-déduire du camp cité (aligné code_from_pick, 2026-07-29)
    if not code:                                   # forme explicite -> déduire le code des équipes citées
        def _cited(name):
            toks = [t for t in re.findall(r"[a-zà-ÿ0-9]+", (name or "").lower()) if len(t) >= 3]
            return bool(toks) and any(t in low for t in toks)
        hh, aa, nul = _cited(home), _cited(away), "nul" in low
        code = "1X" if (hh and nul and not aa) else "X2" if (aa and nul and not hh) \
            else "12" if (hh and aa) else None
    if not code:
        return s
    if home and away:
        expl = {"1X": f"{home} ou nul", "X2": f"{away} ou nul", "12": f"{home} ou {away}"}[code]
        return f"{expl} ({code})"                   # format « <équipe> ou nul (1X) » (user 2026-08-31)
    return f"Double chance {code}"


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



_MIN_CONF = 65   # seuil de confiance MINI pour recommander (calibration réelle : sous 65 %, le système
#                  est sur-confiant et perd ; à partir de 65 % il est fiable). Pas de repli en-dessous.

# LISTE BLANCHE « MARCHÉS PROUVÉS » (user 2026-08-24, option 1 : Over GARDÉ) — la sélection ne retient QUE les
# familles de marché qui ont fait leurs preuves en période gagnante (début juillet : toutes à ~88-92 %). Un pari
# dans une famille NON prouvée (cartons, buteurs, mi-temps, hors-jeu, tirs totaux…) est écarté du pool ->
# abstention si c'était le seul candidat. S'ajoute aux auto-exclusions data-driven (`ok`) + bans durs
# (Corners/BTTS). Réversible : PROVEN_MARKETS_ONLY = False -> comportement d'avant.
PROVEN_MARKETS_ONLY = True
_PROVEN_MARKETS_FOOT = frozenset({"Vainqueur", "Double chance", "Total Under", "Total Over",
                                  "Total équipe", "Handicap"})

# CONFIDENCE-FIRST (user 2026-08-29 : « redevenir comme le 16/07 ») — RESTAURE le comportement de sélection
# observé le 16/07 (captures user). Ce jour-là, des FAVORIS COURTS à confiance CALIBRÉE haute étaient retenus
# et joués MÊME quand la value BRUTE était légèrement négative/nulle (ex. Tijuana +1.5 78 %@1.25 = value −2 %,
# mais recalibré 83,5 % ce jour-là -> EV +4 % -> retenu). Depuis, la calibration a APPRIS (le même 78 % brut
# se recalibre à 78 % -> EV −2 % -> skip) et ces favoris disparaissent. Sur demande explicite user, on les
# REMET : quand AUCUN value play (EV ≥ +3 %) n'existe, on retient le PLUS SÛR (confiance calibrée max) des
# favoris COURTS (cote 1.20-1.60) à confiance ≥ 75 %, tant que la value n'est pas CATASTROPHIQUE (≥ −5 %).
# N'affaiblit RIEN d'autre : bans durs (Corners/BTTS), liste blanche marchés prouvés, `ok` réglable et garde
# de cote s'appliquent toujours (le candidat sort du `pool` déjà filtré). Réversible : CONFIDENCE_FIRST_ON=False
# -> comportement d'avant (value-only). Assumé : légère baisse de ROI possible contre un meilleur taux de
# réussite (favoris à forte chance de passer) — c'est le compromis du 16/07, voulu par l'user.
# FOOT = uniquement les 2 sélecteurs mécaniques (confiance 93% + value B). Coupe la retombée sur l'ancien
# pick EV de Claude (`_recommend`) pour un match foot À VENIR sans pari mécanique -> abstention propre (plus
# de pollution du tier value par des picks hors-profil). Réversible : False -> ancien comportement.
FOOT_MECHANICAL_ONLY = True

# OPTION B (user 2026-08-29) : ne RÉVÉLER un pari mécanique qu'une fois FINAL (re-vérifié à la vague ~1h avant
# KO : prematch_done / published_bet / réglé / historique). Avant ça, le pari du matin est PROVISOIRE ->
# retained_bet renvoie None et l'app montre le match en « À analyser / en attente » (ni pari révélé, ni
# abstention). Évite qu'un abonné mise un pari qui change/disparaît. Affichage seul (la vague publie normalement).
REVEAL_ONLY_FINAL = True

CONFIDENCE_FIRST_ON = False  # SUPERSEDED 2026-08-29 : le backtest a montré que les favoris à value NÉGATIVE
#   ne sont PAS optimaux ; le vrai pari de confiance est le profil 93% mécanique (app.confidence_pick, DC/
#   Handicap EV-indifférent). On coupe donc ce confidence-first ad-hoc -> le pick VALUE (placeholder) redevient
#   value-only propre (EV≥+3%) en attendant le sélecteur value dédié. Réactivable si besoin.
_CF_MIN_CONF = 75            # confiance CALIBRÉE mini pour un pari confidence-first
_CF_COTE_LO = 1.20          # bande de cote COURTE (favori net) — comme les picks du 16/07 (1.24-1.26)
_CF_COTE_HI = 1.60
_CF_EV_FLOOR = -0.05        # on tolère une value légèrement négative (Tijuana −2 %), pas catastrophique


def _recommend(data: list, ok: set | None = None, cprobs: list | None = None,
               codes: list | None = None, conf_first: bool = True) -> dict:
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
    # Confiance ≥ 65 % EXIGÉE (sinon on s'abstient). GARDE-FOUS de COTE mesurés (perf_breakdown) :
    #  • cote 1.70-2.00 = ROI négatif -> on exige 72 % de confiance recalibrée dans cette zone ;
    #  • cote ≥ 2.00 = ROI négatif -> exclue de la reco (les grosses cotes saignent).
    # L'exclusion par MARCHÉ n'est plus codée en dur : elle passe par `ok` (cf. auto_exclusions, qui
    # exclut un marché DATA-DRIVEN si n ≥ 25 ET ROI/calibration mauvais — pas de surapprentissage).
    pool = [s for s in scored
            if s[2] >= _MIN_CONF
            and (data[s[0]].get("cote") or 0) < 2.00
            and ((data[s[0]].get("cote") or 0) < 1.70 or s[2] >= 72)]
    # LISTE BLANCHE MARCHÉS PROUVÉS (user 2026-08-24) : ne garder que les familles prouvées. `codes` (marché par
    # candidat) fourni par l'appelant foot -> famille via market_of ; si `codes` absent, pas de filtre (repli sûr).
    if PROVEN_MARKETS_ONLY and codes:
        pool = [s for s in pool
                if market_of((codes[s[0]] if s[0] < len(codes) else "") or "") in _PROVEN_MARKETS_FOOT]
    if not pool:
        return {"idx": None, "verdict": "skip", "ev": None, "stake_pct": 0.0}
    i, ev, _prob = max(pool, key=lambda s: s[1])
    if ev < 0.03:
        # CONFIDENCE-FIRST (user 2026-08-29, retour au 16/07) : pas de value play, mais un FAVORI COURT à
        # confiance calibrée HAUTE reste jouable (chance de passer max). On prend le PLUS SÛR (cprob max,
        # départage EV) dans la bande de cote courte, value tolérée jusqu'à −5 %. Le candidat vient du `pool`
        # déjà filtré (65 %+, cote<2, marchés prouvés, `ok` réglable) -> aucun garde-fou contourné.
        if CONFIDENCE_FIRST_ON and conf_first:
            cf = [s for s in pool
                  if s[2] >= _CF_MIN_CONF
                  and _CF_COTE_LO <= (data[s[0]].get("cote") or 0) <= _CF_COTE_HI
                  and s[1] >= _CF_EV_FLOOR]
            if cf:
                i, ev, _prob = max(cf, key=lambda s: (s[2], s[1]))   # le plus SÛR d'abord
                b = data[i]["cote"] - 1
                kelly = ev / b if b > 0 else 0.0
                stake_pct = round(max(0.0, min(kelly * 0.25, 0.03)) * 100, 1)
                return {"idx": i, "verdict": "play", "ev": round(ev * 100), "stake_pct": stake_pct}
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


def _note_paras(txt: str, render=None) -> str:
    """Explication d'un pari découpée en PHRASES = une ligne par phrase (lisible, plus un pavé continu
    « qui ne donne pas envie d'être lu »). Coupe sur «. ! ? » suivi d'une MAJUSCULE (pas sur les
    décimales « 0,9 » — virgule en français). `render` = fonction d'échappement HTML (def. _inline)."""
    r = render or _inline
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÝÉÈÊ«])", (txt or "").strip()) if p.strip()]
    if len(parts) <= 1:
        return r(txt or "")
    return "".join(f'<div class="da-bk-line">{r(p)}</div>' for p in parts)


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
    notes, resid = [], []
    for it in _bullets(verdict):
        raw = re.sub(r"\*", "", it).strip()
        low = raw.lower()
        if "évit" in low or "skip" in low or "evit" in low:
            continue                                     # « À éviter / SKIP » RETIRÉ de l'affichage (demande user)
        # ANCRE FIABLE = la cote « @x.xx » : le NOM du pari peut contenir un « : » (« Total de buts :
        # Moins de 3.5 ») -> on NE découpe PAS sur le premier « : » (sinon sel tronquée + « : » qui
        # traîne en tête de l'explication). Tout AVANT @cote = sélection, tout APRÈS = pourquoi.
        m = re.search(r"@\s*[\d.,]+", raw)
        if not m:
            continue
        sel = re.sub(r"^pari\s*\d+\s*[:.\-—–]\s*", "", raw[:m.start()], flags=re.I).strip().rstrip("(").strip()
        why = raw[m.end():].strip(" :—–-().").strip()    # retire le « : »/tiret de liaison en tête
        if sel and why:
            notes.append((sel, _sentence_case(_units_to_pct(_strip_sources(why)))))
    # « Mise conseillée » RETIRÉE de l'affichage (demande utilisateur 2026-06-16) -> on ne la rend plus.
    resid_html = ""
    if resid:
        rows = "".join(
            f'<div class="da-bx {cls}"><div class="da-bx-h"><span class="da-bx-ic">{ic}</span>'
            f'<span class="da-bx-lbl">{html.escape(lbl)}</span></div>'
            f'<div class="da-bx-t">{_inline(txt)}</div></div>'
            for ic, lbl, txt, cls in resid)
        resid_html = f'<div class="da-bets-extra">{rows}</div>'
    return notes, resid_html


_FIRST_STATS_DAY_CACHE = None


def first_stats_day() -> str | None:
    """Premier jour (YYYY-MM-DD) où l'on a commencé à mesurer les stats du site (simples + combinés) —
    demande user 2026-07-24 : le suivi Betmines doit DÉMARRER à cette même date pour aligner sa courbe sur
    les 2 premiers graphiques. = min des dates des courbes d'équité simples/combinés. Mémoïsé (l'origine de
    l'historique est stable : elle ne peut que reculer sur un backfill, événement rare). None si vide."""
    global _FIRST_STATS_DAY_CACHE
    if _FIRST_STATS_DAY_CACHE is not None:
        return _FIRST_STATS_DAY_CACHE or None
    try:
        ds = [d for d in ((stats_full().get("overall") or {}).get("dates") or []) if d]
        ds += [d for d in (combo_stats().get("dates") or []) if d]
        day = min(ds)[:10] if ds else ""
    except Exception:
        day = ""
    _FIRST_STATS_DAY_CACHE = day
    return day or None


def verdict_line(cote, conf, ev, calibrated: bool = True, with_cote: bool = False,
                 hide_neg_value: bool = False, pick_html: str = "",
                 live_pct=None, live_trend: str = "", live_state: str = "",
                 result_html: str = "", bare: bool = False) -> str:
    """Bloc VERDICT PARTAGÉE (cartes de pari ET provisoires -> rendu IDENTIQUE). Refonte 2026-07-18
    (demande user « réorganise tout : aligné, pleine largeur, que l'utile et l'intuitif ») :
      (1) en-tête CONFIANCE = qualificatif + % coloré (par niveau) ;
      (2) BARRE de confiance PLEINE LARGEUR (remplissage animé) + marqueur MARCHÉ (proba implicite) ;
      (3) GRILLE de métriques CENTRÉES sur toute la largeur : Marché · Value · Cote (label / valeur).
    `conf` = confiance affichée (calibrée), `cote` = cote décimale, `ev` = value % (calculée sur la MÊME
    conf -> récit exact). `with_cote` -> ajoute la colonne Cote (cartes de simple/combiné ; PAS les jambes,
    qui montrent déjà @cote). '' si données insuffisantes. Classes CSS `.vb-*`/`.vm-*` (cf. web.py). Seuils
    couleur/mot alignés sur web._conf_hue/_conf_word. Value colorée (vert ≥+3, ambre +1..2, rouge <0) ;
    masquée si EV ≤ 0 sur un combiné (calibrated=False, pari fiabilité) OU un provisoire (hide_neg_value=True,
    pari indicatif hors ROI — pas un value bet) : règle « 💎 si EV+ » STRICTE (un « +0 % » n'est pas un edge —
    pas de colonne). Carte de SIMPLE : transparence totale (value montrée même à 0/négatif)."""
    # Cadre « pari seul » (repli) : si le pari+glose doit vivre DANS le cadre (pick_html, user 2026-08-15)
    # mais qu'on n'a pas de verdict calculable (cote/conf manquantes), on rend quand même le cadre avec juste
    # le pari centré -> jamais de pari perdu.
    _pickbox = (f'<div class="vb"><div class="vm"><div class="vm-pick">{pick_html}</div></div></div>'
                if pick_html else "")
    try:
        cv = float(cote); cf = float(conf); ep = int(round(ev))
    except (TypeError, ValueError):
        return _pickbox
    if not cv or cv <= 1:
        return _pickbox
    be = round(100 / cv)                       # proba implicite marché = seuil de rentabilité
    cfi = int(round(cf))
    _RED = "linear-gradient(90deg,#b23b3b,#ff6b6b)"
    _AMB = "linear-gradient(90deg,#c9902f,#f6c54a)"
    # VERT ÉMERAUDE (demande user 2026-07-18 : « la couleur du OUI ») = #64cd8d, remplace le lime #a6e22e
    # sur la confiance + les barres verdict. Dégradé barre : émeraude foncé -> #64cd8d.
    _GRN_C = "#64cd8d"
    _GRN = "linear-gradient(90deg,#2f9d63,#64cd8d)"
    # couleur du % + dégradé de barre + qualificatif. Un COMBINÉ (calibrated=False) a une proba
    # STRUCTURELLEMENT plus basse (produit de jambes, cote ≥1.95) -> échelle de mots/couleurs DÉDIÉE pour
    # ne pas afficher « Faible » en rouge sur le pari phare du jour. Simples : mêmes seuils que _conf_word.
    if calibrated:
        if cfi < 55:
            col, grad, word = "#ff6b6b", _RED, "Faible"
        elif cfi < 68:
            col, grad, word = "#f6c54a", _AMB, "Modérée"
        elif cfi < 80:
            col, grad, word = _GRN_C, _GRN, "Élevée"
        else:
            col, grad, word = _GRN_C, _GRN, "Très élevée"
    else:
        if cfi < 38:
            col, grad, word = "#ff6b6b", _RED, "Audacieux"
        elif cfi < 52:
            col, grad, word = "#f6c54a", _AMB, "Équilibré"
        elif cfi < 62:
            col, grad, word = _GRN_C, _GRN, "Solide"
        else:
            col, grad, word = _GRN_C, _GRN, "Très solide"
    vcls = "vpos" if ep >= 3 else "vmid" if ep >= 1 else "vneg"
    mark = f'<b class="vb-mark" style="left:{be}%"></b>' if 0 < be < 100 else ""
    # GRILLE de métriques (pleine largeur, colonnes alignées). Marché toujours ; Value sauf combiné à EV<0
    # (pari fiabilité, pas value) ; Cote seulement sur les cartes de simple/combiné (with_cote).
    # GRILLE pleine largeur, colonnes égales. NOTRE confiance placée JUSTE à côté du MARCHÉ (demande user
    # 2026-07-18 : comparaison directe « nous vs marché »). Confiance = héros (valeur colorée + qualificatif
    # en sous-titre). Value sautée sur un combiné à EV<0 (fiabilité). Cote seulement sur simple/combiné.
    # EDGE (user 2026-08-16) = de combien NOTRE confiance bat la proba du marché (`be`=100/cote), en POINTS.
    # Remplace la colonne « Marché » (le marché reste montré par le repère blanc sur la barre) : lecture
    # gauche->droite Confiance -> Edge (on bat le marché de X) -> Value (ce que ça rapporte) -> Cote.
    _edge = cfi - be
    _ecls = "vpos" if _edge >= 2 else "vmid" if _edge >= 0 else "vneg"
    cells = [f'<div class="vm-cell vm-conf"><span class="vm-l">Confiance</span>'
             f'<span class="vm-v" style="color:{col}">{cfi}%</span>'
             f'<span class="vm-sub" style="color:{col}">{word.lower()}</span></div>']
    # MODE `bare` (user 2026-08-17) : COMBINÉ = Confiance + Cote SEULEMENT. Un combiné « sécurité » est
    # structurellement à edge/value NÉGATIFS (DC sûres, cote ~2) -> afficher Edge/Value contredirait son
    # identité (la RÉUSSITE, pas la value) et découragerait à tort. On retire donc ces 2 colonnes.
    if not bare:
        cells.append(f'<div class="vm-cell"><span class="vm-l">Edge</span>'
                     f'<span class="vm-v {_ecls}">{"+" if _edge >= 0 else ""}{_edge} pts</span></div>')
        # VALUE : la colonne est TOUJOURS présente (demande user 2026-07-24 : « ajouter la value partout même
        # si négative ou nulle, mais une barre à la place de la masquée pour garder le même alignement ») — un
        # nombre de cellules CONSTANT garantit que Confiance/Edge/Value[/Cote] restent alignés d'une carte à
        # l'autre. On MONTRE la value réelle quand c'est un vrai edge (ep ≥ +1 %) ou sur une carte de SIMPLE
        # (transparence totale, même à 0/négatif) ; sinon (provisoire/Betmines sans edge : « 💎 si EV+ »
        # strict) on affiche une BARRE « — » au lieu de la masquer -> alignement identique, présente ou pas.
        if ep >= 1 or (calibrated and not hide_neg_value):
            _valh = f'<span class="vm-v {vcls}">{"+" if ep >= 0 else ""}{ep}%</span>'
        else:
            _valh = '<span class="vm-v vm-na">—</span>'
        cells.append(f'<div class="vm-cell"><span class="vm-l">Value</span>{_valh}</div>')
    if with_cote:
        # COTE affichée à 2 DÉCIMALES MAX (user 2026-08-20) : la cote d'un combiné est un PRODUIT (ex. 2.016) ->
        # arrondie à l'affichage comme les simples (2.02). Display-only, la valeur stockée reste intacte.
        cells.append('<div class="vm-cell vm-cote"><span class="vm-l">Cote</span>'
                     f'<span class="vm-v">{round(cv, 2):g}</span></div>')
    # Pari+glose (user 2026-08-15) : DANS le cadre `.vm`, centré, AU-DESSUS de la grille de chiffres
    # (le séparateur sous les équipes est retiré côté carte). Les cellules passent dans `.vm-grid`
    # (l'ancienne rangée flex de `.vm`) -> alignement identique pour tous les types de cartes.
    _pk = f'<div class="vm-pick">{pick_html}</div>' if pick_html else ""
    # BARRE : statique = confiance calibrée (avant-match). LIVE (user 2026-08-15) = la MÊME barre DEVIENT
    # « Confiance live » = chance en direct (remplissage = live_pct), NOS couleurs (émeraude/ambre/rouge par
    # niveau), MARQUEUR marché d'avant-match conservé (le `mark`). Verrou acquis/perdu -> « Gagné »/« Perdu ».
    if isinstance(live_pct, int):
        lp = max(0, min(100, live_pct))
        # `_hrgb` = triplet RGB de la couleur de remplissage -> le HALO prend LA MÊME couleur (user 2026-08-15),
        # via les variables CSS --hlo/--hhi (rgba avec opacité) lues par le keyframe vblivehalo.
        if lp < 50:
            lgrad, _hrgb = _RED, "255,107,107"
        elif lp < 68:
            lgrad, _hrgb = _AMB, "246,197,74"
        else:
            lgrad, _hrgb = _GRN, "100,205,141"
        _ar = {"up": "▲", "down": "▼"}.get(live_trend, "")
        if live_state == "acquis":
            _lt, lgrad, _hrgb = "Gagné", _GRN, "100,205,141"
        elif live_state == "perdu":
            _lt, lgrad, _hrgb = "Perdu", _RED, "255,107,107"
        else:
            _lt = "Confiance live"
        # TÉMOINS D'AVANT-MATCH (user 2026-08-15) : sur la barre live, deux marqueurs = notre confiance
        # d'avant-match (VERT, cohérent avec « Confiance » de la grille) et celle des bookmakers (BLANC = `mark`,
        # proba implicite `be`, cohérent avec « Marché »). Les valeurs chiffrées sont DÉJÀ dans la grille en
        # dessous -> pas de légende texte (retirée à la demande user).
        _mk_us = f'<b class="vb-mark vb-mk-us" style="left:{cfi}%"></b>' if 0 < cfi < 100 else ""
        _bar = ('<div class="vb-live">'
                f'<div class="vb-live-hd"><span class="vb-live-t">{_lt}</span>'
                f'<span class="vb-live-v">{lp}%<span class="vb-live-ar">{_ar}</span></span></div>'
                f'<div class="vb-bar"><i style="width:{lp}%;background:{lgrad};'
                f'--hlo:rgba({_hrgb},.18);--hhi:rgba({_hrgb},.52)"></i>{_mk_us}{mark}</div>'
                '</div>')
    elif bare:
        # COMBINÉ (user 2026-08-17) : barre PROPRE = juste NOTRE confiance/réussite, SANS repère marché ni
        # zone edge (cohérent avec le retrait des colonnes Edge/Value — le marché n'est pas le sujet ici).
        _bar = f'<div class="vb-bar"><i style="width:{min(cfi, 100)}%;background:{grad}"></i></div>'
    else:
        # BARRE = ZONE EDGE (user 2026-08-17) : le remplissage va jusqu'à NOTRE confiance ; la portion qui
        # DÉPASSE le marché (= notre edge) est en SURBRILLANCE (edge+), ou le manque jusqu'au marché est
        # HACHURÉ (edge-). Repère marché DISCRET = frontière de la zone. Remplace le trait blanc « marché »
        # (jugé ambigu maintenant qu'on affiche l'Edge en colonne, pas la proba marché).
        _cfic = max(0, min(cfi, 100))
        _bec = max(0, min(be, 100))
        if _cfic >= _bec:                          # edge POSITIF : marché..nous en surbrillance (notre avantage)
            _ov = f'<i class="vb-edge vb-edge-pos" style="left:{_bec}%;width:{_cfic - _bec}%"></i>'
        else:                                      # edge NÉGATIF : nous..marché hachuré (le marché nous devance)
            _ov = f'<i class="vb-edge vb-edge-neg" style="left:{_cfic}%;width:{_bec - _cfic}%"></i>'
        _tick = f'<b class="vb-mktb" style="left:{_bec}%"></b>' if 0 < _bec < 100 else ""
        _bar = (f'<div class="vb-bar"><i style="width:{_cfic}%;background:{grad}"></i>'
                f'{_ov}{_tick}</div>')
    # CARTE RÉGLÉE (user 2026-08-15) : le badge « Gagné/Perdu » REMPLACE la barre et vit AUSSI DANS le cadre.
    if result_html:
        _bar = f'<div class="vm-res">{result_html}</div>'
    # BARRE INTÉGRÉE DANS LE CADRE (user 2026-08-15) : la barre de confiance / « Confiance live » vit
    # DÉSORMAIS À L'INTÉRIEUR du cadre `.vm`, SOUS la grille de chiffres (plus en dehors/dessous).
    return (
        '<div class="vb">'
        f'<div class="vm">{_pk}<div class="vm-grid">{"".join(cells)}</div>{_bar}</div>'
        '</div>')


def _bets_table(body: str, results: dict | None = None, compact: bool = False,
                notes: list | None = None, residual: str = "",
                sport: str | None = None, home: str = "", away: str = "",
                validation: dict | None = None, streaks=None) -> str:
    """Paris à jouer : un CADRE par pari (style « confiance ») = label + sélection + barre de
    probabilité + indice de sûreté + cote. `results` = {sélection normalisée: 'won'/'lost'/'push'/
    None} -> cadre VERT/ROUGE + halo + ✓/✗ selon le résultat de CE pari (chaque pari réglé à part).
    `streaks` (audit 2026-07-23) : moyennes d'équipe -> le REFROIDISSEMENT OVER-total (_cool_conf)
    s'applique ici AUSSI, sinon le détail de carte contredit la sélection (69 % affiché vs 58 % au moteur)."""
    data = _parse_bets(body)
    if not data:
        return ""
    results = results or {}
    cprobs = codes = ok = None    # confiances RECALIBRÉES + codes + indices réglables (≈ card_summary)
    if sport:
        from app.settle_analyst import code_from_pick
        ex_sports, _ = auto_exclusions()
        ex_markets = excluded_markets(sport)          # marchés écartés PROPRES À CE SPORT (per-sport)
        codes = [code_from_pick(b.get("sel", ""), sport, home, away) for b in data]
        cprobs = [_cool_conf(calibrated_conf(b.get("prob"), sport, codes[i]), sport, codes[i], streaks)
                  for i, b in enumerate(data)]
        ok = set() if sport in ex_sports else {
            i for i, c in enumerate(codes) if c and market_of(c) not in ex_markets}
    reco = _recommend(data, ok=ok, cprobs=cprobs, codes=codes)
    has_play = reco.get("verdict") == "play"     # y a-t-il un pari RETENU (value + confiance OK) ?
    note_by_idx = _assign_notes([b["sel"] for b in data], notes)   # commentaire Verdict -> bon pari
    # RENDU TICKET PREMIUM (style carte Telegram, sans logo — demande user 2026-07-12) : chaque pari =
    # sélection (gras) + pastille de cote + pastille de chance + justification (barre latérale) + badges
    # (value, sûreté/validation). Toute la logique retenu/abstention/résultat/panel est PRÉSERVÉE.
    cards = []
    for k, b in enumerate(data):
        pari = _inline(pretty_sel(b["sel"], home, away))   # « 1X » -> « <équipe> ou nul » (homogène)
        cv, prob, rcls = b["cote"], b["prob"], b["risk_cls"]
        is_reco = reco.get("idx") == k and has_play   # VRAI pari retenu (value + conf OK) ; sinon abstention
        _cp = cprobs[k] if (cprobs and k < len(cprobs) and cprobs[k] is not None) else prob
        ev_pct = round((_cp / 100 * cv - 1) * 100) if (_cp is not None and cv) else None
        # CONFIANCE AFFICHÉE = confiance CALIBRÉE `_cp` (pas la proba brute) : c'est celle qui pilote la
        # VALUE, la sélection (retenu/abstention) ET la bande verdict compacte -> le récit « Marché % ·
        # confiance % → value % » est ainsi TOUJOURS exact (conf × cote − 1 = value) et jamais en
        # contradiction avec le statut. (Corrige l'ancien écart : le tableau montrait la brute, le reste le
        # calibré ; cf. commentaire card_summary l.1940 « la MÊME confiance que le détail ».)
        cote_v = f"{cv:g}" if cv is not None else (_inline(b["cote_txt"]) if b["cote_txt"] else "—")
        res = results.get(_norm_sel(b["sel"]))
        # État + marqueur : pari RETENU -> résultat coloré + ✅/❌/➖ ; abstention -> « aurait gagné/perdu ».
        if is_reco:
            legcls = (" won" if res == "won" else " lost" if res == "lost"
                      else " void" if res == "push" else "")
            mark = ('<span class="tkt-mk">✅</span>' if res == "won"
                    else '<span class="tkt-mk">❌</span>' if res == "lost"
                    else '<span class="tkt-mk">➖</span>' if res == "push" else "")
        else:
            legcls = ""
            mark = ('<span class="tkt-p">aurait gagné</span>' if res == "won"
                    else '<span class="tkt-p">aurait perdu</span>' if res == "lost" else "")
        o_chip = f'<span class="tkt-o">@{cote_v}</span>' if cote_v != "—" else ""
        note = note_by_idx.get(k)
        note_html = f'<div class="tkt-why">{_note_paras(note)}</div>' if note else ""
        # HEADLINE = sélection + COTE (chiffre phare) + résultat. La confiance passe dans la ligne VERDICT.
        _top = (f'<span class="tkt-sel">{pari}</span>'
                f'<span class="tkt-r">{o_chip}{mark}'
                + ('<span class="tkt-chev">▾</span>' if note_html else '') + '</span>')
        # LIGNE VERDICT (refonte demande user 2026-07-17) : raconte la DÉCISION en une phrase reliant les 3
        # chiffres — proba MARCHÉ (100/cote = seuil de rentabilité) · NOTRE confiance (publiée = Telegram,
        # calibrée sur l'historique) → VALUE (l'edge, HÉROS coloré). On comprend POURQUOI c'est un pari (ou
        # une abstention) d'un coup d'œil, sans confondre « chance du marché » et « notre confiance ».
        verdict = (verdict_line(cv, _cp, ev_pct, calibrated=(prob is not None))
                   if (_cp is not None and cv and ev_pct is not None) else "")
        # Ligne META (discrète) : sûreté + validation du panel, ou le motif d'abstention. La VALUE est
        # désormais dans le verdict (héros) -> plus de badge « value » redondant ici.
        subs = []
        if is_reco:
            _sb = [_SAFETY.get(rcls, "Sûreté moyenne")]
            if k == 0 and validation and validation.get("n_ok") is not None:
                _sb.append(f'✓ {validation["n_ok"]}/{validation.get("n", 3)} agents')
            subs.append(f'<span class="tkt-sub">{" · ".join(_sb)}</span>')
        else:
            subs.append('<span class="tkt-sub">⏸ pas de value → abstention</span>')
        subs_html = f'<div class="tkt-subs">{"".join(subs)}</div>'
        if note_html:
            cards.append(f'<details class="tkt-leg tkt-fold{legcls}">'
                         f'<summary class="tkt-leg-top" onclick="event.stopPropagation()">{_top}</summary>'
                         f'{note_html}</details>{verdict}{subs_html}')
        else:
            cards.append(f'<div class="tkt-leg{legcls}"><div class="tkt-leg-top">{_top}</div></div>'
                         f'{verdict}{subs_html}')
    # LIVE (compact) : cartes de paris seules (ni titre ni cote pied), fondu dans la carte live.
    if compact:
        return '<div class="tkt tkt-simple">' + "".join(cards) + "</div>"
    # NON-live : en-tête « Pari à jouer / joué / Analyse du match » + cartes + cote du pari retenu (gros).
    _ri = reco.get("idx") if (has_play and reco.get("idx") is not None) else None
    _reco_res = results.get(_norm_sel(data[_ri]["sel"])) if _ri is not None else None
    if has_play:
        _title = "Pari joué" if _reco_res in ("won", "lost", "push") else "Pari à jouer"
        _rc = data[_ri]["cote"]
        cote_foot = (f'<div class="tkt-cote"><span class="l">Cote</span>'
                     f'<span class="v">{_rc:g}</span></div>' if _rc else "")
    else:
        _title = "Analyse du match"
        cote_foot = ""
    gcls = " won" if _reco_res == "won" else " lost" if _reco_res == "lost" else ""
    return (f'<div class="tkt tkt-simple{gcls}"><div class="tkt-h">{_title}</div>'
            + "".join(cards) + cote_foot + (residual or "") + "</div>")


def _structured(md: str, skip_verdict: bool = False, card_details: bool = False) -> str | None:
    """Rendu gabarit analyste (carte DÉPLIÉE), ou None si le format ne correspond pas (-> repli générique).
    REFONTE 2026-07-13 (demande user « réorganise complètement ») : l'analyse déployée montre enfin le
    RAISONNEMENT (« 🎯 Le pari à jouer », 1400+ car. jusqu'ici masqués) EN PREMIER et EN CLAIR, puis les
    FAITS (visibles, plus repliés dans « Informations »), la MISE, et les séries/tendances. Sections
    premium (cartes `da-sec`).
    `skip_verdict` (demande user 2026-07-16) : n'émet PAS la section « 🎯 Pourquoi ce pari » — utilisé sur
    les cartes d'ABSTENTION où le bloc « 🧪 Le pari provisoire » (via reasoning_html) porte déjà le
    raisonnement -> évite le DOUBLON de conclusion « on s'abstient »."""
    secs = _sections(md)
    verdict = _find(secs, "🎯", "Le pari", "pari à jouer", "Verdict")
    bets = _find(secs, "📊", "Paris class")
    if not verdict and not bets:
        return None
    faits = _find(secs, "📋", "Les faits", "faits")
    mise = _find(secs, "💰", "Mise")
    combo = _find(secs, "🎲", "Combiné", "combiné")     # affiché dans SON cadre -> pas ici (doublon)
    prov = _find(secs, "🧪", "provisoire", "Provisoire")  # bloc provisoire dédié -> pas ici (doublon)
    parts = []

    def _sec(icon_title, body_html, cls=""):
        return (f'<section class="da-sec{cls}">'
                f'<div class="da-h da-h2">{icon_title}</div>{body_html}</section>')

    # `card_details` (demande user 2026-07-20) : sur une CARTE de pari, le pli « 💡 Pourquoi » porte DÉJÀ le
    # raisonnement -> le dépli ne le RÉPÈTE pas (verdict masqué) et REGROUPE la preuve (faits + tendances/H2H)
    # sous un sous-pli « 🔍 Voir les détails » replié, en gardant la Mise VISIBLE. PURE PRÉSENTATION : le .md
    # (données) est intact -> le pli, le règlement et la calibration lisent toujours les mêmes sections.
    _skip_v = skip_verdict or card_details
    detail_parts = []   # faits + séries/tendances/H2H : repliés en mode carte
    # 1) POURQUOI CE PARI — le cœur de l'analyse (raisonnement de l'analyste), VISIBLE en premier. On retire
    #    la 1re puce « **<sél> @cote :** » (redondante avec le pari déjà affiché en tête de carte).
    #    `_skip_v` : masqué sur les cartes (pli dédié) et sur les abstentions (le bloc « 🧪 provisoire » porte
    #    déjà le raisonnement).
    if verdict and not _skip_v:
        _why = re.sub(r"^\s*[-*]\s*\*\*[^\n*]+?@[\d.,]+\s*:\*\*\s*", "- ", verdict, count=1)
        parts.append(_sec("🎯 Pourquoi ce pari", _render_blocks(_why), " da-sec-why"))
    # 2) LES FAITS — visibles (dépli classique) ou dans « 🔍 Voir les détails » (mode carte).
    if faits:
        (detail_parts if card_details else parts).append(_sec("📋 Les faits", _render_blocks(faits)))
    # 3) MISE conseillée (Kelly) — TOUJOURS visible (actionnable), jamais repliée.
    if mise:
        parts.append(_sec("💰 Mise conseillée", _render_blocks(mise), " da-sec-mise"))
    # 4) Toute autre section (séries/tendances Sportradar, H2H…) : à la suite (dépli) ou repliée (carte).
    known = {"", verdict, bets, faits, mise, combo, prov}
    for title, b in secs.items():
        if b and b not in known and title != "":
            if any(k in title or k in title.lower()
                   for k in ("Verdict", "Paris", "faits", "Mise", "🎲", "ombiné", "🧪", "rovisoire")):
                continue
            (detail_parts if card_details else parts).append(_sec(_inline(title), _render_blocks(b)))
    # Mode carte : regrouper la PREUVE sous un sous-pli replié « 🔍 Voir les détails » (progressive disclosure)
    # -> carte épurée (le pli « 💡 Pourquoi » + Cotes & chances + Mise suffisent), la preuve à 1 tap.
    if card_details and detail_parts:
        parts.append('<details class="da-more"><summary class="da-more-s" '
                     'onclick="event.stopPropagation()">🔍 Voir les détails'
                     '<span class="da-more-chev">▾</span></summary>'
                     f'<div class="da-more-b">{"".join(detail_parts)}</div></details>')
    return '<div class="da">' + "".join(parts) + "</div>"


def reasoning_html(sport: str, match_id) -> str:
    """Le RAISONNEMENT du pari PROVISOIRE rendu en HTML, dans un bloc déplié — pour les abstentions, dont
    le pari est INDICATIF (hors ROI). Priorité à la section « 🧪 Pari provisoire » (le meilleur angle
    DÉSIGNÉ + ANALYSÉ par l'analyste, COHÉRENT avec ses faits) ; repli sur « 🎯 Le pari à jouer » (analyses
    d'avant l'ajout de la section provisoire). La ligne technique `PROV:` est retirée. '' si absent.
    NE PAS l'ajouter aux cartes à-jouer (doublon avec les notes par pari)."""
    md = load(sport, match_id)
    if not md:
        return ""
    secs = _sections(md)
    prov = _find(secs, "🧪", "provisoire", "Provisoire")
    body = prov or _find(secs, "🎯", "Verdict")
    if not body:
        return ""
    body = re.sub(r"(?im)^\s*PROV:.*$", "", body).strip()   # ligne technique de règlement -> cachée
    if not body:
        return ""
    title = "🧪 Le pari provisoire (indicatif)" if prov else "🎯 L'analyse du pari"
    return ('<details class="da-faits" open>'
            f'<summary onclick="event.stopPropagation()">{title}</summary>'
            f'<div class="da-faits-b">{_render_blocks(body)}</div></details>')


def _bets_section(md: str) -> str:
    return _find(_sections(_strip(md)), "📊", "Paris class", "paris")


def _reprice_bets(bets: list[dict], sport, match_id) -> None:
    """Remplace la cote de chaque pari par la VRAIE cote Unibet (map `omap` figée au scan), keyée par le
    CODE de règlement — demande user 2026-07-28. La cote du .md vient du texte LLM, parfois mal transcrite
    (ex. OVER 2.5 @2.55 vs 1.60 réel). NO-OP si le sidecar n'a pas d'`omap` (analyses antérieures au fix ->
    ROI historique INTACT). Toutes les voies (statut, publication, affichage, règlement) passent par
    `bets_of` -> re-pricing UNIQUE et cohérent. Modifie `bets` en place (copies -> cache _parse_bets pur)."""
    if not bets:
        return
    m = meta(sport, match_id) or {}
    omap = m.get("omap") or {}
    if not omap:
        return
    from app.settle_analyst import code_from_pick
    for b in bets:
        try:
            code = code_from_pick(b.get("sel", ""), sport, m.get("home", ""), m.get("away", ""))
        except Exception:
            continue
        rc = omap.get(code)
        if isinstance(rc, (int, float)) and rc >= 1.01 and rc != b.get("cote"):
            b["cote"] = rc
            b["cote_txt"] = f"{rc:g}"


def bets_of(sport: str, match_id) -> list[dict]:
    """Liste STRUCTURÉE et ordonnée des paris affichés d'un match (pari 1/2/3), pour le règlement
    par pari. [] si pas d'analyse / pas de tableau. Cotes RE-PRICÉES à la vraie cote Unibet (cf. _reprice_bets)."""
    md = load(sport, match_id)
    if not md:
        return []
    body = _bets_section(md)
    bets = _parse_bets(body) if body else []
    _reprice_bets(bets, sport, match_id)      # cote LLM -> vraie cote Unibet (si `omap` figée au scan)
    return bets


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
    notes, _resid = _verdict_notes(md)     # commentaire Verdict -> sous chaque pari. Le résidu
    # « à éviter » n'est PLUS rendu ici : il est intégré au cadre « Informations » (cf. _structured).
    return _bets_table(body, results, compact=compact, notes=notes, residual="",
                       sport=sport, home=m.get("home", ""), away=m.get("away", ""),
                       validation=m.get("validation"), streaks=m.get("streaks"))


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
        if mnum:
            line = _to_float(mnum.group(1))
        elif not handicap:        # notation SIGNÉE d'un TOTAL (« +4.5 tirs cadrés », « +7.5 corners »)
            sgn = re.search(r"[+\-]\s?(\d+(?:[.,]\d+)?)", t)
            line = _to_float(sgn.group(1)) if sgn else None
    if side is None:
        side = _leg_side(sel, home, away)
    # PROP JOUEUR (« <Nom joueur> - Tirs (cadrés) … ») : NE PAS le régler comme un total MATCH (faux :
    # sot/tirs du match > 0.5 = quasi toujours « gagné »). Segment avant « - » = un nom qui n'est NI une
    # équipe NI un libellé de total -> on laisse au code (PLAYERFB via FotMob), donc live_ok=False.
    if " - " in sel and metric in ("sot", "shots"):
        _head = sel.split(" - ", 1)[0].strip()
        if (_head and _leg_side(_head, home, away) is None
                and not any(w in _head.lower() for w in
                            ("total", "nombre", "but", "corner", "carton", "tir", "mi-temps", "équipe"))):
            return {"metric": metric, "side": None, "dir": direction, "line": line, "scope": scope,
                    "handicap": handicap, "live_ok": False}
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


# --------------------------------------------------------------- barre « % live »
# Reflet EN DIRECT de la chance qu'un pari passe, VU le score courant + le temps restant. PURE AFFICHAGE :
# lecture seule, calculée au rendu, JAMAIS écrite dans un sidecar / stat_bet / la calibration / le ROI
# (couche 3 « CALIBRATION » et couche 2 « STATS » ne sont pas touchées — cf. CLAUDE.md § 3 couches).
# Source, du + fidèle au repli : (1) marché VERROUILLÉ (déjà mathématiquement acquis/perdu) -> 100/0 ;
# (2) « reflet de la cote en direct » = proba implicite dé-margée de la cote live du marché (vainqueur/
# double chance, déjà en cache Unibet, 0 appel) ; (3) repli MODÈLE (Poisson à temps décroissant) pour les
# totaux/BTTS foot quand la cote de CE marché précis n'est pas en main. Sinon None -> pas de barre (jamais
# de faux %). Choix user 2026-07-15 : « cote live du marché (repli modèle) ».

_FOOT_GOALS_90 = 2.7      # buts attendus moyens sur 90' (paramètre du repli modèle totaux/BTTS foot)
_FOOT_FULL_MIN = 90


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) pour X ~ Poisson(lam), k entier >= 0. Repli 1.0 si lam <= 0 (aucun événement à venir)."""
    if lam <= 0:
        return 1.0
    s = term = math.exp(-lam)         # i = 0
    for i in range(1, max(0, k) + 1):
        term *= lam / i
        s += term
    return max(0.0, min(1.0, s))


def _poisson_sf(n: int, lam: float) -> float:
    """P(X >= n) pour X ~ Poisson(lam). n <= 0 -> 1.0."""
    if n <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - _poisson_cdf(n - 1, lam)))


def _foot_remaining(minute) -> float:
    """Fraction de match restante ∈ [0,1] à partir de la minute écoulée (repli 0.5 si inconnue)."""
    if minute is None:
        return 0.5
    return max(0.0, min(1.0, (_FOOT_FULL_MIN - minute) / _FOOT_FULL_MIN))


def _is_signed_handicap(sel: str) -> bool:
    """Vrai si le libellé porte un HANDICAP signé (« +17.5 », « -1.5 ») — même SANS le mot « handicap »
    (ex. basket « Los Angeles Sparks +17.5 (prol. incl.) »). On exclut les totaux « plus/moins de X »
    (ceux-là ne sont pas des handicaps). Sert à ne PAS confondre un handicap avec un vainqueur/total."""
    t = (sel or "").lower()
    if "plus de" in t or "moins de" in t:
        return False
    return re.search(r"[+\-−–]\s?\d", t) is not None


def _winner_side(sel: str, code: str, home: str, away: str, sport: str):
    """Côté d'un pari de RÉSULTAT (vainqueur / double chance), ou None si ce n'est pas un marché de
    résultat exploitable via la cote 1X2/vainqueur live. Renvoie 'home'/'away'/'draw' (simple) ou
    '1X'/'12'/'X2' (double chance). Conservateur : tout marché portant une autre métrique (but/corner/
    tir/carton/mi-temps/handicap/score exact/set) -> None (pas de barre plutôt qu'un faux %)."""
    t = (sel or "").lower()
    c = (code or "").upper()
    if "handicap" in t or _is_signed_handicap(sel):  # handicap (mot OU signe « +17.5 ») ≠ 1X2 -> pas ici
        return None
    if any(w in t for w in ("but", "buteur", "premier", "mi-temps", "carton", "corner", "tir",
                            "score exact", " set", "jeu", "total")):
        return None                                  # autre marché : la cote vainqueur ne le reflète pas
    is_dc = "double chance" in t or c.startswith("DC")
    if is_dc:
        flat = c.replace(" ", "") + t.replace(" ", "")
        for pair in ("1X", "12", "X2"):
            if pair in flat.upper():
                return pair
        return None
    if sport == "foot" and (re.search(r"\bnul\b", t) or "match nul" in t or c in ("1X2 X", "1 X 2 X")):
        return "draw"
    sd = _leg_side(sel, home, away)
    return {"HOME": "home", "AWAY": "away"}.get(sd)


def _winner_pct(side: str, win_odds) -> float | None:
    """Proba implicite DÉ-MARGÉE du côté `side` à partir des cotes vainqueur live `win_odds` (o1,ox,o2)
    — ox peut manquer (tennis/basket, marché 2 voies). None si cotes inexploitables."""
    o1, ox, o2 = (list(win_odds or []) + [None, None, None])[:3]
    raw = {}
    if isinstance(o1, (int, float)) and o1 > 1:
        raw["home"] = 1.0 / o1
    if isinstance(ox, (int, float)) and ox > 1:
        raw["draw"] = 1.0 / ox
    if isinstance(o2, (int, float)) and o2 > 1:
        raw["away"] = 1.0 / o2
    s = sum(raw.values())
    if s <= 0:
        return None
    p = {k: v / s for k, v in raw.items()}
    if side in p:
        return p[side]
    if len(side) == 2:                               # double chance = somme des 2 issues couvertes
        pair = {"1": "home", "X": "draw", "2": "away"}
        return sum(p.get(pair[ch], 0.0) for ch in side)
    return None


def _foot_goals_pct(info: dict, hs: int, as_: int, minute) -> float | None:
    """Proba MODÈLE (Poisson à temps décroissant) d'un total de buts Plus/Moins (match entier ou équipe)
    foot, vu le score et la minute. None si la jambe n'est pas un total de buts exploitable."""
    if info.get("metric") != "goals" or info.get("scope") != "match":
        return None
    line, dirn, side = info.get("line"), info.get("dir"), info.get("side")
    if line is None or dirn not in ("OVER", "UNDER"):
        return None
    rem = _foot_remaining(minute)
    if side in ("HOME", "AWAY"):
        cur = hs if side == "HOME" else as_
        lam = (_FOOT_GOALS_90 / 2.0) * rem
    else:
        cur = hs + as_
        lam = _FOOT_GOALS_90 * rem
    if cur > line:
        p_over = 1.0
    else:
        need = int(math.floor(line - cur)) + 1       # buts FUTURS mini pour franchir la ligne (X.5)
        p_over = _poisson_sf(need, lam)
    return p_over if dirn == "OVER" else 1.0 - p_over


def _foot_btts_pct(sel: str, hs: int, as_: int, minute) -> float:
    """Proba MODÈLE que « les deux équipes marquent » (Oui/Non) vu le score et la minute (chaque équipe
    doit finir avec >= 1 but ; côté déjà marqué = verrouillé à 1)."""
    yes = not re.search(r"\bnon\b", (sel or "").lower())
    lam = (_FOOT_GOALS_90 / 2.0) * _foot_remaining(minute)
    ph = 1.0 if hs >= 1 else _poisson_sf(1, lam)
    pa = 1.0 if as_ >= 1 else _poisson_sf(1, lam)
    both = ph * pa
    return both if yes else 1.0 - both


def _is_btts(sel: str, code: str) -> bool:
    t, c = (sel or "").lower(), (code or "").upper()
    return ("BTTS" in c or "both teams to score" in t
            or ("deux" in t and "marquent" in t) or "les deux équipes marquent" in t)


# --- modèle de DIRECT (statistique du match : score + temps restant) : notre proba propre que le pari
#     passe, INDÉPENDANTE de la cote. Poisson sur les événements restants. C'est la composante « live ». ---
_RATE90 = {"goals": 2.7, "corners": 10.0, "cards": 4.6, "redcards": 0.25, "sot": 8.5, "shots": 25.0}


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _foot_margin_dist(hs: int, as_: int, lam: float, cap: int = 8) -> dict:
    """Distribution du MARGE FINALE (buts domicile − extérieur) : score courant + buts restants ~ Poisson(lam)
    par équipe (répartition neutre — la force d'équipe entre via l'analyse d'avant-match, pas ici)."""
    pf = [_poisson_pmf(k, lam) for k in range(cap + 1)]
    base = hs - as_
    dist: dict = {}
    for fh in range(cap + 1):
        for fa in range(cap + 1):
            m = base + fh - fa
            dist[m] = dist.get(m, 0.0) + pf[fh] * pf[fa]
    return dist


def _foot_result_pct(wside: str, hs: int, as_: int, rem: float) -> float | None:
    """Proba MODÈLE (score + temps restant) d'un résultat foot : home/draw/away ou double chance 1X/12/X2."""
    dist = _foot_margin_dist(hs, as_, (_FOOT_GOALS_90 / 2.0) * rem)
    ph = sum(p for m, p in dist.items() if m > 0)
    pd = dist.get(0, 0.0)
    pa = sum(p for m, p in dist.items() if m < 0)
    tot = ph + pd + pa
    if tot <= 0:
        return None
    ph, pd, pa = ph / tot, pd / tot, pa / tot
    return {"home": ph, "draw": pd, "away": pa,
            "1X": ph + pd, "12": ph + pa, "X2": pd + pa}.get(wside)


def _foot_hcap_pct(info: dict, hs: int, as_: int, rem: float) -> float | None:
    """Proba MODÈLE d'un handicap BUTS (mêmes règles que `_eval_leg` HCAP), vu le score + temps restant."""
    ln, side = info.get("line"), info.get("side")
    if ln is None or side not in ("HOME", "AWAY"):
        return None
    dist = _foot_margin_dist(hs, as_, (_FOOT_GOALS_90 / 2.0) * rem)
    L, over = abs(ln), ln < 0
    p = tot = 0.0
    for m, pr in dist.items():
        tot += pr
        mine_other = m if side == "HOME" else -m          # écart « mien − adverse »
        val = mine_other if over else (-mine_other)       # -L à battre : mien−adverse ; +L coussin : adverse−mien
        if (val > L) == over:
            p += pr
    return p / tot if tot > 0 else None


def _foot_count_pct(info: dict, vals: dict, rem: float) -> float | None:
    """Proba MODÈLE d'un total Plus/Moins d'ÉVÉNEMENTS comptés (corners/cartons/tirs/tirs cadrés), vu le
    compteur LIVE (`vals`) + le temps restant. None si le compteur live du marché n'est pas connu."""
    base = _METRIC_BASE.get(info.get("metric"))
    rate = _RATE90.get(info.get("metric"))
    if base is None or rate is None or info.get("scope") != "match":
        return None
    if info.get("dir") not in ("OVER", "UNDER") or info.get("line") is None:
        return None
    ch, ca = _as_int((vals or {}).get(f"{base}_h")), _as_int((vals or {}).get(f"{base}_a"))
    if ch is None or ca is None:
        return None
    side = info.get("side")
    if side in ("HOME", "AWAY"):
        cur, lam = (ch if side == "HOME" else ca), (rate / 2.0) * rem
    else:
        cur, lam = ch + ca, rate * rem
    line, over = info["line"], info["dir"] == "OVER"
    if cur > line:
        p_over = 1.0
    else:
        p_over = _poisson_sf(int(math.floor(line - cur)) + 1, lam)
    return p_over if over else 1.0 - p_over


def _signed_line(sel: str):
    """Ligne signée d'un handicap depuis le libellé (« … -1.5 » -> -1.5, « … +1 » -> 1). None si absente."""
    m = re.search(r"([+\-−])\s*(\d+(?:[.,]\d+)?)", sel or "")
    return (-1 if (m and m.group(1) in "-−") else 1) * _to_float(m.group(2)) if m else None


def _norm_cdf(z: float) -> float:
    """Fonction de répartition de la loi normale centrée réduite."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


_BK_MARGIN_STD = 12.0    # écart-type de la MARGE finale d'un match complet (approx WNBA/NBA)
_BK_TOTAL_STD = 16.0     # écart-type du TOTAL de points final d'un match complet


def _basket_model_pct(sel, code, info, wside, hs, as_, frac) -> float | None:
    """Notre proba MODÈLE de DIRECT basket (approx. normale sur les points restants) : vainqueur, handicap
    de points, total de points (match ou équipe), vu la marge/le total courants + la fraction de match
    écoulée `frac` ∈ (0,1]. Indépendant de la cote. None si non modélisable."""
    if frac is None or frac <= 0:
        frac = 0.5
    rem = max(0.02, 1.0 - frac)
    M = hs - as_                                        # marge courante (domicile − extérieur), en points
    sig_m = _BK_MARGIN_STD * math.sqrt(rem)
    # 1) HANDICAP de points (le libellé « +17.5 » sans le mot « handicap » trompe _leg_metric -> on le relit).
    hl = _signed_line(sel) if _is_signed_handicap(sel) else (info.get("line") if info.get("handicap") else None)
    if hl is not None:
        side = info.get("side")
        if side not in ("HOME", "AWAY"):
            return None
        d = M if side == "HOME" else -M                # marge courante du côté parié
        return _norm_cdf((d + hl) / sig_m)             # couvre si (marge finale du côté) + hl > 0
    # 2) VAINQUEUR (pas de nul au basket) / double chance.
    if wside is not None:
        p_home = 1.0 - _norm_cdf(-M / sig_m)
        p_away = _norm_cdf(-M / sig_m)
        return {"home": p_home, "away": p_away, "1X": p_home, "12": 1.0, "X2": p_away}.get(wside)
    # 3) TOTAL de points (match entier ou équipe) — extrapolation du rythme courant.
    if info.get("dir") in ("OVER", "UNDER") and info.get("line") is not None:
        side = info.get("side")
        cur = (hs if side == "HOME" else as_) if side in ("HOME", "AWAY") else hs + as_
        exp_final = cur / frac                          # projection linéaire du total au rythme actuel
        sig_t = _BK_TOTAL_STD * math.sqrt(rem)
        p_over = 1.0 - _norm_cdf((info["line"] - exp_final) / sig_t)
        return p_over if info["dir"] == "OVER" else 1.0 - p_over
    return None


def _live_model_pct(sport, sel, code, info, wside, hs, as_, minute, vals, game_frac=None) -> float | None:
    """Notre proba MODÈLE (statistique du direct) que le pari passe, ou None si non modélisable. Foot :
    BTTS / résultat / handicap buts / totaux buts / totaux comptés (Poisson). Basket : vainqueur / handicap
    / total de points (approx. normale, via `game_frac`). Tennis : non modélisé (fusion cote+analyse)."""
    if sport == "basket":
        return _basket_model_pct(sel, code, info, wside, hs, as_, game_frac)
    # TENNIS « au moins un set » (SET HOME/AWAY) : modèle JEUX du set en cours (demande user 2026-07-21 —
    # la barre restait sur l'avant-match alors que le joueur menait 5-2). p(set courant) ≈ 0.5 + 0.13/jeu
    # d'écart (+0.05 à 4 jeux et plus) ; s'il perd CE set, il garde ~35 % d'en prendre un plus tard.
    # P(≥1 set) = 1 − (1−p_cur)(1−p_rest). Le verrou (_live_locked) prend le relais dès le set acquis.
    if sport == "tennis" and (code or "").startswith("SET ") and vals:
        _side = "HOME" if " HOME" in code else "AWAY" if " AWAY" in code else None
        gh, ga = _as_int(vals.get("games_h")), _as_int(vals.get("games_a"))
        if _side and gh is not None and ga is not None:
            gs, go = (gh, ga) if _side == "HOME" else (ga, gh)
            p_cur = max(0.05, min(0.97, 0.5 + 0.13 * (gs - go) + (0.05 if gs >= 4 else 0.0)))
            return 1.0 - (1.0 - p_cur) * (1.0 - 0.35)
        return None
    if sport != "foot":
        return None
    rem = _foot_remaining(minute)
    if _is_btts(sel, code):
        return _foot_btts_pct(sel, hs, as_, minute)
    if wside is not None:
        return _foot_result_pct(wside, hs, as_, rem)
    # Handicap BUTS : en foot, un handicap non qualifié (ni corner/carton/tir) = handicap de buts. `_leg_metric`
    # le classe parfois « special » sans lire la ligne signée -> on la relit ici (métrique buts par défaut).
    if (info.get("handicap") or _is_signed_handicap(sel)) and info.get("metric") in ("goals", "special"):
        ln = info.get("line") if info.get("handicap") else None
        if ln is None:
            ln = _signed_line(sel)
        if ln is not None and info.get("side") in ("HOME", "AWAY"):
            return _foot_hcap_pct({**info, "line": ln}, hs, as_, rem)
    if info.get("metric") == "goals":
        g = _foot_goals_pct(info, hs, as_, minute)
        if g is not None:
            return g
    return _foot_count_pct(info, vals, rem)


def _live_locked(sport, sel, code, info, hs, as_, vals) -> str | None:
    """'won'/'lost' si le pari est déjà MATHÉMATIQUEMENT tranché vu le direct (total franchi, BTTS acquis),
    sinon None. Prioritaire sur le mélange (un pari acquis = 100 %, pas dilué par la cote/l'analyse)."""
    if _is_btts(sel, code) and sport == "foot":
        yes = "non" not in (sel or "").lower()
        if hs >= 1 and as_ >= 1:
            return "won" if yes else "lost"
        return None
    # TENNIS « remporte au moins un set » (SET HOME/AWAY) : ACQUIS dès que le joueur a pris un set
    # (vals.sets_h/sets_a, cf. web._tennis_sets_games) — demande user 2026-07-21 (barre restée à 70 %
    # alors que le set était quasi pris). Jamais « lost » ici (le règlement final s'en charge).
    if sport == "tennis" and (code or "").startswith("SET ") and vals:
        _side = "HOME" if " HOME" in code else "AWAY" if " AWAY" in code else None
        _ss = _as_int(vals.get("sets_h") if _side == "HOME" else vals.get("sets_a"))
        if _side and _ss is not None and _ss >= 1:
            return "won"
        return None
    # TENNIS « total de jeux du Set N » (SETGAMES N OVER/UNDER X.5) : VERROUILLÉ dès que CE set est TERMINÉ
    # (demande user 2026-07-28 : la jambe « Set 1 +8.5 jeux » restait à 72 % alors que le set 1 était fini
    # 6-7 = 13 jeux -> acquise). N° de set = jeton après SETGAMES ; jeux du set via `vals["set_games"]`.
    _cu = (code or "").upper()
    if sport == "tennis" and _cu.startswith("SETGAMES") and vals:
        _m = re.match(r"SETGAMES\s+(\d+)\s+(OVER|UNDER)\s+(\d+(?:[.,]\d+)?)", _cu)
        _sg = vals.get("set_games") or []
        if _m and int(_m.group(1)) <= len(_sg):        # ce set est TERMINÉ -> total figé -> on tranche
            _tot = sum(_sg[int(_m.group(1)) - 1])
            _line = float(_m.group(3).replace(",", "."))
            _over = _m.group(2) == "OVER"
            if _tot > _line:
                return "won" if _over else "lost"
            return "lost" if _over else "won"
        return None
    # TENNIS « total de jeux du MATCH » (TOTGAMES OVER/UNDER X.5) : le total COURANT ne fait que MONTER ->
    # un OVER est ACQUIS dès qu'il dépasse la ligne (et un UNDER PERDU). L'inverse (OVER perdu / UNDER gagné)
    # attend la fin du match (règlement). Total = jeux des sets TERMINÉS + jeux du set EN COURS.
    if sport == "tennis" and _cu.startswith("TOTGAMES") and vals:
        _m = re.match(r"TOTGAMES\s+(OVER|UNDER)\s+(\d+(?:[.,]\d+)?)", _cu)
        _sg = vals.get("set_games") or []
        _gh, _ga = _as_int(vals.get("games_h")), _as_int(vals.get("games_a"))
        if _m and (_sg or _gh is not None):
            _tot = sum(h + a for h, a in _sg) + (_gh or 0) + (_ga or 0)
            _line = float(_m.group(2).replace(",", "."))
            if _tot > _line:                           # seuil déjà franchi (monotone)
                return "won" if _m.group(1) == "OVER" else "lost"
        return None
    if sport != "foot" or info.get("scope") != "match" or info.get("handicap"):
        return None
    if info.get("dir") not in ("OVER", "UNDER") or info.get("line") is None:
        return None
    if info.get("metric") == "goals":
        st, _cur = _eval_leg(info, {"goals_h": hs, "goals_a": as_}, final=False)
        return st if st in ("won", "lost") else None
    base = _METRIC_BASE.get(info.get("metric")) if info.get("metric") in _RATE90 else None
    if base and vals:
        ch, ca = _as_int(vals.get(f"{base}_h")), _as_int(vals.get(f"{base}_a"))
        if ch is not None and ca is not None:
            side = info.get("side")
            cur = ch if side == "HOME" else ca if side == "AWAY" else ch + ca
            if cur > info["line"]:
                return "won" if info["dir"] == "OVER" else "lost"
    return None


def _mk_live(pct: int, source: str, ref_pct) -> dict:
    """Assemble le dict de barre {pct, trend, source}. Tendance ↑/↓ vs `ref_pct` (% d'avant-match, bande ±4)."""
    trend = "flat"
    if isinstance(ref_pct, (int, float)):
        if pct - ref_pct >= 4:
            trend = "up"
        elif ref_pct - pct >= 4:
            trend = "down"
    return {"pct": pct, "trend": trend, "source": source}


_CATALOG_METRICS = {"goals", "corners", "cards", "redcards", "sot", "shots"}  # marchés « ligne » (total/équipe/handicap)


def _catalog_market_pct(catalog, info: dict, home: str, away: str) -> float | None:
    """Proba implicite DÉ-MARGÉE de l'issue jouée, à partir de la COTE LIVE du marché correspondant dans le
    catalogue Bet Builder Unibet (`catalog` = [{id, text, odds}], cotes en direct). Matching par SIGNATURE
    de marché (même `_leg_metric` des deux côtés → exact, pas flou) : métrique + côté (équipe/total) + sens
    (Plus/Moins/Handicap) + ligne + périmètre (match/1ère MT). De-vig sur les issues SŒURS (même marché,
    ligne opposée) ; à défaut, proba implicite brute (reflet direct de la cote). None si pas de match sûr."""
    metric, side, dirn = info.get("metric"), info.get("side"), info.get("dir")
    line, scope = info.get("line"), info.get("scope")
    if metric not in _CATALOG_METRICS or line is None or dirn not in ("OVER", "UNDER", "HCAP"):
        return None
    lref = round(abs(float(line)), 3)
    played_odds, sib_inv = None, 0.0
    for e in (catalog or []):
        od = e.get("odds")
        if not (isinstance(od, (int, float)) and od > 1):
            continue
        ci = _leg_metric({"sel": e.get("text", "")}, home, away)
        if ci.get("metric") != metric or ci.get("scope") != scope or ci.get("side") != side:
            continue
        cl, cd = ci.get("line"), ci.get("dir")
        if cl is None or cd not in ("OVER", "UNDER", "HCAP") or round(abs(float(cl)), 3) != lref:
            continue
        is_played = (cd == dirn and round(float(cl), 3) == round(float(line), 3))
        if is_played and played_odds is None:
            played_odds = od
        else:
            sib_inv += 1.0 / od                       # issue sœur (même marché) -> de-vig
    if played_odds is None:
        return None
    inv_p = 1.0 / played_odds
    if sib_inv > 0:                                   # de-vig sur les issues sœurs (marché complet)
        return min(1.0, inv_p / (inv_p + sib_inv))
    return min(1.0, inv_p)                            # issue seule -> proba implicite brute (reflet cote)


# --- catalogue Bet Builder LIVE mémoïsé (cote fraîche de TOUS les marchés d'un match en cours) ---
_LIVE_CAT_CACHE: dict = {}       # event_id (str) -> (ts, [{id, text, odds}])
_LIVE_CAT_TTL = 45               # s : cote live des marchés fraîche sans marteler ShapeGames


def live_catalog(event_id) -> list:
    """Catalogue Bet Builder LIVE d'un match (cotes fraîches de tous les marchés) — LECTURE SEULE du cache
    rempli hors event loop par `warm_live_catalog`. [] si absent/périmé (-> la barre retombe sur le modèle)."""
    hit = _LIVE_CAT_CACHE.get(str(event_id))
    return hit[1] if (hit and time.time() - hit[0] < _LIVE_CAT_TTL) else []


def warm_live_catalog(event_id) -> None:
    """Re-fetch le catalogue Bet Builder d'un match (urllib BLOQUANT via ShapeGames) et remplit le cache.
    À appeler UNIQUEMENT hors event loop (asyncio.to_thread). No-op si l'entrée est encore fraîche."""
    key = str(event_id)
    hit = _LIVE_CAT_CACHE.get(key)
    if hit and time.time() - hit[0] < _LIVE_CAT_TTL * 0.6:
        return
    try:
        from app import unibet
        cat = unibet.betbuilder_catalog(key)
    except Exception:
        cat = None
    if cat:                                           # ne JAMAIS écraser un bon cache par un échec ([])
        _LIVE_CAT_CACHE[key] = (time.time(), cat)


# Poids du mélange (demande user 2026-07-15 : « fair par rapport à la cote actuelle du direct MAIS AUSSI
# aux analyses d'avant-match et à la statistique du direct »). Le % n'est PAS la cote : c'est la fusion de
# 3 signaux. Le poids de l'avant-match DÉCROÎT avec l'avancement du match `f` (le direct prend le dessus).
_W_MKT = 0.40                    # cote actuelle du marché (dé-margée)
_W_MOD0 = 0.20                   # statistique du direct (score + temps restant) — grandit avec f
_W_PRE0 = 0.40                   # analyse d'avant-match — fond à 0 en fin de match


def live_prob(sport: str, sel: str, code: str, home: str, away: str,
              hs, as_, minute=None, win_odds=None, ref_pct=None, catalog=None, vals=None,
              game_frac=None) -> dict | None:
    """% (0-100) « fair » que le pari `sel`/`code` PASSE à l'instant vu — FUSION de 3 signaux : (1) la cote
    actuelle du pari en direct (dé-margée : `win_odds`/`catalog`), (2) l'analyse d'avant-match (`ref_pct`),
    (3) la statistique du direct (modèle Poisson foot / normale basket). `game_frac` = fraction de match
    écoulée (basket ; foot déduit de `minute`). Le poids de l'avant-match décroît au fil du match. Renvoie
    {"pct","trend","source"} ou None si aucun signal LIVE (cote NI modèle) → pas de barre. PURE AFFICHAGE :
    n'écrit aucune stat, ne compte jamais au ROI/à la calibration."""
    hs, as_ = _as_int(hs), _as_int(as_)
    if hs is None or as_ is None:
        # SCORE live indisponible (feed Unibet/LiveScore en retard ou format inattendu) : ni verrou ni modèle
        # possibles, MAIS on ne masque PAS la barre pour autant — intention « la barre chance live pour TOUS
        # les paris » (user 2026-07-21). On affiche la confiance d'AVANT-MATCH (source honnête), qui basculera
        # d'elle-même dès que le score arrive. Sans ce repli, un match LIVE dont le score tarde n'a AUCUNE barre
        # (bug user 2026-08-02 : « pas de chance live pour ce match » sur un match brésilien au score non lu).
        p_pre = ref_pct / 100.0 if isinstance(ref_pct, (int, float)) else None
        if p_pre is not None:
            return _mk_live(int(round(max(0.0, min(1.0, p_pre)) * 100)), "avant-match", ref_pct)
        return None
    info = _leg_metric({"sel": sel, "code": code}, home, away)
    wside = _winner_side(sel, code, home, away, sport)
    # VERROU : pari déjà mathématiquement tranché par le direct -> 100/0 (pas de dilution par cote/analyse).
    lk = _live_locked(sport, sel, code, info, hs, as_, vals)
    if lk == "won":
        return _mk_live(100, "acquis", ref_pct)
    if lk == "lost":
        return _mk_live(0, "perdu", ref_pct)
    # (1) COTE actuelle du pari en direct, dé-margée : vainqueur/DC via listView, autres marchés via catalogue.
    if wside is not None:
        p_mkt = _winner_pct(wside, win_odds) if win_odds else None
    else:
        p_mkt = _catalog_market_pct(catalog, info, home, away)
    # (3) STATISTIQUE du direct (notre modèle propre, indépendant de la cote).
    p_mod = _live_model_pct(sport, sel, code, info, wside, hs, as_, minute, vals, game_frac)
    # (2) ANALYSE d'avant-match (notre confiance publiée sur ce pari).
    p_pre = ref_pct / 100.0 if isinstance(ref_pct, (int, float)) else None
    if p_mkt is None and p_mod is None:
        # Repli AVANT-MATCH (demande user 2026-07-21 : « la barre pour TOUS les paris ») : marché non
        # mappable en cote/modèle live (ex. tennis « remporte au moins un set ») -> on affiche quand même
        # la barre sur la confiance publiée, source honnête « avant-match ». Elle basculera d'elle-même
        # dès qu'un VERROU tranche (set pris -> 100 « acquis », cf. _live_locked plus haut).
        if p_pre is not None:
            return _mk_live(int(round(max(0.0, min(1.0, p_pre)) * 100)), "avant-match", ref_pct)
        return None                          # vraiment aucun signal -> pas de barre
    f = (game_frac if isinstance(game_frac, (int, float))
         else (min(90, max(0, minute)) / 90.0) if (sport == "foot" and minute is not None) else 0.5)
    w_mod = _W_MOD0 + _W_PRE0 * f            # le direct grandit avec le temps
    w_pre = _W_PRE0 * (1.0 - f)              # l'avant-match s'efface avec le temps
    num = den = 0.0
    parts = []
    if p_mkt is not None:
        num += _W_MKT * p_mkt; den += _W_MKT; parts.append("cote")
    if p_mod is not None:
        num += w_mod * p_mod; den += w_mod; parts.append("stats live")
    if p_pre is not None:
        num += w_pre * p_pre; den += w_pre; parts.append("analyse")
    if den <= 0:
        return None
    pct = int(round(max(0.0, min(1.0, num / den)) * 100))
    return _mk_live(pct, " + ".join(parts), ref_pct)


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


def _why_parts(why) -> tuple[int | None, str, str]:
    """Découpe le « pourquoi » d'une jambe en (proba %, résumé, détail).
    - extrait « proba ~80 % » -> pastille, puis le retire du texte ;
    - coupe à la 1ʳᵉ proposition (avant ' ; ' ou '. ') : résumé visible + détail repliable.
    Chaque morceau est remis en phrase propre (majuscule initiale)."""
    t = re.sub(r"\s+", " ", str(why or "")).strip()
    pm = re.search(r"proba\s*[~≈]?\s*(\d{1,3})\s*%", t, re.I)
    pct = int(pm.group(1)) if pm else None
    t = re.sub(r"[;,]?\s*proba\s*[~≈]?\s*\d{1,3}\s*%\.?", "", t, flags=re.I).strip(" ;,.")
    head, tail = t, ""
    sm = re.search(r"\s*[;.]\s+", t)
    if sm and sm.end() < len(t):
        head, tail = t[:sm.start()].strip(" ;,."), t[sm.end():].strip(" ;,.")
    cap = lambda s: (s[:1].upper() + s[1:]) if s else s
    return pct, cap(head), cap(tail)


_COMBO_LIVE_CACHE: dict = {}     # (eid, oids) -> (ts, real_odds live)
_COMBO_LIVE_TTL = 180            # 3 min : cote re-pricée fraîche sans marteler Kambi


def _combo_oids_key(event_id, combo):
    legs = (combo or {}).get("legs") or []
    oids = [l.get("oid") for l in legs if l.get("oid")]
    if not event_id or not legs or len(oids) != len(legs) or len(oids) < 2:
        return None
    return (str(event_id), tuple(oids))


def _combo_live_odds(event_id, combo):
    """VRAIE cote Unibet du combiné re-pricée récemment — LECTURE SEULE du cache (AUCUN appel réseau :
    cette fonction tourne dans le handler async). None si pas en cache/périmé -> l'appelant retombe sur
    `combo.real_odds` (figé au scan). Le pricing réel est fait hors event loop par `warm_combo_odds`."""
    key = _combo_oids_key(event_id, combo)
    if not key:
        return None
    hit = _COMBO_LIVE_CACHE.get(key)
    if hit and time.time() - hit[0] < _COMBO_LIVE_TTL:
        return hit[1]
    return None


def warm_combo_odds(event_id, combo) -> None:
    """Re-price la VRAIE cote du combiné via Kambi (urllib BLOQUANT) et remplit `_COMBO_LIVE_CACHE`.
    À appeler UNIQUEMENT depuis une boucle de fond (asyncio.to_thread), jamais dans le rendu. No-op si
    l'entrée est encore fraîche (évite de marteler Kambi)."""
    key = _combo_oids_key(event_id, combo)
    if not key:
        return
    hit = _COMBO_LIVE_CACHE.get(key)
    if hit and time.time() - hit[0] < _COMBO_LIVE_TTL * 0.6:   # encore frais -> rien à faire
        return
    try:
        from app import unibet
        real = unibet.betbuilder_odds(key[0], list(key[1]))
    except Exception:
        real = None
    _COMBO_LIVE_CACHE[key] = (time.time(), real)


def has_combo(sport: str, match_id) -> bool:
    """Vrai si le match porte un combiné same-match (Coupe du Monde) — jambes présentes dans le sidecar.
    Test LÉGER (via `meta`, mémoïsé) pour classer une carte dans le cadre « Combinés » vs « Paris à jouer »
    sans rendre le HTML complet. Cohérent avec `combo_html` (même condition `combo.legs`)."""
    return bool(((meta(sport, match_id) or {}).get("combo") or {}).get("legs"))


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
    # RENDU TICKET PREMIUM (style carte Telegram, sans logo — demande user 2026-07-12) : accent cyan,
    # pastilles de cote vertes, justification par jambe (barre latérale), cote combinée en gros en bas.
    # Toute la logique live/résultat (badges ✅/❌/➖, scores en direct, masquage post-règlement) est PRÉSERVÉE.
    # JAMBE = CARTE DE SIMPLE (demande user 2026-07-14) : même cadre `.cleg` que le combiné du jour —
    # en-tête SPORT + badge d'état, pari en gras, cote à droite, justification repliable. Combiné de match
    # (same-match) : on n'affiche PAS le nom du match (déjà en tête de la carte parent).
    _splbl = {"foot": "FOOTBALL", "tennis": "TENNIS", "basket": "BASKET"}.get(sport, (sport or "").upper())
    _emo = {"foot": "⚽", "tennis": "🎾", "basket": "🏀"}.get(sport, "•")
    # Entrées de la barre « Chance live » par jambe (score + minute + cotes vainqueur live du match). Une
    # jambe = un marché de CE match -> `live_prob` s'applique par jambe. Lecture seule (0 réseau). Seulement
    # quand le match est en cours (`live` non nul) ; sinon on ne calcule rien.
    _lh, _la = m.get("home", ""), m.get("away", "")
    _lhs = _las = _lmin = _lwo = _lfrac = None
    _lcat = []
    _lvals = None
    _bar_html = None
    if live is not None:
        from app import match_select, web            # imports locaux (évite le cycle au chargement)
        _bar_html = web._live_bar_html
        _lld = match_select.live_state_for(sport, _lh, _la)
        _lsc = (_lld or {}).get("score") or {}
        _lhs, _las = _as_int(_lsc.get("home")), _as_int(_lsc.get("away"))
        _lmin = match_select.live_minute(_lld)
        _lwo = match_select.live_win_odds(sport, _lh, _la)
        _lcat = live_catalog(match_id)               # cotes live de TOUS les marchés (par jambe)
        _lvals = _combo_live_vals(m)                 # compteurs live (buts/corners/cartons) -> « stats live »
        _lfrac = match_select.basket_frac(_lld, m.get("comp", "")) if sport == "basket" else None
    _leg_pcts = []                                    # % live par jambe -> barre GLOBALE (produit)
    rows = []
    for i, leg in enumerate(combo["legs"]):
        lr = leg.get("result")                       # résultat FINAL réglé (post-match) s'il existe
        in_live = lr is None and live is not None
        prog = ""
        if in_live and live["legs"][i].get("disp"):  # compteur courant/seuil (ou marge handicap)
            prog = f' · {live["legs"][i]["disp"]}'
        # État -> classe de bord + badge (à venir / acquise / perdue / remboursée / en cours).
        if lr == "won":
            state, btxt, bcls = "won", "✅ ACQUISE", "w"
        elif lr == "lost":
            state, btxt, bcls = "lost", "❌ PERDUE", "l"
        elif lr == "void":
            state, btxt, bcls = "push", "➖ REMB.", "n"
        elif in_live:
            state, btxt, bcls = "live", f"⏳ EN COURS{prog}", "live"
        else:
            state, btxt, bcls = "pending", "À VENIR", "p"
        try:
            cote = f"{float(leg.get('cote')):g}"
        except (TypeError, ValueError):
            cote = "?"
        sel = _h.escape(pretty_sel(str(leg.get("sel", "")), m.get("home", ""), m.get("away", "")))
        # Glose « ↳ » en clair de la jambe (jambe = pari joué -> DOIT avoir son explication, demande user
        # 2026-07-17). Point d'entrée TOTAL `web._bet_gloss` (jamais vide). Import local (évite le cycle).
        from app import web as _web
        _lgl = _web._bet_gloss(str(leg.get("sel", "")), sport, m.get("home", ""), m.get("away", ""))
        gloss_html = (f'<div class="cleg-gloss"><span class="ar">↳</span> {_h.escape(_lgl)}</div>'
                      if _lgl else "")
        pct, head, tail = _why_parts(leg.get("why"))
        full = f"{head}. {tail}" if (head and tail) else (head or tail)
        if full and full[-1] not in ".!?":
            full += "."
        # Justification repliable (cachée par défaut, dépliée au clic ; masquée une fois le combiné réglé).
        if full and not res:
            why_html = ('<details class="cleg-fold"><summary class="cleg-fold-s" onclick="event.stopPropagation()">'
                        f'💡 Pourquoi cette jambe<span class="cleg-chev">▾</span></summary>'
                        f'<div class="cleg-why">{_h.escape(full)}</div></details>')
        else:
            why_html = ""
        # Barre « Chance live » de la jambe (uniquement en cours) : acquise -> 100, perdue -> 0, sinon
        # `live_prob` (cote live dé-margée / repli modèle). Alimente aussi la barre GLOBALE (produit).
        bar_html = ""
        if in_live and _bar_html is not None:
            _ls = (live["legs"][i] or {}).get("status")
            if _ls == "won":
                _lp = {"pct": 100, "trend": "flat", "source": "acquis"}
            elif _ls == "lost":
                _lp = {"pct": 0, "trend": "flat", "source": "perdu"}
            else:
                _lp = live_prob(sport, leg.get("sel", ""), leg.get("code", ""), _lh, _la,
                                _lhs, _las, _lmin, _lwo, leg.get("prob"), _lcat, _lvals, _lfrac)
            if _lp:
                _leg_pcts.append(_lp["pct"])
                bar_html = _bar_html(_lp)
        rows.append(
            f'<div class="cleg {state}">'
            f'<div class="cleg-h"><span class="cleg-comp"><b class="cleg-sport">{_emo} {_splbl}</b></span>'
            f'<span class="cleg-bdg {bcls}">{btxt}</span></div>'
            f'<div class="cleg-body"><div class="cleg-main"><div class="cleg-pick">{sel}</div>'
            f'{gloss_html}</div>'
            f'<span class="cleg-cote"><span class="cleg-cote-l">COTE</span>'
            f'<span class="cleg-cote-v">{cote}</span></span></div>'
            f'{bar_html}{why_html}</div>')
    # En-tête : SEUL le résultat FINAL (post-match) affiche un statut ; en live -> badge « en direct » neutre.
    gcls = (" won" if res == "won" else " lost" if res == "lost" else " void" if res == "void" else "")
    if res == "won":
        badge = '<span class="b won">Gagné</span>'
    elif res == "lost":
        badge = '<span class="b lost">Perdu</span>'
    elif res == "void":                              # aucune jambe réglable -> remboursé (mise rendue)
        badge = '<span class="b void">Remboursé</span>'
    elif live:
        badge = '<span class="b live">● en direct</span>'
    else:
        badge = ""
    try:
        total = f"{float(combo.get('total')):.2f}"
    except (TypeError, ValueError):
        total = "?"
    # Cote affichée : VRAIE cote Unibet corrélée (re-pricée live si possible), sinon produit des jambes.
    real = combo.get("real_odds")
    _live = _combo_live_odds(match_id, combo)      # re-pricing LIVE (cote fraîche, pas figée au scan)
    if _live:
        real = _live
    try:
        cote_val = f"{float(real):.2f}" if real else total
    except (TypeError, ValueError):
        cote_val = total
    _tag = (' <span class="top">Unibet en direct</span>' if _live
            else ' <span class="top">cote Unibet</span>' if real else "")
    # Barre « Chance live » GLOBALE du combiné (en cours, non réglé) : perdu dès qu'une jambe saute,
    # acquis quand toutes le sont, sinon proba implicite de la VRAIE cote combinée live (repli = produit
    # des % de jambes). PURE AFFICHAGE.
    _glob_bar = ""
    if live is not None and _bar_html is not None and res is None:
        if live["status"] == "won":
            _glp = {"pct": 100, "trend": "flat", "source": "acquis"}
        elif live["status"] == "lost":
            _glp = {"pct": 0, "trend": "flat", "source": "perdu"}
        elif _live:
            _glp = {"pct": int(round(100.0 / float(_live))), "trend": "flat", "source": "cote live"}
        elif _leg_pcts:
            _prod = 1.0
            for _pc in _leg_pcts:
                _prod *= _pc / 100.0
            _glp = {"pct": int(round(_prod * 100)), "trend": "flat", "source": "modèle"}
        else:
            _glp = None
        _glob_bar = _bar_html(_glp) if _glp else ""
    n_legs = len(combo["legs"])
    synth = combo.get("why")
    # Synthèse REPLIABLE (compacité) : cachée par défaut, dépliée au clic. Masquée une fois le combiné réglé.
    synth_html = (f'<details class="tkt-synth-d"><summary onclick="event.stopPropagation()">'
                  f'<span class="tkt-synth-t">💡 Pourquoi ce combiné</span><span class="tkt-chev">▾</span></summary>'
                  f'<div class="tkt-synth">{_h.escape(_sentence_case(str(synth)))}</div></details>'
                  if (synth and not res) else "")
    return (f'<div class="tkt{gcls}"><div class="tkt-h">Combiné '
            f'<span class="n">· {n_legs} sélections</span> {badge}{_tag}</div>'
            f'{synth_html}<div class="mc-combo-legs">{"".join(rows)}</div>'
            f'{_glob_bar}'
            f'<div class="tkt-cote"><span class="l">Cote combinée</span>'
            f'<span class="v">{cote_val}</span></div></div>')


def _teamtot_over_penalty(sport: str, code: str, streaks) -> float:
    """REFROIDISSEMENT (demande user 2026-07-23, après l'échec Minnesota +92.5 : ligne 92,5 > moyenne 92,
    annoncé 69 %, réel 86 pts → perdu) : un OVER de TOTAL D'ÉQUIPE dont la LIGNE dépasse la MOYENNE de points
    de l'équipe est un pari de MOMENTUM sur-vendu (la proba de dépasser sa propre moyenne est ~50 %, pas 69 %).
    Renvoie les POINTS de confiance à retirer (0 si non applicable). BASKET seulement : la moyenne (streaks
    « Scored points average ») n'existe pas pour le foot. Lecture seule / forward-looking (sélection + affichage)."""
    if sport != "basket" or not code or not isinstance(streaks, dict):
        return 0.0
    parts = code.upper().split()
    if len(parts) < 4 or parts[0] != "TEAMTOT" or parts[2] != "OVER":
        return 0.0
    side = parts[1].lower()
    if side not in ("home", "away"):
        return 0.0
    try:
        line = float(parts[3].replace(",", "."))
    except ValueError:
        return 0.0
    avg = None
    for pair in (streaks.get(side) or []):
        if (isinstance(pair, list) and len(pair) == 2
                and "scored" in str(pair[0]).lower() and "average" in str(pair[0]).lower()):
            try:
                avg = float(str(pair[1]).replace(",", "."))
            except ValueError:
                avg = None
            break
    if avg is None:
        return 0.0
    gap = line - avg
    if gap <= 0:                       # ligne SOUS la moyenne -> OVER favorable, aucune pénalité
        return 0.0
    # base 10 pts dès que la ligne dépasse la moyenne (ancre la conf près de ~50 %) + 1,5 pt par point d'écart,
    # borné à 20. Minnesota (gap 0,5) : ~10,7 pts -> 69 % -> ~58 % -> EV négatif -> abstention.
    return min(20.0, 10.0 + 1.5 * gap)


def _cool_conf(cc, sport: str, code: str, streaks):
    """Confiance calibrée `cc` (déjà passée par `calibrated_conf`) MOINS le refroidissement OVER-total-équipe.
    None-safe ; plancher à 1.0."""
    if cc is None:
        return cc
    pen = _teamtot_over_penalty(sport, code, streaks)
    return max(1.0, cc - pen) if pen else cc


def retained_bet(sport: str, match_id, for_history: bool = False) -> dict | None:
    """Le pari SIMPLE « retenu » par la LOGIQUE NORMALE du site (filtre ⭐ : conf recalibrée ≥ 65 %,
    EV ≥ +3 %, garde-fous de cote, marché réglable/non exclu) = ce que l'app aurait gardé pour un
    match ordinaire. None si AUCUN simple ne passe : on ne FORCE alors PAS de pari simple. Utile en
    CdM, où le combiné force l'affichage de TOUS les matchs : on n'exhibe le simple que s'il aurait
    réellement été récupéré (sinon ce sont des « ancres » à cote plate / EV négatif jamais retenues
    ailleurs → seul le combiné reste à l'affiche). {sel, prob, cote, result, idx} ou None.

    `for_history=True` : pour le SUIVI (stats/courbe). Les exclusions auto (Sets/Total/Corners…)
    sont FORWARD-LOOKING — elles bloquent les PROCHAINS paris (publication), mais ne doivent PAS
    effacer un pari DÉJÀ joué et posté. Sinon le compteur « réglés » BAISSE quand un marché vient
    d'être banni (un pari posté la semaine dernière sort rétroactivement du compte → « posté ≠ compté »).
    En mode historique on garde donc le filtre de base (65 %+EV+garde-fous+calibration) SANS l'overlay
    d'exclusion : un pari réellement publié reste compté à vie (track record honnête, défaites incluses)."""
    bets = bets_of(sport, match_id)
    m = meta(sport, match_id) or {}
    # PARI DE CONFIANCE MÉCANIQUE (profil 93% du backtest 2026-08-29, `app.confidence_pick`). Posé au scan
    # dans `d["confidence_bet"]` (sel/code/prob/cote FIGÉS), sélectionné dans le VIVIER COMPLET du match
    # (fantômes+bets) car l'analyste ne commit qu'~1 pari/match -> 105/117 de ces paris n'existent que dans
    # les fantômes. Il PRIME sur le pick value de Claude (la value sera un 2e sélecteur mécanique plus tard) et
    # CONTOURNE le gate EV de _recommend (le profil 93% est EV-indifférent : favoris DC/Handicap cote ~1.15).
    # Le flag n'est posé QUE sur des matchs À VENIR au scan -> jamais rétroactif (pas d'incident backfill). Le
    # RÉSULTAT est lu depuis le fantôme de MÊME code, réglé normalement. `stat_bet` figé prime (monotone).
    # OPTION B / marqueur vague (user 2026-08-29) : on ne RÉVÈLE un pari mécanique à venir que s'il est FINAL —
    # publié (published_bet), re-vérifié à la vague (prematch_done), réglé, ou en mode historique. Avant ça
    # (matin, provisoire) -> None : l'app le montre en « À analyser / en attente », jamais comme un pari joué
    # qui pourrait changer/disparaître à la re-vérif ~1h avant KO. La vague pose prematch_done AVANT de bâtir
    # la carte -> la publication voit bien le pari. Réversible : REVEAL_ONLY_FINAL = False.
    _final = (not REVEAL_ONLY_FINAL or for_history or is_settled(m) or m.get("prematch_done")
              or (isinstance(m.get("published_bet"), dict) and m["published_bet"].get("sel")))
    _cb = m.get("confidence_bet")
    if _final and isinstance(_cb, dict) and _cb.get("code") and not isinstance(m.get("stat_bet"), dict):
        try:
            from app import confidence_pick as _cpk
            _res = _cpk.resolve_result(m, _cb["code"])
        except Exception:
            _res = None
        _cc = _cool_conf(calibrated_conf(_cb.get("prob"), sport, _cb["code"]), sport, _cb["code"], m.get("streaks"))
        return {"idx": 0, "sel": _cb.get("sel"), "prob": _cb.get("prob"), "cprob": _cc,
                "cote": _cb.get("cote"), "result": _res, "code": _cb["code"], "tier": "confiance"}
    # PARI DE VALUE MÉCANIQUE (profil « grassy » du backtest 2026-08-29, `app.value_pick`). Posé au scan dans
    # `d["value_bet"]` sur les matchs SANS pari de confiance (Confiance prioritaire). Même traitement que la
    # confiance : renvoyé en priorité (bypass EV — le gate EV+3 % est DÉJÀ dans la sélection value), forward +
    # historique via le flag, résultat résolu depuis le fantôme. `stat_bet` figé prime (monotone).
    _vb = m.get("value_bet")
    if (_final and isinstance(_vb, dict) and _vb.get("code") and not (isinstance(_cb, dict) and _cb.get("code"))
            and not isinstance(m.get("stat_bet"), dict)):
        try:
            from app import confidence_pick as _cpk2
            _vres = _cpk2.resolve_result(m, _vb["code"])
        except Exception:
            _vres = None
        _vcc = _cool_conf(calibrated_conf(_vb.get("prob"), sport, _vb["code"]), sport, _vb["code"], m.get("streaks"))
        return {"idx": 0, "sel": _vb.get("sel"), "prob": _vb.get("prob"), "cprob": _vcc,
                "cote": _vb.get("cote"), "result": _vres, "code": _vb["code"], "tier": "value"}
    # FOOT = 2 SÉLECTEURS MÉCANIQUES SEULEMENT (confiance + value). Un match foot À VENIR SANS confidence_bet
    # NI value_bet est une ABSTENTION : on ne rejoue PLUS l'ANCIEN pick EV de Claude (`_recommend`), qui
    # polluait le tier value avec des paris hors-profil (ex. Real Sociedad « Plus de 1.5 @1.25 », cote < 1.40).
    # On PRÉSERVE la montante (mids dédiés) et les combinés (leur pick vient d'ailleurs), et on ne touche pas
    # aux réglés figés (stat_bet -> couche stats/anchor). Réversible : FOOT_MECHANICAL_ONLY = False.
    if (FOOT_MECHANICAL_ONLY and sport == "foot" and not isinstance(m.get("stat_bet"), dict)
            and not (isinstance(_cb, dict) and _cb.get("code"))
            and not (isinstance(_vb, dict) and _vb.get("code"))
            and not (m.get("combo") or {}).get("legs")
            and str(match_id) not in _montante_mids()):
        return None
    # ABSTENTION (panel rejeté / sans value) : le sidecar `abstained` = méta + fantômes SEULS, JAMAIS un pari
    # JOUÉ. La couche AFFICHAGE le cache déjà (`list_for` : `if d.get("abstained") and not stat_bet`). On
    # l'exclut ICI aussi pour que la couche STATS (`stat_bet` se replie sur `retained_bet(for_history=True)`)
    # ne le ressuscite pas depuis son tableau `.md`/fantômes -> sinon un match abstenu (ex. rejeté 1/3 par le
    # panel) fuite dans le suivi « Simple » du sport et serait COMPTÉ au ROI une fois réglé (bug user 2026-07-31,
    # San Luis–Tijuana). Un pari PUBLIÉ n'est JAMAIS `abstained` (le scan préserve son sidecar complet) et un
    # `stat_bet` FIGÉ prime (garde) -> aucun risque de retirer un pari déjà compté (compteur monotone intact).
    if m.get("abstained") and not isinstance(m.get("stat_bet"), dict):
        return None
    # GARDE SKIP / INCOHÉRENCE HEADLINE↔TABLE (bug user 2026-08-14, Wolves) : le modèle a écrit un verdict
    # SKIP / « no-value intégral » en tête (donc AUCUN pick_code committé) mais a laissé une jambe traîner
    # dans sa table de paris -> le pari était retenu à tort en Confiance, avec la phrase de SKIP affichée
    # comme « analyse ». On tranche vers l'ABSTENTION : sans pick_code (pas de marché committé) ET texte de
    # tête explicitement SKIP/no-value -> aucun pari retenu. Garde `stat_bet` figé : un pari DÉJÀ COMPTÉ
    # prime et n'est JAMAIS retiré (invariant monotone intact). Les fantômes continuent de nourrir la calib.
    if (not str(m.get("pick_code") or "").strip()
            and not isinstance(m.get("stat_bet"), dict)
            and re.match(r"^\s*(à\s+[ée]viter|à\s+[ée]carter|à\s+bannir|skip\b|no[-\s]?value|aucun\s+pari)",
                         str(m.get("pick") or ""), re.I)):
        return None
    if not bets and for_history:
        # Repli SUIVI : le tableau du .md peut être VIDE (ré-analyse « NONE » après publication) alors que
        # le SIDECAR porte le pari PUBLIÉ réinjecté + réglé par le filet settle_analyst (2026-07-21 « ne
        # pas flouter l'user ») -> on lit d["bets"] pour que le gel stat_bet/l'historique le comptent.
        bets = [{"sel": b.get("sel", ""), "cote": b.get("odds") or b.get("cote"), "prob": b.get("prob")}
                for b in (m.get("bets") or []) if b.get("sel")]
    if not bets:
        # GEL PUBLIÉ (demande user 2026-07-28) : une ré-analyse a VIDÉ le tableau de paris, mais le pari a été
        # POSTÉ aux abonnés et n'est PAS réglé -> il a pu être JOUÉ (ex. palier montante « Moins de 2.5 »
        # publié + misé). On le RESTITUE à son PRIX CONSEILLÉ au lieu de l'abandonner (sinon il retombe en
        # abstention -> provisoire d'un AUTRE marché, ce qui trahit l'abonné qui a parié). Vaut pour
        # l'affichage COURANT (for_history=False) ET l'historique. Jamais sur un match à COMBINÉ.
        # ⚠️ On lit le dict FIGÉ `published_bet` DIRECTEMENT (pas la fonction published_bet(), qui rappellerait
        # retained_bet(for_history=True) -> récursion infinie). Repli : posté (get_prono) + `pick` du sidecar.
        _pb = m.get("published_bet") if isinstance(m.get("published_bet"), dict) else None
        if not (m.get("combo") or {}).get("legs") and not is_settled(m):
            _psel, _pcote, _pprob = None, None, None
            if _pb and _pb.get("sel"):
                _psel, _pcote, _pprob = _pb.get("sel"), _pb.get("cote"), _pb.get("prob")
            else:
                try:
                    from app import notify as _notify0
                    if _notify0.get_prono(str(match_id)) and m.get("pick"):
                        _psel = re.sub(r"\s*@.*$", "", str(m.get("pick")))
                        _pm = re.search(r"@\s*([\d]+[.,][\d]+)", str(m.get("pick") or ""))
                        _pcote = float(_pm.group(1).replace(",", ".")) if _pm else None
                except Exception:
                    _psel = None
            if _psel:
                from app.settle_analyst import code_from_pick as _cfp0
                return {"idx": 0, "sel": _psel, "prob": _pprob, "cprob": _pprob, "cote": _pcote,
                        "result": None,
                        "code": (_cfp0(_psel, sport, m.get("home", ""), m.get("away", "")) or "")}
        return None
    try:
        from app.settle_analyst import code_from_pick
        ex_sports, _ = (set(), set()) if for_history else auto_exclusions()
        ex_markets = set() if for_history else excluded_markets(sport)   # per-sport (vide en historique)
        # ⛔ BANS DURS (Corners, BTTS) : JAMAIS un pari joué, MÊME en historique (`for_history`). Sans ça, un
        # BTTS pické malgré le ban (non publié) était retenu/figé/affiché à tort (bug user 2026-08-07 : Cambuur,
        # Chicago…). Les exclusions DYNAMIQUES, elles, restent honorées en historique (pari joué avant exclusion).
        if sport == "foot":
            ex_markets = ex_markets | _HARD_BAN_FOOT
        if sport in ex_sports:
            return None
        ok, cprobs, codes = set(), [], []
        for i, b in enumerate(bets):
            code = code_from_pick(b.get("sel", ""), sport, m.get("home", ""), m.get("away", ""))
            codes.append(code)
            cprobs.append(_cool_conf(calibrated_conf(b.get("prob"), sport, code), sport, code, m.get("streaks")))
            if code and market_of(code) not in ex_markets:
                ok.add(i)
        # CONFIDENCE-FIRST seulement en FORWARD (publication de NOUVEAUX paris). En mode SUIVI/historique
        # (for_history), on garde la reconstruction d'avant (value + ancre pari publié/compté) : sinon un
        # favori jamais posté aux abonnés ressusciterait rétroactivement dans « Terminés » (interdit :
        # « corrections d'historique = avant abonnés seulement »).
        reco = _recommend(bets, ok, cprobs, codes, conf_first=not for_history)
    except Exception:
        reco = _recommend(bets, conf_first=not for_history)
    ri = reco.get("idx")
    if reco.get("verdict") != "play" or ri is None:
        ri = None
        # ANCRE ROBUSTE (SUIVI/historique uniquement) : un pari DÉJÀ COMPTÉ (stat_bet figé — survit à un
        # reset du canal ET à la dérive de calibration) OU un prono PUBLIÉ (get_prono, pour les à-venir)
        # reste « retenu » -> il est bien COMPTÉ et AFFICHÉ en terminé (posté/compté = visible). Ne touche
        # PAS la publication de NOUVEAUX paris (for_history=False reste strict), ni les matchs à COMBINÉ.
        if for_history and not ((m.get("combo") or {}).get("legs")):
            _sb = m.get("stat_bet")
            _anchor = _sb.get("sel") if (isinstance(_sb, dict) and _sb.get("sel")) else None
            if not _anchor:
                try:
                    from app import notify as _notify
                    if _notify.get_prono(str(match_id)):
                        _anchor = re.sub(r"\s*@.*$", "", str(m.get("pick") or ""))
                except Exception:
                    _anchor = None
            if _anchor:                          # rapprochement au pari compté/publié (jamais de max-prob deviné)
                _pk = _norm_sel(_anchor)
                _nbs = [_norm_sel(b.get("sel", "")) for b in bets]
                # EXACT d'abord, puis PRÉFIXE/INCLUSION : le `pick` publié est souvent une forme COURTE
                # (« Connecticut Sun +14.5 ») tandis que la sélection structurée porte le suffixe du marché
                # (« … (hand., prol. incl.) ») -> l'égalité stricte ratait le gel, et un pari PUBLIÉ + GAGNÉ
                # n'était jamais compté (bug Connecticut Sun–Minnesota, demande user 2026-07-09). On retient
                # la 1re jambe qui commence par (ou est contenue dans) l'ancre.
                ri = next((i for i, nb in enumerate(_nbs) if _pk and nb == _pk), None)
                if ri is None:
                    ri = next((i for i, nb in enumerate(_nbs)
                               if _pk and (nb.startswith(_pk) or _pk.startswith(nb))), None)
        if ri is None:
            return None
    # ⛔ BAN DUR ABSOLU (même repêché par l'ancre « pari publié ») : un pari de marché banni (BTTS/Corners)
    # n'est JAMAIS retenu/figé/affiché, publié ou pas. Le bloc d'ancre ci-dessus ne re-teste pas ex_markets ;
    # sans ce garde, un BTTS PUBLIÉ (ex. Club Leones 15/07) restait compté malgré le ban (user 2026-08-07).
    if sport == "foot":
        try:
            from app.settle_analyst import code_from_pick as _cfpb
            _rcb = _cfpb(bets[ri].get("sel", ""), sport, m.get("home", ""), m.get("away", ""))
            if _rcb and market_of(_rcb) in _HARD_BAN_FOOT:
                return None
        except Exception:
            pass
    results = {_norm_sel(b.get("sel", "")): b.get("result") for b in (m.get("bets") or [])}
    b = bets[ri]
    # `cprob` = confiance CALIBRÉE du pari retenu (comme le tableau des paris) -> l'affichage compact
    # (bande verdict) montre la MÊME confiance que le détail déplié, pas la proba brute (cohérence carte).
    try:
        from app.settle_analyst import code_from_pick as _cfp
        _rc = _cfp(b.get("sel", ""), sport, m.get("home", ""), m.get("away", ""))
        _cp = _cool_conf(calibrated_conf(b.get("prob"), sport, _rc), sport, _rc, m.get("streaks"))
    except Exception:
        _cp = b.get("prob")
    return {"idx": ri, "sel": b.get("sel", ""), "prob": b.get("prob"), "cprob": _cp,
            "cote": b.get("cote"), "result": results.get(_norm_sel(b.get("sel", "")))}


def published_bet(sport: str, match_id) -> dict | None:
    """Le pari PUBLIÉ aux abonnés, FIGÉ (demande user 2026-07-14) : un pari déjà conseillé (Telegram + site)
    n'est JAMAIS retiré ni re-prixé après un rescan qui ferait chuter sa value -> l'abonné qui a parié le voit
    TOUJOURS, au PRIX CONSEILLÉ. Renvoie {sel, prob, cprob, cote(=prix conseillé), published_cote,
    market_cote, result} ou None si le match n'a pas été publié. `market_cote` = prix du marché MAINTENANT
    (pour la mention transparente « la cote a bougé depuis le conseil »)."""
    m = meta(sport, match_id) or {}
    pb = m.get("published_bet") if isinstance(m.get("published_bet"), dict) else None
    if not pb or not pb.get("sel"):
        # pas encore gelé -> on gèle « à la volée » SEULEMENT si le match a été publié (get_prono)
        try:
            from app import notify
            if not notify.get_prono(str(match_id)):
                return None
        except Exception:
            return None
        rb = retained_bet(sport, match_id, for_history=True)
        if not rb:
            return None
        pb = {"sel": rb["sel"], "cote": rb["cote"], "prob": rb["prob"]}
    results = {_norm_sel(b.get("sel", "")): b.get("result") for b in (m.get("bets") or [])}
    _cur = next((b for b in bets_of(sport, match_id)
                 if _norm_sel(b.get("sel", "")) == _norm_sel(pb.get("sel", ""))), None)
    market_cote = _cur.get("cote") if _cur else None
    try:
        from app.settle_analyst import code_from_pick as _cfp
        _cp = calibrated_conf(pb.get("prob"), sport, _cfp(pb.get("sel", ""), sport,
                                                          m.get("home", ""), m.get("away", "")))
    except Exception:
        _cp = pb.get("prob")
    return {"idx": 0, "sel": pb.get("sel"), "prob": pb.get("prob"), "cprob": _cp,
            "cote": pb.get("cote"), "published_cote": pb.get("cote"), "market_cote": market_cote,
            "result": results.get(_norm_sel(pb.get("sel", "")))}


def freeze_published_bet(sport: str, match_id) -> bool:
    """Gèle le pari CONSEILLÉ dans le sidecar AU MOMENT DE LA PUBLICATION (demande user 2026-07-14) :
    {sel, cote, prob} tels qu'envoyés aux abonnés -> ni retiré ni re-prixé par un rescan ultérieur (l'abonné
    a parié à ce prix). Idempotent (ne réécrit JAMAIS un gel existant). Renvoie True si un gel a été posé."""
    p = os.path.join(DIR, f"{sport}_{match_id}.json")
    if not os.path.exists(p):
        return False
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    if isinstance(d.get("published_bet"), dict) and d["published_bet"].get("sel"):
        return False                                     # déjà gelé -> jamais réécrit
    rb = retained_bet(sport, match_id)                   # le pari retenu ACTUEL = celui qu'on publie
    if not rb or not rb.get("sel"):
        return False
    d["published_bet"] = {"sel": rb["sel"], "cote": rb.get("cote"), "prob": rb.get("prob"),
                          "ts": datetime.now(timezone.utc).isoformat()}
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except OSError:
        return False
    return True


# ===== SPLIT CONFIANCE / VALUE (user 2026-08-09) =====
# But : maximiser le taux du chiffre PHARE. La CONFIANCE = les picks à HAUTE confiance CALIBRÉE (taux mesuré
# ~92-95 %) ; la VALUE = les picks RETENUS (+EV) SOUS ce seuil (rentables — +19/+28 % ROI — mais plus variables).
# ⚠️ Les DEUX tiers restent JOUÉS et comptés au ROI/calibration : RIEN n'est retiré ni perdu. C'est un pur
# CLASSEMENT D'AFFICHAGE, il n'affecte JAMAIS la rétention ni le ROI ni la calibration.
# RÉVERSIBLE d'un seul flag : `TIER_SPLIT_ON = False` -> tout redevient « Confiance » (état EXACT d'avant).
# Seuil TUNABLE : `CONFIANCE_MIN_CONF` (mesuré : 65-70 %≈83-88 %, 80 %+≈96-100 % ; 72 % ≈ ~91-92 %).
TIER_SPLIT_ON = True
CONFIANCE_MIN_CONF = 75.0     # seuil de confiance CALIBRÉE (user 2026-08-13 : 75 % avec le sharp mondial restauré)

# CONFIANCE = SEULEMENT les marchés PROUVÉS fiables à haut taux de réussite (user 2026-08-14). En plus du
# seuil ≥75 % sur LE PARI de CE match, le MARCHÉ doit AVOIR un taux de réussite mesuré ≥ seuil (fantômes
# inclus). Liste NON figée : CALCULÉE en continu (_confiance_markets, plus bas) -> un marché sort/rentre TOUT
# SEUL selon ses stats (ex. Vainqueur sous 75 % -> écarté ; il revient quand ses fantômes le font remonter).
# Sinon le pari reste JOUÉ mais étiqueté VALUE (hors chiffre phare). Réversible : CONFIANCE_MARKET_GATE_ON=False.
CONFIANCE_MARKET_GATE_ON = True
_CONFIANCE_MARKET_MIN_WR = 75     # taux de réussite MESURÉ (%) mini pour qu'un marché soit Confiance-éligible
_CONFIANCE_MARKET_MIN_N = 25      # échantillon mini (fantômes inclus) — sinon « pas prouvé » -> pas Confiance

# PROVISOIRES RETIRÉS (user 2026-08-11 : « je ne veux plus de provisoires ; ces matchs doivent être ignorés »).
# Un match ABSTENU (analysé, aucun pari de value retenu) n'est PLUS affiché ni suivi en « provisoire ». Ses
# prédictions continuent de nourrir la CALIBRATION via ses FANTÔMES (`d["shadow"]`, indépendants — inchangés).
# RÉVERSIBLE : PROVISOIRES_ON = True -> réaffiche/re-suit les provisoires (état d'avant). Purement affichage/
# suivi info-seule : n'a JAMAIS touché le ROI/les stats (provisoire = hors ROI).
PROVISOIRES_ON = False


def bet_tier(cprob, cote=None, market=None, sport=None) -> str:
    """Tier d'AFFICHAGE d'un pari retenu : « confiance » ou « value ». Off (TIER_SPLIT_ON=False) -> toujours
    « confiance » (état d'avant). DEUX conditions pour « confiance » (user 2026-08-14) :
      1) confiance CALIBRÉE de CE pari (sur CE match) ≥ CONFIANCE_MIN_CONF (75 %) — pas la moyenne du marché ;
      2) le MARCHÉ (`market`) fait partie des marchés prouvés fiables (`_CONFIANCE_MARKETS`).
    Sinon -> VALUE (le pari reste joué, mais hors chiffre phare). `market`=None (indéterminé) -> on ne
    déclasse PAS sur ce seul motif (évite de perdre un vrai Confiance sur un code non résolu). N'affecte PAS
    rétention/ROI/calibration — pur filtre d'affichage/compteur."""
    if not TIER_SPLIT_ON:
        return "confiance"
    try:
        if cprob is None or float(cprob) < CONFIANCE_MIN_CONF:
            return "value"
    except (TypeError, ValueError):
        return "value"
    if CONFIANCE_MARKET_GATE_ON and market and sport and market not in _confiance_markets(sport):
        return "value"
    return "confiance"


_MONT_MIDS_CACHE = {"sig": None, "set": frozenset()}


def _montante_mids() -> frozenset:
    """Set (caché) des ids de match MONTANTE -> tier FORCÉ « confiance » (le pari SÛR du jour, demande user
    2026-08-11 : la montante est toujours considérée confiance). Rafraîchi sur le mtime du suivi montante
    (tier_of est sur un chemin chaud -> on évite de relire le JSON à chaque appel)."""
    try:
        from app import montante as _mt
        sig = os.path.getmtime(_mt.TRACK) if os.path.exists(_mt.TRACK) else 0
        if sig != _MONT_MIDS_CACHE["sig"]:
            _MONT_MIDS_CACHE["set"] = frozenset(_mt.montante_mids())
            _MONT_MIDS_CACHE["sig"] = sig
    except Exception:
        pass
    return _MONT_MIDS_CACHE["set"]


def _bet_market(d, bet, sport):
    """Label de marché d'un pari, pour le tier. `bet` = stat_bet (souvent `sel` seul, pas de `code`) ou rb
    (porte `code`). None si indéterminé -> bet_tier ne déclasse PAS sur ce seul motif."""
    code = (bet or {}).get("code")
    if not code and (bet or {}).get("sel"):
        try:
            from app.settle_analyst import code_from_pick
            code = code_from_pick(bet["sel"], sport, (d or {}).get("home", ""), (d or {}).get("away", ""))
        except Exception:
            code = None
    return market_of(code) if code else None


def bet_tier_for(sport, mid) -> str:
    """Tier (« confiance »/« value ») d'un match par son id. MONOTONE : on lit d'abord la confiance calibrée
    FIGÉE au règlement (`stat_bet.cprob`, immunisée à la dérive de calibration) ; repli sur le calcul live
    (pari actif, ou pas encore figé). SOURCE UNIQUE -> affichage ET compteurs cohérents.
    La MONTANTE est sa PROPRE catégorie « montante » (user 2026-08-12) -> jamais comptée dans le taux
    Confiance (ni Value) : elle ne doit pas polluer le phare (elle a sa page/ badge dédiés)."""
    try:
        if MONTANTE_ROI_ON and sport == "foot" and str(mid) in _montante_mids():
            return "montante"    # montante OFF -> le match revient à son tier réel (cohérent avec tier_of)
        d = meta(sport, str(mid)) or {}
        # PARI DE CONFIANCE/VALUE MÉCANIQUE non encore figé (à venir) : tier direct depuis le flag posé au scan,
        # cohérent avec tier_of (sinon un pari value à venir pourrait retomber « confiance » via bet_tier).
        if isinstance(d, dict):
            if isinstance(d.get("confidence_bet"), dict) and d["confidence_bet"].get("code") \
                    and not isinstance(d.get("stat_bet"), dict):
                return "confiance"
            if isinstance(d.get("value_bet"), dict) and d["value_bet"].get("code") \
                    and not isinstance(d.get("stat_bet"), dict):
                return "value"
        sb = d.get("stat_bet")
        if isinstance(sb, dict) and sb.get("cprob") is not None:
            return bet_tier(sb.get("cprob"), sb.get("cote"), _bet_market(d, sb, sport), sport)
        rb = retained_bet(sport, mid, for_history=True) or {}
        return bet_tier(rb.get("cprob"), rb.get("cote"), _bet_market(d, rb, sport), sport)
    except Exception:
        return "confiance"


def tier_of(d, rb=None) -> str:
    """Tier d'une fiche (dict déjà chargé), en préférant la confiance FIGÉE (`stat_bet.cprob`, monotone) sur
    la calibrée live `rb.cprob`. `rb` = retained_bet déjà calculé par l'appelant (évite un 2e calcul).
    La MONTANTE est sa PROPRE catégorie « montante » (user 2026-08-12) -> hors Confiance/Value."""
    if MONTANTE_ROI_ON and isinstance(d, dict) and str(d.get("id") or "") in _montante_mids():
        return "montante"    # montante OFF (MONTANTE_ROI_ON=False) -> le match revient à son tier réel
    # CONFIANCE = STRICTEMENT le profil 93% mécanique (backtest 2026-08-29) : un match porte le tier
    # « confiance » SSI il a un pari de confiance — soit `confidence_bet` (à venir), soit un `stat_bet`
    # figé marqué `kind="confidence"` (réglé). L'ancien split par `bet_tier` est SUPERSEDED : tout le reste
    # (anciens picks de Claude) = « value » (placeholder jusqu'au sélecteur value dédié). Cohérent avec le
    # track record affiché = la méthode réellement déployée.
    sb = d.get("stat_bet") if isinstance(d, dict) else None
    if isinstance(d, dict) and isinstance(d.get("confidence_bet"), dict) and d["confidence_bet"].get("code"):
        return "confiance"
    if isinstance(sb, dict) and sb.get("kind") == "confidence":
        return "confiance"
    return "value"


def stat_bet(d: dict) -> dict | None:
    """Pari du match FIGÉ pour les stats (courbe / ROI / réussite). Une fois qu'un pari est COMPTÉ, il
    est gelé dans `d["stat_bet"]` et le RESTE à vie -> le compteur ne fait plus que MONTER (fini le
    « nombre qui rebaisse » quand la calibration recalcule). NE PERD RIEN : rien de compté n'est jamais
    retiré. N'affecte PAS la calibration (qui garde toutes les prédictions, séparément). Repli sur le
    calcul live for_history tant qu'un pari n'a pas encore été gelé (il le sera au règlement / backfill)."""
    sb = d.get("stat_bet")
    if isinstance(sb, dict):
        return sb                              # figé « compté » -> immuable
    return retained_bet(d.get("sport"), d.get("id"), for_history=True)


def played_result(d: dict) -> str | None:
    """Verdict (won/lost/push/void) du pari RÉELLEMENT JOUÉ : le combiné s'il y en a un, sinon la couche
    mécanique FIGÉE (`stat_bet`). ⚠️ PAS `result.pick_result`, qui note le 1er pari du tableau .md (pick
    BRUT de Claude) et DIVERGE du pari mécanique joué depuis la refonte 2026-08-29 : le scan pick p.ex.
    « Moins de 3.5 » mais le pari CONFIANCE joué est « DC 1X » (venu d'un fantôme). Lire pick_result faisait
    afficher « perdu » sur un pari GAGNÉ (bug user 2026-08-31, Bahia-BA – Internacional 3-2). SOURCE UNIQUE
    du verdict d'affichage (badge headline, chip « Terminés », carte résultat Telegram/site). Repli
    pick_result tant qu'aucun pari mécanique n'est figé (ex. anciens sidecars pré-refonte)."""
    d = d or {}
    combo = d.get("combo") or {}
    if combo.get("legs"):
        return combo.get("result")
    sb = stat_bet(d)
    if isinstance(sb, dict) and sb.get("result") is not None:
        return sb.get("result")
    return (d.get("result") or {}).get("pick_result")


def provisional_shown(sport, sel, cote, prob, home="", away="", fid=None) -> bool:
    """Un pari PROVISOIRE (indicatif) est-il DIGNE d'être affiché/suivi ? (demande user 2026-07-17, affiné
    2026-07-20) Un provisoire est un PICK indicatif : il doit d'abord être un pari qu'on FAVORISE. On ne
    garde JAMAIS un pick « FAIBLE » (confiance calibrée < 55 %, pastille rouge — on l'estime plus probable de
    perdre) MÊME avec de la value : proposer un pari qu'on juge perdant n'a pas de sens (demande user 2026-07-20,
    ex. Dallas Wings gagne @2.35, 47 % « faible » +10 % value). Au-dessus du plancher : gardé s'il a de la
    VALUE (EV>0) OU une confiance calibrée ≥ 60 %. Sous 55 % ou (55-60 % sans value) -> écarté (bruit).
    Confiance CALIBRÉE = celle affichée partout (cohérence). Purement affichage/suivi — jamais ROI/stats/
    calibration. SOURCE UNIQUE : appelée par l'affichage (web._programme_items) ET le suivi
    (provisional.reconcile_with_programme) -> jamais d'écart liste/compteur."""
    if not sel:
        return False
    # SPORTS EN ARRIÈRE-PLAN (tennis/basket) : leurs PROVISOIRES sont désormais SUIVIS en SIMULATION (demande
    # user 2026-07-26 : « sélectionner simple/combiné/provisoires pour basket/tennis comme avant mais pour la
    # simulation »). Ils restent HORS de la page des paris (le filtre `background_sports` de `web._programme_items`
    # les écarte de Pronos en amont) et HORS ROI, mais alimentent l'onglet « Provisoires » de leur cadre sport
    # simulé (per-sport via `_prov_sport_graph`). On ne coupe donc PLUS ici — même règle qualité pour tous.
    try:
        c = float(cote)
    except (TypeError, ValueError):
        c = None
    cp = prob
    _code = ""
    try:
        from app.settle_analyst import code_from_pick
        _code = code_from_pick(sel, sport, home, away)
        cp = calibrated_conf(prob, sport, _code)
        # REFROIDISSEMENT OVER-total (audit 2026-07-23) : sans lui, le pari refroidi en abstention côté
        # sélection re-sortait en PROVISOIRE doré à sa confiance NON refroidie (cas Minnesota 69 % vs 58 %).
        # `fid` = id SIDECAR (fourni par les DEUX appelants via day_programme.provisional.fid) -> streaks du
        # bon sidecar, SOURCE UNIQUE préservée (jamais d'écart affichage/suivi).
        if fid:
            cp = _cool_conf(cp, sport, _code, (meta(sport, fid) or {}).get("streaks"))
    except Exception:
        cp = prob
    # PROVISOIRES = MARCHÉS RÉSULTAT SEULEMENT (user 2026-08-07) : un provisoire TOTAL (Under/Over buts du
    # match) = « le plus probable » sur un total SANS value = pile ou face -> perd (mesuré : Under 65 %/−16 %,
    # Over 60 %/−21 %). Les provisoires RÉSULTAT tiennent (1X2 83 %/+10 %, DC 81 %/+3 %). On n'affiche/suit
    # donc QUE les provisoires résultat -> ROI provisoire positif. Purement affichage/suivi (hors ROI réel).
    if sport == "foot" and (_code or "").upper().startswith(("UNDER ", "OVER ")):
        return False
    # BANS DURS (BTTS/Corners) : jamais en PROVISOIRE non plus (un marché banni ne doit surgir NULLE PART,
    # même en indicatif hors-ROI) — user 2026-08-07, même logique que la sélection/rétention.
    if sport == "foot" and _code and market_of(_code) in _HARD_BAN_FOOT:
        return False
    if cp is None:
        return c is not None                   # sans confiance calculable : garder (repli prudent) si coté
    if cp < 55:                                # PLANCHER : un pick « faible » (rouge) n'est jamais proposé,
        return False                           #           même avec value (demande user 2026-07-20)
    if c and (cp / 100.0 * c - 1) > 0:         # VALUE (EV>0) -> gardé
        return True
    return cp >= 60                            # sinon : gardé seulement si confiance calibrée ≥ 60 %


def card_summary(sport: str, match_id) -> dict:
    """Résumé COMPACT d'un match pour la ligne repliée (carte compacte) : nb de paris, meilleure
    confiance, s'il y a un pari ✅ À JOUER (même règle que la simulation : ≥65 %, EV≥+3 %, réglable),
    et le résultat réglé du pari joué (terminés). {} si pas d'analyse."""
    m0 = meta(sport, match_id) or {}
    combo = m0.get("combo")
    if combo and combo.get("legs"):
        # COMBINÉ (CdM) : on liste DEUX paris distincts dans le résumé compact -> le(s) pari(s) SIMPLE(s)
        # « le plus sûr » PUIS le combiné (demande user : le simple ne doit plus être masqué). Le résultat
        # HEADLINE de la carte (play_result/badge) reste celui du COMBINÉ (le pari phare du match).
        res = combo.get("result")
        sel = f"Combiné ({len(combo['legs'])} jambes)"
        c_cote = combo.get("real_odds") or combo.get("total")   # VRAIE cote Unibet si dispo
        rb = retained_bet(sport, match_id)   # simple AFFICHÉ seulement s'il aurait été RETENU (sinon combiné seul)
        bet_rows = []
        if rb:
            bet_rows.append({"sel": rb["sel"], "result": rb["result"], "cote": rb.get("cote")})
        bet_rows.append({"sel": sel, "result": res, "cote": c_cote})
        return {"n": len(bet_rows), "best_conf": None, "comp": m0.get("comp"), "circuit": m0.get("circuit"),
                "play": res is None, "ev": None, "reco_idx": None,
                "won": 1 if res == "won" else 0, "lost": 1 if res == "lost" else 0,
                "settled": 1 if res in ("won", "lost", "push") else 0,
                "play_result": res, "bets": bet_rows, "is_combo": True}
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
        ex_sports, _ = auto_exclusions()
        ex_markets = excluded_markets(sport)          # marchés écartés PROPRES À CE SPORT (per-sport)
        if sport in ex_sports:
            reco = {"verdict": "skip", "idx": None, "ev": None}
        else:
            ok, cprobs, codes = set(), [], []
            for i, b in enumerate(bets):
                code = code_from_pick(b.get("sel", ""), sport, m.get("home", ""), m.get("away", ""))
                codes.append(code)
                cprobs.append(_cool_conf(calibrated_conf(b.get("prob"), sport, code), sport, code,
                                         m.get("streaks")))   # confiance recalibrée + refroidissement OVER-total
                if code and market_of(code) not in ex_markets:
                    ok.add(i)
            reco = _recommend(bets, ok, cprobs, codes)
    except Exception:                                    # règlement indispo -> EV brut sans filtre
        reco = _recommend(bets)
    out["play"] = reco.get("verdict") == "play" and reco.get("idx") is not None
    out["ev"] = reco.get("ev")
    out["reco_idx"] = reco.get("idx")
    # COHÉRENCE TELEGRAM = SITE (gel des pronos publiés) : si un prono a DÉJÀ été PUBLIÉ aux abonnés
    # mais que la CALIBRATION a DÉRIVÉ depuis (recalibrage sous le seuil) au point que le moteur
    # abstiendrait AUJOURD'HUI, on GÈLE quand même le pari publié -> le site affiche le MÊME pari que la
    # carte Telegram reçue (sinon « pas de pari conseillé » alors que l'abonné a le pick). N'affecte QUE
    # l'affichage : les stats (stat_bet figé) et la calibration (toutes prédictions) restent intactes.
    if not out["play"]:
        _sb = m.get("stat_bet")           # ancre ROBUSTE : pari déjà COMPTÉ (figé, survit reset+dérive)…
        _anchor = _sb.get("sel") if (isinstance(_sb, dict) and _sb.get("sel")) else None
        if not _anchor:                   # …sinon prono PUBLIÉ (get_prono, pour les à-venir pas encore réglés)
            try:
                from app import notify as _notify
                if _notify.get_prono(str(match_id)):
                    _anchor = re.sub(r"\s*@.*$", "", str(m.get("pick") or ""))
            except Exception:
                _anchor = None
        if _anchor:
            _pk = _norm_sel(_anchor)
            # Match EXACT du pari ancré UNIQUEMENT (jamais un pari « deviné » par max-prob : sinon on
            # afficherait comme « à jouer » une sélection jamais publiée, souvent un favori sans value
            # ou un marché exclu — bug audit). Si le pari ancré ne matche aucune ligne, on n'affiche rien.
            _idx = next((i for i, b in enumerate(bets) if _pk and _norm_sel(b.get("sel", "")) == _pk), None)
            if _idx is not None:
                out["play"], out["reco_idx"] = True, _idx
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
    out["bets"] = [{"sel": b.get("sel", ""), "result": results.get(_norm_sel(b.get("sel", ""))),
                    "cote": b.get("cote")}
                   for b in bets]
    return out


def to_html(md: str, skip_verdict: bool = False, card_details: bool = False) -> str:
    """Markdown analyste -> HTML : structuré si gabarit reconnu, sinon rendu générique.
    `skip_verdict` : voir _structured (masque « 🎯 Pourquoi ce pari » sur les abstentions).
    `card_details` : voir _structured (dépli de CARTE épuré — verdict masqué + preuve repliée)."""
    md = _strip(md)
    structured = _structured(md, skip_verdict=skip_verdict, card_details=card_details)
    if structured is not None:
        return structured
    return '<div class="da">' + _render_blocks(md) + "</div>"


_RESULT_CHIP = {"won": "✅ Réussi", "lost": "❌ Perdu", "push": "➖ Remboursé", "void": "➖ Remboursé"}


def result_chip(d: dict) -> tuple[str, str]:
    """(badge court ✅/❌/➖, score) du pari réglé pour les cartes « Terminés ». ('', '') si non réglé.
    Match CdM : le pari AFFICHÉ est le COMBINÉ -> le badge suit SON résultat, jamais le pari simple
    (qui peut diverger : combiné perdu mais BTTS Non gagné = « Pari réussi » trompeur, ex. 0-0)."""
    res = (d or {}).get("result") or {}
    # VERDICT = pari JOUÉ (combiné ou couche mécanique figée), pas `pick_result` (pick brut divergent).
    outcome = played_result(d)
    return (_RESULT_CHIP.get(outcome, ""), res.get("score") or "")


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
    # TIRS AU BUT (Supercoupe / finales de coupe) : score de BUTS = régulation ; les t.a.b. voyagent à part
    # (`pens`) pour l'affichage d'une colonne « T.A.B. » dédiée (demande user 2026-07-31). None si pas de pénos.
    ph, pa = raw.get("pens_home"), raw.get("pens_away")
    pens = (ph, pa) if isinstance(ph, int) and isinstance(pa, int) else None
    return {"score": (f"{h}-{a}" if h is not None and a is not None else ""), "periods": None, "pens": pens}


_BET_KEYS = ("pari1", "pari2", "pari3")   # positions de pari pour les stats (= ordre d'affichage)

# Caches d'AGRÉGATS invalidés par la signature du dossier (cf. _dir_sig) : home/stats/reco refont
# ces agrégations à chaque rendu alors que les sidecars ne changent qu'au scan/règlement.
_STATS_CACHE: dict = {}    # "full" -> (sig, stats_full())
_CALIB_RES_CACHE: dict = {}  # min_conf -> (sig, calibration()) — uniquement pour since_days=None
_PERF_CACHE: dict = {}     # "v" -> (sig, perf_breakdown()) — ROI par cote/marché/confiance

# SNAPSHOT DISQUE des stats agrégées (étape 1 scalabilité, demande user 2026-07-28) : `stats_full()` à FROID
# lit les ~650 sidecars (~2,5 s) et gèle le process unique. On PERSISTE le résultat (keyé par un HASH de la
# signature du dossier) -> après un restart / sur un autre worker, la 1re requête LIT le snapshot (O(1)) au
# lieu de tout recalculer. 100 % LECTURE DÉRIVÉE : jamais la source de vérité (sidecars/.md/stat_bet/
# calibration intacts). Repli automatique = recalcul en direct si snapshot absent/périmé/illisible -> JAMAIS
# de corruption. Rafraîchi en tâche de fond après le scan/règlement (warm_stats_snapshot).
_STATS_SNAP_PATH = os.path.join(_ROOT, "data", "_stats_snapshot.json")


def _sig_hash(sig) -> str:
    import hashlib
    return hashlib.md5(repr(sig).encode("utf-8")).hexdigest()


def _load_stats_snapshot() -> dict | None:
    try:
        with open(_STATS_SNAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_stats_snapshot(sig_hash: str, data: dict) -> None:
    try:
        tmp = _STATS_SNAP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sig": sig_hash, "data": data}, f, ensure_ascii=False)
        os.replace(tmp, _STATS_SNAP_PATH)          # écriture atomique (jamais de fichier à moitié écrit)
    except OSError:
        pass


def warm_stats_snapshot() -> bool:
    """Recalcule + persiste le snapshot des stats SI les données ont changé (appelé APRÈS le scan/règlement,
    en tâche de fond) -> les requêtes utilisateur lisent ensuite le snapshot en O(1), jamais le chemin froid
    2,5 s qui gèle le process. Best-effort ; ne casse jamais. True si (re)calculé. LECTURE SEULE (source
    intacte)."""
    try:
        _sh = _sig_hash(_dir_sig())
        _snap = _load_stats_snapshot()
        if _snap and _snap.get("sig") == _sh:
            return False                           # snapshot déjà à jour -> rien à faire
        stats_full()                               # recalcule + persiste (via le chemin de cache ci-dessous)
        return True
    except Exception:
        return False


# JALONS du modèle : dates (UTC) où la LOGIQUE de sélection a changé -> repères verticaux sur les
# courbes d'équité (pour corréler une inflexion de ROI avec un changement). Garder COURT (s'affiche
# sur un petit graphe) et N'AJOUTER qu'un vrai changement de POLITIQUE de paris (pas l'UI).
MODEL_MILESTONES = [   # (date, libellé court, explication 1 ligne, portée, sport) — SEULS les repères DÉCISIFS.
    #  Ces repères tracent la MÉTHODOLOGIE d'analyse et de SÉLECTION des pronos (ce qui change le ROI) —
    #  PAS la fiabilité/technique/UI. But : voir, PAR SPORT, quand la méthode se stabilise (= optimale).
    #  portée ∈ {"simple","combo","both"} (sur quel graphe) · sport ∈ {"all","foot","tennis","basket"}.
    ("2026-06-09", "Seuil ≥65 %", "Aucun pari n'est retenu sous 65 % de confiance honnête.", "simple", "all"),
    ("2026-06-16", "1 pari/match", "Le modèle ne retient qu'un seul pari par match, le plus probable, validé par trois agents.", "simple", "all"),
    ("2026-06-19", "Corners bannis", "Les corners, le marché le plus perdant au foot, sont exclus de tous les paris (simple et combiné).", "both", "foot"),
    ("2026-06-26", "Combinés calibrés", "Jambes de combiné recalibrées comme les simples ; les marchés perdants (Total, Sets) s'écartent automatiquement.", "combo", "all"),
    ("2026-07-05", "Combiné = cote réelle corrélée", "La probabilité d'un combiné est ajustée par la vraie cote Bet Builder (corrélation du marché) au lieu du produit naïf des probabilités : un combiné anti-corrélé est refusé, une domination corrélée est valorisée.", "combo", "all"),
    ("2026-07-06", "Combiné = pari désigné", "Le combiné proposé est exactement celui désigné par l'analyste, jamais un combiné de remplacement ; s'il n'est pas combinable, on s'abstient plutôt que de forcer.", "combo", "all"),
]
# Icônes/noms de sport partagés (repères auto, journaux). "combo" = props joueur en jambe de combiné.
_SPORT_ICON = {"foot": "⚽", "tennis": "🎾", "basket": "🏀", "combo": "🎲"}
_SPORT_NOM = {"foot": "Foot", "tennis": "Tennis", "basket": "Basket", "combo": "Combiné"}
# Les combinés ne comptent dans le palmarès qu'à partir de la date de DÉCISION (NON rétroactif) :
# les combinés antérieurs (placés quand ils ne comptaient pas) ne polluent pas le suivi.
# ⚠️ Cette bascule pilote AUSSI l'AFFICHAGE des simples (list_for : un match à combiné antérieur
# est masqué) — NE PAS la déplacer pour un besoin de stats combinés, sinon on cache des simples.
_COMBO_COUNT_FROM = "2026-06-18"
# Bascule DÉDIÉE aux STATS des COMBINÉS (demande user 2026-08-11) : seul le COMBINÉ DU JOUR (double
# chance), mis en place le 29/07, compte dans le bilan combinés. Les combinés multi-marchés d'avant
# (l'ancien système, 18/06→19/07) sont EXCLUS du bilan combinés mais RESTENT dans la calibration et
# les stats fantômes (leurs jambes sont des prédictions de fiche, calibrées indépendamment).
_COMBO_STATS_FROM = "2026-07-29"
# COMBINÉS HORS-RÈGLE (user 2026-08-20) : du 08/08 au 20/08, le combiné du jour a été bricolé avec des
# logiques EXPÉRIMENTALES ABANDONNÉES (4 jambes, multi-marchés, couple « Sûr + Cote 2 », analyste quant).
# On restaure la règle MÉCANIQUE du 29/07→07/08 (la double chance la PLUS SÛRE par match -> pick_combo
# ~1,95 ; taux excellent : 6W-1L sur cette fenêtre). Ces jours hors-règle sont RETIRÉS du BILAN combiné
# (rows/curve/ROI combiné + courbe par sport) mais GARDÉS en calibration : `calibration()` lit les shadows
# et paris, PAS `combo_stats()`, donc rien n'est retiré du calibrage. Fenêtre inclusive [début, fin].
_COMBO_RULE_VOID = ("2026-08-08", "2026-08-20")


def _combo_rule_void(day: str) -> bool:
    """True si le combiné de ce jour (ISO/AAAA-MM-JJ…) relève de la période hors-règle -> hors bilan combiné."""
    d10 = (day or "")[:10]
    return bool(d10) and _COMBO_RULE_VOID[0] <= d10 <= _COMBO_RULE_VOID[1]


# COMBINÉS HORS ROI — bascule TEMPORAIRE & RÉVERSIBLE (user 2026-08-15/16 : « afficher l'onglet Combiné
# mais ne pas le compter au ROI »). L'onglet Combiné + la courbe par sport sont AFFICHÉS (marqués « hors
# ROI »/« non compté au ROI »), MAIS le combiné n'apparaît PAS comme métrique de ROI en tête : quand False,
# le chip « C » du résumé perf est masqué et l'onglet Combiné ne porte PAS de chip ROI. `combo_stats()` reste
# EXACT. N'affecte NI le ROI des simples (all_ev/stat_bet), NI l'invariant monotone, NI la calibration, NI la
# carte combiné du jour. Remettre True pour recompter les combinés comme un ROI en tête.
COMBO_ROI_ON = True  # user 2026-08-31 : combinés du JOUR (variant='') + du SOIR (variant='soir') COMPTÉS AU ROI et affichés dans les stats (jour ET nuit). Overall = Confiance + Value + Combiné. Repasser False = combinés en affichage seul.
MONTANTE_ROI_ON = False  # user 2026-08-29 : montante DÉSACTIVÉE (refonte de la sélection à venir) -> RIEN dans
#                          la catégorie montante ; les matchs montante-mids reviennent à leur tier réel
#                          (confiance/value) via tier_of. Réactiver = True + recréer data/montante_active.flag.
#   (ancien) user 2026-08-20 (nuit) : « montante compte au ROI (période validée) » -> ses paliers AFFICHÉS
#                          (montante.public_steps = hors période d'erreurs 09/08→20/08) entrent dans all_ev en mise plate.


def _agg_bets(events: list) -> dict:
    """Agrège une liste de paris (start, result, odds) -> bloc stats complet : courbe de profit
    cumulé (démarre à 0), won/lost/push/settled, % réussite, profit (u), ROI (%), cote moyenne.
    Mise plate 1 u : gagné +(cote-1), perdu -1, remboursé 0. ROI = profit ÷ total misé.
    `dates` = coup d'envoi de chaque pari, ALIGNÉ sur points[1:] (points[0]=0 avant tout pari) ->
    sert à placer les jalons MODEL_MILESTONES sur la courbe."""
    events = sorted(events, key=lambda x: x[0] or "")
    cum, osum = 0.0, 0.0
    pts, dates, won, lost, push = [0.0], [], 0, 0, 0
    # Réussite : MÊME cadence que la courbe d'équité — 1 point par pari réglé + une ANCRE de tête (comme
    # pts[0]=0.0), les remboursés/push REPORTENT le taux (l'équité les trace à plat aussi). Résultat : les 2
    # courbes partagent l'axe x bout-à-bout -> index i = même pari = même date (demande user 2026-07-27 :
    # graphes alignés, correspondances visibles). Les None de tête (avant le 1er gagné/perdu) sont comblés
    # après la boucle par le 1er taux connu.
    hit_pts, hit_dates = [None], []
    cote_pts = [None]                                    # COTE MOYENNE cumulée (3e graphe, demande user 2026-07-27)
    recent = []                                          # détails par pari (si fournis en 4e élément)
    for _ev in events:
        _start, res, odds = _ev[0], _ev[1], _ev[2]
        _meta = _ev[3] if len(_ev) > 3 else None
        if _meta and res in ("won", "lost", "push"):     # pour le panneau « derniers paris » (au clic)
            recent.append({"start": _start, "result": res, "cote": odds,
                           "name": _meta.get("name"), "sel": _meta.get("sel"),
                           "sport": _meta.get("sport"), "legs": _meta.get("legs")})   # jambes -> historique
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
        # taux de réussite cumulé APRÈS ce pari (un point par pari réglé, comme l'équité ; None tant qu'aucun
        # gagné/perdu -> comblé après la boucle). Report automatique sur push : won/(won+lost) inchangé.
        hit_pts.append(round(100 * won / (won + lost), 1) if (won + lost) else None)
        hit_dates.append(_start or "")
        # cote MOYENNE cumulée (même cadence) -> 3e graphe « Cote moyenne » (demande user 2026-07-27)
        cote_pts.append(round(osum / (won + lost), 2) if (won + lost) else None)
    # Comble les None de tête (ancre + éventuels push avant le 1er gagné/perdu) par le 1er taux connu ->
    # départ plat et longueur = celle de la courbe d'équité. Aucun pari gagné/perdu -> pas de courbe.
    _first_rate = next((h for h in hit_pts if h is not None), None)
    hit_pts = [] if _first_rate is None else [(_first_rate if h is None else h) for h in hit_pts]
    _first_cote = next((c for c in cote_pts if c is not None), None)
    cote_pts = [] if _first_cote is None else [(_first_cote if c is None else c) for c in cote_pts]
    settled, staked = won + lost, won + lost + push
    # Série EN COURS (signée) : nb de gagnés (+) ou perdus (-) consécutifs en fin de période.
    seq = [ev[1] for ev in events if ev[1] in ("won", "lost")]
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
    _all_form = [ev[1] for ev in events]
    form = _all_form[-5:]            # 5 derniers (lignes par sport, compactes)
    form12 = _all_form[-12:]         # 12 derniers (bandeau d'accueil des stats)
    form_run = _all_form[-24:]       # série longue (courbes perf : on affiche le MAX qui tient/ligne)
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
            "form_run": form_run,
            "recent": recent,                            # TOUT l'historique détaillé (W/L + nom + sel + cote) — panneau déplié scrollable (demande user 2026-07-25)
            "hit_points": hit_pts, "hit_dates": hit_dates,   # courbe du taux de réussite cumulé (amélioration dans le temps)
            "cote_points": cote_pts,                         # courbe de la COTE MOYENNE cumulée (3e graphe)
            "max_dd": round(dd, 2),
            "dd_pct": (round(100 * dd / staked, 1) if staked else None)}



def pending_roi_bets(combo: bool = False, sport: str | None = None) -> list:
    """Paris comptés (ROI ou SIMULÉ) mais PAS ENCORE réglés (matchs À VENIR / EN COURS) — même format que la
    clé `recent` d'_agg_bets ({start, result, cote, name, sel, sport}) pour les afficher SOUS la courbe, à
    côté des paris réglés (demande user 2026-07-14). `result="pending"`. `combo=True` -> combiné du jour en
    cours ; sinon les SIMPLES retenus/publiés à venir. Le plus PROCHE en tête.
    `sport` : CIBLE un sport précis (ex. cadres de SIMULATION tennis/basket — demande user 2026-07-26) et
    CONTOURNE alors l'exclusion arrière-plan (on veut les voir dans LEUR cadre simulé, façon football).
    None (défaut ROI officiel) -> exclut les sports en arrière-plan."""
    out = []
    _bg = background_sports()
    if combo:
        try:
            from app import combo_daily
            # DEUX combinés/jour (user 2026-08-31) : le combiné EN ATTENTE doit inclure le SOIR (variant="soir")
            # en plus du JOUR (variant=""). Avant, `entries(sport=_sp)` ne lisait QUE le jour -> le combiné du
            # soir en cours n'apparaissait JAMAIS dans l'onglet Combinés des Stats tant qu'il n'était pas réglé.
            for _sp in ((sport,) if sport else ("foot",)):
                for _cvar, _clbl in (("", "Combiné du jour"), ("soir", "Combiné du soir")):
                    for cb in combo_daily.entries(sport=_sp, variant=_cvar):
                        if cb.get("result") in ("won", "lost", "void"):
                            continue
                        if _combo_rule_void(cb.get("date")):     # combiné hors-règle (08/08→20/08) -> ni bilan ni historique
                            continue
                        out.append({"start": (cb.get("date") or "") + "T00:00:00+00:00", "result": "pending",
                                    "cote": cb.get("cote"), "name": f"{_clbl} ({len(cb.get('legs') or [])} j.)",
                                    "sel": _clbl.lower(), "sport": _sp, "tier": "confiance",
                                    "legs": combo_daily._leg_summ(cb)})   # jambes -> historique
        except Exception:
            pass
    else:
        for p in glob.glob(os.path.join(DIR, "*.json")):
            d = _meta_load(p)
            if not d:
                continue
            # On garde À-VENIR / EN-COURS ET les matchs FINIS mais PAS ENCORE RÉGLÉS (matchs DÉCALÉS :
            # status_of se fie à l'heure PRÉVUE, peu fiable — user 2026-08-02) -> le pari simple reste
            # visible en ⏳ « en attente » dans l'historique Stats jusqu'à son règlement. On n'exclut que
            # le RÉGLÉ (il rejoint alors les W/L). Ne compte JAMAIS au ROI tant que non réglé (liste seule).
            if status_of(d) not in ("notstarted", "inprogress") and is_settled(d):
                continue
            if (d.get("combo") or {}).get("legs"):
                continue                                     # combiné same-match -> pas un simple
            _sp, mid = d.get("sport"), str(d.get("id"))
            if sport is not None:
                if _sp != sport:                             # sport CIBLÉ (simulation) -> pas d'exclusion bg
                    continue
            elif _sp in _bg:                                 # défaut ROI officiel : exclut tennis/basket (pause)
                continue                                     # (fix 2026-07-25 : ne pas polluer l'historique foot)
            # FOOT (user 2026-08-29) : PAS de repli `published_bet` -> un pari foot en attente n'est compté que
            # s'il est RETENU par la logique mécanique actuelle (retained_bet). Sinon un `published_bet` HÉRITÉ
            # (ancien pick / incident, ex. Bournemouth « Plus de 1.5 » posté avant le fix) remonterait en
            # « value » fantôme. Le repli reste pour les autres sports (aucun aujourd'hui).
            rb = retained_bet(_sp, mid) if _sp == "foot" else (retained_bet(_sp, mid) or published_bet(_sp, mid))
            if rb and rb.get("result") not in ("won", "lost", "push"):
                out.append({"start": d.get("start") or "", "result": "pending", "cote": rb.get("cote"),
                            "name": d.get("name"), "sel": rb.get("sel"), "sport": _sp,
                            "tier": tier_of(d, rb)})   # tier -> onglets Confiance/Value des Stats
    out.sort(key=lambda x: x.get("start") or "")            # chronologique (le plus proche en tête à l'affichage)
    return out


def stats_full(since_days: int | None = None, _bypass_snapshot: bool = False) -> dict:
    """Suivi pour l'accueil = LE pari JOUÉ (retenu) de chaque match — celui qui passe le seuil de jeu,
    = le pari par défaut du match. Les matchs où le système s'abstient (aucun pari retenu) ne comptent
    PAS (avant : on comptait le pari même sur les abstentions -> courbe sur-pessimiste). Courbe HONNÊTE
    (les défaites des paris joués restent comptées). Combinés exclus (suivis à part). Niveaux :
    `overall`, `since_change` (nouveau système), `by_sport`. Chaque bloc = `_agg_bets` (courbe + ROI +
    réussite + cote moy. + drawdown). `since_days` : ne garde que les coups d'envoi des N derniers jours."""
    sig = _dir_sig() if since_days is None else None   # cache UNIQUEMENT la vue complète (pas de cutoff)
    _snap_h = None
    if sig is not None and not _bypass_snapshot:
        hit = _STATS_CACHE.get("full")
        if hit and hit[0] == sig:
            return hit[1]
        # SNAPSHOT DISQUE (survit aux restarts + partagé entre workers) : si sa signature correspond, on lit
        # le résultat persisté (rapide) au lieu de recalculer 2,5 s. Repli = calcul en direct ci-dessous.
        _snap_h = _sig_hash(sig)
        _snap = _load_stats_snapshot()
        if isinstance(_snap, dict) and _snap.get("sig") == _snap_h and isinstance(_snap.get("data"), dict):
            _STATS_CACHE["full"] = (sig, _snap["data"])   # repeuple le cache mémoire
            return _snap["data"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)) if since_days else None
    all_ev: list = []         # TOUS les paris réglés depuis le début -> courbe d'équité COMPLÈTE
    since_ev: list = []       # paris du NOUVEAU système (validés 3 agents) -> KPI à suivre
    match_form: list = []     # 1 résultat PAR MATCH (combiné OU pari principal) -> bulles de forme HONNÊTES
    simple_form: list = []    # forme des paris SIMPLES (non-CdM principal + simple RETENU CdM) -> ligne dédiée
    combo_form: list = []     # forme des COMBINÉS (CdM) -> 2e ligne dédiée (demande user, graphe principal)
    by_sport: dict = {}
    n_analysed = 0            # matchs analysés (sidecars dans la fenêtre) -> panneau « volume de données »
    _first = _last = None     # plage de coups d'envoi couverte (période de mesure du volume/calibration)
    # Sports en ARRIÈRE-PLAN (tennis/basket) : leurs paris comptent dans `by_sport` (ROI SIMULÉ, suivi séparé)
    # mais PAS dans `overall`/`since_ev` (headline = sports actifs, foot) — demande user 2026-07-24.
    _bg = background_sports()
    conf_ev: list = []        # paris FOOT du tier CONFIANCE (haute confiance figée) -> stats par tier
    value_ev: list = []       # paris FOOT du tier VALUE (sous le seuil)
    mont_ev: list = []        # paris MONTANTE -> catégorie à part (jamais dans Confiance/Value, user 2026-08-12)
    _roi_mids: set = set()    # mids DÉJÀ comptés dans all_ev (foot) -> garde anti-double-compte du ROI montante
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        if d.get("roi_void"):        # pari EXCLU du ROI/stats/série (correction d'approche, user 2026-08-07)
            continue                 # -> hors bilan affiché ; reste en CALIBRATION (fonction séparée, intacte)
        sport = d.get("sport")
        start = d.get("start") or ""
        if cutoff is not None:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except (ValueError, AttributeError):
                dt = None
            if dt is None or dt < cutoff:
                continue
        n_analysed += 1
        if start:                                 # plage couverte (start = ISO triable lexicalement)
            if _first is None or start < _first:
                _first = start
            if _last is None or start > _last:
                _last = start
        # FORME « 1 par match » (TOUS les matchs, SANS la borne combiné) : un combiné = son résultat
        # GLOBAL, sinon le pari principal (1er) -> 1 bulle par combiné / par match (demande user).
        # Les LIGNES DE FORME (W/L) doivent refléter le MÊME périmètre que la courbe/l'historique OFFICIELS :
        # sports ACTIFS seuls (foot). Sans ce filtre, la ligne W/L incluait tennis/basket alors que la courbe
        # et « Derniers paris » sont foot-only -> W/L ≠ historique (bug capture user 2026-07-24).
        _bgf = sport in _bg
        _c0 = d.get("combo")
        _has_combo = bool(_c0 and _c0.get("legs"))
        _mr = (_c0.get("result") if _has_combo
               else ((d.get("bets") or [{}])[0].get("result")))
        if _mr in ("won", "lost", "push") and not _bgf:
            match_form.append((start, _mr, sport))
        # DEUX lignes de forme distinctes (graphe principal) : SIMPLES vs COMBINÉS.
        # La forme SIMPLES doit refléter EXACTEMENT le pari JOUÉ (retenu) de chaque match — la MÊME base
        # que la courbe/réussite (stat_bet). Avant, hors combiné on prenait bets[0] (1er pari), souvent
        # gagnant alors que le pari retenu perdait -> W/L incohérents avec le ROI (bug vu 2026-07-02).
        if _has_combo and _c0.get("result") in ("won", "lost", "push") and not _bgf:
            combo_form.append((start, _c0["result"], sport))
        _rbf = stat_bet(d)                              # LE pari joué/retenu (figé, compteur monotone)
        if _rbf and _rbf.get("result") in ("won", "lost", "push") and not _bgf:
            simple_form.append((start, _rbf["result"], sport))
        # « Nouveau système » = analyse passée par la VALIDATION 3 agents (signature fiable), pas une
        # simple date de match (un match du 16/06 a pu être généré la veille en ancien système).
        is_new = bool(d.get("validation"))
        # Le pari COMPTÉ dans la courbe / ROI / réussite = LE pari JOUÉ (RETENU) du match : celui qui
        # passe le seuil de jeu (confiance ≥65 % + EV ≥3 % + garde-fous de cote + marché non exclu).
        # C'est LE pari par défaut du match QUAND il est retenu. Si AUCUN pari n'est retenu (le système
        # s'abstient sur ce match), le match N'ENTRE PAS dans la courbe — avant, on comptait le pari
        # même sur les abstentions, ce qui sur-dramatisait la perte (vestige de l'ère « Pari 1/2/3 »).
        # Combiné : exclu de la courbe (suivi à part via combo_form + calibration) ; seul son éventuel
        # SIMPLE retenu compte, et uniquement à partir de la bascule _COMBO_COUNT_FROM (non rétroactif).
        if _has_combo and (d.get("start") or "")[:10] < _COMBO_COUNT_FROM:
            continue                                   # combiné antérieur à la bascule -> match non compté
        rb = stat_bet(d)                               # pari FIGÉ (track record stable, monotone)
        if rb and rb.get("result") in ("won", "lost", "push"):
            ev = (start, rb["result"], rb.get("cote") or rb.get("odds"),
                  {"name": d.get("name"), "sel": rb.get("sel"), "sport": sport})   # détails -> panneau « derniers paris »
            by_sport.setdefault(sport, []).append(ev)  # TOUJOURS (ROI simulé par sport, tennis/basket inclus)
            if sport not in _bg:                       # headline foot : les sports arrière-plan n'y entrent pas
                # SPLIT CONFIANCE / VALUE (user 2026-08-09) : classement par la confiance FIGÉE (tier_of ->
                # monotone). MONTANTE = produit À PART (ladder, hors ROI simples, user 2026-08-19) -> ses picks
                # N'ENTRENT PAS dans l'overall (sinon total ≠ Confiance+Value+Combiné). Confiance+Value comptent.
                _t = tier_of(d, rb)   # confiance / value / montante
                if _t != "montante":
                    all_ev.append(ev)
                    _roi_mids.add(str(d.get("id") or ""))   # -> garde anti-double-compte du ROI montante
                    if is_new:
                        since_ev.append(ev)
                (conf_ev if _t == "confiance" else mont_ev if _t == "montante" else value_ev).append(ev)
        # UN MATCH = UN PARI (user 2026-08-07) : le pari du 1er scan (`stat_bet_first`) N'EST PLUS compté
        # séparément — un même match ne doit produire QU'UNE ligne / UN résultat au ROI et à la série (avant :
        # un rescan qui changeait le pick faisait compter 2 fois le match, ex. Vitória-Athletico = 2 défaites
        # pour 1 match). On garde UNIQUEMENT le pari retenu (stat_bet). Le champ reste sur disque (jamais
        # supprimé) mais n'alimente plus le ROI/affichage. Annule la décision « double scan » du 2026-07-21.
    # COMBINÉS DU JOUR (Sûr + Cote 2) — COMPTÉS AU ROI OVERALL (user 2026-08-19 : « compte les combinés dans le
    # ROI aussi »). Quand `COMBO_ROI_ON`, chaque combiné réglé entre dans `all_ev` (overall + courbe d'équité) en
    # plus du bilan combiné dédié -> Overall = Confiance + Value + Combiné. Toujours dans `combo_form` (2e ligne).
    try:
        from app import combo_daily as _cdroi
        for _cev in (_cdroi.roi_events() + _cdroi.roi_events(variant="cote2")
                     + _cdroi.roi_events(variant="soir")):   # jour + (cote2 dormant) + SOIR au ROI
            if cutoff is not None:
                try:
                    _cdt = datetime.fromisoformat((_cev[0] or "") + "T00:00:00+00:00")
                except ValueError:
                    _cdt = None
                if _cdt is None or _cdt < cutoff:
                    continue
            if _cev[1] in ("won", "lost", "push") and not _combo_rule_void(_cev[0]):
                combo_form.append((_cev[0], _cev[1], "combiné"))
                # COMPTÉ À L'OVERALL avec le MÊME périmètre que le bilan combiné (`combo_stats`) -> filtre
                # `_COMBO_STATS_FROM` (sinon des combinés antérieurs à la bascule gonflent le total). Cohérence
                # totale : Overall = Confiance + Value + Combiné (mêmes combinés que l'onglet).
                if COMBO_ROI_ON and (_cev[0] or "")[:10] >= _COMBO_STATS_FROM:
                    all_ev.append(((_cev[0] or "") + "T00:00:00+00:00", _cev[1], _cev[2],
                                   {"name": (_cev[3] or {}).get("name") or "Combiné du jour",
                                    "sel": "combiné", "sport": "combiné"}))
    except Exception:
        pass
    # MONTANTE — PALIERS COMPTÉS AU ROI OVERALL (user 2026-08-20 nuit : « montante compte au ROI, période
    # validée »). Comme le combiné : chaque palier AFFICHÉ (montante.public_steps = HORS période d'erreurs
    # 09/08→20/08) entre dans all_ev en MISE PLATE 1u sur SA cote/résultat. La montante-mid reste exclue plus
    # haut via `_t=='montante'` (aucun double-compte du pari flagship du même match). Le ladder capitalisé (page
    # Montante) est INCHANGÉ (autre représentation). Pas de rétroactivité : `public_steps` borne au 26/07+.
    if MONTANTE_ROI_ON:
        try:
            from app import montante as _mtroi
            for _ms in _mtroi.public_steps():
                _mr = _ms.get("result")
                if _mr not in ("won", "lost", "push"):
                    continue
                if str(_ms.get("mid") or "") in _roi_mids:   # GARDE anti-double-compte : le match est DÉJÀ au ROI
                    continue                                 # (via son pari flagship) -> ne pas le recompter en montante
                _mdt = (_ms.get("date") or "") + "T00:00:00+00:00"
                if cutoff is not None:
                    try:
                        _mdd = datetime.fromisoformat(_mdt)
                    except ValueError:
                        _mdd = None
                    if _mdd is None or _mdd < cutoff:
                        continue
                _mco = _ms.get("cote")
                if not isinstance(_mco, (int, float)):
                    continue
                all_ev.append((_mdt, _mr, _mco,
                               {"name": _ms.get("match"), "sel": _ms.get("sel") or "montante",
                                "sport": "montante"}))
        except Exception:
            pass
    out = {"overall": _agg_bets(all_ev),               # suivi principal = TOUS les paris depuis le début
           "since_change": _agg_bets(since_ev),        # nouveau système (s'enrichit au fil des scans)
           "by_tier": {"confiance": _agg_bets(conf_ev), "value": _agg_bets(value_ev),
                       "montante": _agg_bets(mont_ev)},   # onglets Confiance/Value + montante à part
           "by_sport": {sport: _agg_bets(evs) for sport, evs in by_sport.items()},
           # « Volume de données » (panneau transparence) : matchs analysés vs matchs réglés (1 par match),
           # + plage de coups d'envoi couverte (période de mesure -> contexte du nombre calibré).
           "volume": {"analysed": n_analysed, "matches": len(match_form),
                      "first": _first, "last": _last}}
    # Bulles de FORME : 1 par match (combiné OU pari principal), TOUS les matchs (défaites de combinés
    # incluses) -> honnête, INDÉPENDANT de la borne combiné du ROI/courbe (demande utilisateur).
    match_form.sort(key=lambda x: x[0] or "")
    _mf = [r for _s, r, _sp in match_form]
    out["overall"]["form"] = _mf[-5:]
    out["overall"]["form12"] = _mf[-12:]
    # Deux lignes SÉPARÉES (graphe principal ET chaque onglet sport) : simples d'un côté, combinés de
    # l'autre. Les combinés n'existent qu'en foot (CdM) -> la ligne combinés ne s'affiche que là.
    simple_form.sort(key=lambda x: x[0] or "")
    combo_form.sort(key=lambda x: x[0] or "")
    out["overall"]["form_simple"] = [r for _s, r, _sp in simple_form][-24:]
    out["overall"]["form_combo"] = [r for _s, r, _sp in combo_form][-24:]
    # Idem pour les mini-formes PAR SPORT : 1 par match, défaites de combinés INCLUSES (sinon le
    # bandeau d'un sport affiche une fausse série de victoires alors que des combinés ont perdu).
    for _sp, blk in out["by_sport"].items():
        _spf = [r for _s, r, sp in match_form if sp == _sp]
        blk["form"] = _spf[-5:]
        blk["form12"] = _spf[-12:]
        blk["form_simple"] = [r for _s, r, sp in simple_form if sp == _sp][-24:]
        blk["form_combo"] = [r for _s, r, sp in combo_form if sp == _sp][-24:]
    if sig is not None and not _bypass_snapshot:
        _STATS_CACHE["full"] = (sig, out)
        _save_stats_snapshot(_snap_h or _sig_hash(sig), out)   # PERSISTE -> lecture O(1) ensuite (restart/workers)
    return out


def volume_24h() -> dict:
    """Activité des dernières 24 h (par coup d'envoi) : combien de matchs/paris/fantômes sont entrés
    dans le volume de données. Sert à afficher la VARIATION 24 h sous chaque compteur du panneau
    « Volume ». TOUJOURS la vraie fenêtre des 24 dernières heures (indépendant du filtre de période).
    Compté à l'IDENTIQUE des compteurs cumulés (matchs 1/match, simple via for_history, calibration
    = fantômes tous sports + paris joués hors-CdM, dans une bande ≥45 %)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    lo0 = _CALIB_BANDS[0][0]
    out = {"analysed": 0, "matches": 0, "simples": 0, "combos": 0, "calibrated": 0, "ghosts": 0}
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        start = d.get("start") or ""
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        except (ValueError, AttributeError):
            dt = None
        if dt is None or dt < cutoff:
            continue
        out["analysed"] += 1
        c = d.get("combo") or {}
        has = bool(c.get("legs"))
        if has and c.get("result") in ("won", "lost", "push"):
            out["matches"] += 1
            out["combos"] += 1
        elif not has and (d.get("result") or {}).get("pick_result") in ("won", "lost", "push"):
            out["matches"] += 1
        rb = retained_bet(d.get("sport"), d.get("id"), for_history=True)
        if rb and rb.get("result") in ("won", "lost", "push"):
            out["simples"] += 1
        for sp in (d.get("shadow") or []):           # fantômes (calibration) — tous sports, CdM incluse
            if sp.get("result") in ("won", "lost") and (sp.get("prob") or 0) >= lo0:
                out["calibrated"] += 1
                out["ghosts"] += 1
        if not _is_world_cup(d):                      # paris JOUÉS dans la calibration (hors CdM)
            for b in (d.get("bets") or []):
                if b.get("result") in ("won", "lost") and (b.get("prob") or 0) >= lo0:
                    out["calibrated"] += 1
    return out


def volume_pending() -> dict:
    """Pronos EN COURS (analysés, PAS encore réglés) : simples retenus, combinés et fantômes en
    attente de résultat. Garde-fou : coup d'envoi À VENIR ou < 2 jours (couvre le délai de règlement
    et l'attente des jambes de combiné) -> exclut les vieux matchs bloqués non réglés, pour refléter
    le pipeline RÉELLEMENT actif. Renvoie {simples, combos, ghosts}."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    lo0 = _CALIB_BANDS[0][0]
    out = {"simples": 0, "combos": 0, "ghosts": 0}
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        start = d.get("start") or ""
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        except (ValueError, AttributeError):
            dt = None
        if dt is None or dt < cutoff:                 # futur -> toujours gardé ; passé -> seulement < 2 j
            continue
        c = d.get("combo") or {}
        if c.get("legs") and c.get("result") not in ("won", "lost", "push"):
            out["combos"] += 1
        rb = retained_bet(d.get("sport"), d.get("id"), for_history=True)
        if rb and rb.get("result") not in ("won", "lost", "push"):
            out["simples"] += 1
        for sp in (d.get("shadow") or []):
            if sp.get("result") not in ("won", "lost") and (sp.get("prob") or 0) >= lo0:
                out["ghosts"] += 1
    return out


def calibration_reliability(buckets: int = 7) -> dict:
    """INDICE DE FIABILITÉ de la calibration + sa TENDANCE chronologique = preuve MESURÉE que le modèle
    s'auto-améliore. On mesure l'écart moyen (MAE) confiance-annoncée ↔ réussite-réelle sur TOUTES les
    prédictions réglées datées (fantômes + jouées, mêmes que la calibration), trié par coup d'envoi.
    L'indice = 100 − MAE·3 (borné 0-100 : 0 pt d'écart = 100). La tendance compare l'écart de la 1ʳᵉ
    moitié à celui de la 2ᵉ (robuste au bruit). HONNÊTE : si ça n'améliore pas, le delta est ≤0 et la
    tendance « flat »/« down ». {} si pas assez de recul. Renvoie {index, mae, series[], delta_mae,
    mae_first, mae_last, trend, n, first, last}."""
    items = []   # (start, prob, won)
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        st = d.get("start") or ""
        if not st:
            continue
        for sp in (d.get("shadow") or []):
            if sp.get("result") in ("won", "lost") and sp.get("prob") is not None:
                items.append((st, sp["prob"], sp["result"] == "won"))
        if not _is_world_cup(d):
            for b in (d.get("bets") or []):
                if b.get("result") in ("won", "lost") and b.get("prob") is not None:
                    items.append((st, b["prob"], b["result"] == "won"))
    items.sort(key=lambda x: x[0])
    n = len(items)
    if n < 50:
        return {}

    def _mae(sub):
        return _calib_agg([(p, w) for _s, p, w in sub]).get("mae")

    def _idx(mae):
        return None if mae is None else max(0, min(100, round(100 - mae * 3)))

    mae = _mae(items)
    if mae is None:
        return {}
    # Série CUMULATIVE : chaque point = fiabilité sur TOUT depuis le début jusqu'à cet instant. Le
    # DERNIER point = tout l'échantillon = l'INDICE GLOBAL -> la courbe FINIT sur le gros chiffre
    # (cohérence : avant, la courbe (fenêtres) ne collait pas à l'indice (global), d'où « 92 mais le
    # point est plus bas que 86 »). Monte à mesure que la calibration se resserre.
    npts = max(4, buckets)
    series, maes = [], []
    for j in range(1, npts + 1):
        cut = max(40, round(j * n / npts))
        m = _mae(items[:cut])
        if m is not None:
            series.append(_idx(m))
            maes.append(m)
    idx = series[-1] if series else _idx(mae)        # = fiabilité globale = fin de courbe (cohérent)
    mae_first = maes[0] if maes else mae             # écart des débuts (peu de données)
    mae_last = maes[-1] if maes else mae             # écart global (fin, = indice)
    delta = (series[-1] - series[0]) if len(series) >= 2 else 0
    trend = "up" if delta >= 2 else "down" if delta <= -2 else "flat"
    return {"index": idx, "mae": mae, "series": series, "delta_mae": round(mae_first - mae_last, 1),
            "mae_first": mae_first, "mae_last": mae_last, "trend": trend, "n": n,
            "first": items[0][0], "last": items[-1][0]}


def combo_stats(since_days: int | None = None) -> dict:
    """Bilan dédié des COMBINÉS réglés (exclus du ROI général, suivis ici) : W/L, profit (mise plate
    1u sur la VRAIE cote), ROI, vraie cote moyenne, rabot moyen vs produit, EV moyenne, et détail
    par NOMBRE DE JAMBES. Non rétroactif (≥ _COMBO_COUNT_FROM). `since_days` filtre la fenêtre."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)) if since_days else None
    rows = []   # (result, real_odds, shave, n_legs, prob)
    curve = []  # (start, result, real_odds) -> courbe d'équité cumulée (mise plate 1u, chronologique)
    crecent = []   # (start, result, real_odds, meta) -> panneau « derniers combinés » (au clic)
    by_sp: dict = {}   # sport -> [(start, result, real_odds)] -> courbe combinés PAR SPORT (onglets)
    _bg = background_sports()   # tennis/basket en arrière-plan : combos EXCLUS de l'officiel (comme les simples)
    for p in glob.glob(os.path.join(DIR, "*.json")):
        d = _meta_load(p)
        if not d:
            continue
        c = d.get("combo") or {}
        if not c.get("legs"):
            continue
        sport = d.get("sport")
        start = d.get("start") or ""
        if start[:10] < _COMBO_STATS_FROM:
            continue                                   # ancien combiné multi-marchés -> calibration seule
        if _combo_rule_void(start):
            continue                                   # combiné hors-règle (08/08→20/08) -> calibration seule
        if cutoff is not None:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except (ValueError, AttributeError):
                dt = None
            if dt is None or dt < cutoff:
                continue
        res = c.get("result")
        if res not in ("won", "lost", "push"):
            continue                                       # 'void' (remboursé) = neutre -> pas au ROI
        # Cote EFFECTIVE si des jambes ont été retirées au règlement (void/push -> cote 1) : le payout
        # d'un combiné gagné amputé d'une jambe indéterminée utilise le produit des jambes gagnées.
        odds = c.get("settle_odds") or c.get("real_odds") or c.get("total")
        _clegs = [{"name": l.get("name") or f'{l.get("home", "")} - {l.get("away", "")}'.strip(" -"),
                   "sel": l.get("sel"), "cote": l.get("cote"), "result": l.get("result")}
                  for l in (c.get("legs") or [])]   # jambes -> historique (demande user 2026-07-26)
        _cmeta = {"name": d.get("name"), "sel": f"Combiné {len(c['legs'])} jambes", "sport": sport,
                  "legs": _clegs}
        by_sp.setdefault(sport, []).append((start, res, float(odds) if odds else None, _cmeta))  # TOUJOURS (simulation)
        if sport not in _bg:                           # OFFICIEL = sports actifs seuls (tennis/basket simulés à part)
            rows.append((res, float(odds) if odds else None, c.get("shave"), len(c["legs"]), c.get("prob")))
            curve.append((start, res, float(odds) if odds else None))
            crecent.append((start, res, float(odds) if odds else None, _cmeta))
    # COMBINÉS DU JOUR — ROI OVERALL = FOOTBALL SEULEMENT (demande user 2026-07-25 : « le ROI ne garde que
    # ce qui est lié au football, tant qu'il n'y a pas d'abonnés »). Un combiné du jour non-foot (tennis/
    # basket mono OU MULTISPORT contenant du tennis/basket) ne compte PLUS au ROI global (tennis/basket sont
    # SIMULÉS). Les mono restent ventilés dans le « Combinés » de leur sport (by_sp = stats simulées) ; les
    # multisport ont déjà leurs jambes reversées dans les provisoires de chaque sport (multisport_legs). C'est
    # une correction d'historique légitime AVANT abonnés (cf. mémoire history-corrections-before-subscribers).
    try:
        from app import combo_daily as _cdmod
        # DEUX combinés/jour (user 2026-08-19) : « Sûr » (variant='') + « Cote 2 » (variant='cote2'). LES DEUX
        # comptent dans le BILAN COMBINÉS (rows/curve/crecent), hors ROI officiel des simples.
        for _dt, _res, _cote, _det in (_cdmod.roi_events() + _cdmod.roi_events(variant="cote2")
                                       + _cdmod.roi_events(variant="soir")):   # jour + cote2 + SOIR
            if _res not in ("won", "lost", "push"):
                continue
            if (_dt or "")[:10] < _COMBO_STATS_FROM:
                continue                               # combiné du jour n'existe qu'à partir du 26/07
            if _combo_rule_void(_dt):
                continue                               # combiné hors-règle (08/08→20/08) -> calibration seule
            if cutoff is not None:
                try:
                    _cdt2 = datetime.fromisoformat((_dt or "") + "T00:00:00+00:00")
                except ValueError:
                    _cdt2 = None
                if _cdt2 is None or _cdt2 < cutoff:
                    continue
            _o = float(_cote) if _cote else None
            _lsp = _det.get("leg_sport")
            if _lsp == "foot":                             # SEUL le combiné du jour 100 % foot compte au ROI
                rows.append((_res, _o, None, _det.get("n_legs") or 0, None))
                curve.append((_dt, _res, _o))
                crecent.append((_dt, _res, _o, {"name": _det.get("name"), "sel": "combiné du jour",
                                                "sport": "combiné", "legs": _det.get("legs")}))
            if _lsp:                                       # ventilation par sport (mono, y c. tennis/basket simulés)
                by_sp.setdefault(_lsp, []).append((_dt, _res, _o,
                    {"name": _det.get("name"), "sel": "Combiné du jour", "sport": _lsp,
                     "legs": _det.get("legs")}))
    except Exception:
        pass
    # COMBINÉS DU JOUR SIMULÉS (tennis/basket, demande user 2026-07-25) : injectés SEULEMENT dans by_sp
    # (courbe/ROI de l'onglet Combinés du sport) -> JAMAIS dans rows/curve/crecent (ROI officiel intact).
    try:
        from app import combo_daily as _cdmod2
        for _spsim in _cdmod2.SIM_SPORTS:
            for _dt, _res, _cote, _det in _cdmod2.sim_events(_spsim):
                if _res not in ("won", "lost", "push"):
                    continue
                if cutoff is not None:
                    try:
                        _cdt3 = datetime.fromisoformat((_dt or "") + "T00:00:00+00:00")
                    except ValueError:
                        _cdt3 = None
                    if _cdt3 is None or _cdt3 < cutoff:
                        continue
                _o2 = float(_cote) if _cote else None
                by_sp.setdefault(_spsim, []).append((_dt, _res, _o2,
                    {"name": _det.get("name"), "sel": "Combiné du jour", "sport": _spsim,
                     "legs": _det.get("legs")}))
    except Exception:
        pass
    won = sum(1 for r, o, s, n, pr in rows if r == "won")
    lost = sum(1 for r, o, s, n, pr in rows if r == "lost")
    push = sum(1 for r, o, s, n, pr in rows if r == "push")
    settled, staked = won + lost, won + lost + push
    profit = sum((o - 1) if r == "won" else (-1 if r == "lost" else 0.0)
                 for r, o, s, n, pr in rows if o)
    odds_vals = [o for r, o, s, n, pr in rows if o]
    shaves = [s for r, o, s, n, pr in rows if s is not None]
    evs = [o * pr / 100 - 1 for r, o, s, n, pr in rows if o and pr]
    by_legs = {}
    for k in sorted({n for r, o, s, n, pr in rows}):
        sub = [r for r, o, s, n, pr in rows if n == k]
        sw = sum(1 for r in sub if r == "won")
        sset = sum(1 for r in sub if r in ("won", "lost"))
        by_legs[k] = {"n": len(sub), "won": sw,
                      "wr": round(100 * sw / sset) if sset else None}
    # Courbe d'équité COMBINÉS : cumul P&L (mise plate 1u sur la VRAIE cote), ordre chronologique.
    curve.sort(key=lambda x: x[0] or "")
    pts, dates, cum = [0.0], [], 0.0    # `dates` aligné sur points[1:] -> place les repères MODEL_MILESTONES
    for _s, r, o in curve:
        if r == "won" and o:
            cum += o - 1
        elif r == "lost":
            cum -= 1
        pts.append(round(cum, 3))
        dates.append(_s or "")
    # Série EN COURS (signée) + 15 derniers combinés détaillés (panneau au clic sur le graphe).
    _cseq = [r for _s, r, _o in curve if r in ("won", "lost")]
    cstreak = 0
    if _cseq:
        _cl, _cc = _cseq[-1], 0
        for r in reversed(_cseq):
            if r != _cl:
                break
            _cc += 1
        cstreak = _cc if _cl == "won" else -_cc
    crecent.sort(key=lambda x: x[0] or "")
    crec = [{"start": s, "result": r, "cote": o, "name": (mt or {}).get("name"),
             "sel": (mt or {}).get("sel"), "sport": (mt or {}).get("sport"),
             "legs": (mt or {}).get("legs")}     # jambes -> historique dépliable (demande user 2026-07-26)
            for s, r, o, mt in crecent if r in ("won", "lost", "push")]   # TOUT l'historique (panneau scrollable, demande user 2026-07-25)
    return {"n": len(rows), "won": won, "lost": lost, "push": push, "dates": dates,
            "streak": cstreak, "recent": crec,
            "win_rate": round(100 * won / settled) if settled else None,
            "profit": round(profit, 2),
            "roi": round(100 * profit / staked, 1) if staked else None,
            "avg_odds": round(sum(odds_vals) / len(odds_vals), 2) if odds_vals else None,
            "avg_shave": round(sum(shaves) / len(shaves), 1) if shaves else None,
            "avg_ev": round(100 * sum(evs) / len(evs)) if evs else None,
            "by_legs": by_legs, "points": pts,
            # Bilan combinés PAR SPORT (mêmes clés que stats_full.by_sport via _agg_bets :
            # points/roi/pct/settled/avg_odds) -> courbe combinés dédiée dans chaque onglet sport.
            "by_sport": {sp: _agg_bets(evs) for sp, evs in by_sp.items() if sp}}


_CALIB_BANDS = [(45, 55), (55, 65), (65, 75), (75, 85), (85, 101)]

_MARKET_FAMILY = {   # 1er token du code -> famille de marché lisible (pour la calibration par marché)
    "1X2": "Vainqueur", "WIN": "Vainqueur", "DC": "Double chance", "REGTIME": "Vainqueur",
    # OVER et UNDER = familles SÉPARÉES (user 2026-08-13) : regroupés, l'auto-exclusion ne pouvait pas bannir
    # les Under seuls quand les Over marchaient (ou l'inverse). Séparés -> chaque sens s'exclut/se ré-inclut
    # indépendamment sur SON propre ROI/écart calibré.
    "OVER": "Total Over", "UNDER": "Total Under", "BTTS": "Les 2 marquent",
    "HCAP": "Handicap", "SETHCAP": "Handicap", "HCAP3": "Handicap", "TEAMTOT": "Total équipe",
    "HALFRES": "Mi-temps",
    "SET": "Sets", "SETWIN": "Sets", "SETSCORE": "Sets", "SETSTOT": "Sets",
    "SETGAMES": "Jeux", "TOTGAMES": "Jeux", "TEAMGAMES": "Jeux", "GAMESHCAP": "Jeux", "HOLD1": "Jeux",
    "TIEBREAK": "Tie-break",
    "CARDS": "Cartons", "REDCARDS": "Cartons", "CORNERS": "Corners",
    "FIRSTTO": "Premier à X pts",
    # mi-temps (foot)
    "TEAMHALF": "Mi-temps", "HALFTOT": "Mi-temps", "WINHALF": "Mi-temps",
    "BTTSHALF": "Mi-temps", "TEAMBOTH": "Mi-temps",
    # quart-temps / mi-temps (basket)
    "BQTOT": "Quart-temps/MT", "BQTEAM": "Quart-temps/MT", "BQWIN": "Quart-temps/MT",
    "BQHCAP": "Quart-temps/MT",
    # props joueur (foot Opta + basket box-score)
    "PLAYERBK": "Props joueur", "PLAYERFB": "Props joueur",
    # but/buteur/gardien/score exact
    "FIRSTGOAL": "Premier but", "FIRSTSCORER": "Premier buteur",
    "GKSAVES": "Arrêts gardien", "SCORE": "Score exact",
    # tirs (foot) — étaient noyés dans « Autre » (demande user 2026-08-01 : « Autre » ne doit pas exister)
    "SHOTSOT": "Tirs cadrés", "SHOTS": "Tirs",
    # mi-temps (foot) supplémentaires
    "BOTHHALVES": "Mi-temps", "DCHALF": "Mi-temps",
    # tennis : breaks de service
    "TOTBREAKS": "Breaks", "BREAKS": "Breaks",
    # props joueur : « marque ou passe décisive »
    "SCOREASSIST": "Props joueur",
}


# Exclusions calibrées AUTOMATIQUES : on n'écarte un sport/marché des recommandations QUE s'il a fait
# ses preuves dans le MAUVAIS sens — assez de paris ET un écart nettement négatif. Sinon (petit
# échantillon = bruit), on NE conclut PAS : le pari reste éligible, protégé par le seuil de confiance.
# Auto-révisable : si une catégorie redevient bonne avec plus de données, elle se ré-inclut seule.
CALIB_MIN_N = 25     # nb mini de paris réglés avant d'oser exclure une catégorie (sous ça = bruit)
CALIB_GAP_MAX = -8   # réussite réelle au moins 8 pts SOUS la confiance annoncée = sur-confiance nette
# HYSTÉRÉSIS (demande user 2026-07-16) — bande haute/basse pour TUER le flottement jour/jour (un marché,
# ex. « Vainqueur », écarté un jour, remis le lendemain, ré-écarté le surlendemain, parce que son écart/ROI
# oscille juste autour du seuil unique). On EXCLUT à un seuil STRICT (CALIB_GAP_MAX / CALIB_ROI_MAX) mais on
# ne RÉ-INTÈGRE qu'après une récupération NETTE (CALIB_GAP_BACK / CALIB_ROI_BACK). Entre les deux = ZONE
# MORTE : on GARDE l'état précédent (persisté). Schmitt-trigger classique. La largeur de bande doit dépasser
# le « pas » d'un pari réglé (~100/n pts sur le taux) -> plus de bascule sur un seul résultat.
CALIB_GAP_BACK = -4  # une fois exclu (écart ≤ -8), le marché ne revient que si l'écart REMONTE à ≥ -4
CALIB_ROI_BACK = -8  # une fois exclu (ROI ≤ -15%), le marché ne revient que si le ROI REMONTE à ≥ -8%
# GARDE-FOU « sur-calibration CATASTROPHIQUE » (demande user 2026-07-28) : un marché dont l'écart réel−annoncé
# est ÉNORME (≤ CALIB_GAP_SEVERE) est exclu SANS attendre CALIB_MIN_N=25 — dès CALIB_MIN_N_SEVERE paris. Motif :
# un écart de -20/-40 pts est statistiquement damnant bien avant 25 (binomiale) et le laisser « en zone bruit »
# ouvre un trou (ex. foot « Les 2 marquent — Non » : annoncé 73% → réel 32%, n=19, ni exclu ni corrigé par
# calibrated_conf). Sortie par l'hystérésis normale (CALIB_GAP_BACK) une fois le recul suffisant.
CALIB_GAP_SEVERE = -20   # écart réel−annoncé ≤ -20 pts = sur-confiance catastrophique (exclusion accélérée)
CALIB_MIN_N_SEVERE = 12  # nb mini de prédictions pour conclure sur un écart CATASTROPHIQUE (< bruit total)
_SPORT_FR = {"Football": "foot", "Tennis": "tennis", "Basket": "basket"}
_EXCL_STATE_PATH = os.path.join(_ROOT, "data", "excluded_state.json")   # dernier état COMMITÉ {sport:[marchés]}


def _load_excluded_state() -> dict:
    """Dernier ensemble écarté COMMITÉ par sport ({sport: set}) — l'« état précédent » de l'hystérésis.
    {} si le fichier n'existe pas encore (1er run) -> la décision se prend alors au seuil strict, sans
    zone morte (comportement historique)."""
    try:
        with open(_EXCL_STATE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return {sp: set(ms) for sp, ms in raw.items() if isinstance(ms, list)}
    except (OSError, ValueError):
        return {}


def _save_excluded_state(state: dict) -> None:
    """Persiste l'état écarté (écriture ATOMIQUE tmp+replace). Best-effort : un échec disque ne casse
    jamais la sélection (on garde l'état en mémoire pour ce cycle)."""
    try:
        tmp = _EXCL_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({sp: sorted(ms) for sp, ms in state.items()}, f, ensure_ascii=False)
        os.replace(tmp, _EXCL_STATE_PATH)
    except OSError:
        pass


def market_of(code: str) -> str:
    """Famille de marché lisible déduite du code de règlement (ex. 'UNDER 163' -> 'Total Under'). Le 1er token
    est nettoyé du suffixe joueur `|Nom` (ex. 'SCOREASSIST|Un joueur' -> 'SCOREASSIST'). « Autre » ne doit
    JAMAIS apparaître (demande user 2026-08-01) : tout code connu est classé -> si « Autre » ressort, c'est
    un NOUVEAU token de marché à ajouter à `_MARKET_FAMILY`."""
    tok = ((code or "").split()[0] if code else "").split("|")[0]
    return _MARKET_FAMILY.get(tok, "Autre")


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


_CONF_MK_CACHE = {"ts": 0.0, "by_sport": {}}


def _confiance_markets(sport: str) -> frozenset:
    """Marchés Confiance-éligibles, CALCULÉS EN CONTINU (pas de liste figée — user 2026-08-14) : un marché est
    éligible si son TAUX DE RÉUSSITE mesuré est ≥ `_CONFIANCE_MARKET_MIN_WR` ET son échantillon ≥
    `_CONFIANCE_MARKET_MIN_N`. On lit la MÊME mesure que `exclusions_report` : `calibration(min_conf=_MIN_CONF)`
    (paris/fantômes à confiance ≥ 65 % -> exclut le bruit basse-confiance ; donne Over 76 %, DC 79 %, etc.).
    Un marché sort/rentre TOUT SEUL au fil des fantômes (ex. Vainqueur 71 % -> écarté ; revient dès ≥ 75 %).
    Caché 120 s (calibration globe tous les sidecars)."""
    now = time.time()
    if not _CONF_MK_CACHE["by_sport"] or now - _CONF_MK_CACHE["ts"] >= 120:
        cal = calibration(min_conf=_MIN_CONF)
        by = {}
        for sport_label, g in (cal.get("by_sport") or {}).items():
            sp = _SPORT_FR.get(sport_label, sport_label.lower())
            by[sp] = frozenset(
                mk for mk, mg in (g.get("markets") or {}).items()
                if mg.get("win_rate") is not None and mg["win_rate"] >= _CONFIANCE_MARKET_MIN_WR
                and (mg.get("n") or 0) >= _CONFIANCE_MARKET_MIN_N)
        _CONF_MK_CACHE.update(ts=now, by_sport=by)
    return _CONF_MK_CACHE["by_sport"].get(sport, frozenset())


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


CALIB_ROI_MAX = -15   # ROI réel (%) sous lequel un marché est exclu — SI l'échantillon est suffisant
#                       (n ≥ CALIB_MIN_N). Un marché peut être bien CALIBRÉ mais EV-négatif (cotes courtes) :
#                       le gap de calibration ne le capte pas, le ROI oui. Data-driven, auto-révisable.


def markets_coverage() -> dict:
    """MATRICE DE RÉSOLUBILITÉ (data-driven, doc vivante) : par (sport, marché), combien de paris/jambes
    au TOTAL, combien RÉGLÉS, et combien NON réglés sur un match FINI (= trou de règlement) + les paris
    SANS code (marché non mappé). `resolvable` = tout réglé et code présent. Référence : docs/SOURCES.md."""
    from collections import defaultdict

    def _fin(d):
        r = d.get("result")
        return bool((r or {}).get("pick_result") if isinstance(r, dict) else r) \
            or bool((d.get("combo") or {}).get("result"))

    agg = defaultdict(lambda: {"total": 0, "settled": 0, "unresolved": 0})
    for p in glob.glob(os.path.join(DIR, "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sport, fin = d.get("sport"), _fin(d)
        items = [(b.get("code"), b.get("result")) for b in (d.get("bets") or [])]
        items += [(l.get("code"), l.get("result")) for l in ((d.get("combo") or {}).get("legs") or [])]
        for code, res in items:
            mk = market_of(code) if code else "(sans code)"
            a = agg[(sport, mk)]
            a["total"] += 1
            if res in ("won", "lost", "push"):
                a["settled"] += 1
            elif fin:
                a["unresolved"] += 1
    by_sport, gaps = defaultdict(list), []
    for (sp, mk), a in sorted(agg.items(), key=lambda x: (x[0][0], -x[1]["total"])):
        resolvable = a["unresolved"] == 0 and mk != "(sans code)"
        by_sport[sp].append({"market": mk, "total": a["total"], "settled": a["settled"],
                             "unresolved_on_finished": a["unresolved"], "resolvable": resolvable})
        if not resolvable and (a["unresolved"] >= 2 or mk == "(sans code)"):
            gaps.append({"sport": sp, "market": mk, "unresolved_on_finished": a["unresolved"], "total": a["total"]})
    return {"by_sport": dict(by_sport), "gaps": gaps, "doc": "docs/SOURCES.md",
            "note": "resolvable=False -> voir docs/SOURCES.md §4 (trous à combler)"}


_EXCL_BY_SPORT_CACHE: tuple = (0.0, {})   # (expiry_ts, {sport:set}) — cache court : _excluded_by_sport() est
_EXCL_BY_SPORT_TTL = 30                    # appelé PAR CARTE au rendu (via _bets_table) et rappelait calibration()
#                                            + _dir_sig() (scandir+stat de TOUT le dossier) à chaque fois -> gel de
#                                            l'event loop ~8 s (O(cartes×fichiers)). Les exclusions bougent à
#                                            l'échelle du JOUR (« forward-looking ») -> 30 s = 0 impact fonctionnel.


# BANS DURS football (décision user, jamais data-driven, JAMAIS jouables — même en historique `for_history`) :
#   « Corners » (2026-06-19) · « Les 2 marquent » (BTTS, 2026-08-07 : 25 %/−59 % ROI, casse les séries).
# Source UNIQUE partagée par `_excluded_by_sport` (sélection/exclusion) ET `retained_bet` (rétention/gel/
# affichage) — sans quoi un pick BTTS non publié se faisait retenir/figer/afficher via `for_history=True`.
_HARD_BAN_FOOT = {"Corners", "Les 2 marquent"}


def _excluded_by_sport() -> dict:
    """{sport: set(marchés écartés POUR CE SPORT)} — les exclusions de marché sont désormais PROPRES À
    CHAQUE SPORT (demande user 2026-07-02). Un marché mauvais en basket n'écarte PAS le même marché en
    foot. Trois raisons d'écarter un marché DANS UN SPORT : (a) SUR-CONFIANCE nette dans la calibration
    DU SPORT (n ≥ CALIB_MIN_N ET écart réel−annoncé ≤ CALIB_GAP_MAX) ; (b) ban dur « Corners » (foot,
    demande user 2026-06-19) ; (c) ROI ≤ CALIB_ROI_MAX PAR (sport,marché), calculé sur la calibration
    FANTÔMES INCLUS. Data-driven, auto-révisable dans les deux sens. Vide tant qu'on manque de recul PAR
    SPORT (le petit n ne conclut pas). NB : avant, l'exclusion était globale et DILUAIT les problèmes d'un
    sport (ex. basket Vainqueur/Total sur-confiants, invisibles noyés dans le foot/tennis).
    (c) 2026-07-06 : on ne lit PLUS le ROI GLOBAL des paris JOUÉS (perf_breakdown, lent : ~1 pari/match)
    mais le ROI PAR (sport,marché) de la calibration, qui INCLUT les FANTÔMES (10-14/match). Un marché
    bien calibré mais EV-négatif (ex. tennis « Jeux » -21 %) est ainsi écarté sur un VRAI échantillon
    (n ≥ CALIB_MIN_N) SANS attendre 25 paris réellement joués. (La calibration agrège fantômes + joués :
    le signal réel-argent y est déjà contenu.)"""
    global _EXCL_BY_SPORT_CACHE
    _now = time.time()
    if _EXCL_BY_SPORT_CACHE[0] > _now:            # cache court -> pas de calibration()/_dir_sig() par carte
        return _EXCL_BY_SPORT_CACHE[1]
    cal = calibration(min_conf=_MIN_CONF)
    prev = _load_excluded_state()                        # état précédent = charnière de l'hystérésis
    out: dict[str, set] = {}
    for fr, g in (cal.get("by_sport") or {}).items():
        sp = _SPORT_FR.get(fr, fr.lower())
        prev_sp = prev.get(sp, set())
        # (b) BANS DURS foot : Corners (user 2026-06-19) + « Les 2 marquent » (BTTS) — user 2026-08-07 :
        #     BTTS mesuré 1G/3P = 25 % / ROI −59 % depuis le 22/06, marché « pile ou face » qui casse les
        #     séries. Banni comme les corners/cartons (objectif : retrouver les longues séries de victoires).
        ms = set(_HARD_BAN_FOOT) if sp == "foot" else set()
        for name, mg in (g.get("markets") or {}).items():
            n = mg.get("n") or 0
            gap = (mg.get("win_rate") or 0) - (mg.get("avg_conf") or 0)
            roi = mg.get("roi")
            was = name in prev_sp
            # GARDE-FOU sur-calibration CATASTROPHIQUE : écart énorme (≤ CALIB_GAP_SEVERE) sur un échantillon
            # déjà parlant (≥ CALIB_MIN_N_SEVERE) -> exclu TOUT DE SUITE, sans attendre CALIB_MIN_N (sinon le
            # marché reste « en zone bruit » alors qu'il perd franchement, cf. foot « Les 2 marquent — Non »).
            if n >= CALIB_MIN_N_SEVERE and gap <= CALIB_GAP_SEVERE:
                ms.add(name)
                continue
            if n < CALIB_MIN_N:                          # pas de recul -> on ne conclut pas : statu quo
                if was:
                    ms.add(name)
                continue
            # HYSTÉRÉSIS : signal MAUVAIS = sur-confiance nette OU ROI franchement perdant (seuil STRICT) ;
            # signal RÉCUPÉRÉ = écart ET ROI revenus au-dessus des seuils de retour (plus haut). Entre les
            # deux = zone morte -> on garde l'état précédent (`was`). Cf. CALIB_GAP_BACK/CALIB_ROI_BACK.
            bad = (gap <= CALIB_GAP_MAX) or (roi is not None and roi <= CALIB_ROI_MAX)
            back = (gap >= CALIB_GAP_BACK) and (roi is None or roi >= CALIB_ROI_BACK)
            if bad:
                ms.add(name)                             # (a)/(c) exclu (ou maintenu exclu)
            elif back:
                pass                                     # récupération nette -> ré-intégré
            elif was:
                ms.add(name)                             # zone morte -> on garde l'exclusion de la veille
        out[sp] = ms
    out.setdefault("foot", set(_HARD_BAN_FOOT))   # foot garde au minimum les bans durs (corners + BTTS)
    if out != prev:                                      # ne persiste QUE sur un vrai changement de décision
        _save_excluded_state(out)
    _EXCL_BY_SPORT_CACHE = (_now + _EXCL_BY_SPORT_TTL, out)
    return out


def excluded_markets(sport: str) -> set:
    """Marchés écartés POUR CE SPORT (per-sport, auto-révisable) — cf. _excluded_by_sport. C'est CE filtre
    (et non un filtre global) qu'utilise la SÉLECTION du pari simple et des combinés pour chaque sport."""
    return _excluded_by_sport().get(sport, set(_HARD_BAN_FOOT) if sport == "foot" else set())


_SPORT_PROB_PATH = os.path.join(_ROOT, "data", "sport_probation.json")   # sports EN PROBATION (persisté)
SPORT_ROI_ENTER = -8    # ROI calibration (fantômes) SOUS lequel un SPORT entre AUTO en probation (n ≥ CALIB_MIN_N)
SPORT_ROI_BACK = -2     # ROI calibration AU-DESSUS duquel il est signalé « PRÊT à réactiver » — mais la sortie
#                         reste MANUELLE (reactivate_sport), jamais automatique (demande user 2026-07-24)


def _load_sport_probation() -> set:
    try:
        with open(_SPORT_PROB_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return set(raw) if isinstance(raw, list) else set()
    except (OSError, ValueError):
        return set()


def _save_sport_probation(s: set) -> None:
    try:
        tmp = _SPORT_PROB_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(s), f, ensure_ascii=False)
        os.replace(tmp, _SPORT_PROB_PATH)
    except OSError:
        pass


def _sport_probation(cal_by_sport: dict) -> set:
    """Sports « en probation » = ROI durablement NÉGATIF -> on SUSPEND la publication de leurs paris (comptés
    au ROI), MAIS on continue de les ANALYSER (fantômes/calibration) pour mesurer une remontée. L'ENTRÉE est
    AUTOMATIQUE (protection, ROI calibration ≤ SPORT_ROI_ENTER). La SORTIE est **100 % MANUELLE** (demande
    user 2026-07-24 : « je ne veux pas que le tennis soit réactivé tout seul sans mon accord ») : un sport en
    pause Y RESTE tant que `reactivate_sport()` n'est pas appelé, MÊME si son ROI a récupéré — on ne fait que
    le SIGNALER (`sport_reactivation_ready`). Persisté (data/sport_probation.json)."""
    prev = _load_sport_probation()
    cur = set(prev)                             # PERSISTANT : jamais de sortie AUTO (réactivation manuelle)
    for name, g in (cal_by_sport or {}).items():
        sp = _SPORT_FR.get(name, name.lower())
        roi, n = g.get("roi"), g.get("n") or 0
        if roi is None or n < CALIB_MIN_N:
            continue
        if sp not in cur and roi <= SPORT_ROI_ENTER:   # ENTRÉE auto (protection) ; aucune sortie auto
            cur.add(sp)
    if cur != prev:
        _save_sport_probation(cur)
    return cur


def reactivate_sport(sport: str) -> bool:
    """RÉACTIVE manuellement un sport en pause (seul moyen d'en sortir — jamais auto, demande user
    2026-07-24). True s'il était en pause. Après ça, ses paris/provisoires/combos re-sortent normalement."""
    s = _load_sport_probation()
    if sport in s:
        s.discard(sport)
        _save_sport_probation(s)
        return True
    return False


def pause_sport(sport: str) -> bool:
    """Met MANUELLEMENT un sport en pause (en plus de l'entrée auto). True si nouvellement ajouté."""
    s = _load_sport_probation()
    if sport in s:
        return False
    s.add(sport)
    _save_sport_probation(s)
    return True


def sport_reactivation_ready() -> dict:
    """{sport: roi_calibration} pour les sports EN PAUSE dont le ROI calibration (fantômes) a RÉCUPÉRÉ
    (≥ SPORT_ROI_BACK, n ≥ CALIB_MIN_N) -> PRÊTS à réactiver, EN ATTENTE de l'accord du proprio. SIGNAL
    seulement : la réactivation reste manuelle (`reactivate_sport`)."""
    paused = _load_sport_probation()
    if not paused:
        return {}
    out = {}
    for name, g in (calibration(min_conf=_MIN_CONF).get("by_sport") or {}).items():
        sp = _SPORT_FR.get(name, name.lower())
        roi = g.get("roi")
        if sp in paused and roi is not None and roi >= SPORT_ROI_BACK and (g.get("n") or 0) >= CALIB_MIN_N:
            out[sp] = roi
    return out


_VOL_BY_SPORT_CACHE: dict = {}   # (dir_sig, ndays) -> {sport: {...}}


def volume_by_sport(ndays: int = 7) -> dict:
    """Volume d'ANALYSE et de SÉLECTION par sport sur les `ndays` derniers jours SPORTIFS (06h→06h) :
    {sport: {analysed, picks, ghosts, combos}}. `analysed` = matchs dont le dossier a été produit ;
    `picks` = matchs avec un pari retenu (`bets`) ; `ghosts` = prédictions fantômes (calibration) ;
    `combos` = combinés du jour du sport. Lecture seule, cachée par signature du dossier (perf)."""
    from datetime import timezone, timedelta
    key = (_dir_sig(), ndays)
    cached = _VOL_BY_SPORT_CACHE.get(key)
    if cached is not None:
        return cached
    from app import web as _w
    from app import combo_daily as _cd
    try:
        today = _w._sport_date(_w.to_local(datetime.now(timezone.utc)))
    except Exception:
        today = datetime.now(timezone.utc).date()
    lo = today - timedelta(days=ndays - 1)

    def _sday(dt):
        try:
            return _w._sport_date(_w.to_local(dt)) if dt else None
        except Exception:
            return None
    per = {sp: {"analysed": 0, "picks": 0, "provisional": 0, "ghosts": 0, "combos": 0}
           for sp in ("foot", "tennis", "basket")}
    for sp in per:
        for d in iter_meta(sp):
            sd = _sday(d.get("_start_dt"))
            if not sd or not (lo <= sd <= today):
                continue
            per[sp]["analysed"] += 1
            # SÉLECTION = pari SIMPLE réellement retenu par la logique du site (conf calibrée ≥ 65 % + EV +
            # garde-fous), indépendant de la PUBLICATION Telegram. `for_history=True` re-sélectionne aussi
            # les sports en arrière-plan (tennis/basket) → mesure la capacité de sélection. Avant : `d["bets"]`
            # (= pari publié/réinjecté) → 0 si scan --no-notify, trompeur.
            if retained_bet(sp, str(d.get("id") or ""), for_history=True):
                per[sp]["picks"] += 1
            per[sp]["ghosts"] += len(d.get("shadow") or [])
        for day, cb in (_cd._load(sp) or {}).items():        # combinés du jour du sport dans la fenêtre
            try:
                cd = datetime.fromisoformat(day).date()
            except (ValueError, TypeError):
                continue
            if lo <= cd <= today and isinstance(cb, dict) and cb.get("legs"):
                per[sp]["combos"] += 1
    # PROVISOIRES (indicatifs, hors ROI) : suivi séparé data/provisional_track.json (1 par match, `start` iso).
    try:
        from app import provisional as _prov
        for p in (_prov._load() or {}).values():
            sp = p.get("sport")
            if sp not in per:
                continue
            try:
                pdt = datetime.fromisoformat(str(p.get("start")).replace("Z", "+00:00"))
            except (ValueError, TypeError, AttributeError):
                continue
            sd = _sday(pdt)
            if sd and lo <= sd <= today:
                per[sp]["provisional"] += 1
    except Exception:
        pass
    _VOL_BY_SPORT_CACHE[key] = per
    return per


def our_market_analysis(home: str, away: str, market: str) -> tuple:
    """NOTRE lecture COMPLÈTE (confiance CALIBRÉE %, cote Unibet, ID DE FICHE) d'un match + MARCHÉ, depuis
    NOS sidecars foot — le match est analysé par notre scan (ex. picks Betmines via `_betmines_extra_foot`).
    Le code est DÉRIVÉ du LIBELLÉ de marché (« Plus de 1.5 buts » -> « OVER 1.5 ») car les sources externes
    (Betmines) ont un code compact (« O15 »). L'id de fiche renvoyé permet à l'appelant de charger NOTRE
    justification (.md) pour l'afficher À L'IDENTIQUE du pari joué (SOURCE UNIQUE d'analyse, demande user
    2026-07-27 : jamais deux analyses divergentes du même pari). (None, None, None) si non analysé / marché
    non prédit -> l'appelant garde sa valeur externe."""
    from app.settle_analyst import code_from_pick
    code = (code_from_pick(market or "", "foot", home or "", away or "") or "").strip()
    if not code:
        return (None, None, None)
    _stop = {"fc", "sc", "cf", "ii", "de", "do", "da", "ac", "if", "sp", "rj", "mg", "slc"}

    def _tk(s):
        # DÉ-ACCENTUÉ avant tokenisation : sinon « Östers » -> token « sters » (le `[a-z0-9]+` saute le `ö`)
        # et le match échoue silencieusement (bug 2026-07-27 : jambe Betmines figée à 95 % faute de match).
        return set(re.findall(r"[a-z0-9]+", _deacc(s or "").lower())) - _stop

    def _hit(a, b):
        # chevauchement TOLÉRANT au pluriel/suffixe (« Öster » vs « Östers », « Varberg » vs « Varbergs ») :
        # un token de ≥3 car est préfixe de l'autre. Évite l'échec sur les variantes de nommage Betmines/Unibet.
        for x in a:
            for y in b:
                if x == y or (len(x) >= 3 and len(y) >= 3 and (x.startswith(y) or y.startswith(x))):
                    return True
        return False
    th, ta = _tk(home), _tk(away)
    if not (th and ta):
        return (None, None, None)
    for d in iter_meta("foot"):
        nm = d.get("name") or ""
        dh, _, da = nm.partition(" - ")
        _dh, _da = _tk(dh), _tk(da)
        if not ((_hit(_dh, th) and _hit(_da, ta)) or (_hit(_dh, ta) and _hit(_da, th))):
            continue
        _preds = list(d.get("shadow") or []) + [
            {"sel": b.get("sel"), "cote": b.get("odds"), "prob": b.get("prob"), "code": b.get("code")}
            for b in (d.get("bets") or [])]
        for p in _preds:
            _pc = (p.get("code") or code_from_pick(p.get("sel") or "", "foot", dh, da) or "").strip()
            if _pc and _pc == code:
                _pr = p.get("prob")
                _cal = calibrated_conf(_pr, "foot", _pc) if isinstance(_pr, (int, float)) else None
                return (round(_cal) if isinstance(_cal, (int, float)) else _pr, p.get("cote"), d.get("id"))
        return (None, None, None)      # match trouvé mais ce marché précis non prédit
    return (None, None, None)


def our_market_view(home: str, away: str, market: str) -> tuple:
    """Comme `our_market_analysis` mais renvoie seulement (confiance CALIBRÉE %, cote Unibet) — repli pour les
    appelants qui n'ont pas besoin de l'id de fiche (ex. gel `our_prob`/`our_cote` de betmines_watch)."""
    _p, _c, _ = our_market_analysis(home, away, market)
    return (_p, _c)


def canonical_match(home: str, away: str) -> tuple:
    """Noms d'équipe + compétition CANONIQUES (depuis NOTRE fiche foot du match) pour un match désigné par des
    noms EXTERNES (ex. Betmines « SalPa II » / « Finland - Kolmonen - … »). Uniformise l'AFFICHAGE : un même
    match se lit PAREIL partout — même nom d'équipe, même libellé de compétition (demande user 2026-07-28 :
    « tout ce qui est affiché doit être pareil si c'est le même match »). Les noms retournés sont ALIGNÉS sur
    l'ordre de l'entrée (`home` d'entrée -> l'équipe à domicile côté entrée). (None, None, None) si le match
    n'est pas dans nos fiches -> l'appelant garde les noms externes. PURE lecture d'affichage."""
    _stop = {"fc", "sc", "cf", "ii", "de", "do", "da", "ac", "if", "sp", "rj", "mg", "slc"}

    def _tk(s):
        return set(re.findall(r"[a-z0-9]+", _deacc(s or "").lower())) - _stop

    def _hit(a, b):
        for x in a:
            for y in b:
                if x == y or (len(x) >= 3 and len(y) >= 3 and (x.startswith(y) or y.startswith(x))):
                    return True
        return False
    th, ta = _tk(home), _tk(away)
    if not (th and ta):
        return (None, None, None)
    for d in iter_meta("foot"):
        nm = d.get("name") or ""
        _dh, _, _da = nm.partition(" - ")
        ch = d.get("home") or _dh
        ca = d.get("away") or _da
        comp = d.get("comp") or ""
        th_h, th_a = _tk(ch), _tk(ca)
        if _hit(th_h, th) and _hit(th_a, ta):          # même ordre (input home ↔ fiche home)
            return (ch, ca, comp)
        if _hit(th_h, ta) and _hit(th_a, th):          # ordre INVERSÉ -> on ré-aligne sur l'entrée
            return (ca, ch, comp)
    return (None, None, None)


def background_sports() -> set:
    """Sports en mode ARRIÈRE-PLAN / SIMULATION (demande user 2026-07-24) : tennis/basket sont ANALYSÉS et
    leurs picks CONTINUENT de se sélectionner + se régler → ROI SIMULÉ vivant (bien plus représentatif que
    les fantômes pour décider une réintégration), MAIS ils sont CACHÉS de la page des paris, NON publiés, et
    EXCLUS du ROI headline (foot). Le set persiste (`sport_probation.json`) — entrée auto si ROI ≤
    SPORT_ROI_ENTER (via _sport_probation, appelé par auto_exclusions), SORTIE 100 % MANUELLE
    (reactivate_sport). Lecture CHEAP (fichier) pour les chemins chauds (list_for/provisional_shown)."""
    return _load_sport_probation()


def auto_exclusions() -> tuple[set, set]:
    """(sports exclus DUR, marchés exclus). Un SPORT n'est écarté DUR (picks supprimés) que si sa calibration
    globale est mauvaise (SUR-CONFIANCE : n ≥ CALIB_MIN_N, écart ≤ CALIB_GAP_MAX). ⚠️ Les sports en ARRIÈRE-
    PLAN (tennis/basket, `background_sports`) NE SONT PLUS ici : leurs picks CIRCULENT (ROI simulé) — ils sont
    juste cachés/non publiés/séparés en aval (demande user 2026-07-24, ex-« probation » qui les supprimait).
    Marchés = per-sport (`excluded_markets`) ; union renvoyée pour l'aperçu."""
    c = calibration(min_conf=_MIN_CONF)
    by = c.get("by_sport") or {}
    sports = set()
    for name, g in by.items():
        gap = (g.get("win_rate") or 0) - (g.get("avg_conf") or 0)
        if (g.get("n") or 0) >= CALIB_MIN_N and gap <= CALIB_GAP_MAX:
            sports.add(_SPORT_FR.get(name, name.lower()))
    _sport_probation(by)                        # AUTO-ENTRÉE arrière-plan (effet persisté) — PAS dans ex_sports
    markets: set = set()
    for ms in _excluded_by_sport().values():
        markets |= ms
    return sports, markets


def combo_player_props_allowed() -> tuple[bool, dict]:
    """Props JOUEUR dans les COMBINÉS : EXCLUES par défaut (variance qui a plombé le ROI), RÉ-INTÉGRÉES
    automatiquement DÈS QUE les FANTÔMES le prouvent — famille « Props joueur » bien calibrée sur un
    VRAI échantillon (n ≥ CALIB_MIN_N ET écart réel−annoncé au-dessus du seuil de sur-confiance). Logique
    INVERSE d'auto_exclusions (exclu par défaut → inclus si prouvé), auto-révisable dans les deux sens.
    Les fantômes prédisent DÉJÀ des props joueur (bloc CALIB du scan) -> la donnée s'accumule même
    exclues. Renvoie (autorisé, {n, gap, win_rate, avg_conf}) — le dict sert au message du scan."""
    g = (calibration(min_conf=_MIN_CONF).get("by_market") or {}).get("Props joueur") or {}
    n = g.get("n") or 0
    gap = (g.get("win_rate") or 0) - (g.get("avg_conf") or 0)
    info = {"n": n, "gap": round(gap, 1), "win_rate": g.get("win_rate"), "avg_conf": g.get("avg_conf")}
    if n < CALIB_MIN_N or gap <= CALIB_GAP_MAX:            # pas assez de recul OU sur-confiance -> exclues
        return False, info
    # garde-fou ROI (paris joués, si assez) : les props joueur ne doivent pas perdre d'argent.
    for pm in (perf_breakdown().get("by_market") or []):
        if pm.get("label") == "Props joueur" and (pm.get("settled") or 0) >= CALIB_MIN_N \
                and (pm.get("roi") or 0) < 0:
            return False, info
    return True, info


def exclusions_report() -> dict:
    """TRANSPARENCE (lecture seule), PROPRE À CHAQUE SPORT (demande user 2026-07-02) : pour chaque sport
    et chaque famille de marché DE CE SPORT, dit si elle est ÉCARTÉE ou non et POURQUOI, avec les valeurs
    vs les SEUILS (n, écart de calibration réel−annoncé du sport, ROI global). + le cas « Props joueur en
    combiné » (global, logique INVERSE : exclu par défaut, réintégré si prouvé). Reflète l'état RÉEL des
    recommandations per-sport (excluded_markets), jamais filtré. Le ROI reste global (pas de ROI par
    (sport,marché)) mais l'écart de calibration, lui, est bien celui DU SPORT."""
    cal = calibration(min_conf=_MIN_CONF)
    bysport = cal.get("by_sport") or {}
    perf = {g.get("label"): g for g in (perf_breakdown().get("by_market") or [])}   # ROI = GLOBAL
    HARD = {"Corners"}
    _order = {"ban": 0, "gap": 1, "roi": 1, "excl": 1, "watch": 2, "ok": 3}
    _LABELS = [("foot", "Football", "⚽")]   # FOOT SEUL (user 2026-08-07 : tennis/basket retirés)
    sports_out = []
    for sp, fr, icon in _LABELS:
        g_sport = bysport.get(fr) or {}
        bm = g_sport.get("markets") or {}
        ex_m = excluded_markets(sp)
        rows, seen = [], set()
        names = set(bm)
        if sp == "foot":
            names.add("Corners")                        # garantir la ligne du ban dur même sans prédiction
        for name in names:
            seen.add(name)
            g, p = bm.get(name) or {}, perf.get(name) or {}
            n = g.get("n") or 0
            wr, ac = g.get("win_rate"), g.get("avg_conf")
            gap = (wr - ac) if (wr is not None and ac is not None) else None
            settled, roi = p.get("settled") or 0, p.get("roi")
            cal_roi = g.get("roi")          # ROI calibration (fantômes inclus) = celui QU'UTILISE le moteur
            excluded = name in ex_m
            if name in HARD:
                kind, reason = "ban", "Banni (marché le plus perdant — décision produit, jamais réintégré)."
            elif excluded and gap is not None and n >= CALIB_MIN_N and gap <= CALIB_GAP_MAX:
                kind, reason = "gap", (f"Sur-confiance sur ce sport : réussite {wr}% sous la confiance "
                                       f"annoncée {ac}% (écart {gap:+d} pts ≤ {CALIB_GAP_MAX}).")
            elif excluded and cal_roi is not None and n >= CALIB_MIN_N and cal_roi <= CALIB_ROI_MAX:
                kind, reason = "roi", (f"ROI réel {cal_roi:+d}% ≤ {CALIB_ROI_MAX}% (perd de l'argent même "
                                       f"bien calibré — ROI fantômes inclus).")
            elif excluded:
                # NI sur-confiance dure NI ROI perdant en ce moment -> l'exclusion est MAINTENUE par
                # l'HYSTÉRÉSIS : le marché a récupéré au-dessus des seuils d'exclusion mais pas encore
                # NETTEMENT (seuils de retour), donc on le garde écarté pour éviter le flottement jour à jour.
                _hy = []
                if gap is not None:
                    _hy.append(f"écart {gap:+d} (retour à ≥ {CALIB_GAP_BACK})")
                if cal_roi is not None:
                    _hy.append(f"ROI {cal_roi:+d}% (retour à ≥ {CALIB_ROI_BACK}%)")
                kind, reason = "excl", ("Maintenu écarté (hystérésis anti-flottement) : "
                                        + " ; ".join(_hy) + "." if _hy
                                        else "Maintenu écarté (hystérésis anti-flottement).")
            elif n < CALIB_MIN_N:
                kind, reason = "watch", (f"Sous surveillance — échantillon insuffisant sur ce sport "
                                         f"({n}/{CALIB_MIN_N} prédictions) : on ne conclut pas sur du bruit.")
            else:
                _g = f"{gap:+d}" if gap is not None else "?"
                if roi is not None and settled and settled < CALIB_MIN_N and roi <= CALIB_ROI_MAX:
                    _rn = (f" ; ROI {roi:+d}% mais sur {settled} paris joués seulement "
                           f"(< {CALIB_MIN_N} : non concluant)")
                elif roi is not None and settled >= CALIB_MIN_N:
                    _rn = f" ; ROI réel {roi:+d}% (au-dessus du seuil {CALIB_ROI_MAX}%)"
                else:
                    _rn = ""
                kind, reason = "ok", f"Fiable sur ce sport : bien calibré (écart {_g} pts > {CALIB_GAP_MAX}){_rn}."
            rows.append({"market": name, "excluded": excluded, "kind": kind, "reason": reason,
                         "n": n, "win_rate": wr, "avg_conf": ac, "gap": gap, "roi": roi, "settled": settled})
        rows.sort(key=lambda r: (_order.get(r["kind"], 9), -(r["n"] or 0)))
        if rows:
            sports_out.append({"key": sp, "label": fr, "icon": icon, "rows": rows,
                               "n_excluded": sum(1 for r in rows if r["excluded"])})
    pp_ok, pp = combo_player_props_allowed()
    return {"sports": sports_out, "player_props": {"allowed": pp_ok, **pp},
            "thresholds": {"min_n": CALIB_MIN_N, "gap_max": CALIB_GAP_MAX, "roi_max": CALIB_ROI_MAX},
            "journal": exclusion_journal()}


# ─── AJUSTEMENTS AUTOMATIQUES de marché (auto-exclu / auto-réintégré), DATÉS ───────────────────────
# Ces changements MODIFIENT la création des tickets (simples & combinés) : quand le système écarte ou
# ré-intègre TOUT SEUL un marché, la sélection change. On les surface comme REPÈRES « auto » (ambrés)
# sur les courbes — à côté des jalons méthodo manuels (MODEL_MILESTONES, bleus) — et dans un journal
# lisible sous « Marchés écartés ». Source = data/learning_log.json (photo QUOTIDIENNE déjà prise par
# app/learning.py à chaque scan) : on n'ajoute AUCUN nouveau fichier ni tâche, on RECONSTRUIT la
# chronologie en diffant les photos jour à jour. Le ban dur « Corners » (décision produit) est un jalon
# méthodo, PAS un ajustement auto -> filtré ici pour ne pas doublonner le repère « Corners bannis ».
_EXCL_HARD_BAN = set(_HARD_BAN_FOOT)   # Corners + BTTS = bans DURS (jalons méthodo manuels) -> pas de doublon auto


def _excl_reason(e: dict) -> str:
    """Phrase courte et claire pour un événement d'ajustement (repère + journal)."""
    if e.get("baseline"):
        return "Écarté au démarrage du suivi (état initial des exclusions par sport)."
    if e.get("action") == "réintégré":
        return "Ré-intégré automatiquement : repassé au-dessus des seuils de fiabilité sur ce sport."
    return "Écarté automatiquement : sur-confiance ou ROI perdant prouvés sur ce sport (échantillon suffisant)."


_EXCL_DEBOUNCE_DAYS = 2   # un état d'exclusion qui tient < 2 jours ET qui REVIENT à l'état précédent =
#                           FLOTTEMENT (artefact du seuil unique, corrigé par l'hystérésis) -> absorbé de
#                           l'AFFICHAGE des repères. Décision user 2026-07-16 « lisser le flottement ».


def _debounce_series(series: list, min_persist: int = _EXCL_DEBOUNCE_DAYS) -> list:
    """Lisse une série d'états booléens (excluded/inclus par jour) : absorbe les « runs » plus courts que
    `min_persist` qui REVIENNENT à l'état précédent (blip d'aller-retour = flottement). Ne touche JAMAIS
    au 1er run (baseline) ni au DERNIER (état courant = vérité du jour). Itère jusqu'à stabilité. Pur
    affichage : ne modifie pas le learning_log, seulement la chronologie reconstruite des repères."""
    s = list(series)
    if len(s) <= 2:
        return s
    changed = True
    while changed:
        changed = False
        runs, i = [], 0                          # runs = [start, end_exclusif, valeur]
        while i < len(s):
            j = i
            while j < len(s) and s[j] == s[i]:
                j += 1
            runs.append([i, j, s[i]])
            i = j
        for k in range(1, len(runs) - 1):        # runs INTERNES seulement (ni baseline ni dernier)
            st, en, _ = runs[k]
            if (en - st) < min_persist and runs[k - 1][2] == runs[k + 1][2]:
                for x in range(st, en):          # blip qui revient -> réabsorbé dans l'état précédent
                    s[x] = runs[k - 1][2]
                changed = True
                break
    return s


def _exclusion_transitions() -> list[dict]:
    """Reconstruit la CHRONOLOGIE des ajustements auto à partir des photos quotidiennes du journal
    d'apprentissage. Pour chaque jour où l'ensemble écarté d'un sport (ou les props joueur en combiné)
    change vs la veille -> un événement daté {date, sport, market, action, baseline, reason}. Le premier
    jour connu = BASELINE (état de départ). Filtre le ban dur « Corners ». [] si pas d'historique.
    LISSAGE (décision user 2026-07-16) : les séries d'exclusion sont DÉBOUNCÉES avant diff -> un marché
    qui flotte (écarté/réintégré/ré-écarté sur des jours isolés, ex. basket « Vainqueur ») ne produit QUE
    sa transition NETTE, plus 3 repères parasites. Le learning_log reste intact (pur affichage)."""
    from app import learning                    # import local : évite le cycle analyses<->learning
    log = learning._load()
    if not log:
        return []
    days = sorted(log)
    sports = ("foot", "tennis", "basket")
    per_day_ex = [{sp: set((log[d].get("exclusions") or {}).get(sp) or []) - _EXCL_HARD_BAN
                   for sp in sports} for d in days]
    props_raw = [bool(log[d].get("combo_props_allowed")) for d in days]
    # Séries bool par (sport, marché), DÉBOUNCÉES -> gomme les allers-retours d'un jour (flottement).
    all_mk = {sp: sorted({mk for dd in per_day_ex for mk in dd[sp]}) for sp in sports}
    smooth = {sp: {mk: _debounce_series([mk in per_day_ex[i][sp] for i in range(len(days))])
                   for mk in all_mk[sp]} for sp in sports}
    props = _debounce_series(props_raw)
    # États quotidiens LISSÉS reconstruits depuis les séries débouncées.
    day_ex = [{sp: {mk for mk in all_mk[sp] if smooth[sp][mk][i]} for sp in sports}
              for i in range(len(days))]
    evs, prev = [], None
    for i, day in enumerate(days):
        cur, pp = day_ex[i], props[i]
        if prev is None:                         # ── baseline (état initial, pas un « changement »)
            for sp in sports:
                for mk in sorted(cur[sp]):
                    evs.append({"date": day, "sport": sp, "market": mk, "action": "exclu", "baseline": True})
            if not pp:
                evs.append({"date": day, "sport": "combo", "market": "Props joueur",
                            "action": "exclu", "baseline": True})
        else:
            for sp in sports:
                for mk in sorted(cur[sp] - prev["ex"][sp]):
                    evs.append({"date": day, "sport": sp, "market": mk, "action": "exclu", "baseline": False})
                for mk in sorted(prev["ex"][sp] - cur[sp]):
                    evs.append({"date": day, "sport": sp, "market": mk, "action": "réintégré", "baseline": False})
            if pp != prev["pp"]:
                evs.append({"date": day, "sport": "combo", "market": "Props joueur",
                            "action": ("réintégré" if pp else "exclu"), "baseline": False})
        prev = {"ex": cur, "pp": pp}
    for e in evs:
        e["reason"] = _excl_reason(e)
    return evs


def exclusion_events() -> list:
    """Les ajustements auto (hors baseline) au format REPÈRE de courbe :
    (date, libellé court, explication, portée, sport, "auto"). Fusionnés avec MODEL_MILESTONES à
    l'affichage -> pastilles ambrées datées sur les courbes. Un marché écarté/réintégré vaut pour le
    simple ET le combiné (portée « both ») ; les props joueur ne concernent que le combiné."""
    out = []
    for e in _exclusion_transitions():
        if e.get("baseline"):
            continue
        sp = e["sport"]
        scope = "combo" if sp == "combo" else "both"
        sport = sp if sp in ("foot", "tennis", "basket") else "all"
        verb = "réintégré" if e["action"] == "réintégré" else "écarté"
        label = f'{e["market"]} {verb}'
        expl = f'{_SPORT_NOM.get(sp, sp)} : {e["reason"]}'
        out.append((e["date"], label, expl, scope, sport, "auto"))
    return out


def exclusion_journal() -> dict:
    """Journal LISIBLE (récent d'abord) de tous les ajustements auto, baseline incluse, + date de début
    de suivi. Pour le panneau « Marchés écartés » (transparence complète)."""
    from app import learning
    log = learning._load()
    started = min(log) if log else None
    evs = sorted(_exclusion_transitions(), key=lambda e: (e.get("date") or "", 0 if e.get("baseline") else 1),
                 reverse=True)
    return {"started": started, "events": evs}


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
    """pairs = [(prob, won_bool[, odds, played])] -> tranches de confiance : confiance annoncée vs
    réussite réelle (TOUTES prédictions, fantômes inclus), + **ROI** calculé sur les seuls paris JOUÉS
    (mise plate 1u ; jamais les fantômes). mae pondéré + verdict (good ≤6 pts sinon over/under)."""
    buckets = {b: {"n": 0, "won": 0, "conf": 0.0, "pf": 0.0, "stk": 0} for b in _CALIB_BANDS}
    for pair in pairs:
        prob, won = pair[0], pair[1]
        odds = pair[2] if len(pair) > 2 else None
        played = pair[3] if len(pair) > 3 else False
        band = next(((lo, hi) for lo, hi in _CALIB_BANDS if lo <= prob < hi), None)
        if not band:
            continue
        bk = buckets[band]
        bk["n"] += 1
        bk["conf"] += prob
        bk["won"] += 1 if won else 0
        if played and odds:                       # ROI : UNIQUEMENT les paris réellement joués
            bk["stk"] += 1
            bk["pf"] += (float(odds) - 1) if won else -1.0
    rows, total, mae_num = [], 0, 0.0
    for lo, hi in _CALIB_BANDS:
        bk = buckets[(lo, hi)]
        n = bk["n"]
        if not n:
            continue
        wr, conf = round(100 * bk["won"] / n), round(bk["conf"] / n)
        rows.append({"lo": lo, "hi": hi, "n": n, "won": bk["won"], "win_rate": wr,
                     "avg_conf": conf, "gap": wr - conf,
                     # ROI masqué sous 3 paris joués (1-2 = bruit trompeur, ex. +96% sur 1 pari)
                     "roi": round(100 * bk["pf"] / bk["stk"]) if bk["stk"] >= 3 else None,
                     "roi_n": bk["stk"]})
        total += n
        mae_num += abs(wr - conf) * n
    mae = round(mae_num / total, 1) if total else None
    tot_pf = sum(buckets[b]["pf"] for b in _CALIB_BANDS)
    tot_stk = sum(buckets[b]["stk"] for b in _CALIB_BANDS)
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
            "win_rate": wr, "avg_conf": ac,
            "roi": round(100 * tot_pf / tot_stk) if tot_stk >= 3 else None, "roi_n": tot_stk}


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
    n_shadow = n_played = 0   # part fantômes (calibration seule) vs paris JOUÉS (= ceux des gains/ROI)
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
        sport = d.get("sport") or ""
        mid = os.path.basename(p)[len(sport) + 1:-5]
        # PRÉDICTIONS FANTÔMES (shadow) : prédictions de l'analyste NON jouées, réglées après match ->
        # calibrage sur TOUT le spectre de proba (corrige le biais de sélection « 1 pari joué/match »).
        # TOUS sports, CdM INCLUSE (ce sont des prédictions de MARCHÉ individuelles, calibrables). Jamais
        # dans l'affichage/ROI/forme — UNIQUEMENT ici.
        for _sp in (d.get("shadow") or []):
            _r, _pr = _sp.get("result"), _sp.get("prob")
            if _r in ("won", "lost") and _pr is not None and _pr >= min_conf:
                _mk = _MARKET_FAMILY.get((_sp.get("code") or "").split()[0] if _sp.get("code") else "", "Autre")
                items.append((_pr, _r == "won", sport, _mk, _sp.get("cote"), False))
                if _pr >= _CALIB_BANDS[0][0]:      # compté seulement s'il entre dans une bande (cohérent avec n)
                    n_shadow += 1
        if _is_world_cup(d):         # CdM : paris simples/combiné EXCLUS (shadow ci-dessus INCLUS).
            continue
        stored = d.get("bets") or []
        if not stored:
            continue
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
            items.append((prob, res == "won", sport, mkt, b.get("odds"), True))
            if prob >= _CALIB_BANDS[0][0]:        # idem : cohérent avec le n des bandes
                n_played += 1

    out = _calib_agg([(p, w, o, pl) for p, w, _s, _m, o, pl in items])
    out["n_shadow"] = n_shadow     # fantômes (calibration UNIQUEMENT)
    out["n_played"] = n_played     # paris JOUÉS (= base des gains/ROI)
    _SPL = {"foot": "Football", "tennis": "Tennis", "basket": "Basket"}
    by_sport = {}            # par SPORT, avec les TYPES DE PARIS du sport en SOUS-CATÉGORIE (`markets`)
    for sp in ("foot", "tennis", "basket"):
        sub = [(p, w, o, pl) for p, w, s, _m, o, pl in items if s == sp]
        if not sub:
            continue
        agg = _calib_agg(sub)
        mkts = {}            # sous-catégories : chaque type de pari DE CE SPORT (≥3 paris)
        for mk in sorted({m for _p, _w, s, m, _o, _pl in items if s == sp}):
            msub = [(p, w, o, pl) for p, w, s, m, o, pl in items if s == sp and m == mk]
            if len(msub) >= 3:
                mkts[mk] = _calib_agg(msub)
        agg["markets"] = mkts
        by_sport[_SPL[sp]] = agg
    by_market = {}           # par FAMILLE (tous sports) -> sert à l'optimisation (auto_exclusions)
    for mk in sorted({m for _p, _w, _s, m, _o, _pl in items}):
        sub = [(p, w, o, pl) for p, w, _s, m, o, pl in items if m == mk]
        if len(sub) >= 3:        # marché avec trop peu de paris -> pas affiché (bruit)
            by_market[mk] = _calib_agg(sub)
    out["by_sport"] = by_sport
    out["by_market"] = by_market
    if sig is not None:
        _CALIB_RES_CACHE[min_conf] = (sig, out)
    return out


def market_overview(min_n: int = 30) -> list:
    """APERÇU PAR MARCHÉ (foot) au niveau SEUIL (code complet : « OVER 2.5 » ≠ « OVER 3.5 ») ventilé en 3 vues
    (demande user 2026-08-20) :
      👻 FANTÔME  = TOUTES les prédictions réglées du marché (réussite réelle + confiance annoncée = calibration) ;
      💎 VALUE    = celles à VALUE>0 (conf×cote−1>0 = ce qu'on jouerait) + leur ROI ;
      ⭐ CONFIANCE = value>0 ET confiance ≥75 % + leur ROI.
    Under/Over restent SÉPARÉS PAR SEUIL (le regroupement famille masquait qu'UNDER 2.5 gagne mais UNDER 3.5/4.5
    perdent : cotes trop courtes = fausse value). LECTURE des sidecars (fantômes NON biaisés par la sélection ;
    confiance = ANNONCÉE, cohérent avec la calibration). Renvoie une liste triée [famille, volume] :
    [{code, fam, n, win, conf, val_n, val_roi, cf_n, cf_roi}]. Sert la vue Résultats›Analyse."""
    groups: dict = {}
    for p in glob.glob(os.path.join(DIR, "foot_*.json")):       # app 100 % foot
        d = _meta_load(p)
        if not d:
            continue
        preds = list(d.get("shadow") or [])
        if not _is_world_cup(d):                                # CdM : fantômes OK, paris exclus (comme calibration)
            for b in (d.get("bets") or []):
                preds.append({"prob": b.get("prob"), "cote": b.get("odds") or b.get("cote"),
                              "result": b.get("result"), "code": b.get("code")})
        for s in preds:
            prob, cote, res, code = s.get("prob"), s.get("cote"), s.get("result"), s.get("code")
            if res in ("won", "lost") and prob and cote and cote > 1 and code:
                groups.setdefault(str(code).upper(), []).append((prob / 100.0, float(cote), res == "won"))

    def _roi(sub):
        if not sub:
            return (0, None)
        ret = sum(o for _p, o, w in sub if w)
        return (len(sub), round((ret - len(sub)) / len(sub) * 100))

    out = []
    for code, lst in groups.items():
        if len(lst) < min_n:
            continue
        n = len(lst)
        val = [x for x in lst if x[0] * x[1] - 1 > 0]           # value>0 = jouable
        cf = [x for x in val if x[0] >= 0.75]                   # + confiance ≥75 %
        vn, vr = _roi(val)
        cn, cr = _roi(cf)
        out.append({"code": code, "fam": _MARKET_FAMILY.get(code.split()[0], "Autre"), "n": n,
                    "win": round(sum(1 for x in lst if x[2]) / n * 100),
                    "conf": round(sum(x[0] for x in lst) / n * 100),
                    "val_n": vn, "val_roi": vr, "cf_n": cn, "cf_roi": cr})
    out.sort(key=lambda r: (r["fam"], -r["n"]))                 # groupé par famille, puis volume décroissant
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


# Tranches de cote AFFINÉES dans la zone RÉELLE des paris foot (user 2026-08-08) : 100 % des paris joués
# (et même toute la population diagnostique, n=221) sont entre 1.10 et 1.68 — les anciennes tranches larges
# (1.00–1.30 … + deux tranches ≥1.70 TOUJOURS VIDES) noyaient l'info (ex. la faiblesse ROI des cotes <1.25,
# invisible car fondue dans « 1.00–1.30 »). Découpage fin par pas de 0.10 sur 1.25–1.55 (le cœur, cote
# médiane 1.37) + une tranche « 1.70 + » filet (masquée tant qu'elle est vide -> catch-all outlier futur).
_ODDS_BUCKETS = ((1.0, 1.25, "< 1.25"), (1.25, 1.35, "1.25–1.35"), (1.35, 1.45, "1.35–1.45"),
                 (1.45, 1.55, "1.45–1.55"), (1.55, 1.70, "1.55–1.70"), (1.70, 99.0, "1.70 +"))
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


def _result_badge(m: dict | None) -> str:
    """Bandeau résultat HEADLINE du match après règlement : ✅ réussi / ❌ perdu / ➖ remboursé, ou
    le score seul si non vérifiable. '' si pas encore réglé. Match CdM : le pari PHARE est le COMBINÉ
    -> le bandeau suit SON résultat, jamais le simple (sinon « Pari réussi » trompeur quand le combiné
    perd mais que le simple passe, ex. Ghana-Panama 1-0 : combiné perdu / simple gagné)."""
    m = m or {}
    res = m.get("result") or {}
    if not res:
        return ""
    combo = m.get("combo") or {}
    sc = res.get("score") or ""
    sco = f'<span class="da-res-sc">{html.escape(sc)}</span>' if sc else ""
    if combo.get("legs"):                        # CdM : le bandeau suit le COMBINÉ (pari phare)
        pr = combo.get("result")
        cls, txt = {"won": ("win", "✅ Pari réussi"), "lost": ("lose", "❌ Pari perdu"),
                    "push": ("push", "➖ Pari remboursé"),
                    "void": ("push", "➖ Pari remboursé")}.get(pr, ("nv", "Résultat connu"))
        return f'<div class="da-res da-res-{cls}">{txt} {sco}</div>'
    # SIMPLE : « Pari réussi/perdu » UNIQUEMENT si un pari a été JOUÉ (couche mécanique figée). Sinon
    # ABSTENTION -> bandeau NEUTRE (sinon « ✅ Pari réussi » sur un match non joué = trompeur). Le VERDICT
    # suit le pari JOUÉ (`played_result` -> stat_bet), PAS `pick_result` (pick brut du .md, divergent depuis
    # la refonte mécanique 2026-08-29 : DC 1X joué/gagné mais pick_result notait Under 3.5 -> « perdu » à tort).
    sb = stat_bet(m)
    if isinstance(sb, dict):
        pr = played_result(m)
        cls, txt = {"won": ("win", "✅ Pari réussi"), "lost": ("lose", "❌ Pari perdu"),
                    "push": ("push", "➖ Pari remboursé"),
                    "void": ("push", "➖ Pari remboursé")}.get(pr, ("nv", "Résultat connu"))
    else:                                        # vraie abstention : bandeau NEUTRE, sans langage « pari »
        cls, txt = "nv", "Match analysé · pas de pari"
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
    # Lien Unibet RETIRÉ de la carte (demande utilisateur 2026-06-16).
    return f'<div class="da-links">{"".join(btns)}</div>' if btns else ""


def links_html(sport: str, match_id) -> str:
    """Bannières SofaScore / Unibet d'un match (depuis le sidecar), à poser SUR la carte."""
    return _links_bar(meta(sport, match_id))


def render(sport: str, match_id, skip_verdict: bool = False, card_details: bool = False) -> str | None:
    """HTML prêt à afficher de l'analyse de ce match, ou None si pas d'analyse. En tête : bandeau
    résultat ✓/✗ (si réglé). Les bannières SofaScore/Unibet ne sont PLUS ici : elles sont portées
    par la carte (cf. web._links_for_url) pour éviter un doublon à l'ouverture de l'analyse.
    `skip_verdict` : masque « 🎯 Pourquoi ce pari » (abstentions -> le bloc « 🧪 provisoire » le porte déjà).
    `card_details` : dépli de CARTE épuré (le pli « 💡 Pourquoi » porte le raisonnement -> verdict masqué,
    faits/tendances/H2H regroupés sous « 🔍 Voir les détails », Mise visible). PURE PRÉSENTATION."""
    md = load(sport, match_id)
    if not md:
        return None
    m = meta(sport, match_id) or {}
    return _result_badge(m) + to_html(md, skip_verdict=skip_verdict, card_details=card_details)
