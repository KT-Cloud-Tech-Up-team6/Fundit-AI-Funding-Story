"""Run the production generation pipeline with real models and a local BE receiver.

Only final PNG/HTML artifacts are retained for the requested local preview.
No production project, external callback, database, or repository publication is used.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from html import escape
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_generation_runtime import local_backend, sample

from funding_story.config import Settings, settings
from funding_story.models import FundingStoryContext, SourceImageRef, StoryContext
from funding_story.planner import plan


def sha(value):
    return hashlib.sha256(value).hexdigest()


def completeness(expected, scene, fixed, draft, sources, body, uploaded, model_hashes, reference_hashes):
    """Independent full-output checks; no timing condition may relax completeness."""
    block_ids = [b["id"] for b in expected["blocks"]]
    image_ids = {n["id"] for b in expected["blocks"] for n in b["nodes"] if n["kind"] == "image"}
    text_ids = {
        n["id"]
        for b in expected["blocks"]
        for n in b["nodes"]
        if n["kind"] == "text" and n["id"] not in fixed
    }
    errors = []
    if body.status != "succeeded" or body.failed_slots:
        errors.append("not_fully_succeeded")
    if set(draft.texts) != text_ids or any(not str(v).strip() for v in draft.texts.values()):
        errors.append("missing_generated_text")
    if set(sources) != image_ids or set(draft.image_prompts) != image_ids:
        errors.append("missing_source_image")
    source_hashes = []
    for data, _ in sources.values():
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            visible = Image.new("RGBA", image.size, "white")
            visible.alpha_composite(image.convert("RGBA"))
            if all(low == high for low, high in visible.convert("RGB").getextrema()):
                errors.append("blank_generated_image")
        source_hashes.append(sha(data))
    if sorted(source_hashes) != sorted(model_hashes) or set(source_hashes) & set(reference_hashes):
        errors.append("source_not_fresh_model_output")
    if len(set(source_hashes)) != len(source_hashes):
        errors.append("duplicate_generated_image")
    if [b["id"] for b in scene["blocks"]] != block_ids:
        errors.append("changed_block_selection")
    expected_regions = {
        b["id"]: [(n["id"], n["kind"]) for n in b["nodes"]] for b in expected["blocks"]
    }
    for block in scene["blocks"]:
        if [(n["id"], n["kind"]) for n in block["nodes"]] != expected_regions.get(block["id"]):
            errors.append("changed_region_selection")
        for node in block["nodes"]:
            if node["kind"] == "image" and (node.get("pending") or node.get("assetId") != node["id"]):
                errors.append("unresolved_image_region")
            if node["kind"] == "text" and node.get("text") != fixed.get(
                node["id"], draft.texts.get(node["id"])
            ):
                errors.append("text_not_applied")
    if [item.slot_id for item in body.successful_images] != block_ids or set(uploaded) != set(block_ids):
        errors.append("missing_uploaded_png")
    if not body.generated_body:
        errors.append("missing_html")
    else:
        content = body.generated_body.intro_content
        if [b.slot_id for b in content if b.type == "IMAGE"] != block_ids:
            errors.append("missing_html_image")
        if content[-1].type != "TEXT" or not content[-1].value.strip():
            errors.append("missing_lower_html")
    return {
        "passed": not errors,
        "errors": sorted(set(errors)),
        "expected_blocks": len(block_ids),
        "expected_image_slots": len(image_ids),
        "generated_image_slots": len(sources),
        "expected_generated_text_slots": len(text_ids),
        "generated_text_slots": len(draft.texts),
        "fixed_text_slots": len(fixed),
        "source_image_sha256": source_hashes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--information-file", type=Path, default=ROOT / "tests/fixtures/story-information.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--model-profile",
        required=True,
        choices=("local_openai_smoke", "local_google_experiment"),
        help="Local real-model profile; runtime is verified only in the deployed dev/prod environment",
    )
    parser.add_argument(
        "--image-model",
        help="Google experiment model override; valid only with local_google_experiment",
    )
    args = parser.parse_args()
    overrides = {"model_profile": args.model_profile}
    if args.image_model:
        if args.model_profile != "local_google_experiment":
            parser.error("--image-model은 local_google_experiment에서만 사용할 수 있습니다.")
        overrides["image_model"] = args.image_model
    cfg = Settings(_env_file=args.env_file, **overrides)
    for key in (
        "ai_service_token",
        "google_cloud_project",
        "google_cloud_location",
        "model_profile",
        "text_model",
        "image_provider",
        "image_model",
        "image_quality",
        "openai_auth_mode",
        "openai_identity_provider_id",
        "openai_service_account_id",
        "openai_wif_audience",
        "openai_wif_token_file",
        "openai_project",
        "openai_organization",
        "image_generation_concurrency",
        "image_generation_attempts",
        "image_retry_delay_seconds",
        "image_retry_max_delay_seconds",
        "image_request_interval_seconds",
        "image_request_max_interval_seconds",
        "image_generation_budget_seconds",
        "render_concurrency",
        "pretendard_font_path",
    ):
        os.environ[key.upper()] = str(getattr(cfg, key))
    if cfg.openai_auth_mode == "api_key" and cfg.openai_api_key:
        os.environ["OPENAI_API_KEY"] = cfg.openai_api_key.get_secret_value()
    os.environ["APP_ENV"] = "local"
    settings.cache_clear()

    from funding_story import image_jobs, media, provider, tasks

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "images").mkdir()
    project, review, references = sample()
    information = json.loads(args.information_file.read_text())["information"]
    review.story_context = StoryContext(
        **{
            field: information.get(field, "").replace("V01 프로젝트 팀", "LUMI S1 프로젝트 팀")
            for field in StoryContext.model_fields
        }
    )
    expected_scene, fixed = plan(project, review)
    scene = expected_scene
    run_id = str(uuid.uuid4())
    started = time.perf_counter()
    stages = {}
    captured = {}
    model_hashes = []

    def record(event, **fields):
        row = {"event": event, "elapsed_seconds": round(time.perf_counter() - started, 2), **fields}
        line = json.dumps(row, ensure_ascii=False)
        print(line, flush=True)
        with (output / "execution.jsonl").open("a") as stream:
            stream.write(line + "\n")

    def timed(name, fn):
        def invoke(*args, **kwargs):
            record("stage_started", stage=name)
            start = time.perf_counter()
            result = fn(*args, **kwargs)
            stages[name] = round(time.perf_counter() - start, 3)
            record("stage_finished", stage=name, seconds=stages[name])
            return result

        return invoke

    def model_events(event, **fields):
        if event in ("provider_retry", "image_response_missing", "image_usage"):
            record(event, **{key: value for key, value in fields.items() if key != "feedback"})

    with local_backend(references[0][0]) as backend, ExitStack() as stack:
        context = FundingStoryContext(
            project={
                "business_type": project.business_type,
                "category": {"major": "테크·가전", "minor": "생활가전"},
                "title": project.title,
                "goal_amount": project.goal_amount,
            },
            rewards=project.rewards,
            source_images=[
                SourceImageRef(
                    slot_id="source",
                    read_url=backend.base + "/source.png",
                    content_type="image/png",
                    file_size=len(references[0][0]),
                    expires_at=datetime.now(UTC) + timedelta(hours=3),
                )
            ],
        )
        runtime_cfg = settings().model_copy(
            update={
                "project_service_base_url": backend.base,
                "internal_api_key": "local-preview-only",
            }
        )
        stack.enter_context(patch.object(media, "settings", lambda: runtime_cfg))
        stack.enter_context(
            patch.object(
                tasks,
                "application",
                SimpleNamespace(
                    worker_context=lambda _: (context, review, []),
                    complete_run_delivery=lambda _, status: setattr(backend, "delivered", status),
                ),
            )
        )
        stack.enter_context(patch.object(provider, "emit", model_events))
        stack.enter_context(patch.object(image_jobs, "emit", record))
        original_copy = tasks._write_copy

        def capture_copy(*values, **kwargs):
            result = original_copy(*values, **kwargs)
            captured.update(draft=result, scene=values[4])
            return result

        stack.enter_context(patch.object(tasks, "_write_copy", capture_copy))
        original_sources = tasks._generate_source_images

        def capture_sources(*values, **kwargs):
            result = original_sources(*values, **kwargs)
            captured["sources"] = result[0]
            return result

        stack.enter_context(patch.object(tasks, "_generate_source_images", capture_sources))
        for name, attr in [
            ("copy", "_write_copy"),
            ("images", "_generate_source_images"),
            ("render", "_render_blocks"),
            ("upload", "_upload_outputs"),
        ]:
            stack.enter_context(patch.object(tasks, attr, timed(name, getattr(tasks, attr))))

        original_image = provider.image_once

        def generate_image(prompt, reference_images, **kwargs):
            start = time.perf_counter()
            try:
                result = original_image(prompt, reference_images, **kwargs)
                model_hashes.append(sha(result[0]))
                record("image_generated", seconds=round(time.perf_counter() - start, 3))
                return result
            except Exception as exc:
                record(
                    "image_call_failed",
                    seconds=round(time.perf_counter() - start, 3),
                    status=getattr(exc, "status_code", None) or getattr(exc, "code", None),
                    error_type=type(exc).__name__,
                )
                raise

        stack.enter_context(patch.object(provider, "image_once", generate_image))

        original_upload = media.BackendClient.upload

        def save_final(client, upload_url, content):
            original_upload(client, upload_url, content)
            slot_id = urlparse(upload_url).path.rsplit("/", 1)[-1]
            if slot_id not in {block["id"] for block in scene["blocks"]}:
                raise ValueError("Unexpected output slot")
            (output / "images" / (slot_id + ".png")).write_bytes(content)

        stack.enter_context(patch.object(media.BackendClient, "upload", save_final))

        record(
            "run_started",
            blocks=len(scene["blocks"]),
            image_slots=sum(node["kind"] == "image" for b in scene["blocks"] for node in b["nodes"]),
            model_profile=runtime_cfg.model_profile,
            text_model=runtime_cfg.text_model,
            image_provider=runtime_cfg.image_provider,
            image_model=runtime_cfg.image_model,
            image_concurrency=runtime_cfg.image_generation_concurrency,
            policy={
                key: getattr(runtime_cfg, key)
                for key in (
                    "image_generation_attempts",
                    "image_retry_delay_seconds",
                    "image_retry_max_delay_seconds",
                    "image_request_interval_seconds",
                    "image_request_max_interval_seconds",
                    "image_generation_budget_seconds",
                )
            },
            template_sha256=sha((ROOT / "src/funding_story/resources/template.json").read_bytes()),
            input_sha256=sha(
                json.dumps(
                    {"project": project.model_dump(mode="json"), "review": review.model_dump(mode="json")},
                    sort_keys=True,
                ).encode()
            ),
            code_sha256={
                path: sha((ROOT / path).read_bytes())
                for path in (
                    "src/funding_story/config.py",
                    "src/funding_story/provider.py",
                    "src/funding_story/provider_errors.py",
                    "src/funding_story/image_jobs.py",
                    "src/funding_story/image_limiter.py",
                    "src/funding_story/planner.py",
                    "src/funding_story/tasks.py",
                    "src/funding_story/renderer.py",
                    "src/funding_story/body.py",
                )
            },
        )
        try:
            tasks.generate({"id": run_id, "project_id": "local-preview"})
        except Exception as exc:
            record("run_failed", error_type=type(exc).__name__, status=getattr(exc, "code", None))
            (output / "result.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "seconds": round(time.perf_counter() - started, 3),
                        "target_met": False,
                        "error_type": type(exc).__name__,
                        "stages": stages,
                    },
                    indent=2,
                )
            )
            raise
        body = backend.completion
        if body is None:
            raise RuntimeError("Completion was not delivered")
        elapsed = time.perf_counter() - started
        audit = completeness(
            expected_scene,
            captured["scene"],
            fixed,
            captured["draft"],
            captured["sources"],
            body,
            backend.uploaded,
            model_hashes,
            [sha(data) for data, _ in references],
        )
        lower_html = body.generated_body.intro_content[-1].value if body.generated_body else ""
        missing_facts = [
            key
            for key in StoryContext.model_fields
            if escape(getattr(review.story_context, key)).replace("\n", "<br>") not in lower_html
        ]
        if missing_facts:
            audit["errors"].append("missing_lower_facts")
            audit["passed"] = False
        metadata = {
            "status": body.status,
            "seconds": round(time.perf_counter() - started, 3),
            "generation_seconds": round(elapsed, 3),
            "completeness": audit,
            "target_met": audit["passed"] and time.perf_counter() - started <= 300,
            "measurement_scope": "local invocation preparation through PNG upload and completion ACK; all retry/pacing waits included",
            "not_measured": ["deployed API/DB polling delay", "real BE/S3 network", "FE display"],
            "model_profile": runtime_cfg.model_profile,
            "image_model": runtime_cfg.image_model,
            "image_provider": runtime_cfg.image_provider,
            "expected_blocks": len(scene["blocks"]),
            "successful_blocks": len(body.successful_images),
            "failed_slots": [f.model_dump() for f in body.failed_slots],
            "stages": stages,
            "input_kind": "existing fictional LUMI S1 project fixture",
            "execution": "production planner, real text/image models and PNG renderer; local BE receiver",
        }
        (output / "result.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        (output / "completion.json").write_text(body.model_dump_json(indent=2))
        if body.generated_body:
            labels = {block["id"]: block["label"] for block in scene["blocks"]}
            blocks = []
            for block in body.generated_body.intro_content:
                if block.type == "IMAGE":
                    blocks.append(
                        '<img src="images/'
                        + escape(block.slot_id)
                        + '.png" alt="'
                        + escape(labels[block.slot_id])
                        + '" width="860">'
                    )
                else:
                    blocks.append('<section class="information">' + block.value + "</section>")
            html = (
                """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LUMI S1 · 전체 상세페이지 생성 결과</title>
<style>body{margin:0;background:#e7e9ec;color:#222;font-family:Arial,sans-serif}
main{max-width:860px;margin:auto;background:white}main>img{display:block;width:100%;height:auto}
.information{padding:48px 40px;line-height:1.8;font-size:18px;overflow-wrap:anywhere}
.information p{margin:0 0 20px}.information p:has(>strong){margin:40px 0 14px;font-size:24px}
.information p:first-child{margin-top:0}</style><main>"""
                + "".join(blocks)
                + "</main></html>"
            )
            (output / "index.html").write_text(html)
        record("run_finished", **metadata)
        if not audit["passed"]:
            raise SystemExit("Full-output audit failed; see result.json (never counted as success)")


if __name__ == "__main__":
    main()
