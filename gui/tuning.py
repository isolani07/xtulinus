"""Advanced Tuning tab: per-group turbo ratios, PL1/PL2 power limits, Apply
and Apply-Save-Boot-Default buttons, plus stock-vs-current indicators and a
persistent warning banner when a non-stock profile is active.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from common.protocol import TURBO_GROUPS, TURBO_GROUP_SHIFTS
from common.protocol import DEFAULT_SAFE_TURBO_CEILING, STOCK_TDP_WATTS

GROUP_LABELS = {
    "cores_1_2": "1-2 active cores",
    "cores_3_4": "3-4 active cores",
    "cores_5_6": "5-6 active cores",
}


class AdvancedTuningTab(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.client.response_ready.connect(self._on_response)

        self._stock = None          # last known stock snapshot
        self._current = None        # last known current values
        self._turbo_controls = {}   # group -> (QSlider, QSpinBox)
        self._stock_labels = {}     # group -> QLabel ("stock: Nx")
        self._pl1_box = None
        self._pl2_box = None

        self._build()
        self.client.request("get_status")

    # -- UI construction ------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)

        # Persistent warning banner for an active non-stock profile.
        self.warning = QLabel("⚠ Unsupported overclocking active")
        self.warning.setObjectName("warningBanner")
        self.warning.setWordWrap(True)
        self.warning.hide()
        layout.addWidget(self.warning)

        # Turbo ratios
        turbo_group = QGroupBox("Turbo Ratio (multiplier)")
        turbo_layout = QVBoxLayout(turbo_group)
        for group in TURBO_GROUPS:
            row = QHBoxLayout()
            slider = QSlider(Qt.Horizontal)
            spin = QSpinBox()
            spin.setSuffix(" x")
            slider.setMinimum(10)
            slider.setMaximum(DEFAULT_SAFE_TURBO_CEILING)
            spin.setMinimum(10)
            spin.setMaximum(DEFAULT_SAFE_TURBO_CEILING)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            self._turbo_controls[group] = (slider, spin)

            label = QLabel(GROUP_LABELS[group])
            stock_label = QLabel("")
            stock_label.setObjectName("stockValue")
            self._stock_labels[group] = stock_label

            row.addWidget(label)
            row.addWidget(slider, stretch=1)
            row.addWidget(spin)
            row.addWidget(stock_label)
            turbo_layout.addLayout(row)
        layout.addWidget(turbo_group)

        # Power limits
        power_group = QGroupBox("Package Power Limits")
        form = QFormLayout(power_group)
        self._pl1_box = QDoubleSpinBox()
        self._pl2_box = QDoubleSpinBox()
        for box in (self._pl1_box, self._pl2_box):
            box.setDecimals(0)
            box.setSuffix(" W")
            box.setRange(1, 300)
            box.setSingleStep(5)
        form.addRow("PL1 (long-term)", self._pl1_box)
        form.addRow("PL2 (short-term)", self._pl2_box)
        layout.addWidget(power_group)

        # Buttons
        btn_row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        self.apply_save_btn = QPushButton("Apply and Save as Boot Default")
        self.apply_save_btn.clicked.connect(self._on_apply_save)
        self.reset_btn = QPushButton("Reset to Stock")
        self.reset_btn.clicked.connect(lambda: self.client.request("reset_stock"))

        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.apply_save_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.reset_btn)
        layout.addLayout(btn_row)

        layout.addStretch(1)

    # -- helpers --------------------------------------------------------------
    def _collect_params(self) -> dict:
        """Build the wired params dict from the current control values."""
        return {
            "turbo_ratio_limits": {
                g: self._turbo_controls[g][1].value() for g in TURBO_GROUPS
            },
            "power_limits": {
                "pl1_watts": self._pl1_box.value(),
                "pl2_watts": self._pl2_box.value(),
            },
        }

    def _send_apply(self, save_default: bool) -> None:
        params = self._collect_params()
        self.client.request(
            "set_turbo_ratio",
            **params["turbo_ratio_limits"],
        )
        self.client.request(
            "set_power_limit",
            **params["power_limits"],
        )
        if save_default:
            self.client.request("save_active", params=params)

    def _on_apply(self):
        self._send_apply(save_default=False)

    def _on_apply_save(self):
        self._send_apply(save_default=True)

    # -- response handling ------------------------------------------------------
    def _on_response(self, response: dict):
        cmd = response.get("_cmd")
        if cmd == "get_status":
            if response.get("ok"):
                self._apply_status(response["data"])
            else:
                self._show_error(response.get("error", "unknown error"))
        elif cmd in ("set_turbo_ratio", "set_power_limit", "save_active"):
            if response.get("ok"):
                self.client.request("get_status")  # refresh indicators
            else:
                self._show_error(response.get("error", "unknown error"))
        elif cmd == "reset_stock":
            if response.get("ok"):
                self.client.request("get_status")

    def _apply_status(self, data: dict):
        self._current = data
        stock = data.get("stock") or {}
        safety = data.get("safety_config") or {}
        ceiling = safety.get("safe_turbo_ceiling", DEFAULT_SAFE_TURBO_CEILING)
        min_pl = safety.get("min_pl_watts", 1)
        max_pl = safety.get("max_pl_watts", 300)

        # Update control ranges from authoritative daemon config.
        for _, spin in self._turbo_controls.values():
            spin.setMaximum(ceiling)
        self._pl1_box.setRange(min_pl, max_pl)
        self._pl2_box.setRange(min_pl, max_pl)

        trl = data.get("turbo_ratio_limits", {})
        for group in TURBO_GROUPS:
            value = trl.get(group)
            if value is not None:
                self._turbo_controls[group][1].setValue(value)
            sval = (stock.get("turbo_ratio_limits") or {}).get(group)
            self._stock_labels[group].setText(
                f"stock: {sval}x" if sval is not None else ""
            )

        pl = data.get("power_limits", {})
        if pl.get("pl1_watts") is not None:
            self._pl1_box.setValue(float(pl["pl1_watts"]))
        if pl.get("pl2_watts") is not None:
            self._pl2_box.setValue(float(pl["pl2_watts"]))

        self._update_warning(data, stock)

    def _update_warning(self, data: dict, stock: dict):
        """Show the caution banner whenever current values differ from stock."""
        non_stock = False
        trl = data.get("turbo_ratio_limits", {})
        strl = stock.get("turbo_ratio_limits") or {}
        for group in TURBO_GROUPS:
            if trl.get(group) is not None and strl.get(group) is not None \
                    and trl[group] != strl[group]:
                non_stock = True
        pl = data.get("power_limits", {})
        spl = stock.get("power_limits") or {}
        for key in ("pl1_watts", "pl2_watts"):
            if pl.get(key) is not None and str(spl.get(key)) and \
                    float(pl[key]) != float(spl[key]):
                non_stock = True
        self.warning.setVisible(non_stock)

    def _show_error(self, message: str):
        self.warning.setText(f"⚠ {message}")
        self.warning.setVisible(True)