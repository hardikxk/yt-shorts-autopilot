#!/usr/bin/env node
/**
 * One-time local YouTube OAuth helper.
 *
 * Creates a refresh token for GitHub Actions. Run it once on your laptop:
 *
 *   node scripts/youtube-auth.mjs
 *
 * The refresh token is written to .secrets/youtube-token.json (gitignored).
 * Then add it as the GitHub Actions secret YOUTUBE_REFRESH_TOKEN.
 */

import { createHash, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import process from "node:process";
import { URL } from "node:url";

const ROOT = path.resolve(import.meta.dirname, "..");
const SCOPES = [
  "https://www.googleapis.com/auth/youtube.upload",
  "https://www.googleapis.com/auth/youtube.readonly",
];
const DEFAULT_REDIRECT = "http://127.0.0.1:53682/oauth2callback";

function loadDotEnv(file) {
  const values = {};
  let raw;
  try {
    raw = readFileSync(file, "utf8");
  } catch {
    return values;
  }
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(trimmed);
    if (!match) continue;
    let value = match[2].trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

const fileVars = {
  ...loadDotEnv(path.join(ROOT, ".env")),
  ...loadDotEnv(path.join(ROOT, ".dev.vars")),
};
const clientId = process.env.YOUTUBE_CLIENT_ID || fileVars.YOUTUBE_CLIENT_ID || "";
const clientSecret = process.env.YOUTUBE_CLIENT_SECRET || fileVars.YOUTUBE_CLIENT_SECRET || "";
const redirectUri = process.env.OAUTH_REDIRECT_URI || DEFAULT_REDIRECT;

if (!clientId || !clientSecret) {
  console.error(
    [
      "Missing YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET.",
      "",
      "Put them in .env (copy .env.example) or export them in this terminal.",
      "Create an OAuth client of type 'Web application' in Google Cloud Console",
      `and add this exact Authorized redirect URI: ${redirectUri}`,
    ].join("\n"),
  );
  process.exit(1);
}

const redirect = new URL(redirectUri);
if (!["127.0.0.1", "localhost"].includes(redirect.hostname)) {
  console.error("OAUTH_REDIRECT_URI must use 127.0.0.1 or localhost for this local flow.");
  process.exit(1);
}
const port = Number(redirect.port || "80");
const state = randomBytes(24).toString("base64url");
const verifier = randomBytes(48).toString("base64url");
const challenge = createHash("sha256").update(verifier).digest("base64url");

const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
authUrl.searchParams.set("client_id", clientId);
authUrl.searchParams.set("redirect_uri", redirectUri);
authUrl.searchParams.set("response_type", "code");
authUrl.searchParams.set("scope", SCOPES.join(" "));
authUrl.searchParams.set("access_type", "offline");
authUrl.searchParams.set("prompt", "consent");
authUrl.searchParams.set("include_granted_scopes", "true");
authUrl.searchParams.set("state", state);
authUrl.searchParams.set("code_challenge", challenge);
authUrl.searchParams.set("code_challenge_method", "S256");

function openBrowser(url) {
  try {
    let child;
    if (process.platform === "win32") {
      child = spawn("rundll32", ["url.dll,FileProtocolHandler", url], {
        detached: true,
        stdio: "ignore",
      });
    } else if (process.platform === "darwin") {
      child = spawn("open", [url], { detached: true, stdio: "ignore" });
    } else {
      child = spawn("xdg-open", [url], { detached: true, stdio: "ignore" });
    }
    child.unref();
  } catch {
    // The URL is printed below; opening the browser is best effort.
  }
}

async function exchange(code) {
  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
      code_verifier: verifier,
    }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      `Token exchange failed (${response.status}): ${
        payload.error_description || payload.error || "unknown error"
      }`,
    );
  }
  if (!payload.refresh_token) {
    throw new Error(
      "Google did not return a refresh token. Revoke the app's access at https://myaccount.google.com/permissions and run this again.",
    );
  }
  return payload;
}

async function fetchChannel(accessToken) {
  const response = await fetch(
    "https://www.googleapis.com/youtube/v3/channels?part=snippet,contentDetails&mine=true&maxResults=1",
    { headers: { authorization: `Bearer ${accessToken}` } },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      `channels.list failed (${response.status}): ${
        payload.error?.message || "unknown error"
      }`,
    );
  }
  const item = payload.items?.[0];
  if (!item) return null;
  return {
    id: item.id,
    title: item.snippet?.title ?? "",
    uploadsPlaylistId: item.contentDetails?.relatedPlaylists?.uploads ?? "",
  };
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host}`);
  if (url.pathname !== redirect.pathname) {
    response.writeHead(404, { "content-type": "text/plain" });
    response.end("Not found");
    return;
  }
  const error = url.searchParams.get("error");
  const code = url.searchParams.get("code");
  const returnedState = url.searchParams.get("state");
  if (error || !code || returnedState !== state) {
    response.writeHead(400, { "content-type": "text/html; charset=utf-8" });
    response.end(
      `<h1>Authorization failed</h1><p>${error || "Missing code or state mismatch."}</p>`,
    );
    console.error(`Authorization failed: ${error || "missing code or state mismatch"}`);
    server.close();
    process.exitCode = 1;
    return;
  }

  try {
    const token = await exchange(code);
    const channel = await fetchChannel(token.access_token);
    mkdirSync(path.join(ROOT, ".secrets"), { recursive: true, mode: 0o700 });
    const tokenFile = path.join(ROOT, ".secrets", "youtube-token.json");
    writeFileSync(
      tokenFile,
      JSON.stringify(
        {
          refresh_token: token.refresh_token,
          scope: token.scope,
          token_type: token.token_type,
          created_at: new Date().toISOString(),
          channel,
        },
        null,
        2,
      ),
      { mode: 0o600 },
    );

    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(
      `<h1>YouTube authorization complete</h1><p>You can close this tab and return to the terminal.</p>`,
    );

    console.log("");
    console.log("Authorization complete.");
    console.log(`Token file: ${tokenFile}`);
    if (channel) {
      console.log(`Channel: ${channel.title} (${channel.id})`);
      console.log(`Suggested OWN_CHANNEL_ID: ${channel.id}`);
    }
    console.log("");
    console.log("Upload the secrets to GitHub Actions:");
    console.log("  gh secret set YOUTUBE_CLIENT_ID");
    console.log("  gh secret set YOUTUBE_CLIENT_SECRET");
    console.log("  gh secret set YOUTUBE_REFRESH_TOKEN");
    console.log("");
    console.log("For YOUTUBE_REFRESH_TOKEN, paste the value from:");
    console.log(`  ${tokenFile}`);
    console.log("");
    console.log("Keep the OAuth consent screen in Production, not Testing.");
    console.log("Testing-mode refresh tokens expire after 7 days.");
    server.close();
  } catch (exchangeError) {
    response.writeHead(500, { "content-type": "text/html; charset=utf-8" });
    response.end(`<h1>Token exchange failed</h1><pre>${String(exchangeError.message)}</pre>`);
    console.error(exchangeError.message);
    server.close();
    process.exitCode = 1;
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log("");
  console.log("Open this URL if your browser does not open automatically:");
  console.log(authUrl.toString());
  console.log("");
  openBrowser(authUrl.toString());
});

const timeout = setTimeout(() => {
  console.error("Timed out waiting for the OAuth redirect after 10 minutes.");
  server.close();
  process.exitCode = 1;
}, 600_000);
timeout.unref();
