"""
Video / Image -> Text web tool.

Flask server exposing:
  POST /api/transcribe  { url: "..." }         -> video/audio transcription via Whisper
  POST /api/ocr         multipart: image       -> image OCR via EasyOCR
  GET  /api/status/<job_id>                    -> poll job status

TikTok photo slideshows (where yt-dlp fails with "Unsupported URL /photo/") are
handled automatically: Playwright scrapes slide images, then EasyOCR extracts text.
"""
import os
import uuid
import threading
from pathlib import Path

from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename

from services.transcriber import transcribe_url
from services.ocr_service import ocr_image_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# In-memory job store. For multi-worker deployment, replace with Redis.
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **updates) -> None:
    with _JOBS_LOCK:
        _JOBS.setdefault(job_id, {}).update(updates)


def _get_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        return dict(_JOBS.get(job_id, {}))


def _run_job(job_id: str, fn, *args, **kwargs) -> None:
    _set_job(job_id, status="processing", progress="Starting...")

    def progress_cb(msg: str) -> None:
        _set_job(job_id, progress=msg)

    try:
        result = fn(*args, progress_cb=progress_cb, **kwargs)
        _set_job(job_id, status="done", result=result, progress="Complete")
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        _set_job(job_id, status="error", error=str(exc))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "URL must start with http(s)://"}), 400

    job_id = uuid.uuid4().hex
    _set_job(job_id, status="queued", kind="transcribe", url=url)
    threading.Thread(
        target=_run_job, args=(job_id, transcribe_url, url), daemon=True
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    if "image" not in request.files:
        return jsonify({"error": "No 'image' file in form-data"}), 400

    f = request.files["image"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(f.filename)
    saved_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{filename}"
    f.save(saved_path)

    langs_raw = request.form.get("langs", "en,vi")
    langs = [l.strip() for l in langs_raw.split(",") if l.strip()]

    job_id = uuid.uuid4().hex
    _set_job(job_id, status="queued", kind="ocr", filename=filename)
    threading.Thread(
        target=_run_job,
        args=(job_id, ocr_image_file, str(saved_path), langs),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id: str):
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    app.run(host=host, port=port, debug=False, threaded=True)
