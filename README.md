---
title: Video To Text
emoji: 🎬
colorFrom: indigo
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: YouTube/TikTok/image → text with auto language detection
---

# video → text

A small web tool that converts **YouTube / TikTok videos and images to text**, with automatic language detection.

- **Audio/video** → transcribed by [OpenAI Whisper](https://github.com/openai/whisper) (auto language detection)
- **Images** → OCR via [EasyOCR](https://github.com/JaidedAI/EasyOCR)
- **TikTok photo slideshows** → fallback: [Playwright](https://playwright.dev) scrapes slides, then OCR
- **Any URL yt-dlp supports** works — YouTube, TikTok, Facebook, Twitter/X, Instagram Reels, and more

## Screenshot

Minimal, serif + mono aesthetic. Single-page web UI:

```
 ┌──────────────────────────────────────────┐
 │ video → text                             │
 │                                          │
 │ [ URL ][ Image ]                         │
 │                                          │
 │ Source URL: [ https://...         ]      │
 │ [ Convert ↵ ]                            │
 │                                          │
 │ Output                  [ English ][ video ]
 │ ┌──────────────────────────────────────┐ │
 │ │ Full transcript shows up here...     │ │
 │ └──────────────────────────────────────┘ │
 │ [ copy ] [ download .txt ] [ new ]       │
 └──────────────────────────────────────────┘
```

## Quickstart

### 1. System dependencies

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt-get install -y ffmpeg curl
```

### 2. Python packages

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

### 3. Run

```bash
python app.py
```

Open http://localhost:5000 in your browser.

### 4. (Optional) Docker

```bash
docker build -t video-to-text .
docker run -p 5000:5000 video-to-text
```

## Configuration

Environment variables:

| Variable        | Default  | Description                                               |
| --------------- | -------- | --------------------------------------------------------- |
| `WHISPER_MODEL` | `small`  | `tiny`, `base`, `small`, `medium`, `large`, `large-v3`    |
| `PORT`          | `5000`   | HTTP port                                                 |
| `HOST`          | `0.0.0.0`| Bind address                                              |
| `UPLOAD_DIR`    | `uploads`| Where uploaded images are stored                          |
| `YTDLP_TIMEOUT` | `600`    | yt-dlp timeout in seconds                                 |

Example:

```bash
WHISPER_MODEL=medium PORT=8000 python app.py
```

Model size vs. speed/quality tradeoff (CPU):

| Model    | Size    | Speed | Quality |
| -------- | ------- | ----- | ------- |
| tiny     | ~75 MB  | ⚡⚡⚡⚡  | ★★      |
| base     | ~145 MB | ⚡⚡⚡   | ★★★     |
| small    | ~485 MB | ⚡⚡    | ★★★★    |
| medium   | ~1.5 GB | ⚡     | ★★★★☆   |
| large-v3 | ~3 GB   | 🐢    | ★★★★★   |

## API

### `POST /api/transcribe`

```bash
curl -X POST http://localhost:5000/api/transcribe \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
# -> {"job_id": "abc123..."}
```

### `POST /api/ocr`

```bash
curl -X POST http://localhost:5000/api/ocr \
  -F "image=@photo.jpg" \
  -F "langs=en,vi"
# -> {"job_id": "abc123..."}
```

### `GET /api/status/<job_id>`

```bash
curl http://localhost:5000/api/status/abc123...
# Returns one of:
#   {"status": "queued", ...}
#   {"status": "processing", "progress": "Transcribing..."}
#   {"status": "done", "result": {"type": "video", "text": "...", "language": "en", ...}}
#   {"status": "error", "error": "..."}
```

## How it works

```
┌──────────────┐
│   Browser    │
└──────┬───────┘
       │ POST /api/transcribe { url }
       ▼
┌──────────────┐    ┌────────────────┐
│    Flask     │───▶│  Background    │
│   app.py     │    │  worker thread │
└──────────────┘    └────────┬───────┘
                             │
             ┌───────────────┴───────────────┐
             │                               │
             ▼                               ▼
      ┌──────────┐                    ┌────────────────┐
      │  yt-dlp  │◀── fails on ─────▶│  Playwright    │
      │          │    /photo/ URLs    │  (TikTok slides)│
      └────┬─────┘                    └────────┬───────┘
           │                                   │
           ▼                                   ▼
      ┌──────────┐                      ┌──────────┐
      │ Whisper  │                      │ EasyOCR  │
      │  (audio) │                      │  (text)  │
      └────┬─────┘                      └────┬─────┘
           │                                 │
           └──────────────┬──────────────────┘
                          ▼
                 ┌─────────────────┐
                 │ Auto language   │
                 │   detection     │
                 └─────────────────┘
```

## Project structure

```
video-to-text/
├── app.py                    # Flask server, job queue, endpoints
├── services/
│   ├── transcriber.py        # yt-dlp + Whisper + Playwright pipeline
│   └── ocr_service.py        # EasyOCR wrapper
├── templates/
│   └── index.html            # Single-page UI
├── static/
│   ├── style.css             # Refined minimal CSS
│   └── script.js             # Tabs, polling, result rendering
├── requirements.txt
├── Dockerfile
├── .gitignore
└── README.md
```

## Notes

- First request downloads the Whisper model (~485 MB for `small`) — be patient.
- EasyOCR also downloads per-language models on first use.
- The in-memory job store resets on restart. For production, swap in Redis.
- `yt-dlp` is updated frequently — run `pip install -U yt-dlp` if downloads break.

## License

MIT
