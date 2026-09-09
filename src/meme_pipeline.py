import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from IPython.display import display, Markdown, Image as IPyImage
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from typing import Any
from src.utils.data_classes import *
from src.utils.google_drive_uploader import upload_file_to_drive
from src.utils.prompts import *
from src.utils.schemas import *
from textwrap import dedent


class MemePipeline:

    def __init__(
        self,
        text_llm,
        image_client,
        style_profile="balanced",
        n_angles_per_group=3,
        n_memes_per_angle=3,
        verbose=True,
        upload_to_drive=False
    ):
        self.text_llm = text_llm
        self.image_client = image_client
        self.style_profile = style_profile
        self.style = STYLE_PROFILES[style_profile]
        self.n_angles_per_group = n_angles_per_group
        self.n_memes_per_angle = n_memes_per_angle
        self.verbose = verbose

        self.upload_to_drive = upload_to_drive
        self.temp_root_dir = None
        self.template_json_path = "data/assets/meme_template_v2.json"

        if self.upload_to_drive:
            self.temp_root_dir = Path(tempfile.mkdtemp(prefix="tmp"))
            self.output_dir = self.temp_root_dir / "memes"
            self.meme_log_path = str(self.temp_root_dir / "meme_log.jsonl")
        else:
            self.output_dir = Path("data/memes")
            self.meme_log_path = "data/assets/meme_log.jsonl"

        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.verbose:
            print(f"Output dir: {self.output_dir}")
            print(f"Meme log path: {self.meme_log_path}")


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

    def upload_outputs_to_drive(self):
        if not self.upload_to_drive:
            return []
        uploaded = []
        files_to_upload = []
        if self.output_dir.exists():
            files_to_upload.extend(
                path for path in self.output_dir.iterdir()
                if path.is_file() and path.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]
            )

        log_path = Path(self.meme_log_path)
        if log_path.exists():
            files_to_upload.append(log_path)
        for file_path in files_to_upload:
            try:
                result = upload_file_to_drive(str(file_path))
                uploaded.append(result)
                if self.verbose:
                    print(f"Uploaded to Drive: {file_path.name}")
            except Exception as e:
                print(f"Google Drive upload failed for {file_path}: {e}")
        return uploaded

    def cleanup_temp_outputs(self):
        if not self.upload_to_drive:
            return
        if self.temp_root_dir and Path(self.temp_root_dir).exists():
            shutil.rmtree(self.temp_root_dir, ignore_errors=True)
            if self.verbose:
                print(f"Removed temp folder: {self.temp_root_dir}")

    def finalize_outputs(self):
        if not self.upload_to_drive:
            return
        try:
            self.upload_outputs_to_drive()
        finally:
            self.cleanup_temp_outputs()


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
        humor_types_text = "\n".join(
            f"- {name}: {description}"
            for name, description in HUMOR_TYPE_GUIDE.items()
        )
        data = self.text_llm.generate_json(
            system=MEME_GENERATOR_SYSTEM,
            prompt=MEME_GENERATOR_PROMPT.format(
                n_memes=self.n_memes_per_angle,
                group_name=angle.group_name,
                source_trends=json.dumps(angle.source_trends, ensure_ascii=False, indent=2),
                summary=angle.summary or "",
                angle=angle.angle,
                available_humor_types=humor_types_text,
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
        memes_json = []
        for i, meme in enumerate(memes):
            if isinstance(meme, TemplateMemeCandidate):
                memes_json.append({
                    "meme_index": i,
                    "template_name": meme.template_name,
                    "joke_pattern": meme.visual_gag,
                    "layout": meme.template_layout,
                    "instructions": meme.template_instructions,
                    "meme_text": meme.meme_text,
                    "caption": meme.caption,
                    "humor_type": meme.humor_type,
                    "edit_instruction": meme.edit_instruction,
                })
            else:
                memes_json.append({
                    "meme_index": i,
                    "visual_gag": meme.visual_gag,
                    "caption": meme.caption,
                    "humor_type": meme.humor_type,
                    "image_prompt": meme.image_prompt,
                })
        humor_types_text = "\n".join(
            f"- {name}: {description}"
            for name, description in HUMOR_TYPE_GUIDE.items()
        )
        data = self.text_llm.generate_json(
            system=QUALITY_CRITIC_SYSTEM,
            prompt=QUALITY_CRITIC_PROMPT.format(
                group_name=group_name,
                summary=summary or "",
                angle=angle,
                available_humor_types=humor_types_text,
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
        selected = [meme for meme in memes
                    if meme.meme_score >= min_score
                    and meme.fun_score >= 7]
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
                output_dir=str(self.output_dir),
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
            try:
                self.generate_social_post_text(meme)
            except Exception as e:
                meme.hashtags = []
                meme.social_post_text = None
                if self.verbose:
                    print(f"Social post generation failed: {e}")

            results.append(meme)
            log_path = self.append_meme_log(meme)
            if self.verbose:
                print(f"Generated {filename}: {meme.caption}")
                print(f"Final image saved: {meme.image_path}")
                print(f"Meme logged: {log_path}")
            time.sleep(sleep_seconds)
        return results


    # -------------------------
    # Stage 7: Generate Social Post
    # -------------------------
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

    def generate_social_hashtags(
        self,
        meme_or_record: MemeCandidate | dict,
    ) -> list[str]:
        if isinstance(meme_or_record, MemeCandidate):
            context = {
                "group_name": meme_or_record.group_name,
                "summary": meme_or_record.summary,
                "sources": meme_or_record.source_sources,
                "source_trends": meme_or_record.source_trends,
                "source_urls": meme_or_record.source_urls,
                "caption": meme_or_record.caption,
                "humor_type": meme_or_record.humor_type,
            }
        else:
            context = {
                "group_name": meme_or_record.get("group_name"),
                "summary": meme_or_record.get("summary"),
                "sources": meme_or_record.get("source_sources", []),
                "source_trends": meme_or_record.get("source_trends", []),
                "source_urls": meme_or_record.get("source_urls", []),
                "caption": meme_or_record.get("caption"),
                "humor_type": meme_or_record.get("humor_type"),
            }

        data = self.text_llm.generate_json(
            system=SOCIAL_HASHTAG_SYSTEM,
            prompt=SOCIAL_HASHTAG_PROMPT.format(
                context=json.dumps(
                    context,
                    ensure_ascii=False,
                    indent=2,
                )
            ),
            schema_hint=SOCIAL_HASHTAG_SCHEMA,
            temperature=0.5,
        )

        hashtags = []
        seen = set()
        mandatory_hashtags = {
            tag.lower()
            for tag in MANDATORY_SOCIAL_HASHTAGS
        }

        for tag in data.get("hashtags", []):
            cleaned = self.clean_hashtag(tag)
            if not cleaned:
                continue
            if cleaned in mandatory_hashtags:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            hashtags.append(cleaned)

        for tag in MANDATORY_SOCIAL_HASHTAGS:
            cleaned = self.clean_hashtag(tag)
            if cleaned and cleaned not in seen:
                hashtags.append(cleaned)
                seen.add(cleaned)

        return hashtags

    def generate_social_post_text(
        self,
        meme: MemeCandidate,
    ) -> str:
        hashtags = self.generate_social_hashtags(meme)
        trends_text = (
            "\n".join(meme.source_trends)
            if meme.source_trends else "Unknown"
        )
        links_text = (
            "\n".join(meme.source_urls)
            if meme.source_urls else "Unknown"
        )
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
        meme.hashtags = hashtags
        meme.social_post_text = post_text
        return post_text

    def generate_social_post_for_given_record(
        self,
        record: dict,
    ) -> tuple[list[str], str]:
        hashtags = self.generate_social_hashtags(record)
        source_trends = record.get("source_trends", [])
        source_urls = record.get("source_urls", [])
        trends_text = (
            "\n".join(source_trends)
            if source_trends
            else "Unknown"
        )
        links_text = (
            "\n".join(source_urls)
            if source_urls
            else "Unknown"
        )
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
        return hashtags, post_text


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
            "template": {
                "template_id": getattr(meme, "template_id", None),
                "template_name": getattr(meme, "template_name", None),
                "meme_text": getattr(meme, "meme_text", None),
                "edit_instruction": getattr(meme, "edit_instruction", None),
            },
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
                "image_name": (
                    Path(meme.image_path).name
                    if meme.image_path
                    else None
                ),
            },
            "social": {
                "hashtags": meme.hashtags or [],
                "post_text": meme.social_post_text,
            },
        }

        Path(self.meme_log_path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            self.meme_log_path,
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                ) + "\n"
            )

        return self.meme_log_path
    
    def update_meme_log_record(
        self,
        image_name: str,
        updated_record: dict,
    ) -> bool:
        log_file = Path(self.meme_log_path)
        if not log_file.exists():
            return False

        image_stem = Path(image_name).stem
        records = []
        updated = False
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                logged_name = record.get("image", {}).get("image_name")
                if (
                    not updated
                    and logged_name
                    and Path(logged_name).stem == image_stem
                ):
                    records.append(updated_record)
                    updated = True
                else:
                    records.append(record)

        if not updated:
            return False

        temp_file = log_file.with_suffix(log_file.suffix + ".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            for record in records:
                f.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                    ) + "\n"
                )
        os.replace(temp_file, log_file)
        return True

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
    
    def get_meme_news_text(self, image_name: str) -> str | None:
        record = self.find_meme_log_record(image_name)
        if not record:
            print(f"Meme log record not found: {image_name}")
            return None

        social = record.get("social") or {}
        social_post_text = social.get("post_text")
        hashtags = social.get("hashtags") or []
        if not social_post_text:
            if self.verbose:
                print(
                    f"[SOCIAL BACKFILL] Social post missing for "
                    f"{image_name}. Generating..."
                )

            try:
                hashtags, social_post_text = (
                    self.generate_social_post_for_given_record(record)
                )
                record["social"] = {
                    **social,
                    "hashtags": hashtags,
                    "post_text": social_post_text,
                    "model": getattr(
                        self.text_llm,
                        "last_used_model",
                        None,
                    ),
                    "generated_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "backfilled": True,
                }
                updated = self.update_meme_log_record(
                    image_name=image_name,
                    updated_record=record,
                )

                if self.verbose:
                    if updated:
                        print(
                            f"[SOCIAL BACKFILL] Generated "
                            f"{len(hashtags)} hashtags and updated log."
                        )
                    else:
                        print(
                            "[SOCIAL BACKFILL] Social post generated, "
                            "but log entry could not be updated."
                        )

            except Exception as e:
                print(
                    f"[SOCIAL BACKFILL] Generation failed for "
                    f"{image_name}: {e}"
                )
                hashtags = []
                social_post_text = None

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

        links_text = (
            "\n".join(f"- {url}" for url in source_urls)
            or "- Unknown"
        )
        trends_text = (
            "\n".join(f"- {trend}" for trend in source_trends)
            or "- Unknown"
        )
        hashtags_text = (
            " ".join(hashtags)
            if hashtags
            else "Not generated"
        )
        social_post_display = (
            social_post_text
            if social_post_text
            else "Not generated"
        )

        output = dedent(
            f"""
                SUMMARY
                {summary or "Unknown"}

                NEWS
                {trends_text}

                ARTICLE LINKS
                {links_text}

                HUMOR
                Angle       : {angle or "N/A"}
                Type        : {humor_type or "Unknown"}
                Caption     : {caption or "Unknown"}

                SCORE
                {score:.2f} (
                    lens: {lens}
                    visual: {visual}
                    fun: {fun}
                    originality: {originality}
                )

                HASHTAGS
                {hashtags_text}

                SOCIAL POST
                {social_post_display}
            """
        ).strip()
        print(output)
        return output


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
        for meme in memes:
            print("=" * 80)
            image_name = (
                Path(meme.image_path).stem
                if meme.image_path
                else "Unknown"
            )

            display(Markdown(f"## `{image_name}`"))
            if getattr(meme, "social_post_text", None):
                print(meme.social_post_text)
                print()
            if meme.image_path:
                display(IPyImage(filename=meme.image_path, width=500, height=500))


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
            Generate Image
                ↓
            Generate Social Post
                ↓
            Save Log
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
            print("\n\n6.STEP: Image & Social Post Generation + Logging...")

        try:
            generated_memes = self.generate_caricatures(selected_memes)
            return generated_memes
        finally:
            self.finalize_outputs()

    
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
            temperature=0.2,
        )

        groups = []
        used_indices = set()
        for group in data.get("news_groups", []):
            idxs = list(dict.fromkeys(
                idx for idx in group.get("article_indices", [])
                if isinstance(idx, int)
                and 0 <= idx < len(trends)
                and idx not in used_indices
            ))[:3]
            if not idxs:
                continue

            used_indices.update(idxs)

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

        # Anything omitted by the LLM becomes its own group.
        for idx, trend in enumerate(trends):
            if idx in used_indices:
                continue

            groups.append(
                NewsGroup(
                    group_name=trend.trend_name,
                    article_indices=[idx],
                    source_trends=[trend.trend_name],
                    source_urls=[trend.url] if trend.url else [],
                    source_sources=[trend.source],
                    summary=(trend.metadata or {}).get("summary", ""),
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
            data = json.load(f)

        return [
            MemeTemplate(
                template_id=item["template_id"],
                name=item["name"],
                image_path=item["image_path"],
                joke_pattern=item["joke_pattern"],
                layout=item["layout"],
                instructions=item["instructions"],
            )
            for item in data["templates"]
        ]

    def shortlist_templates_for_group(
        self,
        group: NewsGroup,
        templates: list[MemeTemplate],
        n_templates: int = 3,
    ) -> list[MemeTemplate]:

        templates_json = [
            {
                "template_id": t.template_id,
                "joke_pattern": t.joke_pattern,
            } for t in templates
        ]

        data = self.text_llm.generate_json(
            system=TEMPLATE_SHORTLIST_SYSTEM,
            prompt=TEMPLATE_SHORTLIST_PROMPT.format(
                group_name=group.group_name,
                source_trends=json.dumps(
                    group.source_trends,
                    ensure_ascii=False,
                    indent=2,
                ),
                summary=group.summary or "",
                templates_json=json.dumps(
                    templates_json,
                    ensure_ascii=False,
                    indent=2,
                ),
                n_templates=n_templates,
            ),
            schema_hint=TEMPLATE_SHORTLIST_SCHEMA,
            temperature=0.3,
        )

        template_by_id = {
            t.template_id: t
            for t in templates
        }

        selected = []
        seen = set()
        for template_id in data.get("template_ids", []):
            if template_id in template_by_id and template_id not in seen:
                selected.append(template_by_id[template_id])
                seen.add(template_id)

        return selected[:n_templates]

    def generate_template_memes_for_group(
        self,
        group: NewsGroup,
        templates: list[MemeTemplate],
    ) -> list[TemplateMemeCandidate]:
        templates_json = [
            {
                "template_id": template.template_id,
                "name": template.name,
                "joke_pattern": template.joke_pattern,
                "layout": template.layout,
                "instructions": template.instructions,
            } for template in templates
        ]
        humor_types_text = "\n".join(
            f"- {name}: {description}"
            for name, description in HUMOR_TYPE_GUIDE.items()
        )
        data = self.text_llm.generate_json(
            system=TEMPLATE_MEME_SYSTEM,
            prompt=TEMPLATE_MEME_PROMPT.format(
                n_memes=len(templates),
                group_name=group.group_name,
                source_trends=json.dumps(group.source_trends, ensure_ascii=False, indent=2),
                summary=group.summary or "",
                available_humor_types=humor_types_text,
                templates_json=json.dumps(templates_json, ensure_ascii=False, indent=2),
            ) + "\n\nSTYLE DIRECTION:\n" + self.style["extra_instruction"],
            schema_hint=TEMPLATE_MEME_SCHEMA,
            temperature=1.0,
        )

        template_by_id = {template.template_id: template for template in templates}
        memes = []
        seen_templates = set()
        for item in data.get("memes", []):
            template = template_by_id.get(item.get("template_id"))
            if not template:
                continue
            if template.template_id in seen_templates:
                continue
            if item.get("humor_type") not in HUMOR_TYPES:
                continue
            seen_templates.add(template.template_id)

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
                    visual_gag=template.joke_pattern,
                    caption=item.get("caption", ""),
                    humor_type=item["humor_type"],
                    image_prompt="",
                    template_id=template.template_id,
                    template_name=template.name,
                    template_path=template.image_path,
                    template_layout=template.layout,
                    template_instructions=template.instructions,
                    meme_text=meme_text,
                    edit_instruction=item.get("edit_instruction", ""),
                    text_model=getattr(self.text_llm, "last_used_model", None),
                )
            )
        return memes
    
    def generate_all_template_memes(
        self,
        groups: list[NewsGroup],
        templates: list[MemeTemplate],
        n_templates: int = 3,
    ) -> list[TemplateMemeCandidate]:
        all_memes = []
        for group in groups:
            try:
                shortlisted = self.shortlist_templates_for_group(
                    group, templates, n_templates=n_templates
                )
                memes = self.generate_template_memes_for_group(
                    group, shortlisted
                )

                all_memes.extend(memes)
                if self.verbose:
                    print(
                        f"[TEMPLATE MEMES] "
                        f"{len(shortlisted)} shortlisted → "
                        f"{len(memes)} written | {group.group_name}"
                    )

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
                template_layout=meme.template_layout,
                template_instructions=meme.template_instructions,
                text_json=json.dumps(
                    meme.meme_text,
                    ensure_ascii=False,
                    indent=2,
                ),
                edit_instruction=meme.edit_instruction,
            )

            try:
                image_result = self.image_client.edit(
                    image_path=meme.template_path,
                    prompt=prompt,
                    filename=filename,
                    output_dir=str(self.output_dir),
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
            try:
                self.generate_social_post_text(meme)
            except Exception as e:
                meme.hashtags = []
                meme.social_post_text = None
                if self.verbose:
                    print(f"Social post generation failed: {e}")
                        
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
        n_templates: int = 3,
    ):
        """
            Get Trends
                ↓
            Group / summarize news
                ↓
            Load meme templates
                ↓
            Shortlist fitting templates for each news group
                ↓
            Write one meme for every shortlisted template
                ↓
            Critic scores all written meme candidates
                ↓
            Select global Top-N memes
                ↓
            Render / edit Top-N templates only
                ↓
            Generate social post
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
            print("\n\n3.STEP: Shortlist Templates + Write Meme Candidates...")
        meme_candidates = self.generate_all_template_memes(
            groups=groups,
            templates=templates,
            n_templates=n_templates,
        )

        if self.verbose:
            print("\n\n4.STEP: Meme Critique...")
        scored_memes = self.score_all_memes(meme_candidates)

        if self.verbose:
            print("\n\n5.STEP: Global Top Meme Selection...")
        selected_memes = self.select_passing_memes(
            scored_memes,
            min_score=min_meme_score,
            max_memes=max_memes,
        )
        if not generate_images:
            return selected_memes
        
        if self.verbose:
            print("\n\n6.STEP: Template Image Editing & Social Post Generation + Logging...")
        try:
            return self.generate_memes(selected_memes)
        finally:
            self.finalize_outputs()