"""Profiles tab: manage named tuning profiles (save / apply / delete) plus a
fixed Stock Profile entry that acts as an emergency Reset to Stock.

Saving to an existing name overwrites (edits) that profile. The Stock Profile
is always present at the top, fixed and non-deletable.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

STOCK_ENTRY = "Stock Profile (fixed)"
RESERVED_NAMES = {"Stock", "Stock Profile"}


class ProfilesTab(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.client.response_ready.connect(self._on_response)

        self._current = None    # latest get_status data
        self._profiles = []     # saved profile names (excluding stock)

        self._build()

        # "Stock Profile" is always present even before any refresh.
        self.list.addItem(STOCK_ENTRY)
        self._update_buttons(-1)

        self.client.request("get_status")
        self._refresh_list()

    # -- UI construction ------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)

        title = QLabel(
            "An applied profile is written to the running system immediately. "
            "Saving to an existing profile name overwrites it."
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._update_buttons)
        layout.addWidget(self.list)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Profile name")
        layout.addWidget(self.name_edit)

        row = QHBoxLayout()
        self.save_btn = QPushButton("Save Current as Profile")
        self.save_btn.clicked.connect(self._on_save)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._on_apply)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._on_delete)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_list)
        for btn in (self.save_btn, self.apply_btn, self.delete_btn, self.refresh_btn):
            row.addWidget(btn)
        layout.addLayout(row)

        self.hint = QLabel("")
        self.hint.setObjectName("profileHint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        layout.addStretch(1)

    # -- actions --------------------------------------------------------------
    def _refresh_list(self):
        self.client.request("list_profiles")

    def _update_buttons(self, row):
        self.apply_btn.setEnabled(row is not None and row >= 0)
        self.delete_btn.setEnabled(row is not None and row > 0)

    def _build_params_from_current(self) -> dict:
        return {
            "turbo_ratio_limits": self._current.get("turbo_ratio_limits", {}),
            "power_limits": self._current.get("power_limits", {}),
        }

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            self._show_hint("Enter a profile name first.")
            return
        if name in RESERVED_NAMES:
            self._show_hint("'Stock' is reserved and cannot be overwritten.")
            return
        if self._current is None:
            self._show_hint("No status yet; try again in a moment.")
            return
        self.client.request(
            "save_profile", name=name, params=self._build_params_from_current()
        )

    def _on_apply(self):
        row = self.list.currentRow()
        if row is None or row < 0:
            return
        if row == 0:
            self._show_hint("Restoring stock settings...")
            self.client.request("reset_stock")
            return
        name = self._profiles[row - 1]
        self._show_hint(f"Applying profile '{name}'...")
        self.client.request("load_profile", name=name)

    def _on_delete(self):
        row = self.list.currentRow()
        if row is None or row <= 0:
            return
        name = self._profiles[row - 1]
        self._show_hint(f"Deleting profile '{name}'...")
        self.client.request("delete_profile", name=name)

    # -- response handling ------------------------------------------------------
    def _on_response(self, response: dict):
        cmd = response.get("_cmd")
        if not response.get("ok"):
            if cmd in ("save_profile", "load_profile", "delete_profile",
                       "list_profiles"):
                self._show_hint(response.get("error", "Error"))
            return
        data = response.get("data", {})
        if cmd == "get_status":
            self._current = data
        elif cmd == "list_profiles":
            self._profiles = data.get("profiles", [])
            self._repopulate()
        elif cmd == "save_profile":
            self._show_hint(f"Saved profile '{data.get('name')}'.")
            self._refresh_list()
        elif cmd == "load_profile":
            self._show_hint(f"Applied profile '{data.get('name')}'.")
            self.client.request("get_status")  # refresh status/tuning tabs
        elif cmd == "delete_profile":
            self._show_hint(f"Deleted profile '{data.get('name')}'.")
            self._refresh_list()
        elif cmd == "reset_stock":
            self.client.request("get_status")

    def _repopulate(self):
        current = self.list.currentRow()
        self.list.clear()
        self.list.addItem(STOCK_ENTRY)
        for name in self._profiles:
            self.list.addItem(name)
        # Preserve selection where possible.
        self.list.setCurrentRow(current if current is not None else -1)
        self._update_buttons(self.list.currentRow())

    def _show_hint(self, message: str):
        self.hint.setText(message)
