"""
Async WebSocket Server for OpenSub Live.
Accepts connections from the browser extension / userscript at ws://127.0.0.1:9876.
Receives ahead-of-time audio chunks and continuous playhead synchronization timestamps.
"""
import asyncio
import base64
import json
import logging
import threading
from typing import Callable, Optional
import websockets

from src.audio.demuxer import AudioDemuxer
from src.config import CONFIG
from src.engine.asr_worker import ASRWorker
from src.engine.timeline_queue import TimelineQueue

logger = logging.getLogger("OpenSub.Server")
# Suppress noisy EOF/handshake drop warnings from transient connections or port probes
logging.getLogger("websockets.server").setLevel(logging.WARNING)


class BridgeServer:
    def __init__(
        self,
        timeline_queue: TimelineQueue,
        asr_worker: ASRWorker,
        demuxer: AudioDemuxer,
        on_time_sync: Optional[Callable[[float], None]] = None,
        on_status_change: Optional[Callable[[bool, str], None]] = None
    ):
        self.timeline_queue = timeline_queue
        self.asr_worker = asr_worker
        self.demuxer = demuxer
        self.on_time_sync = on_time_sync
        self.on_status_change = on_status_change

        self.current_playhead: float = 0.0
        self.is_paused: bool = False
        self.active_connections = set()
        self._server = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None

    @property
    def has_active_connections(self) -> bool:
        return len(self.active_connections) > 0

    def start(self):
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True, name="BridgeServer")
        self._thread.start()

    def stop(self):
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        logger.info(f"[Server] Starting WebSocket server on ws://{CONFIG.server_host}:{CONFIG.server_port}")
        try:
            self._server = await websockets.serve(
                self._handle_client,
                CONFIG.server_host,
                CONFIG.server_port,
                max_size=30 * 1024 * 1024  # 30MB max payload
            )
            await self._server.wait_closed()
        except Exception as e:
            logger.error(f"[Server] Failed to bind WebSocket server: {e}")

    async def _handle_client(self, websocket):
        self.active_connections.add(websocket)
        logger.info(f"[Server] Browser client connected from {websocket.remote_address}")
        if self.on_status_change:
            self.on_status_change(True, "Web Video Connected")

        try:
            async for message in websocket:
                if isinstance(message, str):
                    self._handle_json_message(message)
                elif isinstance(message, bytes):
                    # Direct binary fallback
                    pcm, pts = self.demuxer.decode_chunk(message)
                    if pcm is not None and len(pcm) > 0:
                        offset = pts if pts is not None else self.current_playhead
                        self.asr_worker.enqueue_chunk(pcm, timestamp_offset=offset, is_live=False)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.active_connections.discard(websocket)
            logger.info("[Server] Browser client disconnected")
            if self.on_status_change and len(self.active_connections) == 0:
                self.on_status_change(False, "Listening to System Audio")

    def _handle_json_message(self, text: str):
        try:
            msg = json.loads(text)
            msg_type = msg.get("type")

            if msg_type == "SYNC":
                time_pos = float(msg.get("time", 0.0))
                self.current_playhead = time_pos
                self.is_paused = bool(msg.get("paused", False))
                self.timeline_queue.handle_seek(time_pos)
                if self.on_time_sync:
                    self.on_time_sync(time_pos)

            elif msg_type == "INIT_SEGMENT":
                stream_id = msg.get("stream_id", "default")
                data_b64 = msg.get("data", "")
                if data_b64:
                    raw_bytes = base64.b64decode(data_b64)
                    self.demuxer.register_init_segment(stream_id, raw_bytes)

            elif msg_type == "ABORT_STREAM":
                stream_id = msg.get("stream_id", "default")
                self.demuxer.reset_stream(stream_id)
                logger.info(f"[Server] Stream reset (seek / abort) on '{stream_id}'")

            elif msg_type == "AUDIO_CHUNK":
                stream_id = msg.get("stream_id", "default")
                start_time = float(msg.get("start_time", self.current_playhead))
                data_b64 = msg.get("data", "")
                if data_b64:
                    raw_bytes = base64.b64decode(data_b64)
                    ready_blocks = self.demuxer.feed_chunk(raw_bytes, stream_id=stream_id, fallback_start_time=start_time)
                    for pcm, seg_start in ready_blocks:
                        dur = len(pcm) / CONFIG.sample_rate
                        lead_time = seg_start - self.current_playhead
                        logger.info(f"[Server] Continuous Audio Block Ready! Chunk at {seg_start:.2f}s ({dur:.2f}s audio, ahead by {lead_time:.1f}s). Pre-transcribing...")
                        self.asr_worker.enqueue_chunk(pcm, timestamp_offset=seg_start, is_live=False, stream_id=stream_id)

        except Exception as e:
            logger.debug(f"[Server] Error handling message: {e}")
