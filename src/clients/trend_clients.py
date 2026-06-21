import feedparser
import html
import os
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional


PERIOD_TO_DELTA = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=90),
    "yearly": timedelta(days=360),
}


def period_start(period):
    if period not in PERIOD_TO_DELTA:
        raise ValueError(f"Invalid period: {period}")
    return datetime.now(timezone.utc) - PERIOD_TO_DELTA[period]


def clean_html(raw):
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    text = html.unescape(text)
    return " ".join(text.split())


@dataclass
class TrendItem:
    trend_name: str
    source: str
    url: Optional[str] = None
    score: Optional[float] = None
    metadata: Optional[dict] = None

    def to_dict(self):
        return asdict(self)


class SolHaberTrendClient:
    def __init__(self):
        self.url = "https://haber.sol.org.tr/rss.xml"

    def get_trends(self, limit=50, period="daily"):
        min_date = period_start(period)
        feed = feedparser.parse(self.url)

        trends = []
        for entry in feed.entries:
            title = entry.get("title")
            if not title:
                continue

            published = entry.get("published_parsed") or entry.get("updated_parsed")
            published_dt = None
            if published:
                published_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if published_dt < min_date:
                    continue

            trends.append(
                TrendItem(
                    trend_name=title,
                    source="sol_haber",
                    url=entry.get("link"),
                    metadata={
                        "published": entry.get("published"),
                        "updated": entry.get("updated"),
                        "published_dt": published_dt.isoformat() if published_dt else None,
                        "summary": clean_html(entry.get("summary")),
                        "period": period,
                    },
                )
            )

            if len(trends) >= limit:
                break
        return trends
    

