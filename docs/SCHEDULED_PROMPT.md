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
5. Open `<clip>.json` (the source sidecar, e.g. `pexels_12345.json`). The key fields to focus on:
   - `species_verified`: the actual species shown in the clip, extracted from the source platform's slug. **THIS IS THE TRUTH.**
   - `query_used`: the search term that found the clip. **IT IS OFTEN WRONG.** A search for "bald eagle" may return a "white-bellied sea eagle" clip. Do NOT use this for species identification.
   - `source_url`: the Pexels/Pixabay page URL — cross-check `species_verified` against this slug yourself if anything feels off.

## SPECIES ACCURACY — READ THIS BEFORE WRITING
6. This has historically been the #1 failure mode of this pipeline: writing a caption for the wrong species.
   - If `species_verified` is a valid species name, every specific claim in the caption (population numbers, threat status, geographic range, legal protections, evolutionary history) must apply to THAT species. Not to the species implied by `query_used`.
   - If `species_verified` is empty, null, or "unknown", write a GENERIC caption that does not name a specific species. Use phrases like "these birds", "the heron", "a raptor at dusk". Generic-but-correct beats specific-but-wrong every time.
   - When in doubt about whether a claim applies to this exact species, pull back to a broader habitat-level or genus-level claim that clearly applies.

   **Concrete example of the failure mode to avoid:**
   A past post used a clip whose slug was `majestic-sea-eagle-perched-outdoors` (a white-bellied sea eagle, found in Indo-Pacific Asia/Australia). The caption was written about the North American bald eagle's DDT recovery, citing 1963 population numbers and the Endangered Species Act. These facts apply to a completely different species on a different continent. Never again.

## Generate copy
7. Read the voice rules from two places and follow them precisely:
   - Hook rules: `src/caption.py` → `HOOK_SYSTEM_PROMPT` constant (includes species-accuracy rule)
   - Caption rules: `src/caption.py` → `SYSTEM_PROMPT` constant (includes species-accuracy rule + full reference caption)
   - Project feedback memory: /Users/aamirtinwala/.claude/projects/-Users-aamirtinwala-Desktop-Market-Research-agent-md-files/memory/feedback_ig_poster_content_rules.md

8. Generate four outputs for THIS specific clip, anchored on `species_verified` (not `query_used`):
   - `hook`: one line, 4–10 words. Tender/haunting/provocative. If species is verified, can name it; if not, stay generic. No em dashes.
   - `caption`: 90–160 words. Hook line + cohesive narrative body (sentences chain with connectives, vary in length, focus on dangers this species faces with concrete specifics). No em dashes.
   - `hashtags`: 15–20 hashtags, mix broad (#wildlife, #conservation, #birding) and niche (species-specific tag based on species_verified, plus threat-specific tags).
   - `tone_bucket`: exactly one of `"sad"`, `"ambient"`, `"hopeful"`. Pick the dominant emotion of the caption:
     - `sad` — grief, loss, extinction, decline, habitat destruction, climate despair. The weight sits in what's being lost.
     - `ambient` — stillness, quiet awe, solitude, contemplation, wonder without a threat-arc. Observational.
     - `hopeful` — recovery, rescue, release, rehabilitation, triumph, playful, joyful, hopeful action.
     If the caption moves from sad to hopeful, pick where the final third lands. If genuinely split, pick `sad`.

9. Self-check BEFORE writing to the sidecar:
   - **Species check:** the species claimed in the caption matches `species_verified` exactly, OR the caption is generic. If `query_used` and `species_verified` differ, the caption is about species_verified.
   - **Fact check:** every specific number/year/threat named in the caption applies to the verified species. If you cannot confirm, remove the specific claim or broaden it to the habitat.
   - No em dashes anywhere. Scan for \u2014 and \u2013. If present, rewrite.
   - No banned phrases: "dive in", "let's explore", "did you know", "stunning", "amazing", "mind-blowing", "you won't believe", "fun fact", "imagine if", "the beauty of", "majestic creature", "incredible", "nature's wonder".
   - Caption 90–160 words. Hook 4–10 words. Hashtags 15–20.

## Write sidecar + push
9. Write the JSON to `<clip_path_without_extension>.llm.json` with exactly four keys: `hook`, `caption`, `hashtags`, `tone_bucket`.
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
