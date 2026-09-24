"""
Cinema Subtitle Renderer Widget.
Uses QPainterPath with RoundJoin outline strokes to render crisp,
high-contrast movie-style subtitles (white/yellow text with 2.8px black outline)
guaranteed to be 100% legible over any video background.
"""
from PySide6.QtCore import Qt, QPointF, QRectF, QPropertyAnimation, Property
from PySide6.QtGui import (
    QPainter,
    QPainterPath,
    QFont,
    QPen,
    QColor,
    QBrush,
    QFontMetrics
)
from PySide6.QtWidgets import QWidget
from src.config import CONFIG


class SubtitleRenderer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._text = ""
        self._opacity = 1.0

        # Animation for smooth crossfade
        self._anim = QPropertyAnimation(self, b"sub_opacity")
        self._anim.setDuration(CONFIG.fade_duration_ms)

    def get_sub_opacity(self) -> float:
        return self._opacity

    def set_sub_opacity(self, val: float):
        self._opacity = val
        self.update()

    sub_opacity = Property(float, get_sub_opacity, set_sub_opacity)

    def set_text(self, new_text: str):
        if self._text == new_text:
            return

        if not new_text:
            self._text = ""
            self.update()
            return

        self._text = new_text
        # Trigger quick fade-in
        self._anim.stop()
        self._anim.setStartValue(0.4)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self.update()

    def paintEvent(self, event):
        if not self._text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setOpacity(self._opacity)

        font = QFont(CONFIG.font_family, CONFIG.font_size)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        lines = self._text.split("\n")
        line_height = metrics.lineSpacing()
        total_height = len(lines) * line_height

        # Calculate max line width for the background pill
        max_line_width = max((metrics.horizontalAdvance(line) for line in lines), default=0)

        # Draw translucent background pill if enabled
        if CONFIG.enable_pill_bg and max_line_width > 0:
            pill_pad_x = 22
            pill_pad_y = 10
            pill_rect = QRectF(
                (self.width() - max_line_width) / 2.0 - pill_pad_x,
                (self.height() - total_height) / 2.0 - pill_pad_y,
                max_line_width + (pill_pad_x * 2),
                total_height + (pill_pad_y * 2)
            )
            # Subtle dark glass
            bg_brush = QBrush(QColor(0, 0, 0, 140))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg_brush)
            painter.drawRoundedRect(pill_rect, 14, 14)

        # Outline & Fill Setup
        stroke_pen = QPen(QColor(CONFIG.stroke_color))
        stroke_pen.setWidthF(CONFIG.stroke_width)
        stroke_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        stroke_pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        fill_brush = QBrush(QColor(CONFIG.text_color))

        # Vertical centering
        start_y = (self.height() - total_height) / 2.0 + metrics.ascent()

        for i, line in enumerate(lines):
            line_width = metrics.horizontalAdvance(line)
            start_x = (self.width() - line_width) / 2.0
            y_pos = start_y + (i * line_height)

            path = QPainterPath()
            path.addText(QPointF(start_x, y_pos), font, line)

            # Draw solid black outline first
            painter.strokePath(path, stroke_pen)
            # Draw crisp fill on top
            painter.fillPath(path, fill_brush)

        painter.end()
