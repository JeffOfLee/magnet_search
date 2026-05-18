from pathlib import Path

import pytest

from magnet_search.storage import S3Uploader, UploadConfigError, load_s3_upload_config


class FakeS3Client:
    def __init__(self):
        self.uploads = []

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.uploads.append((filename, bucket, key))


def test_load_s3_upload_config_reads_required_and_optional_fields(tmp_path: Path):
    config_path = tmp_path / "s3-upload.toml"
    config_path.write_text(
        "\n".join(
            [
                'bucket = "my-bucket"',
                'prefix = "magnet-search/"',
                'region = "ap-southeast-1"',
                'endpoint_url = "https://s3.example.invalid"',
                'access_key_id = "key"',
                'secret_access_key = "secret"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_s3_upload_config(config_path)

    assert config.bucket == "my-bucket"
    assert config.prefix == "magnet-search/"
    assert config.region == "ap-southeast-1"
    assert config.endpoint_url == "https://s3.example.invalid"
    assert config.access_key_id == "key"
    assert config.secret_access_key == "secret"


def test_load_s3_upload_config_requires_bucket(tmp_path: Path):
    config_path = tmp_path / "s3-upload.toml"
    config_path.write_text('prefix = "magnet-search/"\n', encoding="utf-8")

    with pytest.raises(UploadConfigError, match="bucket must be a non-empty string"):
        load_s3_upload_config(config_path)


def test_s3_uploader_uploads_files_with_prefix_and_relative_paths(tmp_path: Path):
    file_path = tmp_path / "downloads" / "nested" / "movie.mp4"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("payload", encoding="utf-8")
    client = FakeS3Client()
    config_path = tmp_path / "s3-upload.toml"
    config_path.write_text('bucket = "my-bucket"\nprefix = "magnet-search/"\n', encoding="utf-8")
    config = load_s3_upload_config(config_path)
    uploader = S3Uploader(config=config, client=client)

    uploaded = uploader.upload_files([file_path], base_dir=tmp_path / "downloads")

    assert uploaded == ["s3://my-bucket/magnet-search/nested/movie.mp4"]
    assert client.uploads == [(str(file_path), "my-bucket", "magnet-search/nested/movie.mp4")]
