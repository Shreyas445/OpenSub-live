"""
OpenSub Live - Main Application Entrypoint
Wires together the Cinema Overlay GUI, Ahead-of-Time WebSocket Server,
Faster-Whisper ASR Worker, and System WASAPI Loopback fallback.
"""
import sys
import signal
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal, QTimer

from src.config import CONFIG
from src.audio.demuxer import AudioDemuxer
from src.audio.vad_filter import SpeechClauseSegmenter
from src.audio.wasapi_loopback import WASAPILoopbackRecorder
from src.engine.asr_worker import ASRWorker
from src.engine.timeline_queue import TimelineQueue
from src.gui.overlay_window import OverlayWindow
from src.server.websocket_server import BridgeServer
from src.utils.hotkey_listener import GlobalHotkeyManager

# Setup clean console logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("OpenSub.Main")


class EventBridge(QObject):
    """Qt signal bridge for thread-safe cross-thread UI updates."""
    time_synced = Signal(float)
    status_changed = Signal(bool, str)
    live_subtitle_ready = Signal(str)
    toggle_lock_requested = Signal()


def main():
    logger.info("==========================================")
    logger.info("   Starting OpenSub Live Subtitle System  ")
    logger.info("==========================================")

    # Enable native Ctrl+C handling in Qt event loop
    signal.signal(signal.SIGINT, lambda *args: QApplication.quit())

    app = QApplication(sys.argv)
    app.setApplicationName("OpenSub Live")
    app.setQuitOnLastWindowClosed(True)

    # 150ms timer ensures Python interpreter gets execution slices to process OS signals (Ctrl+C)
    sig_timer = QTimer()
    sig_timer.timeout.connect(lambda: None)
    sig_timer.start(150)

    # 1. Thread-safe Signal Bridge
    bridge = EventBridge()

    # 2. Subtitle Timeline Queue
    timeline_queue = TimelineQueue(
        max_chars_per_line=CONFIG.max_chars_per_line,
        min_display_sec=CONFIG.min_display_time_sec
    )

    # 3. Background ASR Worker (Faster-Whisper)
    asr_worker = ASRWorker(
        timeline_queue=timeline_queue,
        on_subtitle_ready=lambda text: bridge.live_subtitle_ready.emit(text)
    )

    # 4. In-Memory PyAV Demuxer
    demuxer = AudioDemuxer(target_sample_rate=CONFIG.sample_rate)

    # 5. Local IPC WebSocket Server (ws://127.0.0.1:9876)
    server = BridgeServer(
        timeline_queue=timeline_queue,
        asr_worker=asr_worker,
        demuxer=demuxer,
        on_time_sync=lambda t: bridge.time_synced.emit(t),
        on_status_change=lambda conn, msg: bridge.status_changed.emit(conn, msg)
    )

    # 6. Cinema Overlay Window (Dual-Window Architecture)
    overlay = OverlayWindow(timeline_queue=timeline_queue)

    # Connect Qt Signals to Overlay methods
    bridge.time_synced.connect(overlay.set_playhead_time)
    bridge.status_changed.connect(overlay.set_status)
    bridge.live_subtitle_ready.connect(overlay.set_live_subtitle)
    bridge.toggle_lock_requested.connect(lambda: overlay.set_click_through(not CONFIG.is_locked))

    # 7. Low-Latency System Audio Capture (Fast 1.4s clause slicing)
    segmenter = SpeechClauseSegmenter(
        sample_rate=CONFIG.sample_rate,
        energy_threshold=0.0025,
        min_speech_ms=250,
        silence_pause_ms=180,
        max_clause_sec=1.4
    )

    def on_loopback_chunk(pcm_chunk):
        # Priority Scheduling: If web video lookahead is active, skip live loopback
        # transcription to eliminate 1-sentence lag and avoid overwriting pre-computed subtitles
        if server.has_active_connections:
            return

        clauses = segmenter.feed_audio(pcm_chunk)
        for clause in clauses:
            dur = len(clause) / CONFIG.sample_rate
            logger.info(f"[Main] Captured speech clause ({dur:.2f}s). Transcribing on {asr_worker.current_device}...")
            asr_worker.enqueue_chunk(clause, is_live=True)

    wasapi_recorder = WASAPILoopbackRecorder(
        on_audio_chunk=on_loopback_chunk,
        target_sample_rate=CONFIG.sample_rate
    )

    # 8. Global Hotkey (Ctrl+Shift+L)
    hotkey_mgr = GlobalHotkeyManager(
        on_toggle_lock=lambda: bridge.toggle_lock_requested.emit()
    )

    # Start Services
    asr_worker.start()
    server.start()
    wasapi_recorder.start()
    hotkey_mgr.start()

    overlay.show()
    logger.info("[Main] OpenSub Live is running. Subtitle overlay visible.")

    # Clean shutdown hook
    def on_shutdown():
        logger.info("[Main] Shutting down services cleanly...")
        try:
            wasapi_recorder.stop()
        except Exception:
            pass
        try:
            server.stop()
        except Exception:
            pass
        try:
            asr_worker.stop()
        except Exception:
            pass
        try:
            hotkey_mgr.stop()
        except Exception:
            pass
        try:
            CONFIG.save()
        except Exception:
            pass

    app.aboutToQuit.connect(on_shutdown)
    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        logger.info("[Main] Interrupted by user (Ctrl+C). Exiting.")
        on_shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
