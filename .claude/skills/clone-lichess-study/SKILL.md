---
name: clone-lichess-study
description: Copy (clone) a Lichess study that Julen's chess teacher shared into Julen's Lichess account (julenlarra), so he can work on it and the study tracker picks it up. Use whenever the user shares a lichess.org/study link and wants it cloned, copied or added to Julen's profile, or says things like "clone this study", "add Klase7 for Julen", "copy the teacher's study".
---

# Clone a Lichess study into Julen's account

Lichess has no clone API, so the copy is rebuilt: chapters are downloaded as PGN, a new
**unlisted** study is created in Julen's account, the chapters are imported one by one
(names, starting positions, moves, comments, arrows and board orientation are kept) and the
empty chapter Lichess adds to new studies is removed. The default name is the original's
name + `" - julen"`, which is how the study tracker sorts studies into tracks.

After copying, the link to Julen's copy is sent to the teacher, **markel3**, as a Lichess private
message (text in `clone_message.txt` in the repo). Lichess's API can't add study members, so the
message is how the copy is shared; the copy is unlisted, so the link opens it. Sending needs the
token's `msg:write` scope; without it the clone still succeeds and the run says the link wasn't sent.

The tracker lives in the GitHub repo `ilarra/julen_lichess_tracker`. Its **Clone study**
workflow does the copy with a token stored as a GitHub secret, records the original in
`shared_studies.txt` (so the tracker can tell Julen's moves from the teacher's) and refreshes
the dashboard at https://ilarra.github.io/julen_lichess_tracker/.

## Steps

1. **Get the link(s).** Accept `https://lichess.org/study/XXXXXXXX` (optionally followed by
   `/chapterId`) or a bare 8-character id. If the user gives a custom name, use it; otherwise
   leave the name empty so the default applies. Several links = one run per link.

2. **Pick the first route that works here** (check quickly, don't ask the user which):

   **A. GitHub workflow (preferred).** Works where `gh` can reach the repo, e.g. Claude Code
   sessions with `ilarra/julen_lichess_tracker` attached (in a cloud session, attach it with
   the add-repo tool first if it isn't).
   ```bash
   gh workflow run clone.yml -R ilarra/julen_lichess_tracker -f study_url="<link>" [-f name="<name>"]
   ```
   Then find the run (`gh run list -R ilarra/julen_lichess_tracker --workflow clone.yml -L 1`),
   wait for it to finish, and read the result from the job log (the `Done: https://lichess.org/study/…`
   line, `Sent the link to markel3` / `Not sent to markel3: …`, or `Error: …`). Only pass
   `-f force=true` if the user explicitly wants a second copy, and `-f notify=false` if they say
   not to tell the teacher.

   **B. Direct, with a local token.** Works where `lichess.org` is reachable and the environment
   has `LICHESS_TOKEN` set to a token with `study:write` (e.g. Julen's mini PC):
   ```bash
   python3 scripts/clone_study.py "<link>" --suffix " - julen" --notify markel3 \
     --message "Kaixo Markel! Hemen duzu nire kopia: {url}" [--name "<name>"]
   ```
   (`scripts/` is next to this file.) Run with `--dry-run` first only if the user asks what would
   be copied. If the tracker repo is checked out, add `--record <repo>/shared_studies.txt` and
   commit that file.

   **C. Neither available** (typical in a plain claude.ai chat): don't improvise another method.
   Tell the user to open
   https://github.com/ilarra/julen_lichess_tracker/actions/workflows/clone.yml →
   **Run workflow** → paste the link → **Run workflow** (works from the GitHub phone app too),
   and give them the exact link to paste.

3. **Report** the new study's link and chapter count, whether the link reached markel3, and that the dashboard updates after the
   sync that the workflow starts (a few minutes). If it failed, give the error in one line:
   - "already has a study called …" → Julen already has it; give that link. Offer a forced copy only if they want one.
   - "refused the token" → the `LICHESS_WRITE_TOKEN` secret (or `LICHESS_TOKEN`) needs the `study:write` scope.
   - "Not found" → the study is private and not shared with Julen; ask the teacher to share or make it unlisted.

## Rules

- Never ask the user to paste a Lichess token into the chat. Tokens belong in the GitHub secret
  `LICHESS_WRITE_TOKEN` or the local environment.
- Copies are created **unlisted**: only people with the link can see them, like the teacher's original.
- Lichess limits: 30 new studies per day, 64 chapters per study.
