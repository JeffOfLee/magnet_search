# Transfer Cache Storage Design

## Goal

Add `--transfer-cache-storage` to `magnet-search download` so batch download plus S3 upload can apply backpressure when the local transfer cache grows beyond a user-provided size.

## User Interface

Example:

```bash
magnet-search download input.csv --output downloads/ \
  --upload s3-upload.toml \
  --download-concurrency 4 \
  --upload-concurrency 2 \
  --transfer-cache-storage 10GB
```

The option accepts byte sizes such as `1024`, `500MB`, `2GB`, and `1.5GiB`. It is only valid when `--upload` is provided, because cache space is released by successful upload cleanup.

## Behavior

The cache limit applies to files produced by the current command run that have not yet been uploaded and removed locally. Existing files already present in `--output` do not count against the limit.

When tracked cache usage is over the configured limit, new download workers wait before starting another source. Downloads already in progress are allowed to finish, so usage can temporarily exceed the limit. Each completed download is queued for upload. After upload succeeds, the local files for that result are deleted, empty child directories are pruned, tracked cache usage is reduced, and waiting download workers are notified.

If upload fails, files are kept locally and the command reports the upload error. The command does not delete files that failed to upload.

## Architecture

`src/magnet_search/download.py` will own reusable cache primitives:

- size parsing for the CLI option.
- a thread-safe `TransferCacheStorage` that tracks current-run cached files.
- local cleanup helpers after successful upload.
- an optional `before_download` callback in `run_download_batch()` so concurrent workers can wait before starting downloads.

`src/magnet_search/cli.py` will parse the option, reject it without `--upload`, wire the cache gate into batch downloads, and clean files after successful uploads when the cache limit is active.

## Testing

Focused tests will cover:

- size parsing and invalid values.
- batch download backpressure waiting until cached files are released.
- CLI forwarding of the cache limit into batch execution.
- cleanup after successful upload when cache limiting is enabled.
- validation that `--transfer-cache-storage` requires `--upload`.
