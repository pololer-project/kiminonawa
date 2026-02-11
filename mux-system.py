#!/usr/bin/env python3
"""
MuxTools Automation Script for Anime Muxing.

This script automates the process of muxing anime episodes using MuxTools.
It supports batch processing of episodes, custom flags/naming, and dry-run mode.
The script handles video, audio, subtitles, fonts, and chapters in a standardized way.

Usage:
    uv run python mux-system.py <episode> [outdir] [options]

    <episode>: Episode specification:
               - Single number: "1"
               - Comma-separated list: "1,3,5"
               - Range: "1-5"
               - Mixed format: "1-3,5,7-9"
               - All episodes: "all"
    [outdir]: Output directory (default: "muxed")

Options:
    -v, --version: Version number for the release
    -f, --flag: Group tag to include in the filename
    -d, --dry-run: Test the muxing process without actually creating files
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

try:
    from muxtools import (
        AudioFile,
        Chapters,
        GlobSearch,
        LoggingException,
        Premux,
        Setup,
        SubFile,
        TmdbConfig,
        log,
        mux,
    )
except ImportError as e:
    raise ImportError("The 'muxtools' module is not installed. Run: uv sync") from e

__all__ = ["RunMode", "ShowConfig", "mux_episode", "parse_episode_list", "main"]


class RunMode(Enum):
    """Enum to control script behavior."""

    NORMAL = "normal"
    DRYRUN = "dryrun"


@dataclass(frozen=True, slots=True)
class ShowConfig:
    """Immutable configuration for the anime show."""

    name: str
    premux_dir: Path
    audio_dir: Path
    sub_dir: Path
    tmdb_id: int = 0
    titles: tuple[str, ...] = ()
    songs: tuple[tuple[str, str, frozenset[int]], ...] = ()

    @classmethod
    def from_defaults(cls) -> ShowConfig:
        """Create default configuration. Edit values here."""
        return cls(
            name="JudulAnime",
            premux_dir=Path(""),
            audio_dir=Path(""),
            sub_dir=Path("./"),
            tmdb_id=0,
            titles=(
                "Judul Episode 01",
                "Judul Episode 02",
            ),
            songs=(
                ("OP", "{opsync}", frozenset({1})),
                ("ED", "{edsync}", frozenset({1})),
            ),
        )


# Default configuration instance
CONFIG = ShowConfig.from_defaults()


@dataclass(slots=True)
class MuxResult:
    """Result of a mux operation."""

    episode: int
    success: bool
    output_path: Path | None = None
    error: str | None = None


def _find_video_file(episode_str: str, premux_dir: Path) -> Path | None:
    """Find video file for the given episode."""
    search = GlobSearch(f"*{episode_str}*.mkv", dir=str(premux_dir))
    return search.paths[0] if search.paths else None


def _find_audio_files(episode_str: str, audio_dir: Path) -> AudioFile | None:
    """Find audio files for the given episode."""
    search = GlobSearch(
        f"*{episode_str}*.flac", allow_multiple=True, dir=str(audio_dir)
    )
    return AudioFile(search.paths) if search.paths else None


def _create_subtitle_file(
    episode_str: str,
    show_name: str,
    episode_number: int,
    config: ShowConfig,
) -> SubFile | None:
    """Create subtitle file with all merged content."""
    base_path = Path(episode_str)
    dialog_file = base_path / f"{show_name} - {episode_str} - Dialog.ass"
    ts_file = base_path / f"{show_name} - {episode_str} - TS.ass"

    if not dialog_file.exists():
        return None

    sub_file = SubFile(str(dialog_file))

    # Merge typesetting if exists
    if ts_file.exists():
        sub_file.merge(str(ts_file))

    # Merge songs for this episode
    log.info("Merging songs...")
    for song_name, syncpoint, episodes in config.songs:
        if episode_number in episodes:
            sub_file = sub_file.merge(f"./songs/{song_name}.ass", syncpoint)

    # Merge warning and clean up
    sub_file.merge(r"./common/warning.ass").clean_garbage()
    return sub_file


def mux_episode(
    episode_number: int,
    out_dir: str = "muxed",
    version: int = 1,
    flag: str = "testing",
    mode: RunMode = RunMode.NORMAL,
    config: ShowConfig | None = None,
) -> MuxResult:
    """
    Mux a single anime episode into an MKV file.

    Args:
        episode_number: The episode number to mux
        out_dir: Directory where the output MKV file will be saved
        version: Version number of the release (defaults to 1, not shown if 1)
        flag: Group/tag name to include in the filename
        mode: Controls whether to actually mux or just do a dry run
        config: Show configuration (uses default if None)

    Returns:
        MuxResult with success status and optional output path or error
    """
    if config is None:
        config = CONFIG

    episode_str = f"{episode_number:02d}"
    version_str = "" if version == 1 else f"v{version}"
    episode_title = (
        f" | {config.titles[episode_number - 1]}"
        if config.titles and episode_number <= len(config.titles)
        else ""
    )

    # Initialize setup with error_on_danger for stricter font validation
    setup = Setup(
        episode_str,
        None,
        show_name=config.name,
        out_name=f"[{flag}] $show$ - $ep${version_str} (BDRip 1920x1080 HEVC FLAC) [$crc32$]",
        mkv_title_naming=f"$show$ - $ep${version_str}{episode_title}",
        out_dir=out_dir,
        clean_work_dirs=False,
        error_on_danger=False,  # Set True to raise errors on missing fonts/glyphs
    )

    log.debug(f"Starting mux of episode {episode_str}")

    # Dry run mode - skip file checks
    if mode == RunMode.DRYRUN:
        # Still validate subtitle files exist
        sub_file = _create_subtitle_file(
            episode_str, config.name, episode_number, config
        )
        if not sub_file:
            return MuxResult(
                episode=episode_number,
                success=False,
                error="Subtitle files missing",
            )

        log.debug(f"Dry run for episode {episode_str} completed")
        return MuxResult(episode=episode_number, success=True)

    # Find video file
    video_file = _find_video_file(episode_str, config.premux_dir)
    if not video_file:
        log.warn(f"Skipping episode {episode_str}: Video file not found", mux_episode)
        return MuxResult(
            episode=episode_number,
            success=False,
            error="Video file not found",
        )

    setup.set_default_sub_timesource(video_file)

    # Create premux
    premux = Premux(
        video_file,
        audio=None,
        subtitles=None,
        keep_attachments=False,
        mkvmerge_args=["--no-global-tags", "--no-chapters"],
    )

    # Find audio files
    audio_files = _find_audio_files(episode_str, config.audio_dir)
    if not audio_files:
        log.warn(f"Skipping episode {episode_str}: Audio files missing", mux_episode)
        return MuxResult(
            episode=episode_number,
            success=False,
            error="Audio files missing",
        )

    # Create subtitle file
    sub_file = _create_subtitle_file(episode_str, config.name, episode_number, config)
    if not sub_file:
        log.warn(f"Skipping episode {episode_str}: Subtitle files missing", mux_episode)
        return MuxResult(
            episode=episode_number,
            success=False,
            error="Subtitle files missing",
        )

    # Generate chapters and collect fonts
    chapters = Chapters.from_sub(sub_file, use_actor_field=True)
    fonts = sub_file.collect_fonts(
        use_system_fonts=False,
        additional_fonts=[Path(f"{episode_str}/fonts"), Path("./songs/fonts")],
    )

    # Perform muxing
    try:
        outfile: Path = mux(
            premux,
            audio_files.to_track("Japanese", "ja", default=True),
            sub_file.to_track(f"{flag}", "id", default=True),
            *fonts,
            chapters,
            tmdb=TmdbConfig(config.tmdb_id, write_cover=True),
        )
        print(f"Successfully muxed: {outfile.name}")
        return MuxResult(episode=episode_number, success=True, output_path=outfile)
    except Exception as e:
        log.error(f"Error muxing episode {episode_str}: {e}", mux_episode)
        return MuxResult(episode=episode_number, success=False, error=str(e))


def parse_episode_list(episode_arg: str) -> list[int]:
    """
    Parse episode input into a list of episode numbers.

    Args:
        episode_arg: String containing episode selection (e.g., "1,3,5", "1-5", or "all")

    Returns:
        List of episode numbers to process (empty list for "all")

    Raises:
        ValueError: If the episode argument format is invalid
    """
    if episode_arg == "all":
        return []

    episodes: list[int] = []

    for item in episode_arg.split(","):
        item = item.strip()

        if "-" in item:
            # Handle range notation
            parts = item.split("-")
            if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
                raise ValueError(f"Invalid episode range: {item}")

            start, end = int(parts[0].strip()), int(parts[1].strip())
            if start > end:
                raise ValueError(f"Invalid episode range (start > end): {item}")

            episodes.extend(range(start, end + 1))

        elif item.isdigit():
            episodes.append(int(item))

        else:
            raise ValueError(f"Invalid episode number: {item}")

    return sorted(set(episodes))


def _discover_all_episodes(sub_dir: Path) -> list[int]:
    """Discover all episodes from subtitle directory."""
    pattern = re.compile(r".*?(\d+).*")
    search = GlobSearch("*.ass", allow_multiple=True, recursive=True, dir=str(sub_dir))

    return sorted(
        {
            int(match.group(1))
            for path in search.paths
            if (match := pattern.match(Path(path).stem))
        }
    )


def main() -> int:
    """
    Main function to parse arguments and control the muxing process.

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    parser = argparse.ArgumentParser(
        description="Anime muxing automation using MuxTools",
        epilog="Example: uv run python mux-system.py 1-5 output_dir -f MyGroup -v 2",
    )
    parser.add_argument(
        "episode",
        type=str,
        help='Episode(s) to mux: number, range (e.g., "1-5"), comma-separated list, or "all"',
    )
    parser.add_argument(
        "outdir",
        type=str,
        help="Output directory (default: muxed)",
        default="muxed",
        nargs="?",
    )
    parser.add_argument(
        "-v", "--version", type=int, default=1, help="Version number (default: 1)"
    )
    parser.add_argument(
        "-f", "--flag", default="testing", help="Group tag for filename"
    )
    parser.add_argument(
        "-d", "--dry-run", action="store_true", help="Testing without mux"
    )
    args = parser.parse_args()

    mode = RunMode.DRYRUN if args.dry_run else RunMode.NORMAL
    if mode == RunMode.DRYRUN:
        log.info("Running in dry-run mode - no files will be created")

    try:
        # Create output directory
        Path(args.outdir).mkdir(exist_ok=True, parents=True)

        # Parse episode argument
        try:
            episode_numbers = parse_episode_list(args.episode)
        except ValueError as e:
            log.error(str(e))
            return 2

        # Discover all episodes if "all" was specified
        if not episode_numbers and args.episode == "all":
            episode_numbers = _discover_all_episodes(CONFIG.sub_dir)
            if not episode_numbers:
                log.error("No valid episodes found in subtitle directory.")
                return 1
            log.info(f"Found episodes: {episode_numbers}")

        if not episode_numbers:
            log.error("No valid episodes specified.")
            return 1

        # Mux episodes
        results = [
            mux_episode(
                ep, args.outdir, version=args.version, flag=args.flag, mode=mode
            )
            for ep in episode_numbers
        ]

        successful = sum(1 for r in results if r.success)
        log.info(
            f"Muxing complete: {successful} of {len(episode_numbers)} episodes processed"
        )

        return 0 if successful > 0 else 1

    except LoggingException:
        log.crit("Critical error while muxing!")
        return 1

    except Exception as e:
        log.error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
