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
def test_rank_of_trouve_par_jetons():
    ranks = {"Jannik Sinner": 1, "Carlos Alcaraz": 2}
    rk, nm = sources._rank_of(ranks, "Sinner, Jannik")
    assert rk == 1 and nm == "Jannik Sinner"
    rk, _nm = sources._rank_of(ranks, "Joueur Inconnu")
    assert rk is None


def test_tennis_form_dedup_et_tri():
    idx = {"cirstea": [("20260610", True, "A. Inglis", "6-1 6-2", "HSBC"),
                       ("20260601", False, "M. Andreeva", "4-6 3-6", "RG")],
           "sorana": [("20260610", True, "A. Inglis", "6-1 6-2", "HSBC")]}   # doublon par jeton
    form, fatigue = sources._tennis_form(idx, "Sorana Cirstea")
    assert form.count("Inglis") == 1                  # dédupliqué
    assert form.index("Inglis") < form.index("Andreeva")   # plus récent d'abord


# ------------------------------------------------------------------ basket
def test_bb_team_rows_par_jetons():
    d = {"Atlanta Dream": ["x"], "New York Liberty": ["y"]}
    assert sources._bb_team_rows(d, "Atlanta Dream (F)") == ["x"]
    assert sources._bb_team_rows(d, "Inconnu FC") is None


# ------------------------------------------------------------------ saison Understat
def test_us_season():
    assert sources._us_season("2026-03-10T20:00:00Z") == "2025"   # printemps -> saison 2025-26
    assert sources._us_season("2026-09-10T20:00:00Z") == "2026"   # automne -> saison 2026-27


# ------------------------------------------------------------------ règlement de secours
def test_fm_score_oriente_et_filtre_les_non_finis():
    m = {"status": {"finished": True},
         "home": {"name": "South Korea", "score": 2}, "away": {"name": "Czechia", "score": 1}}
    sc = sources._fm_score_from_match(m, "Corée du Sud", "Tchéquie")
    assert sc and sc["home"] == 2 and sc["away"] == 1 and sc["label"] == "2-1"
    # sidecar inversé par rapport à FotMob -> scores retournés
    sc = sources._fm_score_from_match(m, "Tchéquie", "Corée du Sud")
    assert sc and sc["home"] == 1 and sc["away"] == 2
    # pas fini -> None ; mauvaises équipes -> None
    assert sources._fm_score_from_match({**m, "status": {}}, "Corée du Sud", "Tchéquie") is None
    assert sources._fm_score_from_match(m, "Mexique", "Canada") is None


def test_bb_score_from_event_quarts_et_orientation():
    ev = {"competitions": [{
        "status": {"type": {"name": "STATUS_FINAL"}},
        "competitors": [
            {"team": {"displayName": "Indiana Fever"}, "score": "114",
             "linescores": [{"value": 30}, {"value": 25}, {"value": 28}, {"value": 21}, {"value": 10}]},
            {"team": {"displayName": "Chicago Sky"}, "score": "106",
             "linescores": [{"value": 28}, {"value": 27}, {"value": 26}, {"value": 15}, {"value": 10}]},
        ]}]}
    sc = sources._bb_score_from_event(ev, "Indiana Fever (F)", "Chicago Sky (F)")
    assert sc and sc["home"] == 114 and sc["away"] == 106
    assert sc["periods"][1] == (30, 28) and len(sc["periods"]) == 5   # 4 QT + prolongation
    # match pas fini -> None
    ev2 = {"competitions": [{"status": {"type": {"name": "STATUS_IN_PROGRESS"}},
                             "competitors": ev["competitions"][0]["competitors"]}]}
    assert sources._bb_score_from_event(ev2, "Indiana Fever (F)", "Chicago Sky (F)") is None


def test_tennis_score_from_comp_sets_et_jeux():
    cps = [
        {"athlete": {"displayName": "Tatjana Maria"},
         "linescores": [{"value": 6}, {"value": 3}]},
        {"athlete": {"displayName": "Maria Sakkari"},
         "linescores": [{"value": 3}, {"value": 6}]},
    ]
    # Maria gagne... non : 6-3 / 3-6 -> 1 set partout = pas de vainqueur lisible -> None
    assert sources._tennis_score_from_comp(cps, "Tatjana Maria", "Maria Sakkari") is None
    cps[0]["linescores"].append({"value": 6})
    cps[1]["linescores"].append({"value": 2})
    sc = sources._tennis_score_from_comp(cps, "Tatjana Maria", "Maria Sakkari")
    assert sc and sc["sets_home"] == 2 and sc["sets_away"] == 1
    assert sc["label"] == "2-1 (sets)" and sc["periods"][3] == (6, 2)
    # orientation inversée (le sidecar a Sakkari en home)
    sc = sources._tennis_score_from_comp(cps, "Maria Sakkari", "Tatjana Maria")
    assert sc and sc["sets_home"] == 1 and sc["sets_away"] == 2


def test_parse_bets_ignore_les_notes_sans_cote_ni_proba():
    from app.analyses import _parse_bets
    body = ('| Pari | Cote | Proba | Risque |\n|---|---|---|---|\n'
            '| _(aucun pari ne franchit le seuil de 65 %)_ |  |  |  |\n'
            '| Vrai pari @X | 1.41 | 78% | vert |\n')
    bets = _parse_bets(body)
    assert len(bets) == 1 and bets[0]['cote'] == 1.41
