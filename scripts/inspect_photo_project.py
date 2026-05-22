#!/usr/bin/env python3
"""Inspect a photo folder and recommend a dependency profile before install."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List


PHOTO_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
    ".heic",
    ".heif",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".dng",
    ".raf",
    ".rw2",
    ".orf",
    ".srw",
    ".pef",
}

RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".rw2", ".orf", ".srw", ".pef"}
HEIF_EXTENSIONS = {".heic", ".heif"}
CORE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


def iter_files(root: Path, recursive: bool) -> List[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(path for path in root.glob(pattern) if path.is_file())


def human_gb(num_bytes: int) -> float:
    return round(num_bytes / (1024 ** 3), 3)


def recommend_profile(extension_counts: Counter[str], total_files: int) -> Dict[str, str]:
    has_raw = any(ext in RAW_EXTENSIONS for ext in extension_counts)
    has_heif = any(ext in HEIF_EXTENSIONS for ext in extension_counts)
    has_core_images = any(ext in CORE_EXTENSIONS for ext in extension_counts)
    if has_raw or has_heif:
        return {
            "profile": "plus",
            "complexity": "medium",
            "reason": "Folder includes RAW or HEIC/HEIF files, so decoding coverage benefits from plus.",
        }
    if has_core_images:
        return {
            "profile": "core",
            "complexity": "light",
            "reason": "Folder uses common image formats, so core is enough for first-pass culling.",
        }
    return {
        "profile": "core",
        "complexity": "light",
        "reason": "No supported photo extensions were detected; core can still report inventory and blockers.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect photo project size and format complexity.")
    parser.add_argument("input_dir", help="Photo folder to inspect.")
    parser.add_argument("--recursive", action="store_true", help="Scan nested folders.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.input_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Input folder not found: {root}")
    all_files = iter_files(root, args.recursive)
    photo_files = [path for path in all_files if path.suffix.lower() in PHOTO_EXTENSIONS]
    extension_counts = Counter(path.suffix.lower() or "[none]" for path in photo_files)
    total_bytes = sum(path.stat().st_size for path in photo_files)
    recommendation = recommend_profile(extension_counts, len(photo_files))
    result = {
        "input_dir": str(root),
        "recursive": args.recursive,
        "all_files": len(all_files),
        "photo_files": len(photo_files),
        "total_photo_gb": human_gb(total_bytes),
        "extensions": dict(sorted(extension_counts.items())),
        "raw_files": sum(count for ext, count in extension_counts.items() if ext in RAW_EXTENSIONS),
        "heif_files": sum(count for ext, count in extension_counts.items() if ext in HEIF_EXTENSIONS),
        "recommended_profile": recommendation,
        "profiles": {
            "core": {
                "complexity": "light",
                "libraries": ["Pillow", "numpy"],
                "best_for": "JPG/PNG/WebP/TIFF first-pass blur, exposure, duplicate grouping, and contact sheets.",
            },
            "plus": {
                "complexity": "medium",
                "libraries": ["Pillow", "numpy", "pillow-heif", "rawpy", "opencv-python-headless", "imagehash", "scikit-image"],
                "best_for": "HEIC/HEIF, RAW, and stronger OpenCV-assisted metrics.",
            },
            "full": {
                "complexity": "heavy",
                "libraries": ["plus libraries", "mediapipe", "pyiqa"],
                "best_for": "Face-landmark and learned quality/aesthetic experiments after explicit approval.",
            },
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
