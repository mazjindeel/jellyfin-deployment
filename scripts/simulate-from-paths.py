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


def read_catalog(paths_file: Path) -> tuple[list[Path], list[Path]]:
    library_root = Path("/MEDIA_ROOT")
    all_paths: list[Path] = []
    video_paths: list[Path] = []
    for line in paths_file.read_text().splitlines():
        line = line.strip()
        if not line or line == ".":
            continue
        rel = line[2:] if line.startswith("./") else line
        path = library_root / rel
        all_paths.append(path)
        if Path(rel).suffix.lower() in {".mkv", ".mp4", ".avi", ".m4v", ".wmv", ".mov", ".ts", ".m2ts"}:
            video_paths.append(path)
    return all_paths, video_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths_file", type=Path, help="paths.txt from find(1)")
    parser.add_argument("--type", choices=("tv", "movies", "auto"), default="auto")
    parser.add_argument("--minimal-names", action="store_true")
    parser.add_argument("--drop-titles", action="store_true")
    parser.add_argument("--save-plan", type=Path)
    args = parser.parse_args()

    org = load_organize_module()
    catalog, files = read_catalog(args.paths_file.expanduser().resolve())
    if not files:
        print("No video paths found.")
        return 1

    source_root = Path("/MEDIA_ROOT")
    output_root = Path("/MEDIA_ORGANIZED")

    actions, skipped, unclassified = org.build_plan(
        files,
        source_root,
        output_root,
        library_type=args.type,
        minimal_names=args.minimal_names,
        keep_titles=not args.drop_titles,
        catalog=catalog,
    )

    org.print_plan_summary(actions, skipped, unclassified, output_root)

    moves = [a for a in actions if a.kind == "move"]
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
    print("SUMMARY BY SHOW / MOVIE")
    print("=" * 60)
    for show, count in sorted(shows.items()):
        print(f"  {count:3d} episodes  {show}")
    for movie in sorted(movies):
        print(f"    movie      {movie}")

    if args.save_plan:
        import json
        from dataclasses import asdict

        payload = [
            asdict(a)
            | {
                "source": str(a.source).replace("/MEDIA_ROOT/", "./"),
                "destination": str(a.destination).replace("/MEDIA_ORGANIZED/", "./"),
            }
            for a in actions
        ]
        args.save_plan.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote plan to {args.save_plan}")

    print("\nSimulation only — no files were modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