class BirGunTrendClient:
    def __init__(self, url="https://www.birgun.net/"):
        self.url = url

    def get_trends(self, limit=10, period="daily"):
        min_date = period_start(period)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            )
        }
        try:
            r = requests.get(self.url, headers=headers, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"BirGün scrape failed: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        trends = []
        seen = set()
        for a in soup.find_all("a", href=True):
            title = clean_html(a.get_text(" ", strip=True))
            href = a.get("href")

            if not title or len(title) < 20:
                continue
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.birgun.net" + href
            if "birgun.net" not in href:
                continue

            key = title.lower().strip()
            if key in seen:
                continue
            seen.add(key)

            trends.append(
                TrendItem(
                    trend_name=title,
                    source="birgun",
                    url=href,
                    metadata={
                        "summary": self.extract_summary(href),
                        "period": period,
                    },
                )
            )

            if len(trends) >= limit:
                break
        return trends
    
    def extract_summary(self, url):
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # Öncelik 1: meta description
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                return clean_html(meta["content"])

            # Öncelik 2: og:description
            meta = soup.find("meta", attrs={"property": "og:description"})
            if meta and meta.get("content"):
                return clean_html(meta["content"])

            # Öncelik 3: ilk paragraf
            p = soup.find("p")
            if p:
                return clean_html(p.get_text())

        except Exception:
            pass

        return None


class GoogleNewsTrendClient:
    def __init__(self, country="US", language="en", topic="WORLD"):
        self.country = country
        self.language = language
        self.topic = topic

    def get_trends(self, limit=50, period="daily"):
        url = (
            f"https://news.google.com/rss/headlines/section/topic/{self.topic}"
            f"?hl={self.language}-{self.country}"
            f"&gl={self.country}"
            f"&ceid={self.country}:{self.language}"
        )

        min_date = period_start(period)
        feed = feedparser.parse(url)

        trends = []
        for entry in feed.entries:
            title = entry.get("title")
            if not title:
                continue
            if "feed is not available" in title.lower() or "feed kullanılamıyor" in title.lower():
                continue

            published = entry.get("published_parsed")
            if published:
                published_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if published_dt < min_date:
                    continue

            trends.append(
                TrendItem(
                    trend_name=title,
                    source="google_news",
                    url=entry.get("link"),
                    metadata={
                        "published": entry.get("published"),
                        "summary": clean_html(entry.get("summary")),
                        "country": self.country,
                        "topic": self.topic,
                        "period": period,
                    },
                )
            )

            if len(trends) >= limit:
                break

        return trends


class NewsTrendClient:
    def __init__(
        self,
        api_key=None,
        language="tr",
        country="tr",
        query="Türkiye OR ekonomi OR siyaset OR seçim OR dünya OR teknoloji OR savaş",
    ):
        self.api_key = api_key or os.getenv("API_KEY_NEWSAPI")
        self.language = language
        self.country = country
        self.query = query

        if not self.api_key:
            raise ValueError("API_KEY_NEWSAPI not configured")

    def parse_articles(self, articles, period):
        trends = []
        for article in articles:
            title = article.get("title")
            if not title:
                continue

            trends.append(
                TrendItem(
                    trend_name=title,
                    source="newsapi",
                    url=article.get("url"),
                    metadata={
                        "published_at": article.get("publishedAt"),
                        "summary": clean_html(article.get("description")),
                        "source_name": article.get("source", {}).get("name"),
                        "period": period,
                    },
                )
            )

        return trends

    def get_trends(self, limit=50, period="daily"):
        from_date = period_start(period).isoformat()

        if period in {"weekly", "monthly"}:
            return self.get_everything_trends(
                limit=limit,
                period=period,
                from_date=from_date,
            )

        top_url = "https://newsapi.org/v2/top-headlines"
        top_params = {
            "country": self.country,
            "pageSize": limit,
            "apiKey": self.api_key,
        }

        try:
            r = requests.get(top_url, params=top_params, timeout=20)
            r.raise_for_status()
            articles = r.json().get("articles", [])
            trends = self.parse_articles(articles, period)
            if trends:
                return trends[:limit]
        except Exception as e:
            print(f"NewsAPI top-headlines failed: {e}")

        return self.get_everything_trends(
            limit=limit,
            period=period,
            from_date=from_date,
        )

    def get_everything_trends(self, limit=10, period="daily", from_date=None):
        if from_date is None:
            from_date = period_start(period).isoformat()

        everything_url = "https://newsapi.org/v2/everything"
        everything_params = {
            "q": self.query,
            "language": self.language,
            "from": from_date,
            "sortBy": "popularity" if period in {"weekly", "monthly"} else "publishedAt",
            "pageSize": limit,
            "apiKey": self.api_key,
        }

        try:
            r = requests.get(everything_url, params=everything_params, timeout=20)
            r.raise_for_status()
            articles = r.json().get("articles", [])
            return self.parse_articles(articles, period)[:limit]
        except Exception as e:
            print(f"NewsAPI everything failed: {e}")
            return []


class RedditTrendClient:
    def __init__(self, subreddit="popular"):
        self.subreddit = subreddit

    def get_trends(self, limit=50, period="daily"):
        reddit_period_map = {
            "hourly": "hour",
            "daily": "day",
            "weekly": "week",
            "monthly": "month",
        }

        reddit_period = reddit_period_map.get(period, "day")
        url = f"https://www.reddit.com/r/{self.subreddit}/top/.rss?t={reddit_period}"
        feed = feedparser.parse(url)

        return [
            TrendItem(
                trend_name=entry.get("title"),
                source="reddit",
                url=entry.get("link"),
                score=None,
                metadata={
                    "subreddit": self.subreddit,
                    "published": entry.get("published"),
                    "summary": clean_html(entry.get("summary")),
                    "period": period,
                    "reddit_period": reddit_period,
                },
            )
            for entry in feed.entries[:limit]
            if entry.get("title")
        ]


class TrendAggregator:
    def __init__(
        self,
        clients,
        limit_per_source=8,
        total_limit=24,
        period="weekly",
    ):
        self.clients = clients
        self.limit_per_source = limit_per_source
        self.total_limit = total_limit
        self.period = period
        self.trends = None

    def get_trends(self):
        trends = []
        seen = set()

        for client in self.clients:
            try:
                source_trends = client.get_trends(
                    limit=self.limit_per_source,
                    period=self.period,
                )

                for trend in source_trends:
                    key = trend.trend_name.lower().strip()
                    if key in seen:
                        continue

                    seen.add(key)
                    trends.append(trend)

            except Exception as e:
                print(f"{client.__class__.__name__} failed: {e}")

        self.trends = [trend.to_dict() for trend in trends[:self.total_limit]]
        return self.trends

    def trends_df(self):
        if self.trends is None:
            self.get_trends()

        rows = []
        for trend in self.trends:
            rows.append(
                {
                    "trend_name": trend.get("trend_name"),
                    "source": trend.get("source"),
                    "url": trend.get("url"),
                    "summary": (trend.get("metadata") or {}).get("summary"),
                    "period": (trend.get("metadata") or {}).get("period"),
                }
            )

        return pd.DataFrame(rows)

    def print_trends(self):
        if self.trends is None:
            self.get_trends()

        for i, trend in enumerate(self.trends):
            print("=" * 80)
            print("INDEX:", i)
            print("TREND:", trend.get("trend_name"))
            print("SOURCE:", trend.get("source"))
            print("URL:", trend.get("url"))
            print("SUMMARY:", (trend.get("metadata") or {}).get("summary"))
            print()