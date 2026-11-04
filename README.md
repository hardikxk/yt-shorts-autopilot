# YouTube Shorts Autopilot — GitHub Actions Edition

A free, serverless automation that turns one long-form YouTube video per day into **3–5 vertical Shorts** and uploads them to your YouTube channel.

It runs entirely on **GitHub Actions**:

- no Cloudflare, no VPS, no Docker, no credit card;
- daily cron built into GitHub Actions;
- GitHub emails you automatically if a run fails;
- optional Resend email for success summaries;
- a small JSON file committed back to the repo prevents reusing the same source video.

> **Rights notice:** do not use this to re-upload videos you do not own or have permission to reuse. The default configuration searches recent **Creative Commons** videos (`rights_mode: creativeCommon`). For your own channel, switch to `source_mode: channel` + `rights_mode: owned`. For third-party sources, use an explicit `allowlist` with written permission.

---

## How it works

```text
GitHub Actions schedule (04:00 UTC)
  │
  ├─ select a recent video from your channel
  │    └─ age / duration / live / privacy / already-used filters
  │
  ├─ yt-dlp download (≤1080p)
  ├─ ffmpeg render 1080×1920 Shorts
  │    ├─ blurred background + centered source
  │    ├─ title overlay, fades, loudness normalization
  │    └─ 45s, H.264/AAC, 30fps
  │
  ├─ YouTube resumable upload, one at a time
  ├─ write a summary to the Actions run
  ├─ optional Resend success email
  └─ commit state/used_videos.json back to the repo
```

If the job fails, GitHub Actions sends the repository owner a failure email automatically. If you configure Resend, the workflow also sends a detailed failure email.

---

## Setup

### 1. Create the Google OAuth client

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select a project.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen:
   - User type: **External**.
   - Add scopes:
     - `https://www.googleapis.com/auth/youtube.upload`
     - `https://www.googleapis.com/auth/youtube.readonly`
   - Add your Google account as a test user.
4. Create an OAuth client:
   - Type: **Web application**
   - Authorized redirect URI: `http://127.0.0.1:53682/oauth2callback`
5. Copy the client ID and client secret.

For unattended operation, publish the OAuth app to **Production**. Testing-mode refresh tokens expire after **7 days**.

