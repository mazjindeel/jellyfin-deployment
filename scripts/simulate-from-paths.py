#!/usr/bin/env python3
"""Dry-run organize-media.py against a paths.txt listing (no real files required)."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter
from pathlib import Path


def load_organize_module():
    script = Path(__file__).resolve().parent / "organize-media.py"
    spec = importlib.util.spec_from_file_location("organize_media", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["organize_media"] = module
    spec.loader.exec_module(module)
    return module


def read_catalog(paths_file: Path, org) -> tuple[list[Path], list[Path], list[Path]]:
    library_root = Path("/MEDIA_ROOT")
    all_paths: list[Path] = []
    video_paths: list[Path] = []
    audiobook_paths: list[Path] = []
    for line in paths_file.read_text().splitlines():
        line = line.strip()
        if not line or line == ".":
            continue
        rel = line[2:] if line.startswith("./") else line
        path = library_root / rel
        all_paths.append(path)
        if org.is_video_file(path):
            video_paths.append(path)
        elif org.is_audiobook_file(path):
            audiobook_paths.append(path)
    return all_paths, video_paths, audiobook_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths_file", type=Path, help="paths.txt from find(1)")
    parser.add_argument("--type", choices=("tv", "movies", "auto", "audiobooks"), default="auto")
    parser.add_argument("--minimal-names", action="store_true")
    parser.add_argument("--drop-titles", action="store_true")
    parser.add_argument(
        "--audiobooks-output",
        type=Path,
        default=Path("/AUDIOBOOKS_ORGANIZED"),
        help="Simulated audiobook destination root",
    )
    parser.add_argument("--save-plan", type=Path)
    args = parser.parse_args()

    org = load_organize_module()
    catalog, video_files, audiobook_files = read_catalog(
        args.paths_file.expanduser().resolve(), org
    )
    if not video_files and not audiobook_files:
        print("No video or audiobook paths found.")
        return 1

    source_root = Path("/MEDIA_ROOT")
    output_root = Path("/MEDIA_ORGANIZED")
    ab_output = args.audiobooks_output.expanduser().resolve()

    video_actions: list = []
    video_skipped: list = []
    video_unclassified: list = []
    if video_files and args.type in ("tv", "movies", "auto"):
        video_actions, video_skipped, video_unclassified = org.build_plan(
            video_files,
            source_root,
            output_root,
            library_type=args.type if args.type != "audiobooks" else "auto",
            minimal_names=args.minimal_names,
            keep_titles=not args.drop_titles,
            catalog=catalog,
        )

    ab_actions: list = []
    ab_skipped: list = []
    ab_unclassified: list = []
    if audiobook_files and args.type in ("audiobooks", "auto"):
        ab_actions, ab_skipped, ab_unclassified = org.build_audiobook_plan(
            audiobook_files,
            source_root,
            ab_output,
            catalog=catalog,
        )

    org.print_plan_summary(
        video_actions,
        video_skipped,
        video_unclassified,
        output_root,
        audiobooks_output_root=ab_output if ab_actions or ab_skipped or ab_unclassified else None,
        audiobook_actions=ab_actions,
        audiobook_skipped=ab_skipped,
        audiobook_unclassified=ab_unclassified,
    )

    moves = [a for a in video_actions if a.kind == "move"]
    shows = Counter()
    movies = []
    for action in moves:
        rel = str(action.destination).replace("/MEDIA_ORGANIZED/", "")
        top = rel.split("/")[0]
        if "/Season " in rel:
            shows[top] += 1
        else:
            movies.append(rel)

    print("\n" + "=" * 60)
    print("VIDEO SUMMARY BY SHOW / MOVIE")
    print("=" * 60)
    for show, count in sorted(shows.items()):
        print(f"  {count:3d} episodes  {show}")
    for movie in sorted(movies):
        print(f"    movie      {movie}")

    ab_moves = [a for a in ab_actions if a.kind == "audiobook"]
    if ab_moves:
        print("\n" + "=" * 60)
        print("AUDIOBOOK SUMMARY")
        print("=" * 60)
        for action in ab_moves:
            rel = str(action.destination).replace(f"{ab_output}/", "")
            print(f"    book       {rel}")

    if args.save_plan:
        import json
        from dataclasses import asdict

        all_actions = video_actions + ab_actions
        payload = [
            asdict(a)
            | {
                "source": str(a.source).replace("/MEDIA_ROOT/", "./"),
                "destination": str(a.destination)
                .replace("/MEDIA_ORGANIZED/", "./")
                .replace(f"{ab_output}/", "./audiobooks/"),
            }
            for a in all_actions
        ]
        args.save_plan.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote plan to {args.save_plan}")

    print("\nSimulation only — no files were modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
