from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import ManagedVm
from .parsing import format_bps, format_bytes


def state_label(vm: ManagedVm) -> str:
    if vm.throttle_active:
        return "throttled"
    if vm.over_limit:
        return "over-limit"
    return "normal"


def remaining_bytes(vm: ManagedVm) -> int:
    return max(vm.limit_bytes - vm.total_bytes, 0)


def usage_percent(vm: ManagedVm) -> float:
    return vm.usage_ratio * 100


def format_percent(value: float) -> str:
    if 0 < value < 0.01:
        return "<0.01%"
    return f"{value:.2f}%"


def progress_bar(ratio: float, width: int = 20) -> str:
    clamped = max(0.0, min(ratio, 1.0))
    filled = int(round(clamped * width))
    filled = max(0, min(filled, width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def usage_progress(vm: ManagedVm, width: int = 20) -> str:
    return f"{progress_bar(vm.usage_ratio, width=width)} {format_percent(usage_percent(vm))}"


def usage_summary(vm: ManagedVm) -> str:
    return f"{format_bytes(vm.total_bytes)} / {format_bytes(vm.limit_bytes)}"


def used_summary(vm: ManagedVm) -> str:
    return format_bytes(vm.total_bytes)


def limit_summary(vm: ManagedVm) -> str:
    return format_bytes(vm.limit_bytes)


def remaining_summary(vm: ManagedVm) -> str:
    return format_bytes(remaining_bytes(vm))


def local_datetime_text(value: datetime, zone: ZoneInfo) -> str:
    return value.astimezone(zone).isoformat(sep=" ", timespec="seconds")


def local_event_text(value: str, zone: ZoneInfo) -> str:
    return local_datetime_text(datetime.fromisoformat(value), zone)


def build_usage_snapshot(vm: ManagedVm, zone: ZoneInfo) -> dict[str, object]:
    return {
        "vmid": vm.vmid,
        "name": vm.name,
        "uuid": vm.bios_uuid,
        "usage_bytes": vm.total_bytes,
        "usage_used_text": used_summary(vm),
        "limit_bytes": vm.limit_bytes,
        "limit_text": limit_summary(vm),
        "remaining_bytes": remaining_bytes(vm),
        "usage_ratio": vm.usage_ratio,
        "usage_percent": usage_percent(vm),
        "usage_percent_text": format_percent(usage_percent(vm)),
        "progress_bar": progress_bar(vm.usage_ratio, width=20),
        "progress_text": usage_progress(vm, width=20),
        "usage_text": usage_summary(vm),
        "remaining_text": remaining_summary(vm),
        "throttle_bps": vm.throttle_bps,
        "throttle_text": format_bps(vm.throttle_bps),
        "throttle_active": vm.throttle_active,
        "state": state_label(vm),
        "anchor_day": vm.anchor_day,
        "period_start_at": local_datetime_text(vm.period_start, zone),
        "next_reset_at": local_datetime_text(vm.next_reset_at, zone),
        "last_seen_at": local_datetime_text(vm.last_seen_at, zone),
        "last_sync_at": local_datetime_text(vm.last_sync_at, zone) if vm.last_sync_at else None,
    }


def render_usage_text(snapshot: dict[str, object]) -> str:
    lines = [
        f"虚拟机: {snapshot['vmid']} ({snapshot['name']})",
        f"流量:   {snapshot['progress_text']}",
        f"已用:   {snapshot['usage_text']}",
        f"剩余:   {snapshot['remaining_text']}",
        f"状态:   {snapshot['state']}",
        f"超量限速: {snapshot['throttle_text']}",
        f"重置日: 每月 {snapshot['anchor_day']} 号",
        f"下次重置: {snapshot['next_reset_at']}",
    ]
    if snapshot["throttle_active"]:
        lines.append("提示: 当前已处于超额限速状态")
    return "\n".join(lines)


def render_usage_brief(snapshot: dict[str, object]) -> str:
    return (
        f"{snapshot['usage_bytes']}\t"
        f"{snapshot['limit_bytes']}\t"
        f"{snapshot['usage_percent']:.6f}\t"
        f"{snapshot['state']}"
    )
