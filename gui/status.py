"""Status tab: a "Read Status" button that pulls a live snapshot from the
daemon and shows it. (Live/Polling monitoring graphs arrive in a later step.)
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class StatusTab(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.client.response_ready.connect(self._on_response)
        self.client.connection_error.connect(self._on_connection_error)

        layout = QVBoxLayout(self)

        self._status_labels = {}

        status_group = QGroupBox("Current Status")
        form = QFormLayout(status_group)
        self._add_row(form, "Clock multiplier", "current_ratio", " x")
        self._add_row(form, "Turbo 1-2 cores", "turbo_1_2", " x")
        self._add_row(form, "Turbo 3-4 cores", "turbo_3_4", " x")
        self._add_row(form, "Turbo 5-6 cores", "turbo_5_6", " x")
        self._add_row(form, "PL1", "pl1", " W")
        self._add_row(form, "PL2", "pl2", " W")
        self._add_row(form, "Package temp", "pkg_temp", " °C")
        self._add_row(form, "TjMax", "tjmax", " °C")
        layout.addWidget(status_group)

        self.read_button = QPushButton("Read Status")
        self.read_button.clicked.connect(lambda: self.client.request("get_status"))
        layout.addWidget(self.read_button, alignment=Qt.AlignRight)

        self._hint = QLabel(
            "Waiting for data...\n"
            "(Ensure the daemon is running:  systemctl status xtu-linuxd)"
        )
        self._hint.setWordWrap(True)
        self._hint.setFrameShape(QFrame.StyledPanel)
        layout.addWidget(self._hint)

        layout.addStretch(1)

    def _add_row(self, form: QFormLayout, text: str, key: str, unit: str):
        value = QLabel("—")
        form.addRow(text, value)
        self._status_labels[key] = (value, unit)

    def _on_response(self, response: dict):
        if response.get("_cmd") != "get_status":
            return
        if not response.get("ok"):
            self._hint.setText(f"Error: {response.get('error')}")
            return

        data = response.get("data", {})
        trl = data.get("turbo_ratio_limits", {})
        powl = data.get("power_limits", {})
        values = {
            "current_ratio": data.get("current_ratio"),
            "turbo_1_2": trl.get("cores_1_2"),
            "turbo_3_4": trl.get("cores_3_4"),
            "turbo_5_6": trl.get("cores_5_6"),
            "pl1": powl.get("pl1_watts"),
            "pl2": powl.get("pl2_watts"),
            "pkg_temp": data.get("package_temp_c"),
            "tjmax": data.get("tjmax"),
        }

        for key, (label, unit) in self._status_labels.items():
            value = values.get(key)
            label.setText("—" if value is None else f"{value}{unit}")

        self._hint.setText("Status updated.")

    def _on_connection_error(self, message: str):
        self._hint.setText(message)