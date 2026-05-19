from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


class UploadConfigError(ValueError):
    """Raised when an upload configuration file is invalid."""


class UploadError(RuntimeError):
    """Raised when file upload fails."""


@dataclass(frozen=True)
class S3UploadConfig:
    bucket: str
    prefix: str = ""
    region: str = ""
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise UploadConfigError(f"{field} must be a non-empty string")
    return value


def _optional_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field, "")
    if not isinstance(value, str):
        raise UploadConfigError(f"{field} must be a string")
    return value


def load_s3_upload_config(path: Path) -> S3UploadConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise UploadConfigError("upload config must be a table")
    return S3UploadConfig(
        bucket=_required_string(raw, "bucket"),
        prefix=_optional_string(raw, "prefix"),
        region=_optional_string(raw, "region"),
        endpoint_url=_optional_string(raw, "endpoint_url"),
        access_key_id=_optional_string(raw, "access_key_id"),
        secret_access_key=_optional_string(raw, "secret_access_key"),
    )


class S3Uploader:
    def __init__(self, config: S3UploadConfig, client: Any | None = None, key_gen: str = "hash"):
        if key_gen not in ("hash", "path"):
            raise UploadConfigError("key_gen must be hash or path")
        self.config = config
        self.key_gen = key_gen
        self.client = client or self._build_client(config)

    def upload_files(self, files: list[Path], base_dir: Path) -> list[str]:
        uploaded: list[str] = []
        for file_path in files:
            key = self._object_key(file_path, base_dir)
            content_type, _ = mimetypes.guess_type(str(file_path))
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type
            try:
                self.client.upload_file(str(file_path), self.config.bucket, key, ExtraArgs=extra_args)
            except Exception as error:
                raise UploadError(f"failed to upload {file_path}: {error}") from error
            uploaded.append(f"s3://{self.config.bucket}/{key}")
        return uploaded

    @staticmethod
    def _build_client(config: S3UploadConfig) -> Any:
        try:
            import boto3
        except ImportError as error:
            raise UploadError("boto3 is required for S3 upload") from error

        kwargs: dict[str, str] = {}
        if config.region:
            kwargs["region_name"] = config.region
        if config.endpoint_url:
            kwargs["endpoint_url"] = config.endpoint_url
        if config.access_key_id:
            kwargs["aws_access_key_id"] = config.access_key_id
        if config.secret_access_key:
            kwargs["aws_secret_access_key"] = config.secret_access_key
        return boto3.client("s3", **kwargs)

    def _object_key(self, file_path: Path, base_dir: Path) -> str:
        try:
            relative = file_path.relative_to(base_dir).as_posix()
        except ValueError:
            relative = file_path.name

        key = relative
        if self.key_gen == "hash":
            key = f"{hashlib.sha256(relative.encode('utf-8')).hexdigest()}{file_path.suffix}"

        prefix = self.config.prefix.strip("/")
        if not prefix:
            return key
        return f"{prefix}/{key}"
