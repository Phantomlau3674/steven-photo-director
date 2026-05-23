#!/usr/bin/env python3
"""Build score-assisted review boards after model/user scene choices."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from scan_photos import build_sheet


ROLE_MULTIPLIER = {
    "hero": 2.2,
    "strong": 1.6,
    "support": 1.0,
    "weak": 0.55,
    "drop": 0.0,
}


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_method_scores(path: Path | None) -> Dict[str, float]:
    if not path or not path.exists():
        return {}
    scores: Dict[str, float] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = row.get("path") or ""
            try:
                score = float(row.get("consensus_score") or row.get("technical_score") or 0)
            except ValueError:
                score = 0.0
            if key:
                scores[key] = score
    return scores


def load_group_guidance(choices_path: Path | None, assignments_path: Path | None) -> Dict[str, Any]:
    if not choices_path:
        return {
            "actions": {},
            "keep_counts": {},
            "scene_quality": {},
            "scene_roles": {},
            "evidence": {},
            "memory_notes": {},
            "path_to_group": {},
            "has_include": False,
            "reviewed": False,
        }
    choices = choices_path.expanduser().resolve()
    if assignments_path:
        assignments = assignments_path.expanduser().resolve()
    else:
        assignments = choices.parent / "group_assignments.json"
    assignment_data = load_json(assignments)
    actions: Dict[str, str] = {}
    keep_counts: Dict[str, int] = {}
    scene_quality: Dict[str, float] = {}
    scene_roles: Dict[str, str] = {}
    evidence: Dict[str, str] = {}
    memory_notes: Dict[str, str] = {}
    with choices.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            group_id = str(row.get("group_id") or "").strip()
            if not group_id:
                continue
            action = str(row.get("action") or "").strip().lower()
            if action:
                actions[group_id] = action
            role = str(row.get("scene_role") or "").strip().lower()
            if role:
                scene_roles[group_id] = role
            quality_text = str(row.get("scene_quality") or "").strip()
            if quality_text:
                try:
                    scene_quality[group_id] = max(0.0, min(5.0, float(quality_text)))
                except ValueError:
                    pass
            note = str(row.get("memory_note") or "").strip()
            if note:
                memory_notes[group_id] = note
            evidence_text = str(row.get("evidence") or row.get("notes") or "").strip()
            if evidence_text:
                evidence[group_id] = evidence_text
            keep_count_text = str(row.get("keep_count") or "").strip()
            if keep_count_text:
                try:
                    keep_counts[group_id] = max(0, int(keep_count_text))
                except ValueError:
                    pass
    return {
        "actions": actions,
        "keep_counts": keep_counts,
        "scene_quality": scene_quality,
        "scene_roles": scene_roles,
        "evidence": evidence,
        "memory_notes": memory_notes,
        "path_to_group": assignment_data.get("path_to_group", {}),
        "has_include": any(action in {"include", "priority"} for action in actions.values()),
        "reviewed": True,
    }


def group_id_for(item: Dict[str, Any], guidance: Dict[str, Any]) -> str:
    return str(guidance.get("path_to_group", {}).get(item.get("path")) or "ungrouped")


def group_action(group_id: str, guidance: Dict[str, Any]) -> str:
    return str(guidance.get("actions", {}).get(group_id) or "")


def group_allowed(group_id: str, guidance: Dict[str, Any]) -> bool:
    action = group_action(group_id, guidance)
    role = str(guidance.get("scene_roles", {}).get(group_id) or "")
    if action == "exclude" or role == "drop":
        return False
    if guidance.get("has_include"):
        return action in {"include", "priority"}
    return True


def scene_multiplier(group_id: str, guidance: Dict[str, Any]) -> float:
    action = group_action(group_id, guidance)
    role = str(guidance.get("scene_roles", {}).get(group_id) or "").lower()
    quality = guidance.get("scene_quality", {}).get(group_id)
    multiplier = ROLE_MULTIPLIER.get(role, 1.0)
    if quality is not None:
        multiplier *= max(0.35, min(2.0, float(quality) / 3.0))
    if action == "priority":
        multiplier *= 1.35
    if action == "include":
        multiplier *= 1.1
    return max(0.0, multiplier)


def scene_review_target(group_id: str, guidance: Dict[str, Any], top_per_scene: int) -> int:
    explicit = guidance.get("keep_counts", {}).get(group_id)
    if explicit:
        return max(top_per_scene, explicit * 4)
    multiplier = scene_multiplier(group_id, guidance)
    if multiplier <= 0:
        return 0
    return max(3, int(round(top_per_scene * multiplier)))


def review_score(item: Dict[str, Any], method_scores: Dict[str, float]) -> float:
    path = str(item.get("path") or "")
    if path in method_scores:
        return float(method_scores[path])
    return float(item.get("technical_score") or 0) / 100.0


def board_item(item: Dict[str, Any], group_id: str, score: float) -> Dict[str, Any]:
    clone = dict(item)
    rel = str(item.get("relative_path") or Path(str(item.get("path") or "")).name)
    clone["scene_group_id"] = group_id
    clone["relative_path"] = f"{group_id} score {score:.3f} {rel}"
    clone["technical_score"] = f"{score:.3f}"
    return clone


def sort_group_items(items: Sequence[Dict[str, Any]], method_scores: Dict[str, float]) -> List[Dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            -review_score(item, method_scores),
            item.get("duplicate_group_id") or "",
            item.get("duplicate_rank") or 9999,
            str(item.get("relative_path") or "").lower(),
        ),
    )


def round_robin_by_scene(scene_items: Dict[str, List[Dict[str, Any]]], guidance: Dict[str, Any]) -> List[Dict[str, Any]]:
    ordered_groups = sorted(
        scene_items,
        key=lambda group_id: (
            0 if group_action(group_id, guidance) == "priority" else 1,
            group_id,
        ),
    )
    merged: List[Dict[str, Any]] = []
    max_len = max((len(rows) for rows in scene_items.values()), default=0)
    for index in range(max_len):
        for group_id in ordered_groups:
            rows = scene_items[group_id]
            if index < len(rows):
                merged.append(rows[index])
    return merged


def write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build score-assisted review boards after scene decisions.")
    parser.add_argument("manifest", help="Path to manifest.json.")
    parser.add_argument("--group-choices", default=None, help="Model/user-reviewed group_choices.csv.")
    parser.add_argument("--group-assignments", default=None, help="group_assignments.json.")
    parser.add_argument("--method-votes", default=None, help="method_votes.csv from ensemble_select_photos.py.")
    parser.add_argument("--output", default=None, help="Output folder. Defaults to manifest parent/review_boards.")
    parser.add_argument("--target-count", type=int, default=0, help="Final target count, used to size the merged review pool.")
    parser.add_argument("--top-per-scene", type=int, default=12, help="Max scored candidates per selected scene board.")
    parser.add_argument("--board-size", type=int, default=24, help="Images per merged board.")
    parser.add_argument("--merged-limit", type=int, default=0, help="Max images in merged review pool. Default is target_count*4 or 120.")
    parser.add_argument("--mode", choices=["board", "super"], default="board", help="board uses contact sheets; super also writes a one-by-one shortlist.")
    parser.add_argument("--super-multiplier", type=int, default=3, help="Super mode shortlist size is target_count multiplied by this value.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_json(manifest_path)
    output_dir = Path(args.output).expanduser().resolve() if args.output else manifest_path.parent / "review_boards"
    output_dir.mkdir(parents=True, exist_ok=True)
    scene_dir = output_dir / "01_场景分数图板"
    merged_dir = output_dir / "02_合并精审图板"
    scene_dir.mkdir(parents=True, exist_ok=True)
    merged_dir.mkdir(parents=True, exist_ok=True)

    guidance = load_group_guidance(
        Path(args.group_choices) if args.group_choices else None,
        Path(args.group_assignments) if args.group_assignments else None,
    )
    method_scores = load_method_scores(Path(args.method_votes).expanduser().resolve() if args.method_votes else None)

    scene_items: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    pool_rows: List[Dict[str, Any]] = []
    for item in manifest.get("items", []):
        group_id = group_id_for(item, guidance)
        if not group_allowed(group_id, guidance):
            continue
        scene_items[group_id].append(item)

    selected_for_merge: Dict[str, List[Dict[str, Any]]] = {}
    for group_id, rows in sorted(scene_items.items()):
        ordered = sort_group_items(rows, method_scores)
        limit = scene_review_target(group_id, guidance, args.top_per_scene)
        if limit <= 0:
            continue
        limited = ordered[: max(1, limit)]
        board_rows = [board_item(item, group_id, review_score(item, method_scores)) for item in limited]
        selected_for_merge[group_id] = board_rows
        build_sheet(board_rows, scene_dir / f"{group_id}.jpg", f"{group_id} score-assisted candidates", columns=4)
        for rank, item in enumerate(limited, 1):
            score = review_score(item, method_scores)
            pool_rows.append(
                {
                    "scene_group_id": group_id,
                    "scene_action": group_action(group_id, guidance),
                    "scene_quality": guidance.get("scene_quality", {}).get(group_id),
                    "scene_role": guidance.get("scene_roles", {}).get(group_id),
                    "scene_multiplier": round(scene_multiplier(group_id, guidance), 4),
                    "scene_evidence": guidance.get("evidence", {}).get(group_id),
                    "memory_note": guidance.get("memory_notes", {}).get(group_id),
                    "scene_rank": rank,
                    "review_score": round(score, 6),
                    "technical_score": item.get("technical_score"),
                    "relative_path": item.get("relative_path"),
                    "path": item.get("path"),
                    "duplicate_group_id": item.get("duplicate_group_id"),
                    "duplicate_rank": item.get("duplicate_rank"),
                    "risk_flags": ",".join(item.get("risk_flags") or []),
                    "face_count": item.get("face_count"),
                }
            )

    merged = round_robin_by_scene(selected_for_merge, guidance)
    if args.merged_limit > 0:
        merged_limit = args.merged_limit
    elif args.target_count > 0:
        merged_limit = max(24, args.target_count * 4)
    else:
        merged_limit = 120
    merged = merged[:merged_limit]
    for index in range(0, len(merged), args.board_size):
        chunk = merged[index : index + args.board_size]
        if not chunk:
            continue
        build_sheet(
            chunk,
            merged_dir / f"merged_review_{index // args.board_size + 1:03d}.jpg",
            f"Merged score-assisted review {index // args.board_size + 1}",
            columns=4,
        )

    write_csv(output_dir / "review_pool.csv", pool_rows)
    scene_memory_rows = []
    for group_id in sorted(scene_items):
        if not group_allowed(group_id, guidance):
            continue
        scene_memory_rows.append(
            {
                "scene_group_id": group_id,
                "action": group_action(group_id, guidance),
                "scene_quality": guidance.get("scene_quality", {}).get(group_id),
                "scene_role": guidance.get("scene_roles", {}).get(group_id),
                "keep_count": guidance.get("keep_counts", {}).get(group_id),
                "review_candidates": len(selected_for_merge.get(group_id, [])),
                "evidence": guidance.get("evidence", {}).get(group_id),
                "memory_note": guidance.get("memory_notes", {}).get(group_id),
            }
        )
    write_csv(output_dir / "scene_review_memory.csv", scene_memory_rows)

    super_list_path = None
    if args.mode == "super":
        if args.target_count > 0:
            super_limit = max(args.target_count, args.target_count * max(1, args.super_multiplier))
        else:
            super_limit = min(len(merged), 80)
        super_rows = []
        for index, item in enumerate(merged[:super_limit], 1):
            original_path = str(item.get("path") or "")
            group_id = str(item.get("scene_group_id") or str(item.get("relative_path") or "").split(" ", 1)[0])
            super_rows.append(
                {
                    "review_order": index,
                    "scene_group_id": group_id,
                    "path": original_path,
                    "relative_path": item.get("relative_path"),
                    "task": "open_original_and_decide_KEEP_REVIEW_REJECT",
                }
            )
        super_list_path = output_dir / "03_超级精选逐张清单.csv"
        write_csv(super_list_path, super_rows)
        (output_dir / "03_超级精选逐张清单.md").write_text(
            "\n".join(
                [
                    "# Super Select One-By-One List",
                    "",
                    "Use only after scene choices and score-assisted boards. Open originals in this order for final tie-breaks, faces, expressions, and delivery confidence.",
                    "",
                    "Do not use this mode for the whole folder unless the user explicitly accepts the token/time cost.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    index_lines = [
        "# Score-Assisted Review Boards",
        "",
        "Use these boards after scene choices are set. Scores decide board order only; the model/editor still decides final photos.",
        "",
        "## Review Order",
        "",
        "1. Open `01_场景分数图板/` and compare candidates inside each chosen scene.",
        "2. Open `02_合并精审图板/` for cross-scene balance and final shortlist.",
        "3. Open original files only for tie-breaks, faces, expressions, and final delivery checks.",
        "",
        "Do not restart from a global top-N score list.",
        "",
        "## Files",
        "",
        "- `review_pool.csv`: machine-readable list of board items and scores.",
        "- `scene_review_memory.csv`: temporary scene memory; use it to remember why good scenes get more picks and weak scenes get fewer.",
        "- `01_场景分数图板/`: per-scene score-assisted boards.",
        "- `02_合并精审图板/`: merged cross-scene boards.",
        "",
    ]
    (output_dir / "review_board_index.md").write_text("\n".join(index_lines), encoding="utf-8")
    summary = {
        "output_dir": str(output_dir),
        "scene_boards": len(selected_for_merge),
        "merged_items": len(merged),
        "reviewed_scene_choices": bool(guidance.get("reviewed")),
        "method_scores_used": bool(method_scores),
        "mode": args.mode,
        "review_pool_csv": str(output_dir / "review_pool.csv"),
        "scene_review_memory_csv": str(output_dir / "scene_review_memory.csv"),
        "super_select_csv": str(super_list_path) if super_list_path else None,
        "index": str(output_dir / "review_board_index.md"),
    }
    (output_dir / "review_board_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
