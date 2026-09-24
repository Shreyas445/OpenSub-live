"""
WASAPI System Audio Loopback Recorder for OpenSub Live.
Captures 'what you hear' (laptop speakers/headphones) digitally on Windows.
Runs in a background thread and emits 16kHz Mono audio chunks.
"""
import logging
import threading
import time
from typing import Callable, Optional
import numpy as np

logger = logging.getLogger("OpenSub.WASAPI")

try:
    import pyaudiowpatch as pyaudio
    HAS_PYAUDIOWPATCH = True
except ImportError:
    HAS_PYAUDIOWPATCH = False
    logger.warning("[WASAPI] PyAudioWPatch not found. System loopback capture disabled.")


class WASAPILoopbackRecorder:
    def __init__(self, on_audio_chunk: Optional[Callable[[np.ndarray], None]] = None, target_sample_rate: int = 16000):
        self.on_audio_chunk = on_audio_chunk
        self.target_sample_rate = target_sample_rate
        self.is_running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if not HAS_PYAUDIOWPATCH:
            logger.error("[WASAPI] Cannot start: PyAudioWPatch is not installed.")
            return False

        if self.is_running:
            return True

        self.is_running = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True, name="WASAPI-Loopback")
        self._thread.start()
        return True

    def stop(self):
        self.is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("[WASAPI] Loopback recording stopped.")

    def _record_loop(self):
        try:
            with pyaudio.PyAudio() as p:
                wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

                if not default_speakers["isLoopbackDevice"]:
                    loopback_dev = None
                    for dev in p.get_loopback_device_info_generator():
                        if default_speakers["name"] in dev["name"]:
                            loopback_dev = dev
                            break
                    if loopback_dev is None:
                        loopback_dev = next(p.get_loopback_device_info_generator(), None)
                else:
                    loopback_dev = default_speakers

                if loopback_dev is None:
                    logger.error("[WASAPI] No loopback audio device found.")
                    return

                logger.info(f"[WASAPI] Capturing from loopback: {loopback_dev['name']}")

                native_rate = int(loopback_dev["defaultSampleRate"])
                channels = loopback_dev["maxInputChannels"]
                chunk_frames = 2048

                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=native_rate,
                    input=True,
                    input_device_index=loopback_dev["index"],
                    frames_per_buffer=chunk_frames
                )

                while self.is_running:
                    raw_data = stream.read(chunk_frames, exception_on_overflow=False)
                    if not raw_data:
                        continue

                    # Convert int16 bytes to numpy float32
                    pcm_int16 = np.frombuffer(raw_data, dtype=np.int16)
                    if channels > 1:
                        pcm_int16 = pcm_int16.reshape(-1, channels)
                        pcm_mono = pcm_int16.mean(axis=1)
                    else:
                        pcm_mono = pcm_int16

                    # Normalize to [-1.0, 1.0]
                    pcm_float = (pcm_mono / 32768.0).astype(np.float32)

                    # Resample to 16,000 Hz
                    if native_rate == 48000 and self.target_sample_rate == 16000:
                        pcm_resampled = pcm_float[::3]
                    elif native_rate != self.target_sample_rate:
                        num_samples = int(len(pcm_float) * self.target_sample_rate / native_rate)
                        indices = np.linspace(0, len(pcm_float) - 1, num_samples)
                        pcm_resampled = np.interp(indices, np.arange(len(pcm_float)), pcm_float).astype(np.float32)
                    else:
                        pcm_resampled = pcm_float

                    if self.on_audio_chunk and len(pcm_resampled) > 0:
                        self.on_audio_chunk(pcm_resampled)

                stream.stop_stream()
                stream.close()

        except Exception as e:
            logger.error(f"[WASAPI] Error in loopback thread: {e}", exc_info=True)
            self.is_running = False
