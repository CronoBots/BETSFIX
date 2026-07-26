"""Box-score live foot (cartons/corners) : Unibet expose CE bloc sous DEUX structures selon le match
(constaté en direct 2026-07-26). `live_fields` doit produire `fstats` dans les DEUX cas, sinon certaines
jambes/cartes n'ont aucun carton alors qu'un match voisin les affiche (bug user 2026-07-26)."""
from app import web


def test_fstats_depuis_statistics_football():
    """Structure (1) : objet imbriqué statistics.football.{home,away}."""
    ld = {"score": {"home": 1, "away": 0}, "matchClock": {"minute": 30},
          "statistics": {"football": {"home": {"yellowCards": 2, "redCards": 0, "corners": 5},
                                      "away": {"yellowCards": 3, "redCards": 1, "corners": 2}}}}
    fs = web.live_fields(ld, "foot").get("fstats")
    assert fs == {"rc_h": 0, "rc_a": 1, "yc_h": 2, "yc_a": 3, "cor_h": 5, "cor_a": 2}


def test_fstats_repli_livestatistics():
    """Structure (2) : liste plate liveStatistics — SEULE présente pour certains matchs (ex. Flamengo)."""
    ld = {"score": {"home": 0, "away": 0}, "matchClock": {"minute": 22},
          "liveStatistics": [{"occurrenceTypeId": "CORNERS_HOME", "count": 3},
                             {"occurrenceTypeId": "CORNERS_AWAY", "count": 1},
                             {"occurrenceTypeId": "CARDS_YELLOW_HOME", "count": 1},
                             {"occurrenceTypeId": "CARDS_YELLOW_AWAY", "count": 0},
                             {"occurrenceTypeId": "CARDS_RED_HOME", "count": 0},
                             {"occurrenceTypeId": "CARDS_RED_AWAY", "count": 0}]}
    fs = web.live_fields(ld, "foot").get("fstats")
    assert fs == {"rc_h": 0, "rc_a": 0, "yc_h": 1, "yc_a": 0, "cor_h": 3, "cor_a": 1}


def test_fstats_prefere_statistics_football_si_les_deux():
    """Si les DEUX structures existent, on lit la (1) en priorité (aucune régression)."""
    ld = {"score": {"home": 0, "away": 0},
          "statistics": {"football": {"home": {"yellowCards": 9, "redCards": 0, "corners": 9},
                                      "away": {"yellowCards": 0, "redCards": 0, "corners": 0}}},
          "liveStatistics": [{"occurrenceTypeId": "CARDS_YELLOW_HOME", "count": 1}]}
    fs = web.live_fields(ld, "foot").get("fstats")
    assert fs["yc_h"] == 9   # vient de statistics.football, pas de liveStatistics


def test_pas_de_fstats_si_aucune_structure():
    """Aucune stat (début de match) -> pas de fstats -> repli score simple (comportement inchangé)."""
    ld = {"score": {"home": 0, "away": 0}, "matchClock": {"minute": 3}}
    assert web.live_fields(ld, "foot").get("fstats") is None
