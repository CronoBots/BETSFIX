"""Modèles Pydantic : structures normalisées renvoyées par l'API (indépendantes de la source)."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


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
    source: str = Field(default="sofascore", description="Source des données : sofascore / livescore")

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


class PlayerStatistics(BaseModel):
    """Statistiques agrégées d'un joueur sur un tournoi/saison (analyse de forme).

    Idéal pour le pari : % de 1ère/2ème balle, points de break sauvés/convertis,
    winners vs fautes directes, aces, tie-breaks…
    """

    player_id: int
    tournament_id: int | None = None
    season_id: int | None = None
    season_year: int | None = None
    matches: int | None = None
    wins: int | None = None
    # Service
    aces: int | None = None
    avg_aces: float | None = None
    double_faults: int | None = None
    avg_double_faults: float | None = None
    first_serve_percentage: float | None = None
    first_serve_points_won_percentage: float | None = None
    second_serve_percentage: float | None = None
    second_serve_points_won_percentage: float | None = None
    total_serve_attempts: int | None = None
    first_serve_points_scored: int | None = None
    first_serve_points_total: int | None = None
    second_serve_points_scored: int | None = None
    second_serve_points_total: int | None = None
    # Break points
    break_points_scored: int | None = Field(default=None, description="Balles de break converties")
    break_points_total: int | None = None
    break_points_saved_percentage: float | None = None
    break_points_saved_converted_percentage: float | None = None
    opponent_break_points_scored: int | None = None
    opponent_break_points_total: int | None = None
    # Jeu
    winners_total: int | None = None
    unforced_errors_total: int | None = None
    tiebreaks_won: int | None = None
    tiebreak_losses: int | None = None
    tiebreak_win_percentage: float | None = None


class PlayerStatsAvailability(BaseModel):
    """Tournois/saisons pour lesquels un joueur a des statistiques disponibles."""

    tournament_id: int | None = None
    tournament_name: str | None = None
    seasons: list[TournamentSeason] = Field(default_factory=list)


class UnibetOutcome(BaseModel):
    """Un choix de pari chez Unibet Belgique."""

    label: str = ""
    participant: str | None = None
    odds: float | None = Field(default=None, description="Cote décimale (ex: 1.44)")
    fractional: str | None = None
    line: float | None = Field(default=None, description="Ligne (Over/Under, handicap)")
    implied_probability: float | None = Field(default=None, description="Proba implicite brute = 1/cote")


class UnibetMarket(BaseModel):
    """Un marché de paris Unibet Belgique (vainqueur, total de jeux, sets…)."""

    label: str = ""
    type: str | None = None
    outcomes: list[UnibetOutcome] = Field(default_factory=list)


class UnibetOdds(BaseModel):
    """Cotes Unibet Belgique pour un match, matchées sur l'événement SofaScore."""

    match_id: int
    matched: bool = Field(description="True si l'événement a été retrouvé chez Unibet")
    kambi_event_id: int | None = None
    event_name: str | None = None
    start_time: datetime | None = None
    markets: list[UnibetMarket] = Field(default_factory=list)


class AnalysisFactor(BaseModel):
    """Un facteur du modèle, avec sa contribution pour chaque joueur."""

    name: str
    home: float | None = Field(default=None, description="Probabilité home selon ce facteur (0-1)")
    away: float | None = None
    weight: float = Field(description="Poids du facteur dans le modèle final")
    detail: str | None = None


class ValueBet(BaseModel):
    """Évaluation de la 'value' d'un pari sur un joueur."""

    model_config = ConfigDict(protected_namespaces=())

    side: str = Field(description="home / away")
    player: str = ""
    odds: float | None = Field(default=None, description="Cote Unibet décimale")
    model_probability: float | None = Field(default=None, description="Proba estimée par le modèle")
    implied_probability: float | None = Field(default=None, description="Proba implicite (vig retirée)")
    fair_probability: float | None = Field(
        default=None, description="Proba retenue après ancrage au marché (sert au calcul de mise)"
    )
    edge: float | None = Field(default=None, description="fair - implied. Positif = value")
    kelly_fraction: float | None = Field(default=None, description="Fraction de bankroll (Kelly)")
    recommended_stake_pct: float | None = Field(default=None, description="Mise conseillée (¼ Kelly, plafonnée), % bankroll")
    is_value: bool = False


class MatchAnalysis(BaseModel):
    """Analyse pré-match complète orientée aide à la décision de pari."""

    model_config = ConfigDict(protected_namespaces=())

    match_id: int
    home: Player = Field(default_factory=Player)
    away: Player = Field(default_factory=Player)
    status: str | None = None
    ground_type: str | None = None
    model_home_probability: float | None = None
    model_away_probability: float | None = None
    confidence: str | None = Field(default=None, description="élevée / moyenne / faible")
    factors: list[AnalysisFactor] = Field(default_factory=list)
    unibet_matched: bool = False
    value_bets: list[ValueBet] = Field(default_factory=list)
    recommendation: str = ""
    disclaimer: str = (
        "Estimation statistique à titre informatif — aucune garantie de gain. "
        "Pariez de manière responsable et uniquement ce que vous pouvez perdre."
    )


class MarketEdge(BaseModel):
    """Évaluation d'un pari sur un marché Unibet quelconque (pas que le vainqueur)."""

    model_config = ConfigDict(protected_namespaces=())

    market: str = Field(description="Nom du marché. Ex: 'Nombre total de jeux'")
    selection: str = Field(description="Le choix parié. Ex: 'Plus de', 'Carlos Alcaraz', '3-1'")
    line: float | None = Field(default=None, description="Ligne (Over/Under, handicap)")
    odds: float | None = None
    model_probability: float | None = None
    implied_probability: float | None = Field(default=None, description="Proba implicite (vig retirée)")
    edge: float | None = Field(default=None, description="fair - implied (après ancrage marché)")
    recommended_stake_pct: float | None = None
    is_value: bool = False


class MatchMarketsAnalysis(BaseModel):
    """Analyse de TOUS les marchés Unibet d'un match via simulation du déroulé."""

    model_config = ConfigDict(protected_namespaces=())

    match_id: int
    home: Player = Field(default_factory=Player)
    away: Player = Field(default_factory=Player)
    best_of: int | None = Field(default=None, description="3 (WTA) ou 5 (ATP RG)")
    model_home_probability: float | None = None
    unibet_matched: bool = False
    markets_evaluated: int = 0
    value_bets: list[MarketEdge] = Field(default_factory=list)
    all_markets: list[MarketEdge] = Field(default_factory=list)
    note: str = ""
    disclaimer: str = (
        "EXPÉRIMENTAL. Le simulateur reproduit mal la distribution du book sur les "
        "marchés de 'forme' (jeux/sets/tie-breaks) : il tend à surestimer les matchs "
        "longs/serrés. Les 'value' sur ces marchés sont probablement des artefacts du "
        "modèle, PAS des edges réels — à NE PAS utiliser pour parier en l'état."
    )


class TournamentInfo(BaseModel):
    tour: str
    id: int
    name: str
    current_season_id: int | None = None
    current_season: int | None = None
