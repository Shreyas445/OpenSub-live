"""
Settings Dialog for OpenSub Live.
Allows customizing speech models, live translation, typography, and overlay appearance.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QSlider,
    QCheckBox,
    QPushButton,
    QGroupBox,
    QFormLayout
)
from src.config import CONFIG


class SettingsDialog(QDialog):
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenSub Live - Settings")
        self.setFixedWidth(440)
        self.setStyleSheet("""
            QDialog {
                background-color: #1A1A22;
                color: #EEEEEE;
            }
            QLabel {
                color: #DCDCDC;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #333340;
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 14px;
                font-weight: bold;
                color: #00E676;
            }
            QComboBox {
                background-color: #262632;
                border: 1px solid #444455;
                border-radius: 4px;
                padding: 4px 8px;
                color: white;
            }
            QPushButton {
                background-color: #2962FF;
                border: none;
                border-radius: 6px;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #1E88E5;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #333344;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #00E676;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # 1. AI & Translation Settings
        ai_group = QGroupBox("Speech AI & Live Translation")
        ai_form = QFormLayout(ai_group)
        ai_form.setSpacing(10)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["tiny", "base", "small", "medium"])
        self.model_combo.setCurrentText(CONFIG.model_size)
        ai_form.addRow("Whisper Model:", self.model_combo)

        self.task_combo = QComboBox()
        self.task_combo.addItem("Translate to English (Live)", "translate")
        self.task_combo.addItem("Transcribe Original Language", "transcribe")
        idx = 0 if CONFIG.task == "translate" else 1
        self.task_combo.setCurrentIndex(idx)
        ai_form.addRow("Mode:", self.task_combo)

        self.device_combo = QComboBox()
        self.device_combo.addItems(["auto", "cuda", "cpu"])
        self.device_combo.setCurrentText(CONFIG.device)
        ai_form.addRow("Hardware Acceleration:", self.device_combo)

        layout.addWidget(ai_group)

        # 2. Appearance & Subtitle Typography
        ui_group = QGroupBox("Cinema Typography & Styling")
        ui_form = QFormLayout(ui_group)
        ui_form.setSpacing(10)

        self.font_combo = QComboBox()
        self.font_combo.addItems(["Segoe UI", "Arial", "Trebuchet MS", "Inter", "Roboto", "Montserrat"])
        self.font_combo.setCurrentText(CONFIG.font_family)
        ui_form.addRow("Font Family:", self.font_combo)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(16, 52)
        self.size_slider.setValue(CONFIG.font_size)
        self.size_val_label = QLabel(f"{CONFIG.font_size} px")
        self.size_slider.valueChanged.connect(lambda v: self.size_val_label.setText(f"{v} px"))
        size_layout = QHBoxLayout()
        size_layout.addWidget(self.size_slider)
        size_layout.addWidget(self.size_val_label)
        ui_form.addRow("Font Size:", size_layout)

        self.pill_check = QCheckBox("Show semi-transparent background pill")
        self.pill_check.setChecked(CONFIG.enable_pill_bg)
        ui_form.addRow(self.pill_check)

        layout.addWidget(ui_group)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = QPushButton("Save & Apply")
        self.save_btn.clicked.connect(self._save_settings)
        btn_layout.addWidget(self.save_btn)

        layout.addLayout(btn_layout)

    def _save_settings(self):
        CONFIG.model_size = self.model_combo.currentText()
        CONFIG.task = self.task_combo.currentData()
        CONFIG.device = self.device_combo.currentText()
        CONFIG.font_family = self.font_combo.currentText()
        CONFIG.font_size = self.size_slider.value()
        CONFIG.enable_pill_bg = self.pill_check.isChecked()
        CONFIG.save()
        self.settings_changed.emit()
        self.accept()
