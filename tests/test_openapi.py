import json
from pathlib import Path

from funding_story.api import app


def test_checked_in_openapi_matches_application():
    assert json.loads(Path("docs/openapi.json").read_text()) == app.openapi()


def test_content_insight_openapi_contains_request_and_response_examples():
    paths = app.openapi()["paths"]
    assert "/api/v1/ai/content-insight-runs" not in paths
    assert "/api/v1/ai/storyline-runs" in paths
    operation = paths["/api/v1/ai/page-summary-runs"]["post"]
    request = operation["requestBody"]["content"]["application/json"]
    response = operation["responses"]["202"]["content"]["application/json"]

    assert request["examples"]["registration"]["value"]["trigger"] == "PROJECT_REGISTRATION_COMPLETED"
    assert "requested_artifacts" not in request["examples"]["registration"]["value"]
    assert response["example"]["artifacts"]["PAGE_SUMMARY"]["required"] is True
    assert response["example"]["artifacts"]["STORYLINE"]["status"] == "NOT_REQUESTED"
