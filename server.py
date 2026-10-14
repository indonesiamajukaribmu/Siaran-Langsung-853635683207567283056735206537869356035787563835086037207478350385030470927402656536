#!/usr/bin/env python3
"""
Live Streaming Server (Python + Flask + SocketIO)
Real audio/video broadcasting platform with WebRTC getUserMedia support.
Bypasses voice-detector blocking by using real Web Audio API routing.
"""

import os
import json
import time
import uuid
import asyncio
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = BASE_DIR / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"

app = Flask(__name__, static_folder=str(STATIC_DIR), template_folder=str(TEMPLATE_DIR))
app.config["SECRET_KEY"] = "live-stream-secret-" + str(uuid.uuid4())
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512MB per upload

# Use threading mode so no external async runtime is required.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    ping_interval=10, ping_timeout=20)

# ----------------------------------------------------------------------------
# Live state (in-memory)
# ----------------------------------------------------------------------------
# Possible statuses: "offline" (red), "online" (orange), "live" (green)
HOST_STATE = {
    "status": "offline",
    "viewers": 0,
    "started_at": None,
    "channel_name": "Ruang Siaran Langsung",
    "host_name": "Host",
    "last_update": time.time(),
}

VIEWERS = {}  # sid -> {name, joined_at}

# ----------------------------------------------------------------------------
# HTTP routes
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    """Main streaming page."""
    return render_template("index.html", host_state=HOST_STATE)


@app.route("/api/state")
def api_state():
    """Current host state - JSON endpoint for polling fallback."""
    return jsonify({
        "status": HOST_STATE["status"],
        "viewers": len(VIEWERS),
        "started_at": HOST_STATE["started_at"],
        "channel_name": HOST_STATE["channel_name"],
        "host_name": HOST_STATE["host_name"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


@app.route("/api/state", methods=["POST"])
def set_state():
    """Update host state - can be called to change offline/online/live."""
    data = request.get_json(force=True)
    new_status = data.get("status", "offline")
    if new_status not in ("offline", "online", "live"):
        return jsonify({"error": "invalid status"}), 400
    HOST_STATE["status"] = new_status
    HOST_STATE["last_update"] = time.time()
    if new_status == "live" and not HOST_STATE["started_at"]:
        HOST_STATE["started_at"] = time.time()
    elif new_status == "offline":
        HOST_STATE["started_at"] = None
    # Broadcast to all connected viewers
    socketio.emit("host_state", HOST_STATE, namespace="/")
    return jsonify(HOST_STATE)


@app.route("/api/upload_chunk", methods=["POST"])
def upload_chunk():
    """Receive a recorded video chunk from the browser (MediaRecorder)."""
    if "video" not in request.files:
        return jsonify({"error": "no video chunk"}), 400
    file = request.files["video"]
    session_id = request.form.get("session_id", "default")
    seq = int(request.form.get("seq", "0"))
    ext = "webm"  # MediaRecorder default output
    fname = f"live_{session_id}_{seq:06d}.{ext}"
    safe = secure_filename(fname)
    target = RECORDINGS_DIR / safe
    file.save(str(target))
    return jsonify({"ok": True, "filename": safe, "size": target.stat().st_size})


@app.route("/api/finalize_recording", methods=["POST"])
def finalize_recording():
    """Combine chunks into a single downloadable video file."""
    data = request.get_json(force=True)
    session_id = data.get("session_id", "default")
    total = int(data.get("total_chunks", 0))
    if total <= 0:
        return jsonify({"error": "invalid chunk count"}), 400
    chunks = []
    for i in range(total):
        p = RECORDINGS_DIR / f"live_{session_id}_{i:06d}.webm"
        if p.exists():
            chunks.append(p)
    if not chunks:
        return jsonify({"error": "no chunks found"}), 404
    final_name = f"recording_{session_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.webm"
    final_path = RECORDINGS_DIR / final_name
    with open(final_path, "wb") as out:
        for c in chunks:
            with open(c, "rb") as inp:
                out.write(inp.read())
    # Optionally clean up chunk files
    for c in chunks:
        try:
            c.unlink()
        except Exception:
            pass
    return jsonify({"ok": True, "filename": final_name, "size": final_path.stat().st_size})


@app.route("/api/recordings")
def list_recordings():
    """List all available recordings for download."""
    files = []
    for p in sorted(RECORDINGS_DIR.glob("recording_*.webm"), key=lambda x: x.stat().st_mtime, reverse=True):
        files.append({
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            "url": f"/recordings/{p.name}",
        })
    return jsonify({"recordings": files})


@app.route("/recordings/<path:filename>")
def download_recording(filename):
    """Download a finalized recording."""
    safe = secure_filename(filename)
    return send_from_directory(str(RECORDINGS_DIR), safe, as_attachment=True)


# ----------------------------------------------------------------------------
# WebSocket events
# ----------------------------------------------------------------------------
@socketio.on("connect")
def on_connect():
    sid = request.sid
    VIEWERS[sid] = {"name": f"Tonton-{sid[:6]}", "joined_at": time.time()}
    emit("host_state", HOST_STATE)
    socketio.emit("viewer_count", {"count": len(VIEWERS)}, namespace="/")
    print(f"[CONNECT] {sid} | total viewers: {len(VIEWERS)}")


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    if sid in VIEWERS:
        del VIEWERS[sid]
    socketio.emit("viewer_count", {"count": len(VIEWERS)}, namespace="/")
    print(f"[DISCONNECT] {sid} | total viewers: {len(VIEWERS)}")


@socketio.on("set_host_status")
def on_set_status(data):
    """Viewer or host changes status: offline/online/live."""
    new_status = data.get("status", "offline")
    if new_status not in ("offline", "online", "live"):
        return
    HOST_STATE["status"] = new_status
    HOST_STATE["last_update"] = time.time()
    if new_status == "live" and not HOST_STATE["started_at"]:
        HOST_STATE["started_at"] = time.time()
    elif new_status == "offline":
        HOST_STATE["started_at"] = None
    socketio.emit("host_state", HOST_STATE, namespace="/")
    print(f"[STATUS] -> {new_status}")


@socketio.on("broadcast_chunk")
def on_broadcast_chunk(data):
    """Relay real-time audio/video data to all viewers (low-latency broadcast)."""
    socketio.emit("live_chunk", data, namespace="/", include_self=False)


@socketio.on("chat_message")
def on_chat(data):
    """Simple chat relay."""
    msg = {
        "name": VIEWERS.get(request.sid, {}).get("name", "Anon"),
        "text": str(data.get("text", ""))[:500],
        "ts": time.time(),
    }
    socketio.emit("chat_message", msg, namespace="/")


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    HOST = os.environ.get("STREAM_HOST", "0.0.0.0")
    PORT = int(os.environ.get("STREAM_PORT", "8080"))
    print("=" * 60)
    print("  LIVE STREAMING SERVER")
    print(f"  Open:  http://localhost:{PORT}")
    print(f"  Recordings: {RECORDINGS_DIR}")
    print("=" * 60)
    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)
