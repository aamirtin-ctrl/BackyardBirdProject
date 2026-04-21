# Instagram Auto-Poster

Daily agent: pulls a nature/environmental documentary clip, generates an educational caption via Claude, and posts it to Instagram as a Reel. Runs on GitHub Actions cron, zero monthly cost beyond Anthropic API usage.

## Status
- [x] Project skeleton + config + logging
- [x] `sources.py` — archive.org + YouTube via `yt-dlp`
- [x] `video.py` — ffmpeg 9:16 center-crop, 60-90s trim, IG-compliant encode
- [x] `caption.py` — Claude `claude-sonnet-4-5` with voice rules enforced
- [x] `state.py` — JSON post history, atomic writes, self-test
- [x] `storage.py` — Cloudflare R2 upload
- [x] `instagram.py` — Graph API create + poll + publish (dry-run supported)
- [x] `main.py` orchestrator + `scripts/ingest.py`, `scripts/post_now.py`
- [x] `.github/workflows/daily-post.yml` — cron + manual dispatch
- [ ] **You**: populate `.env` with creds (see below)
- [ ] **You**: first manual run, then enable cron

## Quickstart

```bash
# deps
brew install uv ffmpeg
uv sync

# config
cp .env.example .env
# fill in ANTHROPIC_API_KEY, R2_*, IG_*, META_*

# dry-run: pulls + processes + generates caption, skips R2 upload & IG publish
uv run python -m src.main --dry-run

# manual ingest only
uv run python scripts/ingest.py -n 3

# post whatever's next in the queue
uv run python scripts/post_now.py --dry-run   # or drop --dry-run for real
```

## Sources & licensing

All sources used are licensed for commercial social-media use. Nonprofit status doesn't change that — the licenses below would permit a for-profit to do the same thing.

**Priority order** (`src/sources.py`, consumed by `main.py`):

