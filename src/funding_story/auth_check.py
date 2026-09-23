"""Offline checks for the two credential sources mounted into an EKS workload."""

import argparse
import base64
import binascii
import json
import os
import time
from pathlib import Path


class AuthCheckError(RuntimeError):
    pass


def _decode_base64url(value):
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError, TypeError) as exc:
        raise AuthCheckError("projected token payload가 Base64URL 형식이 아닙니다.") from exc


def jwt_claims(token):
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise AuthCheckError("projected token이 compact JWT 형식이 아닙니다.")
    try:
        claims = json.loads(_decode_base64url(parts[1]))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AuthCheckError("projected token payload가 JSON object가 아닙니다.") from exc
    if not isinstance(claims, dict):
        raise AuthCheckError("projected token payload가 JSON object가 아닙니다.")
    return claims


def check_openai_wif_token(token_file, expected_audience, expected_subject="", now=None):
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AuthCheckError("OpenAI WIF projected token을 읽을 수 없습니다.") from exc
    claims = jwt_claims(token)
    audiences = claims.get("aud", [])
    audiences = [audiences] if isinstance(audiences, str) else audiences
    if expected_audience not in audiences:
        raise AuthCheckError("OpenAI WIF token audience가 설정과 일치하지 않습니다.")
    subject = claims.get("sub", "")
    if not subject or (expected_subject and subject != expected_subject):
        raise AuthCheckError("OpenAI WIF token subject가 설정과 일치하지 않습니다.")
    issuer = claims.get("iss", "")
    if not isinstance(issuer, str) or not issuer.startswith("https://"):
        raise AuthCheckError("OpenAI WIF token issuer가 올바르지 않습니다.")
    expires_at = claims.get("exp")
    current_time = time.time() if now is None else now
    if not isinstance(expires_at, (int, float)) or expires_at <= current_time + 60:
        raise AuthCheckError("OpenAI WIF token이 만료되었거나 곧 만료됩니다.")
    return {
        "status": "ok",
        "issuer": issuer,
        "subject": subject,
        "audience": expected_audience,
        "expires_at": int(expires_at),
    }


def check_google_external_account(credentials_file):
    try:
        payload = json.loads(Path(credentials_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthCheckError("Google external-account ADC 설정을 읽을 수 없습니다.") from exc
    if payload.get("type") != "external_account":
        raise AuthCheckError("Google ADC 설정은 external_account 형식이어야 합니다.")
    if payload.get("private_key") or payload.get("private_key_id"):
        raise AuthCheckError("Google 서비스 계정 private key는 사용할 수 없습니다.")
    credential_source = payload.get("credential_source") or {}
    token_file = credential_source.get("file")
    if not token_file or not Path(token_file).is_file():
        raise AuthCheckError("Google external-account subject token file을 찾을 수 없습니다.")
    if not payload.get("audience") or not payload.get("token_url"):
        raise AuthCheckError("Google external-account audience 또는 token_url이 없습니다.")
    return {
        "status": "ok",
        "type": "external_account",
        "subject_token_file": token_file,
        "service_account_impersonation": bool(payload.get("service_account_impersonation_url")),
    }


def main():
    parser = argparse.ArgumentParser(description="Validate mounted EKS provider credentials offline")
    parser.add_argument(
        "--openai-token-file",
        default=os.environ.get("OPENAI_WIF_TOKEN_FILE", ""),
    )
    parser.add_argument(
        "--openai-audience",
        default=os.environ.get("OPENAI_WIF_AUDIENCE", ""),
    )
    parser.add_argument(
        "--openai-subject",
        default=os.environ.get("OPENAI_WIF_EXPECTED_SUBJECT", ""),
    )
    parser.add_argument(
        "--google-credentials-file",
        default=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
    )
    args = parser.parse_args()
    if not args.openai_token_file or not args.openai_audience or not args.google_credentials_file:
        parser.error(
            "OPENAI_WIF_TOKEN_FILE, OPENAI_WIF_AUDIENCE, GOOGLE_APPLICATION_CREDENTIALS가 필요합니다."
        )
    result = {
        "openai": check_openai_wif_token(
            args.openai_token_file,
            args.openai_audience,
            args.openai_subject,
        ),
        "google": check_google_external_account(args.google_credentials_file),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
