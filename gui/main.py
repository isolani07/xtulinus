#!/usr/bin/env python3
"""XTU-Linux GUI entry point.

Tabs: Status (live readout), Advanced Tuning, Profiles (named tuning
profiles + stock reset), Monitoring (live graphs), About & Safety.
The hardware-writing tabs (Advanced Tuning, Profiles) are gated behind a
one-time safety acknowledgment dialog on first launch.

Usage:
    python gui/main.py [--socket /run/xtu-linux.sock] [--tcp PORT]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui import about  # noqa: E402
from gui.client import DaemonClient  # noqa: E402
from gui.monitoring import MonitoringTab  # noqa: E402
from gui.profiles import ProfilesTab  # noqa: E402
from gui.status import StatusTab  # noqa: E402
from gui.tuning import AdvancedTuningTab  # noqa: E402


class MainWindow(QMainWindow):
    def __init__(self, socket_path: str, tcp_port: int | None = None):
        super().__init__()
        self.setWindowTitle("XTU-Linux")
        self.resize(640, 520)

        self.client = DaemonClient(socket_path, tcp_port=tcp_port, parent=self)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.status_tab = StatusTab(self.client)
        self.tabs.addTab(self.status_tab, "Status")

        self.tuning_tab = AdvancedTuningTab(self.client)
        self.tabs.addTab(self.tuning_tab, "Advanced Tuning")

        self.profiles_tab = ProfilesTab(self.client)
        self.tabs.addTab(self.profiles_tab, "Profiles")

        self.monitoring_tab = MonitoringTab(self.client)
        self.tabs.addTab(self.monitoring_tab, "Monitoring")

        self.about_tab = about.AboutTab(self.client, on_acknowledge=self._enable_writes)
        self.tabs.addTab(self.about_tab, "About & Safety")

    # -- safety gating --------------------------------------------------------
    def _enable_writes(self):
        self.tabs.setTabEnabled(self.tabs.indexOf(self.tuning_tab), True)
        self.tabs.setTabEnabled(self.tabs.indexOf(self.profiles_tab), True)

    def _disable_writes(self):
        self.tabs.setTabEnabled(self.tabs.indexOf(self.tuning_tab), False)
        self.tabs.setTabEnabled(self.tabs.indexOf(self.profiles_tab), False)

    def closeEvent(self, event):
        self.client.close()
        self.client.worker.wait(2000)
        super().closeEvent(event)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="XTU-Linux GUI")
    parser.add_argument("--socket", default="/run/xtu-linux.sock")
    parser.add_argument("--tcp", type=int, default=None, metavar="PORT",
                        help="talk to daemon over 127.0.0.1:PORT (dev/testing)")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("XTU-Linux")
    app.setOrganizationName("xtu-linux")

    window = MainWindow(args.socket, tcp_port=args.tcp)
    window.show()

    if not about.is_accepted():
        window._disable_writes()
        dialog = about.DisclaimerDialog(window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            about.set_accepted(True)
            window._enable_writes()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
