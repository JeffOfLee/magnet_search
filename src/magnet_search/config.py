from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class HttpProviderConfig:
    name: str
    enabled: bool
    search_url: str
    result_path: str
    title_path: str
    magnet_path: str
    url_path: str = ""
    size_path: str = ""
    published_at_path: str = ""


@dataclass(frozen=True)
class AppConfig:
    http_providers: list[HttpProviderConfig]


class ConfigError(ValueError):
    """Raised when user configuration has an invalid shape or value."""


def default_config_path() -> Path:
    return Path.home() / ".config" / "magnet-search" / "config.toml"


def _load_raw(path: Path | None, data: dict[str, Any] | None) -> dict[str, Any]:
    if data is not None:
        return data
    if path is None:
        path = default_config_path()
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _provider_label(index: int, entry: dict[str, Any]) -> str:
    name = entry.get("name")
    if isinstance(name, str) and name:
        return name
    return f"provider {index}"


def _required_string(entry: dict[str, Any], field: str, label: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} required field {field} must be a non-empty string")
    return value


def _optional_string(entry: dict[str, Any], field: str, label: str) -> str:
    value = entry.get(field, "")
    if not isinstance(value, str):
        raise ConfigError(f"{label} field {field} must be a string")
    return value


def load_config(path: Path | None = None, data: dict[str, Any] | None = None) -> AppConfig:
    raw = _load_raw(path, data)
    providers_table = raw.get("providers", {})
    if not isinstance(providers_table, dict):
        raise ConfigError("providers must be a table")

    http_entries = providers_table.get("http", [])
    if not isinstance(http_entries, list):
        raise ConfigError("providers.http must be a list")

    providers: list[HttpProviderConfig] = []
    for index, entry in enumerate(http_entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"provider {index} must be a table")

        label = _provider_label(index, entry)
        name = _required_string(entry, "name", label)
        label = name
        search_url = _required_string(entry, "search_url", label)
        if "{query}" not in search_url:
            raise ConfigError(f"{label} search_url must contain {{query}}")

        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{label} enabled must be a bool")
        if not enabled:
            continue

        providers.append(
            HttpProviderConfig(
                name=name,
                enabled=enabled,
                search_url=search_url,
                result_path=_required_string(entry, "result_path", label),
                title_path=_required_string(entry, "title_path", label),
                magnet_path=_required_string(entry, "magnet_path", label),
                url_path=_optional_string(entry, "url_path", label),
                size_path=_optional_string(entry, "size_path", label),
                published_at_path=_optional_string(entry, "published_at_path", label),
            )
        )

    return AppConfig(http_providers=providers)
