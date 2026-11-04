# Privacy Policy — Shorts Autopilot

**Last updated:** 2026-11-04

Shorts Autopilot is a personal automation tool that creates short-form videos from source videos and uploads them to the operator's own YouTube channel.

## Data processed

- YouTube OAuth credentials, including a refresh token, used only to upload videos.
- YouTube channel and video metadata needed to select a source and publish a Short.
- Generated video files, titles, descriptions, tags, and run logs.
- The operator's email address, used only for operational alerts.

## How data is used

- To authenticate with the YouTube Data API.
- To render and upload Shorts to the configured channel.
- To record run state, prevent duplicate uploads, and send success or failure notifications.
- To diagnose failures and improve reliability.

## Storage and sharing

- OAuth credentials and API secrets are stored as encrypted GitHub Actions repository secrets.
- Run state is stored in `state/used_videos.json` in the repository.
- Temporary video files are processed on the GitHub Actions runner and are not committed.
- Email alerts are delivered through Resend when configured.
- Data is not sold, rented, or used for advertising.

## Retention

- Used-video state is retained until the operator deletes it.
- OAuth access can be revoked at any time at <https://myaccount.google.com/permissions>.
- Temporary media is deleted with the ephemeral GitHub Actions runner.

## Contact

For privacy questions or deletion requests, contact: **YOUR_EMAIL@example.com**
