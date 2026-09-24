"""
Main Cinema Overlay Architecture for OpenSub Live.
Composed of two synchronized native windows:
1. PillBarWindow: Always clickable top toolbar (handles drag, lock toggle, font, close).
   NEVER click-through, ensuring you can ALWAYS click the 🔒 icon to unlock!
2. SubtitleOverlayWindow: Frameless, transparent cinema subtitle renderer underneath.
   Toggles WS_EX_TRANSPARENT so mouse clicks pass through subtitles directly to video.
"""
import ctypes
import logging
import time
from typing import Optional
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QGuiApplication

from src.config import CONFIG
from src.engine.timeline_queue import TimelineQueue
from src.gui.control_pill import ControlPill
from src.gui.subtitle_renderer import SubtitleRenderer
from src.gui.settings_dialog import SettingsDialog

logger = logging.getLogger("OpenSub.Overlay")

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20


class PillBarWindow(QWidget):
    """Floating toolbar window that remains permanently clickable even when subtitles are locked."""
    def __init__(self, overlay_window: "SubtitleOverlayWindow"):
        super().__init__()
        self.overlay = overlay_window
        self.drag_start: Optional[QPoint] = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pill = ControlPill(self)
        layout.addWidget(self.pill)

        # Connect toolbar signals
        self.pill.lock_toggled.connect(self.overlay.set_click_through)
        self.pill.font_size_changed.connect(lambda _: self.overlay.renderer.update())
        self.pill.color_toggled.connect(lambda: self.overlay.renderer.update())
        self.pill.settings_requested.connect(self._open_settings)
        self.pill.minimize_requested.connect(self.overlay.showMinimized)
        self.pill.close_requested.connect(self.overlay.close)

        self.adjustSize()

    def move_both(self, delta_x: int, delta_y: int):
        self.move(self.x() + delta_x, self.y() + delta_y)
        self.overlay.move(self.overlay.x() + delta_x, self.overlay.y() + delta_y)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_start is not None:
            curr = event.globalPosition().toPoint()
            delta = curr - self.drag_start
            self.drag_start = curr
            self.move_both(delta.x(), delta.y())
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_start = None
        event.accept()

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.settings_changed.connect(lambda: self.overlay.renderer.update())
        dialog.exec()


class SubtitleOverlayWindow(QWidget):
    """Transparent cinema subtitle display window."""
    def __init__(self, timeline_queue: TimelineQueue):
        super().__init__()
        self.timeline_queue = timeline_queue
        self.current_playhead: float = 0.0
        self.is_sync_mode: bool = False
        self.is_showing_live: bool = False
        self.drag_start: Optional[QPoint] = None

        self._setup_window_flags()
        self._init_ui()

        # Coordinated toolbar window
        self.pill_bar = PillBarWindow(self)
        self._position_bottom_center()

        # Live subtitle auto-clear timer
        self.live_clear_timer = QTimer(self)
        self.live_clear_timer.setSingleShot(True)
        self.live_clear_timer.timeout.connect(self._clear_live_subtitles)

        # Lookahead browser sync timer (30 FPS)
        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self._update_lookahead_subtitles)
        self.render_timer.start(33)

    def _setup_window_flags(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.SubWindow
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(CONFIG.window_width, 86)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.renderer = SubtitleRenderer(self)
        layout.addWidget(self.renderer)

    def _position_bottom_center(self):
        screen = QGuiApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = screen.height() - self.height() - CONFIG.bottom_margin
        self.move(x, y)

        pill_x = x + (self.width() - self.pill_bar.width()) // 2
        pill_y = y - self.pill_bar.height() - 4
        self.pill_bar.move(pill_x, pill_y)

    def show(self):
        super().show()
        self.pill_bar.show()

    def close(self):
        self.pill_bar.close()
        super().close()

    def set_playhead_time(self, current_time: float):
        self.current_playhead = current_time
        self._last_sync_wall_time = time.perf_counter()

    def set_paused(self, is_paused: bool):
        self.is_paused = is_paused

    def set_status(self, is_connected: bool, message: str):
        self.is_sync_mode = is_connected
        self.pill_bar.pill.set_status(is_connected, message)

    def set_live_subtitle(self, text: str):
        """Displays live subtitles with reading time retention."""
        if not text:
            return
        self.is_showing_live = True
        self.renderer.set_text(text)
        reading_ms = int(max(2600, len(text.split()) * 350))
        self.live_clear_timer.start(reading_ms)

    def _clear_live_subtitles(self):
        self.is_showing_live = False
        cue = self.timeline_queue.get_cue_at(self.current_playhead)
        if not cue:
            self.renderer.set_text("")

    def _update_lookahead_subtitles(self):
        """Checks timeline queue for pre-computed lookahead subtitles."""
        try:
            playhead = self.current_playhead
            if self.is_sync_mode and hasattr(self, "_last_sync_wall_time") and not getattr(self, "is_paused", False):
                elapsed = time.perf_counter() - self._last_sync_wall_time
                if 0 < elapsed < 1.0:
                    playhead += elapsed

            cue = self.timeline_queue.get_cue_at(playhead)
            if cue:
                self.is_showing_live = False
                self._current_cue_end = cue.end_sec
                self.renderer.set_text(cue.text)
            elif self.is_sync_mode and not self.is_showing_live:
                # Natural cinema reading hang-time (retains subtitle for 1.1s after sentence ends)
                # Clears immediately if playhead is past cue end or was rewound before cue
                cue_end = getattr(self, "_current_cue_end", 0.0)
                if playhead > cue_end + 1.1 or playhead < cue_end - 2.5:
                    self.renderer.set_text("")
                    self._current_cue_end = 0.0
        except Exception:
            pass

    def set_click_through(self, enabled: bool):
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            if enabled:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED)
                CONFIG.is_locked = True
                self.pill_bar.pill.update_lock_state(True)
                logger.info("[Overlay] Subtitles locked (Click-Through active). Toolbar remains interactive.")
            else:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT)
                CONFIG.is_locked = False
                self.pill_bar.pill.update_lock_state(False)
                logger.info("[Overlay] Subtitles unlocked. Full interactivity restored.")
        except Exception as e:
            logger.error(f"[Overlay] Failed to toggle click-through: {e}")

    def move_both(self, delta_x: int, delta_y: int):
        self.move(self.x() + delta_x, self.y() + delta_y)
        self.pill_bar.move(self.pill_bar.x() + delta_x, self.pill_bar.y() + delta_y)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not CONFIG.is_locked:
            self.drag_start = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_start is not None and not CONFIG.is_locked:
            curr = event.globalPosition().toPoint()
            delta = curr - self.drag_start
            self.drag_start = curr
            self.move_both(delta.x(), delta.y())
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_start = None
        event.accept()


# Backward compatibility
OverlayWindow = SubtitleOverlayWindow
