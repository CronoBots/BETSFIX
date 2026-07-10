"""Garde-fou selfcheck `ghost_resolution` — verrouille la détection AUTO d'un trou de résolution des
fantômes (incident 2026-07-10 : matchs internationaux voidés faute de traduction FR→EN des noms → 100 %
de fantômes en attente). Le check doit alerter en WARN dès 3 matchs suspects, rester INFO/OK sinon."""

from app import selfcheck


def _match(home, away, n_ghosts, n_pending, start="2026-07-01T18:00:00+00:00"):
    sh = [{"sel": f"s{i}", "result": (None if i < n_pending else "won")} for i in range(n_ghosts)]
    return ("p", {"sport": "basket", "home": home, "away": away, "start": start,
                  "result": {"pick_result": "void"}, "shadow": sh})


def test_ok_quand_les_fantomes_sont_regles():
    rows = [_match("A", "B", 12, 1), _match("C", "D", 13, 2)]   # 1-2 marchés exotiques restants = normal
    assert selfcheck._check_ghost_resolution(rows)["level"] == "ok"


def test_info_pour_un_trou_isole():
    # un seul match 100 % pending (ex. Malte-Armenia sans aucune source) = info, pas warn
    rows = [_match("Malte", "Armenia", 10, 10)]
    g = selfcheck._check_ghost_resolution(rows)
    assert g["level"] == "info" and len(g["items"]) == 1


def test_warn_des_trois_matchs_suspects():
    # scénario incident : 3 matchs terminés avec majorité de fantômes non réglés = régression systémique
    rows = [_match("Syrie", "Irak", 12, 12), _match("Roumanie", "Grèce", 10, 10),
            _match("Suède", "Rép.Tchèque", 11, 11)]
    assert selfcheck._check_ghost_resolution(rows)["level"] == "warn"


def test_ignore_les_matchs_recents():
    # un match tout juste fini (règlement encore en cours) ne doit PAS déclencher (age < 2 j)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT18:00:00+00:00")
    rows = [_match("X", "Y", 12, 12, start=today) for _ in range(3)]
    assert selfcheck._check_ghost_resolution(rows)["level"] == "ok"


def test_run_complet_reste_stable():
    r = selfcheck.run()
    assert r["status"] in ("ok", "info")               # jamais warn/error sur l'état réel courant
    assert any(c["key"] == "ghost_resolution" for c in r["checks"])
