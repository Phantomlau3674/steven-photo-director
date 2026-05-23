#!/usr/bin/env python3
"""Group photos by scene/visual sequence for user-guided selection."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scan_photos import build_sheet, hamming_hex


def load_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "items" not in data or not isinstance(data["items"], list):
        raise SystemExit("manifest.json must contain an item list.")
    return data


def file_number(item: Dict[str, Any]) -> Optional[int]:
    rel = str(item.get("relative_path") or "")
    matches = re.findall(r"(\d+)", rel)
    if not matches:
        return None
    return int(matches[-1])


def sort_key(item: Dict[str, Any]) -> Tuple[str, int, str]:
    return (
        str(item.get("capture_time") or ""),
        file_number(item) if file_number(item) is not None else 10**12,
        str(item.get("relative_path") or "").lower(),
    )


def metric_delta(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    total = 0.0
    for key, weight in (
        ("brightness_mean", 24.0),
        ("contrast", 18.0),
        ("colorfulness", 0.15),
    ):
        lval = left.get(key)
        rval = right.get(key)
        if isinstance(lval, (int, float)) and isinstance(rval, (int, float)):
            total += abs(float(lval) - float(rval)) * weight
    return total


def visual_distance(left: Dict[str, Any], right: Dict[str, Any]) -> Optional[int]:
    lhash = left.get("dhash")
    rhash = right.get("dhash")
    if not lhash or not rhash:
        return None
    try:
        return hamming_hex(str(lhash), str(rhash))
    except Exception:
        return None


def should_start_new_group(
    prev: Dict[str, Any],
    current: Dict[str, Any],
    current_group_size: int,
    visual_threshold: int,
    max_sequence_gap: int,
    max_group_size: int,
) -> Tuple[bool, str]:
    if current_group_size >= max_group_size:
        return True, "max_group_size"
    prev_dup = prev.get("duplicate_group_id")
    cur_dup = current.get("duplicate_group_id")
    if prev_dup and cur_dup and prev_dup == cur_dup:
        return False, "same_duplicate_group"
    prev_num = file_number(prev)
    cur_num = file_number(current)
    if prev_num is not None and cur_num is not None and abs(cur_num - prev_num) > max_sequence_gap:
        return True, "sequence_gap"
    distance = visual_distance(prev, current)
    if distance is not None and distance > visual_threshold:
        return True, f"visual_distance_{distance}"
    if metric_delta(prev, current) > 18:
        return True, "tone_color_shift"
    return False, "continuous"


def build_groups(
    items: Sequence[Dict[str, Any]],
    visual_threshold: int,
    max_sequence_gap: int,
    max_group_size: int,
) -> List[Dict[str, Any]]:
    sorted_items = sorted(items, key=sort_key)
    groups: List[Dict[str, Any]] = []
    current_items: List[Dict[str, Any]] = []
    current_reason = "start"

    for item in sorted_items:
        if not current_items:
            current_items = [item]
            current_reason = "start"
            continue
        start_new, reason = should_start_new_group(
            current_items[-1],
            item,
            len(current_items),
            visual_threshold,
            max_sequence_gap,
            max_group_size,
        )
        if start_new:
            groups.append({"items": current_items, "split_reason": current_reason})
            current_items = [item]
            current_reason = reason
        else:
            current_items.append(item)
    if current_items:
        groups.append({"items": current_items, "split_reason": current_reason})

    for index, group in enumerate(groups, 1):
        group["group_id"] = f"scene_{index:04d}"
        group_items = group["items"]
        numbers = [file_number(item) for item in group_items if file_number(item) is not None]
        group["count"] = len(group_items)
        group["first_relative_path"] = group_items[0].get("relative_path")
        group["last_relative_path"] = group_items[-1].get("relative_path")
        group["number_start"] = min(numbers) if numbers else None
        group["number_end"] = max(numbers) if numbers else None
        group["duplicate_groups"] = sorted(
            {str(item.get("duplicate_group_id")) for item in group_items if item.get("duplicate_group_id")}
        )
        group["risk_flags"] = sorted({flag for item in group_items for flag in (item.get("risk_flags") or [])})
        group["avg_technical_score"] = round(
            sum(float(item.get("technical_score") or 0) for item in group_items) / max(1, len(group_items)),
            3,
        )
    return groups


def unique_destination(base_dir: Path, source: Path) -> Path:
    candidate = base_dir / source.name
    if not candidate.exists():
        return candidate
    stem = source.stem
    suffix = source.suffix
    counter = 2
    while True:
        candidate = base_dir / f"{stem}_{counter:03d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def materialize(source: Path, target: Path, file_mode: str) -> str:
    if file_mode == "none":
        return "skipped"
    if file_mode == "hardlink":
        try:
            os.link(source, target)
            return "hardlinked"
        except Exception:
            shutil.copy2(source, target)
            return "copied_after_hardlink_failed"
    shutil.copy2(source, target)
    return "copied"


def write_group_files(groups: List[Dict[str, Any]], output_dir: Path, file_mode: str) -> None:
    groups_dir = output_dir / "场景分组"
    sheets_dir = output_dir / "group_contact_sheets"
    groups_dir.mkdir(parents=True, exist_ok=True)
    sheets_dir.mkdir(parents=True, exist_ok=True)

    representatives = []
    for group in groups:
        group_id = group["group_id"]
        title = f"{group_id} {group['first_relative_path']} - {group['last_relative_path']} ({group['count']})"
        group_dir = groups_dir / group_id
        group_dir.mkdir(parents=True, exist_ok=True)
        for item in group["items"]:
            source = Path(item["path"])
            if source.exists():
                target = unique_destination(group_dir, source)
                materialize(source, target, file_mode)
        build_sheet(group["items"], sheets_dir / f"{group_id}.jpg", title, columns=5)
        representative = sorted(
            group["items"],
            key=lambda item: (
                -float(item.get("technical_score") or 0),
                item.get("duplicate_rank") or 999,
                str(item.get("relative_path") or "").lower(),
            ),
        )[0].copy()
        representative["relative_path"] = f"{group_id}: {representative.get('relative_path')}"
        representatives.append(representative)
    build_sheet(representatives, output_dir / "group_overview.jpg", "Scene group overview", columns=5)


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def write_outputs(manifest: Dict[str, Any], groups: List[Dict[str, Any]], output_dir: Path) -> None:
    assignment = {}
    group_records = []
    choice_rows = []
    for group in groups:
        clean_group = {key: value for key, value in group.items() if key != "items"}
        clean_group["paths"] = [item["path"] for item in group["items"]]
        group_records.append(clean_group)
        for item in group["items"]:
            assignment[item["path"]] = group["group_id"]
        choice_rows.append(
            {
                "group_id": group["group_id"],
                "action": "",
                "scene_quality": "",
                "scene_role": "",
                "evidence": "",
                "memory_note": "",
                "keep_count": "",
                "notes": "",
                "count": group["count"],
                "avg_technical_score": group["avg_technical_score"],
                "first_relative_path": group["first_relative_path"],
                "last_relative_path": group["last_relative_path"],
                "risk_flags": ",".join(group["risk_flags"]),
            }
        )

    (output_dir / "group_assignments.json").write_text(
        json.dumps(
            {
                "source_manifest": str(Path(manifest.get("output_dir", ".")) / "manifest.json"),
                "path_to_group": assignment,
                "groups": group_records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_csv(
        output_dir / "groups.csv",
        group_records,
        [
            "group_id",
            "count",
            "first_relative_path",
            "last_relative_path",
            "number_start",
            "number_end",
            "avg_technical_score",
            "split_reason",
            "duplicate_groups",
            "risk_flags",
        ],
    )
    write_csv(
        output_dir / "group_choices.csv",
        choice_rows,
        [
            "group_id",
            "action",
            "scene_quality",
            "scene_role",
            "evidence",
            "memory_note",
            "keep_count",
            "notes",
            "count",
            "avg_technical_score",
            "first_relative_path",
            "last_relative_path",
            "risk_flags",
        ],
    )
    lines = [
        "# Photo Scene Groups",
        "",
        "These script groups are provisional. The agent/model must inspect the scene folders/contact sheets before editing `group_choices.csv`:",
        "",
        "- `include`: model/user wants this scene considered",
        "- `priority`: model/user wants this scene boosted",
        "- `exclude`: model/user wants this scene kept out",
        "- `scene_quality`: 0-5, model/user quality judgment for this scene",
        "- `scene_role`: hero / strong / support / weak / drop",
        "- `evidence`: why this scene is worth more or less",
        "- `memory_note`: short temporary memory for later merged review",
        "- `keep_count`: model/user approximate target count for that scene",
        "",
        f"Total groups: {len(groups)}",
        "",
    ]
    for group in groups:
        lines.append(
            f"- `{group['group_id']}` ({group['count']}): {group['first_relative_path']} -> {group['last_relative_path']}"
        )
    (output_dir / "group_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Group photos by visual/scene sequence.")
    parser.add_argument("manifest", help="Path to manifest.json.")
    parser.add_argument("--output", default=None, help="Output folder. Defaults to manifest parent/groups.")
    parser.add_argument("--visual-threshold", type=int, default=24, help="dHash distance above this starts a new visual group.")
    parser.add_argument("--max-sequence-gap", type=int, default=12, help="Filename number gap above this starts a new group.")
    parser.add_argument("--max-group-size", type=int, default=24, help="Split very large continuous runs.")
    parser.add_argument("--file-mode", choices=["none", "copy", "hardlink"], default="hardlink")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    output_dir = Path(args.output).expanduser().resolve() if args.output else manifest_path.parent / "groups"
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = build_groups(
        manifest["items"],
        visual_threshold=args.visual_threshold,
        max_sequence_gap=args.max_sequence_gap,
        max_group_size=args.max_group_size,
    )
    write_group_files(groups, output_dir, args.file_mode)
    write_outputs(manifest, groups, output_dir)
    summary = {
        "output_dir": str(output_dir),
        "groups": len(groups),
        "file_mode": args.file_mode,
        "group_choices": str(output_dir / "group_choices.csv"),
        "group_assignments": str(output_dir / "group_assignments.json"),
        "group_overview": str(output_dir / "group_overview.jpg"),
    }
    (output_dir / "group_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
