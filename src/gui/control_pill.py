"""
Floating Control Pill for OpenSub Live.
Appears smoothly on hover to allow quick control:
Dragging, Click-Through Lock, Font Size, Color Toggle, Settings, Close.
"""
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFrame
)
from src.config import CONFIG


class ControlPill(QFrame):
    lock_toggled = Signal(bool)
    font_size_changed = Signal(int)
    color_toggled = Signal()
    settings_requested = Signal()
    close_requested = Signal()
    minimize_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ControlPill")
        self.drag_start_pos: QPoint | None = None
        self.setStyleSheet("""
            #ControlPill {
                background-color: rgba(20, 20, 25, 220);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 17px;
            }
            QPushButton {
                background: transparent;
                color: #E0E0E0;
                border: none;
                font-size: 13px;
                font-weight: bold;
                padding: 4px 8px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.20);
                color: #FFFFFF;
            }
            QLabel {
                color: #A0A0A0;
                font-size: 11px;
                font-weight: 500;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 3, 12, 3)
        layout.setSpacing(6)

        # Status Dot
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #00E676; font-size: 10px;")
        layout.addWidget(self.status_dot)

        # Status Text
        self.status_label = QLabel("OpenSub")
        layout.addWidget(self.status_label)

        # Draggable Handle (Using QLabel so it doesn't absorb mouse drag)
        self.drag_handle = QLabel("  ⠿ Drag  ")
        self.drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self.drag_handle.setStyleSheet("""
            color: #EAEAEA;
            font-size: 12px;
            font-weight: bold;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 6px;
            padding: 3px 6px;
        """)
        layout.addWidget(self.drag_handle)

        # Click-Through Lock Button
        self.lock_btn = QPushButton("🔓")
        self.lock_btn.setToolTip("Lock / Click-Through (Hotkey: Ctrl+Shift+L)")
        self.lock_btn.clicked.connect(self._on_lock_click)
        layout.addWidget(self.lock_btn)

        # Font Size Controls
        self.font_down_btn = QPushButton("A-")
        self.font_down_btn.setToolTip("Decrease Font Size")
        self.font_down_btn.clicked.connect(self._font_down)
        layout.addWidget(self.font_down_btn)

        self.font_up_btn = QPushButton("A+")
        self.font_up_btn.setToolTip("Increase Font Size")
        self.font_up_btn.clicked.connect(self._font_up)
        layout.addWidget(self.font_up_btn)

        # Color Switch (White / Cinema Yellow)
        self.color_btn = QPushButton("🟡" if CONFIG.text_color == "#FFFFFF" else "⚪")
        self.color_btn.setToolTip("Toggle Subtitle Color (White / Cinema Yellow)")
        self.color_btn.clicked.connect(self._on_color_click)
        layout.addWidget(self.color_btn)

        # Settings Gear
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_btn)

        # Minimize
        self.min_btn = QPushButton("−")
        self.min_btn.clicked.connect(self.minimize_requested.emit)
        layout.addWidget(self.min_btn)

        # Close
        self.close_btn = QPushButton("✕")
        self.close_btn.setStyleSheet("QPushButton:hover { background-color: #E53935; color: white; }")
        self.close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.close_btn)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_start_pos is not None:
            current_pos = event.globalPosition().toPoint()
            delta = current_pos - self.drag_start_pos
            self.drag_start_pos = current_pos

            win = self.window()
            if hasattr(win, "move_both"):
                win.move_both(delta.x(), delta.y())
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_start_pos = None
        event.accept()

    def set_status(self, is_connected: bool, message: str):
        if is_connected:
            self.status_dot.setStyleSheet("color: #00E676; font-size: 10px;")
        else:
            self.status_dot.setStyleSheet("color: #29B6F6; font-size: 10px;")
        self.status_label.setText(message)

    def _on_lock_click(self):
        CONFIG.is_locked = not CONFIG.is_locked
        self.update_lock_state(CONFIG.is_locked)
        self.lock_toggled.emit(CONFIG.is_locked)

    def update_lock_state(self, is_locked: bool):
        if is_locked:
            self.lock_btn.setText("🔒")
            self.lock_btn.setStyleSheet("color: #FFB300;")
            self.lock_btn.setToolTip("Subtitles Locked (Click-Through Active). Click here to unlock or press Ctrl+Shift+L")
        else:
            self.lock_btn.setText("🔓")
            self.lock_btn.setStyleSheet("")
            self.lock_btn.setToolTip("Subtitles Unlocked. Click to lock / pass clicks through")

    def _font_down(self):
        if CONFIG.font_size > 14:
            CONFIG.font_size -= 2
            CONFIG.save()
            self.font_size_changed.emit(CONFIG.font_size)

    def _font_up(self):
        if CONFIG.font_size < 54:
            CONFIG.font_size += 2
            CONFIG.save()
            self.font_size_changed.emit(CONFIG.font_size)

    def _on_color_click(self):
        if CONFIG.text_color == "#FFFFFF":
            CONFIG.text_color = CONFIG.alt_text_color
            self.color_btn.setText("⚪")
        else:
            CONFIG.text_color = "#FFFFFF"
            self.color_btn.setText("🟡")
        CONFIG.save()
        self.color_toggled.emit()
