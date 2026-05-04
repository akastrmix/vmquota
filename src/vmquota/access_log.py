from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
import tempfile


_LOCK = threading.Lock()


def append_access_log(
    path: Path,
    *,
    max_entries: int,
    ts: datetime,
    request_path: str,
    uuid: str | None,
    status: int,
    vmid: int | None,
) -> None:
    if max_entries <= 0:
        return
    entry = {
        "ts": ts.isoformat(timespec="seconds"),
        "path": request_path,
        "uuid": uuid,
        "status": status,
        "vmid": vmid,
    }
    line = json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n"
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        _trim_access_log(path, max_entries)


def read_access_log(path: Path, *, limit: int) -> list[dict[str, object]]:
    if limit <= 0 or not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[dict[str, object]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError("access log entry must be a JSON object")
        entries.append(parsed)
    return entries


def _trim_access_log(path: Path, max_entries: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= max_entries:
        return
    keep = lines[-max_entries:]
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(keep) + "\n")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
