from dataclasses import dataclass
from typing import Optional


# -------------------------
# FIXED VARIABLES
# -------------------------
HUMOR_TYPES = ["absurd", "satire", "irony", "relatable", "dark"]

SOURCE_NAMES = {
    "reddit": "Reddit",
    "newsapi": "News API",
    "google_news": "Google News",
}

STYLE_PROFILES = {
    "balanced": {
        "extra_instruction": """
            Keep the meme balanced: funny, visual, politically sharp, but not preachy.
        """,
        "meme_weights": {
            "fun": 0.35,
            "visual": 0.30,
            "lens": 0.25,
            "originality": 0.10,
        }
    },
    "fun": {
        "extra_instruction": """
            Make the meme funnier and less didactic.
            Prefer absurdity, surprise, exaggeration, irony, and relatable internet humor.
            Avoid slogans, moral lessons, political explaining, and educational captions.
            The viewer should laugh first and understand the politics second.
        """,
        "meme_weights": {
            "fun": 0.50,
            "visual": 0.30,
            "lens": 0.10,
            "originality": 0.10,
        }
    },
    "political": {
        "extra_instruction": """
            Make the meme more ideologically sharp, but still visual and not essay-like.
        """,
        "meme_weights": {
            "fun": 0.20,
            "visual": 0.30,
            "lens": 0.40,
            "originality": 0.10,
        }
    },
}

MANDATORY_SOCIAL_HASHTAGS = [
    "#karikatür",
    "#politikmizah",
    "#gündem",
    "#meme",
    "#ai",
]


# -------------------------
# Data Classes
# -------------------------
@dataclass
class Trend:
    trend_name: str
    source: str
    url: Optional[str] = None
    metadata: Optional[dict] = None


@dataclass
class AngleCandidate:
    group_name: str
    article_indices: list[int]
    source_trends: list[str]
    source_urls: list[str]
    source_sources: list[str]
    summary: Optional[str]
    angle: str

    lens_score: int = 0
    visual_score: int = 0
    fun_score: int = 0
    originality_score: int = 0
    angle_score: float = 0.0


@dataclass
class MemeCandidate:
    group_name: str
    source_trends: list[str]
    source_urls: list[str]
    source_sources: list[str]
    summary: Optional[str]
    angle: str

    visual_gag: str
    caption: str
    humor_type: str
    image_prompt: str

    lens_score: int = 0
    fun_score: int = 0
    visual_score: int = 0
    originality_score: int = 0
    meme_score: float = 0.0

    text_model: Optional[str] = None
    image_model: Optional[str] = None
    image_path: Optional[str] = None


@dataclass
class NewsGroup:
    group_name: str
    article_indices: list[int]
    source_trends: list[str]
    source_urls: list[str]
    source_sources: list[str]
    summary: Optional[str]


@dataclass
class MemeTemplate:
    template_id: str
    name: str
    image_path: str
    best_for: list[str]
    layout: str
    notes: Optional[str] = None


@dataclass
class TemplateMemeCandidate(MemeCandidate):
    template_id: Optional[str] = None
    template_name: Optional[str] = None
    template_path: Optional[str] = None
    meme_text: Optional[list[str]] = None
    edit_instruction: Optional[str] = None
    reason: Optional[str] = None