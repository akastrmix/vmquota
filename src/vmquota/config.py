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
    data = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    general = data.get("general", {})
    api = data.get("api", {})
    scope = data.get("scope", {})
    defaults = data.get("defaults", {})

    timezone_name = general.get("timezone", "UTC")
    vmid_ranges = parse_vmid_ranges(scope.get("vmid_ranges", ["101-110"]))
    return AppConfig(
        path=config_path,
        timezone_name=timezone_name,
        timezone=ZoneInfo(timezone_name),
        state_db=Path(general.get("state_db", "/var/lib/vmquota/state.sqlite")),
        api_bind_host=str(api.get("bind_host", "10.200.0.1")),
        api_bind_port=int(api.get("bind_port", 9527)),
        enforce_shaping=bool(general.get("enforce_shaping", False)),
        auto_enroll=bool(defaults.get("auto_enroll", True)),
        vmid_ranges=vmid_ranges,
        default_limit_bytes=int(defaults.get("limit_bytes", 2_000_000_000_000)),
        default_throttle_bps=parse_rate_bps(defaults.get("throttle_rate", "2mbit")),
    )
