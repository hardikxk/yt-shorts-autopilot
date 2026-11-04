# Architecture

## Runtime

```text
GitHub Actions (ubuntu-latest)
  ├─ schedule / manual dispatch
  ├─ actions/checkout
  ├─ Python 3.12 + ffmpeg + fonts
  ├─ scripts/run_daily.py
  │    ├─ YouTube Data API: select source
  │    ├─ yt-dlp: download source
  │    ├─ ffmpeg: render Shorts
  │    ├─ YouTube resumable upload
  │    ├─ Resend: optional email
  │    └─ state/used_videos.json
  └─ commit state back to the repository
```

There is no server, container, database, or Cloudflare account.

## Selection

`config/pipeline.json` controls the source:

- `source_mode: channel` reads the uploads playlist of `own_channel_id` or `source_channel_ids`.
- `source_mode: trending` reads the regional most-popular chart.
- `rights_mode` filters to your channel, an allowlist, or Creative Commons.
- A source is rejected if it is older than `max_video_age_hours`, outside the duration bounds, not public, not processed, not embeddable, live, or already in `state/used_videos.json`.

The newest eligible source is selected. There is no random behavior.

## Rendering

The renderer uses only `ffmpeg`:

- 1080×1920, 30fps, H.264 High@4.1, AAC 128k.
- Blurred background + centered source.
- Wrapped title overlay in a translucent box.
- `loudnorm` and fades.
- Default 45s, hard maximum 180s.
- Audio-energy window selection, with a shorter-count fallback if the source cannot fit the requested number.

## State

`state/used_videos.json` is committed to the repo. Each entry contains the source ID, title, date, status, and uploaded Short IDs. The workflow commits it with the built-in `GITHUB_TOKEN` after the pipeline step, even if the run fails, so successful and partial uploads are not repeated.

## Secrets

Stored only in GitHub Actions repository secrets:

- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`
- optional `RESEND_API_KEY`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`

They are injected as environment variables for the pipeline step only. They are never written to the repo or printed.

## Reliability

- `concurrency` prevents overlapping daily jobs.
- `timeout-minutes: 60` caps the run.
- Each upload is attempted independently; the run fails if fewer than `min_shorts` succeed.
- If the process dies before state is committed, the next run may retry the same source. YouTube duplicate uploads are possible in that edge case; check YouTube Studio after a failed run.
- GitHub provides a failure email automatically. Resend is optional for richer summaries.

## Costs

Public GitHub repositories receive free standard-runner minutes. A daily 10–30 minute job is well within the private-repo free allowance of 2,000 minutes/month. YouTube API quota is 10,000 units/day; each upload costs 1,600 units.
