import base64
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from funding_story import provider
from funding_story.config import Settings


def test_default_local_profile_uses_google_experiment(monkeypatch):
    monkeypatch.delenv("MODEL_PROFILE", raising=False)
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
    monkeypatch.delenv("IMAGE_MODEL", raising=False)
    monkeypatch.delenv("IMAGE_QUALITY", raising=False)
    monkeypatch.delenv("OPENAI_AUTH_MODE", raising=False)
    cfg = Settings(_env_file=None, ai_service_token="test-only")
    assert cfg.model_profile == "local_google_experiment"
    assert cfg.text_model == "gemini-3.8-flash"
    assert cfg.image_provider == "google"
    assert cfg.image_model == "gemini-3.1-flash-image"


def test_runtime_profile_selects_production_models(monkeypatch):
    for name in ("IMAGE_PROVIDER", "IMAGE_MODEL", "IMAGE_QUALITY", "OPENAI_AUTH_MODE"):
        monkeypatch.delenv(name, raising=False)
    cfg = Settings(
        _env_file=None,
        app_env="test",
        model_profile="runtime",
        ai_service_token="test-only",
    )
    assert cfg.text_model == "gemini-3.8-flash"
    assert cfg.image_provider == "openai"
    assert cfg.image_model == "gpt-image-2.5-flare"
    assert cfg.image_quality == "medium"
    assert cfg.openai_auth_mode == "eks_wif"


def test_local_openai_smoke_uses_api_key_auth(monkeypatch):
    for name in ("IMAGE_PROVIDER", "IMAGE_MODEL", "IMAGE_QUALITY", "OPENAI_AUTH_MODE"):
        monkeypatch.delenv(name, raising=False)
    cfg = Settings(
        _env_file=None,
        app_env="local",
        model_profile="local_openai_smoke",
        ai_service_token="test-only",
    )
    assert cfg.image_provider == "openai"
    assert cfg.openai_auth_mode == "api_key"


def test_image_model_environment_override_is_preserved(monkeypatch):
    monkeypatch.setenv("IMAGE_PROVIDER", "google")
    monkeypatch.setenv("IMAGE_MODEL", "gemini-3.1-flash-image")
    cfg = Settings(_env_file=None, ai_service_token="test-only")
    assert cfg.image_model == "gemini-3.1-flash-image"


@pytest.mark.parametrize("response_kind", ["valid", "no_content", "no_parts", "empty", "corrupt", "mime"])
def test_image_worker_provider_makes_one_call_and_checks_payload(monkeypatch, response_kind):
    calls = []
    png = (Path(__file__).parent / "fixtures/original.png").read_bytes()
    data = SimpleNamespace(data=png, mime_type="image/png")
    if response_kind == "empty":
        data.data = b""
    elif response_kind == "corrupt":
        data.data = b"not an image"
    elif response_kind == "mime":
        data.mime_type = "image/jpeg"
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(inline_data=data)]), finish_reason="STOP"
    )
    if response_kind == "no_content":
        candidate.content = None
    elif response_kind == "no_parts":
        candidate.content.parts = None

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(candidates=[candidate], prompt_feedback=None)

    @contextmanager
    def client():
        yield SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    monkeypatch.setattr(provider, "client", client)
    monkeypatch.setattr(
        provider,
        "settings",
        lambda: SimpleNamespace(
            image_provider="google", image_model="gemini-3.1-flash-lite-image"
        ),
    )
    if response_kind == "valid":
        assert provider.image_once("product", []) == (png, "image/png")
    else:
        with pytest.raises(provider.MissingImageError):
            provider.image_once("product", [])
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-3.1-flash-lite-image"


@pytest.mark.parametrize("with_references", [False, True])
def test_openai_provider_selects_generate_or_edit_and_validates_png(monkeypatch, with_references):
    png = (Path(__file__).parent / "fixtures/original.png").read_bytes()
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(png).decode())],
        usage=None,
        _request_id="req_test",
    )
    calls = []

    class Images:
        def generate(self, **kwargs):
            calls.append(("generate", kwargs))
            return response

        def edit(self, **kwargs):
            calls.append(("edit", kwargs))
            return response

    class Client:
        images = Images()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    cfg = SimpleNamespace(
        image_provider="openai",
        image_model="gpt-image-2.5-flare",
        image_quality="medium",
    )
    monkeypatch.setattr(provider, "settings", lambda: cfg)
    monkeypatch.setattr(provider, "openai_client", Client)
    references = [(png, "image/png")] if with_references else []

    assert provider.image_once("product", references, size="1536x1024") == (png, "image/png")
    method, kwargs = calls[0]
    assert method == ("edit" if with_references else "generate")
    assert kwargs["model"] == "gpt-image-2.5-flare"
    assert kwargs["quality"] == "medium"
    assert kwargs["size"] == "1536x1024"
    assert kwargs["output_format"] == "png"
    if with_references:
        assert "input_fidelity" not in kwargs
        assert len(kwargs["image"]) == 1


