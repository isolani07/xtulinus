# XTU-Linux — Technical Handoff Summary

## Project
Linux GUI overclocking utility for Intel CPUs (XTU-inspired), written in Python. Target: Xeon E5-1650 v3 (Haswell-EP) on X99/C612. Two components over a JSON Unix socket: a root **daemon** (MSR access) and an unprivileged **PySide6 GUI**. All UI text is English.

## Project Structure
```
xtulinus/
├── common/
│   ├── __init__.py
│   └── protocol.py        # MSR address constants, wire protocol, SafetyConfig, validation helpers
├── daemon/
│   ├── __init__.py
│   ├── msr.py             # /dev/cpu/N/msr pread/pwrite + write_verified readback
│   ├── status.py          # decode MSRs into a JSON status snapshot (StatusReader)
│   └── xtu_linuxd.py      # socket server, command dispatch, MockMSR, config, boot re-apply
├── gui/
│   ├── __init__.py
│   ├── client.py          # DaemonClient/DaemonWorker (QThread + FIFO queue, signals)
│   ├── status.py          # Status tab (Read Status button + readout)
│   ├── tuning.py          # Advanced Tuning tab (turbo/power controls, warning banner)
│   └── main.py            # QMainWindow + QTabWidget (Status, Advanced Tuning)
├── systemd/xtu-linuxd.service
├── packaging/
│   ├── xtu-linux.desktop
│   └── config.json        # default safety config
├── icons/xtu-linux.svg
├── install.sh             # group/service/GUI/desktop installer
├── tests/
│   ├── test_daemon.py     # protocol end-to-end (sock/TCP) + boot re-apply
│   └── test_gui.py        # headless offscreen GUI test
└── README.md, .gitignore
```

## MSR Registers (protocol.MSR)
| MSR | Addr | Purpose |
|-----|------|---------|
| IA32_PERF_STATUS | 0x198 | current ratio (bits 0–7, direct multiplier) |
| MSR_TURBO_RATIO_LIMIT | 0x1AD | per active-core-group turbo (10–47x) |
| MSR_PLATFORM_INFO | 0xCE | max ratio bits 40–47, min bits 48–55 |
| MSR_PKG_POWER_LIMIT | 0x610 | PL1 bits 0–15, PL2 bits 32–47, enable bits 15/47, lock 17/49 |
| MSR_RAPL_POWER_UNIT | 0x606 | power unit exponent bits 0–3 (raw = watts × 2^exp) |
| IA32_TEMPERATURE_TARGET | 0x1A2 | TjMax bits 16–23 |
| IA32_PACKAGE_THERM_STATUS | 0x1B1 | temp = TjMax − (bits 16–22) |

Turbo encoding: **integer multiplier** per group (40 = 40.0x), groups `cores_1_2`/`cores_3_4`/`cores_5_6` at bit shifts 0/8/16 of 0x1AD. Power: `raw = watts × 2^power_unit`, 15-bit masks.

## Wire Protocol (JSON, newline-delimited)
```
{"cmd":"get_status"}                                  → data: {..., current_ratio, turbo_ratio_limits, power_limits{pl1/pl2_watts,pl1_enabled,pl2_enabled}, tjmax, package_temp_c, safety_config, stock{...}}
{"cmd":"set_turbo_ratio","cores_1_2":40,"cores_3_4":38,"cores_5_6":36}
{"cmd":"set_power_limit","pl1_watts":160,"pl2_watts":180}
{"cmd":"save_active","params":{turbo_ratio_limits,power_limits}}
{"cmd":"load_active"}                                  → {params}
{"cmd":"reset_stock"}
Response: {"ok":true,"data":{...}} | {"ok":false,"error":"..."}
```
GUI client injects `response["_cmd"]` = originating command for signal routing. `DaemonClient` supports Unix socket (linux) or `--tcp PORT` (dev/Windows, no AF_UNIX).

