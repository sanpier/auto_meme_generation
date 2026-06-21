# -------------------------
# Schemas
# -------------------------
ANGLE_GROUP_SCHEMA = """
    {
        "angle_groups": [
            {
                "group_name": "string",
                "article_indices": [0, 1],
                "summary": "string",
                "angles": [
                    "string"
                ]
            }
        ]
    }
"""

ANGLE_RANK_SCHEMA = """
    {
        "ranked_angles": [
            {
                "angle_index": 0,
                "lens_score": 1,
                "visual_score": 1,
                "fun_score": 1,
                "originality_score": 1,
            }
        ]
    }
"""

MEME_SCHEMA = """
    {
        "memes": [
            {
                "visual_gag": "string",
                "caption": "string",
                "humor_type": "absurd|satire|irony|relatable|dark",
                "image_prompt": "string"
            }
        ]
    }
"""

CRITIC_SCHEMA = """
    {
        "ranked_memes": [
            {
                "meme_index": 0,
                "lens_score": 1,
                "fun_score": 1,
                "visual_score": 1,
                "originality_score": 1
            }
        ]
    }
"""

SOCIAL_HASHTAG_SCHEMA = """
    {
        "hashtags": [
            "#hashtag"
        ]
    }
"""

NEWS_GROUP_SCHEMA = """
{
    "news_groups": [
        {
            "group_name": "string",
            "article_indices": [0, 1],
            "summary": "string"
        }
    ]
}
"""

TEMPLATE_MEME_SCHEMA = """
{
    "memes": [
        {
            "template_id": "string",
            "reason": "string",
            "caption": "string",
            "humor_type": "absurd|satire|irony|relatable|dark",
            "meme_text": ["string"],
            "edit_instruction": "string"
        }
    ]
}
"""