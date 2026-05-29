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
    round: str | None = Field(default=None, description="Nom du round (source, en anglais). Ex: 'Final', 'Round of 16'")
    round_slug: str | None = Field(default=None, description="Identifiant court du round. Ex: 'final', 'quarterfinals'")
    round_code: int | None = None
    status: str | None = Field(default=None, description="not started / inprogress / finished")
    status_description: str | None = None
    court: str | None = None
    city: str | None = None
    country: str | None = None
    ground_type: str | None = Field(default=None, description="Surface. Ex: 'Red clay'")
    start_time: datetime | None = None
    duration_seconds: int | None = Field(default=None, description="Durée totale du match (s)")
    set_durations: list[int | None] = Field(default_factory=list, description="Durée de chaque set (s)")
    first_to_serve: str | None = Field(default=None, description="home / away : qui a servi en premier")
    home: Player = Field(default_factory=Player)
    away: Player = Field(default_factory=Player)
    home_seed: str | None = Field(default=None, description="Tête de série du joueur home")
    away_seed: str | None = Field(default=None, description="Tête de série du joueur away")
    home_score: Score = Field(default_factory=Score)
    away_score: Score = Field(default_factory=Score)
    winner: str | None = Field(default=None, description="home / away / None")
    has_statistics: bool = False
    custom_id: str | None = None
    slug: str | None = None

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


class PointByPointPoint(BaseModel):
    """Un point joué dans un jeu : score du jeu après le point (ex: '30'/'15')."""

    home: str | None = None
    away: str | None = None


class PointByPointGame(BaseModel):
    """Un jeu dans un set."""

    game: int | None = None
    home_score: int | None = None
    away_score: int | None = None
    server: str | None = Field(default=None, description="home / away : qui sert ce jeu")
    points: list[PointByPointPoint] = Field(default_factory=list)


class PointByPointSet(BaseModel):
    set: int | None = None
    games: list[PointByPointGame] = Field(default_factory=list)


class MatchPointByPoint(BaseModel):
    """Déroulé point par point d'un match (set → jeu → point)."""

    match_id: int
    sets: list[PointByPointSet] = Field(default_factory=list)


class PlayerProfile(BaseModel):
    """Fiche détaillée d'un joueur (bio + classement courant)."""

    id: int | None = None
    name: str = ""
    full_name: str | None = None
    short_name: str | None = None
    gender: str | None = Field(default=None, description="M / F")
    country: str | None = None
    national: bool | None = None
    ranking: int | None = Field(default=None, description="Classement courant (ATP/WTA)")
    plays: str | None = Field(default=None, description="right-handed / left-handed")
    height_m: float | None = Field(default=None, description="Taille (mètres)")
    weight_kg: int | None = Field(default=None, description="Poids (kg)")
    turned_pro: str | None = None
    birth_date: datetime | None = None
    birth_place: str | None = None
    residence: str | None = None
    prize_current: int | None = Field(default=None, description="Gains de la saison (USD)")
    prize_total: int | None = Field(default=None, description="Gains en carrière (USD)")
    user_count: int | None = Field(default=None, description="Nombre de fans SofaScore")


class RankingEntry(BaseModel):
    """Une ligne de classement d'un joueur (ATP, Live, UTR…)."""

    ranking_class: str | None = Field(default=None, description="team (ATP/WTA) / livetennis / utr")
    type: int | None = None
    ranking: int | None = None
    points: float | None = None
    previous_ranking: int | None = None
    previous_points: float | None = None
    best_ranking: int | None = None
    tournaments_played: int | None = None


class HeadToHead(BaseModel):
    """Bilan des confrontations directes entre les deux joueurs d'un match."""

    match_id: int
    home: Player = Field(default_factory=Player)
    away: Player = Field(default_factory=Player)
    home_wins: int | None = None
    away_wins: int | None = None
    draws: int | None = None


class MatchVotes(BaseModel):
    """Pronostics des fans pour un match."""

    match_id: int
    home_votes: int | None = None
    away_votes: int | None = None
    home_percent: float | None = None
    away_percent: float | None = None


class Streak(BaseModel):
    name: str
    value: str | None = None
    side: str | None = Field(default=None, description="home / away")
    continued: bool | None = None


class MatchStreaks(BaseModel):
    match_id: int
    general: list[Streak] = Field(default_factory=list)
    head_to_head: list[Streak] = Field(default_factory=list)


class OddChoice(BaseModel):
    """Un pari possible sur un marché (ex: vainqueur '1' ou '2')."""

    name: str = Field(description="Libellé du choix. Ex: '1', '2', 'Over', 'Under'")
    fractional: str | None = Field(default=None, description="Cote fractionnaire. Ex: '9/4'")
    decimal: float | None = Field(default=None, description="Cote décimale équivalente. Ex: 3.25")
    initial_fractional: str | None = None
    winning: bool | None = Field(default=None, description="Choix gagnant (si match terminé)")
    change: int | None = Field(default=None, description="Évolution récente: -1 / 0 / 1")


class OddsMarket(BaseModel):
    """Un marché de paris (vainqueur du match, 1er set, total de jeux…)."""

    market_id: int | None = None
    name: str = ""
    group: str | None = None
    period: str | None = None
    is_live: bool | None = None
    suspended: bool | None = None
    handicap: str | None = Field(default=None, description="Seuil pour les marchés Over/Under")
    choices: list[OddChoice] = Field(default_factory=list)


class MatchOdds(BaseModel):
    """Cotes (paris) d'un match."""

    match_id: int
    markets: list[OddsMarket] = Field(default_factory=list)


class TournamentSeason(BaseModel):
    """Une édition disponible du tournoi."""

    id: int | None = None
    year: int | None = None
    name: str | None = None


class TournamentInfo(BaseModel):
    tour: str
    id: int
    name: str
    current_season_id: int | None = None
    current_season: int | None = None
