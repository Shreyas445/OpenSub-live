/**
 * OpenSub Live - MediaSource & SourceBuffer Interceptor (mse_hook.js)
 * Intercepts ahead-of-time audio chunks in the browser's RAM cache and
 * sends them over WebSocket to OpenSub Live (ws://127.0.0.1:9876).
 */
(function() {
    // Only run in top-level window (avoids iframe ads and connection spam)
    if (window.top !== window) {
        return;
    }

    console.log("[OpenSub Live] Initializing Media Buffer Tap...");

    const WS_URL = "ws://127.0.0.1:9876";
    let ws = null;
    let isConnected = false;
    let pendingMessages = [];
    let activeMediaElement = null;
    let savedInitSegments = {}; // stream_id -> base64 init data

    function arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        const chunk_size = 0x8000; // 32KB chunks for fast C-speed execution
        let binary = '';
        for (let i = 0; i < bytes.length; i += chunk_size) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk_size));
        }
        return window.btoa(binary);
    }

    function isInitSegment(bytes) {
        if (bytes.length < 8) return false;
        // WebM EBML header: 0x1A 0x45 0xDF 0xA3
        if (bytes[0] === 0x1A && bytes[1] === 0x45 && bytes[2] === 0xDF && bytes[3] === 0xA3) {
            return true;
        }
        // MP4 ftyp or moov box
        const tag = String.fromCharCode(bytes[4], bytes[5], bytes[6], bytes[7]);
        if (tag === "ftyp" || tag === "moov") {
            return true;
        }
        return false;
    }

    // 1. Resilient WebSocket Connection
    function connectWS() {
        try {
            ws = new WebSocket(WS_URL);

            ws.onopen = () => {
                isConnected = true;
                console.log("[OpenSub Live] Connected to desktop app!");

                // Resend all saved init segments on connect
                for (const streamId in savedInitSegments) {
                    ws.send(JSON.stringify({
                        type: "INIT_SEGMENT",
                        stream_id: streamId,
                        data: savedInitSegments[streamId]
                    }));
                }

                // Flush pending messages
                while (pendingMessages.length > 0 && isConnected) {
                    ws.send(pendingMessages.shift());
                }
            };

            ws.onclose = () => {
                isConnected = false;
                setTimeout(connectWS, 2000);
            };

            ws.onerror = () => {
                isConnected = false;
            };
        } catch (e) {
            setTimeout(connectWS, 2500);
        }
    }

    connectWS();

    function sendSafe(msgStr) {
        if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(msgStr);
        } else if (pendingMessages.length < 50) {
            pendingMessages.push(msgStr);
        }
    }

    // 2. Intercept SourceBuffer.prototype.appendBuffer & abort
    if (window.SourceBuffer && !window.SourceBuffer.__opensub_hooked) {
        window.SourceBuffer.__opensub_hooked = true;
        const origAppend = SourceBuffer.prototype.appendBuffer;
        const origAbort = SourceBuffer.prototype.abort;

        SourceBuffer.prototype.appendBuffer = function(data) {
            try {
                if (!this.__opensub_id) {
                    this.__opensub_id = "audio_" + Math.random().toString(36).substring(2, 8);
                }

                const mime = this.__opensub_mime || "";
                // Detect audio tracks (mime audio or non-video small chunks)
                const isAudio = mime.includes("audio") || (!mime.includes("video") && data.byteLength < 600000);

                if (isAudio && data && data.byteLength > 0) {
                    const chunkArrayBuffer = data.slice ? data.slice(0) : new Uint8Array(data).buffer;
                    const u8 = new Uint8Array(chunkArrayBuffer);
                    const b64Data = arrayBufferToBase64(chunkArrayBuffer);

                    // Check for initialization header (EBML header or moov box)
                    if (isInitSegment(u8) || !this.__opensub_has_init) {
                        this.__opensub_has_init = true;
                        savedInitSegments[this.__opensub_id] = b64Data;
                        console.log("[OpenSub Live] Captured audio INIT segment (" + chunkArrayBuffer.byteLength + " bytes)");
                        sendSafe(JSON.stringify({
                            type: "INIT_SEGMENT",
                            stream_id: this.__opensub_id,
                            mime: mime,
                            data: b64Data
                        }));
                    } else {
                        // Forward EVERY media slice so audio is 100% continuous with zero gaps
                        let startTime = 0;
                        if (this.buffered && this.buffered.length > 0) {
                            startTime = this.buffered.end(this.buffered.length - 1);
                        } else if (activeMediaElement) {
                            startTime = activeMediaElement.currentTime;
                        }

                        sendSafe(JSON.stringify({
                            type: "AUDIO_CHUNK",
                            stream_id: this.__opensub_id,
                            start_time: startTime,
                            data: b64Data
                        }));
                    }
                }
            } catch (err) {
                console.warn("[OpenSub Live] Error intercepting appendBuffer:", err);
            }

            return origAppend.apply(this, arguments);
        };

        SourceBuffer.prototype.abort = function() {
            if (this.__opensub_id) {
                sendSafe(JSON.stringify({
                    type: "ABORT_STREAM",
                    stream_id: this.__opensub_id
                }));
            }
            return origAbort.apply(this, arguments);
        };

        // Track MIME types
        if (window.MediaSource && !window.MediaSource.__opensub_hooked) {
            window.MediaSource.__opensub_hooked = true;
            const origAddSourceBuffer = MediaSource.prototype.addSourceBuffer;

            MediaSource.prototype.addSourceBuffer = function(mimeType) {
                const sb = origAddSourceBuffer.apply(this, arguments);
                sb.__opensub_mime = mimeType;
                return sb;
            };
        }
    }

    // 3. Track Video Playhead Synchronization
    function hookMediaElement(el) {
        if (el.__opensub_hooked) return;
        el.__opensub_hooked = true;
        activeMediaElement = el;

        let lastSyncTime = 0;
        function emitSync(force = false) {
            const now = performance.now();
            if (force || now - lastSyncTime > 40) {
                lastSyncTime = now;
                sendSafe(JSON.stringify({
                    type: "SYNC",
                    time: el.currentTime,
                    paused: el.paused,
                    rate: el.playbackRate
                }));
            }
        }

        el.addEventListener("timeupdate", () => emitSync(false));
        el.addEventListener("seeking", () => emitSync(true));
        el.addEventListener("seeked", () => emitSync(true));
        el.addEventListener("play", () => {
            activeMediaElement = el;
            emitSync(true);
        });
        el.addEventListener("pause", () => emitSync(true));
    }

    function scanMediaElements() {
        document.querySelectorAll("video, audio").forEach(hookMediaElement);
    }

    scanMediaElements();
    const observer = new MutationObserver(scanMediaElements);
    observer.observe(document.documentElement, { childList: true, subtree: true });

    console.log("[OpenSub Live] Browser Lookahead Tap active and monitoring!");
})();
