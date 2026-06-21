# Media library scripts

Tools for organizing the ThinkPad Jellyfin library at `~/stuff/media` (container path `/media`).

**Primary script:** [`organize-media.py`](organize-media.py) — dry-run by default; renames/moves TV episodes, movies, subtitles, and bonus content into a Jellyfin-friendly directory layout.

**Simulation helper:** [`simulate-from-paths.py`](simulate-from-paths.py) — dry-run against a `find` listing without needing the actual files on disk (used for review on a dev machine).

**Review artifacts (repo root):**

| File | Purpose |
|------|---------|
| `FINAL_PLAN.md` | Human-readable dry-run summary for the full library |
| `dry-run-final-plan.json` | Machine-readable action list (moves + subtitles) |
| `final_list.txt` | `find ~/stuff/media` snapshot used to validate the last plan |
| `paths.txt`, `paths2.txt` | Smaller library samples for regression checks |

---

## When to use `organize-media.py`

Use this script when:

- Downloaded releases use inconsistent naming (`Show.Name.S01E01.x265`, `BILLIONS - S01 E08`, `00 Firefly Serenity - Pilot Film`, etc.).
- The same show is split across folders (`SEAL Team` vs `Seal Team`, `Billions (2016)` vs `Billions (2017)`).
- Subtitles live in `Subs/`, `Subtitles/`, or as loose sidecars and should follow their episode.
- Bonus content (`Extras/`, `Featurettes/`) should be kept, not discarded.

Do **not** use this script for:

- Fixing Jellyfin metadata/posters (use Jellyfin UI or metadata providers).
- Deduplicating two different rips of the same episode (manual review).
- In-place transcoding or remuxing.
- Deleting junk files — it **skips** them; delete manually after a successful run.

---

## Target layout

```
Show Name (Year)/          # year omitted when unknown or intentionally consolidated
  Season 01/
    S01E01 - Episode Title.mkv
    S01E01.en.srt

Movie Name (Year)/
  Movie Name (Year).mkv
  Featurettes/             # DVD/Blu-ray bonus — path preserved under movie folder
    Behind The Story/
      Some Featurette.mkv
```

Design choices:

- **Show name in folder, not in every filename** — redundant inside `Show/Season NN/`.
- **`SxxEyy` kept in filenames** — Jellyfin/Plex episode matching.
- **Episode titles kept by default** — pass `--drop-titles` to omit.
- **Movies** use `Title (Year)/Title (Year).ext` unless bonus content keeps a subpath.

---

## Usage (ThinkPad)

```bash
export ORG_SCRIPT=~/jellyfin-deployment/scripts/organize-media.py

# 1. Dry-run (default) — always do this first
python3 "$ORG_SCRIPT" ~/stuff/media 2>&1 | tee ~/stuff/media-dry-run.log

# 2. Staging (recommended) — leaves original tree intact
python3 "$ORG_SCRIPT" ~/stuff/media --output ~/stuff/media-organized --apply

# 3. In-place — reorganizes within ~/stuff/media
python3 "$ORG_SCRIPT" ~/stuff/media --apply --cleanup-empty

# 4. Save plan as JSON
python3 "$ORG_SCRIPT" ~/stuff/media --save-plan ~/stuff/media-plan.json
```

### CLI flags

| Flag | Description |
|------|-------------|
| `source` | Directory to scan (required) |
| `--output` | Destination root (default: same as `source`) |
| `--apply` | Execute moves; without this, dry-run only |
| `--cleanup-empty` | After `--apply`, remove empty dirs under `source` |
| `--type tv\|movies\|auto` | Default `auto`: TV first, then movies for unclassified videos |
| `--minimal-names` | `E01` instead of `S01E01` inside season folders |
| `--drop-titles` | Omit episode title from filenames |
| `--save-plan PATH` | Write planned actions as JSON |

**Safety:** `apply_actions()` refuses to overwrite an existing destination file. There is no undo — use `--output` for a staging run when unsure.

