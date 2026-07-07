"""CLI wrapper for extracting embedded images from HWP files."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.converters import HWPImageExtractor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract embedded images from HWP files.")
    parser.add_argument(
        "directories",
        nargs="*",
        help="Directories to scan. Defaults to ./data when omitted.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove *_images directories instead of extracting.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress output.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    directories = args.directories or ["data"]
    extractor = HWPImageExtractor()

    if args.clean:
        deleted = extractor.clean_directories(
            directories,
            show_progress=not args.no_progress,
        )
        print(f"Removed {deleted} image directories.")
        return

    result = extractor.extract_directories(
        directories,
        show_progress=not args.no_progress,
    )

    print("=" * 50)
    print("HWP image extraction complete")
    print("=" * 50)
    print(f"Directories:       {', '.join(str(Path(d)) for d in directories)}")
    print(f"HWP files:         {result.total_files}")
    print(f"Files extracted:   {result.extracted_files}")
    print(f"Files skipped:     {result.skipped_files}")
    print(f"Files failed:      {result.failed_files}")
    print(f"Files without img: {result.no_image_files}")
    print(f"Images extracted:  {result.total_images}")


if __name__ == "__main__":
    main()
