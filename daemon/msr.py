"""Low-level access to Intel Model Specific Registers (MSR).

This module talks directly to /dev/cpu/<cpu>/msr using pread/pwrite at the
register's byte offset. It MUST only be imported/used by the root daemon;
the unprivileged GUI never touches this code path.

Layout of /dev/cpu/N/msr:
  * each 64-bit MSR is addressed by its register number * 8 bytes,
  * reads/writes are always exactly 8 bytes wide.
"""

from __future__ import annotations

import os
from pathlib import Path

MSR_DEV_GLOB = "/dev/cpu/*/msr"


class MSRError(RuntimeError):
    """Raised when an MSR read or write fails."""


class MSR:
    """Open a single MSR device file and read/write 64-bit registers.

    Prefer CPU 0 (package 0 owns the per-package RAPL and turbo limits on
    Haswell-EP). Writes are serialized by an optional cross-process lock.
    """

    def __init__(self, cpu: int = 0, device_dir: str = "/dev/cpu"):
        self.device = Path(device_dir) / str(cpu) / "msr"
        self._fd: int | None = None

    def open(self) -> "MSR":
        try:
            self._fd = os.open(str(self.device), os.O_RDWR)
        except OSError as exc:
            raise MSRError(
                f"cannot open {self.device}: {exc.strerror or exc}. "
                "Is 'msr' module loaded and is this process running as root?"
            ) from exc
        return self

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def __enter__(self) -> "MSR":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._fd is None:
            raise MSRError(f"{self.device} is not open")

    def read(self, register: int) -> int:
        self._check_open()
        data = os.pread(self._fd, 8, register * 8)
        if len(data) != 8:
            raise MSRError(f"short read on MSR 0x{register:X}")
        return int.from_bytes(data, "little")

    def write(self, register: int, value: int) -> None:
        self._check_open()
        data = value.to_bytes(8, "little")
        os.pwrite(self._fd, data, register * 8)

    def write_verified(self, register: int, value: int) -> None:
        """Write an MSR and read it back, confirming it matches.

        Raises MSRError if the hardware does not apply the requested value.
        """
        self.write(register, value)
        readback = self.read(register)
        if readback != value:
            raise MSRError(
                f"MSR 0x{register:X} write did not stick: "
                f"requested 0x{value:X}, read back 0x{readback:X}"
            )
