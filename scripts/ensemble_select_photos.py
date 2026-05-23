#!/usr/bin/env python3
"""Multi-method photo candidate triage with cross-check votes.

This is the "firepower" candidate builder: several lightweight methods score
the same manifest, then the script organizes a consensus candidate pool and
exposes disagreements for model/editor visual review.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence, Set, Tuple

from export_selection import DECISION_ORDER, DRAFT_RESULT_DIR, PROCESS_DIR, copy_rows, write_list, write_open_here
from select_photos import face_review_status, is_hard_reject


METHODS = ("technical", "duplicate_representative", "aesthetic_proxy", "people_gentle")
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


def load_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "items" not in data or not isinstance(data["items"], list):
        raise SystemExit("manifest.json must contain an item list.")
    return data


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def exposure_balance(item: Dict[str, Any]) -> float:
    mean = item.get("brightness_mean")
    shadow = item.get("shadow_clip_pct") or 0
    highlight = item.get("highlight_clip_pct") or 0
    if not isinstance(mean, (int, float)):
        return 50.0
    distance = abs(float(mean) - 0.5)
    return clamp(100 - distance * 120 - float(shadow) * 35 - float(highlight) * 45)


def technical_score(item: Dict[str, Any]) -> float:
    flags = set(item.get("risk_flags") or [])
    score = float(item.get("technical_score") or 0)
    if "severe_blur" in flags:
        score -= 15
    if "soft_focus_risk" in flags:
        score -= 8
    if "relative_low_sharpness" in flags:
        score -= 5
    if "underexposure_risk" in flags or "overexposure_risk" in flags:
        score -= 8
    return clamp(score)


def duplicate_representative_score(item: Dict[str, Any]) -> float:
    score = technical_score(item)
    group_id = item.get("duplicate_group_id")
    rank = item.get("duplicate_rank") or 999
    if group_id:
        score += 14 if rank == 1 else max(-18, 8 - (rank * 3))
    else:
        score += 8
    return clamp(score)


def aesthetic_proxy_score(item: Dict[str, Any]) -> float:
    flags = set(item.get("risk_flags") or [])
    score = 45.0
    score += exposure_balance(item) * 0.25
    contrast = item.get("contrast")
    colorfulness = item.get("colorfulness")
    blur = item.get("blur_score")
    if isinstance(contrast, (int, float)):
        score += min(12.0, float(contrast) * 45)
    if isinstance(colorfulness, (int, float)):
        score += min(12.0, float(colorfulness) / 8)
    if isinstance(blur, (int, float)):
        score += min(8.0, math.log10(float(blur) + 1) * 3)
    score -= len(flags & SOFT_FLAGS) * 4
    if "near_duplicate" in flags and not item.get("script_duplicate_pick"):
        score -= 4
    return clamp(score)


def people_gentle_score(item: Dict[str, Any]) -> float:
    score = technical_score(item) * 0.55 + aesthetic_proxy_score(item) * 0.45
    if item.get("duplicate_group_id") and not item.get("script_duplicate_pick"):
        score -= 7
    flags = set(item.get("risk_flags") or [])
    if "severe_blur" in flags:
        score -= 8
    return clamp(score)


SCORE_FUNCS: Dict[str, Callable[[Dict[str, Any]], float]] = {
    "technical": technical_score,
    "duplicate_representative": duplicate_representative_score,
    "aesthetic_proxy": aesthetic_proxy_score,
    "people_gentle": people_gentle_score,
}


def load_group_guidance(
    choices_path: str | None,
    assignments_path: str | None,
) -> Dict[str, Any]:
    if not choices_path:
        return {
            "path_to_group": {},
            "actions": {},
            "keep_counts": {},
            "has_include": False,
        }
    choices = Path(choices_path).expanduser().resolve()
    if assignments_path:
        assignments = Path(assignments_path).expanduser().resolve()
    else:
        assignments = choices.parent / "group_assignments.json"
    assignment_data = json.loads(assignments.read_text(encoding="utf-8-sig"))
    actions: Dict[str, str] = {}
    keep_counts: Dict[str, int] = {}
    with choices.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            group_id = str(row.get("group_id") or "").strip()
            if not group_id:
                continue
            action = str(row.get("action") or "").strip().lower()
            if action:
                actions[group_id] = action
            keep_count_text = str(row.get("keep_count") or "").strip()
            if keep_count_text:
                try:
                    keep_counts[group_id] = max(0, int(keep_count_text))
                except ValueError:
                    pass
    return {
        "path_to_group": assignment_data.get("path_to_group", {}),
        "actions": actions,
        "keep_counts": keep_counts,
        "has_include": any(action in {"include", "priority"} for action in actions.values()),
    }


def group_action(item: Dict[str, Any], guidance: Dict[str, Any]) -> str:
    group_id = guidance.get("path_to_group", {}).get(item.get("path"))
    if not group_id:
        return ""
    return guidance.get("actions", {}).get(group_id, "")


def is_group_allowed(item: Dict[str, Any], guidance: Dict[str, Any]) -> bool:
    action = group_action(item, guidance)
    if action == "exclude":
        return False
    if guidance.get("has_include"):
        return action in {"include", "priority"}
    return True


def method_rankings(
    items: Sequence[Dict[str, Any]],
    cull_style: str,
    group_guidance: Dict[str, Any],
) -> Dict[str, List[Tuple[float, Dict[str, Any]]]]:
    rankings: Dict[str, List[Tuple[float, Dict[str, Any]]]] = {}
    for method, scorer in SCORE_FUNCS.items():
        rows = []
        for item in items:
            if is_hard_reject(item, cull_style):
                continue
            if not is_group_allowed(item, group_guidance):
                continue
            score = scorer(item)
            action = group_action(item, group_guidance)
            if action == "priority":
                score += 12
            elif action == "include":
                score += 6
            rows.append((score, item))
        rows.sort(
            key=lambda pair: (
                -pair[0],
                str(pair[1].get("capture_time") or ""),
                str(pair[1].get("relative_path") or "").lower(),
            )
        )
        rankings[method] = rows
    return rankings


def vote_rows(
    items: Sequence[Dict[str, Any]],
    rankings: Dict[str, List[Tuple[float, Dict[str, Any]]]],
    keep_count: int,
) -> Dict[str, Dict[str, Any]]:
    item_by_path = {item["path"]: item for item in items}
    votes: Dict[str, Dict[str, Any]] = {
        path: {
            "path": path,
            "relative_path": item.get("relative_path"),
            "method_scores": {},
            "method_ranks": {},
            "votes": 0,
            "consensus_score": 0.0,
        }
        for path, item in item_by_path.items()
    }
    vote_window = max(keep_count, math.ceil(keep_count * 1.7))
    for method, rows in rankings.items():
        for rank, (score, item) in enumerate(rows, 1):
            path = item["path"]
            votes[path]["method_scores"][method] = round(score, 3)
            votes[path]["method_ranks"][method] = rank
            if rank <= vote_window:
                votes[path]["votes"] += 1
                votes[path]["consensus_score"] += max(0.0, vote_window - rank + 1) / vote_window
    for path, item in item_by_path.items():
        votes[path]["consensus_score"] += people_gentle_score(item) / 100.0
    return votes


def select_consensus(
    items: Sequence[Dict[str, Any]],
    vote_data: Dict[str, Dict[str, Any]],
    keep_count: int,
    cull_style: str,
    group_guidance: Dict[str, Any],
) -> Set[str]:
    candidates = [
        item
        for item in items
        if not is_hard_reject(item, cull_style)
        and is_group_allowed(item, group_guidance)
    ]
    ranked = sorted(
        candidates,
        key=lambda item: (
            -vote_data[item["path"]]["votes"],
            -vote_data[item["path"]]["consensus_score"],
            str(item.get("capture_time") or ""),
            str(item.get("relative_path") or "").lower(),
        ),
    )
    keep: Set[str] = set()
    used_groups: Set[str] = set()
    bucket_counts: Counter[str] = Counter()
    max_per_bucket = max(2, math.ceil(keep_count / 12))
    path_to_group = group_guidance.get("path_to_group", {})
    group_keep_counts = group_guidance.get("keep_counts", {})
    if group_keep_counts:
        for group_id, group_target in group_keep_counts.items():
            if len(keep) >= keep_count:
                break
            group_items = [
                item for item in ranked
                if path_to_group.get(item.get("path")) == group_id
            ]
            selected = 0
            for item in group_items:
                if len(keep) >= keep_count or selected >= group_target:
                    break
                keep.add(item["path"])
                selected += 1
    for item in ranked:
        if len(keep) >= keep_count:
            break
        group_id = item.get("duplicate_group_id")
        if group_id and group_id in used_groups:
            continue
        bucket = scene_bucket(item)
        if bucket_counts[bucket] >= max_per_bucket:
            continue
        keep.add(item["path"])
        if group_id:
            used_groups.add(str(group_id))
        bucket_counts[bucket] += 1
    for item in ranked:
        if len(keep) >= keep_count:
            break
        bucket = scene_bucket(item)
        relaxed_limit = max_per_bucket + 2
        if bucket_counts[bucket] >= relaxed_limit and len(keep) < max(1, int(keep_count * 0.8)):
            continue
        keep.add(item["path"])
        bucket_counts[bucket] += 1
    return keep


def scene_bucket(item: Dict[str, Any], bucket_size: int = 25) -> str:
    rel = str(item.get("relative_path") or "")
    match = re.search(r"(\d{3,})", rel)
    if not match:
        return rel[:20].lower()
    number = int(match.group(1))
    return f"seq_{number // bucket_size:04d}"


def build_selection(
    manifest: Dict[str, Any],
    keep_count: int,
    brief: str,
    cull_style: str,
    group_guidance: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    items = manifest["items"]
    rankings = method_rankings(items, cull_style, group_guidance)
    votes = vote_rows(items, rankings, keep_count)
    keep_paths = select_consensus(items, votes, keep_count, cull_style, group_guidance)
    selections = []
    audit_rows = []
    for item in items:
        path = item["path"]
        vote = votes[path]
        flags = set(item.get("risk_flags") or [])
        if path in keep_paths:
            decision = "KEEP"
            rating = 5
            label = "green"
            reason = f"Consensus pick: {vote['votes']} method votes."
        elif not is_group_allowed(item, group_guidance):
            decision = "UNSELECTED"
            rating = 2
            label = "blue"
            reason = "Not selected by model/user scene choice. This is 未入选, not 废片."
        elif is_hard_reject(item, cull_style):
            decision = "REJECT"
            rating = 1 if not item.get("decode_failed") else 0
            label = "red"
            reason = "Clear mechanical reject under the selected culling style."
        else:
            decision = "REVIEW"
            rating = 3
            label = "yellow"
            if vote["votes"] > 0:
                decision = "REVIEW"
                rating = 3
                label = "yellow"
                reason = f"Method disagreement/near miss: {vote['votes']} method votes."
            elif item.get("duplicate_group_id"):
                decision = "REVIEW"
                rating = 3
                label = "yellow"
                reason = "Duplicate/sequence alternate kept for expression, pose, and taste review."
            else:
                decision = "UNSELECTED"
                rating = 2
                label = "blue"
                reason = "Not selected by consensus candidate pass. This is 未入选, not 废片."
        if flags:
            reason += " Flags: " + ", ".join(sorted(flags)) + "."
        selections.append(
            {
                "path": path,
                "relative_path": item.get("relative_path"),
                "decision": decision,
                "rating": rating,
                "label": label,
                "votes": vote["votes"],
                "consensus_score": round(vote["consensus_score"], 4),
                "technical_score": item.get("technical_score"),
                "blur_score": item.get("blur_score"),
                "risk_flags": item.get("risk_flags") or [],
                "first_pass_decision": item.get("first_pass_decision"),
                "scene_group_id": group_guidance.get("path_to_group", {}).get(path),
                "method_scores": vote["method_scores"],
                "method_ranks": vote["method_ranks"],
                "reason": reason,
            }
        )
        rank_values = list(vote["method_ranks"].values())
        rank_spread = max(rank_values) - min(rank_values) if rank_values else None
        audit_rows.append(
            {
                "relative_path": item.get("relative_path"),
                "decision": decision,
                "votes": vote["votes"],
                "consensus_score": round(vote["consensus_score"], 4),
                "rank_spread": rank_spread,
                "duplicate_group_id": item.get("duplicate_group_id"),
                "duplicate_rank": item.get("duplicate_rank"),
                "scene_group_id": group_guidance.get("path_to_group", {}).get(path),
                "group_action": group_action(item, group_guidance),
                "risk_flags": ",".join(sorted(flags)),
                **{f"{method}_score": vote["method_scores"].get(method) for method in METHODS},
                **{f"{method}_rank": vote["method_ranks"].get(method) for method in METHODS},
                "path": path,
            }
        )
    face_status = face_review_status(items, keep_paths, brief)
    selection = {
        "source_manifest": str(Path(manifest.get("output_dir", ".")) / "manifest.json"),
        "target_keep_count": keep_count,
        "actual_keep_count": sum(1 for row in selections if row["decision"] == "KEEP"),
        "cull_style": cull_style,
        "selection_brief": brief,
        "selection_method": "ensemble",
        **face_status,
        "requires_model_scene_review": True,
        "review_status": "machine_triage_only_requires_model_scene_review",
        "selection_notes": "Machine triage only. Ensemble scores only organize candidates and disagreements; the agent/model must visually identify scenes, compare within scenes, and decide the final set.",
        "selections": selections,
    }
    return selection, audit_rows


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_exports(selection: Dict[str, Any], audit_rows: Sequence[Dict[str, Any]], output_dir: Path, copy_to: Path | None, file_mode: str) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = selection["selections"]
    by_decision = {decision: [item for item in rows if item["decision"] == decision] for decision in DECISION_ORDER}
    write_list(output_dir / "selection_keep.txt", by_decision["KEEP"])
    write_list(output_dir / "selection_review.txt", by_decision["REVIEW"])
    write_list(output_dir / "selection_unselected.txt", by_decision["UNSELECTED"])
    write_list(output_dir / "selection_reject.txt", by_decision["REJECT"])
    write_csv(output_dir / "method_votes.csv", audit_rows)
    disagreement_rows = [
        row for row in audit_rows
        if row.get("decision") != "KEEP" and row.get("votes", 0) > 0
    ]
    disagreement_rows.sort(key=lambda row: (-int(row.get("votes") or 0), -float(row.get("consensus_score") or 0)))
    write_csv(output_dir / "method_disagreements.csv", disagreement_rows)
    copied = copy_rows(rows, copy_to, "zh", file_mode) if copy_to else []
    summary = {
        "counts": dict(Counter(item["decision"] for item in rows)),
        "output_dir": str(output_dir),
        "copy_to": str(copy_to) if copy_to else None,
        "file_mode": file_mode,
        "copied_count": len([row for row in copied if row["status"] == "copied"]),
        "hardlinked_count": len([row for row in copied if row["status"] == "hardlinked"]),
        "disagreement_count": len(disagreement_rows),
        "face_keep_count": selection.get("face_keep_count", 0),
        "requires_face_final_review": selection.get("requires_face_final_review", False),
        "face_final_review_status": selection.get("face_final_review_status"),
        "requires_model_scene_review": selection.get("requires_model_scene_review", True),
        "review_status": selection.get("review_status"),
    }
    (output_dir / "selection_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a candidate pool using multiple cross-checking methods.")
    parser.add_argument("manifest", help="Path to manifest.json.")
    parser.add_argument("--keep-count", type=int, required=True)
    parser.add_argument("--brief", default="")
    parser.add_argument("--cull-style", choices=["gentle", "balanced", "strict"], default="balanced")
    parser.add_argument("--output", default=None)
    parser.add_argument("--copy-to", default=None)
    parser.add_argument("--file-mode", choices=["copy", "hardlink"], default="copy")
    parser.add_argument("--group-choices", default=None, help="CSV from group_photos.py edited by the user.")
    parser.add_argument("--group-assignments", default=None, help="Optional group_assignments.json path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.keep_count <= 0:
        raise SystemExit("--keep-count must be positive.")
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    group_guidance = load_group_guidance(args.group_choices, args.group_assignments)
    output_dir = Path(args.output).expanduser().resolve() if args.output else manifest_path.parent / "ensemble_selection"
    process_dir = output_dir / PROCESS_DIR
    copy_to = Path(args.copy_to).expanduser().resolve() if args.copy_to else output_dir / DRAFT_RESULT_DIR
    selection, audit_rows = build_selection(manifest, args.keep_count, args.brief, args.cull_style, group_guidance)
    output_dir.mkdir(parents=True, exist_ok=True)
    process_dir.mkdir(parents=True, exist_ok=True)
    selection_path = process_dir / "selection.json"
    selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = write_exports(selection, audit_rows, process_dir, copy_to, args.file_mode)
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
            status="多方法脚本只完成候选池整理、风险标记和分歧暴露；这不是最终选片。必须让智能体/模型看图做场景识别、场景取舍和最终精选。",
            next_action=(
                "先看 `01_模型审片候选/精选`、`01_模型审片候选/待定`、method_disagreements.csv、重复/风险对比图和场景分组。"
                "模型确认每个场景的意义、保留价值和最好瞬间后，再导出 `01_最终结果`。"
            ),
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
