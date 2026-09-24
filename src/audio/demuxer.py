"""
In-Memory Audio Demuxer & Continuous Stream Accumulator for OpenSub Live.
Decodes fragmented WebM/Opus and fMP4/AAC byte streams into 16kHz Mono Float32 NumPy arrays.
Maintains continuous stream continuity across slices to eliminate gaps and dropped audio.
"""
import io
import subprocess
import logging
import threading
from typing import Optional, List, Tuple
import numpy as np

logger = logging.getLogger("OpenSub.Demuxer")

try:
    import av
    HAS_PYAV = True
except ImportError:
    HAS_PYAV = False
    logger.warning("[Demuxer] PyAV not found. Will use FFmpeg subprocess fallback.")


class StreamSession:
    """Maintains a continuous byte stream session for an active browser media stream."""
    def __init__(self, stream_id: str, init_hdr: bytes, target_sample_rate: int = 16000):
        self.stream_id = stream_id
        self.init_hdr = init_hdr
        self.target_sample_rate = target_sample_rate
        self.buffer = bytearray(init_hdr)
        self.last_decoded_pts: float = -1.0
        self.pending_pcm: List[np.ndarray] = []
        self.pending_start_time: Optional[float] = None
        self.lock = threading.Lock()

    def reset(self, new_init: Optional[bytes] = None):
        with self.lock:
            if new_init:
                self.init_hdr = new_init
            self.buffer = bytearray(self.init_hdr)
            self.last_decoded_pts = -1.0
            self.pending_pcm.clear()
            self.pending_start_time = None

    def feed_bytes(self, raw_bytes: bytes, fallback_time: float) -> List[Tuple[np.ndarray, float]]:
        """Appends new bytes to stream buffer and decodes any newly arrived complete packets."""
        with self.lock:
            self.buffer.extend(raw_bytes)

            # Prevent unbounded memory growth by trimming older decoded data beyond 4MB
            if len(self.buffer) > 4 * 1024 * 1024:
                # Keep init header + last 1MB of clusters
                self.buffer = bytearray(self.init_hdr + bytes(self.buffer[-1024 * 1024:]))

            results: List[Tuple[np.ndarray, float]] = []
            if not HAS_PYAV:
                return results

            try:
                container = av.open(io.BytesIO(self.buffer))
                audio_stream = next((s for s in container.streams if s.type == "audio"), None)
                if not audio_stream:
                    return results

                resampler = av.AudioResampler(
                    format="flt",
                    layout="mono",
                    rate=self.target_sample_rate,
                )

                time_base = audio_stream.time_base
                new_frames: List[np.ndarray] = []

                for packet in container.demux(audio_stream):
                    if packet.size == 0:
                        continue

                    # Determine packet timestamp in seconds
                    if packet.pts is not None and time_base:
                        pts_sec = float(packet.pts * time_base)
                    else:
                        pts_sec = None

                    # If this packet was already decoded previously, skip it
                    if pts_sec is not None and pts_sec <= self.last_decoded_pts + 0.005:
                        continue

                    # Record start time for this batch of audio
                    if self.pending_start_time is None:
                        if pts_sec is not None and pts_sec > 0.05:
                            self.pending_start_time = pts_sec
                        else:
                            self.pending_start_time = fallback_time

                    # Decode packet into frames
                    try:
                        frames = packet.decode()
                    except Exception:
                        try:
                            frames = audio_stream.codec_context.decode(packet)
                        except Exception:
                            frames = []

                    for frame in frames:
                        for rf in resampler.resample(frame):
                            new_frames.append(rf.to_ndarray().flatten())

                    if pts_sec is not None:
                        self.last_decoded_pts = max(self.last_decoded_pts, pts_sec)

                if new_frames:
                    self.pending_pcm.append(np.concatenate(new_frames).astype(np.float32))

                # Check accumulated audio length
                total_samples = sum(len(a) for a in self.pending_pcm)
                pending_dur = total_samples / self.target_sample_rate

                # Emit when we have accumulated >= 1.5s of speech
                if pending_dur >= 1.5 and self.pending_start_time is not None:
                    merged = np.concatenate(self.pending_pcm).astype(np.float32)
                    max_val = np.max(np.abs(merged)) if len(merged) > 0 else 0
                    if max_val > 1.0:
                        merged = merged / max_val
                    results.append((merged, self.pending_start_time))
                    self.pending_pcm.clear()
                    self.pending_start_time = None

            except Exception as e:
                logger.debug(f"[Demuxer] Stream parse slice: {e}")

            return results

    def flush_pending(self) -> List[Tuple[np.ndarray, float]]:
        """Flushes any remaining pending audio samples."""
        with self.lock:
            results = []
            if self.pending_pcm and self.pending_start_time is not None:
                merged = np.concatenate(self.pending_pcm).astype(np.float32)
                if len(merged) >= int(self.target_sample_rate * 0.4):  # At least 400ms
                    max_val = np.max(np.abs(merged)) if len(merged) > 0 else 0
                    if max_val > 1.0:
                        merged = merged / max_val
                    results.append((merged, self.pending_start_time))
                self.pending_pcm.clear()
                self.pending_start_time = None
            return results


