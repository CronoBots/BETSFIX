#!/usr/bin/env python3
"""CONTRÔLE QUALITÉ D'ANALYSE (user 2026-08-25) — vérifie que CHAQUE match du jour est analysé « comme en
période gagnante » : couverture 100 %, profondeur par match, et taux de CONVERSION (analysé -> pari).

Priorité user : être SÛR que chaque match du programme est bien analysé (pas sauté, pas bâclé). Lecture SEULE.

3 indicateurs :
  1. COUVERTURE : chaque match du programme a-t-il une analyse ? Un match NON analysé n'est un PROBLÈME que si
     sa vague KO−1 h est PASSÉE (sinon il est juste « en attente » de sa vague, normal sous Option B).
  2. PROFONDEUR : `.md` présent + substantiel (>= MIN_MD o) + panel exécuté (`validation`). Flag les stubs.
  3. CONVERSION : paris retenus / matchs analysés. ~40-50 % = analyse profonde (période gagnante) ;
     ~10 % = signal d'ALERTE (analyse redevenue bâclée, cf. dilution 13-23/08).

Usage :  python tools/analysis_quality.py            (jour courant)
         python tools/analysis_quality.py --date 2026-08-25
Sortie : rapport lisible + code retour 1 si ALERTE (match manqué OU conversion basse), 0 sinon.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Console Windows en cp1252 : les ═ / ✅ / emojis des logs crasheraient (UnicodeEncodeError) -> exit 1
# indistinct d'une vraie ALERTE. On force la sortie en UTF-8 (idiome projet, cf. generate_analyses.py).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app import analyses as A  # noqa: E402

MIN_MD = 2500            # octets : en-dessous, l'analyse est un stub (les vraies font ~2900-5900 o)
CONV_ALERT = 0.25        # conversion < 25 % -> alerte (gagnante ~0.49 ; diluée ~0.11)
WAVE_LEAD_H = 0.75       # la vague analyse à KO-1.0 h ; on ne crie "manqué" qu'APRÈS + une marge de ~15 min
#                          (KO-0.75 h). ⚠️ Doit être < 1.0 : un seuil >= au lead de la vague (ex. l'ancien 1.6)
#                          déclarait "manqué" AVANT que la vague ne tourne -> faux positif (Atletico Grau
#                          2026-09-01 : alerté à KO-1.6h=20:24 alors que la vague était prévue à 21:00).


def _prog_path() -> str:
    return os.path.join(os.path.dirname(A.DIR), "day_programme.json")


def _load_programme() -> dict:
    try:
        p = json.load(open(_prog_path(), encoding="utf-8"))
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _send_owner(text: str) -> bool:
    """Envoie une alerte EN PRIVÉ au propriétaire (data/owner_chat.txt) — JAMAIS le canal abonnés. Best-effort."""
    chat_p = os.path.join(os.path.dirname(A.DIR), "owner_chat.txt")
    if not os.path.exists(chat_p):
        return False
    try:
        from app import notify
        chat = open(chat_p, encoding="utf-8").read().strip()
        tok, _ = notify._config()
        if not (tok and chat):
            return False
        import httpx
        httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                   json={"chat_id": chat, "text": text[:3900]}, timeout=15)
        return True
    except Exception as exc:
        print(f"(alerte privée ignorée : {exc})")
        return False


def _played_bet(d: dict) -> dict | None:
    """LE PARI JOUÉ d'un match, valable AVANT comme APRÈS règlement (fix 2026-09-02).
    ⚠️ `retained_bet` seul ne suffit PAS : une fois `stat_bet` gelé au règlement, il ne reconstruit
    plus le pari mécanique et retombe sur le PICK BRUT du tableau .md (autre marché) — ou sur None.
    La QC annonçait alors « PARI JOUÉ — Moins de 3.5 buts @1.37 » (et le VALIDAIT) alors que le pari
    réellement joué était « Double chance 1X @1.15 » (Sheffield-Bolton), et comptait Torino en
    abstention alors qu'un pari avait été joué -> taux de conversion faussé. Ça mordait sur tout match
    dont la fiche part APRÈS le règlement (vague manquée, rattrapage). Même règle que
    `card_data.build_prono_card` (commit 11fcd37) : sur un réglé, la source unique est `stat_bet`."""
    if not isinstance(d, dict):
        return None
    if A.is_settled(d):
        sb = A.stat_bet(d)
        if sb:
            return sb
    return A.retained_bet("foot", str(d.get("id") or ""))


_ALERT_STATE = os.path.join(os.path.dirname(A.DIR), "quality_alerts.json")


def _new_issues(day: str, keys: list[str]) -> list[str]:
    """DÉDUP : ne renvoie que les problèmes PAS ENCORE alertés aujourd'hui (évite le spam à chaque vague)."""
    try:
        st = json.load(open(_ALERT_STATE, encoding="utf-8"))
    except Exception:
        st = {}
    done = set(st.get(day) or [])
    new = [k for k in keys if k not in done]
    if new:
        st = {day: sorted(done | set(new))}          # on ne garde que le jour courant
        try:
            json.dump(st, open(_ALERT_STATE, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return new


def _sidecar_for(mid: str, home: str, away: str):
    """Sidecar (json) + chemin .md d'un match du programme : par id d'abord, repli par home/away."""
    jp = os.path.join(A.DIR, f"foot_{mid}.json")
    if os.path.exists(jp):
        try:
            return json.load(open(jp, encoding="utf-8")), jp[:-5] + ".md"
        except Exception:
            pass
    hn, an = (home or "").lower(), (away or "").lower()
    for p in glob.glob(os.path.join(A.DIR, "foot_*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("home", "") or "").lower() == hn and (d.get("away", "") or "").lower() == an:
            return d, p[:-5] + ".md"
    return None, None


def run(date: str | None = None, send_alert: bool = False) -> int:
    prog = _load_programme()
    matches = prog.get("matches") or []
    day = date or prog.get("date") or ""
    if date:
        matches = [m for m in matches if (m.get("start") or "")[:10] == date]
    now = datetime.now(timezone.utc).timestamp()

    analysed = pending = missed = 0
    bets = 0
    shallow = []          # matchs analysés mais .md manquant/stub
    missed_list = []      # matchs dont la vague est passée SANS analyse
    print(f"═══ CONTRÔLE QUALITÉ D'ANALYSE — {day or 'jour courant'} ═══")
    print(f"Programme : {len(matches)} match(s)\n")
    for m in matches:
        mid = str(m.get("id") or "")
        home, away = m.get("home", ""), m.get("away", "")
        name = m.get("name") or f"{home} - {away}"
        d, md = _sidecar_for(mid, home, away)
        is_analysed = bool(d and (d.get("bets") or d.get("shadow") or d.get("abstained") or d.get("stat_bet")))
        ko = _ts(m.get("start"))
        wave_due = ko is not None and now >= (ko - WAVE_LEAD_H * 3600)   # la vague aurait dû tourner
        if is_analysed:
            analysed += 1
            rb = _played_bet(d) if d else None      # réglé -> stat_bet (sinon pick brut / None)
            has_bet = bool(rb and rb.get("sel"))
            if has_bet:
                bets += 1
            # profondeur
            md_ok = bool(md and os.path.exists(md) and os.path.getsize(md) >= MIN_MD)
            panel = bool(d.get("validation") or d.get("votes")) if d else False
            flag = ""
            if not md_ok:
                shallow.append(name); flag = "  ⚠️ .md manquant/stub"
            tag = "PARI" if has_bet else ("abstention" if (d or {}).get("abstained") else "analysé")
            depth = "profond" + ("+panel" if panel else "") if md_ok else "SUPERFICIEL?"
            print(f"  ✅ {name[:34]:34} {tag:11} [{depth}]{flag}")
        elif wave_due:
            missed += 1; missed_list.append(name)
            print(f"  ❌ {name[:34]:34} NON ANALYSÉ (vague KO−1 h PASSÉE) — MANQUÉ")
        else:
            pending += 1
            lead = (ko - now) / 3600 if ko else 0
            print(f"  ⏳ {name[:34]:34} en attente de sa vague (KO dans {lead:.1f} h)")

    conv = bets / analysed if analysed else 0
    print("\n── BILAN ──")
    print(f"  Couverture : {analysed} analysé(s) · {pending} en attente de vague · {missed} MANQUÉ(s)")
    print(f"  Conversion : {bets} pari(s) / {analysed} analysé(s) = {100*conv:.0f}%  "
          f"(gagnante ~49 % · diluée ~11 %)")
    if shallow:
        print(f"  Profondeur : ⚠️ {len(shallow)} match(s) au .md manquant/stub : {', '.join(s[:20] for s in shallow)}")
    else:
        print(f"  Profondeur : ✅ tous les .md sont substantiels")

    alert = []
    if missed:
        alert.append(f"{missed} match(s) MANQUÉ(s) (vague passée sans analyse) : {', '.join(missed_list)}")
    # CONVERSION : jugée SEULEMENT en fin de journée (pending == 0). Tôt le matin, seules les abstentions du
    # slate jour sont analysées (les vagues n'ont pas tourné) -> conversion trompeuse. Les abstentions du matin
    # seront re-analysées près du KO (Option B) et peuvent devenir des paris.
    if pending == 0 and analysed >= 4 and conv < CONV_ALERT:
        alert.append(f"conversion {100*conv:.0f}% < {100*CONV_ALERT:.0f}% -> analyse peut-être superficielle")
    if shallow:
        alert.append(f"{len(shallow)} analyse(s) superficielle(s) (.md stub)")
    print()
    if alert:
        print("🔴 ALERTE :")
        for a in alert:
            print(f"   - {a}")
        # ENVOI PRIVÉ (owner) — dédupliqué : chaque problème n'alerte qu'UNE fois par jour.
        keys = [f"missed:{n}" for n in missed_list] + [f"shallow:{n}" for n in shallow]
        if pending == 0 and analysed >= 4 and conv < CONV_ALERT:
            keys.append("conversion")
        new = _new_issues(day, keys)
        if send_alert and new:
            msg = (f"⚠️ BETSFIX — contrôle qualité {day}\n\n" + "\n".join(f"• {a}" for a in alert)
                   + f"\n\nCouverture {analysed} analysé · {missed} manqué · conversion {100*conv:.0f}%.")
            if _send_owner(msg):
                print(f"   → alerte privée envoyée ({len(new)} nouveau(x) problème(s)).")
        return 1
    if pending:
        print(f"🟡 OK pour l'instant ({pending} match(s) encore à analyser par leur vague — à revérifier plus tard).")
    else:
        print("🟢 OK : couverture complète, profondeur et conversion saines.")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# FICHE QC PAR MATCH (user 2026-09-01) — chaque match analysé envoie EN PRIVÉ (owner) une fiche pour
# vérifier PROFESSIONNELLEMENT la qualité de l'analyse et le choix fait (pari joué / abstention).
# Envoyée UNE fois par match, à sa vague (décision FINALE), dédupliquée par jour. Lecture seule.
# ─────────────────────────────────────────────────────────────────────────────
_QC_SENT = os.path.join(os.path.dirname(A.DIR), "match_qc_sent.json")

try:
    import zoneinfo
    _BX = zoneinfo.ZoneInfo("Europe/Brussels")
except Exception:
    _BX = None


def _ko_bx(start: str) -> str:
    """Heure de coup d'envoi en Europe/Brussels (« HH:MM »), depuis le start ISO (UTC) du programme."""
    try:
        dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.astimezone(_BX) if _BX else dt).strftime("%H:%M")
    except Exception:
        return ""


_SRC_TOKENS = ("fotmob", "flashscore", "sportradar", "understat", "sofascore", "espn", "livescore")


def _md_text(md: str | None) -> str:
    try:
        return open(md, encoding="utf-8").read() if (md and os.path.exists(md)) else ""
    except OSError:
        return ""


def _md_section(txt: str, needle: str) -> str:
    """Corps d'une section « ## … <needle> … » du .md (jusqu'au prochain « ## »), lignes machine
    (PICK:/PROV:/CALIB:/LIB:) et commentaires retirés. '' si section absente."""
    import re
    for part in re.split(r"(?m)^##\s*", txt):
        head = part.splitlines()[0] if part else ""
        if needle.lower() in head.lower():
            body = part.split("\n", 1)[1] if "\n" in part else ""
            keep = [ln for ln in body.splitlines()
                    if ln.strip() and not re.match(r"^\s*(PICK|PROV|CALIB|LIB)\s*:", ln)
                    and not ln.strip().startswith("<!--")]
            return "\n".join(keep).strip()
    return ""


# Citation d'une ANCRE sharp chiffrée dans la prose (« sharp (43 %) », « Pinnacle 82 % », « sharp à 66 % »…).
_SHARP_CITE = re.compile(r"(sharp|pinnacle)[^.\n]{0,20}?\d{1,3}\s*%", re.I)


def _qc_collect(d: dict, md: str | None, mdtxt: str) -> dict:
    """Rassemble les signaux BRUTS d'un match (lecture seule). Repli sur le .md quand le JSON (fiche
    d'abstention) ne porte pas sources/sharp -> pas de faux ❌."""
    srcs = d.get("sources")
    src_names = srcs if isinstance(srcs, list) else (list(srcs.keys()) if isinstance(srcs, dict) else [])
    if not src_names and mdtxt:
        low = mdtxt.lower()
        src_names = [t for t in _SRC_TOKENS if t in low]
    md_ko = (os.path.getsize(md) / 1000.0) if (md and os.path.exists(md)) else 0.0
    return {
        "src_names": src_names, "n_src": len(src_names),
        "sharp_ok": (bool(d.get("sharp_map")) or ("pinnacle" in mdtxt.lower())) and not d.get("no_sharp"),
        # ⚠️ FABRICATION D'ANCRE : l'analyse cite un « sharp/Pinnacle XX % » alors qu'AUCUNE ancre réelle n'existe
        # (`sharp_map` vide) -> chiffre inventé pour justifier une EV (bug Burnley-Middlesbrough 2026-09-02).
        "sharp_map_empty": not bool(d.get("sharp_map")),
        "sharp_cited": bool(_SHARP_CITE.search(mdtxt or "")),
        # ancre REJETÉE par le garde anti-résolution-fausse (favori sharp opposé au marché, cf. build_dossier).
        "sharp_conflict": bool(d.get("sharp_conflict")),
        "n_omap": len(d.get("omap") or {}),
        "md_ko": md_ko, "md_ok": md_ko * 1000 >= MIN_MD,
        "panel": bool(d.get("validation") or d.get("votes")) or ("Paris classés" in mdtxt),
        "n_shadow": len(d.get("shadow") or []),
        "has_facts": bool(_md_section(mdtxt, "Les faits")),
        "final": bool(d.get("prematch_done") or d.get("abstained") or A.is_settled(d)
                      or (isinstance(d.get("published_bet"), dict) and d["published_bet"].get("sel"))),
    }


# Bandes de cote par tier (source de vérité = les sélecteurs mécaniques).
try:
    from app import confidence_pick as _CP, value_pick as _VP
    _BANDS = {"confiance": (_CP.COTE_LO, _CP.COTE_HI), "value": (_VP.COTE_LO, _VP.COTE_HI)}
except Exception:
    _BANDS = {"confiance": (1.05, 1.50), "value": (1.40, 2.30)}


def _qc_audit(d: dict, rb: dict | None, sig: dict) -> dict:
    """Verdict DIAGNOSTIC par pilier (SCAN / ANALYSE / SÉLECTION / SOURCES) + liste des soucis détectés.
    ✅ ok · ⚠️ attention · ❌ problème. Le pire pilier donne le feu global (🟢/🟠/🔴)."""
    issues, sel_notes = [], []
    # SCAN : analyse écrite et décision finalisée
    scan = "✅"
    if not sig["md_ok"]:
        scan = "❌"; issues.append(f"SCAN : analyse trop courte ({sig['md_ko']:.1f} ko, stub)")
    if not sig["final"]:
        scan = "⚠️" if scan == "✅" else scan; issues.append("SCAN : décision pas encore finalisée (vague à venir)")
    # ANALYSE : faits multi-sources + panel + vivier de fantômes
    analyse = "✅"
    if not sig["has_facts"]:
        analyse = "❌"; issues.append("ANALYSE : section « Les faits » absente")
    if not sig["panel"]:
        analyse = "⚠️" if analyse == "✅" else analyse; issues.append("ANALYSE : panel de validation absent")
    if sig["n_shadow"] < 6:
        analyse = "⚠️" if analyse == "✅" else analyse; issues.append(f"ANALYSE : peu de fantômes ({sig['n_shadow']})")
    # SOURCES : ≥2 sources indépendantes + ancre sharp + vraies cotes Unibet
    sources = "✅"
    if sig["n_src"] < 2:
        sources = "❌"; issues.append(f"SOURCES : {sig['n_src']} source(s) (<2 requis)")
    if sig.get("sharp_conflict"):
        sources = "⚠️" if sources == "✅" else sources
        issues.append("SOURCES : ancre sharp REJETÉE (favori opposé au marché = résolution fausse → match différé)")
    elif sig.get("sharp_cited") and sig.get("sharp_map_empty"):
        sources = "❌"   # GRAVE : chiffre sharp inventé (la prose s'appuie sur une ancre qui n'existe pas)
        issues.append("SOURCES : l'analyse CITE un sharp INEXISTANT (aucune ancre `sharp_map` — chiffre fabriqué)")
    elif not sig["sharp_ok"]:
        sources = "⚠️" if sources == "✅" else sources; issues.append("SOURCES : pas d'ancre sharp Pinnacle")
    if sig["n_omap"] == 0:
        sources = "⚠️" if sources == "✅" else sources; issues.append("SOURCES : cotes réelles Unibet (omap) absentes")
    # SÉLECTION : pari dans sa bande + cote réelle + cohérence proba/cote ; ou abstention (décision valide)
    selection = "✅"
    if rb and rb.get("sel"):
        tkey = rb.get("tier") or "confiance"
        lo, hi = _BANDS.get(tkey, (1.01, 100.0))
        cote, prob, cprob, code = rb.get("cote"), rb.get("prob"), rb.get("cprob"), rb.get("code")
        if isinstance(cote, (int, float)) and lo - 1e-9 <= cote <= hi + 1e-9:
            sel_notes.append(f"cote dans la bande {tkey} {lo:g}–{hi:g}")
        else:
            selection = "❌"; issues.append(f"SÉLECTION : cote {cote} HORS bande {tkey} [{lo:g}–{hi:g}]")
        if code and code not in (d.get("omap") or {}):
            selection = "❌"; issues.append("SÉLECTION : cote du pari ABSENTE de l'omap (cote non vérifiée)")
        else:
            sel_notes.append("cote réelle Unibet vérifiée")
        # cohérence proba/cote : sur la CONFIANCE, une proba très au-dessus de l'implicite = drapeau rouge
        # (ex. ancien bug « DC 91 %@1.42 » = cote fantôme / mauvais match). La VALUE, elle, VEUT proba>implicite.
        imp = (1.0 / cote) if isinstance(cote, (int, float)) and cote else None
        use = cprob if isinstance(cprob, (int, float)) else prob
        if imp and isinstance(use, (int, float)):
            if tkey == "confiance" and (use / 100.0 - imp) > 0.15:
                selection = "❌"; issues.append(
                    f"SÉLECTION : proba {use:.0f}% ≫ implicite {imp*100:.0f}% (cote {cote}) — incohérence proba/cote")
            else:
                sel_notes.append(f"cohérence proba/cote OK (implicite {imp*100:.0f}%)")
    else:
        sel_notes.append("abstention (aucun candidat mécanique ≥ seuil — décision valide)")
    rank = {"✅": 0, "⚠️": 1, "❌": 2}
    worst = max((scan, analyse, selection, sources), key=lambda s: rank[s])
    return {"overall": {"✅": "🟢", "⚠️": "🟠", "❌": "🔴"}[worst],
            "scan": scan, "analyse": analyse, "selection": selection, "sources": sources,
            "issues": issues, "sel_notes": sel_notes}


def _qc_card(d: dict, m: dict, md: str | None) -> str:
    """Fiche QC DIAGNOSTIC d'un match : en-tête feu global + 4 piliers (SCAN/ANALYSE/SÉLECTION/SOURCES),
    soucis détectés, le pari joué (ou abstention) + cohérence, l'analyse (faits sourcés), le détail qualité."""
    home, away = str(d.get("home", "")), str(d.get("away", ""))
    name = m.get("name") or f"{home} - {away}"
    comp = d.get("comp") or ""
    ko = _ko_bx(m.get("start") or d.get("start"))
    mdtxt = _md_text(md)
    rb = _played_bet(d) if d.get("id") else None      # réglé -> stat_bet (sinon pick brut / None)
    sig = _qc_collect(d, md, mdtxt)
    au = _qc_audit(d, rb, sig)

    sub = " · ".join(x for x in (comp, (f"{ko} (Brussels)" if ko else "")) if x)
    lines = [f"{au['overall']} CONTRÔLE — {name}"]
    if sub:
        lines.append(f"🏆 {sub}")
    lines.append(f"SCAN {au['scan']} · ANALYSE {au['analyse']} · SÉLECTION {au['selection']} · SOURCES {au['sources']}")
    if au["issues"]:
        lines += ["", "⚠️ À VÉRIFIER :"] + [f"  • {x}" for x in au["issues"]]
    lines.append("")

    if rb and rb.get("sel"):
        tier = {"confiance": "CONFIANCE", "value": "VALUE"}.get(rb.get("tier"), (rb.get("tier") or "").upper())
        prob, cprob = rb.get("prob"), rb.get("cprob")
        conf = ""
        if isinstance(prob, (int, float)):
            conf = f"confiance {prob:.0f}%"
            if isinstance(cprob, (int, float)) and abs(cprob - prob) >= 1:
                conf += f" · calibrée {cprob:.0f}%"
        lines.append(f"🎯 PARI JOUÉ — {tier}")
        lines.append(f"   {A.pretty_sel(rb.get('sel', ''), home, away)} @ {rb.get('cote')}"
                     + (f"   ({conf})" if conf else ""))
        for nt in au["sel_notes"]:
            lines.append(f"   ✔ {nt}")
        why = d.get("played_why") or {}
        wtext = why.get("text") if isinstance(why, dict) else None
        if wtext:
            lines += ["", f"💬 Pourquoi ce pari : {str(wtext).strip()[:800]}"]
    else:
        lines.append("⏸️ ABSTENTION — aucun pari mécanique ≥ seuil")
        lines.append("   (Confiance : conf ≥ 80 · cote 1.05–1.50 — Value : conf ≥ 68 · cote 1.40–2.30 · EV ≥ +5 %)")
        skip = _md_section(mdtxt, "Le pari à jouer")
        if skip:
            # Le .md « Le pari à jouer » peut être une vraie ABSTENTION (« À éviter / SKIP ») OU un pari
            # RECOMMANDÉ par l'analyste que le sélecteur mécanique a REFUSÉ (marché banni / cote hors bande /
            # conf sous seuil). Ne PAS afficher ce dernier comme « Décision : <pari> » sous une ABSTENTION
            # (contradiction vue par le user 2026-09-02) -> on le relabélise clairement « angle NON retenu ».
            _is_skip = bool(re.match(r"\s*[-*•]?\s*\*{0,2}\s*(à\s+[ée]viter|skip|on\s+s['’]abstient|"
                                     r"aucun\s+pari|pas\s+de\s+pari|on\s+ne\s+joue\s+pas)", skip, re.I))
            if _is_skip:
                lines += ["", f"🧭 Décision : {skip[:450]}"]
            else:
                lines += ["", f"💡 Angle de l'analyse — NON RETENU par la sélection mécanique "
                              f"(sous seuil / hors bande / marché exclu) : {skip[:420]}"]

    facts = _md_section(mdtxt, "Les faits")             # L'ANALYSE = faits multi-sources sourcés (à vérifier)
    if facts:
        lines += ["", "📋 Les faits (analyse) :", facts[:900]]
    lines += ["", "📊 Détail qualité",
              f"  • Sources : {sig['n_src']}"
              + (f" ({', '.join(sig['src_names'][:5])})" if sig["src_names"] else " (aucune trace)"),
              f"  • Ancre sharp (Pinnacle) : {'✅' if sig['sharp_ok'] else '❌'}",
              f"  • Cotes réelles Unibet (omap) : {'✅ ' + str(sig['n_omap']) if sig['n_omap'] else '❌'}",
              f"  • Profondeur : {sig['md_ko']:.1f} ko {'✅' if sig['md_ok'] else '⚠️'}"
              f" · panel {'✅' if sig['panel'] else '❌'} · fantômes {sig['n_shadow']}"]
    return "\n".join(lines)


def notify_match_qc(date: str | None = None, send: bool = False) -> int:
    """Envoie (ou prévisualise) une FICHE QC par match analysé FINALISÉ, une seule fois par match/jour."""
    prog = _load_programme()
    matches = prog.get("matches") or []
    day = date or prog.get("date") or ""
    if date:
        matches = [m for m in matches if (m.get("start") or "")[:10] == date]
    try:
        st = json.load(open(_QC_SENT, encoding="utf-8"))
    except Exception:
        st = {}
    done = set(st.get(day) or [])
    n_sent = n_wait = 0
    print(f"═══ FICHES QC PAR MATCH — {day or 'jour courant'} ═══")
    for m in matches:
        mid = str(m.get("id") or "")
        d, md = _sidecar_for(mid, m.get("home", ""), m.get("away", ""))
        analysed = bool(d and (d.get("bets") or d.get("shadow") or d.get("abstained") or d.get("stat_bet")))
        if not analysed:
            continue
        # DÉCISION FINALE seulement (Option B) : vague passée (`prematch_done`), abstention confirmée,
        # publié, ou réglé -> `retained_bet` reflète le VRAI pari (pas l'état provisoire du matin).
        final = bool(d.get("prematch_done") or d.get("abstained") or A.is_settled(d)
                     or (isinstance(d.get("published_bet"), dict) and d["published_bet"].get("sel")))
        if not final:
            n_wait += 1
            continue
        if send and mid in done:                 # dédup à l'ENVOI seulement ; l'aperçu montre toujours tout
            continue
        card = _qc_card(d, m, md)
        if send:
            if _send_owner(card):
                done.add(mid); n_sent += 1
                print(f"  → QC envoyée : {m.get('name') or mid}")
            else:
                print(f"  (envoi impossible : {m.get('name') or mid})")
        else:
            print("\n" + "-" * 60 + "\n" + card)
    if send and n_sent:
        try:
            json.dump({day: sorted(done)}, open(_QC_SENT, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    print(f"\n{n_sent} fiche(s) envoyée(s) · {n_wait} en attente de vague.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="jour ISO (défaut : programme courant)")
    ap.add_argument("--alert", action="store_true",
                    help="envoie une alerte PRIVÉE au propriétaire (data/owner_chat.txt) si problème, "
                         "dédupliquée (1 fois/problème/jour). Sans ce flag : rapport console seul.")
    ap.add_argument("--match-messages", action="store_true",
                    help="envoie une FICHE QC PRIVÉE par match analysé finalisé (une fois/match/jour). "
                         "Combiner avec --alert pour ENVOYER ; seul = prévisualisation console.")
    args = ap.parse_args()
    if args.match_messages:
        sys.exit(notify_match_qc(args.date, send=args.alert))
    sys.exit(run(args.date, send_alert=args.alert))
