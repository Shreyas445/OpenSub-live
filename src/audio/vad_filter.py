"""
Voice Activity Detection (VAD) & Adaptive Clause Segmenter.
Buffers continuous 16kHz audio from WASAPI and emits clean, low-latency speech clauses
when natural pauses (180ms) or maximum clause durations (1.4s) are reached.
"""
import logging
from typing import Optional, List
import numpy as np

logger = logging.getLogger("OpenSub.VAD")


class SpeechClauseSegmenter:
    """
    Accepts arbitrary chunks of 16kHz float32 audio.
    Segments speech into short, low-latency clauses (1.2 - 1.5s) for near-instant transcription.
    """
    def __init__(
        self,
        sample_rate: int = 16000,
        energy_threshold: float = 0.003,  # Sensitive enough for quiet laptop audio
        min_speech_ms: int = 250,         # Minimum speech to consider valid (0.25s)
        silence_pause_ms: int = 180,      # Natural breath pause limit (0.18s)
        max_clause_sec: float = 1.4       # Fast clause flush (1.4s max for ultra-low latency)
    ):
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.min_speech_samples = int(sample_rate * min_speech_ms / 1000)
        self.silence_limit_samples = int(sample_rate * silence_pause_ms / 1000)
        self.max_clause_samples = int(sample_rate * max_clause_sec)

        self._in_speech = False
        self._speech_buffer: List[np.ndarray] = []
        self._silence_samples = 0
        self._total_speech_samples = 0

    def feed_audio(self, pcm_chunk: np.ndarray) -> List[np.ndarray]:
        """
        Feeds arbitrary length 16kHz float32 PCM array.
        Returns a list of completed speech clause arrays (if any).
        """
        if pcm_chunk is None or len(pcm_chunk) == 0:
            return []

        clauses = []
        frame_size = 512  # 32ms frame at 16kHz

        for i in range(0, len(pcm_chunk), frame_size):
            frame = pcm_chunk[i:i + frame_size]
            if len(frame) < 128:
                continue

            # Compute RMS energy of this frame
            rms = float(np.sqrt(np.mean(frame ** 2)))
            is_voice = rms >= self.energy_threshold

            if is_voice:
                self._in_speech = True
                self._silence_samples = 0
                self._speech_buffer.append(frame)
                self._total_speech_samples += len(frame)

                # Force flush if clause reached maximum duration
                if self._total_speech_samples >= self.max_clause_samples:
                    clause = self._flush()
                    if clause is not None:
                        clauses.append(clause)
            else:
                if self._in_speech:
                    self._speech_buffer.append(frame)
                    self._silence_samples += len(frame)
                    self._total_speech_samples += len(frame)

                    # If silence reached the pause limit, end clause
                    if self._silence_samples >= self.silence_limit_samples:
                        clause = self._flush()
                        if clause is not None:
                            clauses.append(clause)

        return clauses

    def _flush(self) -> Optional[np.ndarray]:
        if not self._speech_buffer:
            return None

        combined = np.concatenate(self._speech_buffer).astype(np.float32)
        self._speech_buffer = []
        self._in_speech = False
        self._silence_samples = 0
        self._total_speech_samples = 0

        # Only emit if it contains enough speech
        if len(combined) >= self.min_speech_samples:
            return combined
        return None
