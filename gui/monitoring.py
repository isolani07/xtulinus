"""Monitoring tab: live scrolling graphs of clock speed, package temperature
and power draw.

A 1 s QTimer polls get_status only while this tab is visible (started/stopped
from showEvent/hideEvent). Samples are kept in small ring buffers and the
plots are updated on the UI thread via the client's response signal.
"""

from __future__ import annotations

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

MAX_POINTS = 60
POLL_MS = 1000

COLORS = {"clock": "#1f77b4", "temp": "#d62728", "power": "#2ca02c"}

UNITS = {"clock": "GHz", "temp": "°C", "power": "W"}
TITLES = {"clock": "Clock Speed", "temp": "Package Temperature", "power": "Power Draw"}


class MonitoringTab(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.client.response_ready.connect(self._on_response)

        self._buffers = {"clock": [], "temp": [], "power": []}
        self._value_labels = {}
        self._charts = {}

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

        self._build()
        self._poll()

    # -- UI construction ------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Live readings sampled once per second while this tab is visible."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for key in COLORS:
            title, view = self._make_chart(key)
            self._value_labels[key] = title
            layout.addWidget(title)
            layout.addWidget(view)

        layout.addStretch(1)

    def _make_chart(self, key):
        color = QColor(COLORS[key])

        title = QLabel(f"{TITLES[key]}: — {UNITS[key]}")
        title.setObjectName("chartTitle")

        series = QLineSeries()
        pen = series.pen()
        pen.setWidth(2)
        pen.setColor(color)
        series.setPen(pen)

        chart = QChart()
        chart.addSeries(series)
        chart.legend().hide()
        chart.setBackgroundRoundness(0)

        x_axis = QValueAxis()
        x_axis.setRange(0, MAX_POINTS)
        x_axis.setLabelFormat("%d")
        x_axis.setTitleText("sample")
        y_axis = QValueAxis()
        y_axis.setRange(0, 10)
        y_axis.setLabelFormat("%.0f")

        chart.addAxis(x_axis, Qt.AlignBottom)
        chart.addAxis(y_axis, Qt.AlignLeft)
        series.attachAxis(x_axis)
        series.attachAxis(y_axis)

        view = QChartView(chart)
        view.setRenderHint(QPainter.Antialiasing)
        view.setMinimumHeight(170)

        self._charts[key] = (view, chart, series, y_axis)
        return title, view

    # -- polling --------------------------------------------------------------
    def _poll(self):
        self.client.request("get_status")

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()
        self._poll()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    # -- response handling ------------------------------------------------------
    def _on_response(self, response: dict):
        if response.get("_cmd") != "get_status":
            return
        if not response.get("ok"):
            return
        data = response.get("data", {})
        self._append("clock", data.get("current_ratio") * 0.1
                     if data.get("current_ratio") is not None else None)
        self._append("temp", data.get("package_temp_c"))
        self._append("power", data.get("power_watts"))

    def _append(self, key, value):
        if value is None:
            return
        buf = self._buffers[key]
        buf.append(float(value))
        if len(buf) > MAX_POINTS:
            del buf[0]

        view, chart, series, y_axis = self._charts[key]
        series.clear()
        for i, v in enumerate(buf):
            series.append(i, v)

        if buf:
            low, high = min(buf), max(buf)
            span = high - low
            pad = span * 0.15 if span > 0 else 1.0
            y_axis.setRange(low - pad, high + pad)
            self._value_labels[key].setText(
                f"{TITLES[key]}: {buf[-1]:.1f} {UNITS[key]}"
            )
