from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tomllib
from zoneinfo import ZoneInfo

from .parsing import VmidRange, parse_rate_bps, parse_vmid_ranges


DEFAULT_CONFIG_PATH = Path("/etc/vmquota/config.toml")


@dataclass(frozen=True, slots=True)
class AppConfig:
    path: Path
    timezone_name: str
    timezone: ZoneInfo
    state_db: Path
    api_bind_host: str
    api_bind_port: int
    enforce_shaping: bool
    auto_enroll: bool
    vmid_ranges: tuple[VmidRange, ...]
    default_limit_bytes: int
    default_throttle_bps: int


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    config_path = Path(path or os.environ.get("VMQUOTA_CONFIG", DEFAULT_CONFIG_PATH))
    data: dict[str, object] = {}
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    general = _get_required_table(data, "general")
    api = _get_required_table(data, "api")
    scope = _get_required_table(data, "scope")
    defaults = _get_required_table(data, "defaults")

    timezone_name = _get_str(general, "timezone", default="UTC", section="general")
    vmid_ranges = parse_vmid_ranges(_get_str_list(scope, "vmid_ranges", default=["101-110"], section="scope"))
    return AppConfig(
        path=config_path,
        timezone_name=timezone_name,
        timezone=ZoneInfo(timezone_name),
        state_db=Path(_get_str(general, "state_db", default="/var/lib/vmquota/state.sqlite", section="general")),
        api_bind_host=_get_str(api, "bind_host", default="10.200.0.1", section="api"),
        api_bind_port=_get_int(api, "bind_port", default=9527, section="api", min_value=1, max_value=65535),
        enforce_shaping=_get_bool(general, "enforce_shaping", default=False, section="general"),
        auto_enroll=_get_bool(defaults, "auto_enroll", default=True, section="defaults"),
        vmid_ranges=vmid_ranges,
        default_limit_bytes=_get_int(defaults, "limit_bytes", default=2_000_000_000_000, section="defaults", min_value=1),
        default_throttle_bps=parse_rate_bps(_get_str(defaults, "throttle_rate", default="2mbit", section="defaults")),
    )


def _get_required_table(data: dict[str, object], key: str) -> dict[str, object]:
    if key not in data:
        raise ValueError(f"missing required TOML table: {key}")
    value = data[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a TOML table")
    return value


def _get_bool(section_data: dict[str, object], key: str, *, default: bool, section: str) -> bool:
    if key not in section_data:
        return default
    value = section_data[key]
    if not isinstance(value, bool):
        raise ValueError(f"{section}.{key} must be a TOML boolean")
    return value


def _get_int(
    section_data: dict[str, object],
    key: str,
    *,
    default: int,
    section: str,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    if key not in section_data:
        value = default
    else:
        value = section_data[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{section}.{key} must be a TOML integer")
    if min_value is not None and value < min_value:
        raise ValueError(f"{section}.{key} must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{section}.{key} must be <= {max_value}")
    return value


def _get_str(section_data: dict[str, object], key: str, *, default: str, section: str) -> str:
    if key not in section_data:
        return default
    value = section_data[key]
    if not isinstance(value, str):
        raise ValueError(f"{section}.{key} must be a TOML string")
    if not value:
        raise ValueError(f"{section}.{key} must not be empty")
    return value


def _get_str_list(section_data: dict[str, object], key: str, *, default: list[str], section: str) -> list[str]:
    if key not in section_data:
        return default
    value = section_data[key]
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{section}.{key} must be a non-empty TOML string array")
    return value
