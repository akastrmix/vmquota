from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import ManagedVm, VmEvent
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


def build_event_snapshot(event: VmEvent, zone: ZoneInfo) -> dict[str, object]:
    return {
        "vmid": event.vmid,
        "uuid": event.bios_uuid,
        "ts": local_datetime_text(event.ts, zone),
        "kind": event.kind,
        "message": event.message,
        "details": event.details,
    }


def event_summary(event: VmEvent) -> str | None:
    details = event.details or {}
    if event.kind == "set":
        old = _dict_value(details.get("old"))
        new = _dict_value(details.get("new"))
        if old is None or new is None:
            return None
        changes: list[str] = []
        if old.get("limit_bytes") != new.get("limit_bytes"):
            changes.append(f"limit {_format_bytes_value(old.get('limit_bytes'))} -> {_format_bytes_value(new.get('limit_bytes'))}")
        if old.get("throttle_bps") != new.get("throttle_bps"):
            changes.append(f"throttle {_format_bps_value(old.get('throttle_bps'))} -> {_format_bps_value(new.get('throttle_bps'))}")
        if old.get("anchor_day") != new.get("anchor_day"):
            changes.append(f"reset day {old.get('anchor_day')} -> {new.get('anchor_day')}")
        total = details.get("cleared_total_bytes")
        if isinstance(total, int):
            changes.append(f"cleared {format_bytes(total)}")
        return ", ".join(changes) if changes else None
    if event.kind in {"reset", "period-reset"}:
        total = details.get("cleared_total_bytes")
        if not isinstance(total, int):
            return None
        summary = f"cleared {format_bytes(total)}"
        mode = details.get("mode")
        if isinstance(mode, str):
            summary = f"{summary}, mode={mode}"
        return summary
    if event.kind == "throttle-applied":
        rate = details.get("rate_bps")
        if isinstance(rate, int):
            return f"rate {format_bps(rate)}"
    return None


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


def _dict_value(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def _format_bytes_value(value: object) -> str:
    return format_bytes(value) if isinstance(value, int) else str(value)


def _format_bps_value(value: object) -> str:
    return format_bps(value) if isinstance(value, int) else str(value)