**Skipped files** (left in place): samples, `RARBG.com.mp4` / `ETRG.mp4`, macOS `._*` metadata files. Delete manually after verifying the library.

---

## Architecture

```
iter_video_files(source)
       │
       ▼
build_plan(files, source_root, output_root)
       │
       ├── plan_tv_actions()     parse_tv_file() ──┐
       │                      parse_tv_extras_file() ─┘ → destination_for_episode()
       │                      + plan_subtitle_actions()
       │
       └── plan_movie_actions()  parse_movie_file() → destination_for_movie()
                                 + plan_movie_subtitle_actions()
       │
       ▼
dedupe_actions()   # logs CONFLICT to stderr if two sources want same destination
       │
       ▼
print_plan_summary() / apply_actions()
```

### Key types

```python
EpisodeInfo(show, season, episode, title, year=None)
MovieInfo(title, year, relative_path=None)   # relative_path for Featurettes/
AudiobookInfo(author, title)
PlannedAction(kind="move"|"subtitle"|"audiobook"|"cover", source, destination, reason)
SkippedItem(path, reason)
```

### Classification order (`--type auto`)

Files are partitioned **by extension** first — no filename-keyword guessing:

1. **Audiobook extensions** (`.m4b`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.opus`) → audiobook planning when `--audiobooks-output` is set.
2. **Video extensions** → TV then movies (existing pipeline).
3. Files matching `should_skip_*` are recorded in `skipped`.
4. Anything else is `unclassified` (printed for manual review).

Video classification (unchanged):

1. Each video file is tried as **TV** (`parse_tv_file`, then `parse_tv_extras_file`).
2. Remaining videos are tried as **movies** (`parse_movie_file`).

---

## Audiobooks

Target layout at `~/stuff/audiobooks` (Jellyfin container path `/audiobooks`; Audiobookshelf container path `/audiobooks`):

```txt
Author Name/
  Book Title/
    Book Title.m4b          # single-file
    01 - Chapter.mp3        # multi-chapter (original names preserved)
    cover.jpg               # optional sidecar
```

**Listening platform:** [Audiobookshelf](https://www.audiobookshelf.org/) on the homelab (`https://audiobooks.mazjindeel.com` over VPN/LAN). This script feeds the same directory; optimize layout for ABS, not Jellyfin Books. See [AUDIOBOOK-PLATFORM-PLAN.md](../../home-server/docs/AUDIOBOOK-PLATFORM-PLAN.md).

No extra top-level `Audiobooks/` folder — the mount root is already audiobooks-only.

### Release units

Each **immediate child** of the source directory is one release:

- A subfolder (e.g. `Andy Weir - 2021 - Project Hail Mary (Sci-Fi)/`)
- A loose file at the source root (e.g. `Title by Author.m4b`)

All audio files within a release unit land in the same `{Author}/{Book}/` folder.

### Author/title parsing (priority)

1. Nested `Author/Book/` in the release tree
2. **` by Author`** suffix (Matt Dinniman `.m4b` pattern)
3. **`Author - YYYY - Title`** folder (Andy Weir pattern)
4. Generic ` - ` split
5. **Unclassified** if nothing parses

Strips `(Audiobook)`, `(Fiction)`, `(Sci-Fi)`, series index parens like `(Dungeon Crawler Carl 01)`, and quality tags.

### CLI

```bash
# Completed downloads → dual libraries (dry-run)
python3 scripts/organize-media.py ~/stuff/downloads/completed \
  --output ~/stuff/media \
  --audiobooks-output ~/stuff/audiobooks

# Apply
python3 scripts/organize-media.py ~/stuff/downloads/completed \
  --output ~/stuff/media \
  --audiobooks-output ~/stuff/audiobooks \
  --apply --cleanup-empty
```

### Extension guide — new audiobook pattern

Edit `parse_audiobook_from_label()` in [`organize-media.py`](organize-media.py). Add test paths to [`fixtures/audiobook-paths.txt`](fixtures/audiobook-paths.txt) and run:

```bash
python3 scripts/simulate-from-paths.py scripts/fixtures/audiobook-paths.txt
```

### Jellyfin metadata

Books libraries use **embedded audio tags** (not online scrapers). Requires the **Bookshelf** plugin. Jellyfin Books is optional for browsing; use Audiobookshelf for mobile listening ([plan](../../home-server/docs/AUDIOBOOK-PLATFORM-PLAN.md)). If titles sort under `#` in the sidebar, set **Sort Title** in file tags.

---

## Extension guide (for agents)

### 1. New episode naming pattern

Add to `_EPISODE_PATTERNS` in [`organize-media.py`](organize-media.py). **Order matters** — more specific patterns first.

```python
# Tuple: (compiled_regex, season_group_name_or_None, episode_group_name)
(re.compile(r"...(?P<s>\d{1,2})...(?P<e>\d{1,2})..."), "s", "e"),
```

Rules:

- Use `(?<![0-9A-Za-z])` before `S`/`E` patterns so `x265` does not match as `S02E65`.
- If season is not in the filename, pass `season_group=None`; season falls back to `infer_season_from_path()` then `1`.
- Special cases can be handled **before** the loop in `parse_episode_from_name()` (see `S04 Special` → season 4, episode 0).

After adding a pattern, add a representative path to `paths.txt` or `final_list.txt` and run:

```bash
python3 scripts/simulate-from-paths.py final_list.txt
```

### 2. Show-specific folder naming

Centralize in `canonicalize_show_metadata()`. This is the right place for:

- Consolidating variant names to one folder (`SEAL Team`, `Billions` without per-season years).
- Splitting the same title into distinct shows (`Avatar: The Last Airbender (2005)` vs `(2024)`).
- Fixing casing (`Game of Thrones`).

Current constants at module level:

```python
AVATAR_SHOW = "Avatar: The Last Airbender"
SEAL_TEAM_SHOW = "SEAL Team"
BILLIONS_SHOW = "Billions"
GAME_OF_THRONES_SHOW = "Game of Thrones"
```

Pattern for new rules:

```python
def canonicalize_show_metadata(show, year, path, library_root) -> tuple[str, Optional[int]]:
    blob = path_context_blob(path, library_root)  # path parts + stem

    if re.search(r"\bmyshow\b", show, re.I) or re.search(r"myshow", blob, re.I):
        return "My Show", None  # None year → no "(YYYY)" in folder name

    return show, year
```

Use `path_context_blob()` (not just the filename) when the folder name carries disambiguating info.

### 3. Title / show inference edge cases

`parse_tv_file()` has **explicit branches** for formats that don't fit the generic prefix/suffix split:

| Pattern | Example | Branch |
|---------|---------|--------|
| House MD | `House MD - 19 - Kids` | `\s-\s\d{1,2}\s-\s` |
| Firefly | `10 Firefly Trash - Episode 10` | `Episode N` in stem |
| Firefly pilot | `00 Firefly Serenity - Pilot Film` | `^00\s+` |
| Billions | `BILLIONS - S01 E08 - Title` | spaced `S01 E08` |
| Phineas special | `... - S04 Special` | pre-loop in `parse_episode_from_name` |

Add a new `elif` branch in `parse_tv_file()` when a format needs custom title extraction — don't overload generic prefix logic.

### 4. TV extras (`Extras/`)

- `TV_EXTRAS_PATH_PARTS = {"extras"}` — **not** skipped.
- Files with episode codes → normal `parse_tv_file()`.
- Files without episode codes → `parse_tv_extras_file()` → `Season 00/S00E01` (specials).
- `parse_movie_file()` raises for paths under `Extras/` so TV wins in `auto` mode.

### 5. Movie bonus content (`Featurettes/`, `Behind The Story/`)

- `MOVIE_BONUS_PATH_PARTS` — traversed, not skipped.
- `parse_movie_file()` walks up past bonus folders to find the movie root folder.
- `MovieInfo.relative_path` preserves subfolders under the movie directory.

### 6. Skip rules

| Function | Skips |
|----------|-------|
| `should_skip_path()` | Structural dirs: `subs`, `screens`, `tv`, `shows`, `movies`, etc. |
| `should_skip_file()` | `._*`, `sample`, `RARBG.com.mp4`, `ETRG.mp4` |

**Do not** add `extras` or `featurettes` back to `SKIP_PATH_PARTS`.

`infer_show_from_path()` ignores both `SKIP_PATH_PARTS` and `BONUS_PATH_PARTS` when walking ancestors.

### 7. Subtitles

`find_subtitle_sidecars()` searches:

- Siblings in the video's directory.
- `Subs/`, `Subtitles/`, and any folder containing `subtitle` in the name under ancestor directories.

Matching uses `subtitle_matches_video()` — episode code in the subtitle name (`S01E01`, `S02 E07`), `Episode N`, PBS-style `EpN`, Firefly-style numbers, or an exact stem match with the video. **Sibling `.srt` files in the same folder are filtered by episode** (fixes flat-season rips like Billions where every subtitle would otherwise attach to every episode).

Renamed to `S01E01 - Title.en.srt`, with numeric suffixes (`.en1.srt`) on collisions.

---

## Coding conventions

- **Standard library only** — no third-party dependencies.
- **Python 3.10+** — `from __future__ import annotations`, `dataclass`, `Path`.
- **Regex-first parsing** — keep patterns declarative at module top; document false-positive risks.
- **Dry-run default** — destructive behavior requires explicit `--apply`.
- **Minimal diffs** — one concern per change (new pattern, new canonicalization rule, or new skip).
- **No silent overwrites** — conflicts log to stderr and drop the duplicate action.
- **Functions do one job** — parse vs plan vs apply are separate; don't fold planning into CLI.
- **Comments** only for non-obvious format rules (release naming quirks), not for self-explanatory code.

### Testing without the ThinkPad

```bash
# Full library regression (from final_list.txt)
python3 scripts/simulate-from-paths.py final_list.txt --save-plan /tmp/plan.json

# Smaller samples
python3 scripts/simulate-from-paths.py paths.txt
python3 scripts/simulate-from-paths.py paths2.txt
```

Generate a new `final_list.txt` on the ThinkPad when the on-disk library changes materially:

```bash
find ~/stuff/media -print | sort > ~/final_list.txt
```

Expect **0 conflicts** and review **skipped** / **unclassified** counts before recommending `--apply`.

### Regenerating `FINAL_PLAN.md`

After script changes, re-run simulation and update the review doc (or ask an agent to regenerate from `final_list.txt`). The plan should reflect:

- ~520 video moves, ~1015 subtitle moves for the current library
- 6 skipped junk/sample files
- 0 conflicts

---

## Show-specific rules (current)

| Show | Rule |
|------|------|
| Avatar | `(2005)` in path → animated; otherwise → `(2024)` live-action |
| SEAL Team | All `Seal Team` / `SEAL Team` → `SEAL Team` |
| Billions | All seasons → `Billions` (no `(2016)` etc.) |
| Game of Thrones | Lowercase/dotted filenames → `Game of Thrones` |
| Firefly | `00 ... Pilot` → `S01E00` |
| Phineas and Ferb | `S04 Special` → `S04E00`; `Extras/` without ep code → `S00E01` |
| Blood Diamond | Featurettes stay under `Blood Diamond (2006)/Featurettes/` |

---

## Jellyfin after organizing

1. Dashboard → Libraries → **Scan** the `/media` library (video). Scan `/audiobooks` only if you still use Jellyfin Books for browsing.
2. If folders were consolidated (Billions, SEAL Team, Avatar), you may need to remove duplicate/old library entries or let Jellyfin merge on scan.
3. Fix any remaining metadata mismatches in the Jellyfin UI — this script only handles paths and filenames.
