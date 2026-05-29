"""Réponses SofaScore factices pour les tests (structure réelle simplifiée)."""

SEASONS = {
    "seasons": [
        {"id": 57175, "year": "2024", "name": "French Open 2024"},
        {"id": 48157, "year": "2023", "name": "French Open 2023"},
    ]
}

EVENTS_LAST = {
    "events": [
        {
            "id": 11958222,
            "tournament": {"uniqueTournament": {"name": "French Open"}},
            "season": {"year": "2024"},
            "roundInfo": {"round": 29, "name": "Final", "slug": "final"},
            "status": {"type": "finished", "description": "Ended"},
            "startTimestamp": 1717840800,
            "slug": "alcaraz-zverev",
            "customId": "abcd",
            "groundType": "Red clay",
            "firstToServe": 2,
            "homeTeamSeed": "3",
            "awayTeamSeed": "4",
            "venue": {"name": "Court Philippe Chatrier", "city": {"name": "Paris"}, "country": {"name": "France"}},
            "time": {"period1": 2588, "period2": 3130, "period3": 3910, "period4": 2569, "period5": 3367},
            "homeTeam": {"id": 1, "name": "Alcaraz C.", "country": {"name": "Spain"}, "ranking": 3},
            "awayTeam": {"id": 2, "name": "Zverev A.", "country": {"name": "Germany"}, "ranking": 4},
            "homeScore": {"current": 3, "period1": 6, "period2": 2, "period3": 5, "period4": 6, "period5": 6},
            "awayScore": {"current": 2, "period1": 3, "period2": 6, "period3": 7, "period4": 1, "period5": 2,
                          "period3TieBreak": 5},
            "winnerCode": 1,
            "hasEventPlayerStatistics": True,
        }
    ],
    "hasNextPage": False,
}

EVENTS_NEXT = {
    "events": [
        {
            "id": 11958900,
            "tournament": {"uniqueTournament": {"name": "French Open"}},
            "season": {"year": "2024"},
            "roundInfo": {"round": 28, "name": "Semifinals", "slug": "semifinals"},
            "status": {"type": "notstarted", "description": "Not started"},
            "startTimestamp": 1718000000,
            "homeTeam": {"id": 3, "name": "Sinner J.", "country": {"name": "Italy"}},
            "awayTeam": {"id": 4, "name": "Djokovic N.", "country": {"name": "Serbia"}},
            "homeScore": {},
            "awayScore": {},
        }
    ],
    "hasNextPage": False,
}

EVENT_DETAIL = {"event": EVENTS_LAST["events"][0]}

STATISTICS = {
    "statistics": [
        {
            "period": "ALL",
            "groups": [
                {
                    "groupName": "Service",
                    "statisticsItems": [
                        {"name": "Aces", "home": 12, "away": 8},
                        {"name": "Double faults", "home": 2, "away": 5},
                        {"name": "First serve", "home": "65%", "away": "58%"},
                    ],
                },
                {
                    "groupName": "Points",
                    "statisticsItems": [
                        {"name": "Total points won", "home": 120, "away": 98},
                    ],
                },
            ],
        }
    ]
}

# SofaScore renvoie les sets/jeux du plus récent au plus ancien : l'API doit
# les remettre dans l'ordre chronologique.
POINT_BY_POINT = {
    "pointByPoint": [
        {
            "set": 2,
            "games": [
                {
                    "game": 2,
                    "score": {"homeScore": 1, "awayScore": 1, "serving": 2},
                    "points": [
                        {"homePoint": "0", "awayPoint": "15"},
                        {"homePoint": "15", "awayPoint": "15"},
                    ],
                },
                {
                    "game": 1,
                    "score": {"homeScore": 1, "awayScore": 0, "serving": 1},
                    "points": [{"homePoint": "40", "awayPoint": "30"}],
                },
            ],
        },
        {
            "set": 1,
            "games": [
                {
                    "game": 1,
                    "score": {"homeScore": 1, "awayScore": 0, "serving": 1},
                    "points": [{"homePoint": "15", "awayPoint": "0"}],
                }
            ],
        },
    ]
}

