from __future__ import annotations

import argparse
from dataclasses import dataclass


DECIMAL_BYTE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
}

IEC_BYTE_UNITS = {
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
}

RATE_UNITS = {
    "bit": 1,
    "kbit": 1000,
    "mbit": 1000**2,
    "gbit": 1000**3,
    "tbit": 1000**4,
    "bps": 8,
    "kbps": 8 * 1000,
    "mbps": 8 * 1000**2,
    "gbps": 8 * 1000**3,
}


@dataclass(frozen=True, slots=True)
class VmidRange:
    start: int
    end: int

    def contains(self, vmid: int) -> bool:
        return self.start <= vmid <= self.end


def _split_number_and_unit(raw: str) -> tuple[float, str]:
    text = raw.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("empty value")
    idx = 0
    while idx < len(text) and (text[idx].isdigit() or text[idx] == "."):
        idx += 1
    number = text[:idx]
    unit = text[idx:] or "b"
    if not number:
        raise ValueError(f"invalid numeric value: {raw!r}")
    return float(number), unit


def parse_byte_size(value: str | int) -> int:
    if isinstance(value, int):
        return value
    amount, unit = _split_number_and_unit(value)
    if unit in DECIMAL_BYTE_UNITS:
        return int(amount * DECIMAL_BYTE_UNITS[unit])
    if unit in IEC_BYTE_UNITS:
        return int(amount * IEC_BYTE_UNITS[unit])
    raise ValueError(f"unsupported byte unit: {unit}")


def parse_rate_bps(value: str | int) -> int:
    if isinstance(value, int):
        return value
    amount, unit = _split_number_and_unit(value)
    if unit in RATE_UNITS:
        return int(amount * RATE_UNITS[unit])
    raise ValueError(f"unsupported rate unit: {unit}")


def validate_anchor_day(value: int) -> int:
    if not 1 <= value <= 31:
        raise ValueError("anchor day must be between 1 and 31")
    return value


def normalize_anchor_day(value: int) -> int:
    return min(max(value, 1), 31)


def parse_anchor_day(value: str | int) -> int:
    try:
        anchor_day = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("anchor day must be an integer between 1 and 31") from exc
    try:
        return validate_anchor_day(anchor_day)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_vmid_ranges(values: list[str]) -> tuple[VmidRange, ...]:
    ranges: list[VmidRange] = []
    for value in values:
        text = value.strip()
        if "-" in text:
            start_text, end_text = text.split("-", 1)
            start = int(start_text)
            end = int(end_text)
        else:
            start = end = int(text)
        if start > end:
            raise ValueError(f"invalid VMID range: {value}")
        ranges.append(VmidRange(start=start, end=end))
    return tuple(ranges)


def vmid_in_ranges(vmid: int, ranges: tuple[VmidRange, ...]) -> bool:
    return any(item.contains(vmid) for item in ranges)


def format_bytes(value: int) -> str:
    suffixes = ["B", "KB", "MB", "GB", "TB", "PB"]
    number = float(value)
    for suffix in suffixes:
        if number < 1000 or suffix == suffixes[-1]:
            if suffix == "B":
                return f"{int(number)} {suffix}"
            return f"{number:.2f} {suffix}"
        number /= 1000
    return f"{value} B"


def format_bps(value: int) -> str:
    suffixes = ["bit/s", "kbit/s", "mbit/s", "gbit/s", "tbit/s"]
    number = float(value)
    for suffix in suffixes:
        if number < 1000 or suffix == suffixes[-1]:
            if suffix == "bit/s":
                return f"{int(number)} {suffix}"
            return f"{number:.2f} {suffix}"
        number /= 1000
    return f"{value} bit/s"
