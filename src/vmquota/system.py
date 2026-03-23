from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        *,
        check: bool = False,
        error_message: str = "command failed",
    ) -> CommandResult: ...


@dataclass(slots=True)
class SubprocessCommandRunner:
    dry_run: bool = False

    def run(
        self,
        args: list[str],
        *,
        check: bool = False,
        error_message: str = "command failed",
    ) -> CommandResult:
        if self.dry_run:
            result = CommandResult(args=tuple(args), returncode=0, stdout="", stderr="")
        else:
            try:
                completed = subprocess.run(args, capture_output=True, text=True)
                result = CommandResult(
                    args=tuple(args),
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            except FileNotFoundError as exc:
                result = CommandResult(args=tuple(args), returncode=127, stdout="", stderr=str(exc))
        if check and not result.ok:
            raise RuntimeError(result.stderr.strip() or error_message)
        return result