## Implemented & Working
**Daemon** (`daemon/xtu_linuxd.py`):
- Commands: `get_status`, `set_turbo_ratio`, `set_power_limit`, `save_active`, `load_active`, `reset_stock`.
- Shared `_apply_params()` validates then writes turbo + power; every write uses `write_verified` (readback confirm).
- `SafetyConfig` (loads `/etc/xtu-linux/config.json` or `--config`; defaults ceiling 47, PL 1–300W, max 2.0×TDP=140W). Validation: ceiling, platform max, non-increasing core order, wattage envelope.
- `reset_stock` restores persisted `stock.json` baseline (captured on first run).
- Boot re-apply of `active_profile.json` (`_apply_active_on_boot`).
- `--mock` mode (deterministic MockMSR) + `--tcp` for testing without root.
- systemd unit loads `msr` module (`ExecStartPre=/sbin/modprobe msr`).

**GUI**:
- Tabs: **Status** (readout) and **Advanced Tuning**.
- Advanced Tuning: turbo sliders+spinboxes per group, PL1/PL2 spinboxes, **Apply** (session), **Apply and Save as Boot Default** (`save_active`), **Reset to Stock**, per-field "stock: Nx" labels, and a warning banner shown when current ≠ stock. Control ranges derived from daemon `safety_config`.
- Worker QThread keeps socket I/O off the UI thread (Client signals).

**Tests pass**: `python tests/test_daemon.py`, `python tests/test_gui.py` (offscreen QApplication). Note: on Windows dev box there is no AF_UNIX, so tests run via `--tcp`; daemon/app must be spawned with `stdout/stderr=PIPE` to avoid lost output.

## Gotchas (from debugging)
- Widget tests need `QApplication`, not `QCoreApplication`.
- `isVisible()` is false for unshown/offscreen windows — assert with `isHidden()`.
- A blocked worker `recv` + a test's `on_response` calling `loop.quit()` on any response caused false "stuck" failures; disconnect early-bird handlers before later steps.

## Remaining: Steps 6–8
**Step 6 — Profiles tab (`gui/profiles.py`)** (next):
- List saved profiles: apply / edit / delete.
- "Stock Profile" always present, fixed, non-editable, acts as emergency **Reset to Stock**.
- Save/load/delete profile — daemon currently has `save_active`/`load_active` (single active profile). Extend daemon with named profiles (e.g. `save_profile`/`load_profile`/`list_profiles`/`delete_profile`, persisted under `state_dir/profiles/`), or add a client-side manager. Keep `active_profile.json` as the boot default.
- Requires a new daemon command + storage; wire GUI buttons to it.

**Step 7 — Monitoring tab (`gui/monitoring.py`)**:
- Live graphs: clock speed, temperature, power draw (pyqtgraph or QtCharts).
- Poll `get_status` on a ~1s QTimer only while the tab is active; append to ring buffers; update plots on the UI thread.

**Step 8 — Polish + packaging**:
- About/Safety tab with the full disclaimer.
- First-launch one-time modal (checkbox + "I Understand") gating Advanced Tuning.
- Ensure `install.sh` copies all GUI modules (already updated to `cp -r gui/*.py`), verify systemd unit, `.desktop` entry, icon; final cleanup.

## Suggested next action
Implement Step 6: add named-profile daemon commands (`save_profile`/`list_profiles`/`load_profile`/`delete_profile`) reusing `_apply_params` + `validate_*`, then build `gui/profiles.py` tab and register it in `main.py`. Add matching cases to `tests/test_daemon.py` and a UI test.

## Commands
- Run daemon mock: `python daemon/xtu_linuxd.py --mock --tcp 57980 --state-dir %TEMP%\xtu-state --log %TEMP%\d.log`
- Tests: `python tests/test_daemon.py`, `python tests/test_gui.py`
- Compile check: `python -m py_compile common\protocol.py daemon\xtu_linuxd.py gui\*.py`
- Install: `sudo ./install.sh` (Linux only)