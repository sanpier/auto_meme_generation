import json
import os
import time
from datetime import datetime, timezone
from IPython.display import display
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from typing import Any
from src.utils.data_classes import *
from src.utils.prompts import *
from src.utils.schemas import *


class MemePipeline:

    def __init__(
        self,
        text_llm,
        image_client,
        style_profile="balanced",
        n_angles_per_group=3,
        n_memes_per_angle=1,
        verbose=True,
    ):
        self.text_llm = text_llm
        self.image_client = image_client
        self.style_profile = style_profile
        self.style = STYLE_PROFILES[style_profile]
        self.n_angles_per_group = n_angles_per_group
        self.n_memes_per_angle = n_memes_per_angle
        self.verbose = verbose
        self.meme_log_path = "data/assets/meme_log.jsonl"
        self.template_json_path = "data/assets/meme_template.json"
        self.output_dir = Path("data/memes")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Helpers
    # -------------------------
    def angle_score(self, angle: AngleCandidate) -> float:
        return (
            angle.lens_score * 0.30
            + angle.visual_score * 0.30
            + angle.fun_score * 0.30
            + angle.originality_score * 0.10
        )

    def meme_score(self, meme: MemeCandidate) -> float:
        w = self.style["meme_weights"]
        return (
            meme.fun_score * w["fun"]
            + meme.visual_score * w["visual"]
            + meme.lens_score * w["lens"]
            + meme.originality_score * w["originality"]
        )


    # -------------------------
    # Stage 1: Generate Angle Groups
    # -------------------------
    def generate_angle_groups(self, raw_trends: list[dict[str, Any]]) -> list[AngleCandidate]:
        articles = []
        trends = []

        for i, item in enumerate(raw_trends):
            trend = Trend(
                trend_name=item.get("trend_name", ""),
                source=item.get("source", ""),
                url=item.get("url"),
                metadata=item.get("metadata") or {},
            )
            if not trend.trend_name:
                continue

            trends.append(trend)
            articles.append(
                {
                    "index": len(trends) - 1,
                    "trend_name": trend.trend_name,
                    "source": trend.source,
                    "url": trend.url,
                    "summary": (trend.metadata or {}).get("summary", ""),
                }
            )

        data = self.text_llm.generate_json(
            system=ANGLE_GROUP_GENERATOR_SYSTEM,
            prompt=ANGLE_GROUP_GENERATOR_PROMPT.format(
                n_angles_per_group=self.n_angles_per_group,
                articles=json.dumps(articles, ensure_ascii=False, indent=2),
            ),
            schema_hint=ANGLE_GROUP_SCHEMA,
            temperature=0.9,
        )

        angle_candidates = []
        for group in data.get("angle_groups", []):
            idxs = [
                idx for idx in group.get("article_indices", [])
                if isinstance(idx, int) and 0 <= idx < len(trends)
            ]

            if not idxs:
                continue

            source_trends = [trends[idx].trend_name for idx in idxs]
            source_urls = [trends[idx].url for idx in idxs if trends[idx].url]
            source_sources = sorted(set(trends[idx].source for idx in idxs))
            summary = group.get("summary", "")

            for angle in group.get("angles", []):
                if not isinstance(angle, str) or not angle.strip():
                    continue

                angle_candidates.append(
                    AngleCandidate(
                        group_name=group.get("group_name", source_trends[0]),
                        article_indices=idxs,
                        source_trends=source_trends,
                        source_urls=source_urls,
                        source_sources=source_sources,
                        summary=summary,
                        angle=angle.strip(),
                    )
                )

        if self.verbose:
            self.print_angles(angle_candidates)

        return angle_candidates


    # -------------------------
    # Stage 2: Rank Angles
    # -------------------------
    def rank_angles(self, angles: list[AngleCandidate]) -> list[AngleCandidate]:
        angles_json = [
            {
                "angle_index": i,
                "group_name": angle.group_name,
                "source_trends": angle.source_trends,
                "summary": angle.summary,
                "angle": angle.angle,
            }
            for i, angle in enumerate(angles)
        ]

        data = self.text_llm.generate_json(
            system=ANGLE_RANKER_SYSTEM,
            prompt=ANGLE_RANKER_PROMPT.format(
                angles_json=json.dumps(angles_json, ensure_ascii=False, indent=2),
            ),
            schema_hint=ANGLE_RANK_SCHEMA,
            temperature=0.25,
        )

        for score_item in data.get("ranked_angles", []):
            idx = score_item.get("angle_index")
            if idx is None or not isinstance(idx, int):
                continue
            if idx < 0 or idx >= len(angles):
                continue

            angle = angles[idx]
            angle.lens_score = int(score_item.get("lens_score", 0))
            angle.visual_score = int(score_item.get("visual_score", 0))
            angle.fun_score = int(score_item.get("fun_score", 0))
            angle.originality_score = int(score_item.get("originality_score", 0))
            angle.angle_score = self.angle_score(angle)

        ranked = sorted(angles, key=lambda x: x.angle_score, reverse=True)
        return ranked

    def select_passing_angles(
        self,
        angles: list[AngleCandidate],
        min_score: float = 8,
        max_angles: int | None = 10,
    ) -> list[AngleCandidate]:
        selected = [angle for angle in angles if angle.angle_score >= min_score]
        selected = sorted(selected, key=lambda x: x.angle_score, reverse=True)

        if max_angles is not None:
            selected = selected[:max_angles]

        if self.verbose:
            print(f"\nPASSING ANGLES: {len(selected)}\n")
            self.print_ranked_angles(selected)

        return selected


    # -------------------------
    # Stage 3: Generate Memes
    # -------------------------
    def generate_memes_for_angle(self, angle: AngleCandidate) -> list[MemeCandidate]:
        data = self.text_llm.generate_json(
            system=MEME_GENERATOR_SYSTEM,
            prompt=MEME_GENERATOR_PROMPT.format(
                n_memes=self.n_memes_per_angle,
                group_name=angle.group_name,
                source_trends=json.dumps(angle.source_trends, ensure_ascii=False, indent=2),
                summary=angle.summary or "",
                angle=angle.angle,
                available_humor_types=HUMOR_TYPES,
            ) + "\n\nSTYLE DIRECTION:\n" + self.style["extra_instruction"],
            schema_hint=MEME_SCHEMA,
            temperature=1,
        )

        memes = []
        required_keys = {
            "visual_gag",
            "caption",
            "humor_type",
            "image_prompt",
        }

        for item in data.get("memes", []):
            if not required_keys.issubset(item):
                continue
            if item["humor_type"] not in HUMOR_TYPES:
                continue

            memes.append(
                MemeCandidate(
                    group_name=angle.group_name,
                    source_trends=angle.source_trends,
                    source_urls=angle.source_urls,
                    source_sources=angle.source_sources,
                    summary=angle.summary,
                    angle=angle.angle,
                    visual_gag=item["visual_gag"],
                    caption=item["caption"],
                    humor_type=item["humor_type"],
                    image_prompt=item["image_prompt"],
                    text_model=getattr(self.text_llm, "last_used_model", None),
                )
            )

        return memes

    def generate_all_memes(self, angles: list[AngleCandidate]) -> list[MemeCandidate]:
        all_memes = []
        for angle in angles:
            try:
                memes = self.generate_memes_for_angle(angle)
                all_memes.extend(memes)

                if self.verbose:
                    print(f"[MEMES] generated {len(memes)} memes | {angle.group_name} | {angle.angle}")
            except Exception as e:
                if self.verbose:
                    print(f"Meme generation failed for angle: {angle.angle} | {e}")

        return all_memes


    # -------------------------
    # Stage 4: Quality Critic
    # -------------------------
    def score_memes_for_angle(
        self,
        group_name: str,
        summary: str,
        angle: str,
        memes: list[MemeCandidate],
    ) -> list[MemeCandidate]:
        memes_json = [
            {
                "meme_index": i,
                "visual_gag": meme.visual_gag,
                "caption": meme.caption,
                "humor_type": meme.humor_type,
                "image_prompt": meme.image_prompt,
            }
            for i, meme in enumerate(memes)
        ]

        data = self.text_llm.generate_json(
            system=QUALITY_CRITIC_SYSTEM,
            prompt=QUALITY_CRITIC_PROMPT.format(
                group_name=group_name,
                summary=summary or "",
                angle=angle,
                memes_json=json.dumps(memes_json, ensure_ascii=False, indent=2),
            ) + "\n\nSTYLE DIRECTION:\n" + self.style["extra_instruction"],
            schema_hint=CRITIC_SCHEMA,
            temperature=0.25,
        )

        for score_item in data.get("ranked_memes", []):
            idx = score_item.get("meme_index")
            if idx is None or not isinstance(idx, int):
                continue
            if idx < 0 or idx >= len(memes):
                continue

            meme = memes[idx]
            meme.lens_score = int(score_item.get("lens_score", 0))
            meme.fun_score = int(score_item.get("fun_score", 0))
            meme.visual_score = int(score_item.get("visual_score", 0))
            meme.originality_score = int(score_item.get("originality_score", 0))
            meme.meme_score = self.meme_score(meme)

        return memes

    def score_all_memes(self, memes: list[MemeCandidate]) -> list[MemeCandidate]:
        grouped = {}
        for meme in memes:
            key = (meme.group_name, meme.summary, meme.angle)
            grouped.setdefault(key, []).append(meme)

        scored_memes = []
        for (group_name, summary, angle), group in grouped.items():
            try:
                scored = self.score_memes_for_angle(
                    group_name=group_name,
                    summary=summary,
                    angle=angle,
                    memes=group,
                )
                scored_memes.extend(scored)

                if self.verbose:
                    print(f"[CRITIC] scored {len(scored)} memes | {group_name} | {angle}")
            except Exception as e:
                if self.verbose:
                    print(f"Quality scoring failed: {group_name} | {angle} | {e}")

        return sorted(scored_memes, key=lambda x: x.meme_score, reverse=True)


    # -------------------------
    # Stage 5: Select Passing Memes
    # -------------------------
    def select_passing_memes(
        self,
        memes: list[MemeCandidate],
        min_score: float = 7.0,
        max_memes: int | None = 5,
    ) -> list[MemeCandidate]:
        selected = [meme for meme in memes if meme.meme_score >= min_score]
        selected = sorted(selected, key=lambda x: x.meme_score, reverse=True)

        if max_memes is not None:
            selected = selected[:max_memes]

        if self.verbose:
            print(f"\nPASSING MEMES: {len(selected)}\n")
            self.print_memes(selected)

        return selected


    # -------------------------
    # Stage 6: Generate Images
    # -------------------------
    def add_caption_above_image(
        self,
        image_path: str,
        caption: str,
        font_size: int = 42,
        padding: int = 18,
        line_spacing: int = 8,
    ):
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        caption = caption.strip()

        try:
            font = ImageFont.truetype("impact.ttf", font_size)
        except OSError:
            font = ImageFont.truetype("arial.ttf", font_size)

        draw_dummy = ImageDraw.Draw(image)
        max_text_width = int(width * 0.90)

        words = caption.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = draw_dummy.textbbox((0, 0), test_line, font=font)
            test_width = bbox[2] - bbox[0]

            if test_width <= max_text_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        line_heights = []
        for line in lines:
            bbox = draw_dummy.textbbox((0, 0), line, font=font)
            line_heights.append(bbox[3] - bbox[1])

        caption_height = (
            padding * 2
            + sum(line_heights)
            + line_spacing * max(0, len(lines) - 1)
        )

        new_image = Image.new("RGB", (width, height + caption_height), "black")
        new_image.paste(image, (0, caption_height))

        draw = ImageDraw.Draw(new_image)
        y = padding
        for line, line_height in zip(lines, line_heights):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) / 2
            draw.text((x, y), line, fill="white", font=font)
            y += line_height + line_spacing

        temp_path = str(Path(image_path).with_suffix(".tmp.png"))
        new_image.save(temp_path)
        image.close()
        new_image.close()
        os.replace(temp_path, image_path)

    def add_signature_to_image(
        self,
        image_path: str,
        signature_path: str = "data/assets/sanpi_signature_v2.png",
        width_ratio: float = 0.16,
        margin_ratio: float = 0.025,
        opacity: float = 0.75,
    ):
        img = Image.open(image_path).convert("RGBA")
        sig = Image.open(signature_path).convert("RGBA")

        target_width = int(img.width * width_ratio)
        target_height = int(sig.height * target_width / sig.width)
        sig = sig.resize((target_width, target_height), Image.LANCZOS)

        if opacity < 1:
            alpha = sig.getchannel("A")
            alpha = alpha.point(lambda p: int(p * opacity))
            sig.putalpha(alpha)

        margin = int(img.width * margin_ratio)
        x = img.width - sig.width - margin
        y = img.height - sig.height - int(margin * 0.3)

        img.alpha_composite(sig, (x, y))
        img.convert("RGB").save(image_path, quality=95)

    def generate_caricatures(self, memes: list[MemeCandidate], sleep_seconds: int = 30):
        results = []
        for i, meme in enumerate(memes, start=1):
            safe_caption = "".join(
                c if c.isalnum() else "_"
                for c in meme.caption.lower()
            )[:30]

            filename = f"caricature_{i:03d}_{safe_caption}.png"
            image_result = self.image_client.generate(
                prompt=MEME_PROMPT.format(
                    visual_gag=meme.visual_gag,
                    image_prompt=meme.image_prompt,
                ),
                filename=filename,
            )

            self.add_signature_to_image(
                image_path=image_result.path,
                signature_path="data/assets/sanpi_signature_v2.png",
            )
            self.add_caption_above_image(
                image_path=image_result.path,
                caption=meme.caption,
            )

            meme.image_path = image_result.path
            meme.image_model = image_result.model
            results.append(meme)

            log_path = self.append_meme_log(meme)
            if self.verbose:
                print(f"Generated {filename}: {meme.caption}")
                print(f"Final image saved: {meme.image_path}")
                print(f"Meme logged: {log_path}")
            time.sleep(sleep_seconds)
        return results


    # -------------------------
    # Stage 7: Generate Hashtags
    # -------------------------
    def find_meme_log_record(self, image_name: str) -> dict | None:
        log_file = Path(self.meme_log_path)
        if not log_file.exists():
            return None

        image_stem = Path(image_name).stem
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                image = record.get("image", {})
                logged_name = image.get("image_name")
                if not logged_name:
                    continue

                if Path(logged_name).stem == image_stem:
                    return record
        return None

    def clean_hashtag(self, tag: str) -> str | None:
        if not tag:
            return None

        tag = tag.strip().lower()
        if not tag.startswith("#"):
            tag = "#" + tag
        tag = tag.replace(" ", "")

        allowed = set("abcçdefgğhıijklmnoöprsştuüvyzqwx0123456789_#")
        tag = "".join(c for c in tag if c in allowed)
        if len(tag) <= 1:
            return None
        return tag

    def generate_social_hashtags(self, record: dict) -> list[str]:
        sources = record.get("source_sources", [])
        source_trends = record.get("source_trends", [])
        source_urls = record.get("source_urls", [])

        context = {
            "group_name": record.get("group_name"),
            "summary": record.get("summary"),
            "sources": sources,
            "source_trends": source_trends,
            "source_urls": source_urls,
        }

        data = self.text_llm.generate_json(
            system=SOCIAL_HASHTAG_SYSTEM,
            prompt=SOCIAL_HASHTAG_PROMPT.format(
                context=json.dumps(context, ensure_ascii=False, indent=2)
            ),
            schema_hint=SOCIAL_HASHTAG_SCHEMA,
            temperature=0.5,
        )

        hashtags = []
        seen = set()
        for tag in data.get("hashtags", []):
            cleaned = self.clean_hashtag(tag)
            if not cleaned:
                continue
            if cleaned in MANDATORY_SOCIAL_HASHTAGS:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            hashtags.append(cleaned)

        for tag in MANDATORY_SOCIAL_HASHTAGS:
            if tag not in seen:
                hashtags.append(tag)
                seen.add(tag)
        return hashtags

    def get_social_post_text(self, image_name: str) -> str | None:
        record = self.find_meme_log_record(image_name)
        if not record:
            return None

        source_trends = record.get("source_trends", [])
        source_urls = record.get("source_urls", [])
        trends_text = "\n".join(source_trends) if source_trends else "Unknown"
        links_text = "\n".join(source_urls) if source_urls else "Unknown"
        hashtags = self.generate_social_hashtags(record)
        hashtags_text = " ".join(hashtags)
        post_text = (
            f"{trends_text}\n"
            f"{links_text}\n"
            ".\n"
            ".\n"
            ".\n"
            ".\n"
            ".\n"
            f"{hashtags_text}"
        )
        print(post_text)


    # -------------------------
    # Logging
    # -------------------------
    def append_meme_log(self, meme: MemeCandidate):
        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "group_name": meme.group_name,
            "source_trends": meme.source_trends,
            "source_urls": meme.source_urls,
            "source_sources": meme.source_sources,
            "summary": meme.summary,
            "angle": meme.angle,
            "visual_gag": meme.visual_gag,
            "caption": meme.caption,
            "humor_type": meme.humor_type,
            "image_prompt": meme.image_prompt,
            "scores": {
                "lens_score": meme.lens_score,
                "fun_score": meme.fun_score,
                "visual_score": meme.visual_score,
                "originality_score": meme.originality_score,
                "meme_score": meme.meme_score,
            },
            "models": {
                "text_model": meme.text_model,
                "image_model": meme.image_model,
            },
            "image": {
                "image_path": meme.image_path,
                "image_name": Path(meme.image_path).name if meme.image_path else None,
            },
        }

        Path(self.meme_log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.meme_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return self.meme_log_path

    def get_meme_news_text(self, image_name: str) -> str | None:
        log_file = Path(self.meme_log_path)
        if not log_file.exists():
            return None

        image_stem = Path(image_name).stem
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                image = record.get("image", {})
                logged_name = image.get("image_name")
                if not logged_name:
                    continue

                if Path(logged_name).stem != image_stem:
                    continue

                summary = record.get("summary", "")
                source_urls = record.get("source_urls", [])
                source_trends = record.get("source_trends", [])

                angle = record.get("angle", "")
                caption = record.get("caption", "")
                humor_type = record.get("humor_type", "")

                scores = record.get("scores", {})
                score = scores.get("meme_score", 0)
                lens = scores.get("lens_score", 0)
                visual = scores.get("visual_score", 0)
                fun = scores.get("fun_score", 0)
                originality = scores.get("originality_score", 0)


                links_text = "\n".join(f"- {url}" for url in source_urls) or "- Unknown"
                trends_text = "\n".join(f"- {trend}" for trend in source_trends) or "- Unknown"

                print(f"""
                    SUMMARY       : {summary}
                    NEWS          : {trends_text} | {links_text}
                    HUMOR         : {angle} | {humor_type} | {caption}
                    SCORE         : {score:.2f} (lens: {lens} / visual: {visual} / fun: {fun} / originality: {originality})
                """)


    # -------------------------
    # Display
    # -------------------------
    def score_text(self, obj) -> str:
        if isinstance(obj, AngleCandidate):
            return (
                f"{obj.angle_score:.2f} "
                f"(lens: {obj.lens_score} / visual: {obj.visual_score} / "
                f"fun: {obj.fun_score} / originality: {obj.originality_score} / "
            )
        return (
            f"{obj.meme_score:.2f} "
            f"(lens: {obj.lens_score} / visual: {obj.visual_score} / "
            f"fun: {obj.fun_score} / originality: {obj.originality_score})"
        )

    def print_angles(self, angles: list[AngleCandidate]):
        if not self.verbose:
            return

        print(f"\nANGLES: {len(angles)}\n")
        grouped = {}
        for angle in angles:
            grouped.setdefault(angle.group_name, []).append(angle)

        for group_name, group_angles in grouped.items():
            first = group_angles[0]
            print("=" * 80)
            print("GROUP        :", group_name)
            print("ARTICLES     :", first.article_indices)
            print("SOURCES      :", first.source_sources)
            print("SUMMARY      :", first.summary)
            print()
            for i, angle in enumerate(group_angles, start=1):
                print(f"  ANGLE {i}:", angle.angle)
            print()

    def print_ranked_angles(self, angles: list[AngleCandidate]):
        if not self.verbose:
            return

        print(f"\nRANKED ANGLES: {len(angles)}\n")
        grouped = {}
        for rank, angle in enumerate(angles, start=1):
            grouped.setdefault(angle.group_name, []).append((rank, angle))

        for group_name, ranked_group in grouped.items():
            first = ranked_group[0][1]
            print("=" * 80)
            print("GROUP        :", group_name)
            print("ARTICLES     :", first.article_indices)
            print("SOURCES      :", first.source_sources)
            print("SUMMARY      :", first.summary)
            print()
            for local_i, (rank, angle) in enumerate(ranked_group, start=1):
                print(f"  ANGLE {local_i} | RANK {rank}")
                print("  TEXT        :", angle.angle)
                print("  SCORE       :", self.score_text(angle))
                print()
            print()

    def print_memes(self, memes: list[MemeCandidate]):
        if not self.verbose:
            return

        print(f"\nMEMES: {len(memes)}\n")
        grouped = {}
        for meme in memes:
            grouped.setdefault(meme.group_name, []).append(meme)

        for group_name, group_memes in grouped.items():
            first = group_memes[0]
            print("=" * 80)
            print("GROUP        :", group_name)
            print("SOURCES      :", first.source_sources)
            print("SUMMARY      :", first.summary)
            print()
            for i, meme in enumerate(group_memes, start=1):
                print(f"  MEME {i}")
                print("  ANGLE       :", meme.angle)
                print("  CAPTION     :", meme.caption)
                print("  HUMOR TYPE  :", meme.humor_type)
                print("  SCORE       :", self.score_text(meme))
                print()
                print("  VISUAL GAG:")
                print(" ", meme.visual_gag)
                print()
                print("  IMAGE PROMPT:")
                print(" ", meme.image_prompt)
                print()
            print()

    def display_generated_memes(self, memes: list[MemeCandidate]):
        for i, meme in enumerate(memes, start=1):
            print("=" * 80)
            print("GENERATED MEME:", i)
            print("GROUP         :", meme.group_name)
            print("SUMMARY       :", meme.summary)
            print("SOURCES       :", ", ".join(meme.source_sources))
            print()

            print("ARTICLE LINKS:")
            if meme.source_urls:
                for url in meme.source_urls:
                    print("-", url)
            else:
                print("- None")

            print()
            print("SOURCE TRENDS:")
            for trend in meme.source_trends:
                print("-", trend)

            print()
            print("ANGLE         :", meme.angle)
            print("CAPTION       :", meme.caption)
            print("HUMOR TYPE    :", meme.humor_type)
            print("SCORE         :", self.score_text(meme))
            print("IMAGE MODEL   :", meme.image_model)
            print("IMAGE PROMPT  :", meme.image_prompt)
            print()

            if meme.image_path:
                image = Image.open(meme.image_path)
                display(image)
                image.close()
            print()


    # -------------------------
    # Full Run
    # -------------------------
    def run_caricatures(
        self,
        raw_trends: list[dict[str, Any]],
        generate_images: bool = True,
        min_angle_score: float = 8,
        min_meme_score: float = 7.0,
        max_angles: int | None = 10,
        max_memes: int | None = 5,
    ):
        """
            Get Trends
                ↓
            Generate Angle Groups
                ↓
            Rank Angles
                ↓
            Generate Memes
                ↓
            Quality Critic
                ↓
            Select Passing Memes
                ↓
            Image Model
        """
        if self.verbose:
            print("1.STEP: Generate Angle Groups...")

        angles = self.generate_angle_groups(raw_trends)

        if self.verbose:
            print("\n\n2.STEP: Rank Angles...")

        ranked_angles = self.rank_angles(angles)
        passing_angles = self.select_passing_angles(
            ranked_angles,
            min_score=min_angle_score,
            max_angles=max_angles,
        )

        if self.verbose:
            print("\n\n3.STEP: Meme Generation...")

        meme_candidates = self.generate_all_memes(passing_angles)

        if self.verbose:
            print("\n\n4.STEP: Meme Critique...")

        scored_memes = self.score_all_memes(meme_candidates)

        if self.verbose:
            print("\n\n5.STEP: Select Passing Memes...")

        selected_memes = self.select_passing_memes(
            scored_memes,
            min_score=min_meme_score,
            max_memes=max_memes,
        )

        if not generate_images:
            return selected_memes

        if self.verbose:
            print("\n\n6.STEP: Image Meme Generation...")

        generated_memes = self.generate_caricatures(selected_memes)
        return generated_memes

    
    # -------------------------
    # Meme Usecase
    # -------------------------
    def generate_news_groups(self, raw_trends: list[dict[str, Any]]) -> list[NewsGroup]:
        articles = []
        trends = []
        for item in raw_trends:
            trend = Trend(
                trend_name=item.get("trend_name", ""),
                source=item.get("source", ""),
                url=item.get("url"),
                metadata=item.get("metadata") or {},
            )
            if not trend.trend_name:
                continue

            trends.append(trend)
            articles.append(
                {
                    "index": len(trends) - 1,
                    "trend_name": trend.trend_name,
                    "source": trend.source,
                    "url": trend.url,
                    "summary": (trend.metadata or {}).get("summary", ""),
                }
            )

        data = self.text_llm.generate_json(
            system=NEWS_GROUPER_SYSTEM,
            prompt=NEWS_GROUPER_PROMPT.format(
                articles=json.dumps(articles, ensure_ascii=False, indent=2),
            ),
            schema_hint=NEWS_GROUP_SCHEMA,
            temperature=0.6,
        )

        groups = []
        for group in data.get("news_groups", []):
            idxs = [
                idx for idx in group.get("article_indices", [])
                if isinstance(idx, int) and 0 <= idx < len(trends)
            ]
            if not idxs:
                continue

            groups.append(
                NewsGroup(
                    group_name=group.get("group_name", trends[idxs[0]].trend_name),
                    article_indices=idxs,
                    source_trends=[trends[idx].trend_name for idx in idxs],
                    source_urls=[trends[idx].url for idx in idxs if trends[idx].url],
                    source_sources=sorted(set(trends[idx].source for idx in idxs)),
                    summary=group.get("summary", ""),
                )
            )

        if self.verbose:
            print(f"\nNEWS GROUPS: {len(groups)}\n")
            for group in groups:
                print("=" * 80)
                print("GROUP   :", group.group_name)
                print("ARTICLES:", group.article_indices)
                print("SOURCES :", group.source_sources)
                print("SUMMARY :", group.summary)
                print()

        return groups

    def load_templates(self) -> list[MemeTemplate]:
        with open(self.template_json_path, "r", encoding="utf-8") as f:
            items = json.load(f)

        templates = []
        for item in items:
            templates.append(
                MemeTemplate(
                    template_id=item["template_id"],
                    name=item["name"],
                    image_path=item["image_path"],
                    best_for=item.get("best_for", []),
                    layout=item.get("layout", ""),
                    notes=item.get("notes"),
                )
            )
        return templates

    def generate_template_memes_for_group(
        self,
        group: NewsGroup,
        templates: list[MemeTemplate],
    ) -> list[TemplateMemeCandidate]:
        templates_json = [
            {
                "template_id": template.template_id,
                "name": template.name,
                "best_for": template.best_for,
                "layout": template.layout,
                "notes": template.notes,
            } for template in templates
        ]

        data = self.text_llm.generate_json(
            system=TEMPLATE_MEME_SYSTEM,
            prompt=TEMPLATE_MEME_PROMPT.format(
                n_memes=self.n_memes_per_angle,
                group_name=group.group_name,
                source_trends=json.dumps(group.source_trends, ensure_ascii=False, indent=2),
                summary=group.summary or "",
                available_humor_types=HUMOR_TYPES,
                templates_json=json.dumps(templates_json, ensure_ascii=False, indent=2),
            ) + "\n\nSTYLE DIRECTION:\n" + self.style["extra_instruction"],
            schema_hint=TEMPLATE_MEME_SCHEMA,
            temperature=1.5,
        )

        template_by_id = {template.template_id: template for template in templates}
        memes = []
        for item in data.get("memes", []):
            template = template_by_id.get(item.get("template_id"))
            if not template:
                continue
            if item.get("humor_type") not in HUMOR_TYPES:
                continue

            meme_text = item.get("meme_text") or []
            if isinstance(meme_text, str):
                meme_text = [meme_text]
            memes.append(
                TemplateMemeCandidate(
                    group_name=group.group_name,
                    source_trends=group.source_trends,
                    source_urls=group.source_urls,
                    source_sources=group.source_sources,
                    summary=group.summary,
                    angle="",
                    visual_gag=f"Template meme using {template.name}",
                    caption=item.get("caption", ""),
                    humor_type=item["humor_type"],
                    image_prompt="",
                    template_id=template.template_id,
                    template_name=template.name,
                    template_path=template.image_path,
                    meme_text=meme_text,
                    edit_instruction=item.get("edit_instruction", ""),
                    reason=item.get("reason"),
                    text_model=getattr(self.text_llm, "last_used_model", None),
                )
            )
        return memes
    
    def generate_all_template_memes(
        self,
        groups: list[NewsGroup],
        templates: list[MemeTemplate],
    ) -> list[TemplateMemeCandidate]:
        all_memes = []
        for group in groups:
            try:
                memes = self.generate_template_memes_for_group(group, templates)
                all_memes.extend(memes)
                if self.verbose:
                    print(f"[TEMPLATE MEMES] generated {len(memes)} | {group.group_name}")
            except Exception as e:
                if self.verbose:
                    print(f"Template meme generation failed: {group.group_name} | {e}")
        return all_memes
    
    def generate_memes(
        self,
        memes: list[TemplateMemeCandidate],
        sleep_seconds: int = 30,
    ):
        results = []
        for i, meme in enumerate(memes, start=1):
            safe_caption = "".join(
                c if c.isalnum() else "_"
                for c in meme.caption.lower()
            )[:30]

            filename = f"meme_{i:03d}_{safe_caption}.png"
            prompt = TEMPLATE_IMAGE_EDIT_PROMPT.format(
                template_name=meme.template_name,
                text_json=json.dumps(meme.meme_text, ensure_ascii=False, indent=2),
                edit_instruction=meme.edit_instruction,
            )

            try:
                image_result = self.image_client.edit(
                    image_path=meme.template_path,
                    prompt=prompt,
                    filename=filename,
                )
            except Exception as e:
                meme.image_model = "failed"
                meme.image_path = None
                if self.verbose:
                    print("=" * 80)
                    print("Template meme image generation failed, skipping.")
                    print("Template:", meme.template_name)
                    print("Caption :", meme.caption)
                    print("Text    :", meme.meme_text)
                    print("Error   :", e)
                    print("=" * 80)
                self.append_meme_log(meme)
                continue

            meme.image_path = image_result.path
            meme.image_model = image_result.model

            self.add_signature_to_image(
                image_path=meme.image_path,
                signature_path="data/assets/sanpi_signature_v2.png",
            )
            self.append_meme_log(meme)
            results.append(meme)

            if self.verbose:
                print(f"Generated template meme: {meme.image_path}")
                print(f"Template: {meme.template_name}")
                print(f"Text: {meme.meme_text}")

            time.sleep(sleep_seconds)
        return results
    
    def run_memes(
        self,
        raw_trends: list[dict[str, Any]],
        generate_images: bool = True,
        min_meme_score: float = 7.0,
        max_groups: int | None = 10,
        max_memes: int | None = 5,
    ):
        """
            Get Trends
                ↓
            Group / summarize news
                ↓
            LLM selects template + writes meme text
                ↓
            Critic ranks template memes
                ↓
            Render / edit image
                ↓
            Save log
        """
        if self.verbose:
            print("1.STEP: Group / Summarize News...")
        groups = self.generate_news_groups(raw_trends)
        if max_groups is not None:
            groups = groups[:max_groups]

        if self.verbose:
            print("\n\n2.STEP: Load Templates...")
        templates = self.load_templates()

        if self.verbose:
            print(f"Loaded templates: {len(templates)}")
            print("\n\n3.STEP: Template Selection + Text Generation...")
        meme_candidates = self.generate_all_template_memes(
            groups=groups,
            templates=templates,
        )

        if self.verbose:
            print("\n\n4.STEP: Meme Critique...")
        scored_memes = self.score_all_memes(meme_candidates)

        if self.verbose:
            print("\n\n5.STEP: Select Passing Memes...")
        selected_memes = self.select_passing_memes(
            scored_memes,
            min_score=min_meme_score,
            max_memes=max_memes,
        )
        if not generate_images:
            return selected_memes
        
        if self.verbose:
            print("\n\n6.STEP: Template Image Editing / Rendering...")
        return self.generate_memes(selected_memes)