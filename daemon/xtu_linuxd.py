"""xtu-linuxd — privileged daemon providing MSR access over a Unix socket.

Runs as root via systemd. Exposes /run/xtu-linux.sock restricted to the
'xtuctl' group so the unprivileged GUI can issue commands without sudo.

Commands: get_status, set_turbo_ratio, set_power_limit, reset_stock,
save_active/load_active, and named-profile save_profile/list_profiles/
load_profile/delete_profile.
Every write is validated against the SafetyConfig envelope and read back
afterwards to confirm the hardware applied the requested value.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import protocol  # noqa: E402
from common.protocol import (  # noqa: E402
    MSR as MSRAddr,
    SafetyConfig,
    ValidationError,
    compose_power_limit_msr,
    compose_turbo_value,
    read_power_unit,
    validate_power_limits,
    validate_turbo_ratios,
)
from daemon.msr import MSR, MSRError  # noqa: E402
from daemon.status import StatusReader  # noqa: E402

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class MockMSR:
    """Deterministic, in-memory stand-in for /dev/cpu/0/msr.

    Used with --mock so the socket/JSON protocol and the daemon logic can be
    exercised end-to-end on hosts without MSR support (and in CI). Values are
    chosen to resemble the E5-1650 v3: 3.5 GHz base, 3.8 GHz max single-core
    turbo, ~140 W TDP.
    """

    def __init__(self):
        # TjMax 99, package temp 99 - 42 = 57 C.
        self.regs = {
            protocol.MSR.IA32_PERF_STATUS: 0x26,                       # current ratio 38 (0x26)
            protocol.MSR.MSR_PLATFORM_INFO: (0x23 << 48) | (0x3F << 40),  # min 35x, max 63x
            protocol.MSR.MSR_TURBO_RATIO_LIMIT: self._turbo_mock(),
            protocol.MSR.MSR_RAPL_POWER_UNIT: 0x3,                    # 0.125 W LSB (2^3)
            protocol.MSR.MSR_PKG_POWER_LIMIT: self._power_mock(),
            protocol.MSR.IA32_TEMPERATURE_TARGET: (0x63 << 16),       # TjMax 99
            protocol.MSR.IA32_PACKAGE_THERM_STATUS: (0x2A << 16),     # 99 - 42 = 57 C
        }
        # RAPL energy counter inflates monotonically with wall-clock time so
        # the daemon can report a stable ~165 W power draw (165e6 uJ/s with an
        # ESU of 0 -> 1 uJ per LSB).
        self._energy_rate = 165e6
        self._energy_start = 0
        self._energy_t0 = time.monotonic()

    @property
    def mock_power_watts(self) -> float:
        return self._energy_rate * 1e-6

    @staticmethod
    def _turbo_mock() -> int:
        # 1-2 cores 38.0x, 3-4 cores 36.0x, 5-6 cores 34.0x (integer ratios).
        return (34 << 16) | (36 << 8) | 38

    @staticmethod
    def _power_mock() -> int:
        # PL1 140 W, PL2 180 W with 0.125 W LSB (raw = watts * 8).
        pl1 = 140 * 8
        pl2 = 180 * 8
        return (pl2 << 32) | (1 << 47) | (1 << 15) | pl1

    def read(self, register: int) -> int:
        if register == protocol.MSR.MSR_PKG_ENERGY_STATUS:
            elapsed = time.monotonic() - self._energy_t0
            return int(self._energy_start + elapsed * self._energy_rate)
        return self.regs.get(register, 0)

    def write(self, register: int, value: int) -> None:
        self.regs[register] = value & ((1 << 64) - 1)

    def write_verified(self, register: int, value: int) -> None:
        self.write(register, value)
        if self.read(register) != value:
            raise MSRError("mock write did not stick")


class Daemon:
    def __init__(self, msr, socket_path, log, state_dir, stock_profile_path,
                 tcp_port=None, config: SafetyConfig | None = None,
                 active_profile_path=None):
        self.msr = msr
        self.socket_path = socket_path
        self.tcp_port = tcp_port
        self.log = log
        self.state_dir = state_dir
        self.stock_profile_path = stock_profile_path
        self.active_profile_path = active_profile_path or (state_dir / "active_profile.json")
        self.config = config or SafetyConfig()
        self.reader = StatusReader(msr)
        self._shutdown = threading.Event()

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir = state_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_stock_profile()

    def _platform_max_ratio(self) -> int:
        """Highest multiplier this CPU supports, from MSR_PLATFORM_INFO."""
        raw = self.msr.read(MSRAddr.MSR_PLATFORM_INFO)
        return (raw >> 40) & 0xFF

    # -- stock profile persistence --------------------------------------------
    def _ensure_stock_profile(self) -> None:
        if self.stock_profile_path.exists():
            return
        stock = {
            "turbo_ratio_limits": protocol.split_turbo_from_value(
                self.msr.read(protocol.MSR.MSR_TURBO_RATIO_LIMIT)
            ),
            "pkg_power_raw": self.msr.read(protocol.MSR.MSR_PKG_POWER_LIMIT),
        }
        self.stock_profile_path.write_text(json.dumps(stock, indent=2))
        self.log.info("Captured stock baseline -> %s", self.stock_profile_path)

    def _load_stock_profile(self) -> dict:
        try:
            return json.loads(self.stock_profile_path.read_text())
        except (OSError, json.JSONDecodeError):
            self.log.error("Could not read stock profile; re-capturing")
            self.stock_profile_path.unlink(missing_ok=True)
            self._ensure_stock_profile()
            return json.loads(self.stock_profile_path.read_text())

    # -- command handlers ------------------------------------------------------
    def _cmd_get_status(self) -> dict:
        data = self.reader.snapshot()
        data["safety_config"] = self.config.as_dict()
        data["stock"] = self._stock_snapshot()
        return protocol.success(data)

    def _stock_snapshot(self) -> dict:
        """Return the persisted stock baseline (turbo + power) for GUI
        stock-vs-current comparison."""
        try:
            stock = self._load_stock_profile()
            return {
                "turbo_ratio_limits": stock["turbo_ratio_limits"],
                "power_limits": self._power_limits_from_raw(stock["pkg_power_raw"]),
            }
        except (OSError, KeyError, json.JSONDecodeError):
            return {}

    def _power_limits_from_raw(self, raw: int) -> dict:
        power_unit = read_power_unit(self.msr)
        pl1_raw = (raw >> protocol.PKG_POWER_PL1_SHIFT) & protocol.PKG_POWER_PL1_MASK
        pl2_raw = (raw >> protocol.PKG_POWER_PL2_SHIFT) & protocol.PKG_POWER_PL2_MASK
        return {
            "pl1_watts": round(protocol.raw_to_watts(pl1_raw, power_unit), 1),
            "pl2_watts": round(protocol.raw_to_watts(pl2_raw, power_unit), 1),
        }

    def _apply_params(self, params: dict) -> dict:
        """Validate and write the turbo/power fields in `params`, returning the
        applied values. Raises ValidationError/MSRError on failure so callers
        (set_*, save_*, boot restore) can report consistently.
        """
        result = {}

        ratios = params.get("turbo_ratio_limits")
        if ratios is not None:
            platform_max = self._platform_max_ratio()
            validate_turbo_ratios(ratios, self.config, platform_max)
            new_value = compose_turbo_value(ratios)
            self.msr.write_verified(MSRAddr.MSR_TURBO_RATIO_LIMIT, new_value)
            result["turbo_ratio_limits"] = protocol.split_turbo_from_value(new_value)
            self.log.info("apply turbo_ratio_limits: %s (0x%X)", ratios, new_value)

        power = params.get("power_limits")
        if power is not None:
            pl1 = power.get("pl1_watts")
            pl2 = power.get("pl2_watts")
            validate_power_limits(pl1, pl2, self.config)
            power_unit = read_power_unit(self.msr)
            current = self.msr.read(MSRAddr.MSR_PKG_POWER_LIMIT)
            new_value = compose_power_limit_msr(current, float(pl1), float(pl2),
                                                power_unit)
            self.msr.write_verified(MSRAddr.MSR_PKG_POWER_LIMIT, new_value)
            result["power_limits"] = {"pl1_watts": pl1, "pl2_watts": pl2}
            self.log.info("apply power_limits: pl1=%sW pl2=%sW (0x%X)",
                          pl1, pl2, new_value)

        return result

    def _cmd_set_turbo_ratio(self, message: dict) -> dict:
        ratios = {g: message.get(g) for g in protocol.TURBO_GROUPS}
        try:
            result = self._apply_params({"turbo_ratio_limits": ratios})
        except (ValidationError, MSRError) as exc:
            self.log.warning("set_turbo_ratio rejected: %s", exc)
            return protocol.failure(str(exc))
        return protocol.success(result)

    def _cmd_set_power_limit(self, message: dict) -> dict:
        power = {"pl1_watts": message.get("pl1_watts"),
                 "pl2_watts": message.get("pl2_watts")}
        try:
            result = self._apply_params({"power_limits": power})
        except (ValidationError, MSRError) as exc:
            self.log.warning("set_power_limit rejected: %s", exc)
            return protocol.failure(str(exc))
        return protocol.success(result)

    def _cmd_save_active(self, message: dict) -> dict:
        params = message.get("params", {})
        try:
            self._apply_params(params)
        except (ValidationError, MSRError) as exc:
            self.log.warning("save_active rejected: %s", exc)
            return protocol.failure(str(exc))

        self.active_profile_path.write_text(
            json.dumps({"params": params}, indent=2)
        )
        self.log.info("save_active: persisted to %s", self.active_profile_path)
        return protocol.success({"saved": True, "params": params})

    def _cmd_load_active(self) -> dict:
        try:
            data = json.loads(self.active_profile_path.read_text())
            return protocol.success({"params": data.get("params", {})})
        except (OSError, json.JSONDecodeError):
            return protocol.success({"params": {}})

    def _apply_active_on_boot(self) -> None:
        """Re-apply the last saved active profile at startup (best-effort)."""
        try:
            data = json.loads(self.active_profile_path.read_text())
            params = data.get("params", {})
        except (OSError, json.JSONDecodeError):
            return
        if not params:
            return
        try:
            self._apply_params(params)
            self.log.info("boot: re-applied active profile: %s", params)
        except (ValidationError, MSRError) as exc:
            self.log.error("boot: failed to re-apply active profile: %s", exc)

    def _cmd_reset_stock(self) -> dict:
        stock = self._load_stock_profile()

        turbo_value = protocol.compose_turbo_value(stock["turbo_ratio_limits"])
        self.msr.write_verified(protocol.MSR.MSR_TURBO_RATIO_LIMIT, turbo_value)
        self.log.info(
            "reset_stock: turbo_ratio_limits=%s",
            stock["turbo_ratio_limits"],
        )

        pkg_raw = stock["pkg_power_raw"]
        self.msr.write_verified(protocol.MSR.MSR_PKG_POWER_LIMIT, pkg_raw)
        self.log.info("reset_stock: MSR_PKG_POWER_LIMIT=0x%X", pkg_raw)

        return protocol.success(
            {
                "turbo_ratio_limits": stock["turbo_ratio_limits"],
                "restored": True,
            }
        )

    # -- named profiles ---------------------------------------------------------
    _RESERVED_PROFILE_NAMES = {"Stock", "Stock Profile"}

    def _profile_path(self, name) -> Path:
        """Validate a profile name and return its on-disk path. Raises
        ValidationError for empty, path-traversing, or reserved names."""
        clean = (name or "").strip()
        if not clean or clean in self._RESERVED_PROFILE_NAMES \
                or "/" in clean or "\\" in clean or "\x00" in clean \
                or clean in (".", ".."):
            raise ValidationError(f"invalid or reserved profile name: {name!r}")
        return self.profiles_dir / f"{clean}.json"

    def _cmd_save_profile(self, message: dict) -> dict:
        name = message.get("name")
        params = message.get("params", {})
        try:
            # validate + apply live before persisting, so we never save a
            # profile the hardware rejected.
            self._apply_params(params)
            path = self._profile_path(name)
        except (ValidationError, MSRError) as exc:
            self.log.warning("save_profile rejected: %s", exc)
            return protocol.failure(str(exc))

        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"params": params}, indent=2))
        self.log.info("save_profile %r -> %s", name, path)
        return protocol.success({"saved": True, "name": name, "params": params})

    def _cmd_list_profiles(self) -> dict:
        if not self.profiles_dir.exists():
            return protocol.success({"profiles": []})
        names = sorted(
            p.stem for p in self.profiles_dir.iterdir()
            if p.suffix == ".json" and p.is_file()
        )
        return protocol.success({"profiles": names})

    def _cmd_load_profile(self, message: dict) -> dict:
        name = message.get("name")
        try:
            path = self._profile_path(name)
        except ValidationError as exc:
            return protocol.failure(str(exc))
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            self.log.warning("load_profile: %r not found", name)
            return protocol.failure(f"profile not found: {name!r}")
        params = data.get("params", {})
        if not params:
            return protocol.failure(f"profile empty: {name!r}")
        try:
            result = self._apply_params(params)
        except (ValidationError, MSRError) as exc:
            self.log.warning("load_profile %r rejected: %s", name, exc)
            return protocol.failure(str(exc))
        self.log.info("load_profile %r applied: %s", name, result)
        return protocol.success({"name": name, "params": result, "applied": True})

    def _cmd_delete_profile(self, message: dict) -> dict:
        name = message.get("name")
        try:
            path = self._profile_path(name)
        except ValidationError as exc:
            return protocol.failure(str(exc))
        try:
            path.unlink()
        except FileNotFoundError:
            return protocol.failure(f"profile not found: {name!r}")
        self.log.info("delete_profile %r", name)
        return protocol.success({"deleted": True, "name": name})

    # -- socket loop -------------------------------------------------------------
    def _dispatch(self, message: dict) -> dict:
        cmd = message.get("cmd")
        if cmd == "get_status":
            return self._cmd_get_status()
        if cmd == "set_turbo_ratio":
            return self._cmd_set_turbo_ratio(message)
        if cmd == "set_power_limit":
            return self._cmd_set_power_limit(message)
        if cmd == "save_active":
            return self._cmd_save_active(message)
        if cmd == "load_active":
            return self._cmd_load_active()
        if cmd == "reset_stock":
            return self._cmd_reset_stock()
        if cmd == "save_profile":
            return self._cmd_save_profile(message)
        if cmd == "list_profiles":
            return self._cmd_list_profiles()
        if cmd == "load_profile":
            return self._cmd_load_profile(message)
        if cmd == "delete_profile":
            return self._cmd_delete_profile(message)
        return protocol.failure(f"unknown command: {cmd!r}")

    def _handle_client(self, conn, addr) -> None:
        self.log.debug("connection from %s", addr)
        try:
            with conn:
                buf = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            message = protocol.decode(line)
                        except json.JSONDecodeError as exc:
                            conn.sendall(
                                json.dumps(
                                    protocol.failure(f"bad json: {exc}")
                                ).encode("utf-8")
                                + b"\n"
                            )
                            continue
                        response = self._dispatch(message)
                        conn.sendall(
                            json.dumps(response).encode("utf-8") + b"\n"
                        )
                        self.log.debug("cmd=%s -> ok=%s", message.get("cmd"), response.get("ok"))
        except OSError:
            pass

    def run(self) -> None:
        self._apply_active_on_boot()
        use_tcp = self.tcp_port is not None
        if use_tcp:
            self.log.info("daemon starting (tcp 127.0.0.1:%d)", self.tcp_port)
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", self.tcp_port))
        else:
            self.log.info("daemon starting (socket=%s)", self.socket_path)
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
            try:
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(self.socket_path))
                os.chmod(str(self.socket_path), 0o660)
                self.log.info("listening on %s", self.socket_path)
            except (OSError, AttributeError) as exc:
                self.log.error("failed to bind %s: %s", self.socket_path, exc)
                return
        try:
            server.listen(16)
            while not self._shutdown.is_set():
                try:
                    conn, addr = server.accept()
                except OSError:
                    break
                threading.Thread(
                    target=self._handle_client, args=(conn, addr), daemon=True
                ).start()
        finally:
            server.close()
            self.log.info("daemon stopped")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="xtu-linuxd MSR daemon")
    parser.add_argument("--socket", default="/run/xtu-linux.sock")
    parser.add_argument("--log", default="/var/log/xtu-linux.log")
    parser.add_argument("--state-dir", default="/var/lib/xtu-linux")
    parser.add_argument("--cpu", type=int, default=0)
    parser.add_argument("--mock", action="store_true",
                        help="use a deterministic in-memory MSR (testing/CI)")
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="safety config JSON (default /etc/xtu-linux/config.json)")
    parser.add_argument("--tcp", type=int, default=None, metavar="PORT",
                        help="listen on 127.0.0.1:PORT instead of a unix socket")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format=LOG_FORMAT)
    log = logging.getLogger("xtu-linuxd")

    state_dir = Path(args.state_dir)
    socket_path = Path(args.socket)
    log_path = Path(args.log)

    # Mirror to a file when we can (root + real path), otherwise fall back to
    # stdout/stderr (useful for foreground testing).
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh.setFormatter(logging.Formatter(LOG_FORMAT))
        log.addHandler(fh)
    except OSError:
        pass

    if args.mock:
        msr = MockMSR()
    else:
        try:
            msr = MSR(cpu=args.cpu).open()
        except MSRError as exc:
            log.error("MSR unavailable: %s (use --mock for testing)", exc)
            return 2

    config = SafetyConfig.load(Path(args.config) if args.config else None)
    log.info("safety config: %s", config.as_dict())

    daemon = Daemon(
        msr=msr,
        socket_path=socket_path,
        log=log,
        state_dir=state_dir,
        stock_profile_path=state_dir / "stock.json",
        tcp_port=args.tcp,
        config=config,
    )

    try:
        daemon.run()
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        if not args.mock:
            msr.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