class AudioDemuxer:
    def __init__(self, target_sample_rate: int = 16000):
        self.target_sample_rate = target_sample_rate
        self.init_segments: dict[str, bytes] = {}  # stream_id -> init_header_bytes
        self.sessions: dict[str, StreamSession] = {}
        self.latest_init: Optional[bytes] = None

    def register_init_segment(self, stream_id: str, init_bytes: bytes):
        """Stores initialization segment and initializes stream session."""
        self.init_segments[stream_id] = init_bytes
        self.latest_init = init_bytes
        if stream_id in self.sessions:
            self.sessions[stream_id].reset(init_bytes)
        else:
            self.sessions[stream_id] = StreamSession(stream_id, init_bytes, self.target_sample_rate)
        logger.info(f"[Demuxer] Registered INIT segment for '{stream_id}' ({len(init_bytes)} bytes)")

    def reset_stream(self, stream_id: str):
        """Resets the continuous stream buffer on seeks or track changes."""
        if stream_id in self.sessions:
            self.sessions[stream_id].reset()

    def clear_all(self):
        """Clears all sessions and cached init segments on new video navigation."""
        self.sessions.clear()
        self.init_segments.clear()
        self.latest_init = None

    def feed_chunk(self, raw_bytes: bytes, stream_id: str = "default", fallback_start_time: float = 0.0) -> List[Tuple[np.ndarray, float]]:
        """
        Feeds incoming raw chunk to continuous stream session.
        Returns a list of (pcm_array, start_time) tuples ready for transcription.
        """
        if not raw_bytes or len(raw_bytes) < 10:
            return []

        # Get or create stream session
        session = self.sessions.get(stream_id)
        if not session:
            init_hdr = self.init_segments.get(stream_id, self.latest_init)
            if init_hdr:
                session = StreamSession(stream_id, init_hdr, self.target_sample_rate)
                self.sessions[stream_id] = session

        if session:
            return session.feed_bytes(raw_bytes, fallback_start_time)

        # Fallback to standalone chunk decoding if no session exists yet
        pcm, pts = self.decode_standalone_chunk(raw_bytes, stream_id)
        if pcm is not None and len(pcm) > 0:
            actual_start = pts if (pts is not None and pts > 0.05) else fallback_start_time
            return [(pcm, actual_start)]

        return []

    def decode_standalone_chunk(self, raw_bytes: bytes, stream_id: str = "default") -> Tuple[Optional[np.ndarray], Optional[float]]:
        """Direct standalone chunk decoding fallback."""
        init_hdr = self.init_segments.get(stream_id, self.latest_init)
        if init_hdr and HAS_PYAV:
            combined = init_hdr + raw_bytes
            pcm, pts = self._decode_with_pyav(combined)
            if pcm is not None:
                return pcm, pts

        pcm_pipe = self._decode_with_ffmpeg_pipe(raw_bytes, init_hdr)
        if pcm_pipe is not None and len(pcm_pipe) > 0:
            return pcm_pipe, None

        return None, None

    def _decode_with_pyav(self, data: bytes) -> Tuple[Optional[np.ndarray], Optional[float]]:
        try:
            container = av.open(io.BytesIO(data))
            audio_stream = next((s for s in container.streams if s.type == "audio"), None)
            if not audio_stream:
                return None, None

            resampler = av.AudioResampler(
                format="flt",
                layout="mono",
                rate=self.target_sample_rate,
            )

            frames_data = []
            exact_start_pts = None

            for packet in container.demux(audio_stream):
                if packet.size == 0:
                    continue
                if exact_start_pts is None and packet.pts is not None and audio_stream.time_base:
                    pts_sec = float(packet.pts * audio_stream.time_base)
                    if pts_sec > 0.05:
                        exact_start_pts = pts_sec
                try:
                    for frame in packet.decode():
                        for rf in resampler.resample(frame):
                            frames_data.append(rf.to_ndarray().flatten())
                except Exception:
                    continue

            try:
                for rf in resampler.resample(None):
                    frames_data.append(rf.to_ndarray().flatten())
            except Exception:
                pass

            if not frames_data:
                return None, exact_start_pts

            pcm = np.concatenate(frames_data).astype(np.float32)
            max_val = np.max(np.abs(pcm)) if len(pcm) > 0 else 0
            if max_val > 1.0:
                pcm = pcm / max_val
            return pcm, exact_start_pts
        except Exception:
            return None, None

    def _decode_with_ffmpeg_pipe(self, data: bytes, init_hdr: Optional[bytes] = None) -> Optional[np.ndarray]:
        try:
            payload = (init_hdr + data) if init_hdr else data
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-fflags", "+genpts",
                "-i", "pipe:0",
                "-f", "f32le",
                "-ar", str(self.target_sample_rate),
                "-ac", "1",
                "pipe:1"
            ]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            out, err = proc.communicate(input=payload, timeout=3.0)
            if proc.returncode == 0 and out:
                pcm = np.frombuffer(out, dtype=np.float32)
                return pcm
        except Exception as e:
            logger.debug(f"[Demuxer] FFmpeg pipe fallback failed: {e}")
        return None
