from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from funding_story import media
from funding_story.models import SourceImageRef


def _settings():
    return SimpleNamespace(internal_http_timeout_seconds=1)


def _image():
    output = BytesIO()
    Image.new("RGB", (1, 1), "red").save(output, format="PNG")
    return output.getvalue()


def test_public_image_reader_checks_mime_and_actual_bytes(monkeypatch):
    content = _image()
    monkeypatch.setattr(media, "settings", _settings)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=content)
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        assert media.read_public_image("https://files.example.com/body.png") == (content, "image/png")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/gif"}, content=content)
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(ValueError, match="MIME"):
            media.read_public_image("https://files.example.com/body.png")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, headers={"Content-Type": "application/xml"})
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(httpx.HTTPStatusError):
            media.read_public_image("https://files.example.com/private.png")


def test_signed_image_reader_keeps_metadata_validation(monkeypatch):
    content = _image()
    reference = SourceImageRef(
        slot_id="story_content.1",
        read_url="https://files.example.com/body.png?signature=example",
        content_type="image/png",
        file_size=len(content),
        expires_at="2099-01-01T00:00:00Z",
    )
    monkeypatch.setattr(media, "settings", _settings)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=content)
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        assert media.read_source_image(reference) == (content, "image/png")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"invalid")
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(ValueError, match="크기가 계약과 다릅니다"):
            media.read_source_image(reference)


def _signed_reference(expires_at="2099-01-01T00:00:00Z"):
    return SourceImageRef(
        slot_id="story_content.1",
        read_url="https://files.example.com/body.png?signature=secret",
        content_type="image/png",
        file_size=10,
        expires_at=expires_at,
    )


def test_expired_image_reference_fails_before_network_call(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("이미 만료된 URL로 HTTP 요청을 보내면 안 됩니다.")

    monkeypatch.setattr(media.httpx, "stream", unexpected_call)
    with pytest.raises(media.ImageReadUrlExpired) as error:
        media.read_source_image(_signed_reference("2000-01-01T00:00:00Z"))
    assert error.value.code == "IMAGE_READ_URL_EXPIRED"
    assert error.value.slot_id == "story_content.1"
    assert "signature" not in str(error.value)


@pytest.mark.parametrize("status,code,message", [
    (400, "ExpiredToken", "The provided token has expired."),
    (403, "RequestExpired", "Request has expired"),
    (403, "AccessDenied", "Request has expired"),
])
def test_signed_url_reports_expiry_from_storage_before_declared_expiry(monkeypatch, status, code, message):
    monkeypatch.setattr(media, "settings", _settings)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        status, content=f"<Error><Code>{code}</Code><Message>{message}</Message></Error>".encode()
    ))) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(media.ImageReadUrlExpired) as error:
            media.read_source_image(_signed_reference())
    assert error.value.slot_id == "story_content.1"


@pytest.mark.parametrize("body", [
    b"<Error><Code>AccessDenied</Code><Message>Access Denied</Message></Error>",
    b"<Error><Code>SignatureDoesNotMatch</Code></Error>",
    b"Request has expired",  # Unstructured messages are not proof of expiry.
    b"<Error>" + b"x" * 17000 + b"</Error>",
])
def test_other_storage_errors_do_not_allow_url_refresh(monkeypatch, body):
    monkeypatch.setattr(media, "settings", _settings)
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(403, content=body)
    )) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(httpx.HTTPStatusError):
            media.read_source_image(_signed_reference())
