from dataclasses import dataclass
from typing import Optional


# -------------------------
# FIXED VARIABLES
# -------------------------
HUMOR_TYPE_GUIDE = {
    "misdirection": (
        "Set up one expectation, then reveal an unexpected interpretation "
        "or outcome. The joke should turn in a different direction than expected."
    ),
    "deadpan": (
        "Treat an obviously absurd, cruel, or inappropriate situation as if it "
        "were completely normal through calm dialogue or routine language."
    ),
    "absurdity": (
        "Apply ordinary procedures, fees, forms, rules, approvals, customer-service "
        "language, or bureaucracy to a situation where they are absurdly inappropriate."
    ),
    "wrong_interpretation": (
        "Deliberately interpret the real event in a technically plausible but absurd "
        "way, creating a funny alternative reading of what happened."
    ),
    "role_reversal": (
        "Reverse the expected roles, responsibilities, power relations, or beneficiaries "
        "to expose the contradiction."
    ),
    "escalation": (
        "Take the real contradiction one ridiculous step further while keeping the "
        "connection to the source immediately understandable."
    ),
    "literalization": (
        "Turn an abstract phrase, euphemism, political slogan, corporate term, or "
        "economic concept into a literal physical situation."
    ),
    "everyday_analogy": (
        "Reframe the political or economic situation as a familiar everyday interaction "
        "such as shopping, renting, subscriptions, customer service, dating, work, family life or etc."
    ),
    "uncanny_normality": (
        "Show an obviously unacceptable, disturbing, or absurd situation while the caption "
        "describes it as completely ordinary. The main character's facial expression or body "
        "language should subtly reveal that something is deeply wrong: widened eyes, strained "
        "smile, frozen stare, awkward mouth, nervous posture, or unsettling calm."
    ),
    "grim_understatement": (
        "Use calm, restrained, or almost casual language to describe something severe, cruel, "
        "dangerous, or disastrous. The humor comes from the extreme mismatch between the seriousness "
        "of the situation and how mildly it is described."
    ),
    "fake_professionalism": (
        "Frame exploitation, disaster, corruption, or dysfunction using polished corporate, "
        "consulting, HR, marketing, customer-service, or management language as if it were a normal success."
    ),
    "self_own": (
        "Let the powerful actor accidentally expose their own hypocrisy, incompetence, greed, "
        "or contradiction through what they say or do. The joke works because they condemn themselves."
    )
}

HUMOR_TYPES = list(HUMOR_TYPE_GUIDE.keys())

SOURCE_NAMES = {
    "reddit": "Reddit",
    "newsapi": "News API",
    "google_news": "Google News",
}

STYLE_PROFILES = {
    "balanced": {
        "extra_instruction": """
            Keep the meme balanced: funny, visual, politically sharp, but not preachy.
            Prefer a clear comic turn, strong visual readability, and a caption that adds
            a second comedic beat instead of explaining the image.
            Favor surprise, misdirection, deadpan, absurdity, reversal, or everyday analogy
            when they fit naturally.
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
            Prioritize surprise, misdirection, deadpan, absurdity, wrong interpretation,
            escalation, uncanny normality, and everyday analogy.
            Prefer jokes with a clear setup -> unexpected turn.
            Avoid slogans, moral lessons, political explaining, educational captions,
            obvious first-thought jokes, and captions that merely restate the visual.
            The viewer should laugh first and understand the politics second.
        """,
        "meme_weights": {
            "fun": 0.50,
            "visual": 0.25,
            "lens": 0.10,
            "originality": 0.15,
        }
    },
    "political": {
        "extra_instruction": """
            Make the meme more ideologically sharp while keeping it funny and visual.
            Prefer structural class contradictions, hypocrisy, exploitation, rent-seeking,
            privatization, and power relations over personal moral criticism.
            Still require an actual comic turn: political clarity must not replace the joke.
            Avoid slogans, essays, and captions that simply explain the political message.
        """,
        "meme_weights": {
            "fun": 0.25,
            "visual": 0.25,
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

    hashtags: Optional[list[str]] = None
    social_post_text: Optional[str] = None
    

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
    joke_pattern: str
    layout: str
    instructions: str


@dataclass
class TemplateMemeCandidate(MemeCandidate):
    template_id: Optional[str] = None
    template_name: Optional[str] = None
    template_path: Optional[str] = None
    template_layout: Optional[str] = None
    template_instructions: Optional[str] = None
    meme_text: Optional[list[str]] = None
    edit_instruction: Optional[str] = None