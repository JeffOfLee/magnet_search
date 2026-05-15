# Download and S3 Upload Design

## Goal

Add one `magnet-search download` command that can download either a single magnet link or a CSV batch of magnet links, then optionally upload downloaded files to S3 using a user-provided TOML upload configuration file.

## User Interface

```bash
magnet-search download "magnet:?xt=..." --output downloads/
magnet-search download input.csv --output downloads/
magnet-search download input.csv --column magnet --output downloads/
magnet-search download input.csv --output downloads/ --upload s3-upload.toml
```

The first argument is interpreted as a CSV batch input when it points to an existing `.csv` file. Otherwise it is treated as a single magnet link. The CSV column defaults to `magnet` and can be overridden with `--column`.

`--upload` is optional. When omitted, the command only downloads files locally. When provided, its value is the path to a TOML file containing S3 upload settings.

## S3 Upload Configuration

```toml
bucket = "my-bucket"
prefix = "magnet-search/"
region = "ap-southeast-1"
endpoint_url = ""
access_key_id = ""
secret_access_key = ""
```

`bucket` is required. Empty optional values are ignored. If credentials are omitted, the uploader lets boto3 use its default credential chain.

## Architecture

- `src/magnet_search/download.py` owns aria2c execution, local output tracking, and CSV batch iteration.
- `src/magnet_search/storage.py` owns S3 upload config parsing and object uploads.
- `src/magnet_search/cli.py` adds the `download` command and converts domain errors into clean CLI exits.

The downloader uses `aria2c` through `subprocess.run`, with an injectable runner for tests. It snapshots the output directory before and after each aria2c run, then treats new or modified files as the downloaded payload for optional upload.

## Error Handling

- Missing CSV columns raise `DownloadError`.
- Failed aria2c executions raise `DownloadError` with stderr/stdout context.
- Missing or invalid S3 upload config raises `UploadConfigError`.
- Upload failures raise `UploadError`.
- The CLI prints clean errors and exits with status 1.

## Testing

Tests should cover:

- aria2c command construction.
- successful single downloads with discovered files.
- aria2c failure handling.
- CSV batch default `magnet` column behavior.
- CSV `--column` override behavior.
- missing CSV column errors.
- S3 config parsing and required bucket validation.
- S3 object key generation with prefixes.
- CLI single, batch, and upload routing.
