FROM python:3.11-slim

# Hugging Face Spaces expects port 7860.
# `base` model is ~3x faster than `small` on CPU; good balance for hosted demo.
# HF_HOME and cache dirs are inside /app so they persist across restarts on HF Spaces.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=7860 \
    HOST=0.0.0.0 \
    WHISPER_MODEL=base \
    HF_HOME=/app/.cache/huggingface \
    XDG_CACHE_HOME=/app/.cache \
    EASYOCR_MODULE_PATH=/app/.cache/easyocr \
    WHISPER_CACHE_DIR=/app/.cache/whisper

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

# Make runtime dirs writable (HF Spaces may run as non-root uid 1000).
RUN mkdir -p uploads .cache/whisper .cache/easyocr .cache/huggingface \
    && chmod -R 777 uploads .cache

EXPOSE 7860

CMD ["python", "app.py"]
