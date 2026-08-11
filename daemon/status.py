"""Decode monitored MSRs into a human- and GUI-friendly status snapshot."""

from __future__ import annotations

import time

from common import protocol


class StatusReader:
    """Reads every monitored register and returns a dict JSON-serializable
    snapshot for the 'get_status' command."""

    def __init__(self, msr):
        self.msr = msr
        self._prev_energy = None    # previous energy reading (Joules)
        self._prev_time = None      # previous monotonic timestamp

    def _power_watts(self) -> float:
        """Package power draw in watts, computed from the RAPL energy counter
        (MSR_PKG_ENERGY_STATUS) as dEnergy/dTime. Returns 0.0 until a second
        sample is available."""
        now = time.monotonic()
        raw = self.msr.read(protocol.MSR.MSR_PKG_ENERGY_STATUS)
        esu = protocol.read_energy_unit(self.msr)
        joules = protocol.energy_counter_to_joules(raw, esu)

        if self._prev_energy is None:
            self._prev_energy = joules
            self._prev_time = now
            return 0.0

        d_energy = joules - self._prev_energy
        d_time = now - self._prev_time
        self._prev_energy = joules
        self._prev_time = now

        if d_time <= 0 or d_energy < 0:
            return 0.0
        return round(d_energy / d_time, 1)

    @staticmethod
    def _ratio_from_perf_status(value: int) -> dict:
        # IA32_PERF_STATUS: bits 0-7 hold the current requested ratio
        # (multiplier directly, e.g. 38 for 3.8 GHz).
        ratio = value & 0xFF
        return {"ratio": ratio}

    @staticmethod
    def _platform_info(value: int) -> dict:
        # MSR_PLATFORM_INFO (0xCE): bits 40-47 max ratio, bits 48-55 min.
        max_ratio = (value >> 40) & 0xFF
        min_ratio = (value >> 48) & 0xFF
        return {"max_ratio": max_ratio, "min_ratio": min_ratio}

    def snapshot(self) -> dict:
        data = {}

        data["perf_status"] = self._ratio_from_perf_status(
            self.msr.read(protocol.MSR.IA32_PERF_STATUS)
        )
        data["platform_info"] = self._platform_info(
            self.msr.read(protocol.MSR.MSR_PLATFORM_INFO)
        )
        data["current_ratio"] = data["perf_status"]["ratio"]

        data["power_watts"] = self._power_watts()

        raw_ratios = self.msr.read(protocol.MSR.MSR_TURBO_RATIO_LIMIT)
        data["turbo_ratio_limits"] = protocol.split_turbo_from_value(raw_ratios)

        power_unit = protocol.read_power_unit(self.msr)
        data["power_unit"] = power_unit

        raw_pkg = self.msr.read(protocol.MSR.MSR_PKG_POWER_LIMIT)
        pl1_raw = (raw_pkg >> protocol.PKG_POWER_PL1_SHIFT) & protocol.PKG_POWER_PL1_MASK
        pl2_raw = (raw_pkg >> protocol.PKG_POWER_PL2_SHIFT) & protocol.PKG_POWER_PL2_MASK
        data["power_limits"] = {
            "pl1_watts": round(protocol.raw_to_watts(pl1_raw, power_unit), 1),
            "pl2_watts": round(protocol.raw_to_watts(pl2_raw, power_unit), 1),
            # Bit 15 enables PL1; bit 47 enables PL2.
            "pl1_enabled": bool(raw_pkg & (1 << 15)),
            "pl2_enabled": bool(raw_pkg & (1 << 47)),
        }

        tjmax = (self.msr.read(protocol.MSR.IA32_TEMPERATURE_TARGET) >> 16) & 0xFF
        data["tjmax"] = tjmax

        therm = self.msr.read(protocol.MSR.IA32_PACKAGE_THERM_STATUS)
        pkg_temp = tjmax - ((therm >> 16) & 0x7F)
        data["package_temp_c"] = pkg_temp

        return data
