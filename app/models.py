"""Modèles Pydantic : structures normalisées renvoyées par l'API (indépendantes de la source)."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Player(BaseModel):
    id: int | None = None
    name: str = ""
    country: str | None = None
    ranking: int | None = None


class Score(BaseModel):
    """Score d'un joueur : total de sets gagnés + détail set par set."""

    sets_won: int | None = None
    sets: list[int | None] = Field(default_factory=list)
    tiebreaks: list[int | None] = Field(default_factory=list)


class Match(BaseModel):
    id: int
    tour: str = Field(description="atp ou wta")
    tournament: str = "Roland Garros"
    season: int | None = None
    round: str | None = Field(default=None, description="Ex: '1/8 de finale', 'Finale'")
    round_code: int | None = None
    status: str | None = Field(default=None, description="not started / inprogress / finished")
    status_description: str | None = None
    court: str | None = None
    start_time: datetime | None = None
    home: Player = Field(default_factory=Player)
    away: Player = Field(default_factory=Player)
    home_score: Score = Field(default_factory=Score)
    away_score: Score = Field(default_factory=Score)
    winner: str | None = Field(default=None, description="home / away / None")
    has_statistics: bool = False

    @staticmethod
    def _ts_to_dt(ts: int | None) -> datetime | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)


class StatisticItem(BaseModel):
    name: str
    home: str | None = None
    away: str | None = None


class StatisticGroup(BaseModel):
    name: str
    items: list[StatisticItem] = Field(default_factory=list)


class PeriodStatistics(BaseModel):
    period: str = Field(description="ALL, 1ST, 2ND, ...")
    groups: list[StatisticGroup] = Field(default_factory=list)


class MatchStatistics(BaseModel):
    match_id: int
    periods: list[PeriodStatistics] = Field(default_factory=list)


class TournamentInfo(BaseModel):
    tour: str
    id: int
    name: str
    current_season_id: int | None = None
    current_season: int | None = None
