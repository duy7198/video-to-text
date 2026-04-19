# Deployment Guide

Recommended: **Hugging Face Spaces** — free, 16 GB RAM, 2 vCPU, 50 GB disk. Good fit for this app because of the ML dependencies (Whisper, EasyOCR).

---

## Option A — Hugging Face Spaces (FREE, recommended)

### 1. Create a Space

1. Sign in at https://huggingface.co (create a free account if needed).
2. Go to https://huggingface.co/new-space
3. Fill in:
   - **Owner**: your username (e.g. `duy7198`)
   - **Space name**: `video-to-text`
   - **License**: MIT
   - **Select the SDK**: **Docker** → **Blank**
   - **Hardware**: **CPU basic** (free, 16 GB RAM, 2 vCPU)
   - **Public**
4. Click **Create Space**.

### 2. Push code to the Space

Hugging Face Spaces are just git repositories. From your local machine:

```bash
# Clone the GitHub repo (if not already)
git clone https://github.com/duy7198/video-to-text.git
cd video-to-text

# Add the Space as a second remote
git remote add hf https://huggingface.co/spaces/<YOUR_HF_USERNAME>/video-to-text

# Push to the Space
git push hf main
```

When git asks for credentials:
- **Username**: your Hugging Face username
- **Password**: a Hugging Face **access token** (create at https://huggingface.co/settings/tokens — scope: `write`)

### 3. Watch it build

After pushing, visit `https://huggingface.co/spaces/<YOUR_HF_USERNAME>/video-to-text`. The Space will:
1. **Build** (~3 min first time) — installs Python deps.
2. **Run** — the container starts, Whisper downloads the `base` model (~145 MB) on first request.

Once it says "Running", the app is live at:
```
https://<YOUR_HF_USERNAME>-video-to-text.hf.space
```

That URL is public — share it with anyone.

### 4. Optional: upgrade for speed

CPU Basic (free) transcribes ~1× realtime with the `base` model. If you want faster / higher quality:
- In Space **Settings** → **Hardware**: upgrade to CPU Upgrade (~$0.03/hr) or a small GPU.
- Or change `WHISPER_MODEL=small` (better quality) or `tiny` (faster) in **Settings** → **Variables and secrets**.

---

## Option B — Fly.io ($0–5/mo)

Good if you want a custom domain and more control.

```bash
# Install flyctl: https://fly.io/docs/hands-on/install-flyctl/
fly auth signup
fly launch --no-deploy   # accept Dockerfile, skip DB
fly scale memory 2048    # 2 GB RAM (required for Whisper)
fly deploy
fly open
```

Fly gives you `https://<app-name>.fly.dev`. Free allowance covers low-traffic demos; sustained use costs a few $/mo.

---

## Option C — Railway (~$5/mo, easiest GitHub deploy)

1. Go to https://railway.com/new
2. **Deploy from GitHub repo** → select `duy7198/video-to-text`
3. Railway auto-detects the `Dockerfile`.
4. In the service settings, set:
   - **Memory**: 2 GB (default 512 MB is too small)
5. Click **Deploy**. Get a public URL under **Settings → Networking**.

Requires a $5/mo Hobby plan once the trial credit runs out.

---

## Option D — VPS (DigitalOcean / Hetzner / etc, ~$5–6/mo)

Get any Linux VPS with ≥ 2 GB RAM. Then:

```bash
ssh root@<your-vps>
apt update && apt install -y docker.io git
git clone https://github.com/duy7198/video-to-text.git
cd video-to-text
docker build -t video-to-text .
docker run -d --name v2t --restart=always -p 80:7860 video-to-text
```

The app is live at `http://<your-vps>`. Put Caddy or Nginx in front for HTTPS.

---

## Troubleshooting

**TikTok photo post returns an empty transcript**
- TikTok rate-limits the hosting IP (≈30–60 requests/min). Wait a few minutes and retry.
- If it persists, the JSON schema may have changed. Check the logs for "Rehydration script tag not found" or "Unexpected TikTok JSON structure".

**Runtime error: "Out of memory"**
- Whisper `base` needs ~1 GB, `small` needs ~2 GB. Check the instance has enough RAM.
- Or set `WHISPER_MODEL=tiny` (needs only ~400 MB).

**First request takes forever**
- Whisper downloads the model file on first use. Subsequent requests are fast.
- On HF Spaces, the cache persists between restarts.

**yt-dlp can't download a TikTok / YouTube video**
- yt-dlp changes frequently. In the Space Settings → **Factory reboot** rebuilds with the latest version from pip.
- Or pin a newer version in `requirements.txt`.