### 2. Authorize locally once

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r media/requirements.txt
```

Create `.env` from `.env.example` and fill in:

```dotenv
YOUTUBE_CLIENT_ID="..."
YOUTUBE_CLIENT_SECRET="..."
YOUTUBE_REFRESH_TOKEN=""
```

Run:

```bash
node scripts/youtube-auth.mjs
```

The helper opens a browser, exchanges the code, and writes `.secrets/youtube-token.json`. It prints your channel ID.

Put the refresh token into `.env` for local runs:

```dotenv
YOUTUBE_REFRESH_TOKEN="1//..."
```

### 3. Configure the pipeline

Edit [config/pipeline.json](config/pipeline.json):

```jsonc
{
  "source_mode": "search",
  "rights_mode": "creativeCommon",
  "region_code": "IN",
  "max_video_age_hours": 16,
  "shorts_per_day": 4,
  "min_shorts": 3,
  "short_seconds": 45,
  "upload_privacy": "private",
  "dry_run": false
}
```

`source_mode: search` asks the YouTube Search API for recent Creative Commons videos ordered by view count, then applies the same duration/live/privacy/used filters.

For your own channel instead:

```jsonc
"source_mode": "channel",
"rights_mode": "owned",
"own_channel_id": "YOUR_CHANNEL_ID"
```

For a first test, set:

```jsonc
"max_video_age_hours": 168,
"dry_run": true
```

After the dry run succeeds, set `dry_run` back to `false` and `max_video_age_hours` back to `16`.

For the regional trending chart instead of a search:

```jsonc
"source_mode": "trending",
"rights_mode": "creativeCommon",
"region_code": "IN"
```

For licensed channels:

```jsonc
"source_mode": "channel",
"rights_mode": "allowlist",
"source_channel_ids": ["UC...", "UC..."]
```

### 4. Create the public repository

The repository is already prepared. If you are doing this manually:

```bash
git init
git add .
git commit -m "Initial YouTube Shorts automation"
gh repo create YOUR_USERNAME/yt-shorts-autopilot --public --source=. --remote=origin --push
```

Do **not** commit `.env`, `.dev.vars`, or `.secrets/`; they are in `.gitignore`.

### 5. Add GitHub Actions secrets

In the repository: **Settings → Secrets and variables → Actions → New repository secret**.

Required:

| Secret | Value |
| --- | --- |
| `YOUTUBE_CLIENT_ID` | Google OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | Google OAuth client secret |
| `YOUTUBE_REFRESH_TOKEN` | Refresh token from `.secrets/youtube-token.json` |

Optional email:

| Secret | Value |
| --- | --- |
| `RESEND_API_KEY` | Resend API key |
| `ALERT_EMAIL_TO` | Your email address |
| `ALERT_EMAIL_FROM` | A Resend-verified sender, or `onboarding@resend.dev` |

Or use the CLI:

```bash
gh secret set YOUTUBE_CLIENT_ID
gh secret set YOUTUBE_CLIENT_SECRET
gh secret set YOUTUBE_REFRESH_TOKEN
```

Verify:

```bash
gh secret list
```

### 6. Run a dry run

In GitHub: **Actions → Daily YouTube Shorts → Run workflow**.

- Set **dry_run** to `true`.
- Leave **video_id** blank to auto-select.
- Leave **force** off.

The workflow renders the Shorts but does not upload them. Check the job summary and logs.

If you have no recent long video on your channel, upload one or temporarily set `max_video_age_hours` to `168`.

### 7. Run a real upload

Run the workflow again with **dry_run** off.

With `"upload_privacy": "private"`, the uploads appear in YouTube Studio as private. Review them, then change to `"public"` or `"unlisted"` and commit.

The workflow runs daily at **04:00 UTC / 09:30 IST**. Change the cron in [.github/workflows/daily.yml](.github/workflows/daily.yml) if needed.

---

## Local run

```bash
python scripts/run_daily.py
```

It reads `.env` for credentials and uses `config/pipeline.json`. To force a dry run:

```powershell
$env:DRY_RUN="true"
python scripts/run_daily.py
```

To override the source:

```powershell
$env:VIDEO_ID="YOUR_VIDEO_ID"
$env:DRY_RUN="true"
python scripts/run_daily.py
```

Manual controls:

| Variable | Purpose |
| --- | --- |
| `VIDEO_ID` | Select a specific source video |
| `DRY_RUN=true` | Render without uploading |
| `FORCE=true` | Allow a source already in state |
| `PRIVACY_OVERRIDE=private` | Override config privacy |

---

## State and duplicate prevention

The workflow commits [state/used_videos.json](state/used_videos.json) after a successful or partial upload. A source is skipped if it appears there with status `completed` or `partial`.

The workflow uses `permissions: contents: write` and the built-in `GITHUB_TOKEN` to commit the state file. No personal access token is required.

If a run fails before any upload, the source is not marked used, so the next run can retry it.

---

## Tests

```bash
python -m unittest discover -s media/tests -t media -v
python scripts/smoke_render.py
```

The smoke test creates a synthetic source and validates both the audio and silent render paths at 1080×1920.

---

## Limitations

- YouTube can challenge `yt-dlp` from GitHub runner IPs with “Sign in to confirm you're not a bot.”
- GitHub Actions cron can start a few minutes late.
- A daily 20–30 minute job is within the free private-repo allowance (2,000 minutes/month); public repos have unlimited standard-runner minutes.
- GitHub Actions scheduled workflows can be delayed or disabled after long repository inactivity; keep the repo active.
- There is no caption burn-in or scene-detection model; highlights use the audio energy envelope.
- The renderer uses a fixed 1080×1920 / 45s style. Adjust `short_seconds` or the filter in [media/app/media.py](media/app/media.py).

---

## Security

- Never commit `.env`, `.dev.vars`, or `.secrets/`.
- GitHub Actions secrets are encrypted and not shown in workflow logs.
- The refresh token is only used inside the workflow.
- Revoke OAuth access at <https://myaccount.google.com/permissions> if a token is exposed.

See [docs/architecture.md](docs/architecture.md) and [docs/runbook.md](docs/runbook.md) for design and troubleshooting.

## License

MIT. You are responsible for the rights to the media you process.
