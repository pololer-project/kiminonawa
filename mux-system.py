#!/usr/bin/env python3
"""
MuxTools Automation Script for Kimi no Na wa (Movie).

Automates the process of muxing a movie using MuxTools.
Optimized for efficiency, readability, and correct resource resolution.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

try:
    from muxtools import (
        Chapters,
        GlobSearch,
        Premux,
        Setup,
        SubFile,
        TmdbConfig,
        log,
        mux,
    )
except ImportError as e:
    sys.exit(f"Error: {e}. Run 'uv sync' to install dependencies.")

__all__ = ["RunMode", "ShowConfig", "mux_movie", "main"]


class RunMode(Enum):
    NORMAL = "normal"
    DRYRUN = "dryrun"


@dataclass(frozen=True, slots=True)
class ShowConfig:
    """Immutable configuration for the movie."""

    name: str
    premux_dir: Path
    sub_dir: Path
    tmdb_id: int = 0

    @classmethod
    def from_defaults(cls) -> ShowConfig:
        """Create configuration relative to the script location."""
        base = Path(__file__).resolve().parent

        return cls(
            name="Kimi no Na wa",
            premux_dir=base / "premux",
            sub_dir=base / "subtitle",
            tmdb_id=372058,
        )


CONFIG = ShowConfig.from_defaults()


@dataclass(slots=True)
class MuxResult:
    success: bool
    error: str | None = None


def _find_video(config: ShowConfig) -> Path:
    """Find the video file in premux directory."""
    search = GlobSearch(
        "*.mkv",
        allow_multiple=True,
        recursive=True,
        dir=str(config.premux_dir),
    )

    if not search.paths:
        raise FileNotFoundError("Video file not found in premux directory")

    return Path(search.paths[0])


def _find_subtitle(config: ShowConfig) -> SubFile:
    """Find and prepare the subtitle file."""
    sub_path = config.sub_dir / "Movie.ass"
    if not sub_path.exists():
        # Fallback to glob search
        search = GlobSearch("*.ass", dir=str(config.sub_dir))
        if not search.paths:
            raise FileNotFoundError("Subtitle file not found in subtitle directory")
        sub_path = Path(search.paths[0])

    sub = SubFile(str(sub_path), container_delay=0)
    # Apply cleaning
    sub.merge(r"common/warning.ass").clean_styles().clean_garbage()
    return sub


def _load_chapters(config: ShowConfig) -> Chapters | None:
    """Load chapters from XML file if available."""
    chapter_path = config.sub_dir / "Chapter.xml"
    if chapter_path.exists():
        return Chapters(str(chapter_path))
    return None


def mux_movie(
    out_dir: Path,
    version: int = 1,
    flag: str = "testing",
    mode: RunMode = RunMode.NORMAL,
    config: ShowConfig | None = None,
) -> MuxResult:
    """Mux the movie into an MKV file."""
    config = config or CONFIG
    ep_str = "Movie"
    version_str = "" if version == 1 else f"v{version}"

    setup = Setup(
        ep_str,
        None,
        show_name=config.name,
        out_name=f"[{flag}] $show$ - $ep${version_str} (BDRip 1920x1080 HEVC FLACx2) [$crc32$]",
        mkv_title_naming=f"$show$ - $ep${version_str}",
        out_dir=str(out_dir),
        clean_work_dirs=False,
    )

    if mode == RunMode.DRYRUN:
        log.info(f"[Dry Run] Would mux movie to {out_dir}")
        return MuxResult(True)

    try:
        # Locating Resources
        video_file = _find_video(config)
        setup.set_default_sub_timesource(str(video_file))

        sub_file = _find_subtitle(config)

        # Chapters & Fonts
        chapters = _load_chapters(config)

        font_paths = [
            config.sub_dir / "fonts",
            config.sub_dir.parent / "common" / "fonts",
        ]
        valid_font_paths = [p for p in font_paths if p.exists()]
        fonts = sub_file.collect_fonts(
            use_system_fonts=False, additional_fonts=valid_font_paths
        )

        # Muxing — keep audio from premux
        premux = Premux(
            str(video_file),
            subtitles=None,
            keep_attachments=False,
            mkvmerge_args=["--no-global-tags", "--no-chapters"],
        )

        mux_args = [
            premux,
            sub_file.to_track("Moesubs", "id", default=True),
            *fonts,
        ]

        if chapters:
            mux_args.append(chapters)

        outfile = mux(
            *mux_args,
            tmdb=TmdbConfig(config.tmdb_id, write_cover=True, movie=True),
        )
        log.info(f"Muxed: {outfile.name}")
        return MuxResult(True)

    except Exception as e:
        log.error(f"Failed to mux movie: {e}")
        return MuxResult(False, str(e))


def main() -> int:
    parser = argparse.ArgumentParser(description="Movie Mux System")
    parser.add_argument(
        "outdir",
        nargs="?",
        default="muxed",
        help="Output directory",
    )
    parser.add_argument("-f", "--flag", default="Pololer", help="Release group/flag")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Dry run")
    parser.add_argument("-v", "--version", type=int, default=1, help="Version number")

    args = parser.parse_args()

    out_dir = Path(args.outdir).resolve()
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    result = mux_movie(
        out_dir,
        flag=args.flag,
        mode=RunMode.DRYRUN if args.dry_run else RunMode.NORMAL,
        version=args.version,
    )

    if result.success:
        log.info("Movie muxing completed successfully.")
    else:
        log.error(f"Movie muxing failed: {result.error}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
