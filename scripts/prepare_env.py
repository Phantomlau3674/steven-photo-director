#!/usr/bin/env python3
"""Prepare Python dependencies for steven-photo-director.

Agents should inspect the folder and ask the user before installing anything
beyond core. The default profile is intentionally light.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List


CORE_IMPORTS = {
    "PIL": "Pillow image decoding/contact sheets",
    "numpy": "numeric image metrics",
}

FULL_IMPORTS = {
    "PIL": "Pillow image decoding/contact sheets",
    "numpy": "numeric image metrics",
    "pillow_heif": "HEIC/HEIF decoding",
    "rawpy": "RAW camera file decoding",
    "cv2": "OpenCV blur, sharpness, and face/eye helpers",
    "imagehash": "standard perceptual hash helpers",
    "skimage": "extra image quality metrics",
    "mediapipe": "face landmark and eye/expression cues",
    "pyiqa": "learned image quality metrics",
}

PLUS_IMPORTS = {
    key: FULL_IMPORTS[key]
    for key in ("PIL", "numpy", "pillow_heif", "rawpy", "cv2", "imagehash", "skimage")
}

PROFILE_INFO = {
    "core": {
        "complexity": "light",
        "requirements": "requirements.txt",
        "imports": CORE_IMPORTS,
    },
    "plus": {
        "complexity": "medium",
        "requirements": "requirements-plus.txt",
        "imports": PLUS_IMPORTS,
    },
    "full": {
        "complexity": "heavy",
        "requirements": "requirements-full.txt",
        "imports": FULL_IMPORTS,
    },
}


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def install_requirements(requirements: Path) -> int:
    command = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    print("Running:", " ".join(command))
    return subprocess.call(command)


def check_imports(imports: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    for module, purpose in imports.items():
        found = importlib.util.find_spec(module) is not None
        result[module] = {
            "available": str(found).lower(),
            "purpose": purpose,
        }
    return result


def pip_check() -> Dict[str, str]:
    command = [sys.executable, "-m", "pip", "check"]
    result = subprocess.run(command, capture_output=True, text=True)
    output = (result.stdout or result.stderr or "").strip()
    return {
        "exit_code": str(result.returncode),
        "output": output or "No broken requirements found.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install/check steven-photo-director dependencies.")
    parser.add_argument("--profile", choices=["core", "plus", "full"], default="core")
    parser.add_argument("--check-only", action="store_true", help="Do not install; only report imports.")
    parser.add_argument("--explain", action="store_true", help="Print profile complexity and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.explain:
        explanation = {
            profile: {
                "complexity": info["complexity"],
                "requirements": str(script_dir() / str(info["requirements"])),
                "libraries": list(info["imports"].keys()),
                "purposes": info["imports"],
            }
            for profile, info in PROFILE_INFO.items()
        }
        print(json.dumps(explanation, ensure_ascii=False, indent=2))
        return 0

    profile_info = PROFILE_INFO[args.profile]
    requirements = script_dir() / str(profile_info["requirements"])
    imports = profile_info["imports"]

    install_code = 0
    if not args.check_only:
        install_code = install_requirements(requirements)
    status = {
        "profile": args.profile,
        "complexity": profile_info["complexity"],
        "python": sys.executable,
        "requirements": str(requirements),
        "install_exit_code": install_code,
        "imports": check_imports(imports),
        "pip_check": pip_check(),
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    missing = [
        module
        for module, info in status["imports"].items()
        if info["available"] != "true"
    ]
    if args.profile == "core" and missing:
        return 2
    return 0 if install_code == 0 else install_code


if __name__ == "__main__":
    raise SystemExit(main())
