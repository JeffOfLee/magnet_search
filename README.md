# Magnet Search

`magnet-search` is a Python CLI for finding magnet links from legal/public sources and user-configured JSON HTTP providers.

## Usage

```bash
magnet-search search "resource name" --limit 3
magnet-search search "resource name" --limit 3 --json
magnet-search search "resource name" --limit 3 --verbose
magnet-search search input.csv --output results.csv --limit 3
magnet-search search input.csv --column title --output results.csv --limit 3
magnet-search download "magnet:?xt=..." --output downloads/
magnet-search download movie.torrent --output downloads/
magnet-search download input.csv --output downloads/
magnet-search download input.csv --column magnet --output downloads/ --upload s3-upload.toml
magnet-search download input.csv --output downloads/ --download-concurrency 4 --upload s3-upload.toml --upload-concurrency 8
magnet-search download input.csv --output downloads/ --upload s3-upload.toml --transfer-cache-storage 10GB
```

The built-in provider targets public/legal metadata. The tool does not include built-in piracy or gray-market sources. Additional providers can be configured by the user in TOML.

## Searching

The `search` command accepts either a single query or a CSV path:

```bash
magnet-search search "resource name" --limit 3
magnet-search search input.csv --output results.csv
magnet-search search input.csv --column title --output results.csv
magnet-search search input.csv --output results.csv --verbose
```

If the first argument points to an existing `.csv` file, the command treats it as a batch input. The CSV column defaults to `query`; use `--column` to override it. Batch search requires `--output`.

The legacy `batch` command is still available for existing scripts:

```bash
magnet-search batch input.csv --column title --output results.csv --limit 3
```

## Custom HTTP Providers

Create `~/.config/magnet-search/config.toml`:

```toml
[[providers.http]]
name = "example"
enabled = true
search_url = "https://example.invalid/search?q={query}"
result_path = "results"
title_path = "title"
magnet_path = "magnet"
url_path = "url"
size_path = "size"
published_at_path = "published_at"
```

The provider response must be JSON. Dot paths such as `data.results` are supported for nested objects.

## Downloading Torrent Content

The `download` command supports two engines:

| Engine | Default | Description |
|--------|---------|-------------|
| `aria2c` | Yes | Uses the local `aria2c` executable |
| `qbittorrent` | No | Uses a running qBittorrent instance via Web API |

### aria2c (default)

Install aria2 before running downloads. The `download` command accepts a single magnet link, a single `.torrent` file, or a CSV path:

```bash
magnet-search download "magnet:?xt=..." --output downloads/
magnet-search download movie.torrent --output downloads/
magnet-search download input.csv --output downloads/
magnet-search download input.csv --column link --output downloads/
magnet-search download input.csv --output downloads/ --verbose
```

### qBittorrent

Requires a running qBittorrent instance with Web UI enabled. See the full setup guide at [docs/qbittorrent-setup.md](docs/qbittorrent-setup.md).

```bash
magnet-search download movie.torrent --output downloads/ --engine qbittorrent
magnet-search download "magnet:?xt=..." --output downloads/ --engine qbittorrent \
  --qbittorrent-url http://localhost:8080 \
  --qbittorrent-username admin \
  --qbittorrent-password adminadmin
```

If the first argument points to an existing `.csv` file, the command treats it as a batch input. The CSV column defaults to `magnet`; use `--column` to override it. CSV values can be magnet links or `.torrent` file paths. Relative `.torrent` paths in CSV rows are resolved relative to the CSV file's directory.

To upload downloaded files to S3 after the local download completes, pass an upload config file:

```bash
magnet-search download input.csv --output downloads/ --upload s3-upload.toml
magnet-search download input.csv --output downloads/ --download-concurrency 4 --upload s3-upload.toml --upload-concurrency 8
magnet-search download input.csv --output downloads/ --upload s3-upload.toml --transfer-cache-storage 10GB
```

`--download-concurrency` controls how many CSV batch rows can download at the same time. `--upload-concurrency` controls how many S3 upload tasks can run at the same time when `--upload` is provided. Both default to `1`.

`--transfer-cache-storage` limits the current-run local transfer cache before starting new batch downloads. It accepts sizes such as `500MB`, `10GB`, and `1.5GiB`, and requires `--upload`. When the tracked downloaded files exceed the limit, new downloads wait until a completed upload deletes its local files and releases cache space. Downloads already in progress are allowed to finish, so cache usage can temporarily exceed the limit.

Add `--verbose` to print detailed process logs to stderr. Normal stdout remains unchanged, so JSON output and CSV file outputs stay parseable.

Example `s3-upload.toml`:

```toml
bucket = "my-bucket"
prefix = "magnet-search/"
region = "ap-southeast-1"
endpoint_url = ""
access_key_id = ""
secret_access_key = ""
```

Only `bucket` is required. Empty optional values are ignored. If credentials are omitted, boto3 uses its normal credential chain.

## Extending Providers

Use the configurable `JsonHttpProvider` path when an upstream source already returns stable JSON with ready-to-map magnet fields. Write a custom Python provider when you need multi-step fetching, authentication, non-JSON parsing, magnet generation, or provider-specific normalization.

The full contributor guide is in [docs/custom-provider-development.md](/Users/fujiao.li/source/magnet_search/docs/custom-provider-development.md).

## Output Columns

Batch output writes:

```text
query,title,magnet,source,size,published_at,score,url
```
