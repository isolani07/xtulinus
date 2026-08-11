"""End-to-end test of the daemon socket/JSON protocol using MockMSR.

Run with:  python tests/test_daemon.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common import protocol  # noqa: E402

# Windows Python builds predating 3.9 (or built without AF_UNIX support)
# cannot create Unix sockets. Fall back to the daemon's --tcp transport,
# which is functionally equivalent for protocol testing.
USE_TCP = not hasattr(socket, "AF_UNIX")
PORT = 57981


def _request(payload: dict, port: int = PORT) -> dict:
    if USE_TCP:
        with socket.create_connection(("127.0.0.1", port)) as s:
            s.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            return json.loads(data.decode("utf-8"))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(sock))
        s.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return json.loads(data.decode("utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        sock = tmp_p / "xtu-linux-test.sock"
        state = tmp_p / "state"
        log = tmp_p / "xtu-linux-test.log"

        cmd = [
            sys.executable,
            str(ROOT / "daemon" / "xtu_linuxd.py"),
            "--mock",
            "--state-dir", str(state),
            "--log", str(log),
        ]
        if USE_TCP:
            cmd += ["--tcp", str(PORT)]
        else:
            cmd += ["--socket", str(sock)]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.6)

            # 1. get_status
            resp = _request({"cmd": "get_status"})
            assert resp["ok"] is True, f"get_status failed: {resp}"
            data = resp["data"]
            assert "current_ratio" in data
            assert set(data["turbo_ratio_limits"].keys()) == {
                "cores_1_2", "cores_3_4", "cores_5_6"
            }
            assert "pl1_watts" in data["power_limits"]
            assert "package_temp_c" in data
            assert "power_watts" in data
            print("get_status OK:", data["current_ratio"],
                  data["turbo_ratio_limits"], data["power_limits"])

            # 2. reset_stock
            resp = _request({"cmd": "reset_stock"})
            assert resp["ok"] is True, f"reset_stock failed: {resp}"
            assert resp["data"]["restored"] is True
            print("reset_stock OK:", resp["data"]["turbo_ratio_limits"])

            # 3. set_turbo_ratio (valid, capped below platform max 63x/ceiling 47)
            resp = _request({"cmd": "set_turbo_ratio",
                             "cores_1_2": 40, "cores_3_4": 39, "cores_5_6": 37})
            assert resp["ok"] is True, f"set_turbo_ratio failed: {resp}"
            assert resp["data"]["turbo_ratio_limits"] == {
                "cores_1_2": 40, "cores_3_4": 39, "cores_5_6": 37}
            print("set_turbo_ratio OK:", resp["data"]["turbo_ratio_limits"])

            # 3a. set_turbo_ratio rejected: above safety ceiling
            resp = _request({"cmd": "set_turbo_ratio",
                             "cores_1_2": 60, "cores_3_4": 39, "cores_5_6": 37})
            assert resp["ok"] is False and "safe range" in resp["error"]
            print("set_turbo_ratio above ceiling rejected OK")

            # 3b. set_turbo_ratio rejected: non-monotonic
            resp = _request({"cmd": "set_turbo_ratio",
                             "cores_1_2": 36, "cores_3_4": 38, "cores_5_6": 37})
            assert resp["ok"] is False and "non-increasing" in resp["error"]
            print("set_turbo_ratio non-monotonic rejected OK")

            # 4. set_power_limit (valid)
            resp = _request({"cmd": "set_power_limit", "pl1_watts": 160, "pl2_watts": 180})
            assert resp["ok"] is True, f"set_power_limit failed: {resp}"
            pl = resp["data"]["power_limits"]
            assert pl["pl1_watts"] == 160 and pl["pl2_watts"] == 180
            print("set_power_limit OK:", pl["pl1_watts"], pl["pl2_watts"])

            # 4a. set_power_limit rejected: above 2x TDP (280W), but under hard max
            resp = _request({"cmd": "set_power_limit", "pl1_watts": 290, "pl2_watts": 180})
            assert resp["ok"] is False and "TDP" in resp["error"]
            print("set_power_limit above TDP mult rejected OK")

            # 4b. set_power_limit rejected: below minimum
            resp = _request({"cmd": "set_power_limit", "pl1_watts": 0, "pl2_watts": 180})
            assert resp["ok"] is False
            print("set_power_limit below minimum rejected OK")

            # 5. unknown command
            resp = _request({"cmd": "nope"})
            assert resp["ok"] is False
            print("unknown command rejected OK")

            # 6. malformed json
            if USE_TCP:
                _sock = socket.create_connection(("127.0.0.1", PORT))
            else:
                _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                _sock.connect(str(sock))
            with _sock as s:
                s.sendall(b"{not json}\n")
                data = b""
                while b"\n" not in data:
                    data += s.recv(4096)
                resp = json.loads(data)
                assert resp["ok"] is False
            print("malformed JSON rejected OK")

            # 7. stock.json persisted
            assert (state / "stock.json").exists()
            print("stock.json persisted OK")

            # 8. save_active / load_active round-trip
            params = {
                "turbo_ratio_limits": {"cores_1_2": 41, "cores_3_4": 40, "cores_5_6": 38},
                "power_limits": {"pl1_watts": 160, "pl2_watts": 180},
            }
            resp = _request({"cmd": "save_active", "params": params})
            assert resp["ok"] is True, f"save_active failed: {resp}"
            resp = _request({"cmd": "load_active"})
            assert resp["ok"] is True and resp["data"]["params"] == params
            print("save_active/load_active OK")

            # 9. save_active rejected: bad params
            bad = {"turbo_ratio_limits": {"cores_1_2": 90, "cores_3_4": 40, "cores_5_6": 38}}
            resp = _request({"cmd": "save_active", "params": bad})
            assert resp["ok"] is False
            print("save_active invalid rejected OK")

            # 10. named profiles: save / list / load / delete round-trip
            pparams = {
                "turbo_ratio_limits": {"cores_1_2": 43, "cores_3_4": 41, "cores_5_6": 39},
                "power_limits": {"pl1_watts": 165, "pl2_watts": 190},
            }
            resp = _request({"cmd": "save_profile", "name": "gaming", "params": pparams})
            assert resp["ok"] is True, f"save_profile failed: {resp}"
            assert resp["data"]["saved"] is True
            print("save_profile OK")

            # list_profiles includes the new one
            resp = _request({"cmd": "list_profiles"})
            assert resp["ok"] is True and "gaming" in resp["data"]["profiles"]
            print("list_profiles OK:", resp["data"]["profiles"])

            # load_profile applies the saved params and reflects them in status
            resp = _request({"cmd": "load_profile", "name": "gaming"})
            assert resp["ok"] is True, f"load_profile failed: {resp}"
            resp = _request({"cmd": "get_status"})
            trl = resp["data"]["turbo_ratio_limits"]
            assert trl == pparams["turbo_ratio_limits"], trl
            pl = resp["data"]["power_limits"]
            assert pl["pl1_watts"] == 165.0 and pl["pl2_watts"] == 190.0, pl
            print("load_profile reflected in status OK")

            # delete_profile removes it; list no longer has it
            resp = _request({"cmd": "delete_profile", "name": "gaming"})
            assert resp["ok"] is True and resp["data"]["deleted"] is True
            resp = _request({"cmd": "list_profiles"})
            assert "gaming" not in resp["data"]["profiles"]
            print("delete_profile OK")

            # 10a. save_profile rejected: reserved/invalid names
            resp = _request({"cmd": "save_profile", "name": "Stock", "params": pparams})
            assert resp["ok"] is False
            resp = _request({"cmd": "save_profile", "name": "../../etc/x", "params": pparams})
            assert resp["ok"] is False
            resp = _request({"cmd": "save_profile", "name": "", "params": pparams})
            assert resp["ok"] is False
            print("save_profile invalid names rejected OK")

            # 10b. save_profile rejected: out-of-range params (never persisted)
            badp = {"turbo_ratio_limits": {"cores_1_2": 60, "cores_3_4": 40, "cores_5_6": 38}}
            resp = _request({"cmd": "save_profile", "name": "bad", "params": badp})
            assert resp["ok"] is False
            resp = _request({"cmd": "list_profiles"})
            assert "bad" not in resp["data"]["profiles"]
            print("save_profile invalid params rejected OK")

            # 10c. load/delete of a missing profile fails cleanly
            resp = _request({"cmd": "load_profile", "name": "missing"})
            assert resp["ok"] is False and "not found" in resp["error"]
            resp = _request({"cmd": "delete_profile", "name": "missing"})
            assert resp["ok"] is False
            print("missing profile load/delete rejected OK")

            # 11. power draw is reported once the energy counter has advanced
            resp = _request({"cmd": "get_status"})
            pw = resp["data"].get("power_watts")
            assert pw is not None and pw > 0, resp
            print("power_watts OK:", pw)

        finally:
            proc.terminate()
            proc.wait(timeout=5)

    print("ALL TESTS PASSED")
    return 0


def test_boot_reapply() -> int:
    """Start a daemon with a pre-seeded active_profile and confirm the
    profile is re-applied on startup."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        state = tmp_p / "state"
        state.mkdir()
        (state / "stock.json").write_text(
            json.dumps({
                "turbo_ratio_limits": {"cores_1_2": 38, "cores_3_4": 36, "cores_5_6": 34},
                "pkg_power_raw": (180 * 8) << 32 | (1 << 47) | (1 << 15) | (140 * 8),
            })
        )
        (state / "active_profile.json").write_text(
            json.dumps({"params": {
                "turbo_ratio_limits": {"cores_1_2": 42, "cores_3_4": 40, "cores_5_6": 38},
                "power_limits": {"pl1_watts": 170, "pl2_watts": 200},
            }})
        )

        cmd = [sys.executable, str(ROOT / "daemon" / "xtu_linuxd.py"),
               "--mock", "--state-dir", str(state), "--log", str(tmp_p / "d.log")]
        if USE_TCP:
            cmd += ["--tcp", str(PORT + 1)]
        else:
            cmd += ["--socket", str(tmp_p / "boot.sock")]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(0.6)
            resp = _request({"cmd": "get_status"}, port=PORT + 1)
            assert resp["ok"] is True, resp
            trl = resp["data"]["turbo_ratio_limits"]
            assert trl == {"cores_1_2": 42, "cores_3_4": 40, "cores_5_6": 38}, trl
            pl = resp["data"]["power_limits"]
            assert pl["pl1_watts"] == 170.0 and pl["pl2_watts"] == 200.0, pl
            print("boot re-apply OK:", trl, pl)
        finally:
            proc.terminate()
            proc.wait(timeout=5)
    return 0


if __name__ == "__main__":
    rc = main()
    if rc == 0:
        rc = test_boot_reapply()
    sys.exit(rc)
