from pathlib import Path
from uuid import uuid4

import pytest
from test_contracts import review

from funding_story import assets, store, tasks
from funding_story.models import CopyResult, ProjectInput
from funding_story.planner import plan


def test_pipeline_retries_only_failed_images_and_restores_other_blocks(monkeypatch):
    project = str(uuid4())
    blob = Path("tests/fixtures/original.png").read_bytes()
    aid = assets.put(project, blob)
    info = ProjectInput.model_validate_json(Path("tests/fixtures/appliance.json").read_text())
    info.asset_ids = [aid]
    scene, fixed = plan(info, review())
    draft = CopyResult(
        texts={
            n["id"]: n["text"]
            for b in scene["blocks"]
            for n in b["nodes"]
            if n["kind"] == "text" and n["id"] not in fixed
        },
        image_prompts={n["id"]: n["id"] for b in scene["blocks"] for n in b["nodes"] if n["kind"] == "image"},
        summary="test",
        storyline="test",
    )
    monkeypatch.setattr(tasks, "generate_checked", lambda *a, **k: draft)
    calls = []

    def image(prompt, refs):
        calls.append(prompt.splitlines()[0])
        if calls.count("hero.image") == 1 and prompt.startswith("hero.image"):
            raise ValueError("missing image")
        return blob, "image/png"

    monkeypatch.setattr(tasks.provider, "image", image)
    rid = store.create(
        project,
        "run",
        {
            "status": "running",
            "snapshot": {"input": info.model_dump(), "review": review().model_dump()},
            "source_revision": 1,
            "image_jobs": {},
        },
    )
    tasks.generate(store.get(rid))
    first = store.get(rid)["data"]
    assert first["status"] == "partially_succeeded"
    successful = {k: v["asset_id"] for k, v in first["image_jobs"].items() if v["status"] == "succeeded"}
    tasks.generate(store.get(rid))
    final = store.get(rid)["data"]
    assert final["status"] == "succeeded"
    assert calls.count("hero.image") == 2
    assert all(calls.count(k) == 1 for k in successful)
    assert all(final["image_jobs"][k]["asset_id"] == v for k, v in successful.items())
    assert all(
        not n.get("pending")
        for b in final["document"]["scene"]["blocks"]
        for n in b["nodes"]
        if n["kind"] == "image"
    )


@pytest.mark.parametrize("count", [1, 2])
def test_unregistered_reward_slots_are_persisted(monkeypatch, count):
    info = ProjectInput.model_validate_json(Path("tests/fixtures/appliance.json").read_text())
    info.rewards = info.rewards[:count]
    project = str(uuid4())
    blob = Path("tests/fixtures/original.png").read_bytes()
    info.asset_ids = [assets.put(project, blob)]
    scene, _ = plan(info, review())
    scene["blocks"] = scene["blocks"][-1:]
    rid = store.create(
        project, "run", {"status": "running", "snapshot": {"input": info.model_dump()}, "image_jobs": {}}
    )
    monkeypatch.setattr(tasks.provider, "image", lambda *args: (blob, "image/png"))
    tasks.create_images(
        {
            "rid": rid,
            "scene": scene,
            "draft": {
                "texts": {},
                "image_prompts": {
                    n["id"]: "photo" for n in scene["blocks"][0]["nodes"] if n["kind"] == "image"
                },
                "summary": "",
                "storyline": "",
            },
        }
    )
    jobs = store.get(rid)["data"]["image_jobs"]
    assert jobs["rewards.image-0"]["status"] == "succeeded"
    assert jobs["rewards.image-1"]["status"] == ("succeeded" if count == 2 else "input_required")
    assert jobs["rewards.image-2"]["status"] == "input_required"
    with store.connection() as c:
        c.execute(
            "UPDATE ai_records SET data=jsonb_set(data,'{status}','\"test_complete\"') WHERE id=%s", (rid,)
        )


def test_checkpoint_resumes_after_assembly_exception_without_model_calls(monkeypatch):
    project = str(uuid4())
    info = ProjectInput.model_validate_json(Path("tests/fixtures/appliance.json").read_text())
    blob = Path("tests/fixtures/original.png").read_bytes()
    info.asset_ids = [assets.put(project, blob)]
    scene, fixed = plan(info, review())
    draft = CopyResult(
        texts={
            n["id"]: n["text"]
            for b in scene["blocks"]
            for n in b["nodes"]
            if n["kind"] == "text" and n["id"] not in fixed
        },
        image_prompts={n["id"]: "image" for b in scene["blocks"] for n in b["nodes"] if n["kind"] == "image"},
        summary="",
        storyline="",
    )
    calls = []
    monkeypatch.setattr(tasks, "generate_checked", lambda *a, **k: draft)
    monkeypatch.setattr(tasks.provider, "image", lambda *a: (calls.append(1) or blob, "image/png"))
    original = tasks.assemble
    monkeypatch.setattr(
        tasks,
        "assemble",
        lambda state: (_ for _ in ()).throw(RuntimeError("simulated assembly interruption")),
    )
    rid = store.create(
        project,
        "run",
        {
            "status": "running",
            "snapshot": {"input": info.model_dump(), "review": review().model_dump()},
            "source_revision": 1,
            "image_jobs": {},
        },
    )
    with pytest.raises(RuntimeError):
        tasks.generate(store.get(rid))
    count = len(calls)
    monkeypatch.setattr(tasks, "assemble", original)
    tasks.generate(store.get(rid))
    assert store.get(rid)["data"]["status"] == "succeeded"
    assert len(calls) == count
