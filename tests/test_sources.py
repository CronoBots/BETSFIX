"""Tests des fonctions PURES de app/sources.py (aucun appel réseau)."""

from app import sources


# ------------------------------------------------------------------ correspondance de noms
def test_tok_traduit_les_selections_nationales():
    assert "korea" in sources._tok("Corée du Sud")
    assert "czechia" in sources._tok("Tchéquie")
    assert {"usa", "united", "states"} & sources._tok("Etats-Unis")


def test_teams_match_fr_vs_en():
    assert sources._teams_match("Corée du Sud", "Tchéquie", "South Korea", "Czechia")
    # orientation inversée acceptée
    assert sources._teams_match("Corée du Sud", "Tchéquie", "Czechia", "South Korea")
    # paire différente refusée
    assert not sources._teams_match("Corée du Sud", "Tchéquie", "Mexico", "South Africa")


def test_teams_match_clubs():
    assert sources._teams_match("FC Barcelone", "Real Madrid", "Barcelona", "Real Madrid")
    assert not sources._teams_match("Arsenal", "Chelsea", "Liverpool", "Everton")


# ------------------------------------------------------------------ forme FotMob
def _form_item(rs, home, away, hs, as_, utc="2026-06-01T18:00:00.000Z"):
    return {"resultString": rs,
            "tooltipText": {"homeTeam": home, "awayTeam": away,
                            "homeScore": hs, "awayScore": as_, "utcTime": utc}}


def test_fm_form_lines_oriente_le_score_du_point_de_vue_equipe():
    # Paraguay PERD 2-1 à l'extérieur chez USA -> doit afficher « D 1-2 vs USA »
    tf = [[_form_item("L", "USA", "Paraguay", "2", "1")], []]
    line = sources._fm_form_lines(tf, 0, "Paraguay")
    assert "D 1-2 vs USA" in line


def test_fm_form_lines_plus_recent_d_abord():
    tf = [[_form_item("W", "Mexico", "Ghana", "1", "0", "2026-05-01T00:00:00.000Z"),
           _form_item("L", "Mexico", "Japan", "0", "2", "2026-06-01T00:00:00.000Z")], []]
    line = sources._fm_form_lines(tf, 0, "Mexico")
    assert line.index("vs Japan") < line.index("vs Ghana")


def test_fm_unavailable_nettoie_le_dict_fotmob():
    side = {"unavailablePlayers": [
        {"name": "Jan Kuchta",
         "unavailability": {"injuryId": 87, "type": "injury", "expectedReturn": "Doubtful"}}]}
    out = sources._fm_unavailable(side, "Tchéquie")
    assert "Jan Kuchta" in out and "blessé" in out and "{" not in out


# ------------------------------------------------------------------ tennis
# (tests tennis/basket RETIRÉS 2026-09-11 : code tennis/basket supprimé, BETSFIX = 100% foot)


def test_parse_bets_ignore_les_notes_sans_cote_ni_proba():
    from app.analyses import _parse_bets
    body = ('| Pari | Cote | Proba | Risque |\n|---|---|---|---|\n'
            '| _(aucun pari ne franchit le seuil de 65 %)_ |  |  |  |\n'
            '| Vrai pari @X | 1.41 | 78% | vert |\n')
    bets = _parse_bets(body)
    assert len(bets) == 1 and bets[0]['cote'] == 1.41
