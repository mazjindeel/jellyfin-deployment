#!/usr/bin/env python3
"""
Organize TV shows, movies, and audiobooks for Jellyfin / Plex.

Default behavior is dry-run: prints planned moves/renames without touching files.
Use --apply to execute. Always review the plan first.

Recommended layout (what this script targets):

  TV/
    The Office (2005)/
      Season 01/
        S01E01 - Pilot.mkv

  Movies/
    The Matrix (1999)/
      The Matrix (1999).mkv

  Audiobooks (Author/Book at library root — no extra Audiobooks/ tier):
    Matt Dinniman/
      Dungeon Crawler Carl/
        Dungeon Crawler Carl.m4b

Episode filenames drop the show name (redundant inside the show folder) but keep
SxxEyy so Jellyfin/Plex can match metadata reliably. Use --minimal-names for
E01-style names when you prefer shorter files inside season folders.

Documentation: scripts/README.md (usage, architecture, extension guide).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".wmv", ".mov", ".ts", ".m2ts"}
AUDIOBOOK_EXTENSIONS = {".m4b", ".mp3", ".m4a", ".aac", ".flac", ".opus"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}
COVER_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
COVER_STEMS = frozenset({"cover", "folder", "poster"})
AUDIOBOOK_JUNK_PARENS_RE = re.compile(
    r"\s*\((?:Audiobook|Fiction|Unabridged|Abridged|Sci-Fi|Fantasy|Horror|Non-Fiction)\)\s*",
    re.IGNORECASE,
)
SERIES_INDEX_PARENS_RE = re.compile(r"\s*\([^)]*\d+\)\s*$")
BY_AUTHOR_RE = re.compile(r"\s+by\s+", re.IGNORECASE)

# TV episode patterns — explicit only (no bare NNN; avoids x265/H264 false positives).
# Order matters: more specific patterns are tried first.
_EPISODE_PATTERNS: tuple[tuple[re.Pattern[str], Optional[str], str], ...] = (
    # S01E01, Season 1 Episode 1
    (
        re.compile(
            r"(?<![0-9A-Za-z])[Ss](?:eason\s*)?(?P<s>\d{1,2})[Ee](?:pisode\s*)?(?P<e>\d{1,3})",
            re.IGNORECASE,
        ),
        "s",
        "e",
    ),
    # S01 E08 (spaced — Billions rips)
    (
        re.compile(
            r"(?<![0-9A-Za-z])[Ss](?P<s>\d{1,2})\s+[Ee]\s*(?P<e>\d{1,2})\b",
            re.IGNORECASE,
        ),
        "s",
        "e",
    ),
    # 1x01
    (
        re.compile(r"(?<![0-9A-Za-z])(?P<s>\d{1,2})[xX](?P<e>\d{1,3})"),
        "s",
        "e",
    ),
    # Ep1, Ep.2 (PBS miniseries)
    (
        re.compile(r"(?:^|[\.\s_-])[Ee]p\.?(?P<e>\d{1,2})(?:[\.\s_-]|$)"),
        None,
        "e",
    ),
    # Episode 10 (Firefly-style)
    (
        re.compile(r"[Ee]pisode\s+(?P<e>\d{1,2})\b"),
        None,
        "e",
    ),
    # 00 prefix — Firefly pilot / specials (e.g. "00 Firefly Serenity - Pilot Film")
    (
        re.compile(r"^(?P<e>00)\s+"),
        None,
        "e",
    ),
    # House MD - 19 - Title
    (
        re.compile(r"\s-\s(?P<e>\d{1,2})\s-\s"),
        None,
        "e",
    ),
)

AVATAR_SHOW = "Avatar: The Last Airbender"
SEAL_TEAM_SHOW = "SEAL Team"
BILLIONS_SHOW = "Billions"
GAME_OF_THRONES_SHOW = "Game of Thrones"
KORRA_SHOW = "The Legend of Korra"

SEASON_IN_PATH_RE = re.compile(r"season\s*(\d{1,2})", re.IGNORECASE)
SEASON_FOLDER_RE = re.compile(r"(?:^|[\.\s_-])[Ss](\d{1,2})(?:[\.\s_-]|$)")

JUNK_STEM_EXACT = frozenset({"rarbg.com", "etrg", "rarbg"})

YEAR_RE = re.compile(r"\((\d{4})\)")
YEAR_DOTTED_RE = re.compile(r"(?:^|[\.\s_-])(\d{4})(?:[\.\s_-]|$)")
YEAR_BOUNDARY_RE = re.compile(r"(?:^|[\s\.\-_])(20\d{2})(?:[\s\.\-_\[)\]]|$)")

PAREN_QUALITY_RE = re.compile(
    r"\s*\([^)]*(?:1080p|720p|2160p|480p|4K|UHD|WEB-?DL|BluRay|x26[45]|HEVC|RCVR|Silence)[^)]*\)\s*$",
    re.IGNORECASE,
)
QUALITY_RE = re.compile(
    r"[\.\s_-]+(?:2160p|1080p|720p|480p|4K|UHD|BluRay|WEB-?DL|WEBRip|HDR|HEVC|x264|x265|H\.?264|H\.?265|AAC|DDP?\d(?:\.\d)?|Atmos|REMUX|PROPER|REPACK|Silence|NeoNoir|amiable|dvdrip|xvid|divx\d*).*$",
    re.IGNORECASE,
)
BRACKET_TAG_RE = re.compile(r"\s*\[[^\]]+\]")
JUNK_RE = re.compile(r"[\._]+")
MULTI_EP_TITLE_RE = re.compile(r"^E\d{1,3}\s*-\s*", re.IGNORECASE)

SKIP_PATH_PARTS = {
    "tv",
    "shows",
    "series",
    "movies",
    "film",
    "films",
    "subs",
    "screens",
}

# Bonus-content folders — traversed, not skipped.
TV_EXTRAS_PATH_PARTS = frozenset({"extras"})
MOVIE_BONUS_PATH_PARTS = frozenset({"featurettes", "behind the story"})
BONUS_PATH_PARTS = TV_EXTRAS_PATH_PARTS | MOVIE_BONUS_PATH_PARTS

LANGUAGE_HINTS = (
    (re.compile(r"english", re.I), "en"),
    (re.compile(r"\ben\b", re.I), "en"),
    (re.compile(r"\btr\b", re.I), "tr"),
)


@dataclass(frozen=True)
class EpisodeInfo:
    show: str
    season: int
    episode: int
    title: str
    year: Optional[int] = None


@dataclass(frozen=True)
class PlannedAction:
    kind: str  # move | subtitle
    source: Path
    destination: Path
    reason: str


@dataclass(frozen=True)
class SkippedItem:
    path: Path
    reason: str


def strip_quality_tags(text: str) -> str:
    text = PAREN_QUALITY_RE.sub("", text)
    text = BRACKET_TAG_RE.sub("", text)
    text = QUALITY_RE.sub("", text)
    return text


def clean_title(text: str) -> str:
    text = strip_quality_tags(text)
    text = text.replace(".", " ")
    text = JUNK_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    return text


def extract_year(text: str) -> tuple[str, Optional[int]]:
    match = YEAR_RE.search(text)
    if match:
        year = int(match.group(1))
        stripped = (text[: match.start()] + text[match.end() :]).strip(" -_")
        return stripped, year

    match = YEAR_DOTTED_RE.search(text)
    if match:
        year = int(match.group(1))
        before = text[: match.start()].rstrip("._- ")
        after = text[match.end() :].lstrip("._- ")
        stripped = f"{before} {after}".strip() if before and after else (before or after)
        return stripped, year

    match = YEAR_BOUNDARY_RE.search(text)
    if match:
        year = int(match.group(1))
        stripped = (text[: match.start()] + text[match.end() :]).strip(" -_")
        return stripped, year

    return text, None


def parse_episode_from_name(
    name: str,
    *,
    season_hint: Optional[int] = None,
) -> Optional[tuple[int, int, int, int]]:
    """Return (match_start, match_end, season, episode)."""
    special = re.search(r"[Ss](?P<s>\d{1,2})\s+Special\b", name)
    if special:
        season = int(special.group("s"))
        return special.start(), special.end(), season, 0

    for pattern, season_group, episode_group in _EPISODE_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        episode = int(match.group(episode_group))
        if season_group and match.group(season_group):
            season = int(match.group(season_group))
        else:
            season = season_hint if season_hint is not None else 1
        return match.start(), match.end(), season, episode
    return None


def infer_season_from_path(path: Path, library_root: Path) -> Optional[int]:
    for part in path.relative_to(library_root).parts[:-1]:
        match = SEASON_IN_PATH_RE.search(part)
        if match:
            return int(match.group(1))
        match = SEASON_FOLDER_RE.search(part)
        if match:
            return int(match.group(1))
    return None


def should_skip_file(path: Path) -> Optional[str]:
    stem = path.stem
    lowered = stem.lower()
    if stem.startswith("._"):
        return "macOS metadata file"
    if lowered in JUNK_STEM_EXACT:
        return "release group junk file"
    if re.search(r"\bsample\b", lowered):
        return "sample/preview file"
    return None


def normalize_show_folder_name(name: str) -> str:
    name = clean_title(name)
    name = re.sub(r"\s*-\s*Complete\s+Season\s+\d+.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+Season\s+\d+.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+Complete\s+Series.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+S\d{1,2}\b.*$", "", name, flags=re.IGNORECASE)
    return name.strip()


def path_context_blob(path: Path, library_root: Path) -> str:
    rel = path.relative_to(library_root)
    return " ".join(rel.parts) + " " + path.stem


def is_avatar_content(show: str, blob: str) -> bool:
    blob_l = blob.lower()
    show_l = show.lower()
    if "airbender" not in blob_l and "airbender" not in show_l:
        return False
    return (
        "avatar" in blob_l
        or "avatar" in show_l
        or "legend of aang" in blob_l
    )


def is_avatar_animated_2005(blob: str) -> bool:
    """Animated series — folders/files explicitly tagged (2005)."""
    if "(2005)" in blob:
        return True
    return bool(
        re.search(r"\b2005\b", blob, re.IGNORECASE)
        and re.search(r"repack|season\s*1-3|rcvr", blob, re.IGNORECASE)
    )


def canonicalize_show_metadata(
    show: str,
    year: Optional[int],
    path: Path,
    library_root: Path,
) -> tuple[str, Optional[int]]:
    blob = path_context_blob(path, library_root)

    if re.search(r"\bseal\s*team\b", show, re.IGNORECASE) or re.search(
        r"seal[\s._-]*team", blob, re.IGNORECASE
    ):
        return SEAL_TEAM_SHOW, year

    if is_avatar_content(show, blob):
        if is_avatar_animated_2005(blob):
            return AVATAR_SHOW, 2005
        return AVATAR_SHOW, 2024

    if re.search(r"\bbillions\b", show, re.IGNORECASE) or re.search(
        r"\bbillions\b", blob, re.IGNORECASE
    ):
        return BILLIONS_SHOW, None

    if re.search(r"\bgame\s+of\s+thrones\b", show, re.IGNORECASE) or re.search(
        r"game[\s._-]*of[\s._-]*thrones", blob, re.IGNORECASE
    ):
        return GAME_OF_THRONES_SHOW, None

    if re.search(r"\bthe\s+legend\s+of\s+korra\b", show, re.IGNORECASE) or re.search(
        r"legend[\s._-]*of[\s._-]*korra", blob, re.IGNORECASE
    ):
        return KORRA_SHOW, None

    return show, year


def episode_matches_subtitle(name: str, season: int, episode: int) -> bool:
    lower = name.lower()
    code = episode_code(season, episode).lower()
    if code in lower:
        return True
    # S02 E07 (spaced — Billions rips)
    if re.search(
        rf"(?<![0-9a-z])[Ss]0?{season}\s+[Ee]\s*0?{episode}\b",
        lower,
    ):
        return True
    if re.search(rf"episode\s+0?{episode}\b", lower):
        return True
    if re.search(rf"(?:^|[\s\._-])0?{episode}(?:\s+|\s*-\s*)", lower):
        return True
    if re.search(rf"\s-\s0?{episode}\s-\s", lower):
        return True
    return False


def subtitle_matches_video(
    subtitle_path: Path,
    video_path: Path,
    season: int,
    episode: int,
    *,
    allow_parent_folder_match: bool = False,
) -> bool:
    if episode_matches_subtitle(subtitle_path.name, season, episode):
        return True
    # Only for episode-specific subfolders (e.g. Subs/Show.S02E01/English.srt).
    # Do not use for siblings — season pack folders contain "S01" and false-match ep 1.
    if allow_parent_folder_match and episode_matches_subtitle(
        subtitle_path.parent.name, season, episode
    ):
        return True
    # Same stem as video (e.g. show.mkv + show.srt with no episode in subtitle name)
    return subtitle_path.stem == video_path.stem


def episode_code(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def episode_filename(
    season: int,
    episode: int,
    title: str,
    ext: str,
    *,
    minimal: bool,
    keep_title: bool,
) -> str:
    if minimal:
        code = f"E{episode:02d}"
    else:
        code = f"S{season:02d}E{episode:02d}"
    if keep_title and title:
        return f"{code} - {title}{ext}"
    return f"{code}{ext}"


def show_folder_name(show: str, year: Optional[int]) -> str:
    show = clean_title(show)
    if year and f"({year})" not in show:
        return f"{show} ({year})"
    return show


def movie_folder_name(title: str, year: Optional[int]) -> str:
    title = clean_title(title)
    if year:
        return f"{title} ({year})"
    return title


def should_skip_path(path: Path) -> Optional[str]:
    for part in path.parts:
        if part.lower() in SKIP_PATH_PARTS:
            return f"under '{part}/' (manual review)"
    return None


def is_under_path_parts(path: Path, library_root: Path, parts: frozenset[str]) -> bool:
    rel_parts = path.relative_to(library_root).parts[:-1]
    return any(part.lower() in parts for part in rel_parts)


def bonus_relative_path(path: Path, library_root: Path) -> Optional[Path]:
    rel_parts = path.relative_to(library_root).parts
    for index, part in enumerate(rel_parts[:-1]):
        if part.lower() in BONUS_PATH_PARTS:
            return Path(*rel_parts[index:])
    return None


def content_folder_before_bonus(path: Path, library_root: Path) -> Optional[str]:
    rel_parts = path.relative_to(library_root).parts[:-1]
    for index, part in enumerate(rel_parts):
        if part.lower() in BONUS_PATH_PARTS and index > 0:
            return rel_parts[index - 1]
    return None


def infer_language(filename: str) -> str:
    for pattern, code in LANGUAGE_HINTS:
        if pattern.search(filename):
            return code
    return "en"


def infer_show_from_path(path: Path, library_root: Path) -> tuple[str, Optional[int]]:
    """Use parent directory names as hints when the filename lacks a show title."""
    rel_parts = path.relative_to(library_root).parts
    season_hint = infer_season_from_path(path, library_root)
    candidates: list[str] = []
    for part in rel_parts[:-1]:
        lowered = part.lower()
        if lowered in SKIP_PATH_PARTS or lowered in BONUS_PATH_PARTS:
            continue
        if lowered.startswith("season") or re.fullmatch(r"s\d{1,2}", lowered):
            continue
        if parse_episode_from_name(part, season_hint=season_hint):
            continue
        candidates.append(part)

    if not candidates:
        return "Unknown Show", None

    show_part = candidates[-1] if len(candidates) == 1 else candidates[0]
    show_part, year = extract_year(show_part)
    return normalize_show_folder_name(show_part), year


def clean_episode_title(suffix: str) -> str:
    title = clean_title(suffix.strip(" -_"))
    title = MULTI_EP_TITLE_RE.sub("", title)
    return title


def title_from_episode_prefix(prefix: str) -> str:
    """Extract a human title from the pre-episode portion of a filename."""
    text = clean_title(prefix.strip(" -_."))
    text = re.sub(r"^\d{1,2}\s+", "", text)
    text = re.sub(r"\s*-\s*$", "", text)
    return text


def parse_tv_file(path: Path, library_root: Path) -> Optional[EpisodeInfo]:
    if should_skip_path(path) or should_skip_file(path):
        return None

    stem = path.stem
    season_hint = infer_season_from_path(path, library_root)
    parsed = parse_episode_from_name(stem, season_hint=season_hint)
    if not parsed:
        return None

    start, end, season, episode = parsed
    prefix = stem[:start]
    suffix = stem[end:]

    show, year = infer_show_from_path(path, library_root)

    # House MD: "House MD - 19 - Kids" — show from prefix, title from suffix.
    if re.search(r"\s-\s\d{1,2}\s-\s", stem):
        maybe_show = title_from_episode_prefix(prefix)
        if maybe_show:
            maybe_show, year_from_name = extract_year(maybe_show)
            show = maybe_show or show
            year = year_from_name or year
        title = clean_episode_title(suffix)
    # Firefly: "10 Firefly Trash - Episode 10 ..." — title from prefix after disc number.
    elif re.search(r"[Ee]pisode\s+\d", stem):
        title = title_from_episode_prefix(prefix)
        if title.lower() in {show.lower(), "unknown show"}:
            title = ""
    # Firefly pilot: "00 Firefly Serenity - Pilot Film ..."
    elif re.match(r"^00\s+", stem):
        title = clean_episode_title(suffix)
    # Billions: "BILLIONS - S01 E08 - Boasts and Rails"
    elif re.search(r"[Ss]\d{1,2}\s+[Ee]\s*\d", stem):
        title = clean_episode_title(suffix)
        if show.lower() == "unknown show" or show.upper().startswith("BILLIONS"):
            show = "Billions"
    # Phineas specials: "... - The O.W.C.A Files - S04 Special"
    elif re.search(r"[Ss]\d{1,2}\s+Special\b", stem):
        title_part = clean_episode_title(prefix.strip(" -_."))
        title = re.sub(
            rf"^{re.escape(show)}(?:\s*-\s*)?",
            "",
            title_part,
            flags=re.IGNORECASE,
        ).strip(" -_")
        if not title:
            title = title_part
    else:
        if prefix.strip(" -_."):
            maybe_show = title_from_episode_prefix(prefix)
            # Dotted prefixes before EpN (e.g. PBS.Commanding.Heights.Ep1) are not show names.
            ep_only = bool(re.search(r"(?:^|[\.\s_-])[Ee]p\.?\d{1,2}(?:[\.\s_-]|$)", stem))
            if maybe_show and maybe_show.lower() != "unknown show" and not ep_only:
                maybe_show, year_from_name = extract_year(maybe_show)
                if len(maybe_show) > 3:
                    show = maybe_show or show
                    year = year_from_name or year
        title = clean_episode_title(suffix)

    if show.lower() == "unknown show":
        return None

    show, year = canonicalize_show_metadata(show, year, path, library_root)
    return EpisodeInfo(show=show, season=season, episode=episode, title=title, year=year)


def parse_tv_extras_file(path: Path, library_root: Path) -> Optional[EpisodeInfo]:
    if not is_under_path_parts(path, library_root, TV_EXTRAS_PATH_PARTS):
        return None

    show, year = infer_show_from_path(path, library_root)
    if show.lower() == "unknown show":
        return None

    stem = path.stem
    title = clean_episode_title(stem)
    show_prefix = re.compile(rf"^{re.escape(show)}(?:\s*-\s*)?", re.IGNORECASE)
    title = show_prefix.sub("", title).strip(" -_")
    if not title:
        title = clean_episode_title(stem)

    return EpisodeInfo(show=show, season=0, episode=1, title=title, year=year)


def movie_title_from_folder(folder_name: str) -> tuple[str, Optional[int]]:
    title, year = extract_year(folder_name)
    title = clean_title(title)
    return title, year


@dataclass(frozen=True)
class MovieInfo:
    title: str
    year: Optional[int]
    relative_path: Optional[Path] = None


@dataclass(frozen=True)
class AudiobookInfo:
    author: str
    title: str


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_audiobook_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIOBOOK_EXTENSIONS


def release_unit_for(path: Path, source_root: Path) -> Path:
    """Each immediate child of source_root (file or folder) is one release unit."""
    rel = path.relative_to(source_root)
    if len(rel.parts) == 1:
        return path
    return source_root / rel.parts[0]


def strip_audiobook_junk(text: str) -> str:
    text = AUDIOBOOK_JUNK_PARENS_RE.sub(" ", text)
    text = BRACKET_TAG_RE.sub("", text)
    text = strip_quality_tags(text)
    text = clean_title(text)
    return text.strip()


def normalize_audiobook_title(title: str) -> str:
    title = strip_audiobook_junk(title)
    title = SERIES_INDEX_PARENS_RE.sub("", title).strip()
    if title.lower().startswith("carls "):
        title = "Carl's " + title[6:]
    return title.strip()


def normalize_audiobook_author(author: str) -> str:
    author = strip_audiobook_junk(author)
    return author.strip()


def parse_audiobook_from_label(label: str) -> Optional[AudiobookInfo]:
    """Parse author/title from a release folder name or filename stem."""
    text = label.strip()
    if Path(text).suffix.lower() in AUDIOBOOK_EXTENSIONS:
        text = Path(text).stem
    if not text:
        return None

    by_parts = BY_AUTHOR_RE.split(text)
    if len(by_parts) >= 2:
        title = normalize_audiobook_title(by_parts[0])
        author = normalize_audiobook_author(by_parts[-1])
        if title and author:
            return AudiobookInfo(author=author, title=title)

    dash_parts = [part.strip() for part in text.split(" - ")]
    if len(dash_parts) >= 3 and re.fullmatch(r"\d{4}", dash_parts[1]):
        author = normalize_audiobook_author(dash_parts[0])
        title = normalize_audiobook_title(" - ".join(dash_parts[2:]))
        if author and title:
            return AudiobookInfo(author=author, title=title)

    if len(dash_parts) == 2:
        author = normalize_audiobook_author(dash_parts[0])
        title = normalize_audiobook_title(dash_parts[1])
        if author and title:
            return AudiobookInfo(author=author, title=title)

    title = normalize_audiobook_title(text)
    if title:
        return AudiobookInfo(author="Unknown Author", title=title)
    return None


def parse_audiobook_release(unit: Path, source_root: Path) -> Optional[AudiobookInfo]:
    if unit.is_file():
        return parse_audiobook_from_label(unit.stem)

    rel_parts = unit.relative_to(source_root).parts
    if len(rel_parts) >= 3:
        author = normalize_audiobook_author(rel_parts[0])
        title = normalize_audiobook_title(rel_parts[1])
        if author and title:
            return AudiobookInfo(author=author, title=title)

    return parse_audiobook_from_label(unit.name)


def audiobook_folder_name(name: str) -> str:
    return strip_audiobook_junk(name)


def destination_for_audiobook(
    output_root: Path,
    info: AudiobookInfo,
    filename: str,
) -> Path:
    author_dir = audiobook_folder_name(info.author)
    title_dir = audiobook_folder_name(info.title)
    return output_root / author_dir / title_dir / filename


def iter_audiobook_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and is_audiobook_file(path):
            yield path


def iter_video_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and is_video_file(path):
            yield path


def is_cover_sidecar(path: Path) -> bool:
    if path.suffix.lower() not in COVER_IMAGE_EXTENSIONS:
        return False
    return path.stem.lower() in COVER_STEMS


def iter_cover_sidecars(unit: Path, source_root: Path, catalog: Optional[list[Path]]) -> list[Path]:
    if catalog is not None:
        candidates = [
            p
            for p in catalog
            if is_cover_sidecar(p)
            and release_unit_for(p, source_root) == unit
        ]
        return sorted(set(candidates))

    if unit.is_file():
        search_root = unit.parent
    else:
        search_root = unit
    return sorted(
        p for p in search_root.rglob("*") if p.is_file() and is_cover_sidecar(p)
    )


def plan_audiobook_actions(
    files: Iterable[Path],
    output_root: Path,
    source_root: Path,
    catalog: Optional[list[Path]] = None,
) -> tuple[list[PlannedAction], list[SkippedItem], list[Path]]:
    actions: list[PlannedAction] = []
    skipped: list[SkippedItem] = []
    unclassified: list[Path] = []

    units: dict[Path, list[Path]] = {}
    for path in files:
        unit = release_unit_for(path, source_root)
        units.setdefault(unit, []).append(path)

    classified_audio: set[Path] = set()
    for unit, unit_files in sorted(units.items(), key=lambda item: str(item[0])):
        info = parse_audiobook_release(unit, source_root)
        if not info:
            unclassified.extend(unit_files)
            continue

        book_dir = output_root / audiobook_folder_name(info.author) / audiobook_folder_name(info.title)
        for path in unit_files:
            skip_reason = should_skip_file(path)
            if skip_reason:
                skipped.append(SkippedItem(path=path, reason=skip_reason))
                continue

            dest = book_dir / path.name
            if path.resolve() == dest.resolve():
                classified_audio.add(path)
                continue

            actions.append(
                PlannedAction(
                    kind="audiobook",
                    source=path,
                    destination=dest,
                    reason=f"{info.author} / {info.title}",
                )
            )
            classified_audio.add(path)

        for cover in iter_cover_sidecars(unit, source_root, catalog):
            dest = book_dir / cover.name
            if cover.resolve() == dest.resolve():
                continue
            if any(a.source == cover for a in actions):
                continue
            actions.append(
                PlannedAction(
                    kind="cover",
                    source=cover,
                    destination=dest,
                    reason=f"{info.author} / {info.title} (cover)",
                )
            )

    for path in files:
        if path not in classified_audio and path not in {s.path for s in skipped}:
            if path not in unclassified:
                unclassified.append(path)

    return actions, skipped, unclassified


def build_audiobook_plan(
    files: list[Path],
    source_root: Path,
    output_root: Path,
    catalog: Optional[list[Path]] = None,
) -> tuple[list[PlannedAction], list[SkippedItem], list[Path]]:
    actions, skipped, unclassified = plan_audiobook_actions(
        files, output_root, source_root, catalog
    )
    return dedupe_actions(actions), skipped, unclassified


def parse_movie_file(path: Path, library_root: Path) -> MovieInfo:
    if should_skip_path(path):
        raise ValueError("manual-review content")

    if should_skip_file(path):
        raise ValueError("junk file")

    stem = path.stem
    season_hint = infer_season_from_path(path, library_root)
    if parse_episode_from_name(stem, season_hint=season_hint):
        raise ValueError("episode pattern in movie candidate")

    if is_under_path_parts(path, library_root, TV_EXTRAS_PATH_PARTS):
        raise ValueError("tv extras content")

    rel_parts = path.relative_to(library_root).parts
    bonus_path = bonus_relative_path(path, library_root)
    if bonus_path and is_under_path_parts(path, library_root, MOVIE_BONUS_PATH_PARTS):
        folder_part = content_folder_before_bonus(path, library_root)
        if not folder_part:
            raise ValueError("no movie folder for bonus content")
        title, year = movie_title_from_folder(folder_part)
        return MovieInfo(title=title, year=year, relative_path=bonus_path)

    title: Optional[str] = None
    year: Optional[int] = None

    if len(rel_parts) > 1:
        parent = rel_parts[-2]
        if not parent.lower().startswith("season"):
            folder_title, folder_year = movie_title_from_folder(parent)
            title, year = folder_title, folder_year

    stem_title, stem_year = extract_year(clean_title(stem))
    if not title:
        title, year = stem_title, stem_year
    elif not year:
        year = stem_year

    if not title:
        raise ValueError("no movie title")

    return MovieInfo(title=title, year=year)


def destination_for_episode(
    library_root: Path,
    info: EpisodeInfo,
    ext: str,
    *,
    minimal_names: bool,
    keep_titles: bool,
) -> Path:
    show_dir = library_root / show_folder_name(info.show, info.year)
    season_dir = show_dir / f"Season {info.season:02d}"
    filename = episode_filename(
        info.season,
        info.episode,
        info.title,
        ext,
        minimal=minimal_names,
        keep_title=keep_titles,
    )
    return season_dir / filename


def destination_for_movie(
    library_root: Path,
    info: MovieInfo,
    ext: str,
) -> Path:
    folder = movie_folder_name(info.title, info.year)
    if info.relative_path is not None:
        return library_root / folder / info.relative_path
    return library_root / folder / f"{folder}{ext}"


def _iter_paths_under(parent: Path, catalog: Optional[list[Path]]) -> Iterator[Path]:
    if catalog is None:
        if not parent.exists():
            return
        yield from parent.rglob("*")
        return
    prefix = str(parent)
    if not prefix.endswith("/"):
        prefix += "/"
    for path in catalog:
        path_str = str(path)
        if path_str.startswith(prefix):
            yield path


def _siblings_of(path: Path, catalog: Optional[list[Path]]) -> list[Path]:
    if catalog is None:
        if not path.parent.exists():
            return []
        return list(path.parent.iterdir())
    return [p for p in catalog if p.parent == path.parent]


def _subtitle_search_roots(video_path: Path, source_root: Path) -> list[Path]:
    roots: list[Path] = []

    def add_subtitle_dirs(parent: Path) -> None:
        for child in ("Subs", "Subtitles"):
            candidate = parent / child
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
        if parent.exists():
            for entry in parent.iterdir():
                if (
                    entry.is_dir()
                    and "subtitle" in entry.name.lower()
                    and entry not in roots
                ):
                    roots.append(entry)

    for parent in video_path.parents:
        if parent == source_root:
            add_subtitle_dirs(parent)
            break
        if parent == source_root.parent:
            break
        add_subtitle_dirs(parent)
    return roots


def find_subtitle_sidecars(
    video_path: Path,
    season: int,
    episode: int,
    source_root: Path,
    catalog: Optional[list[Path]] = None,
) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(candidate: Path) -> None:
        key = str(candidate)
        if key not in seen and candidate.suffix.lower() in SUBTITLE_EXTENSIONS:
            seen.add(key)
            found.append(candidate)

    for sibling in _siblings_of(video_path, catalog):
        if sibling.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        if subtitle_matches_video(
            sibling, video_path, season, episode, allow_parent_folder_match=False
        ):
            add(sibling)

    if catalog is None:
        search_roots = _subtitle_search_roots(video_path, source_root)
    else:
        search_roots = []
        for parent in video_path.parents:
            if parent in {source_root, source_root.parent}:
                break
            prefix = str(parent)
            if not prefix.endswith("/"):
                prefix += "/"
            for path in catalog:
                path_str = str(path)
                if not path_str.startswith(prefix):
                    continue
                rel = path_str[len(prefix) :]
                top = rel.split("/")[0] if "/" in rel else rel
                if top.lower() in {"subs", "subtitles"} or "subtitle" in top.lower():
                    root = parent / top
                    if root not in search_roots:
                        search_roots.append(root)

    for subs_root in search_roots:
        for sub_file in _iter_paths_under(subs_root, catalog):
            if sub_file.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            if subtitle_matches_video(
                sub_file, video_path, season, episode, allow_parent_folder_match=True
            ):
                add(sub_file)

    return found


def subtitle_destination(video_destination: Path, subtitle_path: Path, index: int) -> Path:
    lang = infer_language(subtitle_path.name)
    stem = video_destination.stem
    suffix = subtitle_path.suffix
    if index == 0:
        return video_destination.with_name(f"{stem}.{lang}{suffix}")
    return video_destination.with_name(f"{stem}.{lang}{index}{suffix}")


def plan_subtitle_actions(
    video_action: PlannedAction,
    season: int,
    episode: int,
    source_root: Path,
    catalog: Optional[list[Path]] = None,
) -> list[PlannedAction]:
    sidecars = find_subtitle_sidecars(
        video_action.source, season, episode, source_root, catalog
    )
    actions: list[PlannedAction] = []
    for index, sidecar in enumerate(sidecars):
        dest = subtitle_destination(video_action.destination, sidecar, index)
        if sidecar.resolve() == dest.resolve():
            continue
        actions.append(
            PlannedAction(
                kind="subtitle",
                source=sidecar,
                destination=dest,
                reason=f"sidecar for {video_action.destination.name}",
            )
        )
    return actions


def plan_movie_subtitle_actions(
    video_action: PlannedAction,
    catalog: Optional[list[Path]] = None,
) -> list[PlannedAction]:
    actions: list[PlannedAction] = []
    sidecars = [
        p for p in _siblings_of(video_action.source, catalog) if p.suffix.lower() in SUBTITLE_EXTENSIONS
    ]
    for index, sidecar in enumerate(sidecars):
        dest = subtitle_destination(video_action.destination, sidecar, index)
        if sidecar.resolve() == dest.resolve():
            continue
        actions.append(
            PlannedAction(
                kind="subtitle",
                source=sidecar,
                destination=dest,
                reason=f"sidecar for {video_action.destination.name}",
            )
        )
    return actions


def plan_tv_actions(
    files: Iterable[Path],
    output_root: Path,
    source_root: Path,
    *,
    minimal_names: bool,
    keep_titles: bool,
    catalog: Optional[list[Path]] = None,
) -> tuple[list[PlannedAction], list[SkippedItem]]:
    actions: list[PlannedAction] = []
    skipped: list[SkippedItem] = []
    for path in files:
        skip_reason = should_skip_path(path) or should_skip_file(path)
        if skip_reason:
            skipped.append(SkippedItem(path=path, reason=skip_reason))
            continue

        info = parse_tv_file(path, source_root) or parse_tv_extras_file(path, source_root)
        if not info:
            continue

        dest = destination_for_episode(
            output_root,
            info,
            path.suffix,
            minimal_names=minimal_names,
            keep_titles=keep_titles,
        )
        if path.resolve() == dest.resolve():
            continue

        video_action = PlannedAction(
            kind="move",
            source=path,
            destination=dest,
            reason=f"{info.show} {episode_code(info.season, info.episode)}",
        )
        actions.append(video_action)
        actions.extend(
            plan_subtitle_actions(
                video_action, info.season, info.episode, source_root, catalog
            )
        )
    return actions, skipped


def plan_movie_actions(
    files: Iterable[Path],
    output_root: Path,
    source_root: Path,
    catalog: Optional[list[Path]] = None,
) -> tuple[list[PlannedAction], list[SkippedItem]]:
    actions: list[PlannedAction] = []
    skipped: list[SkippedItem] = []
    for path in files:
        skip_reason = should_skip_path(path) or should_skip_file(path)
        if skip_reason:
            skipped.append(SkippedItem(path=path, reason=skip_reason))
            continue

        try:
            info = parse_movie_file(path, source_root)
        except ValueError:
            continue

        dest = destination_for_movie(output_root, info, path.suffix)
        if path.resolve() == dest.resolve():
            continue

        video_action = PlannedAction(
            kind="move",
            source=path,
            destination=dest,
            reason=dest.parent.name,
        )
        actions.append(video_action)
        actions.extend(plan_movie_subtitle_actions(video_action, catalog))
    return actions, skipped


def build_plan(
    files: list[Path],
    source_root: Path,
    output_root: Path,
    *,
    library_type: str = "auto",
    minimal_names: bool = False,
    keep_titles: bool = True,
    catalog: Optional[list[Path]] = None,
) -> tuple[list[PlannedAction], list[SkippedItem], list[Path]]:
    actions: list[PlannedAction] = []
    skipped: list[SkippedItem] = []
    unclassified: list[Path] = []

    if library_type in ("tv", "auto"):
        tv_actions, tv_skipped = plan_tv_actions(
            files,
            output_root,
            source_root,
            minimal_names=minimal_names,
            keep_titles=keep_titles,
            catalog=catalog,
        )
        actions.extend(tv_actions)
        skipped.extend(tv_skipped)

    if library_type in ("movies", "auto"):
        tv_sources = {a.source for a in actions}
        movie_files = [f for f in files if f not in tv_sources]
        movie_actions, movie_skipped = plan_movie_actions(
            movie_files, output_root, source_root, catalog
        )
        actions.extend(movie_actions)
        skipped.extend(movie_skipped)

    seen_skips: dict[Path, SkippedItem] = {item.path: item for item in skipped}
    skipped = list(seen_skips.values())

    classified = {a.source for a in actions} | {s.path for s in skipped}
    for path in files:
        if path not in classified:
            unclassified.append(path)

    return dedupe_actions(actions), skipped, unclassified


def dedupe_actions(actions: list[PlannedAction]) -> list[PlannedAction]:
    seen_dest: dict[Path, PlannedAction] = {}
    seen_source: set[Path] = set()
    for action in actions:
        if action.source in seen_source:
            print(
                f"CONFLICT: source listed twice, skipping duplicate\n"
                f"  {action.source} -> {action.destination}",
                file=sys.stderr,
            )
            continue
        if action.destination in seen_dest:
            existing = seen_dest[action.destination]
            print(
                f"CONFLICT: two files want {action.destination}\n"
                f"  1) {existing.source}\n"
                f"  2) {action.source}",
                file=sys.stderr,
            )
            continue
        seen_source.add(action.source)
        seen_dest[action.destination] = action
    return list(seen_dest.values())


def apply_actions(actions: list[PlannedAction]) -> None:
    for action in actions:
        if not action.source.exists():
            print(f"SKIP (already moved or missing): {action.source}", file=sys.stderr)
            continue
        action.destination.parent.mkdir(parents=True, exist_ok=True)
        if action.destination.exists():
            raise FileExistsError(f"Refusing to overwrite existing file: {action.destination}")
        if action.kind == "subtitle":
            label = "SUB "
        elif action.kind in ("audiobook", "cover"):
            label = "BOOK"
        else:
            label = "MOVE"
        print(f"{label} {action.source} -> {action.destination}")
        shutil.move(str(action.source), str(action.destination))


def cleanup_empty_dirs(root: Path) -> list[Path]:
    removed: list[Path] = []
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            removed.append(path)
    return removed


def print_plan_summary(
    actions: list[PlannedAction],
    skipped: list[SkippedItem],
    unclassified: list[Path],
    output_root: Path,
    *,
    audiobooks_output_root: Optional[Path] = None,
    audiobook_actions: Optional[list[PlannedAction]] = None,
    audiobook_skipped: Optional[list[SkippedItem]] = None,
    audiobook_unclassified: Optional[list[Path]] = None,
) -> None:
    video_moves = [a for a in actions if a.kind == "move"]
    subs = [a for a in actions if a.kind == "subtitle"]
    ab_actions = audiobook_actions or []
    ab_moves = [a for a in ab_actions if a.kind == "audiobook"]
    ab_covers = [a for a in ab_actions if a.kind == "cover"]

    print(f"Planned {len(actions)} video change(s) -> {output_root}")
    print(f"  Video moves:    {len(video_moves)}")
    print(f"  Subtitle moves: {len(subs)}")
    if skipped:
        print(f"  Skipped:        {len(skipped)}")
    if unclassified:
        print(f"  Unclassified:   {len(unclassified)}")

    if audiobooks_output_root is not None:
        print(f"\nPlanned {len(ab_actions)} audiobook change(s) -> {audiobooks_output_root}")
        print(f"  Audiobook moves: {len(ab_moves)}")
        print(f"  Cover moves:     {len(ab_covers)}")
        if audiobook_skipped:
            print(f"  Skipped:         {len(audiobook_skipped)}")
        if audiobook_unclassified:
            print(f"  Unclassified:    {len(audiobook_unclassified)}")
    print()

    for action in actions:
        print(f"[{action.kind}] {action.source}")
        print(f"       -> {action.destination}")
        print(f"       ({action.reason})")

    for action in ab_actions:
        print(f"[{action.kind}] {action.source}")
        print(f"       -> {action.destination}")
        print(f"       ({action.reason})")

    if skipped:
        print("\nSkipped video (left untouched):")
        for item in skipped:
            print(f"  {item.path}")
            print(f"    ({item.reason})")

    if audiobook_skipped:
        print("\nSkipped audiobook (left untouched):")
        for item in audiobook_skipped:
            print(f"  {item.path}")
            print(f"    ({item.reason})")

    all_unclassified = list(unclassified)
    if audiobook_unclassified:
        all_unclassified.extend(audiobook_unclassified)
    if all_unclassified:
        print("\nUnclassified (needs manual review):")
        for path in all_unclassified:
            print(f"  {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Directory to scan (messy archive)")
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination video library root (default: source). Use a separate dir for safer first runs.",
    )
    parser.add_argument(
        "--audiobooks-output",
        type=Path,
        help="Destination audiobook library root (Author/Book layout). Required when audiobook files are present.",
    )
    parser.add_argument(
        "--type",
        choices=("tv", "movies", "auto", "audiobooks"),
        default="auto",
        help="Library type to organize (default: auto = video TV/movies + audiobooks when outputs set)",
    )
    parser.add_argument(
        "--minimal-names",
        action="store_true",
        help="Use E01 instead of S01E01 inside season folders",
    )
    parser.add_argument(
        "--drop-titles",
        action="store_true",
        help="Omit episode title from filenames",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute moves (default is dry-run)",
    )
    parser.add_argument(
        "--cleanup-empty",
        action="store_true",
        help="After --apply, remove empty directories under source",
    )
    parser.add_argument(
        "--save-plan",
        type=Path,
        help="Write planned actions as JSON",
    )
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_dir():
        print(f"Source does not exist or is not a directory: {source}", file=sys.stderr)
        return 1

    output = (args.output or source).expanduser().resolve()
    video_files = list(iter_video_files(source))
    audiobook_files = list(iter_audiobook_files(source))

    if not video_files and not audiobook_files:
        print(f"No video or audiobook files found under {source}")
        return 0

    run_video = args.type in ("tv", "movies", "auto") and video_files
    run_audiobooks = args.type in ("audiobooks", "auto") and audiobook_files

    if run_audiobooks and not args.audiobooks_output:
        print(
            "Audiobook files found but --audiobooks-output was not set.\n"
            "Example: --audiobooks-output ~/stuff/audiobooks",
            file=sys.stderr,
        )
        return 1

    video_actions: list[PlannedAction] = []
    video_skipped: list[SkippedItem] = []
    video_unclassified: list[Path] = []
    if run_video:
        video_actions, video_skipped, video_unclassified = build_plan(
            video_files,
            source,
            output,
            library_type=args.type if args.type != "audiobooks" else "auto",
            minimal_names=args.minimal_names,
            keep_titles=not args.drop_titles,
        )

    ab_actions: list[PlannedAction] = []
    ab_skipped: list[SkippedItem] = []
    ab_unclassified: list[Path] = []
    ab_output: Optional[Path] = None
    if run_audiobooks:
        ab_output = args.audiobooks_output.expanduser().resolve()
        ab_actions, ab_skipped, ab_unclassified = build_audiobook_plan(
            audiobook_files,
            source,
            ab_output,
        )

    if (
        not video_actions
        and not video_skipped
        and not video_unclassified
        and not ab_actions
        and not ab_skipped
        and not ab_unclassified
    ):
        print("Nothing to do — files may already match the target layout.")
        return 0

    print_plan_summary(
        video_actions,
        video_skipped,
        video_unclassified,
        output,
        audiobooks_output_root=ab_output,
        audiobook_actions=ab_actions,
        audiobook_skipped=ab_skipped,
        audiobook_unclassified=ab_unclassified,
    )

    all_actions = video_actions + ab_actions
    if args.save_plan:
        payload = [asdict(a) | {"source": str(a.source), "destination": str(a.destination)} for a in all_actions]
        args.save_plan.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote plan to {args.save_plan}")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to execute.")
        return 0

    try:
        apply_actions(all_actions)
    except (FileExistsError, OSError) as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1

    if args.cleanup_empty:
        removed = cleanup_empty_dirs(source)
        for path in removed:
            print(f"Removed empty dir: {path}")

    print("\nDone. Refresh your Jellyfin libraries (Dashboard -> Libraries -> Scan).")
    if run_video and run_audiobooks:
        print("Scan both /media (video) and /audiobooks (books) if configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
