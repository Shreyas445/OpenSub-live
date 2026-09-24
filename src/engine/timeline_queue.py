"""
Timeline Subtitle Queue for OpenSub Live.
Thread-safe interval queue that stores timestamped subtitle cues and
returns the active subtitle for any given playback time in O(log N).
Also handles intelligent line-wrapping for cinema subtitle aesthetics.
"""
from dataclasses import dataclass
import threading
import bisect
from typing import Optional, List


@dataclass
class SubtitleCue:
    start_sec: float
    end_sec: float
    text: str
    original_text: str = ""
    language: str = "en"
    is_live: bool = False  # True for unbuffered live calls

    def is_active(self, current_time: float) -> bool:
        return self.start_sec <= current_time <= self.end_sec


def format_cinema_lines(text: str, max_chars_per_line: int = 42) -> str:
    """
    Wraps text into 1 or 2 clean cinema subtitle lines.
    Prefers breaking at punctuation (, . ! ? -) or word boundaries.
    """
    text = text.strip()
    if len(text) <= max_chars_per_line:
        return text

    words = text.split()
    lines = []
    current_line = []
    current_len = 0

    for word in words:
        word_len = len(word)
        if current_len + word_len + (1 if current_line else 0) <= max_chars_per_line:
            current_line.append(word)
            current_len += word_len + (1 if len(current_line) > 1 else 0)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_len = word_len

    if current_line:
        lines.append(" ".join(current_line))

    # Keep at most 2 lines
    if len(lines) > 2:
        return lines[0] + "\n" + " ".join(lines[1:])
    return "\n".join(lines)


class TimelineQueue:
    def __init__(self, max_chars_per_line: int = 42, min_display_sec: float = 1.6):
        self.max_chars_per_line = max_chars_per_line
        self.min_display_sec = min_display_sec
        self._cues: List[SubtitleCue] = []
        self._starts: List[float] = []
        self._lock = threading.Lock()
        self.last_sync_time: float = 0.0

    def add_cue(self, start_sec: float, end_sec: float, text: str, original_text: str = "", language: str = "en", is_live: bool = False):
        """Adds or updates a timestamped subtitle cue with cinema reading pacing."""
        formatted_text = format_cinema_lines(text, self.max_chars_per_line)
        if not formatted_text:
            return

        # Natural reading duration (approx 200-350ms per word, bounded)
        word_count = len(text.split())
        reading_dur = max(self.min_display_sec, min(4.8, word_count * 0.36))
        if end_sec - start_sec < reading_dur:
            end_sec = start_sec + reading_dur

        cue = SubtitleCue(
            start_sec=start_sec,
            end_sec=end_sec,
            text=formatted_text,
            original_text=original_text,
            language=language,
            is_live=is_live
        )

        with self._lock:
            # Check for duplicate exact text near the same timestamp
            idx = bisect.bisect_right(self._starts, start_sec)
            if idx > 0 and idx - 1 < len(self._cues):
                prev = self._cues[idx - 1]
                if abs(prev.start_sec - start_sec) < 0.6 and prev.text == formatted_text:
                    return

            self._cues.insert(idx, cue)
            self._starts.insert(idx, start_sec)

            # Keep last 1500 cues (~1.5 hours of subtitles)
            if len(self._cues) > 1500:
                self._cues.pop(0)
                self._starts.pop(0)

    def get_cue_at(self, current_time: float) -> Optional[SubtitleCue]:
        """Returns the active SubtitleCue for current_time, or upcoming cue within 200ms lead-in."""
        with self._lock:
            if not self._cues:
                return None

            # Binary search for cue starting before or at current_time
            idx = bisect.bisect_right(self._starts, current_time) - 1
            if idx >= 0 and idx < len(self._cues):
                cue = self._cues[idx]
                if cue.is_active(current_time):
                    return cue

            # Check next upcoming cue: if current_time is within 220ms before speech starts,
            # display it immediately (movie/Netflix predictive lead-in for human eye reading)
            next_idx = max(0, idx + 1)
            if next_idx < len(self._cues):
                next_cue = self._cues[next_idx]
                if next_cue.is_active(current_time) or (0 <= next_cue.start_sec - current_time <= 0.22):
                    return next_cue

            return None

    def handle_seek(self, new_time: float):
        """Called when a video seek / jump occurs. Preserves pre-computed cues so rewinding works seamlessly."""
        with self._lock:
            self.last_sync_time = new_time

    def clear(self):
        with self._lock:
            self._cues.clear()
            self._starts.clear()
