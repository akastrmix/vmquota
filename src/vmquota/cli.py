from __future__ import annotations

import argparse
import sys

from .api import serve_api
from .config import AppConfig
from .config import load_config
from .db import StateDB
from .parsing import format_bps, parse_byte_size, parse_rate_bps
from .presentation import (
    local_datetime_text,
    local_event_text,
    remaining_summary,
    state_label,
    usage_progress,
    usage_summary,
)
from .syncer import VmQuotaService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vmquota", description="Per-VM traffic quota manager for Proxmox VE")
    parser.add_argument("--config", help="Config path override")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync", help="Discover VMs and update usage")
    subparsers.add_parser("list", help="List managed VMs")
    subparsers.add_parser("serve", help="Run read-only HTTP usage API")

    show_parser = subparsers.add_parser("show", help="Show one managed VM")
    show_parser.add_argument("vmid", type=int)

    set_parser = subparsers.add_parser("set", help="Update a VM policy")
    set_parser.add_argument("vmid", type=int)
    set_parser.add_argument("--limit", type=parse_byte_size)
    set_parser.add_argument("--throttle", type=parse_rate_bps)
    set_parser.add_argument("--anchor-day", type=int)

    set_range_parser = subparsers.add_parser("set-range", help="Batch update a VMID range")
    set_range_parser.add_argument("vmid_range")
    set_range_parser.add_argument("--limit", type=parse_byte_size)
    set_range_parser.add_argument("--throttle", type=parse_rate_bps)
    set_range_parser.add_argument("--anchor-day", type=int)

    reset_parser = subparsers.add_parser("reset", help="Reset VM usage")
    reset_parser.add_argument("vmid", type=int)
    reset_group = reset_parser.add_mutually_exclusive_group()
    reset_group.add_argument("--usage-only", action="store_true")
    reset_group.add_argument("--reanchor-today", action="store_true")
    reset_group.add_argument("--reanchor-day", type=int)

    throttle_parser = subparsers.add_parser("throttle", help="Apply or clear shaping manually")
    throttle_parser.add_argument("vmid", type=int)
    throttle_group = throttle_parser.add_mutually_exclusive_group(required=True)
    throttle_group.add_argument("--apply", action="store_true")
    throttle_group.add_argument("--clear", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "serve":
        return serve_api(config)
    db = StateDB(config.state_db)
    service = VmQuotaService(config=config, db=db)
    try:
        if args.command == "sync":
            messages = service.sync()
            print(f"sync complete: {len(messages)} action(s)")
            for message in messages:
                print(message)
            return 0
        if args.command == "list":
            _print_list(service.list_vms(), config)
            return 0
        if args.command == "show":
            vm, events = service.show_vm(args.vmid)
            _print_vm(vm, config)
            if events:
                print("")
                print("Recent events:")
                for event in events:
                    print(f"- {local_event_text(event['ts'], config.timezone)} {event['kind']}: {event['message']}")
            return 0
        if args.command == "set":
            if args.limit is None and args.throttle is None and args.anchor_day is None:
                parser.error("set requires at least one of --limit, --throttle, or --anchor-day")
            vm = service.set_vm(args.vmid, limit_bytes=args.limit, throttle_bps=args.throttle, anchor_day=args.anchor_day)
            _print_vm(vm, config)
            return 0
        if args.command == "set-range":
            if args.limit is None and args.throttle is None and args.anchor_day is None:
                parser.error("set-range requires at least one of --limit, --throttle, or --anchor-day")
            updated, skipped = service.set_range(
                args.vmid_range,
                limit_bytes=args.limit,
                throttle_bps=args.throttle,
                anchor_day=args.anchor_day,
            )
            print(f"Updated {len(updated)} VM(s).")
            for vm in updated:
                print(f"- VM {vm.vmid}: {usage_summary(vm)}, throttle={format_bps(vm.throttle_bps)}, reset_day={vm.anchor_day}")
            if skipped:
                print(f"Skipped missing VMID(s): {', '.join(str(vmid) for vmid in skipped)}")
            return 0
        if args.command == "reset":
            vm = service.reset_vm(
                args.vmid,
                usage_only=args.usage_only or not (args.reanchor_today or args.reanchor_day is not None),
                reanchor_today=args.reanchor_today,
                reanchor_day=args.reanchor_day,
            )
            _print_vm(vm, config)
            return 0
        if args.command == "throttle":
            action = "apply" if args.apply else "clear"
            vm = service.throttle_vm(args.vmid, action)
            _print_vm(vm, config)
            return 0
        parser.error(f"unsupported command: {args.command}")
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _print_list(vms: list[object], config: AppConfig) -> None:
    if not vms:
        print("No managed VMs.")
        return
    headers = ["VMID", "Name", "Progress", "Usage", "Remaining", "Next Reset", "State"]
    rows = []
    for vm in vms:
        rows.append(
            [
                str(vm.vmid),
                vm.name,
                usage_progress(vm, width=10),
                usage_summary(vm),
                remaining_summary(vm),
                local_datetime_text(vm.next_reset_at, config.timezone),
                state_label(vm),
            ]
        )
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    print("  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)))


def _print_vm(vm: object, config: AppConfig) -> None:
    print(f"VMID:        {vm.vmid}")
    print(f"Name:        {vm.name}")
    print(f"UUID:        {vm.bios_uuid}")
    print(f"Usage:       {usage_summary(vm)}")
    print(f"Progress:    {usage_progress(vm, width=20)}")
    print(f"Remaining:   {remaining_summary(vm)}")
    print(f"Throttle:    {format_bps(vm.throttle_bps)}")
    print(f"Reset Day:   {vm.anchor_day}")
    print(
        "Period:      "
        f"{local_datetime_text(vm.period_start, config.timezone)} -> "
        f"{local_datetime_text(vm.next_reset_at, config.timezone)}"
    )
    print(f"State:       {state_label(vm)}")
    print(f"Last Seen:   {local_datetime_text(vm.last_seen_at, config.timezone)}")
    print(f"Last Sync:   {local_datetime_text(vm.last_sync_at, config.timezone) if vm.last_sync_at else 'never'}")