def test_openai_provider_rejects_more_than_sixteen_references(monkeypatch):
    monkeypatch.setattr(
        provider,
        "settings",
        lambda: SimpleNamespace(
            image_provider="openai",
            image_model="gpt-image-2.5-flare",
            image_quality="medium",
        ),
    )
    with pytest.raises(ValueError, match="16"):
        provider.image_once("product", [(b"image", "image/png")] * 17)


def test_production_openai_provider_requires_wif_configuration(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="OPENAI_IDENTITY_PROVIDER_ID"):
        Settings(
            _env_file=None,
            app_env="prod",
            model_profile="runtime",
            ai_service_token="test-only",
            db_host="db.internal",
            db_password="non-default-test-value",
        )


def test_production_openai_provider_rejects_api_key_mode(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="MODEL_PROFILE=runtime"):
        Settings(
            _env_file=None,
            app_env="prod",
            model_profile="local_openai_smoke",
            ai_service_token="test-only",
            db_host="db.internal",
            db_password="non-default-test-value",
            openai_api_key="sk-test",
        )


def test_model_profile_rejects_mixed_provider(monkeypatch):
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="MODEL_PROFILE=runtime"):
        Settings(
            _env_file=None,
            app_env="test",
            model_profile="runtime",
            image_provider="google",
            ai_service_token="test-only",
        )


def test_automated_test_environment_rejects_external_model_client(monkeypatch):
    monkeypatch.setattr(provider, "settings", lambda: SimpleNamespace(app_env="test"))
    with pytest.raises(RuntimeError, match="외부 모델"):
        provider.openai_client()
    with pytest.raises(RuntimeError, match="외부 모델"):
        provider.client()


def test_eks_projected_identity_token_provider_rereads_rotating_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("eks-token-1\n")
    subject = provider.eks_projected_identity_token_provider(str(token_file))
    assert subject["token_type"] == "jwt"
    assert subject["get_token"]() == "eks-token-1"
    token_file.write_text("eks-token-2\n")
    assert subject["get_token"]() == "eks-token-2"


def test_eks_projected_identity_token_provider_rejects_empty_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("  ")
    subject = provider.eks_projected_identity_token_provider(str(token_file))

    with pytest.raises(RuntimeError, match="identity token"):
        subject["get_token"]()


def test_eks_projected_identity_token_provider_wraps_missing_file(tmp_path):
    subject = provider.eks_projected_identity_token_provider(str(tmp_path / "missing"))

    with pytest.raises(RuntimeError, match="읽을 수 없습니다"):
        subject["get_token"]()


def test_openai_client_uses_eks_wif(monkeypatch):
    captured = {}
    cfg = SimpleNamespace(
        openai_auth_mode="eks_wif",
        openai_identity_provider_id="wip_test",
        openai_service_account_id="svc_test",
        openai_wif_audience="https://api.openai.com/v1",
        openai_wif_token_file="/var/run/secrets/openai-wif/token",
        openai_api_key=None,
        openai_project="",
        openai_organization="",
    )
    expected_provider = {"token_type": "jwt", "get_token": lambda: "token"}
    monkeypatch.setattr(provider, "settings", lambda: cfg)
    monkeypatch.setattr(
        provider,
        "eks_projected_identity_token_provider",
        lambda token_file: expected_provider,
    )
    monkeypatch.setattr(provider, "OpenAI", lambda **kwargs: captured.update(kwargs) or "client")

    assert provider.openai_client() == "client"
    assert captured == {
        "timeout": 180.0,
        "max_retries": 0,
        "workload_identity": {
            "identity_provider_id": "wip_test",
            "service_account_id": "svc_test",
            "provider": expected_provider,
        },
    }


def test_openai_client_keeps_local_api_key_compatibility(monkeypatch):
    captured = {}
    cfg = SimpleNamespace(
        openai_auth_mode="api_key",
        openai_identity_provider_id="",
        openai_service_account_id="",
        openai_wif_audience="",
        openai_wif_token_file="",
        openai_api_key=SimpleNamespace(get_secret_value=lambda: "sk-test"),
        openai_project="project-test",
        openai_organization="org-test",
    )
    monkeypatch.setattr(provider, "settings", lambda: cfg)
    monkeypatch.setattr(provider, "OpenAI", lambda **kwargs: captured.update(kwargs) or "client")

    assert provider.openai_client() == "client"
    assert captured == {
        "timeout": 180.0,
        "max_retries": 0,
        "api_key": "sk-test",
        "project": "project-test",
        "organization": "org-test",
    }
