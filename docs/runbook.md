# Runbook

## First check

1. Open the failed Actions run.
2. Read the failing step and the job summary.
3. Confirm the source channel has a video in the configured age/duration window.
4. Check `gh secret list` shows the three required secrets.

## “No eligible source video”

This is a normal exit, not a workflow failure.

Causes:

- No upload on the channel in the last `max_video_age_hours`.
- Source shorter than `min_source_seconds` or longer than `max_source_seconds`.
- Source is not public, not processed, live, or not embeddable.
- Source is already in `state/used_videos.json`.
- `rights_mode` does not match the source channel/license.

For a first test, temporarily set `max_video_age_hours` to `168` and upload a long test video. Put it back to `16` afterwards.

## OAuth failures

Symptoms: `invalid_grant`, `Token has been expired or revoked`, or `YouTube token refresh failed`.

- Testing-mode OAuth apps expire refresh tokens after **7 days**. Publish the app to Production.
- Re-run:
  ```bash
  node scripts/youtube-auth.mjs
  ```
- Update the GitHub secret:
  ```bash
  gh secret set YOUTUBE_REFRESH_TOKEN
  ```
- Check the redirect URI is exactly `http://127.0.0.1:53682/oauth2callback`.

## YouTube quota exceeded

Each upload costs 1,600 units; the default project quota is 10,000/day.

- Reduce `shorts_per_day`.
- Wait for the Pacific-time reset.
- Request a quota increase in Google Cloud Console.

## `yt-dlp` bot check

Symptoms: `Sign in to confirm you're not a bot`, HTTP 403, or no formats.

- Re-run the workflow; it may be transient.
- Update `yt-dlp` by re-running the workflow (it installs the latest allowed version).
- GitHub runner IPs are datacenter IPs and may be challenged. There is no CAPTCHA bypass in this repo.
- Use your own channel and a fresh upload to test.

## Upload failures

- `401`: OAuth/refresh token problem.
- `403 quotaExceeded`: quota.
- `403 uploadLimitExceeded`: channel upload limit or account restriction.
- `400`: title/description/category/privacy issue.
- File too large: lower `short_seconds` or the bitrate in `media/app/media.py`.

If some uploads succeed and fewer than `min_shorts` do, the run fails and state is marked `partial` after at least one upload. Check YouTube Studio before retrying to avoid duplicates.

## State commit fails

The workflow needs `permissions: contents: write`. If the repository has branch protection, allow `github-actions[bot]` to push or switch to a pull-request-based state update.

If `state/used_videos.json` is malformed, restore the last good version from git history.

## Email

- GitHub failure notifications are automatic for the repository owner.
- For custom success/failure email, set `RESEND_API_KEY`, `ALERT_EMAIL_TO`, and a verified `ALERT_EMAIL_FROM`.
- `onboarding@resend.dev` only sends to the Resend account email.
- Without Resend, the workflow logs `[email skipped]` but still succeeds/fails normally.

## Dry run

Actions → Daily YouTube Shorts → Run workflow → `dry_run: true`.

Local:

```powershell
$env:DRY_RUN="true"
python scripts/run_daily.py
```

## Disable

Actions → Daily YouTube Shorts → **⋯ → Disable workflow**.

Or remove the `schedule` block from `.github/workflows/daily.yml`.

## Rotate a secret

```bash
gh secret set YOUTUBE_REFRESH_TOKEN
```

If the refresh token was exposed, revoke access first:

<https://myaccount.google.com/permissions>

Then re-run `node scripts/youtube-auth.mjs` and update the secret.
