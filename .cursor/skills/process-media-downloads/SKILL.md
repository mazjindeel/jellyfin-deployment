---
name: process-media-downloads
description: >-
  Processes completed Transmission downloads on the ThinkPad Jellyfin homelab:
  inventory ~/stuff/downloads/completed, dry-run organize-media.py, move
  normalized files into ~/stuff/media, remove processed torrents from
  Transmission, update parsing rules in jellyfin-deployment, and walk the user
  through dry-run review before apply.
  Use when organizing new downloads, normalizing media names, running
  organize-media.py, or preparing Jellyfin library imports on thinkpad.
---

# Process Media Downloads

Guides the **completed-downloads → Jellyfin library** workflow on the ThinkPad. **Human approves the dry-run plan** before any `--apply`. Agent inventories, runs the script, summarizes, edits `organize-media.py` when needed, and **always cleans up** processed torrents afterward.

Read first: [scripts/README.md](../../scripts/README.md) (CLI, architecture, extension guide).

## Paths

| | |
|---|---|
| ThinkPad SSH | `maz@192.168.1.50` |
| Completed downloads | `~/stuff/downloads/completed/` |
| Jellyfin library | `~/stuff/media/` (container `/media`) |
| Transmission container | `transmission-vpn` |
| Script (ThinkPad) | `~/homelab/jellyfin-deployment/scripts/organize-media.py` |
| Script (dev) | `~/workspace/jellyfin-deployment/scripts/organize-media.py` |
| Skill (git) | `jellyfin-deployment/.cursor/skills/process-media-downloads/` |
| Skill (Cursor) | `~/.cursor/skills/process-media-downloads` → symlink to git copy |

## Never do without human approval

- `--apply` (moves are not undoable; script refuses overwrites but still destructive)
- Deleting files in `media/`
- Removing torrents from Transmission **before** apply succeeds (data loss risk)
- `git commit` / `git push` (unless user explicitly asks)

Post-apply cleanup in `completed/` and Transmission removal **is part of the approved workflow** — run it automatically after a successful apply (Phase 7).

## Session workflow (in order)

```
- [ ] Phase 1: Inventory completed downloads
- [ ] Phase 2: Sync script (if dev edits pending)
- [ ] Phase 3: Dry-run + save plan
- [ ] Phase 4: Review plan with human
- [ ] Phase 5: Script fixes (if unclassified / wrong targets)
- [ ] Phase 6: Apply (human says go)
- [ ] Phase 7: Post-apply cleanup (Transmission + leftovers, Jellyfin scan)
```

### Phase 1 — Inventory

On ThinkPad, list what arrived:

```bash
ssh maz@192.168.1.50 'find ~/stuff/downloads/completed -type f \( -iname "*.mkv" -o -iname "*.mp4" -o -iname "*.avi" -o -iname "*.m4v" -o -iname "*.srt" \) -print | head -200'
ssh maz@192.168.1.50 'du -sh ~/stuff/downloads/completed/* 2>/dev/null | sort -hr | head -30'
ssh maz@192.168.1.50 'ls -la ~/stuff/downloads/completed/'
```

Summarize for the human: folder names, video count, anything that looks non-media (archives, nfo-only, samples). Note if folders are `root:root` (Transmission missing `PUID`/`PGID` — see [reference.md](reference.md)).

### Phase 2 — Sync script

If `organize-media.py` was edited locally, deploy to ThinkPad before dry-run:

```bash
cd ~/workspace/home-server
./scripts/pull-on-thinkpad.sh main jellyfin-deployment   # after push, if committed
# or one-off:
scp ~/workspace/jellyfin-deployment/scripts/organize-media.py \
  maz@192.168.1.50:~/homelab/jellyfin-deployment/scripts/
```

Confirm ThinkPad has the expected script:

```bash
ssh maz@192.168.1.50 'python3 ~/homelab/jellyfin-deployment/scripts/organize-media.py --help | head -5'
```

### Phase 3 — Dry-run

**Always dry-run first.** Source = completed; destination = library:

```bash
ssh maz@192.168.1.50 'python3 ~/homelab/jellyfin-deployment/scripts/organize-media.py \
  ~/stuff/downloads/completed \
  --output ~/stuff/media \
  --save-plan ~/stuff/downloads/completed-plan.json \
  2>&1 | tee ~/stuff/downloads/completed-dry-run.log'
```

Copy artifacts for local review if helpful:

