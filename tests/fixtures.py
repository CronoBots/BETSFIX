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
            "roundInfo": {"round": 1, "name": "Finale"},
            "status": {"type": "finished", "description": "Ended"},
            "startTimestamp": 1717840800,
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
            "roundInfo": {"round": 2},
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
