import json
from pathlib import Path

from funding_story.api import app


def test_checked_in_openapi_matches_application():
    assert json.loads(Path("docs/openapi.json").read_text()) == app.openapi()
