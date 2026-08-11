"""Headless end-to-end test of the minimal GUI against a mock daemon.

Run with:  python tests/test_gui.py
Requires:  PySide6 and a working daemon in --mock --tcp mode (started here).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.main import MainWindow  # noqa: E402
from gui import about  # noqa: E402

PORT = 57982


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "daemon" / "xtu_linuxd.py"),
                "--mock",
                "--tcp", str(PORT),
                "--state-dir", str(tmp_p / "state"),
                "--log", str(tmp_p / "daemon.log"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.6)

            app = QApplication(sys.argv[:1])
            window = MainWindow("/unused.sock", tcp_port=PORT)

            loop = QEventLoop()
            got = {}

            def on_response(response):
                got["resp"] = response
                loop.quit()

            def on_error(message):
                got["err"] = message
                loop.quit()

            window.client.response_ready.connect(on_response)
            window.client.connection_error.connect(on_error)
            QTimer.singleShot(0, lambda: window.client.request("get_status"))
            QTimer.singleShot(5000, loop.quit)
            loop.exec()

            assert "err" not in got, f"connection error: {got.get('err')}"
            assert "resp" in got and got["resp"].get("ok"), f"no ok response: {got}"
            print("daemon response received OK")

            # Give the connected signal delivery a tick, then assert labels.
            QTimer.singleShot(200, loop.quit)
            loop.exec()
            labels = window.status_tab._status_labels
            ratio = labels["current_ratio"][0].text()
            assert ratio == "38 x", f"unexpected current_ratio label: {ratio!r}"
            assert labels["pl1"][0].text() == "140.0 W"
            assert labels["pl2"][0].text() == "180.0 W"
            assert labels["pkg_temp"][0].text() == "57 °C"
            assert labels["turbo_1_2"][0].text() == "38 x"
            print("status labels updated:", ratio, labels["pl1"][0].text(),
                  labels["pkg_temp"][0].text())

            # Detach the early-bird handlers so they no longer quit the loop.
            window.client.response_ready.disconnect(on_response)
            window.client.connection_error.disconnect(on_error)

            # --- Advanced Tuning tab -------------------------------------------
            tuning = window.tuning_tab
            # Controls populated from get_status (mock: 38/36/34, PL1 140, PL2 180)
            assert tuning._turbo_controls["cores_1_2"][1].value() == 38
            assert tuning._turbo_controls["cores_3_4"][1].value() == 36
            assert tuning._turbo_controls["cores_5_6"][1].value() == 34
            assert tuning._pl1_box.value() == 140.0
            assert tuning._pl2_box.value() == 180.0
            # Stock == current initially -> no warning banner.
            assert tuning.warning.isHidden() is True
            print("tuning tab populated from daemon OK")

            # Change values and Apply -> daemon applies, tab refreshes.
            tuning._turbo_controls["cores_1_2"][1].setValue(41)
            tuning._turbo_controls["cores_3_4"][1].setValue(40)
            tuning._turbo_controls["cores_5_6"][1].setValue(38)
            tuning._pl1_box.setValue(160)
            tuning._pl2_box.setValue(200)
            tuning._on_apply()
            QTimer.singleShot(1500, loop.quit)
            loop.exec()
            # Status tab should now show the applied values, and the warning
            # banner should be visible (non-stock active).
            assert labels["turbo_1_2"][0].text() == "41 x"
            assert labels["pl1"][0].text() == "160.0 W"
            assert labels["pl2"][0].text() == "200.0 W"
            assert tuning.warning.isHidden() is False
            print("apply loop OK; warning banner shown")

            # --- Profiles tab ---------------------------------------------------
            profiles = window.profiles_tab
            # Stock Profile is always present and non-deletable by default.
            assert profiles.list.count() >= 1
            assert profiles.list.item(0).text() == "Stock Profile (fixed)"
            assert profiles.delete_btn.isEnabled() is False
            print("profiles tab stock entry OK")

            # Save the currently-applied settings as a named profile.
            profiles.name_edit.setText("MyProfile")
            profiles._on_save()
            QTimer.singleShot(1500, loop.quit)
            loop.exec()
            names = [profiles.list.item(i).text()
                     for i in range(profiles.list.count())]
            assert names == ["Stock Profile (fixed)", "MyProfile"], names
            print("profile save + list OK:", names)

            # Apply the saved profile back.
            profiles.list.setCurrentRow(1)
            profiles._on_apply()
            QTimer.singleShot(1500, loop.quit)
            loop.exec()
            assert labels["turbo_1_2"][0].text() == "41 x"
            print("profile apply OK")

            # Delete the profile; only the fixed Stock entry remains.
            profiles.list.setCurrentRow(1)
            profiles._on_delete()
            QTimer.singleShot(1500, loop.quit)
            loop.exec()
            names = [profiles.list.item(i).text()
                     for i in range(profiles.list.count())]
            assert names == ["Stock Profile (fixed)"], names
            print("profile delete OK")

            # --- Monitoring tab ------------------------------------------------
            monitoring = window.monitoring_tab
            # Switch to Monitoring (index 3) so polling starts.
            window.tabs.setCurrentIndex(3)
            QTimer.singleShot(2500, loop.quit)
            loop.exec()
            for key in ("clock", "temp", "power"):
                assert len(monitoring._buffers[key]) >= 1, (key, monitoring._buffers)
            assert ":" in monitoring._value_labels["temp"].text()
            assert monitoring._value_labels["power"].text().endswith(" W")
            print("monitoring tab polling OK:", {
                k: (len(v), monitoring._value_labels[k].text())
                for k, v in monitoring._buffers.items()
            })

            # --- About & Safety tab + first-launch gating ----------------------
            assert window.tabs.tabText(4) == "About & Safety"
            assert hasattr(window.about_tab, "ack_btn")
            assert isinstance(about.is_accepted(), bool)

            # Gating toggles the hardware-writing tabs.
            window._disable_writes()
            assert window.tabs.isTabEnabled(1) is False
            assert window.tabs.isTabEnabled(2) is False
            assert window.tabs.isTabEnabled(3) is True  # monitoring unaffected
            window._enable_writes()
            assert window.tabs.isTabEnabled(1) is True
            assert window.tabs.isTabEnabled(2) is True
            print("about tab + gating OK")

            # Disclaimer dialog: Continue disabled until the checkbox is checked.
            dlg = about.DisclaimerDialog()
            assert dlg.accept_btn.isEnabled() is False
            dlg.check.setChecked(True)
            assert dlg.accept_btn.isEnabled() is True
            dlg.deleteLater()
            print("disclaimer dialog gating OK")

            window.close()
            window.client.worker.wait(3000)
            app.processEvents()

        finally:
            proc.terminate()
            proc.wait(timeout=5)

    print("GUI TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
