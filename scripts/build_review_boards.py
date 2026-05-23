#!/usr/bin/env python3
"""Build editable visual review surfaces after model/user scene choices.

The script deliberately avoids final taste decisions. It creates comparison
surfaces so the model/editor can review scenes, near duplicates, possible
standouts, preference-seed neighbors, portraits, and final near misses.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from PIL import Image, ImageDraw, ImageOps

from scan_photos import build_sheet, hamming_hex


ROLE_MULTIPLIER = {
    "hero": 2.2,
    "strong": 1.6,
    "support": 1.0,
    "weak": 0.55,
    "drop": 0.0,
}

SOFT_RISK_FLAGS = {
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


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def load_method_scores(path: Path | None) -> Dict[str, float]:
    if not path or not path.exists():
        return {}
    scores: Dict[str, float] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = row.get("path") or ""
            score = as_float(row.get("consensus_score") or row.get("technical_score") or 0)
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
                scene_quality[group_id] = clamp(as_float(quality_text), 0.0, 5.0)
            note = str(row.get("memory_note") or "").strip()
            if note:
                memory_notes[group_id] = note
            evidence_text = str(row.get("evidence") or row.get("notes") or "").strip()
            if evidence_text:
                evidence[group_id] = evidence_text
            keep_count_text = str(row.get("keep_count") or "").strip()
            if keep_count_text:
                keep_counts[group_id] = max(0, as_int(keep_count_text))
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
    return as_float(item.get("technical_score")) / 100.0


def normalized_review_score(item: Dict[str, Any], method_scores: Dict[str, float]) -> float:
    score = review_score(item, method_scores)
    if score > 1.5:
        return clamp(score / 5.0)
    return clamp(score)


def has_face_signal(item: Dict[str, Any]) -> bool:
    flags = set(item.get("risk_flags") or [])
    return (
        as_float(item.get("face_count")) > 0
        or as_float(item.get("face_landmark_count")) > 0
        or bool(flags & FACE_REVIEW_FLAGS)
    )


def obvious_decode_failure(item: Dict[str, Any]) -> bool:
    return bool(item.get("decode_failed")) or "decode_failed" in set(item.get("risk_flags") or [])


def standout_score(item: Dict[str, Any], method_scores: Dict[str, float]) -> float:
    flags = set(item.get("risk_flags") or [])
    score = normalized_review_score(item, method_scores) * 0.52
    score += clamp(as_float(item.get("colorfulness")) / 90.0) * 0.17
    score += clamp(as_float(item.get("contrast")) * 1.4) * 0.12
    score += clamp(math.log10(as_float(item.get("blur_score")) + 1) / 4.0) * 0.11
    if has_face_signal(item):
        score += 0.05
    if item.get("script_duplicate_pick"):
        score += 0.03
    if item.get("duplicate_group_id") and not item.get("script_duplicate_pick"):
        score -= 0.04
    score -= min(0.28, len(flags & SOFT_RISK_FLAGS) * 0.055)
    if "low_resolution" in flags:
        score -= 0.1
    return round(score, 6)


def standout_reason(item: Dict[str, Any]) -> str:
    reasons = []
    if as_float(item.get("colorfulness")) >= 35:
        reasons.append("color")
    if as_float(item.get("contrast")) >= 0.18:
        reasons.append("contrast")
    if as_float(item.get("blur_score")) >= 180:
        reasons.append("clarity")
    if has_face_signal(item):
        reasons.append("people/portrait")
    if item.get("script_duplicate_pick"):
        reasons.append("sequence representative")
    flags = set(item.get("risk_flags") or [])
    if not (flags & SOFT_RISK_FLAGS):
        reasons.append("low risk")
    return "; ".join(reasons) or "single image may still work"


def board_item(item: Dict[str, Any], group_id: str, score: float, prefix: str = "") -> Dict[str, Any]:
    clone = dict(item)
    rel = str(item.get("relative_path") or Path(str(item.get("path") or "")).name)
    label = f"{group_id} score {score:.3f} {rel}"
    clone["scene_group_id"] = group_id
    clone["relative_path"] = f"{prefix}{label}" if prefix else label
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
    fieldnames: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_sheet_chunks(
    items: Sequence[Dict[str, Any]],
    output_dir: Path,
    basename: str,
    title: str,
    *,
    board_size: int,
    columns: int = 4,
) -> List[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(0, len(items), board_size):
        chunk = list(items[index : index + board_size])
        if not chunk:
            continue
        path = output_dir / f"{basename}_{index // board_size + 1:03d}.jpg"
        build_sheet(chunk, path, f"{title} {index // board_size + 1}", columns=columns)
        written.append(str(path))
    return written


def build_standout_channel(
    scene_items_all: Dict[str, List[Dict[str, Any]]],
    guidance: Dict[str, Any],
    method_scores: Dict[str, float],
    output_dir: Path,
    per_scene: int,
    hero_rescue_limit: int,
    board_size: int,
) -> List[Dict[str, Any]]:
    standout_dir = output_dir / "00_单张出彩候选"
    standout_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    board_items = []
    for group_id, items in sorted(scene_items_all.items()):
        usable = [item for item in items if not obvious_decode_failure(item)]
        ordered = sorted(
            usable,
            key=lambda item: (
                -standout_score(item, method_scores),
                str(item.get("relative_path") or "").lower(),
            ),
        )
        for rank, item in enumerate(ordered[: max(1, per_scene)], 1):
            score = standout_score(item, method_scores)
            rows.append(
                {
                    "scene_group_id": group_id,
                    "standout_rank_in_scene": rank,
                    "standout_score": score,
                    "scene_action": group_action(group_id, guidance),
                    "scene_role": guidance.get("scene_roles", {}).get(group_id),
                    "scene_quality": guidance.get("scene_quality", {}).get(group_id),
                    "reason": standout_reason(item),
                    "relative_path": item.get("relative_path"),
                    "path": item.get("path"),
                    "risk_flags": ",".join(item.get("risk_flags") or []),
                    "face_count": item.get("face_count"),
                    "duplicate_group_id": item.get("duplicate_group_id"),
                    "duplicate_rank": item.get("duplicate_rank"),
                }
            )
            board_items.append(board_item(item, group_id, score, prefix="STANDOUT "))
    board_items.sort(key=lambda item: -as_float(item.get("technical_score")))
    if hero_rescue_limit > 0:
        board_items = board_items[:hero_rescue_limit]
    build_sheet_chunks(
        board_items,
        standout_dir,
        "standout_candidates",
        "Standout candidates: scene quality does not block a strong single image",
        board_size=board_size,
    )
    write_csv(standout_dir / "standout_candidates.csv", rows)
    return rows


def build_pairwise_boards(
    manifest_items: Sequence[Dict[str, Any]],
    selected_for_merge: Dict[str, List[Dict[str, Any]]],
    output_dir: Path,
    group_limit: int,
) -> List[Dict[str, Any]]:
    pairwise_dir = output_dir / "04_相似图对比板"
    pairwise_dir.mkdir(parents=True, exist_ok=True)
    groups: List[tuple[str, str, List[Dict[str, Any]]]] = []

    duplicate_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in manifest_items:
        group_id = item.get("duplicate_group_id")
        if group_id:
            duplicate_groups[str(group_id)].append(item)
    for duplicate_id, rows in sorted(duplicate_groups.items()):
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda row: (as_int(row.get("duplicate_rank"), 999), str(row.get("relative_path") or "")))
        groups.append(("duplicate", duplicate_id, ordered[:4]))

    for scene_id, rows in sorted(selected_for_merge.items()):
        if len(rows) >= 2:
            groups.append(("scene_top", scene_id, rows[:4]))

    rows_out = []
    for index, (kind, source_id, rows) in enumerate(groups[:group_limit], 1):
        sheet_rows = []
        for option_index, item in enumerate(rows, 1):
            clone = dict(item)
            rel = clone.get("relative_path") or Path(str(clone.get("path") or "")).name
            clone["relative_path"] = f"OPTION {option_index}: {rel}"
            sheet_rows.append(clone)
        path = pairwise_dir / f"pairwise_{index:03d}_{kind}_{safe_name(source_id)}.jpg"
        build_sheet(sheet_rows, path, f"Pairwise {index:03d}: {kind} {source_id}", columns=min(4, max(2, len(sheet_rows))))
        for option_index, item in enumerate(rows, 1):
            rows_out.append(
                {
                    "comparison_id": f"pairwise_{index:03d}",
                    "comparison_kind": kind,
                    "source_id": source_id,
                    "option": option_index,
                    "relative_path": item.get("relative_path"),
                    "path": item.get("path"),
                    "decision_prompt": "Which option has better expression, hands, posture, background edges, eye line, and story?",
                    "comparison_sheet": str(path),
                }
            )
    write_csv(pairwise_dir / "pairwise_groups.csv", rows_out)
    return rows_out


def safe_name(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return cleaned or "item"


def find_seed_item(seed: str, manifest_items: Sequence[Dict[str, Any]]) -> Dict[str, Any] | None:
    seed_path = Path(seed).expanduser()
    seed_lower = seed.lower()
    for item in manifest_items:
        path = str(item.get("path") or "")
        rel = str(item.get("relative_path") or "")
        if path.lower() == seed_lower or rel.lower() == seed_lower:
            return item
        if Path(path).name.lower() == seed_path.name.lower() or Path(rel).name.lower() == seed_path.name.lower():
            return item
    return None


def build_preference_seed_boards(
    seeds: Sequence[str],
    manifest_items: Sequence[Dict[str, Any]],
    method_scores: Dict[str, float],
    output_dir: Path,
    limit_per_seed: int,
) -> List[Dict[str, Any]]:
    seed_dir = output_dir / "05_用户偏好种子扩展"
    seed_dir.mkdir(parents=True, exist_ok=True)
    rows_out = []
    for seed_index, seed in enumerate(seeds, 1):
        seed_item = find_seed_item(seed, manifest_items)
        if not seed_item:
            rows_out.append({"seed": seed, "status": "not_found", "relative_path": "", "path": "", "similarity": ""})
            continue
        seed_hash = str(seed_item.get("dhash") or "")
        matches = []
        for item in manifest_items:
            if item.get("path") == seed_item.get("path") or obvious_decode_failure(item):
                continue
            distance = 999
            if seed_hash and item.get("dhash"):
                try:
                    distance = hamming_hex(seed_hash, str(item.get("dhash")))
                except Exception:
                    distance = 999
            color_gap = abs(as_float(item.get("colorfulness")) - as_float(seed_item.get("colorfulness"))) / 100.0
            score = max(0.0, 1.0 - min(distance, 64) / 64.0) * 0.62
            score += max(0.0, 1.0 - min(color_gap, 1.0)) * 0.18
            score += normalized_review_score(item, method_scores) * 0.2
            matches.append((score, distance, item))
        matches.sort(key=lambda row: (-row[0], row[1], str(row[2].get("relative_path") or "")))
        board_items = []
        seed_clone = dict(seed_item)
        seed_clone["relative_path"] = f"SEED: {seed_item.get('relative_path')}"
        board_items.append(seed_clone)
        for rank, (score, distance, item) in enumerate(matches[:limit_per_seed], 1):
            clone = dict(item)
            clone["relative_path"] = f"MATCH {rank} sim {score:.3f} dhash {distance}: {item.get('relative_path')}"
            board_items.append(clone)
            rows_out.append(
                {
                    "seed": seed,
                    "seed_relative_path": seed_item.get("relative_path"),
                    "rank": rank,
                    "similarity": round(score, 6),
                    "dhash_distance": distance,
                    "relative_path": item.get("relative_path"),
                    "path": item.get("path"),
                }
            )
        build_sheet(
            board_items,
            seed_dir / f"preference_seed_{seed_index:03d}_{safe_name(Path(str(seed)).stem)}.jpg",
            f"Preference seed expansion {seed_index:03d}",
            columns=4,
        )
    write_csv(seed_dir / "preference_seed_matches.csv", rows_out)
    return rows_out


def crop_box(width: int, height: int, variant: str) -> tuple[int, int, int, int]:
    if variant == "upper":
        cw = int(width * 0.58)
        ch = int(height * 0.48)
        left = max(0, (width - cw) // 2)
        top = max(0, int(height * 0.06))
    else:
        cw = int(width * 0.78)
        ch = int(height * 0.88)
        left = max(0, (width - cw) // 2)
        top = max(0, int(height * 0.06))
    return (left, top, min(width, left + cw), min(height, top + ch))


def fit_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = image.copy()
    image.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "white")
    canvas.paste(image.convert("RGB"), ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def build_portrait_crop_boards(
    candidates: Sequence[Dict[str, Any]],
    output_dir: Path,
    limit: int,
) -> List[Dict[str, Any]]:
    portrait_dir = output_dir / "06_人像局部放大板"
    portrait_dir.mkdir(parents=True, exist_ok=True)
    people_items = []
    seen = set()
    for item in candidates:
        path = str(item.get("path") or "")
        if not path or path in seen or not has_face_signal(item):
            continue
        if not Path(path).exists():
            continue
        people_items.append(item)
        seen.add(path)
        if len(people_items) >= limit:
            break
    rows = []
    tile_w, tile_h = 220, 260
    columns = 4
    cell_w, cell_h = tile_w + 30, tile_h + 92
    title_h = 44
    tiles: List[tuple[Image.Image, Dict[str, Any], str]] = []
    for item in people_items:
        source = Path(str(item.get("preview_path") or item.get("path")))
        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except Exception:
            continue
        for variant in ("upper", "body"):
            crop = image.crop(crop_box(image.width, image.height, variant))
            tiles.append((fit_image(crop, (tile_w, tile_h)), item, variant))
            rows.append(
                {
                    "relative_path": item.get("relative_path"),
                    "path": item.get("path"),
                    "crop_variant": variant,
                    "review_prompt": "Check expression, eyes, hands, posture, hair blocking, and background cut lines.",
                    "risk_flags": ",".join(item.get("risk_flags") or []),
                    "face_count": item.get("face_count"),
                }
            )
    if tiles:
        rows_count = math.ceil(len(tiles) / columns)
        sheet = Image.new("RGB", (columns * cell_w, title_h + rows_count * cell_h), "white")
        draw = ImageDraw.Draw(sheet)
        draw.rectangle([0, 0, sheet.width, title_h], fill=(32, 42, 54))
        draw.text((14, 14), "Portrait crop board: expression, hands, posture, background edges", fill="white")
        for index, (tile, item, variant) in enumerate(tiles):
            row = index // columns
            col = index % columns
            x = col * cell_w + 15
            y = title_h + row * cell_h + 12
            sheet.paste(tile, (x, y))
            draw.rectangle([x, y, x + tile_w, y + tile_h], outline=(180, 180, 180), width=2)
            label_y = y + tile_h + 8
            rel = str(item.get("relative_path") or Path(str(item.get("path") or "")).name)
            draw.text((x, label_y), f"#{index + 1} {variant}", fill=(30, 30, 30))
            draw.text((x, label_y + 16), rel[:44], fill=(30, 30, 30))
            draw.text((x, label_y + 32), f"flags: {','.join(item.get('risk_flags') or [])[:48]}", fill=(60, 60, 60))
        sheet.save(portrait_dir / "portrait_crops_001.jpg", "JPEG", quality=88)
    write_csv(portrait_dir / "portrait_crop_items.csv", rows)
    return rows


def build_tournament_template(pairwise_rows: Sequence[Dict[str, Any]], output_dir: Path) -> Path | None:
    if not pairwise_rows:
        return None
    tournament_dir = output_dir / "08_二选一锦标赛"
    tournament_dir.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in pairwise_rows:
        grouped[str(row.get("comparison_id"))].append(row)
    rows = []
    for index, comparison_id in enumerate(sorted(grouped), 1):
        options = grouped[comparison_id]
        rows.append(
            {
                "round": 1,
                "match_id": f"match_{index:03d}",
                "comparison_id": comparison_id,
                "option_paths": "|".join(str(row.get("relative_path")) for row in options),
                "model_choice": "",
                "reason": "",
                "checklist": "expression|hands|posture|background_edges|eye_line|story|duplicate_semantics",
                "comparison_sheet": options[0].get("comparison_sheet") if options else "",
            }
        )
    path = tournament_dir / "pairwise_tournament.csv"
    write_csv(path, rows)
    (tournament_dir / "pairwise_tournament_instructions.md").write_text(
        "\n".join(
            [
                "# Pairwise Tournament",
                "",
                "Use this only in full/firepower mode. The goal is not another score; it is a forced edit decision.",
                "",
                "For each match, choose the image that best wins the specific comparison: expression, hands, posture, background edges, eye line, story, and duplicate semantics.",
                "Write the winner into `model_choice` and keep the reason short enough to remember during final merge.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def build_final_reverse_audit(
    selection_json: Path | None,
    manifest_items: Sequence[Dict[str, Any]],
    standout_rows: Sequence[Dict[str, Any]],
    method_scores: Dict[str, float],
    output_dir: Path,
    target_count: int,
) -> Dict[str, Any]:
    audit_dir = output_dir / "07_最终反向审查"
    audit_dir.mkdir(parents=True, exist_ok=True)
    questions = [
        "# Final Reverse Audit",
        "",
        "Before delivering the final set, answer these three questions by looking at the boards here:",
        "",
        "1. Does the final set repeat the same semantic image too many times?",
        "2. Is there any unselected/near-miss image that is a stronger hero than a final pick?",
        "3. Is there any image the user may like even though the scoring flow did not reward it?",
        "",
        "`REJECT` means clearly unusable. Strong but unchosen images should be `UNSELECTED` or `REVIEW`, not treated as waste.",
        "",
    ]
    (audit_dir / "final_reverse_audit_questions.md").write_text("\n".join(questions), encoding="utf-8")
    if not selection_json or not selection_json.exists():
        return {"audit_dir": str(audit_dir), "selection_json_used": None}

    selection = load_json(selection_json)
    by_path = {item.get("path"): item for item in manifest_items}
    final_items = []
    near_miss_items = []
    final_paths = set()
    for row in selection.get("selections", []):
        decision = str(row.get("decision") or "").upper()
        item = by_path.get(row.get("path"))
        if not item:
            continue
        if decision == "KEEP":
            final_items.append(item)
            final_paths.add(item.get("path"))
        elif decision in {"REVIEW", "UNSELECTED"}:
            near_miss_items.append(item)
    near_miss_items.sort(
        key=lambda item: (
            -standout_score(item, method_scores),
            -review_score(item, method_scores),
            str(item.get("relative_path") or ""),
        )
    )
    count_label = target_count or len(final_items)
    if final_items:
        build_sheet(final_items, audit_dir / f"final_{count_label}_contact_sheet.jpg", f"Final {count_label} contact sheet", columns=5)
    near_limit = max(12, (target_count or len(final_items) or 20) * 2)
    if near_miss_items:
        build_sheet(near_miss_items[:near_limit], audit_dir / "near_miss_board.jpg", "Near miss board", columns=5)
    standout_paths = [row.get("path") for row in standout_rows if row.get("path") not in final_paths]
    standout_lookup = {item.get("path"): item for item in manifest_items}
    unselected_standouts = [standout_lookup[path] for path in standout_paths if path in standout_lookup][:near_limit]
    if unselected_standouts:
        build_sheet(
            unselected_standouts,
            audit_dir / "rejected_or_unselected_standout_board.jpg",
            "Rejected or unselected standout board",
            columns=5,
        )
    return {
        "audit_dir": str(audit_dir),
        "selection_json_used": str(selection_json),
        "final_count": len(final_items),
        "near_miss_count": min(len(near_miss_items), near_limit),
        "unselected_standout_count": len(unselected_standouts),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build model/editor review boards after scene decisions.")
    parser.add_argument("manifest", help="Path to manifest.json.")
    parser.add_argument("--group-choices", default=None, help="Model/user-reviewed group_choices.csv.")
    parser.add_argument("--group-assignments", default=None, help="group_assignments.json.")
    parser.add_argument("--method-votes", default=None, help="method_votes.csv from ensemble_select_photos.py.")
    parser.add_argument("--selection-json", default=None, help="Optional final/reviewed selection.json for reverse audit boards.")
    parser.add_argument("--output", default=None, help="Output folder. Defaults to manifest parent/review_boards.")
    parser.add_argument("--target-count", type=int, default=0, help="Final target count, used to size review pools.")
    parser.add_argument("--top-per-scene", type=int, default=12, help="Max scored candidates per selected scene board.")
    parser.add_argument("--board-size", type=int, default=24, help="Images per merged/standout board.")
    parser.add_argument("--merged-limit", type=int, default=0, help="Max images in merged review pool. Default is target_count*4 or 120.")
    parser.add_argument("--mode", choices=["board", "super"], default="board", help="board uses contact sheets; super also writes a one-by-one shortlist.")
    parser.add_argument("--super-multiplier", type=int, default=3, help="Super mode shortlist size is target_count multiplied by this value.")
    parser.add_argument("--editorial-depth", choices=["light", "standard", "full"], default="light", help="Layered visual-review workload.")
    parser.add_argument("--standout-per-scene", type=int, default=2, help="Single-image standout candidates per scene, regardless of scene quality.")
    parser.add_argument("--hero-rescue-limit", type=int, default=0, help="Cap standout rescue board. Default max(30,target_count*2), or 60.")
    parser.add_argument("--comparison-group-limit", type=int, default=60, help="Max pairwise/similar comparison groups for standard/full.")
    parser.add_argument("--preference-seed", action="append", default=[], help="Liked sample photo path/name/relative path; can repeat. Full depth only.")
    parser.add_argument("--preference-limit", type=int, default=16, help="Matches per preference seed.")
    parser.add_argument("--portrait-crop-limit", type=int, default=80, help="Max portrait items to crop in full depth.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_json(manifest_path)
    manifest_items = list(manifest.get("items", []))
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

    scene_items_all: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    scene_items_allowed: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    pool_rows: List[Dict[str, Any]] = []
    for item in manifest_items:
        group_id = group_id_for(item, guidance)
        scene_items_all[group_id].append(item)
        if group_allowed(group_id, guidance):
            scene_items_allowed[group_id].append(item)

    if args.hero_rescue_limit > 0:
        hero_rescue_limit = args.hero_rescue_limit
    elif args.target_count > 0:
        hero_rescue_limit = max(30, args.target_count * 2)
    else:
        hero_rescue_limit = 60
    standout_rows = build_standout_channel(
        scene_items_all,
        guidance,
        method_scores,
        output_dir,
        max(1, min(3, args.standout_per_scene)),
        hero_rescue_limit,
        args.board_size,
    )

    selected_for_merge: Dict[str, List[Dict[str, Any]]] = {}
    for group_id, rows in sorted(scene_items_allowed.items()):
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
                    "standout_score": standout_score(item, method_scores),
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
    build_sheet_chunks(merged, merged_dir, "merged_review", "Merged score-assisted review", board_size=args.board_size)

    write_csv(output_dir / "review_pool.csv", pool_rows)
    scene_memory_rows = []
    for group_id in sorted(scene_items_all):
        allowed = group_allowed(group_id, guidance)
        scene_memory_rows.append(
            {
                "scene_group_id": group_id,
                "allowed_for_merge": allowed,
                "action": group_action(group_id, guidance),
                "scene_quality": guidance.get("scene_quality", {}).get(group_id),
                "scene_role": guidance.get("scene_roles", {}).get(group_id),
                "keep_count": guidance.get("keep_counts", {}).get(group_id),
                "review_candidates": len(selected_for_merge.get(group_id, [])),
                "standout_candidates": len([row for row in standout_rows if row.get("scene_group_id") == group_id]),
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
                    "task": "open_original_and_decide_KEEP_REVIEW_UNSELECTED_REJECT",
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

    pairwise_rows: List[Dict[str, Any]] = []
    if args.editorial_depth in {"standard", "full"}:
        pairwise_rows = build_pairwise_boards(manifest_items, selected_for_merge, output_dir, args.comparison_group_limit)

    preference_rows: List[Dict[str, Any]] = []
    portrait_rows: List[Dict[str, Any]] = []
    tournament_path = None
    if args.editorial_depth == "full":
        if args.preference_seed:
            preference_rows = build_preference_seed_boards(
                args.preference_seed,
                manifest_items,
                method_scores,
                output_dir,
                args.preference_limit,
            )
        portrait_candidates = merged + [row for rows in selected_for_merge.values() for row in rows]
        portrait_rows = build_portrait_crop_boards(portrait_candidates, output_dir, args.portrait_crop_limit)
        tournament_path = build_tournament_template(pairwise_rows, output_dir)

    final_audit_summary = build_final_reverse_audit(
        Path(args.selection_json).expanduser().resolve() if args.selection_json else None,
        manifest_items,
        standout_rows,
        method_scores,
        output_dir,
        args.target_count,
    )

    index_lines = [
        "# Steven Photo Director Review Boards",
        "",
        "Use these boards after scene choices are set. Scores decide board order only; the model/editor still decides final photos.",
        "",
        f"- editorial_depth: `{args.editorial_depth}`",
        f"- review_mode: `{args.mode}`",
        "",
        "## Review Order",
        "",
        "1. Read `scene_review_memory.csv` so the scene decisions stay in mind.",
        "2. Open `00_单张出彩候选/`; every scene gets a possible escape hatch even if the scene is weak.",
        "3. Open `01_场景分数图板/` and compare candidates inside each chosen scene.",
        "4. Open `02_合并精审图板/` for cross-scene balance and final shortlist.",
        "5. Use `07_最终反向审查/` before delivery; with a reviewed selection JSON it creates final/near-miss/rescue boards.",
        "6. In standard/full depth, use `04_相似图对比板/` for forced similar-frame comparison.",
        "7. In full depth, use preference seeds, portrait crop boards, and pairwise tournament only where they answer a real uncertainty.",
        "",
        "Do not restart from a global top-N score list.",
        "",
        "## Files",
        "",
        "- `review_pool.csv`: machine-readable list of board items and scores.",
        "- `scene_review_memory.csv`: temporary scene memory; use it to remember why good scenes get more picks and weak scenes get fewer.",
        "- `00_单张出彩候选/`: standout channel; scene quality does not block a strong single image.",
        "- `01_场景分数图板/`: per-scene score-assisted boards.",
        "- `02_合并精审图板/`: merged cross-scene boards.",
        "- `04_相似图对比板/`: side-by-side comparison boards for duplicate/similar frames.",
        "- `05_用户偏好种子扩展/`: full mode only, visual preference seed expansion.",
        "- `06_人像局部放大板/`: full mode only, face/body crop checks.",
        "- `07_最终反向审查/`: final audit questions always; final contact sheet + near miss + unselected standout rescue when selection JSON is supplied.",
        "- `08_二选一锦标赛/`: full mode only, forced pairwise edit decisions.",
        "",
    ]
    (output_dir / "review_board_index.md").write_text("\n".join(index_lines), encoding="utf-8")
    summary = {
        "output_dir": str(output_dir),
        "editorial_depth": args.editorial_depth,
        "scene_boards": len(selected_for_merge),
        "merged_items": len(merged),
        "standout_candidates": len(standout_rows),
        "pairwise_rows": len(pairwise_rows),
        "preference_seed_rows": len(preference_rows),
        "portrait_crop_rows": len(portrait_rows),
        "reviewed_scene_choices": bool(guidance.get("reviewed")),
        "method_scores_used": bool(method_scores),
        "mode": args.mode,
        "review_pool_csv": str(output_dir / "review_pool.csv"),
        "scene_review_memory_csv": str(output_dir / "scene_review_memory.csv"),
        "super_select_csv": str(super_list_path) if super_list_path else None,
        "tournament_csv": str(tournament_path) if tournament_path else None,
        "final_reverse_audit": final_audit_summary,
        "index": str(output_dir / "review_board_index.md"),
    }
    (output_dir / "review_board_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
