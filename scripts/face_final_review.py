#!/usr/bin/env python3
"""Build and apply a final face/portrait review pack.

This script does not try to decide whether a face is "good." It creates a
review surface where selected people photos can be compared against nearby,
same-scene, and same-sequence alternatives before final delivery.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

from export_selection import (
    DECISION_ORDER,
    HUMAN_RESULT_DIR,
    PROCESS_DIR,
    copy_rows,
    materialize_file,
    unique_destination,
    write_list,
    write_open_here,
)
from scan_photos import build_sheet


FACE_REVIEW_FLAGS = {"face_eye_review", "eye_closed_review", "eye_asymmetry_review"}


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_action(value: str) -> str:
    return (value or "").strip().lower()


def file_number(value: str | None) -> int | None:
    if not value:
        return None
    matches = re.findall(r"(\d+)", value)
    if not matches:
        return None
    return int(matches[-1])


def load_group_assignments(path: str | None) -> Dict[str, str]:
    if not path:
        return {}
    data = load_json(Path(path).expanduser().resolve())
    return data.get("path_to_group", {})


def manifest_maps(manifest: Dict[str, Any]) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_path = {}
    by_relative = {}
    for item in manifest.get("items", []):
        by_path[item["path"]] = item
        by_relative[str(item.get("relative_path"))] = item
    return by_path, by_relative


def selection_maps(selection: Dict[str, Any]) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_path = {}
    by_relative = {}
    for item in selection.get("selections", []):
        by_path[item["path"]] = item
        by_relative[str(item.get("relative_path"))] = item
    return by_path, by_relative


def candidate_score(item: Dict[str, Any]) -> float:
    score = float(item.get("technical_score") or 0)
    flags = set(item.get("risk_flags") or [])
    if "severe_blur" in flags:
        score -= 20
    if "eye_closed_review" in flags:
        score -= 8
    if "eye_asymmetry_review" in flags:
        score -= 5
    if "near_duplicate" in flags and item.get("script_duplicate_pick"):
        score += 5
    return score


def has_face_signal(item: Dict[str, Any]) -> bool:
    flags = set(item.get("risk_flags") or [])
    face_count = item.get("face_count")
    landmark_count = item.get("face_landmark_count")
    return (
        bool(isinstance(face_count, (int, float)) and face_count > 0)
        or bool(isinstance(landmark_count, (int, float)) and landmark_count > 0)
        or bool(flags & FACE_REVIEW_FLAGS)
    )


def face_review_reasons(item: Dict[str, Any]) -> str:
    reasons = []
    flags = set(item.get("risk_flags") or [])
    face_count = item.get("face_count")
    if isinstance(face_count, (int, float)) and face_count > 0:
        reasons.append(f"face_count={int(face_count)}")
    landmark_count = item.get("face_landmark_count")
    if isinstance(landmark_count, (int, float)) and landmark_count > 0:
        reasons.append(f"face_landmarks={int(landmark_count)}")
    if "eye_closed_review" in flags:
        reasons.append("eye_closed_review")
    if "eye_asymmetry_review" in flags:
        reasons.append("eye_asymmetry_review")
    if "face_eye_review" in flags:
        reasons.append("face_eye_review")
    return "; ".join(reasons)


def alternatives_for_keep(
    keep_item: Dict[str, Any],
    all_items: Sequence[Dict[str, Any]],
    path_to_group: Dict[str, str],
    neighbor_window: int,
    max_alternates: int,
) -> List[Dict[str, Any]]:
    keep_path = keep_item["path"]
    keep_group = keep_item.get("duplicate_group_id")
    keep_scene = path_to_group.get(keep_path)
    keep_number = file_number(str(keep_item.get("relative_path")))
    candidates = []
    seen: Set[str] = {keep_path}

    for item in all_items:
        path = item["path"]
        if path in seen:
            continue
        same_duplicate = keep_group and item.get("duplicate_group_id") == keep_group
        same_scene = keep_scene and path_to_group.get(path) == keep_scene
        item_number = file_number(str(item.get("relative_path")))
        near_number = (
            keep_number is not None
            and item_number is not None
            and abs(item_number - keep_number) <= neighbor_window
        )
        if same_duplicate or same_scene or near_number:
            candidates.append(item)
            seen.add(path)

    candidates.sort(
        key=lambda item: (
            -candidate_score(item),
            item.get("duplicate_rank") or 999,
            str(item.get("relative_path") or "").lower(),
        )
    )
    return candidates[:max_alternates]


def display_item(item: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    copy = dict(item)
    copy["relative_path"] = f"{prefix}: {item.get('relative_path')}"
    return copy


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "photo"


def materialize_review_file(item: Dict[str, Any], target_dir: Path, file_mode: str) -> Dict[str, str]:
    source = Path(item["path"])
    target_dir.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return {"source": str(source), "target": "", "status": "missing"}
    target = unique_destination(target_dir, source)
    try:
        status = materialize_file(source, target, file_mode)
    except Exception as exc:
        if file_mode == "hardlink":
            status = materialize_file(source, target, "copy")
            status = f"{status}_after_hardlink_failed:{type(exc).__name__}"
        else:
            raise
    return {"source": str(source), "target": str(target), "status": status}


def build_review_pack(args: argparse.Namespace) -> int:
    selection_path = Path(args.selection_json).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve() if args.output else selection_path.parent / "face_final_review"
    output_dir.mkdir(parents=True, exist_ok=True)
    sheets_dir = output_dir / "01_对比图"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    review_sets_dir = output_dir / "02_逐张对比_当前与候选"
    review_sets_dir.mkdir(parents=True, exist_ok=True)

    selection = load_json(selection_path)
    manifest = load_json(manifest_path)
    manifest_by_path, _manifest_by_relative = manifest_maps(manifest)
    path_to_group = load_group_assignments(args.group_assignments)
    keep_rows = [row for row in selection.get("selections", []) if str(row.get("decision")).upper() == "KEEP"]
    all_items = list(manifest.get("items", []))
    keep_pairs = []
    for keep_row in keep_rows:
        keep_item = manifest_by_path.get(keep_row["path"])
        if keep_item:
            keep_pairs.append((keep_row, keep_item))
    face_pairs = [(row, item) for row, item in keep_pairs if has_face_signal(item)]
    if args.review_scope == "all":
        review_pairs = keep_pairs
        scope_reason = "review_scope=all"
    elif args.review_scope == "faces":
        review_pairs = face_pairs
        scope_reason = "review_scope=faces"
    elif face_pairs:
        review_pairs = face_pairs
        scope_reason = "auto: face signals found"
    else:
        review_pairs = keep_pairs
        scope_reason = "auto fallback: no face detector signal available, review all KEEP rows"
    review_rows = []
    review_set_rows = []
    overview_items = []

    for index, (_keep_row, keep_item) in enumerate(review_pairs, 1):
        alternatives = alternatives_for_keep(
            keep_item,
            all_items,
            path_to_group,
            neighbor_window=args.neighbor_window,
            max_alternates=args.max_alternates,
        )
        sheet_items = [display_item(keep_item, "CURRENT_KEEP")]
        sheet_items.extend(display_item(item, "ALT") for item in alternatives)
        sheet_path = sheets_dir / f"face_review_{index:03d}_{Path(keep_item['path']).stem}.jpg"
        build_sheet(
            sheet_items,
            sheet_path,
            f"Face final review {index:03d}: {keep_item.get('relative_path')}",
            columns=args.columns,
        )
        set_dir = review_sets_dir / f"{index:03d}_{safe_name(Path(keep_item['path']).stem)}"
        current_status = materialize_review_file(keep_item, set_dir / "current_keep", args.file_mode)
        review_set_rows.append(
            {
                "review_index": index,
                "role": "current_keep",
                "relative_path": keep_item.get("relative_path"),
                "review_set_folder": str(set_dir),
                **current_status,
            }
        )
        for alt_index, alternative in enumerate(alternatives, 1):
            alt_status = materialize_review_file(alternative, set_dir / "alternatives", args.file_mode)
            review_set_rows.append(
                {
                    "review_index": index,
                    "role": f"alternative_{alt_index:02d}",
                    "relative_path": alternative.get("relative_path"),
                    "review_set_folder": str(set_dir),
                    **alt_status,
                }
            )
        overview_items.append(keep_item)
        review_rows.append(
            {
                "keep_relative_path": keep_item.get("relative_path"),
                "action": "",
                "replacement_relative_path": "",
                "notes": "",
                "scene_group_id": path_to_group.get(keep_item["path"], ""),
                "duplicate_group_id": keep_item.get("duplicate_group_id") or "",
                "face_review_reasons": face_review_reasons(keep_item),
                "face_count": keep_item.get("face_count"),
                "eye_closed_face_count": keep_item.get("eye_closed_face_count"),
                "eye_asymmetry_face_count": keep_item.get("eye_asymmetry_face_count"),
                "technical_score": keep_item.get("technical_score"),
                "current_path": keep_item["path"],
                "candidate_relative_paths": "|".join(str(item.get("relative_path")) for item in alternatives),
                "comparison_sheet": str(sheet_path),
                "review_set_folder": str(set_dir),
            }
        )

    build_sheet(overview_items, output_dir / "00_当前精选总览.jpg", "Current KEEP face/portrait review", columns=args.columns)
    choices_path = output_dir / "03_人工调整表_face_review_choices.csv"
    fieldnames = [
        "keep_relative_path",
        "action",
        "replacement_relative_path",
        "notes",
        "scene_group_id",
        "duplicate_group_id",
        "face_review_reasons",
        "face_count",
        "eye_closed_face_count",
        "eye_asymmetry_face_count",
        "technical_score",
        "current_path",
        "candidate_relative_paths",
        "comparison_sheet",
        "review_set_folder",
    ]
    with choices_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)
    if review_set_rows:
        with (output_dir / "face_review_sets.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(review_set_rows[0].keys()))
            writer.writeheader()
            writer.writerows(review_set_rows)
    (output_dir / "face_review_instructions.md").write_text(
        "\n".join(
            [
                "# Face Final Review",
                "",
                "Open `00_当前精选总览.jpg` and `01_对比图/`.",
                "You can also browse `02_逐张对比_当前与候选/`: each selected photo has `current_keep/` and `alternatives/` folders.",
                "",
                "Edit `03_人工调整表_face_review_choices.csv`:",
                "",
                "- `keep`: keep current selected photo.",
                "- `replace`: set `replacement_relative_path` to one candidate from the row.",
                "- `drop`: move current KEEP to REVIEW without choosing a replacement.",
                "- `reject`: move current KEEP to REJECT.",
                "",
                "This is for expression, eyes, mouth shape, face angle, hands, posture, and personal taste.",
                "Automatic scoring is only a draft; apply this review before treating people photos as final.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = {
        "output_dir": str(output_dir),
        "total_keep_count": len(keep_rows),
        "review_count": len(review_pairs),
        "face_signal_keep_count": len(face_pairs),
        "review_scope": args.review_scope,
        "scope_reason": scope_reason,
        "choices_csv": str(choices_path),
        "overview": str(output_dir / "00_当前精选总览.jpg"),
        "comparison_sheets": str(sheets_dir),
        "review_sets": str(review_sets_dir),
        "review_sets_csv": str(output_dir / "face_review_sets.csv"),
    }
    (output_dir / "face_review_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def ensure_selection_row(
    path: str,
    manifest_by_path: Dict[str, Dict[str, Any]],
    selection_by_path: Dict[str, Dict[str, Any]],
    selection: Dict[str, Any],
) -> Dict[str, Any]:
    if path in selection_by_path:
        return selection_by_path[path]
    item = manifest_by_path[path]
    row = {
        "path": path,
        "relative_path": item.get("relative_path"),
        "decision": "REVIEW",
        "rating": 3,
        "label": "yellow",
        "reason": "Added from face final review candidate.",
    }
    selection.setdefault("selections", []).append(row)
    selection_by_path[path] = row
    return row


def find_item(identifier: str, by_path: Dict[str, Dict[str, Any]], by_relative: Dict[str, Dict[str, Any]]) -> Dict[str, Any] | None:
    if identifier in by_path:
        return by_path[identifier]
    if identifier in by_relative:
        return by_relative[identifier]
    return None


def apply_review_choices(args: argparse.Namespace) -> int:
    selection_path = Path(args.selection_json).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    choices_path = Path(args.choices_csv).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve() if args.output else selection_path.parent / "face_final_applied"
    output_dir.mkdir(parents=True, exist_ok=True)
    process_dir = output_dir / PROCESS_DIR
    process_dir.mkdir(parents=True, exist_ok=True)

    selection = load_json(selection_path)
    manifest = load_json(manifest_path)
    manifest_by_path, manifest_by_relative = manifest_maps(manifest)
    selection_by_path, _selection_by_relative = selection_maps(selection)
    applied = []

    with choices_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            action = normalize_action(row.get("action") or "")
            if not action or action == "keep":
                continue
            current_path = row.get("current_path") or ""
            current = selection_by_path.get(current_path)
            if not current:
                continue
            note = row.get("notes") or ""
            if action == "replace":
                replacement_id = row.get("replacement_relative_path") or ""
                replacement_item = find_item(replacement_id, manifest_by_path, manifest_by_relative)
                if not replacement_item:
                    applied.append({"action": action, "current": current_path, "status": "replacement_not_found"})
                    continue
                replacement = ensure_selection_row(replacement_item["path"], manifest_by_path, selection_by_path, selection)
                current["decision"] = "REVIEW"
                current["rating"] = 3
                current["label"] = "yellow"
                current["reason"] = f"Replaced during face final review. {note}".strip()
                replacement["decision"] = "KEEP"
                replacement["rating"] = 5
                replacement["label"] = "green"
                replacement["reason"] = f"Selected as face-final replacement for {current.get('relative_path')}. {note}".strip()
                applied.append({"action": action, "current": current_path, "replacement": replacement_item["path"], "status": "applied"})
            elif action == "drop":
                current["decision"] = "REVIEW"
                current["rating"] = 3
                current["label"] = "yellow"
                current["reason"] = f"Dropped from KEEP during face final review. {note}".strip()
                applied.append({"action": action, "current": current_path, "status": "applied"})
            elif action == "reject":
                current["decision"] = "REJECT"
                current["rating"] = 1
                current["label"] = "red"
                current["reason"] = f"Rejected during face final review. {note}".strip()
                applied.append({"action": action, "current": current_path, "status": "applied"})
            else:
                applied.append({"action": action, "current": current_path, "status": "unknown_action"})

    unresolved = [row for row in applied if row.get("status") not in {"applied"}]
    final_selection = process_dir / "selection_face_final.json"
    selection["selection_notes"] = (selection.get("selection_notes") or "") + " Face final review choices applied."
    selection["face_final_review_applied"] = True
    selection["face_final_review_choices"] = str(choices_path)
    selection["face_final_review_status"] = "applied_with_warnings" if unresolved else "applied"
    selection["requires_face_final_review"] = bool(unresolved)
    requires_model_scene_review = bool(selection.get("requires_model_scene_review", False))
    if not requires_model_scene_review and selection.get("review_status") in {None, "", "machine_triage_only_requires_model_scene_review"}:
        selection["review_status"] = "model_scene_reviewed_final"
    final_selection.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = selection.get("selections", [])
    by_decision = {decision: [item for item in rows if item.get("decision") == decision] for decision in DECISION_ORDER}
    write_list(process_dir / "selection_keep.txt", by_decision["KEEP"])
    write_list(process_dir / "selection_review.txt", by_decision["REVIEW"])
    write_list(process_dir / "selection_unselected.txt", by_decision["UNSELECTED"])
    write_list(process_dir / "selection_reject.txt", by_decision["REJECT"])
    result_dir = output_dir / HUMAN_RESULT_DIR
    copied = copy_rows(rows, result_dir, "zh", args.file_mode)
    summary = {
        "output_dir": str(output_dir),
        "result_dir": str(result_dir),
        "process_dir": str(process_dir),
        "final_selection": str(final_selection),
        "counts": dict(Counter(item.get("decision") for item in rows)),
        "face_final_review_status": selection["face_final_review_status"],
        "requires_face_final_review": selection["requires_face_final_review"],
        "requires_model_scene_review": requires_model_scene_review,
        "review_status": selection.get("review_status"),
        "applied": applied,
        "unresolved_count": len(unresolved),
        "copied_count": len([row for row in copied if row["status"] == "copied"]),
        "hardlinked_count": len([row for row in copied if row["status"] == "hardlinked"]),
    }
    summary_path = process_dir / "face_final_apply_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if requires_model_scene_review:
        readme_title = "Steven Photo Director 待场景终审结果"
        readme_status = "已应用人脸/表情调整，但 selection.json 仍标记为需要模型场景审片；请先完成场景识别、场景取舍和最终精选确认。"
        next_action = "继续让智能体/模型按场景检查 `01_最终结果`、候选对比图和场景分组；确认后再把 `requires_model_scene_review` 设为 false。"
    else:
        readme_title = "Steven Photo Director 最终结果"
        readme_status = "已应用人脸/表情终审，并且 selection.json 不再要求模型场景审片。"
        next_action = "这里已经应用人脸/表情终审结果。给用户交付时只需要打开 `01_最终结果`。"
    summary["open_here"] = str(
        write_open_here(
            output_dir,
            title=readme_title,
            result_dir=result_dir,
            process_dir=process_dir,
            selection_json=final_selection,
            summary_json=summary_path,
            counts=summary.get("counts"),
            status=readme_status,
            next_action=next_action,
        )
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build/apply final face review for selected photos.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build face final review sheets and choices CSV.")
    build.add_argument("selection_json")
    build.add_argument("manifest")
    build.add_argument("--output", default=None)
    build.add_argument("--group-assignments", default=None)
    build.add_argument("--max-alternates", type=int, default=8)
    build.add_argument("--neighbor-window", type=int, default=8)
    build.add_argument("--columns", type=int, default=5)
    build.add_argument("--file-mode", choices=["copy", "hardlink"], default="hardlink")
    build.add_argument(
        "--review-scope",
        choices=["auto", "faces", "all"],
        default="auto",
        help="auto reviews face-signal KEEP rows, falling back to all KEEP rows when face data is unavailable.",
    )

    apply = subparsers.add_parser("apply", help="Apply edited face_review_choices.csv.")
    apply.add_argument("selection_json")
    apply.add_argument("manifest")
    apply.add_argument("choices_csv")
    apply.add_argument("--output", default=None)
    apply.add_argument("--file-mode", choices=["copy", "hardlink"], default="hardlink")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "build":
        return build_review_pack(args)
    if args.command == "apply":
        return apply_review_choices(args)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
