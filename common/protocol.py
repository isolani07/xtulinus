"""Shared protocol definitions and MSR constants.

This module is imported by both the privileged daemon and the unprivileged
GUI so that the JSON wire protocol and MSR register definitions stay in
sync between the two components.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Fixed paths
# ---------------------------------------------------------------------------
SOCKET_PATH = Path("/run/xtu-linux.sock")
LOG_PATH = Path("/var/log/xtu-linux.log")
STATE_DIR = Path("/var/lib/xtu-linux")
CONFIG_PATH = Path("/etc/xtu-linux/config.json")
PROFILES_DIR = STATE_DIR / "profiles"
STOCK_PROFILE_PATH = STATE_DIR / "stock.json"
ACTIVE_PROFILE_PATH = STATE_DIR / "active_profile.json"

# Hard upper limit reference used by the GUI for slider ranges (47.0x).
DEFAULT_SAFE_TURBO_CEILING = 47

# ---------------------------------------------------------------------------
# MSR register definitions (Haswell-EP / Xeon E5-1650 v3)
# ---
# | MSR                          | Address | Purpose                            |
# |------------------------------|---------|------------------------------------|
# | IA32_PERF_STATUS             | 0x198   | Current clock/voltage readout      |
# | MSR_TURBO_RATIO_LIMIT         | 0x1AD   | Turbo multiplier per core group    |
# | MSR_PLATFORM_INFO              | 0xCE    | Min/max multiplier limits          |
# | MSR_PKG_POWER_LIMIT             | 0x610   | PL1/PL2 package power limits       |
# | MSR_RAPL_POWER_UNIT              | 0x606   | Power/energy/time scaling units    |
# | IA32_TEMPERATURE_TARGET           | 0x1A2   | TjMax reference                    |
# | IA32_PACKAGE_THERM_STATUS          | 0x1B1   | Current package temperature        |
# ---------------------------------------------------------------------------
class MSR:
    IA32_PERF_STATUS = 0x198
    MSR_TURBO_RATIO_LIMIT = 0x1AD
    MSR_PLATFORM_INFO = 0xCE
    MSR_PKG_POWER_LIMIT = 0x610
    MSR_RAPL_POWER_UNIT = 0x606
    MSR_PKG_ENERGY_STATUS = 0x611
    IA32_TEMPERATURE_TARGET = 0x1A2
    IA32_PACKAGE_THERM_STATUS = 0x1B1

# Power-unit bit layout inside MSR_RAPL_POWER_UNIT (0x606).
RAPL_POWER_UNIT_MASK = 0x0F          # bits 0-3
RAPL_TIME_UNIT_SHIFT = 16            # time units (bits 16-19, unused now)
RAPL_ENERGY_UNIT_SHIFT = 8           # energy status units (bits 8-11)
RAPL_ENERGY_UNIT_MASK = 0x0F

# --- Turbo ratio limit (0x1AD) bit layout for Haswell-EP --------------------
# Haswell-EP defines distinct turbo ratios per active-core group.
#   bits 0-7    : turbo ratio when 1-2 cores active
#   bits 8-15   : turbo ratio when 3-4 cores active
#   bits 16-23  : turbo ratio when 5-6 cores active
# Each group holds the CPU multiplier directly (e.g. 40 == 40.0x), which is
# the same integer form used on the wire (set_turbo_ratio "cores_1_2": 40).
TURBO_GROUP_SHIFTS = {
    "cores_1_2": 0,
    "cores_3_4": 8,
    "cores_5_6": 16,
}
TURBO_GROUP_BYTEMASK = 0xFF
TURBO_GROUPS = ("cores_1_2", "cores_3_4", "cores_5_6")

# --- Power units (0x606) ----------------------------------------------------
# The power limit MSR (0x610) is composed of 15-bit fields scaled by the
# RAPL power unit. Defaults shown here are corrected to correct if unreported.

# PL1 field:  bits 15-0  (value = watts * 2**power_unit)
# PL2 field:  bits 47-32 (value = watts * 2**power_unit)
PKG_POWER_PL1_MASK = 0x7FFF          # bits 0-15
PKG_POWER_PL1_SHIFT = 0
PKG_POWER_PL2_MASK = 0x7FFF          # bits 32-47
PKG_POWER_PL2_SHIFT = 32

# Rated TDP of the Xeon E5-1650 v3.
STOCK_TDP_WATTS = 140

# Plausible power-limit ranges (watts) used for safety validation.
MIN_PL_WATTS = 1
MAX_PL_WATTS = 300


@dataclass
class SafetyConfig:
    """Configurable safety limits enforced by the daemon before any MSR write.

    Loaded from /etc/xtu-linux/config.json (or --config); the GUI uses the
    same defaults for its slider ranges so it never offers out-of-range input.
    """

    safe_turbo_ceiling: int = DEFAULT_SAFE_TURBO_CEILING
    min_pl_watts: float = MIN_PL_WATTS
    max_pl_watts: float = MAX_PL_WATTS
    # Upper bound for PL1/PL2 relative to the CPU's rated TDP.
    max_pl_ratio_of_tdp: float = 2.0

    @classmethod
    def load(cls, path: Path | None = None) -> "SafetyConfig":
        cfg = cls()
        path = path or CONFIG_PATH
        try:
            data = json.loads(path.read_text())
            cfg.safe_turbo_ceiling = int(data.get("safe_turbo_ceiling", cfg.safe_turbo_ceiling))
            cfg.min_pl_watts = float(data.get("min_pl_watts", cfg.min_pl_watts))
            cfg.max_pl_watts = float(data.get("max_pl_watts", cfg.max_pl_watts))
            cfg.max_pl_ratio_of_tdp = float(data.get("max_pl_ratio_of_tdp", cfg.max_pl_ratio_of_tdp))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return cfg

    def as_dict(self) -> dict:
        return {
            "safe_turbo_ceiling": self.safe_turbo_ceiling,
            "min_pl_watts": self.min_pl_watts,
            "max_pl_watts": self.max_pl_watts,
            "max_pl_ratio_of_tdp": self.max_pl_ratio_of_tdp,
        }

# ---------------------------------------------------------------------------
# Wire protocol
# ---------------------------------------------------------------------------
def encode(cmd: str, **kwargs) -> bytes:
    """Serialize a request/response payload to JSON bytes."""
    payload = {"cmd": cmd}
    payload.update(kwargs)
    return (json.dumps(payload) + "\n").encode("utf-8")


def decode(raw: bytes) -> dict:
    """Parse a single JSON line from the socket."""
    return json.loads(raw.decode("utf-8"))


def success(data: dict) -> dict:
    return {"ok": True, "data": data}


def failure(error: str) -> dict:
    return {"ok": False, "error": error}


# ---------------------------------------------------------------------------
# Helpers shared by daemon and (later) GUI
# ---------------------------------------------------------------------------
def split_turbo_from_value(value: int) -> dict:
    """Decode a raw 0x1AD read: extract each per-group multiplier."""
    return {
        group: (value >> shift) & TURBO_GROUP_BYTEMASK
        for group, shift in TURBO_GROUP_SHIFTS.items()
    }


def compose_turbo_value(ratios: dict) -> int:
    """Encode per-group multipliers back into a raw 0x1AD value."""
    value = 0
    for group, shift in TURBO_GROUP_SHIFTS.items():
        value |= (int(ratios.get(group, 0)) & TURBO_GROUP_BYTEMASK) << shift
    return value


def watts_to_raw(watts: int, power_unit: int) -> int:
    """Convert watts to the raw 15-bit value stored in the power-limit MSR."""
    return int(round(watts * (2 ** power_unit)))


def raw_to_watts(raw: int, power_unit: int) -> float:
    """Convert a raw 15-bit power-limit field back to watts."""
    return raw / (2 ** power_unit)


def read_power_unit(msr) -> int:
    """Read MSR_RAPL_POWER_UNIT and return the power unit exponent (0-15)."""
    raw = msr.read(MSR.MSR_RAPL_POWER_UNIT)
    return raw & RAPL_POWER_UNIT_MASK


def read_energy_unit(msr) -> int:
    """Read MSR_RAPL_POWER_UNIT and return the energy status unit exponent."""
    raw = msr.read(MSR.MSR_RAPL_POWER_UNIT)
    return (raw >> RAPL_ENERGY_UNIT_SHIFT) & RAPL_ENERGY_UNIT_MASK


def energy_counter_to_joules(raw: int, esu: int) -> float:
    """Convert a raw MSR_PKG_ENERGY_STATUS reading to Joules.

    The RAPL energy counter reports microjoules scaled by 2**ESU, i.e.
    joules = raw * 2**esu * 1e-6.
    """
    return raw * (2 ** esu) * 1e-6


# ---------------------------------------------------------------------------
# Safety validation (used by the daemon before writing any MSR)
# ---------------------------------------------------------------------------
class ValidationError(ValueError):
    """Raised when a requested value falls outside the safe envelope."""


def validate_turbo_ratios(ratios: dict, cfg: SafetyConfig, platform_max: int) -> None:
    """Check per-group turbo ratios against the safe ceiling and the CPU's
    maximum supported multiplier (from MSR_PLATFORM_INFO). Also enforces that
    more active cores never get a *higher* ratio than fewer (1-2 >= 3-4 >= 5-6).
    """
    try:
        values = {g: int(ratios.get(g, 0)) for g in TURBO_GROUPS}
    except (TypeError, ValueError):
        raise ValidationError("turbo ratios must be integers")

    for group, value in values.items():
        if not (10 <= value <= cfg.safe_turbo_ceiling):
            raise ValidationError(
                f"{group} ratio {value}x is outside the safe range "
                f"[10x, {cfg.safe_turbo_ceiling}x]"
            )
        if value > platform_max:
            raise ValidationError(
                f"{group} ratio {value}x exceeds the CPU maximum "
                f"supported multiplier {platform_max}x"
            )

    if not (values["cores_1_2"] >= values["cores_3_4"] >= values["cores_5_6"]):
        raise ValidationError(
            "turbo ratios must be non-increasing with core count "
            "(1-2 >= 3-4 >= 5-6)"
        )


def validate_power_limits(pl1_watts: float, pl2_watts: float, cfg: SafetyConfig,
                          tdp_watts: float = STOCK_TDP_WATTS) -> None:
    """Check PL1/PL2 against the plausible wattage envelope derived from the
    rated TDP and the configured safety config.
    """
    for name, watts in (("pl1", pl1_watts), ("pl2", pl2_watts)):
        try:
            watts = float(watts)
        except (TypeError, ValueError):
            raise ValidationError(f"{name} must be a number of watts")
        if not (cfg.min_pl_watts <= watts <= cfg.max_pl_watts):
            raise ValidationError(
                f"{name} {watts}W is outside the safe range "
                f"[{cfg.min_pl_watts:g}W, {cfg.max_pl_watts:g}W]"
            )
        if watts > tdp_watts * cfg.max_pl_ratio_of_tdp:
            raise ValidationError(
                f"{name} {watts}W exceeds {cfg.max_pl_ratio_of_tdp:g}x the "
                f"rated TDP of {tdp_watts}W"
            )


def compose_power_limit_msr(current_raw: int, pl1_watts: float, pl2_watts: float,
                            power_unit: int) -> int:
    """Rebuild MSR_PKG_POWER_LIMIT, replacing PL1/PL2 fields while preserving
    the existing enable (15/47) and lock (17/49) control bits.
    """
    pl1_raw = watts_to_raw(pl1_watts, power_unit)
    pl2_raw = watts_to_raw(pl2_watts, power_unit)

    keep = current_raw & ~((PKG_POWER_PL1_MASK << PKG_POWER_PL1_SHIFT)
                           | (PKG_POWER_PL2_MASK << PKG_POWER_PL2_SHIFT))
    return keep | (pl1_raw & PKG_POWER_PL1_MASK) \
        | ((pl2_raw & PKG_POWER_PL2_MASK) << PKG_POWER_PL2_SHIFT)
