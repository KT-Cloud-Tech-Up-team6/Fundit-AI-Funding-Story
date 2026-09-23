from funding_story import image_jobs, provider, tasks
from funding_story.models import (
    CopyResult,
    FundingStoryContext,
    Review,
    RunCompletionResponse,
    UploadTarget,
    UploadTargetsResponse,
)

RUN_1 = "11111111-1111-4111-8111-111111111111"
RUN_2 = "22222222-2222-4222-8222-222222222222"


def context():
    return FundingStoryContext.model_validate(
        {
            "project": {
                "business_type": "SOLE",
                "category": {"major": "테크·가전", "minor": "생활가전"},
                "title": "테스트 제품",
                "goal_amount": 3_000_000,
            },
            "rewards": [
                {
                    "reward_id": 1,
                    "name": "기본 리워드",
                    "description": "본품 1대",
                    "price": 129_000,
                    "is_limited": False,
                    "quantity": None,
                    "is_early_bird": False,
                    "options": [],
                }
            ],
            "source_images": [],
        }
    )


def review():
    return Review(
        reply="확인",
        product="테스트 제품",
        story="테스트 이야기",
        strengths=[{"id": str(index), "title": "강점", "description": "설명"} for index in range(3)],
        problems=[{"heading": str(index), "body": "문제"} for index in range(4)],
    )


def scene():
    def block(block_id):
        return {
            "id": block_id,
            "label": block_id,
            "width": 860,
            "height": 1200,
            "categoryIds": ["main-visual"],
            "nodes": [
                {
                    "id": block_id + ".image",
                    "kind": "image",
                    "width": 860,
                    "height": 1200,
                    "assetId": "",
                    "pending": True,
                    "desc": "제품",
                }
            ],
        }

    return {"brand": "#647895", "blocks": [block("hero"), block("benefit")]}


class FakeApplication:
    def __init__(self):
        self.delivered = None

    def worker_context(self, run_id):
        return context(), review(), []

    def complete_run_delivery(self, run_id, status):
        self.delivered = (run_id, status)


class FakeBackend:
    def __init__(self):
        self.completion = None
        self.uploads = []

    def upload_targets(self, outputs):
        return UploadTargetsResponse(
            targets=[
                UploadTarget(
                    slot_id=output.slot_id,
                    upload_url=f"https://upload.example.com/{output.slot_id}",
                    file_url=f"https://cdn.example.com/{output.slot_id}.png",
                    expires_at="2099-01-01T00:00:00Z",
                )
                for output in outputs
            ]
        )

    def upload(self, upload_url, content):
        self.uploads.append((upload_url, content))

    def complete(self, run_id, body):
        self.completion = body
        return RunCompletionResponse(run_id=run_id, status=body.status)


def test_generation_retries_failed_image_then_reports_partial_success(monkeypatch):
    from funding_story.config import settings

    config = settings().model_copy(update={"image_retry_delay_seconds": 0.001})
    monkeypatch.setattr(image_jobs, "settings", lambda: config)
    application = FakeApplication()
    backend = FakeBackend()
    generated_scene = scene()
    draft = CopyResult(
        texts={},
        image_prompts={"hero.image": "hero", "benefit.image": "benefit"},
        summary="요약",
        storyline="이야기",
    )
    attempts = []

    def image(prompt, references, **__):
        attempts.append(prompt.splitlines()[0])
        if prompt.startswith("benefit"):
            raise provider.MissingImageError("image failed")
        return b"generated", "image/png"

    monkeypatch.setattr(tasks, "application", application)
    monkeypatch.setattr(tasks, "plan", lambda *_: (generated_scene, {}))
    monkeypatch.setattr(tasks, "generate_checked", lambda *args, **kwargs: draft)
    monkeypatch.setattr(tasks.provider, "image_once", image)
    monkeypatch.setattr(image_jobs.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        tasks,
        "render_scene",
        lambda current, sources, block_ids: [
            {
                "block_id": next(iter(block_ids)),
                "bytes": b"png",
                "width": 860,
                "height": 1200,
            }
        ],
    )
    monkeypatch.setattr(tasks, "BackendClient", lambda project: backend)

    tasks.generate({"id": RUN_1, "project_id": "project-1"})

    assert attempts.count("hero") == 1
    assert attempts.count("benefit") == config.image_generation_attempts
    assert backend.completion.status == "partially_succeeded"
    assert [failure.slot_id for failure in backend.completion.failed_slots] == ["benefit"]
    assert [image.slot_id for image in backend.completion.successful_images] == ["hero"]
    assert [block.type for block in backend.completion.generated_body.intro_content] == ["IMAGE"]
    assert application.delivered == (RUN_1, "partially_succeeded")


def test_execute_reports_failed_callback_when_generation_raises(monkeypatch):
    completion = []
    failed = []

    class ClaimingApplication:
        def claim_job(self, record_id):
            class Claim:
                def __enter__(self):
                    return {"id": record_id, "project_id": "project-1", "kind": "run"}

                def __exit__(self, *args):
                    return False

            return Claim()

        def complete_run_delivery(self, run_id, status):
            completion.append((run_id, status))

        def fail_job(self, run_id, exc):
            failed.append((run_id, exc))

    class FailingBackend:
        def complete(self, run_id, body):
            assert body.status == "failed"
            return RunCompletionResponse(run_id=run_id, status="failed")

    monkeypatch.setattr(tasks, "application", ClaimingApplication())
    monkeypatch.setattr(tasks, "generate", lambda row: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(tasks, "BackendClient", lambda project: FailingBackend())
    monkeypatch.setattr(tasks, "emit", lambda *args, **kwargs: None)

    tasks.execute(RUN_2)

    assert completion == [(RUN_2, "failed")]
    assert failed == []