1. **Pexels** — [Pexels License](https://www.pexels.com/license/): free for commercial & personal, no attribution required, redistribution on social media explicitly allowed. Portrait-orientation filter is applied for better 9:16 yield.
2. **Pixabay** — [Content License](https://pixabay.com/service/license-summary/): same terms, animals+nature categories preselected, safesearch on.
3. **archive.org** (fallback) — filtered to NASA, Prelinger, and CC-licensed items only.

**What was removed:** BBC Earth / NatGeo / PBS Nature YouTube channels. Those are copyrighted; reposting isn't licensed even for nonprofits.

**Wildlife-only filter:** tags containing `person`, `people`, `man`, `woman`, `hiker`, `diver`, `tourist`, etc. cause the clip to be skipped. Seed queries target animals, ecosystems, and conservation subjects (see `WILDLIFE_QUERIES` in `sources.py`).

If you want to add your own seed queries, edit `WILDLIFE_QUERIES` directly. Keep them subject-focused, not people-focused.

## Credentials setup

### 1. LLM access (hook + caption generation)

The pipeline needs Claude to write two things for each post: a 4–10 word hook for the static slide, and the 90–160 word educational caption.

There are **two ways** to provide this. Pick one:

#### Option A — Claude Code scheduled trigger (recommended, $0 additional)
Use your existing Claude Code subscription. A scheduled agent runs daily, generates the hook + caption directly in-session, writes them to a sidecar file next to the clip, and pushes to the repo. GitHub Actions then posts with no LLM calls.

- Cost: $0 additional; uses the Claude Max subscription you already have.
- Autonomy: fully daily, hands-off.
- Setup: see §6 below.
- The pipeline reads `<clip>.llm.json` (a sidecar with `hook`, `caption`, and `hashtags` keys) and skips any API call when present.

#### Option B — Anthropic API key
Direct API calls from the GitHub Actions runner. About $0.15–$0.30 per month for daily posts.

- Create the key at https://console.anthropic.com → top-right avatar → **API keys** → **Create key**. Name it `ig-poster`.
- Add a payment method under **Plans & Billing**. Set a $5 monthly cap under **Usage limits** as a safety net.
- Paste into `.env` as `ANTHROPIC_API_KEY=sk-ant-...`
- Pitfall: this key is NOT the same as the login to claude.ai chat. API and chat are separate products.

You can mix both if you want: set the key as a fallback so the pipeline works even if the scheduled trigger fails on a given day. But for the recommended setup, leave `ANTHROPIC_API_KEY` blank and rely on §6.

### 2. Pexels + Pixabay (free, ~30s each)
- Pexels: https://www.pexels.com/api/new/ → `PEXELS_API_KEY`
- Pixabay: https://pixabay.com/api/docs/ → `PIXABAY_API_KEY`

Both are free, require no credit card, and have generous rate limits (Pexels: 200/hour, 20k/month; Pixabay: 100 requests/60s).

### 3. Cloudflare R2 (free 10 GB, no egress fees)

R2 hosts the images and videos the pipeline uploads. IG's servers fetch them via the R2 public URL — this is the reason we need R2 rather than posting the files directly.

**3.1 Create the Cloudflare account**
- https://dash.cloudflare.com → Sign up. No charge below the free tier (10 GB storage, 10M Class A ops/month, 1M Class B ops/month — way more than one daily post uses).
- Verify your email before continuing.

**3.2 Enable R2**
- Left sidebar → **R2 Object Storage**.
- First time only: Cloudflare prompts you to add a payment method. **It will not charge you under the free tier**, but the card has to be on file. Add it and click **Enable R2**.

**3.3 Create the bucket**
- R2 overview → **Create bucket**.
- **Name:** `ig-poster` (bucket names are globally unique; if it's taken, append your initials: `ig-poster-<xy>`).
- **Location:** leave as "Automatic".
- **Default storage class:** Standard.
- Click **Create bucket**.

**3.4 Enable the public development URL**
Required so Instagram's servers can fetch your uploaded files.
- Click into the bucket → **Settings** tab.
- Scroll to **Public Development URL** → click **Enable**.
- Cloudflare warns that anyone with the URL can read the file. Confirm.
- Copy the URL shown — it looks like `https://pub-<32-char-hash>.r2.dev`. This is `R2_PUBLIC_BASE_URL`.
- Privacy note: anything you upload becomes fetchable by that URL. Our pipeline only puts bird images/videos there, but don't use this bucket for anything sensitive.

**3.5 Grab the Account ID**
- On the R2 page, look at the right sidebar → **Account ID** (34-character hex string).
- Copy → this is `R2_ACCOUNT_ID`.
- Heads up: Cloudflare has *three* similar-looking IDs (Account ID, Zone ID, User ID). We want **Account ID**.

**3.6 Create an API token scoped to this bucket**
- Top-right of the R2 page → **Manage R2 API Tokens** → **Create API token**.
- **Token name:** `ig-poster-rw`.
- **Permissions:** **Object Read & Write** (NOT Admin; NOT Read-only).
- **Specify bucket:** select `ig-poster` only (don't leave it on "All buckets" — least privilege).
- **TTL:** leave blank (no expiry).
- **Client IP filter:** leave blank.
- Click **Create API Token**.

Cloudflare shows the Access Key ID and Secret Access Key **exactly once**. Copy both into your scratch file *now*:
- **Access Key ID** → `R2_ACCESS_KEY_ID` (starts with a short hex string)
- **Secret Access Key** → `R2_SECRET_ACCESS_KEY` (longer)

If you miss the copy window, delete the token and create a fresh one.

**3.7 Fill `.env`**
```
R2_ACCOUNT_ID=<from 3.5>
R2_ACCESS_KEY_ID=<from 3.6>
R2_SECRET_ACCESS_KEY=<from 3.6>
R2_BUCKET=ig-poster
R2_PUBLIC_BASE_URL=https://pub-<hash>.r2.dev
```

**3.8 Common errors when you run the pipeline**
- **`Media not found` from IG when posting** → the public URL isn't working. Upload any file via the Cloudflare dashboard and run `curl -I <R2_PUBLIC_BASE_URL>/<filename>` from your laptop. You want HTTP 200. If it's 403 or a 404, you skipped 3.4 or the hash is wrong.
- **`403 InvalidArgument` on upload** → the API token's permission scope is wrong. Recreate it with **Object Read & Write** bound specifically to the `ig-poster` bucket.
- **`SignatureDoesNotMatch`** → the Secret Access Key was pasted with trailing whitespace or a line break. Re-paste it carefully; it should be one continuous string.

### 4. Instagram Graph API — new account walkthrough (Instagram Login path)

We use the 2024+ "Instagram API with Instagram Login" path — no Facebook Page required, just an Instagram Business account and a Meta app.

**4.0 Pre-flight checklist**
- New or existing Instagram account (no followers required).
- Personal **Facebook** account (needed to log into the Meta developer console — this is NOT a Facebook *Page*, it's your regular FB user).
- Laptop/desktop browser for the Meta side; mobile Instagram app for the conversion step in 4.1.
- 20–30 minutes the first time through.
- A scratch text file to stash IDs and tokens as you go. Meta won't let you re-view some of them.

**4.1 Convert the Instagram account to Business**

On mobile (iOS & Android, in the Instagram app):
1. Tap your profile picture bottom-right.
2. Tap the ☰ (three-line) icon top-right.
3. Tap **Settings and privacy**.
4. Scroll to **For professionals** → tap **Account type and tools**.
5. Tap **Switch to professional account**.
6. Read the intro screens; tap Continue through them.
7. Category: pick something like **Environmental Service** or **Non-Profit Organization**. Tap Done.
8. Professional account type: **Business** (NOT Creator — `instagram_business_content_publish` only works for Business).
9. If prompted to review your contact info, fill or skip.
10. If prompted to **Connect to Facebook Page**, tap **Skip** or **Not now**. The new Login API doesn't need a Page.

**4.2 Create a Meta developer account**
1. Open https://developers.facebook.com in your browser.
2. Click **Log In** and use your personal Facebook account. (This is the login identity for the developer console. It's not connected to the IG Business account you'll be posting to.)
3. If it's your first time: accept the developer terms, enter a phone number, and confirm the SMS code Meta sends you.

**4.3 Create the app**
1. Go to https://developers.facebook.com/apps → click **Create App** (top-right).
2. **Use cases** screen → select **Other** (NOT "Authenticate and request data from users with Facebook Login"). Click Next.
3. **App type** → select **Business**. Click Next.
4. **Details** screen:
   - App name: anything descriptive, e.g. `ig-poster` (not visible to Instagram users).
   - App contact email: yours.
   - Business portfolio: there's a small "I don't want to connect a business portfolio" link at the bottom — click it.
5. Click Create App. Solve the captcha. You land on the app dashboard.

**4.4 Add the Instagram product — PICK THE RIGHT ONE**

⚠️ There are **two** Instagram products. Get this right the first time or you'll be undoing it:
- ✅ **Instagram** (new) — uses Instagram Login, doesn't need Facebook Pages. **This is the one.**
- ❌ **Instagram Graph API** (legacy) — requires a Facebook Page linked to the Instagram account. Skip.

On the app dashboard, scroll to **Add products to your app** and find the card titled just **Instagram** (description: "Enables access to the Instagram Graph API"). Click **Set up**.

**Verify you got the right one:** the left navigation should show **Instagram → API setup with Instagram Login**. If it shows "Pages" or "Facebook Login for Business" instead, you picked the legacy one. Go back and pick the other card.

**4.5 Add your Instagram account + authorize scopes**
1. Left nav: **Instagram → API setup with Instagram Login**.
2. Under **1. Generate access tokens** → click **Add account**.
3. A popup appears. Log in with the **Instagram Business account** you made in 4.1 (NOT the personal Facebook account from 4.2 — these are different logins).
4. The authorization screen will ask to grant these scopes:
   - `instagram_business_basic`
   - `instagram_business_content_publish`
   - `instagram_business_manage_comments`
   - `instagram_business_manage_messages`

   Tap **Allow all**.
5. After redirect, the API setup page now lists your connected account. Copy the **Instagram-scoped user ID** (a 17-digit number) → save as `IG_USER_ID` in your scratch file.

**4.6 Generate the access token**
1. Still on the API setup page, next to your connected account, click **Generate token**.
2. A popup shows the token (starts with `IGA...` or `IGQ...`). Copy it.
3. **This token is already long-lived (60 days).** The Instagram Login API issues long-lived tokens directly — no exchange step needed, unlike the legacy Facebook Graph API flow. Save it as `IG_ACCESS_TOKEN`.
4. Verify immediately with:
   ```bash
   curl -s "https://graph.instagram.com/v21.0/me?fields=id,username,account_type&access_token=<TOKEN>"
   ```
   Expect: `{"id":"...","username":"yourhandle","account_type":"BUSINESS"}`.

**4.7 Grab app credentials**
1. Top-left menu on the app dashboard → **App settings** → **Basic**.
2. Look for **Instagram App ID** (NOT the "App ID" at the very top of the page — that's the Meta app ID, a different number). Copy → `META_APP_ID`.
3. Find **Instagram App Secret** → click **Show** → copy → `META_APP_SECRET`.

**4.8 Fill `.env`**
```
IG_USER_ID=<from 4.5, the ID returned by /me in 4.6 is the canonical one if they differ>
IG_ACCESS_TOKEN=<from 4.6>
META_APP_ID=<Instagram App ID, from 4.7>
META_APP_SECRET=<from 4.7>
```

**4.9 Troubleshooting**

| Error | Likely cause & fix |
|---|---|
| `Insufficient Developer Role` (OAuth screen at 4.5) | The IG account hasn't been added as an Instagram Tester. Meta app dashboard → **App Roles → Roles → Instagram Testers** tab → **Add Instagram Testers** → enter your IG username. Then in Instagram: Settings → Apps and websites → **Tester Invites** → Accept. Retry 4.5. |
| `Session key invalid` / `Invalid Access Token` (code 452) when running the old exchange curl | You don't need the exchange step anymore — see 4.6. The Instagram Login API issues long-lived tokens directly. |
| `The user is not a Business Account` | 4.1 wasn't completed, or you picked Creator instead of Business. Redo the switch on mobile. |
| `Application does not have permission for this action` | You didn't grant all four scopes in 4.5. On the Meta app page, remove your account and re-authorize (or use the IG app: Settings → Security → Apps and websites → remove, then re-do 4.5). |
| `Media not found` during publishing | Means Instagram couldn't fetch your R2 URL. See the R2 common errors in §3.8. |
| `(#10) Application does not have permission to impersonate this user` | The account in 4.5 isn't the same as the IG Business one from 4.1. Confirm you're logged into the right IG account when authorizing. |

**Rate limits:** 200 Graph API calls per hour per user. One daily post uses ~8 calls. You won't hit this.

**Development mode:** while the Meta app is in Development mode, it can only post to Instagram accounts explicitly added in 4.5 — **which is exactly the behavior you want** for a single-user setup. Do NOT submit for App Review.

**4.10 Token maintenance**

The 60-day token can be refreshed any time within that window. Refreshing resets the 60-day counter.

Manual refresh:
```bash
curl -G "https://graph.instagram.com/refresh_access_token" \
  --data-urlencode "grant_type=ig_refresh_token" \
  --data-urlencode "access_token=<current_token>"
```

Set a calendar reminder for day 45. Or build a monthly GH Actions workflow that refreshes + commits the new token via `gh secret set` — flagged as optional, not built in v1.

### 5. GitHub Actions secrets
Repo → Settings → Secrets and variables → Actions. Add every key from `.env.example` as a repo secret (except `ANTHROPIC_API_KEY` if you're going with Option A / scheduled trigger — leave it unset).

### 6. Set up the scheduled Claude Code trigger (Option A)

This is the piece that replaces the Anthropic API key. A scheduled trigger fires daily in Claude Code's cloud, generates the hook + caption for the next clip, commits a sidecar JSON to your repo, and kicks off the GitHub Actions workflow that does the actual image build and IG publish.

**6.1 Push this project to GitHub**
1. In this project directory: `git init`, `git add .`, `git commit -m "initial commit"`.
2. Create a new private repo at https://github.com/new (private keeps your state.json visibility minimal).
3. Follow GitHub's "push an existing repository" instructions to link and push.
4. Install GitHub CLI if you don't have it: `brew install gh`, then `gh auth login`.

**6.2 Configure the workflow auth**
The workflow needs to push state.json back to the repo. By default, the built-in `GITHUB_TOKEN` can do this if you enable it:
- Repo → Settings → Actions → General → **Workflow permissions** → select **Read and write permissions** → Save.

**6.3 Create the scheduled trigger in Claude Code**
In any Claude Code session, run `/schedule` and follow prompts. When asked for the trigger's prompt, paste the contents of [docs/SCHEDULED_PROMPT.md](docs/SCHEDULED_PROMPT.md) — but first open that file and replace `<OWNER>/<REPO>` with your real GitHub path (e.g. `aamirtinwala/environmental-insta`).

Suggested cron: `0 22 * * *` (22:00 UTC = 5pm Chicago CDT = peak engagement).

**6.4 What happens each day**
1. 22:00 UTC → trigger fires in Claude Code cloud.
2. Trigger clones the repo, picks the oldest queued clip without an `.llm.json` sidecar.
3. I (Claude, running in the trigger) generate the hook + caption + hashtags, following the voice rules in `src/caption.py` and the feedback memory.
4. Trigger writes `data/queue/<clip>.llm.json`, commits it, pushes.
5. Trigger calls `gh workflow run daily-post.yml`.
6. GitHub Actions picks up the dispatch event within ~1 minute. Sees the `.llm.json`, skips any API calls, does frame extraction + cinematic grade + text overlay + R2 upload + IG carousel publish.
7. Post is live on Instagram by ~22:05 UTC.

**6.5 Verify the loop end-to-end once**
Before turning the schedule on for real:
1. Fill `.env` with everything from §2–§5 (skipping Anthropic).
2. In the repo locally, manually create a test `<clip>.llm.json` with hardcoded hook/caption/hashtags next to one of your queued clips.
3. Run `uv run python -m src.main --dry-run`. Logs should say `using pre-generated caption from llm sidecar` and `using pre-generated hook from llm sidecar`, then `DRY-RUN: would post CAROUSEL`.
4. If that works, drop `--dry-run` for one real test post.
5. Delete the test sidecar. Create the scheduled trigger. Done.

## Pipeline order (from `main.py`)

Two post styles, selected by the `POST_STYLE` env var:

### `POST_STYLE=static_video` (default — carousel)
1. Load config.
2. Top up queue if below 3 clips.
3. Pick oldest un-posted clip.
4. Generate a 4-10 word hook line via Claude (voice rules enforced).
5. Extract a frame from the clip, apply cinematic grade, overlay the hook in Playfair Display. Save as JPEG.
6. Generate the long-form caption via Claude (same voice rules).
7. Upload both the JPEG and the MP4 to R2.
8. Create IG child containers (IMAGE + VIDEO), wait for both FINISHED, create CAROUSEL parent, publish.
9. Update state, archive clip.

### `POST_STYLE=reels`
Original single-Reel flow. Steps 4 + 7 + 8 collapse to "generate caption, upload video, post REELS."

Any failure halts with non-zero exit. GH Actions emails you on failure.

## Things not in v1

No web UI, no multi-account, no scene detection, no TTS, no Stories/Carousels, no analytics. One IG account, one daily Reel.

## Troubleshooting

- **"no video stream in raw"** — source file is audio-only or corrupt. Delete from `data/raw/`.
- **IG container stuck in `IN_PROGRESS`** — R2 URL may not be publicly fetchable. Test with `curl -I $URL`.
- **"banned phrase" warnings** — Claude slipped a cliché. The pipeline keeps going; regenerate if you want by re-running with the same clip.
- **Em dashes** — they're auto-stripped by `_strip_em_dashes` before posting.
