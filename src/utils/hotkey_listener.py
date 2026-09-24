"""
Global Hotkey Listener for OpenSub Live.
Allows toggling Click-Through (lock/unlock) using Ctrl+Shift+L from anywhere in Windows.
"""
import logging
import threading
from typing import Callable

logger = logging.getLogger("OpenSub.Hotkey")

try:
    from pynput import keyboard
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    logger.warning("[Hotkey] pynput not installed. Global hotkey disabled.")


class GlobalHotkeyManager:
    def __init__(self, on_toggle_lock: Callable[[], None], hotkey_str: str = "<ctrl>+<shift>+l"):
        self.on_toggle_lock = on_toggle_lock
        self.hotkey_str = hotkey_str
        self._listener = None
        self._thread = None

    def start(self):
        if not HAS_PYNPUT:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="GlobalHotkey")
        self._thread.start()

    def _run(self):
        try:
            with keyboard.GlobalHotKeys({
                self.hotkey_str: self.on_toggle_lock
            }) as self._listener:
                self._listener.join()
        except Exception as e:
            logger.debug(f"[Hotkey] Failed to register global hotkey: {e}")

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
