"""
Background ASR (Automatic Speech Recognition) Worker for OpenSub Live.
Loads Faster-Whisper (CTranslate2) and transcribes/translates audio chunks asynchronously.
Supports automatic DLL path detection and instant seamless fallback from CUDA to CPU.
"""
import logging
import os
import queue
import site
import threading
import time
from typing import Callable, Optional
import numpy as np

from src.config import CONFIG
from src.engine.timeline_queue import TimelineQueue

logger = logging.getLogger("OpenSub.ASR")

# Auto-add pip-installed NVIDIA CUDA / cuDNN libraries to Windows DLL search path
try:
    for sp in site.getsitepackages():
        cublas_bin = os.path.join(sp, "nvidia", "cublas", "bin")
        cudnn_bin = os.path.join(sp, "nvidia", "cudnn", "bin")
        if os.path.isdir(cublas_bin):
            os.environ["PATH"] = cublas_bin + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(cublas_bin)
                except Exception:
                    pass
        if os.path.isdir(cudnn_bin):
            os.environ["PATH"] = cudnn_bin + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(cudnn_bin)
                except Exception:
                    pass
except Exception:
    pass

try:
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False
    logger.warning("[ASR] faster-whisper not installed. Real-time transcription will be mocked.")


class ASRWorker:
    def __init__(self, timeline_queue: TimelineQueue, on_subtitle_ready: Optional[Callable[[str], None]] = None):
        self.timeline_queue = timeline_queue
        self.on_subtitle_ready = on_subtitle_ready
        self.model: Optional[WhisperModel] = None
        self.is_running = False
        self.current_device = "CPU"
        self.task_queue = queue.Queue(maxsize=100)
        self._thread: Optional[threading.Thread] = None
        self.is_model_loaded = False

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="ASR-Worker")
        self._thread.start()

    def stop(self):
        self.is_running = False
        self.task_queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def enqueue_chunk(self, pcm_audio: np.ndarray, timestamp_offset: float = 0.0, is_live: bool = False, stream_id: str = "default"):
        """Submits an audio chunk (16kHz float32) for background transcription."""
        if not self.is_running or pcm_audio is None or len(pcm_audio) < 1600:
            return
        try:
            self.task_queue.put_nowait({
                "audio": pcm_audio,
                "offset": timestamp_offset,
                "is_live": is_live,
                "stream_id": stream_id
            })
        except queue.Full:
            logger.warning("[ASR] Worker queue full, dropping oldest chunk.")

    def _load_model(self):
        if not HAS_FASTER_WHISPER:
            self.is_model_loaded = True
            return

        device = CONFIG.device
        compute_type = CONFIG.compute_type

        # Check CUDA availability
        can_try_cuda = False
        if device in ("auto", "cuda"):
            try:
                import torch
                can_try_cuda = torch.cuda.is_available()
            except ImportError:
                can_try_cuda = False

        if can_try_cuda:
            c_type = "float16" if compute_type in ("auto", "float16") else compute_type
            logger.info(f"[ASR] Attempting to load Whisper '{CONFIG.model_size}' on CUDA ({c_type})...")
            try:
                self.model = WhisperModel(
                    CONFIG.model_size,
                    device="cuda",
                    compute_type=c_type,
                    cpu_threads=4
                )
                # Verify that CUDA inference actually works (catches missing cublas64_12.dll)
                dummy = np.zeros(3200, dtype=np.float32)
                list(self.model.transcribe(dummy, beam_size=1))
                self.is_model_loaded = True
                self.current_device = "CUDA"
                logger.info("[ASR] CUDA verified and ready for real-time transcription!")
                return
            except Exception as e:
                logger.warning(f"[ASR] CUDA acceleration unavailable ({e}). Automatically falling back to CPU (INT8)...")

        # Fallback to CPU INT8 (Fast & 100% reliable on all PCs)
        logger.info(f"[ASR] Loading Whisper model '{CONFIG.model_size}' on CPU (int8)...")
        try:
            self.model = WhisperModel(
                CONFIG.model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=4
            )
            self.is_model_loaded = True
            self.current_device = "CPU"
            logger.info("[ASR] CPU model loaded and ready.")
        except Exception as e2:
            logger.error(f"[ASR] Failed to load Whisper on CPU: {e2}")

    def _worker_loop(self):
        self._load_model()

        while self.is_running:
            try:
                item = self.task_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                break

            audio = item["audio"]
            offset = item["offset"]
            is_live = item["is_live"]

            try:
                if self.model is not None:
                    try:
                        segments, info = self.model.transcribe(
                            audio,
                            beam_size=CONFIG.beam_size,
                            task=CONFIG.task,
                            language=CONFIG.language,
                            vad_filter=not is_live,
                            word_timestamps=True
                        )
                        segments = list(segments)
                    except RuntimeError as e:
                        # If CUDA fails dynamically during inference, fallback to CPU immediately
                        if "cublas" in str(e).lower() or "cuda" in str(e).lower():
                            logger.warning(f"[ASR] CUDA runtime error ({e}). Switching to CPU INT8...")
                            self.model = WhisperModel(CONFIG.model_size, device="cpu", compute_type="int8", cpu_threads=4)
                            self.current_device = "CPU"
                            segments, info = self.model.transcribe(
                                audio,
                                beam_size=CONFIG.beam_size,
                                task=CONFIG.task,
                                language=CONFIG.language,
                                vad_filter=not is_live,
                                word_timestamps=True
                            )
                            segments = list(segments)
                        else:
                            raise e

                    collected_text = []
                    for seg in segments:
                        text = seg.text.strip()
                        if not text:
                            continue

                        collected_text.append(text)
                        start_sec = offset + seg.start
                        end_sec = offset + seg.end

                        self.timeline_queue.add_cue(
                            start_sec=start_sec,
                            end_sec=end_sec,
                            text=text,
                            language=info.language if info else "en",
                            is_live=is_live
                        )

                    full_text = " ".join(collected_text).strip()
                    if full_text:
                        logger.info(f"[ASR] Subtitle >> '{full_text}'")
                        if is_live and self.on_subtitle_ready:
                            self.on_subtitle_ready(full_text)

                elif not HAS_FASTER_WHISPER:
                    mock_text = "[OpenSub Live] Audio detected. Install faster-whisper to transcribe."
                    self.timeline_queue.add_cue(offset, offset + 2.5, mock_text, is_live=is_live)
                    if is_live and self.on_subtitle_ready:
                        self.on_subtitle_ready(mock_text)

            except Exception as e:
                logger.error(f"[ASR] Error during transcription: {e}", exc_info=True)
            finally:
                self.task_queue.task_done()
