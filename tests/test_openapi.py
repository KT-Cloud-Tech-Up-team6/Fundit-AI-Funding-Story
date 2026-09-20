import json
from pathlib import Path

from funding_story.api import app


def test_checked_in_openapi_matches_application():
    assert json.loads(Path("docs/openapi.json").read_text()) == app.openapi()


def test_content_insight_openapi_contains_request_and_response_examples():
    operation = app.openapi()["paths"]["/api/v1/ai/content-insight-runs"]["post"]
    request = operation["requestBody"]["content"]["application/json"]
    response = operation["responses"]["202"]["content"]["application/json"]

    assert request["examples"]["registration"]["value"]["trigger"] == "PROJECT_REGISTRATION_COMPLETED"
    assert response["example"]["artifacts"]["PAGE_SUMMARY"]["required"] is True
