import base64
import io
import json
from collections import Counter
from pathlib import Path
from threading import Event

from PIL import Image
from test_application import FakeRecordRepository, complete_review, context
from test_recovery import FakeBackend

from funding_story import tasks
from funding_story.application import FundingStoryApplication
from funding_story.models import (
    ConfirmRequest,
    CopyResult,
    MessageRequest,
    Review,
    RunRequest,
    SessionCreateRequest,
)
from funding_story.planner import CATALOG
from funding_story.renderer import browser_page


def test_chat_confirm_real_parallel_png_html_and_completion(monkeypatch, tmp_path):
    repository = FakeRecordRepository()
    application = FundingStoryApplication(repository)
    backend = FakeBackend()
    monkeypatch.setattr(tasks, "application", application)
    monkeypatch.setattr(tasks, "BackendClient", lambda _: backend)
    session_id = application.create_session("project-1", SessionCreateRequest(context=context()))[
        "session_id"
    ]
    chat, _ = application.add_message(
        session_id,
        MessageRequest(
            message_id="facts",
            revision=1,
            text=(
                "예산은 금형 제작에 사용하고 11월 중순 제작합니다. 저희는 생활가전 개발팀입니다. "
                "불량 발생 시 교환하고 부품 지연 시 공지합니다."
            ),
        ),
        "project-1",
    )
    review = Review.model_validate(
        {
            **complete_review(),
            "story_context": {
                "budget": "금형 제작에 사용합니다.",
                "schedule": "11월 중순 제작",
                "team": "생활가전 개발팀",
                "policy": "불량 발생 시 교환",
                "risks": "부품 지연 시 공지",
            },
        }
    )
    monkeypatch.setattr(tasks, "generate_checked", lambda *args, **kwargs: review)
    monkeypatch.setattr(tasks.provider, "chat_stream", lambda _: iter(["요약을 확인해 주세요."]))
    tasks.execute(chat["chat_id"])
    public = application.get_session(session_id, "project-1")
    assert public["revision"] == 2
    assert set(public["summary"]) == {"product", "story", "strengths"}
    reply = public["messages"][-1]["text"]
    assert "프로젝트 예산\n금형 제작에 사용합니다." in reply
    assert "프로젝트 일정\n11월 중순 제작" in reply
    assert "예상되는 어려움\n부품 지연 시 공지" in reply
    application.confirm_session(session_id, ConfirmRequest(revision=2), "project-1")
    run, _ = application.create_run(
        RunRequest(
            session_id=session_id,
            confirmed_revision=2,
            idempotency_key="generate",
            context=context(),
        ),
        "project-1",
    )

    blocks = []
    for block_id in ("hero", "benefit"):
        block = json.loads(json.dumps(CATALOG["promise"]).replace("promise.", block_id + "."))
        block["id"] = block_id
        for node in block["nodes"]:
            if node["kind"] == "image":
                node.update(assetId="", pending=True)
        blocks.append(block)
    scene = {"brand": "#647895", "blocks": blocks}
    draft = CopyResult(
        texts={},
        image_prompts={
            node["id"]: node["id"] for block in blocks for node in block["nodes"] if node["kind"] == "image"
        },
        summary="요약",
        storyline="이야기",
    )
    monkeypatch.setattr(tasks, "plan", lambda *_: (scene, {}))
    monkeypatch.setattr(tasks, "generate_checked", lambda *args, **kwargs: draft)
    original = (Path(__file__).parent / "fixtures/original.png").read_bytes()
    second_finished = Event()

    def image(prompt, _, **__):
        if prompt.startswith("hero."):
            assert second_finished.wait(timeout=10)
        else:
            second_finished.set()
        return original, "image/png"

    monkeypatch.setattr(tasks.provider, "image_once", image)
    tasks.execute(run["run_id"])
    completion = backend.completion
    assert completion.status == "succeeded"
    assert [item.slot_id for item in completion.successful_images] == ["hero", "benefit"]
    assert len(backend.uploads) == 2
    for _, png in backend.uploads:
        with Image.open(io.BytesIO(png)) as rendered:
            assert rendered.format == "PNG" and rendered.size == (860, 1724)
    assert completion.generated_body.intro_content[-1].type == "TEXT"
    html = completion.generated_body.intro_content[-1].value
    assert "금형 제작에 사용합니다." in html and "11월 중순 제작" in html
    assert "리워드 상세 설명" not in html and "본품 1대" not in html
    assert set(repository.get(run["run_id"])["data"]) == {"status", "confirmed_revision", "error"}
    assert "generated_body" not in json.dumps(repository.records, ensure_ascii=False)

    # Local display harness only: FE must implement this composition on its own boundary.
    images = "".join(
        '<img style="display:block;width:100%" src="data:image/png;base64,'
        + base64.b64encode(png).decode()
        + '">'
        for _, png in backend.uploads
    )
    with browser_page() as page:
        page.locator("body").evaluate(
            "(el, html) => {el.innerHTML = html;}",
            '<main style="width:860px;max-width:100%;font-family:Pretendard">' + images + html + "</main>",
        )
        page.locator("main > img").last.wait_for()
        page.wait_for_function(
            "[...document.images].every(image => image.complete && image.naturalWidth > 0)"
        )
        assert page.locator("main > img").count() == 2
        assert "<p>" not in page.locator("main").inner_text()
        assert page.locator("main > p > strong").first.inner_text() == "프로젝트 예산"
        assert page.locator("main > p").first.bounding_box()["y"] >= 3448
        assert (
            page.locator("main > p > strong").first.evaluate("el => getComputedStyle(el).fontWeight") == "700"
        )
        page.screenshot(path=str(tmp_path / "generated-story.png"), full_page=True)


def test_parallel_render_retries_only_failed_block_and_keeps_order(monkeypatch):
    calls = Counter()
    second_finished = Event()
    scene = {"blocks": [{"id": slot, "nodes": []} for slot in ("first", "second", "broken")]}

    def render(_scene, _sources, ids):
        slot = next(iter(ids))
        calls[slot] += 1
        if slot == "first":
            assert second_finished.wait(timeout=5)
            if calls[slot] == 1:
                raise RuntimeError("transient renderer failure")
        if slot == "second":
            second_finished.set()
        if slot == "broken":
            raise RuntimeError("persistent renderer failure")
        return [{"block_id": slot}]

    monkeypatch.setattr(tasks, "render_scene", render)
    failed = {}
    rendered = tasks._render_blocks(scene, {}, failed)
    assert [item["block_id"] for item in rendered] == ["first", "second"]
    assert calls == Counter(first=2, second=1, broken=2)
    assert list(failed) == ["broken"] and failed["broken"].stage == "rendering"
