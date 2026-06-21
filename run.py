from src.meme_pipeline import MemePipeline 
from src.clients.image_clients import FallbackImageClient, OpenAIImageClient, GeminiImageClient, ReplicateImageClient
from src.clients.text_clients import FallbackLLM, GeminiClient, GroqClient, OpenRouterClient
from src.clients.trend_clients import *


def main():

    #### 1. get trends
    clients = [
        SolHaberTrendClient(),
        BirGunTrendClient(),
        GoogleNewsTrendClient(country="TR", language="tr", topic="WORLD"),
        RedditTrendClient(subreddit="Turkey"),
        NewsTrendClient(language="tr", country="tr"),
    ]
    aggregator = TrendAggregator(
        clients=clients,
        limit_per_source=6,
        total_limit=30,
        period="daily", # "hourly", "daily", "weekly", "monthly", "quarterly", "yearly"
    )


    #### 2. set llms
    text_llms = FallbackLLM(
        clients=[
            GeminiClient("gemini-2.5-flash"),
            GeminiClient("gemini-2.5-pro"),
            GroqClient("llama-3.3-70b-versatile"),
            GroqClient("qwen/qwen3-32b"),
            OpenRouterClient("meta-llama/llama-3.3-70b-instruct:free"),
        ],
        timeout_seconds = 120,
        debug=True,
    )
    image_llms = FallbackImageClient(
        clients=[
            OpenAIImageClient("gpt-image-2"),
            GeminiImageClient("imagen-4.0-generate-001"),
            ReplicateImageClient("recraft-ai/recraft-v3"),
            ReplicateImageClient("bytedance/seedream-4"),
        ],
        verbose=True,
    )


    #### 3. set-up pipeline
    pipeline = MemePipeline(
        text_llm=text_llms,
        image_client=image_llms,
        style_profile="balanced", # balanced, fun, political
        verbose=True
    )


    #### 4.1. caricature | run pipeline
    selected_caricatures = pipeline.run_caricatures(
        aggregator.trends,
        max_angles = 12,
        max_memes = 6
    )

    #### 4.2. meme | run pipeline
    selected_memes = pipeline.run_memes(
        aggregator.trends,
        max_groups = 12,
        max_memes = 6
    )


if __name__ == "__main__":
    main()