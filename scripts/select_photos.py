#!/usr/bin/env python3
"""Create a machine triage candidate pool for later model visual review."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

from export_selection import DECISION_ORDER, DRAFT_RESULT_DIR, PROCESS_DIR, copy_rows, write_list, write_open_here


HARD_REJECT_FLAGS = {"decode_failed", "low_resolution"}
STRICT_REJECT_FLAGS = {"decode_failed", "low_resolution", "severe_blur"}
SOFT_FLAGS = {
    "soft_focus_risk",
    "relative_low_sharpness",
    "underexposure_risk",
    "overexposure_risk",
    "low_contrast",
    "face_eye_review",
    "eye_closed_review",
    "eye_asymmetry_review",
}
FACE_REVIEW_FLAGS = {"face_eye_review", "eye_closed_review", "eye_asymmetry_review"}
PEOPLE_BRIEF_HINTS = {
    "person",
    "people",
    "portrait",
    "face",
    "faces",
    "family",
    "wedding",
    "event",
    "social",
    "post",
    "人",
    "人物",
    "人像",
    "脸",
    "表情",
    "婚礼",
    "家庭",
    "合影",
    "朋友圈",
    "小红书",
    "抖音",
}


def load_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "items" not in data or not isinstance(data["items"], list):
        raise SystemExit("manifest.json must contain an item list.")
    return data


def score_item(item: Dict[str, Any]) -> float:
    flags = set(item.get("risk_flags") or [])
    score = float(item.get("technical_score") or 0)
    if item.get("duplicate_group_id") and not item.get("script_duplicate_pick"):
        score -= 22
    for flag in flags:
        if flag in HARD_REJECT_FLAGS:
            score -= 60
        elif flag in SOFT_FLAGS:
            score -= 8
    contrast = item.get("contrast")
    colorfulness = item.get("colorfulness")
    blur = item.get("blur_score")
    if isinstance(contrast, (int, float)):
        score += min(5.0, contrast * 12)
    if isinstance(colorfulness, (int, float)):
        score += min(5.0, colorfulness / 25)
    if isinstance(blur, (int, float)):
        score += min(4.0, blur / 250)
    return score


def is_hard_reject(item: Dict[str, Any], cull_style: str = "gentle") -> bool:
    flags = set(item.get("risk_flags") or [])
    if item.get("decode_failed"):
        return True
    if cull_style == "strict":
        return bool(flags & STRICT_REJECT_FLAGS)
    if flags & HARD_REJECT_FLAGS:
        return True
    if cull_style == "balanced":
        return "severe_blur" in flags
    return False


def has_face_signal(item: Dict[str, Any]) -> bool:
    flags = set(item.get("risk_flags") or [])
    face_count = item.get("face_count")
    landmark_count = item.get("face_landmark_count")
    return (
        bool(isinstance(face_count, (int, float)) and face_count > 0)
        or bool(isinstance(landmark_count, (int, float)) and landmark_count > 0)
        or bool(flags & FACE_REVIEW_FLAGS)
    )


def has_people_brief_hint(brief: str) -> bool:
    lowered = (brief or "").lower()
    return any(hint in lowered for hint in PEOPLE_BRIEF_HINTS)


def face_review_status(items: Sequence[Dict[str, Any]], keep_paths: Set[str], brief: str) -> Dict[str, Any]:
    face_keep_count = sum(1 for item in items if item["path"] in keep_paths and has_face_signal(item))
    people_hint = has_people_brief_hint(brief)
    required = face_keep_count > 0 or people_hint
    return {
        "face_keep_count": face_keep_count,
        "people_brief_hint": people_hint,
        "requires_face_final_review": required,
        "face_final_review_status": "required_before_delivery" if required else "not_required_by_detected_content",
    }


def sorted_candidates(items: Sequence[Dict[str, Any]], cull_style: str) -> List[Tuple[float, Dict[str, Any]]]:
    candidates = []
    for item in items:
        if is_hard_reject(item, cull_style):
            continue
        candidates.append((score_item(item), item))
    return sorted(
        candidates,
        key=lambda pair: (
            -pair[0],
            str(pair[1].get("capture_time") or ""),
            str(pair[1].get("relative_path") or "").lower(),
        ),
    )


def choose_keep(items: Sequence[Dict[str, Any]], keep_count: int, cull_style: str) -> Set[str]:
    keep_paths: Set[str] = set()
    used_groups: Set[str] = set()
    ranked = sorted_candidates(items, cull_style)

    # First pass: one representative per near-duplicate group.
    for _score, item in ranked:
        if len(keep_paths) >= keep_count:
            break
        group_id = item.get("duplicate_group_id")
        if group_id and group_id in used_groups:
            continue
        if group_id and not item.get("script_duplicate_pick"):
            continue
        keep_paths.add(item["path"])
        if group_id:
            used_groups.add(str(group_id))

    # Fill pass: allow alternates only if the target count is larger than the
    # number of strong unique moments.
    for _score, item in ranked:
        if len(keep_paths) >= keep_count:
            break
        keep_paths.add(item["path"])
    return keep_paths


def build_selection(manifest: Dict[str, Any], keep_count: int, brief: str, cull_style: str) -> Dict[str, Any]:
    items = manifest["items"]
    keep_paths = choose_keep(items, keep_count, cull_style)
    selections = []
    for item in items:
        flags = set(item.get("risk_flags") or [])
        if item["path"] in keep_paths:
            decision = "KEEP"
            rating = 5
            label = "green"
            reason = "Selected for the requested final count."
        elif is_hard_reject(item, cull_style):
            decision = "REJECT"
            rating = 1 if not item.get("decode_failed") else 0
            label = "red"
            reason = "Clear mechanical reject under the selected culling style."
        else:
            decision = "UNSELECTED"
            rating = 2
            label = "blue"
            if item.get("duplicate_group_id") and not item.get("script_duplicate_pick"):
                decision = "REVIEW"
                rating = 3
                label = "yellow"
                reason = "Duplicate/sequence alternate. Keep in 待定 for expression, pose, and client taste review."
            else:
                reason = "Not selected by the machine candidate pass. This is 未入选, not 废片."
        if flags:
            reason += " Flags: " + ", ".join(sorted(flags)) + "."
        selections.append(
            {
                "path": item["path"],
                "relative_path": item.get("relative_path"),
                "decision": decision,
                "rating": rating,
                "label": label,
                "score": round(score_item(item), 3),
                "reason": reason,
            }
        )
    face_status = face_review_status(items, keep_paths, brief)
    return {
        "source_manifest": str(Path(manifest.get("output_dir", ".")) / "manifest.json"),
        "target_keep_count": keep_count,
        "actual_keep_count": sum(1 for row in selections if row["decision"] == "KEEP"),
        "cull_style": cull_style,
        "selection_brief": brief,
        **face_status,
        "requires_model_scene_review": True,
        "review_status": "machine_triage_only_requires_model_scene_review",
        "selection_notes": "Machine triage only. Scripts may clean, group, flag risk, and build a candidate pool, but the agent/model must visually identify scenes, compare within scenes, and decide the final set.",
        "selections": selections,
    }


def write_exports(selection: Dict[str, Any], output_dir: Path, copy_to: Path | None, file_mode: str) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = selection["selections"]
    by_decision = {decision: [item for item in rows if item["decision"] == decision] for decision in DECISION_ORDER}
    write_list(output_dir / "selection_keep.txt", by_decision["KEEP"])
    write_list(output_dir / "selection_review.txt", by_decision["REVIEW"])
    write_list(output_dir / "selection_unselected.txt", by_decision["UNSELECTED"])
    write_list(output_dir / "selection_reject.txt", by_decision["REJECT"])
    copied = copy_rows(rows, copy_to, "zh", file_mode) if copy_to else []
    summary = {
        "counts": dict(Counter(item["decision"] for item in rows)),
        "output_dir": str(output_dir),
        "copy_to": str(copy_to) if copy_to else None,
        "file_mode": file_mode,
        "copied_count": len([row for row in copied if row["status"] == "copied"]),
        "hardlinked_count": len([row for row in copied if row["status"] == "hardlinked"]),
        "face_keep_count": selection.get("face_keep_count", 0),
        "requires_face_final_review": selection.get("requires_face_final_review", False),
        "face_final_review_status": selection.get("face_final_review_status"),
        "requires_model_scene_review": selection.get("requires_model_scene_review", True),
        "review_status": selection.get("review_status"),
    }
    (output_dir / "selection_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a script triage candidate pool from manifest.json.")
    parser.add_argument("manifest", help="Path to manifest.json.")
    parser.add_argument("--keep-count", type=int, required=True, help="Number of candidate 精选 photos to place in the model-review pool.")
    parser.add_argument("--brief", default="", help="User instruction or aesthetic brief for agent review notes.")
    parser.add_argument(
        "--cull-style",
        choices=["gentle", "balanced", "strict"],
        default="balanced",
        help="balanced rejects obvious technical failures while keeping people duplicates in 待定.",
    )
    parser.add_argument("--output", default=None, help="Output folder. Defaults to manifest parent/candidate_selection.")
    parser.add_argument("--copy-to", default=None, help="Create candidate 精选/待定/废片 copy folders here.")
    parser.add_argument("--file-mode", choices=["copy", "hardlink"], default="copy", help="Use hardlink to avoid extra disk usage on the same volume.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.keep_count <= 0:
        raise SystemExit("--keep-count must be positive.")
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    output_dir = Path(args.output).expanduser().resolve() if args.output else manifest_path.parent / "candidate_selection"
    process_dir = output_dir / PROCESS_DIR
    copy_to = Path(args.copy_to).expanduser().resolve() if args.copy_to else output_dir / DRAFT_RESULT_DIR
    selection = build_selection(manifest, args.keep_count, args.brief, args.cull_style)
    output_dir.mkdir(parents=True, exist_ok=True)
    process_dir.mkdir(parents=True, exist_ok=True)
    selection_path = process_dir / "selection.json"
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = write_exports(selection, process_dir, copy_to, args.file_mode)
    summary["selection_json"] = str(selection_path)
    summary["human_output_dir"] = str(output_dir)
    summary["result_dir"] = str(copy_to)
    summary["process_dir"] = str(process_dir)
    summary["open_here"] = str(
        write_open_here(
            output_dir,
            title="Steven Photo Director 模型审片候选",
            result_dir=copy_to,
            process_dir=process_dir,
            selection_json=selection_path,
            summary_json=process_dir / "selection_summary.json",
            counts=summary.get("counts"),
            status="机器只完成了清洗、风险标记和候选池整理；这不是最终选片。必须让智能体/模型看图做场景识别、场景取舍和最终精选。",
            next_action=(
                "先看 `01_模型审片候选/精选`、`01_模型审片候选/待定`、重复/风险对比图和场景分组。"
                "模型确认每个场景的意义、保留价值和最好瞬间后，再导出 `01_最终结果`。"
            ),
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