```bash
scp maz@192.168.1.50:~/stuff/downloads/completed-plan.json /tmp/
scp maz@192.168.1.50:~/stuff/downloads/completed-dry-run.log /tmp/
```

### Phase 4 — Review plan with human

Present using [reference.md](reference.md) **Dry-run summary** template. Highlight:

- **Conflicts** (must be 0 before apply — two sources want same destination)
- **Unclassified** (needs manual decision or script rule)
- **Skipped** (samples/junk — removed in Phase 7 with torrent cleanup)
- Representative moves (show → season → filename)

**Stop and wait** for explicit human approval or change requests. Do not suggest apply until conflicts = 0 and unclassified are resolved or explicitly accepted as manual follow-up.

### Phase 5 — Script fixes

When dry-run shows parse gaps, edit on the dev machine:

| Change | Where |
|--------|--------|
| New release naming pattern | `_EPISODE_PATTERNS` in `organize-media.py` |
| Show folder consolidation | `canonicalize_show_metadata()` |
| Special title extraction | `parse_tv_file()` branch |
| Movie / extras edge case | `parse_movie_file()` / `parse_tv_extras_file()` |

Conventions (from `scripts/README.md`):

- Standard library only; Python 3.10+
- One concern per diff; patterns ordered most-specific first
- Dry-run default; no silent overwrites

Test without ThinkPad files:

```bash
cd ~/workspace/jellyfin-deployment
# Add representative paths to a snippet file, then:
python3 scripts/simulate-from-paths.py paths.txt
```

After fixes: re-run Phase 2 → Phase 3 → Phase 4. Human commits when satisfied (agent does not commit unless asked).

### Phase 6 — Apply

Only after human approval.

If completed folders are `root:root`, fix ownership before apply (or set `PUID`/`PGID` on Transmission — see reference):

```bash
ssh maz@192.168.1.50 'sudo chown -R maz:maz ~/stuff/downloads/completed/'
```

Apply:

```bash
ssh maz@192.168.1.50 'python3 ~/homelab/jellyfin-deployment/scripts/organize-media.py \
  ~/stuff/downloads/completed \
  --output ~/stuff/media \
  --apply \
  --cleanup-empty \
  2>&1 | tee ~/stuff/downloads/completed-apply.log'
```

On failure (`FileExistsError`, `OSError`): stop, show log, do not retry apply without human decision. If a cross-filesystem copy succeeded but source unlink failed, remove the duplicate source and re-run apply for remaining items.

### Phase 7 — Post-apply (always run after successful apply)

**1. Remove processed torrents from Transmission** (and delete leftover data in `completed/`):

Map apply log / inventory folder names to torrent IDs, then remove:

```bash
ssh maz@192.168.1.50 'docker exec transmission-vpn transmission-remote -l'
# Remove by ID (comma-separated). --remove-and-delete drops torrent + data on disk.
ssh maz@192.168.1.50 'docker exec transmission-vpn transmission-remote -t {ids} --remove-and-delete'
```

This clears `.nfo`, `Screens/`, and empty release folders left after video moves.

**2. Verify `completed/` is clean:**

```bash
ssh maz@192.168.1.50 'find ~/stuff/downloads/completed -type f \( -iname "*.mkv" -o -iname "*.mp4" \) | wc -l'
ssh maz@192.168.1.50 'ls -la ~/stuff/downloads/completed/'
```

Non-zero video count means unclassified items or partial apply failure.

**3. Jellyfin:** remind human to scan the `/media` library (Dashboard → Libraries → Scan).

## In-place library tidy (optional)

Separate from new downloads — reorganize existing `~/stuff/media` without touching completed:

```bash
ssh maz@192.168.1.50 'python3 ~/homelab/jellyfin-deployment/scripts/organize-media.py ~/stuff/media'
ssh maz@192.168.1.50 'python3 ~/homelab/jellyfin-deployment/scripts/organize-media.py ~/stuff/media --apply --cleanup-empty'
```

Same dry-run → approve → apply gate applies. No Transmission cleanup (source is the library, not completed).

## Standard user prompt

> Process my completed downloads for Jellyfin using the **process-media-downloads** skill. On the ThinkPad, inventory `~/stuff/downloads/completed`, dry-run `organize-media.py` into `~/stuff/media`, walk me through the plan, and only apply after I approve. Update the normalization script if anything is unclassified.

## Additional resources

- [reference.md](reference.md) — output templates, decision tree, Transmission cleanup, troubleshooting
- [scripts/README.md](../../scripts/README.md) — full script documentation
