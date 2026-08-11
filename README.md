# XTU-Linux

GUI overclocking utility for Intel CPUs on Linux, inspired by Intel XTU.
Target: Intel Xeon E5-1650 v3 (Haswell-EP) on X99 (C612) boards.

The tool performs OS-level overclocking/tuning via Model Specific Registers
(MSR), the same mechanism XTU uses on Windows. Because MSR access requires
root while a GUI must not run as root, it is split into two components that
talk over a local Unix socket:

- **`daemon/`** — privileged `xtu-linuxd` service (root). The only component
  with access to `/dev/cpu/*/msr`. Owns the socket, enforces safety
  validation, logs every change, re-applies the last profile on boot.
- **`gui/`** — unprivileged PySide6/Ot GUI speaking JSON over the socket.
- **`common/`** — shared wire-protocol + MSR register definitions.
- **`systemd/`** — `xtu-linuxd.service` unit.
- **`packaging/`** — `.desktop` entry.
- **`icons/`** — scalable app icon.

## Layout

```
daemon/xtu_linuxd.py   entry point (argparse: --socket/--state-dir/--log/--tcp/--mock)
daemon/msr.py          /dev/cpu/N/msr pread/pwrite + write-verify
daemon/status.py       decode MSRs into a JSON snapshot
common/protocol.py     MSR addresses + JSON cmd/response helpers
tests/test_daemon.py   end-to-end socket test (uses --mock, TCP fallback on Windows)
install.sh             installs daemon, GUI, service, desktop entry
```

## Running without installing (dev)

Daemon in mock mode (deterministic fake MSR, no root needed):

```sh
python daemon/xtu_linuxd.py --mock --socket /tmp/xtu-linux.sock \
      --state-dir /tmp/xtu-state --log /tmp/xtu-linux.log -v
```

Test the socket protocol with socat / nc or run the test suite:

```sh
python tests/test_daemon.py
```

Real-mode requires root + the `msr` kernel module and a supported board with
OS-level overclocking enabled in BIOS (unlocked via AMIBCP).

## Install

```sh
sudo ./install.sh
```

Creates the `xtuctl` group, adds your user to it, installs the daemon +
systemd service (enabled on boot), the GUI launcher, and the desktop entry.

## Status

Implemented so far (implementation steps 1-4 of the plan):

- Daemon with `get_status` (read-only MSR snapshot), `set_turbo_ratio`
  (per 1-2/3-4/5-6 core group), `set_power_limit` (PL1/PL2 watts), and
  `reset_stock` (restore persisted stock registers).
- Safety validation before every write: turbo ratios against a configurable
  ceiling and the CPU's max multiplier (MSR_PLATFORM_INFO), plus a
  non-increasing-with-core-count rule; power limits against a plausible range
  derived from rated TDP. Every write is read back (`write_verified`) and
  reported as a failure to the GUI otherwise.
- Unix-socket JSON protocol, verified end-to-end by `tests/test_daemon.py`.
- GUI with a **Status** tab and an **Advanced Tuning** tab: per-group turbo
  sliders/spinboxes, PL1/PL2 watts, "Apply" and "Apply and Save as Boot
  Default" buttons, a "Reset to Stock" button, stock-vs-current labels, and a
  persistent warning banner when a non-stock profile is active. Verified
  headlessly by `tests/test_gui.py`.
- Daemon re-applies the last saved active profile on boot (`save_active` /
  `load_active`).

Next steps: full profile manager tab, and live monitoring graphs.

## Safety

This software modifies low-level CPU parameters via MSR, bypassing BIOS/UEFI
safeguards. Overclocking carries risk of instability, data loss, excess heat
and hardware damage; use entirely at your own risk. Keep a stock profile
handy as a fallback.
