"""About & Safety tab and the first-launch disclaimer dialog.

The disclaimer dialog is shown once before the user can use the hardware-
modifying tools (Advanced Tuning / Profiles). Acceptance is persisted to the
user's Qt config location so it only appears on first launch.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from common.protocol import DEFAULT_SAFE_TURBO_CEILING

ABOUT_TEXT = (
    "XTU-Linux is a Linux GUI overclocking and tuning utility for Intel CPUs, "
    "inspired by Intel's XTU. It consists of two parts:\n"
    "\n"
    "• A privileged daemon (xtu-linuxd) that reads and writes CPU MSRs.\n"
    "• An unprivileged PySide6 GUI that talks to the daemon over a "
    "group-restricted Unix socket.\n"
    "\n"
    "Target CPU: Intel Xeon E5-1650 v3 (Haswell-EP) on X99/C612. "
    "The GUI exposes turbo-ratio limits per active-core group, package power "
    "limits (PL1/PL2), named tuning profiles, and live monitoring of clock, "
    "temperature and power draw."
)

DISCLAIMER_TEXT = (
    "WARNING — USE AT YOUR OWN RISK.\n"
    "\n"
    "XTU-Linux writes directly to CPU model-specific registers (MSRs). "
    "Overclocking, raising turbo ratios, and changing power limits can:\n"
    "\n"
    "• Make your system unstable or fail to boot.\n"
    "• Cause overheating, shorten component life, or damage hardware.\n"
    "• Lead to data loss or corruption.\n"
    "• Void your CPU and motherboard warranty.\n"
    "\n"
    "The daemon enforces safety ceilings from its configuration "
    f"(turbo ratio up to {DEFAULT_SAFE_TURBO_CEILING}x, power limits within the "
    "rated TDP envelope), but this does not guarantee safety.\n"
    "\n"
    "The authors and contributors provide this software \"as is\", without "
    "warranty of any kind, and accept no liability for any damage, data loss, "
    "instability, or other consequence of using it. Ensure you have adequate "
    "cooling and a stable power supply before applying any overclock."
)

LEGAL_TEXT = "© xtu-linux contributors. Licensed as-is; no warranty."
VERSION_TEXT = "XTU-Linux 1.0.0"


def _accept_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    root = Path(base)
    root.mkdir(parents=True, exist_ok=True)
    return root / "acceptance.json"


def is_accepted() -> bool:
    """Return True if the user has already acknowledged the safety warning."""
    try:
        return json.loads(_accept_path().read_text()) == {"accepted": True}
    except (OSError, json.JSONDecodeError):
        return False


def set_accepted(value: bool = True) -> None:
    _accept_path().write_text(json.dumps({"accepted": bool(value)}))


class DisclaimerDialog(QDialog):
    """Modal first-launch dialog gating the hardware-writing tools."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Safety Acknowledgment Required")
        self.setModal(True)
        self.resize(560, 520)

        layout = QVBoxLayout(self)

        heading = QLabel("Before you can use the tuning tools")
        font = heading.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        heading.setFont(font)
        layout.addWidget(heading)

        text = QLabel(DISCLAIMER_TEXT)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignTop)
        layout.addWidget(text)

        layout.addSpacing(8)
        self.check = QCheckBox(
            "I understand the risks and agree to use this software at my own risk."
        )
        layout.addWidget(self.check)

        row = QHBoxLayout()
        self.accept_btn = QPushButton("Continue")
        self.accept_btn.setEnabled(False)
        self.accept_btn.clicked.connect(self.accept)
        decline_btn = QPushButton("Not Now")
        decline_btn.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(decline_btn)
        row.addWidget(self.accept_btn)
        layout.addLayout(row)

        self.check.toggled.connect(self.accept_btn.setEnabled)


class AboutTab(QWidget):
    def __init__(self, client, on_acknowledge=None, parent=None):
        super().__init__(parent)
        self.client = client
        self._on_acknowledge = on_acknowledge
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        about_group = QGroupBox("About")
        about = QLabel(ABOUT_TEXT)
        about.setWordWrap(True)
        about.setAlignment(Qt.AlignTop)
        v = QVBoxLayout(about_group)
        v.addWidget(about)
        layout.addWidget(about_group)

        safety_group = QGroupBox("Safety & Disclaimer")
        saf = QLabel(DISCLAIMER_TEXT)
        saf.setWordWrap(True)
        saf.setAlignment(Qt.AlignTop)
        sv = QVBoxLayout(safety_group)
        sv.addWidget(saf)

        ack_row = QHBoxLayout()
        self.ack_btn = QPushButton("Acknowledge Safety Agreement")
        self.ack_btn.clicked.connect(self._on_acknowledge_clicked)
        self.ack_status = QLabel(
            "Advanced tuning tools are enabled." if is_accepted()
            else "Advanced tuning tools are disabled until acknowledged."
        )
        self.ack_status.setWordWrap(True)
        ack_row.addWidget(self.ack_btn)
        ack_row.addWidget(self.ack_status, stretch=1)
        sv.addLayout(ack_row)
        layout.addWidget(safety_group)

        meta = QFormLayout()
        meta.addRow("Version", QLabel(VERSION_TEXT))
        meta.addRow("License", QLabel(LEGAL_TEXT))
        layout.addLayout(meta)

        layout.addStretch(1)

    def _on_acknowledge_clicked(self):
        dlg = DisclaimerDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            set_accepted(True)
            if self._on_acknowledge is not None:
                self._on_acknowledge()
            self.ack_status.setText("Advanced tuning tools are enabled.")
        else:
            self.ack_status.setText(
                "Acknowledgment not confirmed. Writing tools remain disabled."
            )