ODDS = {
    "eventId": 11958222,
    "markets": [
        {
            "marketId": 1,
            "marketName": "Full time",
            "marketGroup": "Home/Away",
            "marketPeriod": "Match",
            "isLive": False,
            "suspended": False,
            "choices": [
                {"name": "1", "fractionalValue": "9/4", "initialFractionalValue": "5/2",
                 "winning": False, "change": -1},
                {"name": "2", "fractionalValue": "9/25", "initialFractionalValue": "3/10",
                 "winning": True, "change": 1},
            ],
        },
        {
            "marketId": 12,
            "marketName": "Total games won",
            "marketGroup": "Total sets/games",
            "marketPeriod": "Match",
            "choiceGroup": "38.5",
            "isLive": False,
            "suspended": False,
            "choices": [
                {"name": "Over", "fractionalValue": "5/6", "winning": True, "change": 0},
                {"name": "Under", "fractionalValue": "5/6", "winning": False, "change": 0},
            ],
        },
    ],
}

H2H = {"teamDuel": {"homeWins": 4, "awayWins": 6, "draws": 0}, "managerDuel": {}}

VOTES = {"vote": {"vote1": 16040, "vote2": 26797, "voteX": None}}

TEAM_STREAKS = {
    "general": [
        {"name": "Wins", "value": "12", "team": "home", "continued": False},
        {"name": "Wins", "value": "6", "team": "away", "continued": True},
    ],
    "head2head": [],
}

PLAYER = {
    "team": {
        "id": 2,
        "name": "Alexander Zverev",
        "fullName": "Alexander Zverev",
        "shortName": "A. Zverev",
        "gender": "M",
        "national": False,
        "ranking": 3,
        "userCount": 84252,
        "country": {"name": "Germany"},
        "playerTeamInfo": {
            "height": 1.98,
            "weight": 90,
            "plays": "right-handed",
            "turnedPro": "2013",
            "prizeCurrent": 2692752,
            "prizeTotal": 54458156,
            "birthDateTimestamp": 549244800,
            "birthCity": {"name": "Hamburg"},
            "residenceCity": {"name": "Monte Carlo"},
        },
    }
}

PLAYER_STATS_SEASONS = {
    "uniqueTournamentSeasons": [
        {
            "uniqueTournament": {"id": 2480, "name": "Roland Garros"},
            "seasons": [
                {"id": 52016, "year": "2024", "name": "French Open 2024"},
                {"id": 50017, "year": "2023", "name": "French Open 2023"},
            ],
        }
    ],
    "typesMap": {},
}

PLAYER_OVERALL_STATS = {
    "statistics": {
        "matches": 7,
        "wins": 7,
        "aces": 25,
        "avgAces": 3.5714285714286,
        "doubleFaults": 19,
        "avgDoubleFaults": 2.7142857142857,
        "firstServePercentage": 67.957276368491,
        "firstServePointsWonPercentage": 68.565815324165,
        "secondServePercentage": 92.083333333333,
        "secondServePointsWonPercentage": 54.166666666667,
        "totalServeAttempts": 989,
        "breakPointsScored": 46,
        "breakPointsTotal": 97,
        "breakPointsSavedPercentage": 62.903225806452,
        "breakPointsSavedConvertedPercentage": 47.422680412371,
        "winnersTotal": 277,
        "unforcedErrorsTotal": 255,
        "tiebreaksWon": 2,
        "tiebreakLosses": 0,
        "tiebreakWinPercentage": 100,
    }
}

PLAYER_RANKINGS = {
    "rankings": [
        {"type": 5, "ranking": 3, "points": 5705, "previousRanking": 3, "previousPoints": 5705,
         "bestRanking": 2, "tournamentsPlayed": 25, "rankingClass": "team"},
        {"type": 7, "ranking": 3, "points": 5405, "previousRanking": 3, "rankingClass": "livetennis"},
        {"type": 34, "ranking": 4, "points": 15.97, "previousRanking": 5, "bestRanking": 4,
         "rankingClass": "utr"},
    ]
}

PLAYER_EVENTS = {
    "events": [
        {
            "id": 99001,
            "tournament": {"uniqueTournament": {"name": "Roland Garros",
                                                "category": {"slug": "atp"}}},
            "season": {"year": "2024"},
            "roundInfo": {"round": 6, "name": "Round of 32", "slug": "round-of-32"},
            "status": {"type": "finished", "description": "Ended"},
            "startTimestamp": 1716800000,
            "homeTeam": {"id": 99, "name": "Halys Q.", "country": {"name": "France"}, "gender": "M"},
            "awayTeam": {"id": 2, "name": "Zverev A.", "country": {"name": "Germany"}, "gender": "M"},
            "homeScore": {"current": 0},
            "awayScore": {"current": 3},
            "winnerCode": 2,
        }
    ],
    "hasNextPage": False,
}
