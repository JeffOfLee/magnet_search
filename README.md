# Magnet Search

`magnet-search` is a Python CLI for finding magnet links from legal/public sources and user-configured JSON HTTP providers.

## Usage

```bash
magnet-search search "resource name" --limit 3
magnet-search search "resource name" --limit 3 --json
magnet-search batch input.csv --column title --output results.csv --limit 3
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

## Output Columns

Batch output writes:

```text
query,title,magnet,source,size,published_at,score,url
```
