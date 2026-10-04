from __future__ import annotations

"""
V9 ORACLE MAXX — CORE ENGINE

Single-file deterministic intelligence engine.

Purpose:
- discover candidate topics from weak YouTube signals
- validate demand using Reddit, Google Trends and optional X
- measure supply, saturation, outliers and newcomer breakouts
- compare demand versus supply
- track temporal momentum, acceleration and persistence
- detect false deserts and false outliers
- build evidence from real audience questions and gaps
- produce a transparent, auditable Oracle Signal

This file intentionally contains NO agent system, NO prompt layer and NO AI dependency.
An AI provider may consume the output later, but it is not part of the decision core.

Design rule:
OBSERVED DATA -> FEATURES -> RULES -> SCORES -> SIGNAL

Missing data is NEVER interpreted as negative evidence.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from collections import defaultdict
import math
import re
import statistics
from typing import Callable, Iterable, Optional, Sequence, Any


# ============================================================
# CONFIGURATION
# ============================================================

@dataclass(frozen=True)
class Config:
    # Discovery gate
    small_channel_subs: int = 50_000
    newcomer_subs: int = 10_000
    newcomer_min_views: int = 50_000
    newcomer_window_days: int = 90
    outlier_min_ratio: float = 3.0
    outlier_hot_ratio: float = 10.0
    candidate_max_age_days: int = 180
    min_small_channels_for_cluster: int = 2
    max_topics_investigated: int = 12

    # YouTube supply
    recent_big_channel_subs: int = 100_000
    recent_big_video_views: int = 100_000
    strong_supply_min_videos: int = 3
    false_desert_min_videos: int = 5
    old_supply_days: int = 730

    # Reddit
    reddit_window_days: int = 7
    reddit_min_questions: int = 3
    reddit_min_frustration: int = 2
    reddit_hot_comments_per_hour: float = 10.0
    reddit_hot_growth_pct: float = 50.0

    # Google Trends
    trends_min_points: int = 14
    rising_ratio: float = 1.30
    falling_ratio: float = 0.70
    isolated_spike_ratio: float = 2.50
    isolated_spike_tail_ratio: float = 0.40

    # Temporal engine
    history_window: int = 12
    acceleration_min_delta: float = 0.05
    meaningful_score_delta: float = 10.0

    # Scoring
    w_demand: float = 0.18
    w_momentum: float = 0.15
    w_acceleration: float = 0.16
    w_validation: float = 0.14
    w_gap: float = 0.15
    w_youtube_outlier: float = 0.08
    w_reddit_intent: float = 0.07
    w_persistence: float = 0.07

    # Window
    immediate_min_score: float = 80.0
    enter_now_min_score: float = 68.0
    watch_min_score: float = 50.0


CFG = Config()


# ============================================================
# ENUMS
# ============================================================

class SourceStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"


class TrendState(str, Enum):
    RISING = "subindo"
    STABLE = "estavel"
    SPIKE = "pico_isolado"
    FALLING = "caindo"
    NOISE = "sem_padrao"
    UNVERIFIED = "nao_verificado"


class Temperature(str, Enum):
    COLD = "frio"
    STABLE = "estavel"
    HEATING = "aquecendo"
    HOT = "esquentando"
    ACCELERATING = "acelerando"
    EXPLOSIVE = "explosivo"
    COOLING = "esfriando"


class Verdict(str, Enum):
    OPPORTUNITY = "oportunidade"
    WAR = "guerra"
    DESERT = "deserto"
    TRAP = "armadilha"
    UNVERIFIED = "nao_verificado"


class Window(str, Enum):
    IMMEDIATE = "imediata"
    ENTER_NOW = "24-48h"
    THREE_TO_SEVEN_DAYS = "3-7_dias"
    ONE_TO_TWO_WEEKS = "1-2_semanas"
    TWO_TO_FOUR_WEEKS = "2-4_semanas"
    WATCH = "monitorar"
    DISCARD = "descartado"


class AudienceClass(str, Enum):
    ENTERTAINMENT = "entretenimento_puro"
    APPLIED_CURIOSITY = "curiosidade_aplicada"
    LATENT_PAIN = "dor_latente"
    UNVERIFIED = "nao_verificado"


# ============================================================
# RAW INPUT TYPES
# ============================================================

@dataclass
class Video:
    video_id: str
    title: str
    channel_id: str
    views: int
    published_at: datetime
    channel_subs: int


@dataclass
class RedditPost:
    title: str
    subreddit: str
    num_comments: int
    created_at: datetime
    body: str = ""
    score: int = 0


@dataclass
class XPost:
    text: str
    author: str
    created_at: datetime
    likes: int = 0
    replies: int = 0
    reposts: int = 0


@dataclass
class SourceReading:
    status: SourceStatus
    evidence: list[str] = field(default_factory=list)


@dataclass
class GoogleReading(SourceReading):
    state: TrendState = TrendState.UNVERIFIED
    note: str = ""
    current_level: Optional[float] = None
    growth_ratio: Optional[float] = None
    rising_queries: list[str] = field(default_factory=list)
    related_queries: list[str] = field(default_factory=list)


@dataclass
class RedditReading(SourceReading):
    matched_posts: int = 0
    question_count: int = 0
    frustration_count: int = 0
    comment_velocity: float = 0.0
    comment_growth_pct: Optional[float] = None
    communities: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    frustrations: list[str] = field(default_factory=list)


@dataclass
class XReading(SourceReading):
    matched_posts: int = 0
    posts_per_hour: float = 0.0
    engagement_per_hour: float = 0.0
    communities: int = 0
    recurring_terms: list[str] = field(default_factory=list)
    sentiment_hint: Optional[str] = None


@dataclass
class YouTubeReading(SourceReading):
    total: int = 0
    small_outlier_channels: int = 0
    newcomers: int = 0
    best_ratio: float = 0.0
    best_velocity_per_day: float = 0.0
    recent_big: int = 0
    median_age_top_days: float = 0.0
    supply: str = "nao_verificado"
    saturation_score: Optional[float] = None
    notes: list[str] = field(default_factory=list)


# ============================================================
# TEMPORAL HISTORY
# ============================================================

@dataclass
class TopicSnapshot:
    topic: str
    timestamp: datetime
    oracle_score: float
    demand_score: Optional[float]
    momentum_score: Optional[float]
    acceleration_score: Optional[float]
    validation_score: Optional[float]
    gap_score: Optional[float]
    saturation_score: Optional[float]
    reddit_score: Optional[float]
    youtube_score: Optional[float]
    x_score: Optional[float]
    temperature: Temperature


@dataclass
class TopicHistory:
    snapshots: list[TopicSnapshot] = field(default_factory=list)

    def add(self, snapshot: TopicSnapshot, max_items: int = 12) -> None:
        self.snapshots.append(snapshot)
        self.snapshots.sort(key=lambda x: x.timestamp)
        if len(self.snapshots) > max_items:
            del self.snapshots[:-max_items]

    def scores(self) -> list[float]:
        return [s.oracle_score for s in self.snapshots]

    def recent(self, n: int = 3) -> list[TopicSnapshot]:
        return self.snapshots[-n:]


# ============================================================
# DISCOVERY PLAN
# ============================================================

@dataclass
class PlannedTopic:
    term: str
    channels: int
    best_ratio: float


@dataclass
class Plan:
    topics: list[PlannedTopic]
    solo_outliers: list[str]
    gated_out: int


# ============================================================
# FINAL SIGNAL
# ============================================================

@dataclass
class OracleSignal:
    topic: str
    verdict: Verdict
    window: Window
    temperature: Temperature

    oracle_score: float

    strength: int
    sources_available: list[str]
    sources_confirmed: list[str]
    sources_unverified: list[str]

    demand_evidence: list[str]
    supply: str
    supply_notes: list[str]

    momentum: Optional[float]
    acceleration: Optional[float]
    persistence: Optional[float]
    validation: Optional[float]
    gap: Optional[float]
    saturation: Optional[float]

    youtube_outlier: Optional[float]
    reddit_intent: Optional[float]
    x_realtime: Optional[float]

    trend: TrendState
    trend_note: str

    newcomer_breakouts: int
    best_outlier_ratio: float
    best_velocity_per_day: float

    audience: AudienceClass
    audience_note: str

    question_cluster: list[str]
    frustration_cluster: list[str]
    search_cluster: list[str]
    recurring_x_terms: list[str]

    opportunity_reason: list[str]
    warnings: list[str]
    data_gaps: list[str]

    window_confidence: Optional[float]
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class PipelineResult:
    ranked: list[OracleSignal]
    plan: Plan
    run_timestamp: datetime


# ============================================================
# TEXT HELPERS
# ============================================================

STOP_WORDS = set("""
a o as os um uma uns umas de do da dos das em no na nos nas por para com sem
sobre entre e ou mas que como porque pra pro ao aos se seu sua seus suas meu minha
este esta esse essa isso isto muito mais menos ja nao sim the an of to in on for
and or with without about how why what is are was your my this that these those it
its you we they be from at by vs video videos melhor melhores top today news
""".split())

FRUSTRATION_RE = re.compile(
    r"(ningu[eé]m (explica|fala|ensina|mostra)|todo (v[ií]deo|canal)|"
    r"s[oó] (fala|mostra)|n[aã]o (acho|encontro)|cansei|"
    r"nobody (explains|talks|covers)|every (video|tutorial)|"
    r"can'?t find|tired of|wish (there was|someone))",
    re.I,
)

QUESTION_STARTS = {
    "como", "por", "porque", "qual", "quais", "quando", "onde", "sera",
    "vale", "how", "why", "what", "which", "does", "is", "can", "should"
}

HIGH_PAIN = {
    "dinheiro", "renda", "investir", "investimento", "divida", "emagrecer",
    "emagrecimento", "saude", "ansiedade", "carreira", "emprego", "negocio",
    "lucro", "aposentadoria", "imposto", "money", "income", "invest", "debt",
    "health", "career", "business", "profit", "faturar", "faturando", "vender"
}

ENTERTAINMENT = {
    "meme", "humor", "curiosidades", "curiosidade", "incrivel", "chocante",
    "viral", "challenge", "react"
}


def _strip_accents(text: str) -> str:
    table = str.maketrans(
        "áàâãäéèêëíìîïóòôõöúùûüç",
        "aaaaaeeeeiiiiooooouuuuc",
    )
    return text.lower().translate(table)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _strip_accents(text))


def _tokenset(text: str) -> set[str]:
    return set(_tokens(text))


def _filtered(text: str) -> list[str]:
    return [
        t for t in _tokens(text)
        if t not in STOP_WORDS and len(t) >= 3 and not t.isdigit()
    ]


def _ngrams(title: str) -> set[str]:
    toks = _filtered(title)
    grams = {t for t in toks if len(t) >= 5}
    grams |= {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    return grams


def _matches(term: str, tokens: set[str]) -> bool:
    return all(token in tokens for token in term.split())


def _is_question(text: str) -> bool:
    toks = _tokens(text)
    return "?" in text or (bool(toks) and toks[0] in QUESTION_STARTS)


def _age_days(dt: datetime, now: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((now - dt).total_seconds() / 86400.0, 1.0 / 24.0)


def _age_hours(dt: datetime, now: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((now - dt).total_seconds() / 3600.0, 1.0)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _safe_pct(current: float, previous: float) -> Optional[float]:
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _mean_or_none(values: Sequence[float]) -> Optional[float]:
    return statistics.mean(values) if values else None


# ============================================================
# DISCOVERY GATE
# ============================================================

def is_candidate(video: Video, now: datetime, cfg: Config = CFG) -> bool:
    if video.channel_subs > cfg.small_channel_subs:
        return False
    if _age_days(video.published_at, now) > cfg.candidate_max_age_days:
        return False

    ratio = video.views / max(video.channel_subs, 1)
    return ratio >= cfg.outlier_min_ratio


def plan_topics(videos: Sequence[Video], now: datetime, cfg: Config = CFG) -> Plan:
    candidates = [v for v in videos if is_candidate(v, now, cfg)]

    term_channels: dict[str, set[str]] = defaultdict(set)
    term_videos: dict[str, list[Video]] = defaultdict(list)

    for video in candidates:
        for gram in _ngrams(video.title):
            term_channels[gram].add(video.channel_id)
            term_videos[gram].append(video)

    eligible = [
        gram for gram, channels in term_channels.items()
        if len(channels) >= cfg.min_small_channels_for_cluster
    ]

    eligible.sort(
        key=lambda g: (
            len(term_channels[g]),
            len(g.split()),
            len(g),
        ),
        reverse=True,
    )

    claimed: set[str] = set()
    topics: list[PlannedTopic] = []

    for gram in eligible:
        free = [
            v for v in term_videos[gram]
            if v.video_id not in claimed
        ]
        free_channels = {v.channel_id for v in free}

        if len(free_channels) < cfg.min_small_channels_for_cluster:
            continue

        for video in term_videos[gram]:
            claimed.add(video.video_id)

        best_ratio = max(
            v.views / max(v.channel_subs, 1)
            for v in term_videos[gram]
        )

        topics.append(
            PlannedTopic(
                term=gram,
                channels=len(free_channels),
                best_ratio=best_ratio,
            )
        )

        if len(topics) >= cfg.max_topics_investigated:
            break

    solo = [
        f"{v.title} ({v.views / max(v.channel_subs, 1):.1f}x)"
        for v in candidates
        if v.video_id not in claimed
    ]

    return Plan(
        topics=topics,
        solo_outliers=solo,
        gated_out=len(videos) - len(candidates),
    )


# ============================================================
# GOOGLE / TRENDS
# ============================================================

def evaluate_trends(
    series: Optional[Sequence[float]],
    rising_queries: Optional[Sequence[str]] = None,
    related_queries: Optional[Sequence[str]] = None,
    cfg: Config = CFG,
) -> GoogleReading:
    if series is None:
        return GoogleReading(
            status=SourceStatus.UNAVAILABLE,
            evidence=[],
            state=TrendState.UNVERIFIED,
            note="sem dado de Trends",
            rising_queries=list(rising_queries or []),
            related_queries=list(related_queries or []),
        )

    values = list(series)

    if len(values) < cfg.trends_min_points:
        return GoogleReading(
            status=SourceStatus.PARTIAL,
            state=TrendState.UNVERIFIED,
            note=f"serie curta ({len(values)} pontos)",
            rising_queries=list(rising_queries or []),
            related_queries=list(related_queries or []),
        )

    third = max(len(values) // 3, 1)
    first = statistics.mean(values[:third])
    last = statistics.mean(values[-third:])
    peak = max(values)
    median = statistics.median(values)
    tail = statistics.mean(values[-3:])
    current = values[-1]

    growth_ratio = last / max(first, 1.0)

    if peak >= cfg.isolated_spike_ratio * max(median, 1.0) and \
       tail < cfg.isolated_spike_tail_ratio * peak:
        state = TrendState.SPIKE
        note = "subiu e caiu rapidamente; provável pico de evento"
    elif last < cfg.falling_ratio * max(first, 1.0):
        state = TrendState.FALLING
        note = "curva em queda"
    elif last >= cfg.rising_ratio * max(first, 1.0):
        state = TrendState.RISING
        note = "subida consistente"
    elif median >= 20 and statistics.pstdev(values) / max(statistics.mean(values), 1.0) < 0.25:
        state = TrendState.STABLE
        note = "demanda relativamente estável"
    else:
        state = TrendState.NOISE
        note = "sem padrão suficientemente claro"

    status = SourceStatus.OK
    if not values:
        status = SourceStatus.EMPTY

    return GoogleReading(
        status=status,
        state=state,
        note=note,
        current_level=current,
        growth_ratio=growth_ratio,
        rising_queries=list(rising_queries or []),
        related_queries=list(related_queries or []),
        evidence=[f"Trends: {note}"],
    )


def trends_score(reading: GoogleReading) -> Optional[float]:
    if reading.status in {
        SourceStatus.UNAVAILABLE,
        SourceStatus.TIMEOUT,
        SourceStatus.ERROR,
        SourceStatus.NOT_CONFIGURED,
    }:
        return None

    base = {
        TrendState.RISING: 85.0,
        TrendState.STABLE: 70.0,
        TrendState.SPIKE: 45.0,
        TrendState.FALLING: 25.0,
        TrendState.NOISE: 40.0,
        TrendState.UNVERIFIED: None,
    }[reading.state]

    if base is None:
        return None

    query_bonus = min(len(reading.rising_queries), 10) * 1.5
    return _clamp(base + query_bonus)


# ============================================================
# REDDIT
# ============================================================

def evaluate_reddit(
    term: str,
    posts: Optional[Sequence[RedditPost]],
    now: datetime,
    previous_comment_velocity: Optional[float] = None,
    cfg: Config = CFG,
) -> RedditReading:
    if posts is None:
        return RedditReading(
            status=SourceStatus.UNAVAILABLE,
            evidence=[],
        )

    matched: list[RedditPost] = []

    for post in posts:
        if _age_days(post.created_at, now) > cfg.reddit_window_days:
            continue

        tokens = _tokenset(post.title + " " + post.body)
        if _matches(term, tokens):
            matched.append(post)

    if not matched:
        return RedditReading(
            status=SourceStatus.EMPTY,
            evidence=["nenhum post correspondente encontrado"],
        )

    questions = [
        p.title for p in matched
        if _is_question(p.title)
    ]

    frustrations = [
        p.title for p in matched
        if FRUSTRATION_RE.search(p.title + " " + p.body)
    ]

    comment_velocity = max(
        (
            p.num_comments / _age_hours(p.created_at, now)
            for p in matched
        ),
        default=0.0,
    )

    comment_growth = (
        _safe_pct(comment_velocity, previous_comment_velocity)
        if previous_comment_velocity not in (None, 0)
        else None
    )

    communities = sorted({p.subreddit for p in matched})

    evidence: list[str] = []

    if len(questions) >= cfg.reddit_min_questions:
        evidence.append(
            f"{len(questions)} threads com perguntas recorrentes"
        )

    if len(frustrations) >= cfg.reddit_min_frustration:
        evidence.append(
            f"{len(frustrations)} sinais de frustração/brecha declarada"
        )

    if comment_velocity >= cfg.reddit_hot_comments_per_hour:
        evidence.append(
            f"atividade quente: {comment_velocity:.1f} comentários/h"
        )

    if comment_growth is not None and comment_growth >= cfg.reddit_hot_growth_pct:
        evidence.append(
            f"velocidade de comentários +{comment_growth:.0f}%"
        )

    status = SourceStatus.OK
    if not evidence:
        status = SourceStatus.PARTIAL

    return RedditReading(
        status=status,
        matched_posts=len(matched),
        question_count=len(questions),
        frustration_count=len(frustrations),
        comment_velocity=comment_velocity,
        comment_growth_pct=comment_growth,
        communities=communities,
        questions=questions,
        frustrations=frustrations,
        evidence=evidence,
    )


def reddit_intent_score(reading: RedditReading) -> Optional[float]:
    if reading.status in {
        SourceStatus.UNAVAILABLE,
        SourceStatus.TIMEOUT,
        SourceStatus.ERROR,
        SourceStatus.NOT_CONFIGURED,
    }:
        return None

    score = 20.0
    score += min(reading.question_count * 10.0, 35.0)
    score += min(reading.frustration_count * 8.0, 25.0)
    score += min(len(reading.communities) * 3.0, 10.0)

    if reading.comment_velocity > 0:
        score += min(reading.comment_velocity, 10.0)

    if reading.comment_growth_pct is not None:
        score += min(max(reading.comment_growth_pct, 0) / 10.0, 10.0)

    return _clamp(score)


# ============================================================
# X / REALTIME
# ============================================================

def evaluate_x(
    term: str,
    posts: Optional[Sequence[XPost]],
    now: datetime,
) -> XReading:
    if posts is None:
        return XReading(status=SourceStatus.UNAVAILABLE)

    term_tokens = _tokenset(term)
    matched = [
        p for p in posts
        if term_tokens.issubset(_tokenset(p.text))
    ]

    if not matched:
        return XReading(status=SourceStatus.EMPTY)

    total_hours = max(
        sum(_age_hours(p.created_at, now) for p in matched) /
        max(len(matched), 1),
        1.0,
    )

    engagement = sum(
        p.likes + p.replies + p.reposts
        for p in matched
    )

    posts_per_hour = len(matched) / total_hours
    engagement_per_hour = engagement / total_hours

    terms = _filtered(" ".join(p.text for p in matched))
    freq: dict[str, int] = defaultdict(int)
    for token in terms:
        freq[token] += 1

    recurring_terms = [
        term for term, count in sorted(
            freq.items(),
            key=lambda x: (x[1], x[0]),
            reverse=True,
        )
        if count >= 2
    ][:10]

    return XReading(
        status=SourceStatus.OK,
        matched_posts=len(matched),
        posts_per_hour=posts_per_hour,
        engagement_per_hour=engagement_per_hour,
        communities=len({p.author for p in matched}),
        recurring_terms=recurring_terms,
    )


def x_realtime_score(reading: XReading) -> Optional[float]:
    if reading.status in {
        SourceStatus.UNAVAILABLE,
        SourceStatus.TIMEOUT,
        SourceStatus.ERROR,
        SourceStatus.NOT_CONFIGURED,
    }:
        return None

    velocity = min(reading.posts_per_hour * 5.0, 50.0)
    engagement = min(math.log10(reading.engagement_per_hour + 1) * 15.0, 40.0)
    author_spread = min(reading.communities * 1.5, 10.0)

    return _clamp(velocity + engagement + author_spread)


# ============================================================
# YOUTUBE
# ============================================================

def evaluate_youtube(
    term: str,
    videos: Sequence[Video],
    now: datetime,
    cfg: Config = CFG,
) -> YouTubeReading:
    matched = [
        v for v in videos
        if _matches(term, _tokenset(v.title))
    ]

    if not matched:
        return YouTubeReading(
            status=SourceStatus.EMPTY,
            supply="fraca",
        )

    small_outlier_channels: set[str] = set()
    newcomer_channels: set[str] = set()
    best_ratio = 0.0
    best_velocity = 0.0
    recent_big = 0

    for video in matched:
        age = _age_days(video.published_at, now)
        ratio = video.views / max(video.channel_subs, 1)
        velocity = video.views / age

        best_ratio = max(best_ratio, ratio)
        best_velocity = max(best_velocity, velocity)

        if (
            video.channel_subs <= cfg.small_channel_subs
            and ratio >= cfg.outlier_min_ratio
        ):
            small_outlier_channels.add(video.channel_id)

        if (
            video.channel_subs < cfg.newcomer_subs
            and video.views >= cfg.newcomer_min_views
            and age <= cfg.newcomer_window_days
        ):
            newcomer_channels.add(video.channel_id)

        if (
            age <= 365
            and video.channel_subs >= cfg.recent_big_channel_subs
            and video.views >= cfg.recent_big_video_views
        ):
            recent_big += 1

    top = sorted(
        matched,
        key=lambda x: x.views,
        reverse=True,
    )[:10]

    median_age = (
        statistics.median(
            [_age_days(v.published_at, now) for v in top]
        )
        if top else 0.0
    )

    supply = (
        "forte"
        if recent_big >= cfg.strong_supply_min_videos
        else "fraca"
    )

    notes: list[str] = []

    if supply == "forte":
        notes.append(
            f"{recent_big} vídeos recentes de canais grandes com boa audiência"
        )

    if top and median_age > cfg.old_supply_days:
        notes.append(
            f"oferta envelhecida: mediana de {median_age / 365:.1f} anos"
        )

    if (
        len(matched) >= cfg.false_desert_min_videos
        and recent_big == 0
        and median_age > cfg.old_supply_days
    ):
        notes.append(
            "saturação falsa: muito volume antigo e pouca oferta forte recente"
        )

    # Saturação: combina volume, força e recência da oferta.
    volume_component = min(len(matched) / 50.0, 1.0) * 45.0
    strong_component = min(recent_big / 10.0, 1.0) * 35.0
    freshness_component = (
        20.0 if median_age <= 30
        else 12.0 if median_age <= 90
        else 6.0 if median_age <= 365
        else 0.0
    )

    saturation = _clamp(
        volume_component +
        strong_component +
        freshness_component
    )

    return YouTubeReading(
        status=SourceStatus.OK,
        total=len(matched),
        small_outlier_channels=len(small_outlier_channels),
        newcomers=len(newcomer_channels),
        best_ratio=best_ratio,
        best_velocity_per_day=best_velocity,
        recent_big=recent_big,
        median_age_top_days=median_age,
        supply=supply,
        saturation_score=saturation,
        notes=notes,
    )


def youtube_score(reading: YouTubeReading) -> Optional[float]:
    if reading.status in {
        SourceStatus.UNAVAILABLE,
        SourceStatus.TIMEOUT,
        SourceStatus.ERROR,
        SourceStatus.NOT_CONFIGURED,
    }:
        return None

    outlier = min(reading.small_outlier_channels, 5) * 12.0
    newcomer = min(reading.newcomers, 5) * 7.0
    velocity = min(math.log10(reading.best_velocity_per_day + 1) * 12.0, 30.0)

    return _clamp(outlier + newcomer + velocity)


# ============================================================
# TEMPORAL ENGINE
# ============================================================

def calculate_momentum(history: Optional[TopicHistory]) -> Optional[float]:
    if not history or len(history.snapshots) < 2:
        return None

    scores = history.scores()
    if len(scores) < 2:
        return None

    current = scores[-1]
    previous = scores[-2]

    delta = current - previous
    return _clamp(50.0 + delta * 3.0)


def calculate_acceleration(history: Optional[TopicHistory]) -> Optional[float]:
    if not history or len(history.snapshots) < 3:
        return None

    scores = history.scores()
    d1 = scores[-2] - scores[-3]
    d2 = scores[-1] - scores[-2]

    acceleration = d2 - d1

    return _clamp(50.0 + acceleration * 5.0)


def calculate_persistence(history: Optional[TopicHistory]) -> Optional[float]:
    if not history or not history.snapshots:
        return None

    snaps = history.snapshots[-6:]
    positive = 0

    for current, previous in zip(snaps[1:], snaps[:-1]):
        if current.oracle_score >= previous.oracle_score:
            positive += 1

    if len(snaps) == 1:
        return 50.0

    return _clamp(positive / (len(snaps) - 1) * 100.0)


def detect_temperature(
    momentum: Optional[float],
    acceleration: Optional[float],
    previous_temperature: Optional[Temperature] = None,
) -> Temperature:
    if momentum is None:
        return Temperature.STABLE

    if acceleration is not None and acceleration >= 85 and momentum >= 80:
        return Temperature.EXPLOSIVE

    if acceleration is not None and acceleration >= 68 and momentum >= 68:
        return Temperature.ACCELERATING

    if momentum >= 80:
        return Temperature.HOT

    if momentum >= 62:
        return Temperature.HEATING

    if momentum < 35:
        return Temperature.COOLING

    if previous_temperature == Temperature.HOT and momentum < 55:
        return Temperature.COOLING

    return Temperature.STABLE


# ============================================================
# AUDIENCE
# ============================================================

def classify_audience(
    term: str,
    reddit_questions: Sequence[str],
    reddit_frustrations: Sequence[str],
) -> tuple[AudienceClass, str]:
    text = term + " " + " ".join(reddit_questions) + " " + \
        " ".join(reddit_frustrations)

    tokens = _tokenset(text)

    pain = len(tokens & HIGH_PAIN)
    entertainment = len(tokens & ENTERTAINMENT)

    if pain >= 2:
        return (
            AudienceClass.LATENT_PAIN,
            "há sinais de necessidade/dor prática; validar com evidência adicional",
        )

    if entertainment > pain:
        return (
            AudienceClass.ENTERTAINMENT,
            "predomínio de entretenimento/curiosidade; heurística lexical",
        )

    return (
        AudienceClass.APPLIED_CURIOSITY,
        "curiosidade com aplicação potencial; heurística lexical",
    )


# ============================================================
# DEMAND / SUPPLY / GAP
# ============================================================

def calculate_demand_score(
    google: Optional[float],
    reddit: Optional[float],
    x_score: Optional[float],
) -> Optional[float]:
    values = [
        value for value in [google, reddit, x_score]
        if value is not None
    ]

    if not values:
        return None

    return _clamp(statistics.mean(values))


def calculate_gap_score(
    demand: Optional[float],
    saturation: Optional[float],
    youtube_outlier: Optional[float],
    persistence: Optional[float],
) -> Optional[float]:
    if demand is None and saturation is None:
        return None

    demand_value = demand if demand is not None else 50.0
    saturation_value = saturation if saturation is not None else 50.0
    outlier_value = youtube_outlier if youtube_outlier is not None else 50.0
    persistence_value = persistence if persistence is not None else 50.0

    # Demanda alta + saturação baixa = brecha.
    supply_gap = demand_value - saturation_value + 50.0

    # Outliers e persistência aumentam a confiança de que existe assunto real.
    score = (
        supply_gap * 0.65 +
        outlier_value * 0.20 +
        persistence_value * 0.15
    )

    return _clamp(score)


# ============================================================
# VALIDATION
# ============================================================

def cross_validate(
    google: Optional[float],
    reddit: Optional[float],
    youtube: Optional[float],
    x_score: Optional[float],
) -> tuple[float, int, list[str], list[str], list[str]]:
    sources = {
        "Google": google,
        "Reddit": reddit,
        "YouTube": youtube,
        "X": x_score,
    }

    available = [
        name for name, value in sources.items()
        if value is not None
    ]

    confirmed = [
        name for name, value in sources.items()
        if value is not None and value >= 55
    ]

    unverified = [
        name for name, value in sources.items()
        if value is None
    ]

    if not available:
        return 0.0, 0, [], unverified, []

    confirmation_ratio = len(confirmed) / len(available)

    strength_bonus = min(len(confirmed) * 12.5, 50.0)
    score = _clamp(
        confirmation_ratio * 50.0 + strength_bonus
    )

    evidence = [
        f"{len(confirmed)}/{len(available)} fontes disponíveis confirmam o sinal"
    ]

    return (
        score,
        len(confirmed),
        confirmed,
        unverified,
        available,
    )


# ============================================================
# WINDOW
# ============================================================

def estimate_window(
    oracle_score: float,
    temperature: Temperature,
    acceleration: Optional[float],
    saturation: Optional[float],
    validation: Optional[float],
) -> tuple[Window, Optional[float]]:
    confidence_parts: list[float] = []

    if validation is not None:
        confidence_parts.append(validation)

    if acceleration is not None:
        confidence_parts.append(acceleration)

    if saturation is not None:
        confidence_parts.append(100.0 - saturation)

    confidence = (
        statistics.mean(confidence_parts) / 100.0
        if confidence_parts else None
    )

    if temperature == Temperature.EXPLOSIVE and oracle_score >= 80:
        return Window.IMMEDIATE, confidence

    if (
        temperature == Temperature.ACCELERATING
        and oracle_score >= 68
    ):
        return Window.ENTER_NOW, confidence

    if (
        temperature in {Temperature.HOT, Temperature.HEATING}
        and oracle_score >= 68
    ):
        return Window.THREE_TO_SEVEN_DAYS, confidence

    if oracle_score >= 55:
        return Window.ONE_TO_TWO_WEEKS, confidence

    if oracle_score >= 45:
        return Window.WATCH, confidence

    return Window.DISCARD, confidence


# ============================================================
# SCORE
# ============================================================

def weighted_score(components: dict[str, Optional[float]], cfg: Config = CFG) -> tuple[float, dict[str, float]]:
    weights = {
        "demand": cfg.w_demand,
        "momentum": cfg.w_momentum,
        "acceleration": cfg.w_acceleration,
        "validation": cfg.w_validation,
        "gap": cfg.w_gap,
        "youtube_outlier": cfg.w_youtube_outlier,
        "reddit_intent": cfg.w_reddit_intent,
        "persistence": cfg.w_persistence,
    }

    weighted_sum = 0.0
    used_weight = 0.0
    breakdown: dict[str, float] = {}

    for key, weight in weights.items():
        value = components.get(key)

        if value is None:
            continue

        contribution = (value / 100.0) * weight * 100.0
        breakdown[key] = round(contribution, 2)
        weighted_sum += contribution
        used_weight += weight

    if used_weight == 0:
        return 0.0, breakdown

    # Renormaliza somente pelo peso de fontes realmente disponíveis.
    score = weighted_sum / used_weight
    return round(_clamp(score), 2), breakdown


# ============================================================
# TOPIC ANALYSIS
# ============================================================

def analyze_topic(
    term: str,
    videos: Sequence[Video],
    reddit_posts: Optional[Sequence[RedditPost]],
    trend_series: Optional[Sequence[float]],
    now: datetime,
    x_posts: Optional[Sequence[XPost]] = None,
    rising_queries: Optional[Sequence[str]] = None,
    related_queries: Optional[Sequence[str]] = None,
    history: Optional[TopicHistory] = None,
    previous_reddit_comment_velocity: Optional[float] = None,
    cfg: Config = CFG,
) -> OracleSignal:
    youtube = evaluate_youtube(term, videos, now, cfg)
    google = evaluate_trends(
        trend_series,
        rising_queries,
        related_queries,
        cfg,
    )
    reddit = evaluate_reddit(
        term,
        reddit_posts,
        now,
        previous_reddit_comment_velocity,
        cfg,
    )
    x_reading = evaluate_x(term, x_posts, now)

    google_value = trends_score(google)
    reddit_value = reddit_intent_score(reddit)
    youtube_value = youtube_score(youtube)
    x_value = x_realtime_score(x_reading)

    # Não tratar outlier como prova absoluta.
    # Ele vale como um componente separado.
    youtube_outlier = None
    if youtube.status not in {
        SourceStatus.UNAVAILABLE,
        SourceStatus.ERROR,
        SourceStatus.TIMEOUT,
        SourceStatus.NOT_CONFIGURED,
    }:
        if youtube.best_ratio > 0:
            youtube_outlier = _clamp(
                math.log10(youtube.best_ratio + 1) * 45.0
            )

    demand = calculate_demand_score(
        google_value,
        reddit_value,
        x_value,
    )

    persistence = calculate_persistence(history)
    momentum = calculate_momentum(history)
    acceleration = calculate_acceleration(history)

    temperature = detect_temperature(
        momentum,
        acceleration,
        history.snapshots[-1].temperature if history and history.snapshots else None,
    )

    gap = calculate_gap_score(
        demand,
        youtube.saturation_score,
        youtube_outlier,
        persistence,
    )

    validation, strength, confirmed, unverified, available = cross_validate(
        google_value,
        reddit_value,
        youtube_value,
        x_value,
    )

    trend = google.state

    warnings: list[str] = []
    gaps: list[str] = []

    if trend == TrendState.SPIKE:
        warnings.append(
            "pico isolado detectado; pode ser evento/notícia"
        )

    if youtube.total < cfg.false_desert_min_videos and demand is None:
        warnings.append(
            "possível falso deserto: pouca oferta e nenhuma demanda verificada"
        )

    if (
        youtube.recent_big > 0
        and youtube.best_ratio < cfg.outlier_min_ratio
    ):
        warnings.append(
            "possível falso outlier: desempenho concentrado em canais grandes"
        )

    if youtube.total >= cfg.false_desert_min_videos and \
       youtube.recent_big == 0 and \
       youtube.median_age_top_days > cfg.old_supply_days:
        warnings.append(
            "saturação falsa: volume histórico alto, oferta recente forte baixa"
        )

    for source in unverified:
        gaps.append(
            f"{source} não verificado: ausência de dado não é evidência negativa"
        )

    # Veredito de oportunidade.
    demand_exists = demand is not None and demand >= 50
    supply_weak = youtube.supply == "fraca"

    if not available:
        verdict = Verdict.UNVERIFIED
    elif demand_exists and supply_weak:
        verdict = Verdict.OPPORTUNITY
    elif demand_exists:
        verdict = Verdict.WAR
    elif supply_weak:
        verdict = Verdict.DESERT
    else:
        verdict = Verdict.TRAP

    # Score final
    components = {
        "demand": demand,
        "momentum": momentum,
        "acceleration": acceleration,
        "validation": validation,
        "gap": gap,
        "youtube_outlier": youtube_outlier,
        "reddit_intent": reddit_value,
        "persistence": persistence,
    }

    oracle_score, score_breakdown = weighted_score(
        components,
        cfg,
    )

    # Score deve refletir a ausência de evidência:
    # não penalizamos diretamente por source unavailable.
    # Entretanto, sem validação e sem demanda, a decisão permanece limitada.
    if verdict in {Verdict.DESERT, Verdict.TRAP, Verdict.UNVERIFIED}:
        oracle_score *= 0.75

    oracle_score = round(_clamp(oracle_score), 2)

    window, window_confidence = estimate_window(
        oracle_score,
        temperature,
        acceleration,
        youtube.saturation_score,
        validation,
    )

    if verdict == Verdict.WAR and gap is not None and gap >= 65:
        window = min(
            [Window.ENTER_NOW, Window.THREE_TO_SEVEN_DAYS],
            key=lambda x: {
                Window.ENTER_NOW: 0,
                Window.THREE_TO_SEVEN_DAYS: 1,
            }[x],
        )

    audience, audience_note = classify_audience(
        term,
        reddit.questions,
        reddit.frustrations,
    )

    question_cluster = [
        f"[pergunta] {q}"
        for q in dict.fromkeys(reddit.questions)
    ][:6]

    frustration_cluster = [
        f"[brecha] {f}"
        for f in dict.fromkeys(reddit.frustrations)
    ][:4]

    search_cluster = list(
        dict.fromkeys(
            list(google.rising_queries) +
            list(google.related_queries)
        )
    )[:12]

    opportunity_reason: list[str] = []

    if demand is not None and demand >= 65:
        opportunity_reason.append(
            "demanda verificada acima da zona neutra"
        )

    if gap is not None and gap >= 65:
        opportunity_reason.append(
            "brecha demanda/oferta favorável"
        )

    if acceleration is not None and acceleration >= 65:
        opportunity_reason.append(
            "aceleração temporal detectada"
        )

    if strength >= 2:
        opportunity_reason.append(
            "movimento confirmado por múltiplas fontes"
        )

    if reddit.question_count >= cfg.reddit_min_questions:
        opportunity_reason.append(
            "perguntas recorrentes indicam demanda sem resposta suficiente"
        )

    if youtube.newcomers >= 2:
        opportunity_reason.append(
            "novos canais apresentaram breakout"
        )

    return OracleSignal(
        topic=term,
        verdict=verdict,
        window=window,
        temperature=temperature,
        oracle_score=oracle_score,
        strength=strength,
        sources_available=available,
        sources_confirmed=confirmed,
        sources_unverified=unverified,
        demand_evidence=google.evidence + reddit.evidence + x_reading.evidence,
        supply=youtube.supply,
        supply_notes=youtube.notes,
        momentum=momentum,
        acceleration=acceleration,
        persistence=persistence,
        validation=validation,
        gap=gap,
        saturation=youtube.saturation_score,
        youtube_outlier=youtube_outlier,
        reddit_intent=reddit_value,
        x_realtime=x_value,
        trend=trend,
        trend_note=google.note,
        newcomer_breakouts=youtube.newcomers,
        best_outlier_ratio=youtube.best_ratio,
        best_velocity_per_day=youtube.best_velocity_per_day,
        audience=audience,
        audience_note=audience_note,
        question_cluster=question_cluster,
        frustration_cluster=frustration_cluster,
        search_cluster=search_cluster,
        recurring_x_terms=x_reading.recurring_terms,
        opportunity_reason=opportunity_reason,
        warnings=warnings,
        data_gaps=gaps,
        window_confidence=window_confidence,
        score_breakdown=score_breakdown,
    )


# ============================================================
# PIPELINE
# ============================================================

def run_pipeline(
    videos: Sequence[Video],
    fetch_reddit: Callable[[str], Optional[Sequence[RedditPost]]],
    fetch_trends: Callable[[str], Optional[Sequence[float]]],
    fetch_youtube: Optional[Callable[[str], Sequence[Video]]] = None,
    fetch_x: Optional[Callable[[str], Optional[Sequence[XPost]]]] = None,
    fetch_rising_queries: Optional[Callable[[str], Sequence[str]]] = None,
    fetch_related_queries: Optional[Callable[[str], Sequence[str]]] = None,
    histories: Optional[dict[str, TopicHistory]] = None,
    now: Optional[datetime] = None,
    cfg: Config = CFG,
) -> PipelineResult:
    """
    Pipeline de baixo custo:

    1. Gate local com vídeos já coletados.
    2. Agrupamento de candidatos.
    3. Só então chama fontes adicionais por termo.
    4. Calcula sinal determinístico.
    5. Ordena por score.

    Nenhuma falha de fonte interrompe o pipeline.
    """
    now = now or datetime.now(timezone.utc)
    histories = histories or {}

    plan = plan_topics(videos, now, cfg)
    signals: list[OracleSignal] = []

    for planned in plan.topics:
        topic_videos = list(videos)

        if fetch_youtube is not None:
            try:
                existing_ids = {v.video_id for v in topic_videos}
                extra = fetch_youtube(planned.term) or []
                topic_videos.extend(
                    v for v in extra
                    if v.video_id not in existing_ids
                )
            except Exception:
                # O estado real do provider deve ser registrado fora ou
                # pelo adapter. O core simplesmente continua.
                pass

        try:
            reddit_posts = fetch_reddit(planned.term)
        except Exception:
            reddit_posts = None

        try:
            trends = fetch_trends(planned.term)
        except Exception:
            trends = None

        x_posts = None
        if fetch_x is not None:
            try:
                x_posts = fetch_x(planned.term)
            except Exception:
                x_posts = None

        try:
            rising = (
                list(fetch_rising_queries(planned.term))
                if fetch_rising_queries else []
            )
        except Exception:
            rising = []

        try:
            related = (
                list(fetch_related_queries(planned.term))
                if fetch_related_queries else []
            )
        except Exception:
            related = []

        history = histories.get(planned.term)

        previous_comment_velocity = None
        if history and history.snapshots:
            # Valor anterior pode ser alimentado por storage específico.
            previous_comment_velocity = None

        signal = analyze_topic(
            term=planned.term,
            videos=topic_videos,
            reddit_posts=reddit_posts,
            trend_series=trends,
            x_posts=x_posts,
            rising_queries=rising,
            related_queries=related,
            history=history,
            previous_reddit_comment_velocity=previous_comment_velocity,
            now=now,
            cfg=cfg,
        )

        signals.append(signal)

    signals.sort(
        key=lambda s: (
            s.oracle_score,
            s.acceleration or 0,
            s.momentum or 0,
        ),
        reverse=True,
    )

    return PipelineResult(
        ranked=signals,
        plan=plan,
        run_timestamp=now,
    )


# ============================================================
# SNAPSHOT CREATION
# ============================================================

def snapshot_from_signal(
    signal: OracleSignal,
    timestamp: Optional[datetime] = None,
) -> TopicSnapshot:
    return TopicSnapshot(
        topic=signal.topic,
        timestamp=timestamp or datetime.now(timezone.utc),
        oracle_score=signal.oracle_score,
        demand_score=signal.validation,
        momentum_score=signal.momentum,
        acceleration_score=signal.acceleration,
        validation_score=signal.validation,
        gap_score=signal.gap,
        saturation_score=signal.saturation,
        reddit_score=signal.reddit_intent,
        youtube_score=signal.youtube_outlier,
        x_score=signal.x_realtime,
        temperature=signal.temperature,
    )


# ============================================================
# AUDIT / SERIALIZATION
# ============================================================

def signal_to_dict(signal: OracleSignal) -> dict[str, Any]:
    return asdict(signal)


def signal_to_json_ready(signal: OracleSignal) -> dict[str, Any]:
    data = signal_to_dict(signal)

    def normalize(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value

    return normalize(data)


def explain_signal(signal: OracleSignal) -> str:
    lines = [
        f"=== {signal.topic.upper()} | ORACLE {signal.oracle_score:.1f} ===",
        f"VEREDITO       {signal.verdict.value}",
        f"JANELA         {signal.window.value}",
        f"TEMPERATURA    {signal.temperature.value}",
        f"VALIDAÇÃO      {signal.strength}/{len(signal.sources_available)} confirmadas",
        f"FONTES         {', '.join(signal.sources_available) or 'nenhuma'}",
        f"DEMANDA        {signal.validation if signal.validation is not None else 'não verificada'}",
        f"MOMENTUM       {signal.momentum if signal.momentum is not None else 'não verificado'}",
        f"ACELERAÇÃO     {signal.acceleration if signal.acceleration is not None else 'não verificada'}",
        f"BRECHA         {signal.gap if signal.gap is not None else 'não verificada'}",
        f"SATURAÇÃO      {signal.saturation if signal.saturation is not None else 'não verificada'}",
        f"OUTLIER YT     {signal.youtube_outlier if signal.youtube_outlier is not None else 'não verificado'}",
        f"INTENÇÃO RD    {signal.reddit_intent if signal.reddit_intent is not None else 'não verificada'}",
        f"REALTIME X     {signal.x_realtime if signal.x_realtime is not None else 'não verificado'}",
        f"TRENDS         {signal.trend.value} — {signal.trend_note}",
        f"NOVATOS        {signal.newcomer_breakouts}",
        f"OUTLIER MÁX    {signal.best_outlier_ratio:.1f}x",
        f"VELOCIDADE     {signal.best_velocity_per_day:,.0f} views/dia",
    ]

    if signal.opportunity_reason:
        lines.append("MOTIVOS")
        lines.extend(f"  - {x}" for x in signal.opportunity_reason)

    if signal.question_cluster:
        lines.append("PERGUNTAS REAIS")
        lines.extend(f"  - {x}" for x in signal.question_cluster)

    if signal.frustration_cluster:
        lines.append("BRECHAS DECLARADAS")
        lines.extend(f"  - {x}" for x in signal.frustration_cluster)

    if signal.search_cluster:
        lines.append("BUSCAS")
        lines.extend(f"  - {x}" for x in signal.search_cluster)

    if signal.warnings:
        lines.append("AVISOS")
        lines.extend(f"  - {x}" for x in signal.warnings)

    if signal.data_gaps:
        lines.append("LACUNAS DE DADOS")
        lines.extend(f"  - {x}" for x in signal.data_gaps)

    lines.append(
        "SCORE BREAKDOWN: " +
        ", ".join(
            f"{key}={value:.1f}"
            for key, value in signal.score_breakdown.items()
        )
    )

    return "\n".join(lines)


# ============================================================
# OPTIONAL RULE: TOPIC COMPARISON
# ============================================================

def compare_topics(a: OracleSignal, b: OracleSignal) -> dict[str, Any]:
    """
    Comparação descritiva. Não atribui 'melhor' ao tema.
    """
    return {
        "topic_a": a.topic,
        "topic_b": b.topic,
        "score_delta": round(a.oracle_score - b.oracle_score, 2),
        "momentum_delta": _optional_delta(a.momentum, b.momentum),
        "acceleration_delta": _optional_delta(a.acceleration, b.acceleration),
        "validation_delta": _optional_delta(a.validation, b.validation),
        "gap_delta": _optional_delta(a.gap, b.gap),
        "saturation_delta": _optional_delta(a.saturation, b.saturation),
    }


def _optional_delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 2)


# ============================================================
# EXAMPLE CONTRACT
# ============================================================

__all__ = [
    "Config",
    "Video",
    "RedditPost",
    "XPost",
    "TopicSnapshot",
    "TopicHistory",
    "Plan",
    "PlannedTopic",
    "OracleSignal",
    "PipelineResult",
    "run_pipeline",
    "analyze_topic",
    "snapshot_from_signal",
    "signal_to_dict",
    "signal_to_json_ready",
    "explain_signal",
]
