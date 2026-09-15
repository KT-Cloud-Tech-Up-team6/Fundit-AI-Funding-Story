"""Read-only local checks. Uses an existing owned project; never creates production data."""

import os

import httpx

base = os.environ.get("FUNDING_STORY_BE_URL", "http://127.0.0.1:58002")
project = os.environ["TEST_PROJECT_ID"]
headers = {
    "X-User-Id": os.environ["TEST_USER_ID"],
    "X-User-Roles": "SELLER",
    "X-Internal-Api-Key": os.environ["TEST_GATEWAY_KEY"],
}
with httpx.Client(base_url=base, headers=headers, timeout=30) as client:
    path = f"/api/v1/projects/{project}/funding-story"
    context = client.get(path + "/context")
    context.raise_for_status()
    document = client.get(path + "/document")
    document.raise_for_status()
    print(
        {
            "context": context.status_code,
            "document": document.status_code,
            "revision": document.json()["revision"],
        }
    )
