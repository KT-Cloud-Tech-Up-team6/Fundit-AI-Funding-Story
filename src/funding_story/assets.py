from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .bootstrap import application
from .config import settings

records = application


def put(project: str, content: bytes, mime: str = "image/png"):
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("이미지는 20MB 이하로 업로드해 주세요.")
    with Image.open(BytesIO(content)) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError("이미지 해상도가 너무 큽니다.")
        if image.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("PNG, JPEG, WebP만 지원합니다.")
        mime = Image.MIME[image.format]
    asset_id = str(uuid4())
    key = f"{project}/{asset_id}"
    cfg = settings()
    if cfg.storage_backend == "s3":
        import boto3

        boto3.client("s3", endpoint_url=cfg.s3_endpoint).put_object(
            Bucket=cfg.s3_bucket, Key=key, Body=content, ContentType=mime
        )
    else:
        path = Path(cfg.storage_dir) / asset_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    records.create(project, "asset", {"key": key, "mime": mime, "size": len(content)}, record_id=asset_id)
    return asset_id


def read(asset_id, project):
    row = records.get(asset_id, project)
    if row["kind"] != "asset":
        raise LookupError("이미지를 찾을 수 없습니다.")
    cfg = settings()
    if cfg.storage_backend == "s3":
        import boto3

        data = (
            boto3.client("s3", endpoint_url=cfg.s3_endpoint)
            .get_object(Bucket=cfg.s3_bucket, Key=row["data"]["key"])["Body"]
            .read()
        )
    else:
        data = (Path(cfg.storage_dir) / row["id"]).read_bytes()
    return data, row["data"]["mime"]


def delete(asset_id, project):
    row = records.get(asset_id, project, kind="asset")
    cfg = settings()
    if cfg.storage_backend == "s3":
        import boto3

        boto3.client("s3", endpoint_url=cfg.s3_endpoint).delete_object(
            Bucket=cfg.s3_bucket, Key=row["data"]["key"]
        )
    else:
        (Path(cfg.storage_dir) / row["id"]).unlink(missing_ok=True)
    records.delete_record(asset_id, project, kind="asset")


def import_s3(project, key):
    if not key.startswith(f"projects/{project}/") or ".." in key.split("/"):
        raise ValueError("프로젝트 이미지 경로가 아닙니다.")
    import boto3

    cfg = settings()
    if cfg.storage_backend != "s3":
        raise ValueError("S3 연결 설정이 필요합니다.")
    obj = boto3.client("s3", endpoint_url=cfg.s3_endpoint).get_object(Bucket=cfg.s3_bucket, Key=key)
    return put(project, obj["Body"].read(20 * 1024 * 1024 + 1))
