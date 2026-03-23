from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
import json
import sqlite3

from .models import ManagedVm, VmEvent
from .parsing import normalize_anchor_day


def _as_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class StateDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self._configure_connection()
        self._initialize()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> StateDB:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        with self.conn:
            yield

    def _configure_connection(self) -> None:
        pragmas = (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA foreign_keys=ON",
            "PRAGMA busy_timeout=30000",
        )
        for pragma in pragmas:
            self.conn.execute(pragma)

    def _initialize(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS managed_vms (
                vmid INTEGER PRIMARY KEY,
                bios_uuid TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                anchor_day INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                next_reset_at TEXT NOT NULL,
                limit_bytes INTEGER NOT NULL,
                throttle_bps INTEGER NOT NULL,
                manual_throttle INTEGER NOT NULL DEFAULT 0,
                throttle_active INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                last_sync_at TEXT
            );

            CREATE TABLE IF NOT EXISTS nic_counters (
                vmid INTEGER NOT NULL,
                nic TEXT NOT NULL,
                last_rx_bytes INTEGER NOT NULL,
                last_tx_bytes INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (vmid, nic)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vmid INTEGER NOT NULL,
                bios_uuid TEXT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            );
            """
        )
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(managed_vms)").fetchall()
        }
        if "manual_throttle" not in columns:
            self.conn.execute(
                "ALTER TABLE managed_vms ADD COLUMN manual_throttle INTEGER NOT NULL DEFAULT 0"
            )
        self.conn.commit()

    def get_vm(self, vmid: int) -> ManagedVm | None:
        row = self.conn.execute("SELECT * FROM managed_vms WHERE vmid = ?", (vmid,)).fetchone()
        return self._row_to_vm(row) if row else None

    def get_vm_by_uuid(self, bios_uuid: str) -> ManagedVm | None:
        row = self.conn.execute(
            "SELECT * FROM managed_vms WHERE lower(bios_uuid) = lower(?) LIMIT 1",
            (bios_uuid,),
        ).fetchone()
        return self._row_to_vm(row) if row else None

    def list_vms(self) -> list[ManagedVm]:
        rows = self.conn.execute("SELECT * FROM managed_vms ORDER BY vmid").fetchall()
        return [self._row_to_vm(row) for row in rows]

    def upsert_vm(self, vm: ManagedVm, *, commit: bool = True) -> None:
        with self._maybe_transaction(commit):
            self._upsert_vm(vm)

    def save_vm_state(
        self,
        vm: ManagedVm,
        counters: dict[str, tuple[int, int]] | None = None,
        *,
        replace_counters: bool = False,
        clear_counters: bool = False,
        events: list[VmEvent] | None = None,
        commit: bool = True,
    ) -> None:
        with self._maybe_transaction(commit):
            self._upsert_vm(vm, commit=False)
            if replace_counters or clear_counters:
                self.conn.execute("DELETE FROM nic_counters WHERE vmid = ?", (vm.vmid,))
            if counters:
                self.conn.executemany(
                    """
                    INSERT INTO nic_counters (vmid, nic, last_rx_bytes, last_tx_bytes, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (vm.vmid, nic, rx, tx, vm.last_sync_at.isoformat() if vm.last_sync_at else vm.updated_at.isoformat())
                        for nic, (rx, tx) in counters.items()
                    ],
                )
            for event in events or []:
                self._add_event(event)

    def _upsert_vm(self, vm: ManagedVm, *, commit: bool = False) -> None:
        self.conn.execute(
            """
            INSERT INTO managed_vms (
                vmid, bios_uuid, name, created_at, updated_at, last_seen_at,
                anchor_day, period_start, next_reset_at, limit_bytes,
                throttle_bps, manual_throttle, throttle_active, total_bytes, last_sync_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vmid) DO UPDATE SET
                bios_uuid=excluded.bios_uuid,
                name=excluded.name,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                last_seen_at=excluded.last_seen_at,
                anchor_day=excluded.anchor_day,
                period_start=excluded.period_start,
                next_reset_at=excluded.next_reset_at,
                limit_bytes=excluded.limit_bytes,
                throttle_bps=excluded.throttle_bps,
                manual_throttle=excluded.manual_throttle,
                throttle_active=excluded.throttle_active,
                total_bytes=excluded.total_bytes,
                last_sync_at=excluded.last_sync_at
            """,
            (
                vm.vmid,
                vm.bios_uuid,
                vm.name,
                vm.created_at.isoformat(),
                vm.updated_at.isoformat(),
                vm.last_seen_at.isoformat(),
                vm.anchor_day,
                vm.period_start.isoformat(),
                vm.next_reset_at.isoformat(),
                vm.limit_bytes,
                vm.throttle_bps,
                1 if vm.manual_throttle else 0,
                1 if vm.throttle_active else 0,
                vm.total_bytes,
                vm.last_sync_at.isoformat() if vm.last_sync_at else None,
            ),
        )
        if commit:
            self.conn.commit()

    def set_counter(
        self,
        vmid: int,
        nic: str,
        rx: int,
        tx: int,
        updated_at: datetime,
        *,
        commit: bool = True,
    ) -> None:
        with self._maybe_transaction(commit):
            self.conn.execute(
                """
                INSERT INTO nic_counters (vmid, nic, last_rx_bytes, last_tx_bytes, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(vmid, nic) DO UPDATE SET
                    last_rx_bytes=excluded.last_rx_bytes,
                    last_tx_bytes=excluded.last_tx_bytes,
                    updated_at=excluded.updated_at
                """,
                (vmid, nic, rx, tx, updated_at.isoformat()),
            )

    def get_counters(self, vmid: int) -> dict[str, tuple[int, int]]:
        rows = self.conn.execute(
            "SELECT nic, last_rx_bytes, last_tx_bytes FROM nic_counters WHERE vmid = ?",
            (vmid,),
        ).fetchall()
        return {row["nic"]: (row["last_rx_bytes"], row["last_tx_bytes"]) for row in rows}

    def clear_counters(self, vmid: int, *, commit: bool = True) -> None:
        with self._maybe_transaction(commit):
            self.conn.execute("DELETE FROM nic_counters WHERE vmid = ?", (vmid,))

    def add_event(
        self,
        event: VmEvent,
        *,
        commit: bool = True,
    ) -> None:
        with self._maybe_transaction(commit):
            self._add_event(event)

    def recent_events(self, vmid: int, limit: int = 10) -> list[VmEvent]:
        rows = self.conn.execute(
            "SELECT bios_uuid, ts, kind, message, details FROM events WHERE vmid = ? ORDER BY id DESC LIMIT ?",
            (vmid, limit),
        ).fetchall()
        return [self._row_to_event(vmid, row) for row in rows]

    @staticmethod
    def _row_to_vm(row: sqlite3.Row) -> ManagedVm:
        return ManagedVm(
            vmid=row["vmid"],
            bios_uuid=row["bios_uuid"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            anchor_day=normalize_anchor_day(row["anchor_day"]),
            period_start=datetime.fromisoformat(row["period_start"]),
            next_reset_at=datetime.fromisoformat(row["next_reset_at"]),
            limit_bytes=row["limit_bytes"],
            throttle_bps=row["throttle_bps"],
            manual_throttle=bool(row["manual_throttle"]),
            throttle_active=bool(row["throttle_active"]),
            total_bytes=row["total_bytes"],
            last_sync_at=_as_datetime(row["last_sync_at"]),
        )

    def _add_event(self, event: VmEvent) -> None:
        payload = json.dumps(event.details, ensure_ascii=True, sort_keys=True) if event.details else None
        self.conn.execute(
            "INSERT INTO events (vmid, bios_uuid, ts, kind, message, details) VALUES (?, ?, ?, ?, ?, ?)",
            (event.vmid, event.bios_uuid, event.ts.isoformat(), event.kind, event.message, payload),
        )

    def _maybe_transaction(self, commit: bool):
        return self.transaction() if commit else nullcontext()

    @staticmethod
    def _row_to_event(vmid: int, row: sqlite3.Row) -> VmEvent:
        details_text = row["details"]
        details: dict[str, object] | None
        if not details_text:
            details = None
        else:
            try:
                parsed = json.loads(details_text)
            except json.JSONDecodeError:
                details = {"raw": details_text}
            else:
                if isinstance(parsed, dict):
                    details = parsed
                else:
                    details = {"value": parsed}
        return VmEvent(
            vmid=vmid,
            bios_uuid=row["bios_uuid"],
            ts=datetime.fromisoformat(row["ts"]),
            kind=row["kind"],
            message=row["message"],
            details=details,
        )
