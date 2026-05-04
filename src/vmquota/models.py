from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class NicConfig:
    index: int
    bridge: str | None
    firewall: bool
    mac: str | None
    model: str | None
    raw: str


@dataclass(frozen=True, slots=True)
class SourceHook:
    device: str
    hook: str


@dataclass(frozen=True, slots=True)
class TrafficPlan:
    counter_devices: tuple[str, ...]
    upload_hooks: tuple[SourceHook, ...]
    download_hooks: tuple[SourceHook, ...]


@dataclass(frozen=True, slots=True)
class VmInfo:
    vmid: int
    name: str
    status: str
    bios_uuid: str | None
    tags: tuple[str, ...]
    template: bool
    nics: tuple[NicConfig, ...]

    def build_traffic_plan(self, interfaces: set[str]) -> TrafficPlan:
        counter_devices: list[str] = []
        upload_hooks: list[SourceHook] = []
        download_hooks: list[SourceHook] = []
        for nic in self.nics:
            tap_name = f"tap{self.vmid}i{nic.index}"
            if tap_name in interfaces:
                counter_devices.append(tap_name)
                upload_hooks.append(SourceHook(device=tap_name, hook="ingress"))
            fwln_name = f"fwln{self.vmid}i{nic.index}"
            fwpr_name = f"fwpr{self.vmid}p{nic.index}"
            if nic.firewall and fwln_name in interfaces:
                download_hooks.append(SourceHook(device=fwln_name, hook="ingress"))
            elif nic.firewall and fwpr_name in interfaces:
                download_hooks.append(SourceHook(device=fwpr_name, hook="ingress"))
            elif not nic.firewall and tap_name in interfaces:
                download_hooks.append(SourceHook(device=tap_name, hook="egress"))
        return TrafficPlan(
            counter_devices=tuple(counter_devices),
            upload_hooks=tuple(upload_hooks),
            download_hooks=tuple(download_hooks),
        )


@dataclass(slots=True)
class ManagedVm:
    vmid: int
    bios_uuid: str
    name: str
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime
    anchor_day: int
    period_start: datetime
    next_reset_at: datetime
    limit_bytes: int
    throttle_bps: int
    manual_throttle: bool
    throttle_active: bool
    total_bytes: int
    last_sync_at: datetime | None

    @property
    def over_limit(self) -> bool:
        return self.total_bytes >= self.limit_bytes

    @property
    def usage_ratio(self) -> float:
        if self.limit_bytes <= 0:
            return 0.0
        return self.total_bytes / self.limit_bytes

    @property
    def remaining_bytes(self) -> int:
        return max(self.limit_bytes - self.total_bytes, 0)


@dataclass(frozen=True, slots=True)
class VmEvent:
    vmid: int
    bios_uuid: str | None
    ts: datetime
    kind: str
    message: str
    details: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ShapingAction:
    action: Literal["apply", "clear"]
    vmid: int
    plan: TrafficPlan
    rate_bps: int


@dataclass(slots=True)
class VmMutationPlan:
    record: ManagedVm
    counters: dict[str, tuple[int, int]] | None = None
    replace_counters: bool = False
    clear_counters: bool = False
    events: list[VmEvent] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    shaping_actions: list[ShapingAction] = field(default_factory=list)
