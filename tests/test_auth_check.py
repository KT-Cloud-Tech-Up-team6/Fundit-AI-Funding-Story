import base64
import json
import time

import pytest

from funding_story.auth_check import (
    AuthCheckError,
    check_google_external_account,
    check_openai_wif_token,
)


def jwt(payload):
    def segment(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'RS256'})}.{segment(payload)}.signature"


def test_openai_wif_token_check_accepts_eks_claims(tmp_path):
    token_file = tmp_path / "openai-token"
    token_file.write_text(
        jwt(
            {
                "iss": "https://oidc.eks.ap-northeast-2.amazonaws.com/id/cluster",
                "aud": ["https://api.openai.com/v1"],
                "sub": "system:serviceaccount:dev:funding-story-ai",
                "exp": int(time.time()) + 3600,
            }
        )
    )

    result = check_openai_wif_token(
        token_file,
        "https://api.openai.com/v1",
        "system:serviceaccount:dev:funding-story-ai",
    )

    assert result["status"] == "ok"
    assert result["subject"] == "system:serviceaccount:dev:funding-story-ai"


def test_openai_wif_token_check_rejects_wrong_audience(tmp_path):
    token_file = tmp_path / "openai-token"
    token_file.write_text(
        jwt(
            {
                "iss": "https://issuer.example",
                "aud": "wrong",
                "sub": "subject",
                "exp": int(time.time()) + 3600,
            }
        )
    )

    with pytest.raises(AuthCheckError, match="audience"):
        check_openai_wif_token(token_file, "https://api.openai.com/v1")


def test_google_external_account_check_requires_keyless_file_source(tmp_path):
    subject_token = tmp_path / "google-token"
    subject_token.write_text("projected-token")
    credentials_file = tmp_path / "google-credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "type": "external_account",
                "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/k",
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "token_url": "https://sts.googleapis.com/v1/token",
                "credential_source": {"file": str(subject_token)},
            }
        )
    )

    assert check_google_external_account(credentials_file)["status"] == "ok"

    credentials_file.write_text(
        json.dumps(
            {
                "type": "service_account",
                "private_key": "not-allowed",
                "credential_source": {"file": str(subject_token)},
            }
        )
    )
    with pytest.raises(AuthCheckError, match="external_account"):
        check_google_external_account(credentials_file)
