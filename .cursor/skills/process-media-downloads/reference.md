# Process media downloads — reference

## ThinkPad layout

| Path | Role |
|------|------|
| `~/stuff/downloads/completed/` | Transmission finished torrents (source for this workflow) |
| `~/stuff/downloads/incomplete/` | Active downloads — do not organize |
| `~/stuff/downloads/watch/` | Watch folder — do not organize |
| `~/stuff/media/` | Jellyfin library root |
| `~/homelab/jellyfin-deployment/` | Deployment repo (script lives here) |
| `transmission-vpn` container | Transmission + VPN; RPC on port 9091 |

Transmission maps `~/stuff/downloads/` → container `/data`. Completed and media are intentionally separate.

## Dry-run summary template

Use this when presenting Phase 4 results:

```markdown
### Completed downloads dry-run

**Source:** `~/stuff/downloads/completed`
**Destination:** `~/stuff/media`
**Script:** `organize-media.py` @ `{sha or mtime}`

| Metric | Count |
|--------|------:|
| Video moves | {N} |
| Subtitle moves | {N} |
| Conflicts | {N} ⚠️ must be 0 |
| Skipped | {N} |
| Unclassified | {N} |

#### Sample moves
- `{source}` → `{dest}` ({reason})
- …

#### Unclassified (needs decision)
- `{path}` — suggest: {manual move / new pattern / skip}

#### Skipped (junk — removed in Phase 7 with torrent cleanup)
- `{path}` ({reason})

**Human decision:** [ ] approve apply [ ] fix script first [ ] handle unclassified manually [ ] abort
```

## Decision tree

```
Inventory completed/
       │
       ▼
Dry-run (--output ~/stuff/media)
       │
       ├── conflicts > 0 ──► STOP — show conflicting paths; human resolves duplicates manually
       │
       ├── unclassified > 0 ──► Edit organize-media.py OR human accepts manual follow-up
       │         │
       │         └── re dry-run
       │
       └── human approves ──► --apply --cleanup-empty
                 │
                 ├── remove torrents (--remove-and-delete)
                 │
                 └── Jellyfin library scan
```

## Transmission cleanup (Phase 7)

After a successful apply, **always** remove the processed torrents. Videos are in `~/stuff/media`; leftover `.nfo`, `Screens/`, and empty folders stay in `completed/` until the torrent is removed.

```bash
# List torrents
ssh maz@192.168.1.50 'docker exec transmission-vpn transmission-remote -l'

# Remove by ID — deletes torrent entry AND data under completed/
ssh maz@192.168.1.50 'docker exec transmission-vpn transmission-remote -t 1,2,3 --remove-and-delete'
```

Match torrent names to the folders from the apply log. Do **not** remove torrents before apply completes (you would delete files that were not yet moved to the library).

## Root-owned downloads (Transmission)

**Symptom:** `completed/` folders owned by `root:root`; `--apply` fails with `Permission denied` or leaves duplicate files after cross-filesystem copy.

**Cause:** `transmission-vpn` running without `PUID`/`PGID` (container defaults to root).

**Fix (persistent):** in `~/homelab/vpn-transmission-deployment/.env.prod`:

```
PUID=1000
PGID=1000
```

Recreate:

```bash
cd ~/homelab/vpn-transmission-deployment
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --force-recreate
docker exec transmission-vpn id   # expect uid=1000(maz), not uid=0(root)
```

**Fix (one-off before apply):** `sudo chown -R maz:maz ~/stuff/downloads/completed/`

Repo: `vpn-transmission-deployment` — see its README for deploy details.

## Common unclassified causes

| Symptom | Likely fix |
|---------|------------|
| Movie with no year in name | Add year pattern or manual folder name |
| Miniseries without SxxEyy | Add `_EPISODE_PATTERNS` entry or `parse_tv_file` branch |
| Wrong show folder | `canonicalize_show_metadata()` rule |
| File under `Extras/` without episode code | `parse_tv_extras_file()` → Season 00 |
| Duplicate rips same episode | Manual — script does not dedupe |

## Troubleshooting

### `FileExistsError` on apply

Destination already exists in `~/stuff/media`. Options (human chooses):

1. Remove/rename the existing file in media
2. Remove duplicate from completed
3. Skip that item manually

Script never overwrites.

### Partial apply (copy succeeded, unlink failed)

Cross-device `shutil.move` copies then deletes source. If source is root-owned, destination may exist while source remains. Remove duplicate source, fix ownership, re-run apply for remaining items.

### Script on ThinkPad stale vs dev

```bash
ssh maz@192.168.1.50 'head -3 ~/homelab/jellyfin-deployment/scripts/organize-media.py'
head -3 ~/workspace/jellyfin-deployment/scripts/organize-media.py
```

Compare; sync via `pull-on-thinkpad.sh` or `scp` before dry-run.

### Test pattern locally

```bash
cd ~/workspace/jellyfin-deployment
printf '%s\n' \
  '/home/maz/stuff/downloads/completed/Show.Name.S01E01.x265.mkv' \
  > /tmp/test-paths.txt
python3 scripts/simulate-from-paths.py /tmp/test-paths.txt
```

### Regenerate full-library regression (optional)

When the on-disk library changes materially:

```bash
ssh maz@192.168.1.50 'find ~/stuff/media -print | sort' > ~/workspace/jellyfin-deployment/final_list.txt
cd ~/workspace/jellyfin-deployment
python3 scripts/simulate-from-paths.py final_list.txt --save-plan /tmp/full-plan.json
```

Expect 0 conflicts before recommending in-place `--apply` on the full library.

## Jellyfin after apply

1. Dashboard → Libraries → scan the `/media` library
2. If show folders were consolidated, old duplicate entries may need removal in Jellyfin UI
3. Metadata/posters are **not** handled by the script — fix in Jellyfin if needed

## Symlink setup (dev machine)

```bash
mkdir -p ~/.cursor/skills
ln -sf ~/workspace/jellyfin-deployment/.cursor/skills/process-media-downloads \
  ~/.cursor/skills/process-media-downloads
```
