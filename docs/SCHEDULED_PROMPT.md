# Scheduled Trigger Prompt — Daily IG Copy Generation

This is the prompt used by the Claude Code scheduled remote trigger (created via `/schedule`). The trigger fires once a day and executes these instructions in a fresh Claude Code environment.

The trigger's job is **LLM generation only**: pick the next clip from the queue, write a hook + caption + hashtags to a sidecar JSON, push to GitHub, and kick off the GitHub Actions workflow that does the rest (image build, R2 upload, Instagram publish).

---

## Prompt (copy this verbatim into the `/schedule` prompt field)

```
You are running the daily LLM-generation phase of the Environmental_Insta_Project Instagram auto-poster. This is a non-interactive daily task. Complete every step, then stop.

Repo: https://github.com/<OWNER>/<REPO>   ← replace before first run

## Setup
1. Clone the repo into /tmp/ig-poster if not already cloned. cd into it. `git pull` to sync.

## Pick the next clip
2. List files in `data/queue/*.mp4` sorted by mtime ascending.
3. For each, check:
   a. Is the basename already in `data/state.json`'s `posted[].file` list? Skip it.
   b. Does `<clip_stem>.llm.json` already exist in the same directory? Skip it — already generated.
4. The first clip that passes both checks is the target. If no target exists, log "queue exhausted" and stop.

## Read clip metadata
5. Open `<clip>.json` (the source sidecar, e.g. `pexels_12345.json`). Fields include `title`, `description`, `subject`, `tags`, `source_url`, `source`.

## Generate copy
6. Read the voice rules from two places and follow them precisely:
   - Hook rules: `src/caption.py` → `HOOK_SYSTEM_PROMPT` constant
   - Caption rules: `src/caption.py` → `SYSTEM_PROMPT` constant
   Plus the project feedback memory: /Users/aamirtinwala/.claude/projects/-Users-aamirtinwala-Desktop-Market-Research-agent-md-files/memory/feedback_ig_poster_content_rules.md

7. Generate three outputs for THIS specific clip (using the metadata to anchor the content — don't write generic bird copy):
   - `hook`: one line, 4–10 words, following HOOK_SYSTEM_PROMPT. Tender/haunting/provocative, species-specific, no em dashes, no banned phrases.
   - `caption`: 90–160 words following SYSTEM_PROMPT. Hook line → body (3–6 sentences with specifics, numbers, places, names) → close (1–2 sentences, no moralizing). Absolutely no em dashes.
   - `hashtags`: 15–20 hashtags, mix broad (#documentary, #wildlife) and niche (e.g. #kakapo, #sixthmassextinction).

8. Self-check before writing:
   - No em dashes anywhere. Scan for \u2014 and \u2013. If present, rewrite.
   - No banned phrases in either hook or caption: "dive in", "let's explore", "did you know", "stunning", "amazing", "mind-blowing", "you won't believe", "fun fact", "imagine if", "the beauty of", "majestic creature".
   - Caption word count is in 90–160.
   - Hook word count is in 4–10.
   - Hashtag count is in 15–20.

## Write sidecar + push
9. Write the JSON to `<clip_path_without_extension>.llm.json` with exactly three keys: `hook`, `caption`, `hashtags`.
10. `git add <the new file>`, commit with message `chore: pre-generate copy for <clip_stem>`, push to `main`.

## Kick off GitHub Actions
11. Run: `gh workflow run daily-post.yml`
12. Confirm with: `gh run list --workflow=daily-post.yml --limit=1` — a run should be queued or in progress.

## Report
13. Print a one-line summary:
    `OK clip=<stem> hook="<hook>" workflow_run_id=<id>`

If ANY step fails, stop and print `FAIL at step N: <error>`. The run should exit non-zero so the scheduled trigger marks it failed and notifies me.
```

---

## Notes for whoever sets this up

- **Where to edit before first run:** replace `<OWNER>/<REPO>` on line 3 with your actual GitHub path.
- **Cron suggestion:** `0 22 * * *` (22:00 UTC = 5pm Chicago). Gives you a roughly 5pm local post (peak engagement). If the GitHub Actions workflow takes 2–3 minutes to post, the Reel lands at ~5:03pm.
- **Git auth inside the trigger:** Claude Code scheduled triggers run in a sandboxed environment with `gh` authenticated to your GitHub account (configured at schedule creation time). Make sure the scope includes `workflow` so step 11 can dispatch.
- **The trigger does NOT do R2 upload or IG posting** — that's GitHub Actions' job. Keeping them split means the trigger stays fast (~30s) and doesn't need R2/IG credentials.
- **If queue is empty:** the trigger exits cleanly. The GitHub Actions workflow includes the ingest step that tops the queue back up before picking the next clip.
