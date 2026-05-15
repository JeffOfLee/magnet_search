# Magnet Search

`magnet-search` is a Python CLI for finding magnet links from legal/public sources and user-configured JSON HTTP providers.

## Usage

```bash
magnet-search search "resource name" --limit 3
magnet-search search "resource name" --limit 3 --json
magnet-search batch input.csv --column title --output results.csv --limit 3
magnet-search download "magnet:?xt=..." --output downloads/
magnet-search download input.csv --output downloads/
magnet-search download input.csv --column magnet --output downloads/ --upload s3-upload.toml
```

The built-in provider targets public/legal metadata. The tool does not include built-in piracy or gray-market sources. Additional providers can be configured by the user in TOML.

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

## Downloading Magnet Content

The `download` command uses the local `aria2c` executable to download magnet content. Install aria2 before running downloads.

The command accepts either a single magnet link or a CSV path:

```bash
magnet-search download "magnet:?xt=..." --output downloads/
magnet-search download input.csv --output downloads/
magnet-search download input.csv --column link --output downloads/
```

If the first argument points to an existing `.csv` file, the command treats it as a batch input. The CSV column defaults to `magnet`; use `--column` to override it.

To upload downloaded files to S3 after the local download completes, pass an upload config file:

```bash
magnet-search download input.csv --output downloads/ --upload s3-upload.toml
```

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
