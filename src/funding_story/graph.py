import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from . import provider
from .bootstrap import new_checkpointer


class FormatState(TypedDict, total=False):
    prompt: str
    schema: dict
    raw: str
    result: dict
    errors: list[str]
    attempt: int
    failed: bool


def format_graph(parser, saver=None, generator=None):
    generate = generator or provider.structured

    def call(state):
        feedback = "\n출력 형식 오류를 수정하세요: " + state["errors"][-1] if state.get("errors") else ""
        raw = generate(state["prompt"] + feedback, state["schema"])
        return {"raw": raw, "attempt": state.get("attempt", 0) + 1}

    def validate(state):
        try:
            parsed = parser(json.loads(state["raw"]))
            return {"result": parsed.model_dump(), "failed": False}
        except (ValueError, TypeError) as exc:
            return {"errors": [*state.get("errors", []), str(exc)[:6000]], "failed": True}

    graph = StateGraph(FormatState)
    graph.add_node("generate", call)
    graph.add_node("validate_format", validate)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "validate_format")
    graph.add_conditional_edges(
        "validate_format", lambda s: END if not s["failed"] or s["attempt"] >= 3 else "generate"
    )
    return graph.compile(checkpointer=saver)


def generate_checked(rid, prompt, model, parser=None, references=()):
    saver = new_checkpointer()
    graph = format_graph(
        parser or model.model_validate, saver, lambda p, s: provider.structured(p, s, references)
    )
    config = {"configurable": {"thread_id": rid}, "metadata": {"run_id": rid}, "recursion_limit": 16}
    snapshot = graph.get_state(config)
    initial = (
        None
        if snapshot.values
        else {"prompt": prompt, "schema": model.model_json_schema(), "attempt": 0, "errors": []}
    )
    if snapshot.values and not snapshot.next:
        result = snapshot.values
    else:
        result = graph.invoke(initial, config)
    if result.get("failed"):
        raise ValueError("출력 형식 재시도 2회 초과: " + result["errors"][-1])
    return model.model_validate(result["result"])
