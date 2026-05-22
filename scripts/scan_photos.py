#!/usr/bin/env python3
"""Portable first-pass photo culling scanner.

The script is intentionally conservative and non-destructive. It writes analysis
artifacts into an output folder and never edits source files.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat, ExifTags
except Exception as exc:  # pragma: no cover - exercised only on missing deps
    raise SystemExit(
        "Pillow is required for image decoding. Install with: pip install pillow"
    ) from exc

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    HEIF_AVAILABLE = False

try:
    import numpy as np
except Exception:  # pragma: no cover - fallback path
    np = None  # type: ignore

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore

try:
    import rawpy
except Exception:  # pragma: no cover - optional dependency
    rawpy = None  # type: ignore

try:
    import mediapipe as mp
except Exception:  # pragma: no cover - optional dependency
    mp = None  # type: ignore


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
    ".3fr",
    ".erf",
    ".kdc",
    ".mef",
    ".mrw",
    ".nrw",
    ".rwl",
    ".iiq",
}

RAW_EXTENSIONS = {
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
    ".3fr",
    ".erf",
    ".kdc",
    ".mef",
    ".mrw",
    ".nrw",
    ".rwl",
    ".iiq",
}

EXIF_NAME_BY_ID = {value: key for key, value in ExifTags.TAGS.items()}

LEFT_EYE_EAR_POINTS = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_EAR_POINTS = (362, 385, 387, 263, 373, 380)
_FACE_MESH_CACHE: Dict[Tuple[int, float], Any] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_text(value: Any, limit: int = 80) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.encode("ascii", "replace").decode("ascii")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def iter_photo_files(input_dir: Path, recursive: bool) -> List[Path]:
    pattern = "**/*" if recursive else "*"
    files = [
        path
        for path in input_dir.glob(pattern)
        if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
    ]
    return sorted(files, key=lambda p: str(p).lower())


def same_stem_preview(path: Path) -> Optional[Path]:
    if path.suffix.lower() not in RAW_EXTENSIONS:
        return None
    candidates = []
    for ext in (".jpg", ".jpeg", ".JPG", ".JPEG"):
        candidates.append(path.with_suffix(ext))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def image_exif(image: Image.Image) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    try:
        exif = image.getexif()
    except Exception:
        return result
    for tag_id, value in exif.items():
        name = ExifTags.TAGS.get(tag_id, str(tag_id))
        if name in {
            "DateTimeOriginal",
            "DateTimeDigitized",
            "DateTime",
            "Make",
            "Model",
            "LensModel",
            "FocalLength",
            "ExposureTime",
            "FNumber",
            "ISOSpeedRatings",
            "PhotographicSensitivity",
        }:
            result[name] = exif_value_to_json(value)
    return result


def exif_value_to_json(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, tuple):
        return [exif_value_to_json(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def make_analysis_image(image: Image.Image, max_dim: int) -> Image.Image:
    working = ImageOps.exif_transpose(image).convert("RGB")
    working.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    return working


def load_source_image(path: Path) -> Tuple[Image.Image, str]:
    if path.suffix.lower() in RAW_EXTENSIONS and rawpy is not None:
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
        return Image.fromarray(rgb), "rawpy"
    with Image.open(path) as image:
        image.load()
        return image.copy(), "pillow"


def dhash(image: Image.Image, hash_size: int) -> str:
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = gray.tobytes()
    bits = []
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            bits.append(1 if pixels[offset + col] > pixels[offset + col + 1] else 0)
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    width = max(1, (hash_size * hash_size + 3) // 4)
    return f"{value:0{width}x}"


def hamming_hex(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def gray_array(image: Image.Image) -> Any:
    gray = image.convert("L")
    if np is None:
        return gray
    return np.asarray(gray, dtype=np.float32)


def blur_score(image: Image.Image) -> float:
    """Return a Laplacian-like variance score. Higher is sharper."""
    if cv2 is not None and np is not None:
        gray_u8 = np.asarray(image.convert("L"), dtype=np.uint8)
        return float(cv2.Laplacian(gray_u8, cv2.CV_64F).var())
    gray = gray_array(image)
    if np is None:
        edges = gray.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(edges)
        return float(stat.var[0])
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    lap = (
        gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
        - 4 * gray[1:-1, 1:-1]
    )
    return float(np.var(lap))


def brightness_stats(image: Image.Image) -> Dict[str, float]:
    gray = gray_array(image)
    if np is None:
        stat = ImageStat.Stat(gray)
        hist = gray.histogram()
        total = max(1, sum(hist))
        shadow = sum(hist[:6]) / total
        highlight = sum(hist[250:]) / total
        return {
            "brightness_mean": float(stat.mean[0] / 255.0),
            "shadow_clip_pct": float(shadow),
            "highlight_clip_pct": float(highlight),
            "contrast": float(math.sqrt(stat.var[0]) / 255.0),
        }
    total = max(1, gray.size)
    return {
        "brightness_mean": float(np.mean(gray) / 255.0),
        "shadow_clip_pct": float(np.sum(gray <= 5) / total),
        "highlight_clip_pct": float(np.sum(gray >= 250) / total),
        "contrast": float(np.std(gray) / 255.0),
    }


def colorfulness(image: Image.Image) -> float:
    if np is None:
        stat = ImageStat.Stat(image.convert("RGB"))
        return float(sum(math.sqrt(v) for v in stat.var) / 3.0)
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    red = arr[:, :, 0]
    green = arr[:, :, 1]
    blue = arr[:, :, 2]
    rg = np.abs(red - green)
    yb = np.abs(0.5 * (red + green) - blue)
    std_root = math.sqrt(float(np.std(rg) ** 2 + np.std(yb) ** 2))
    mean_root = math.sqrt(float(np.mean(rg) ** 2 + np.mean(yb) ** 2))
    return float(std_root + 0.3 * mean_root)


def distance_2d(left: Any, right: Any) -> float:
    return math.hypot(float(left.x) - float(right.x), float(left.y) - float(right.y))


def eye_aspect_ratio(landmarks: Sequence[Any], indexes: Sequence[int]) -> Optional[float]:
    try:
        p1, p2, p3, p4, p5, p6 = [landmarks[index] for index in indexes]
        horizontal = distance_2d(p1, p4)
        if horizontal <= 0:
            return None
        vertical_1 = distance_2d(p2, p6)
        vertical_2 = distance_2d(p3, p5)
        return float((vertical_1 + vertical_2) / (2.0 * horizontal))
    except Exception:
        return None


def get_face_mesh(max_faces: int, min_confidence: float) -> Any:
    if mp is None:
        return None
    key = (max_faces, min_confidence)
    if key not in _FACE_MESH_CACHE:
        _FACE_MESH_CACHE[key] = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=max_faces,
            refine_landmarks=True,
            min_detection_confidence=min_confidence,
        )
    return _FACE_MESH_CACHE[key]


def mediapipe_eye_metrics(image: Image.Image, args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    if mp is None or np is None:
        return None
    result: Dict[str, Any] = {
        "eye_analysis_method": "mediapipe",
        "face_landmark_count": 0,
        "eye_ear_min": None,
        "eye_ear_avg": None,
        "eye_ear_left_min": None,
        "eye_ear_right_min": None,
        "eye_closed_face_count": 0,
        "eye_asymmetry_face_count": 0,
        "eye_analysis_error": None,
    }
    try:
        face_mesh = get_face_mesh(args.max_faces, args.face_min_confidence)
        if face_mesh is None:
            return None
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        processed = face_mesh.process(rgb)
        faces = processed.multi_face_landmarks or []
        result["face_landmark_count"] = len(faces)
        left_values: List[float] = []
        right_values: List[float] = []
        avg_values: List[float] = []
        for face in faces:
            landmarks = face.landmark
            left_ear = eye_aspect_ratio(landmarks, LEFT_EYE_EAR_POINTS)
            right_ear = eye_aspect_ratio(landmarks, RIGHT_EYE_EAR_POINTS)
            if left_ear is None or right_ear is None:
                continue
            left_values.append(left_ear)
            right_values.append(right_ear)
            avg_ear = (left_ear + right_ear) / 2.0
            avg_values.append(avg_ear)
            if avg_ear <= args.eye_closed_ear_threshold:
                result["eye_closed_face_count"] += 1
            if (
                abs(left_ear - right_ear) >= args.eye_asymmetry_threshold
                and min(left_ear, right_ear) <= args.eye_closed_ear_threshold + 0.06
            ):
                result["eye_asymmetry_face_count"] += 1
        if avg_values:
            result["eye_ear_min"] = round(min(avg_values), 4)
            result["eye_ear_avg"] = round(sum(avg_values) / len(avg_values), 4)
        if left_values:
            result["eye_ear_left_min"] = round(min(left_values), 4)
        if right_values:
            result["eye_ear_right_min"] = round(min(right_values), 4)
        return result
    except Exception as exc:
        result["eye_analysis_error"] = f"{type(exc).__name__}: {exc}"
        return result


def face_eye_counts(image: Image.Image) -> Tuple[Optional[int], Optional[int]]:
    if cv2 is None or np is None:
        return None, None
    try:
        data_path = Path(cv2.data.haarcascades)
        face_cascade = cv2.CascadeClassifier(str(data_path / "haarcascade_frontalface_default.xml"))
        eye_cascade = cv2.CascadeClassifier(str(data_path / "haarcascade_eye.xml"))
        if face_cascade.empty() or eye_cascade.empty():
            return None, None
        gray = np.asarray(image.convert("L"), dtype=np.uint8)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(32, 32))
        eye_total = 0
        for (x, y, w, h) in faces:
            roi = gray[y : y + h, x : x + w]
            eyes = eye_cascade.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=4, minSize=(12, 12))
            eye_total += len(eyes)
        return int(len(faces)), int(eye_total)
    except Exception:
        return None, None


def analyze_faces_and_eyes(image: Image.Image, args: argparse.Namespace) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "face_count": None,
        "eye_count": None,
        "eye_analysis_method": None,
        "face_landmark_count": None,
        "eye_ear_min": None,
        "eye_ear_avg": None,
        "eye_ear_left_min": None,
        "eye_ear_right_min": None,
        "eye_closed_face_count": None,
        "eye_asymmetry_face_count": None,
        "eye_analysis_error": None,
    }
    if args.eye_analysis == "none":
        return result

    if args.eye_analysis in {"auto", "mediapipe"}:
        metrics = mediapipe_eye_metrics(image, args)
        if metrics is not None:
            result.update(metrics)
            if metrics.get("face_landmark_count") is not None:
                result["face_count"] = metrics.get("face_landmark_count")
                result["eye_count"] = int(metrics.get("face_landmark_count") or 0) * 2
            if args.eye_analysis == "mediapipe" or result.get("face_count") is not None:
                return result

    if args.eye_analysis in {"auto", "opencv"}:
        face_count, eye_count = face_eye_counts(image)
        result["face_count"] = face_count
        result["eye_count"] = eye_count
        if face_count is not None or eye_count is not None:
            result["eye_analysis_method"] = "opencv"
        elif args.eye_analysis == "opencv":
            result["eye_analysis_error"] = "OpenCV face/eye cascade unavailable"
    elif args.eye_analysis == "mediapipe" and result.get("eye_analysis_method") is None:
        result["eye_analysis_error"] = "MediaPipe unavailable"
    return result


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return ordered[int(idx)]
    return ordered[lower] * (upper - idx) + ordered[upper] * (idx - lower)


@dataclass
class BKNode:
    value: str
    indexes: List[int] = field(default_factory=list)
    children: Dict[int, "BKNode"] = field(default_factory=dict)


class BKTree:
    def __init__(self) -> None:
        self.root: Optional[BKNode] = None

    def add(self, value: str, index: int) -> None:
        if self.root is None:
            self.root = BKNode(value=value, indexes=[index])
            return
        node = self.root
        while True:
            distance = hamming_hex(value, node.value)
            if distance == 0:
                node.indexes.append(index)
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = BKNode(value=value, indexes=[index])
                return
            node = child

    def query(self, value: str, threshold: int) -> List[int]:
        matches: List[int] = []
        if self.root is None:
            return matches
        stack = [self.root]
        while stack:
            node = stack.pop()
            distance = hamming_hex(value, node.value)
            if distance <= threshold:
                matches.extend(node.indexes)
            low = distance - threshold
            high = distance + threshold
            for edge_distance, child in node.children.items():
                if low <= edge_distance <= high:
                    stack.append(child)
        return matches


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.rank[root_left] < self.rank[root_right]:
            self.parent[root_left] = root_right
        elif self.rank[root_left] > self.rank[root_right]:
            self.parent[root_right] = root_left
        else:
            self.parent[root_right] = root_left
            self.rank[root_left] += 1


def analyze_file(path: Path, input_dir: Path, args: argparse.Namespace) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "path": str(path.resolve()),
        "relative_path": str(path.relative_to(input_dir)),
        "extension": path.suffix.lower(),
        "file_size_bytes": path.stat().st_size,
        "preview_path": None,
        "decode_failed": False,
        "decode_error": None,
        "decoder": None,
        "raw_file": path.suffix.lower() in RAW_EXTENSIONS,
        "width": None,
        "height": None,
        "megapixels": None,
        "capture_time": None,
        "camera_make": None,
        "camera_model": None,
        "lens_model": None,
        "blur_score": None,
        "brightness_mean": None,
        "shadow_clip_pct": None,
        "highlight_clip_pct": None,
        "contrast": None,
        "colorfulness": None,
        "face_count": None,
        "eye_count": None,
        "eye_analysis_method": None,
        "face_landmark_count": None,
        "eye_ear_min": None,
        "eye_ear_avg": None,
        "eye_ear_left_min": None,
        "eye_ear_right_min": None,
        "eye_closed_face_count": None,
        "eye_asymmetry_face_count": None,
        "eye_analysis_error": None,
        "dhash": None,
        "duplicate_group_id": None,
        "duplicate_rank": None,
        "script_duplicate_pick": False,
        "risk_flags": [],
        "technical_score": 0,
        "first_pass_decision": "AUTO_RISK",
    }
    decode_path = same_stem_preview(path) or path
    if decode_path != path:
        item["preview_path"] = str(decode_path.resolve())
    try:
        image, decoder = load_source_image(decode_path)
        item["decoder"] = decoder
        item["width"], item["height"] = image.size
        item["megapixels"] = round((image.size[0] * image.size[1]) / 1_000_000, 3)
        exif = image_exif(image)
        item["capture_time"] = (
            exif.get("DateTimeOriginal")
            or exif.get("DateTimeDigitized")
            or exif.get("DateTime")
        )
        item["camera_make"] = exif.get("Make")
        item["camera_model"] = exif.get("Model")
        item["lens_model"] = exif.get("LensModel")
        analysis_image = make_analysis_image(image, args.analysis_max_dim)
        item["dhash"] = dhash(analysis_image, args.hash_size)
        item["blur_score"] = round(blur_score(analysis_image), 3)
        stats = brightness_stats(analysis_image)
        item.update({key: round(value, 5) for key, value in stats.items()})
        item["colorfulness"] = round(colorfulness(analysis_image), 3)
        item.update(analyze_faces_and_eyes(analysis_image, args))
    except Exception as exc:
        item["decode_failed"] = True
        item["decode_error"] = f"{type(exc).__name__}: {exc}"
        item["risk_flags"].append("decode_failed")
        if item["raw_file"] and rawpy is None and not item.get("preview_path"):
            item["risk_flags"].append("raw_needs_preview_or_rawpy")
    return item


def assign_risk_flags(items: List[Dict[str, Any]], args: argparse.Namespace) -> None:
    blur_values = [
        float(item["blur_score"])
        for item in items
        if item.get("blur_score") is not None and not item.get("decode_failed")
    ]
    blur_p08 = percentile(blur_values, 0.08)
    for item in items:
        flags = list(item.get("risk_flags") or [])
        if item.get("decode_failed"):
            item["risk_flags"] = sorted(set(flags))
            continue
        width = item.get("width") or 0
        height = item.get("height") or 0
        megapixels = item.get("megapixels") or 0
        blur = item.get("blur_score")
        brightness = item.get("brightness_mean")
        shadow = item.get("shadow_clip_pct")
        highlight = item.get("highlight_clip_pct")
        contrast = item.get("contrast")
        if width < args.min_width or height < args.min_height or megapixels < args.min_megapixels:
            flags.append("low_resolution")
        if blur is not None:
            if blur < args.severe_blur_threshold:
                flags.append("severe_blur")
            elif blur < args.soft_blur_threshold:
                flags.append("soft_focus_risk")
            if blur_p08 is not None and blur <= blur_p08 and len(blur_values) >= 12:
                flags.append("relative_low_sharpness")
        if brightness is not None and shadow is not None:
            if brightness < args.underexposed_mean or shadow > args.shadow_clip_threshold:
                flags.append("underexposure_risk")
        if brightness is not None and highlight is not None:
            if brightness > args.overexposed_mean or highlight > args.highlight_clip_threshold:
                flags.append("overexposure_risk")
        if contrast is not None and contrast < args.low_contrast_threshold:
            flags.append("low_contrast")
        if item.get("eye_closed_face_count"):
            flags.append("eye_closed_review")
        if item.get("eye_asymmetry_face_count"):
            flags.append("eye_asymmetry_review")
        if item.get("face_count") and item.get("eye_count") == 0:
            flags.append("face_eye_review")
        item["risk_flags"] = sorted(set(flags))


def technical_score(item: Dict[str, Any]) -> int:
    if item.get("decode_failed"):
        return 0
    score = 100.0
    flags = set(item.get("risk_flags") or [])
    if "severe_blur" in flags:
        score -= 35
    elif "soft_focus_risk" in flags:
        score -= 18
    if "relative_low_sharpness" in flags:
        score -= 8
    if "low_resolution" in flags:
        score -= 25
    if "underexposure_risk" in flags:
        score -= 16
    if "overexposure_risk" in flags:
        score -= 16
    if "low_contrast" in flags:
        score -= 8
    if "eye_closed_review" in flags:
        score -= 8
    if "eye_asymmetry_review" in flags or "face_eye_review" in flags:
        score -= 4
    blur = item.get("blur_score")
    if isinstance(blur, (int, float)) and blur > 0:
        score += min(8.0, math.log10(blur + 1.0))
    return int(max(0, min(100, round(score))))


def assign_duplicates(items: List[Dict[str, Any]], threshold: int) -> None:
    hash_indexes = [
        index for index, item in enumerate(items) if item.get("dhash") and not item.get("decode_failed")
    ]
    uf = UnionFind(len(items))
    tree = BKTree()
    for index in hash_indexes:
        value = str(items[index]["dhash"])
        for match in tree.query(value, threshold):
            uf.union(index, match)
        tree.add(value, index)

    components: Dict[int, List[int]] = defaultdict(list)
    for index in hash_indexes:
        components[uf.find(index)].append(index)

    duplicate_components = [indexes for indexes in components.values() if len(indexes) > 1]
    duplicate_components.sort(
        key=lambda indexes: min(str(items[index]["relative_path"]).lower() for index in indexes)
    )
    for group_number, indexes in enumerate(duplicate_components, 1):
        group_id = f"dup_{group_number:04d}"
        ranked = sorted(
            indexes,
            key=lambda idx: (
                -(items[idx].get("technical_score") or 0),
                str(items[idx].get("capture_time") or ""),
                str(items[idx].get("relative_path") or "").lower(),
            ),
        )
        for rank, index in enumerate(ranked, 1):
            item = items[index]
            item["duplicate_group_id"] = group_id
            item["duplicate_rank"] = rank
            item["script_duplicate_pick"] = rank == 1
            flags = set(item.get("risk_flags") or [])
            flags.add("near_duplicate")
            item["risk_flags"] = sorted(flags)


def assign_decisions(items: List[Dict[str, Any]]) -> None:
    for item in items:
        item["technical_score"] = technical_score(item)
    assign_duplicates(items, threshold=assign_decisions.duplicate_threshold)  # type: ignore[attr-defined]
    for item in items:
        flags = set(item.get("risk_flags") or [])
        severe_blur_hard_fail = "severe_blur" in flags and float(item.get("technical_score") or 0) < 45
        if item.get("decode_failed") or "low_resolution" in flags or severe_blur_hard_fail:
            item["first_pass_decision"] = "AUTO_RISK"
        elif item.get("duplicate_group_id") and not item.get("script_duplicate_pick"):
            item["first_pass_decision"] = "AUTO_REVIEW_DUPLICATE"
        elif flags:
            item["first_pass_decision"] = "AUTO_REVIEW"
        else:
            item["first_pass_decision"] = "AUTO_PASS"


def csv_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def write_manifest_csv(items: List[Dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "relative_path",
        "path",
        "preview_path",
        "extension",
        "file_size_bytes",
        "raw_file",
        "decode_failed",
        "decode_error",
        "decoder",
        "width",
        "height",
        "megapixels",
        "capture_time",
        "camera_make",
        "camera_model",
        "lens_model",
        "blur_score",
        "brightness_mean",
        "shadow_clip_pct",
        "highlight_clip_pct",
        "contrast",
        "colorfulness",
        "face_count",
        "eye_count",
        "eye_analysis_method",
        "face_landmark_count",
        "eye_ear_min",
        "eye_ear_avg",
        "eye_ear_left_min",
        "eye_ear_right_min",
        "eye_closed_face_count",
        "eye_asymmetry_face_count",
        "eye_analysis_error",
        "dhash",
        "duplicate_group_id",
        "duplicate_rank",
        "script_duplicate_pick",
        "risk_flags",
        "technical_score",
        "first_pass_decision",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow({key: csv_value(item.get(key)) for key in fieldnames})


def write_duplicate_csv(items: List[Dict[str, Any]], path: Path) -> None:
    rows = [item for item in items if item.get("duplicate_group_id")]
    fieldnames = [
        "duplicate_group_id",
        "duplicate_rank",
        "script_duplicate_pick",
        "relative_path",
        "technical_score",
        "blur_score",
        "risk_flags",
        "first_pass_decision",
        "path",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in sorted(rows, key=lambda row: (row["duplicate_group_id"], row["duplicate_rank"])):
            writer.writerow({key: csv_value(item.get(key)) for key in fieldnames})


def draft_selection(items: List[Dict[str, Any]], manifest_path: Path) -> Dict[str, Any]:
    selections = []
    for item in items:
        decision = "REVIEW"
        rating = 3
        if item.get("decode_failed"):
            decision = "REJECT"
            rating = 0
        elif item.get("first_pass_decision") == "AUTO_RISK":
            decision = "REJECT"
            rating = 1
        reason = "; ".join(item.get("risk_flags") or []) or "No mechanical risk flagged."
        selections.append(
            {
                "path": item["path"],
                "relative_path": item["relative_path"],
                "decision": decision,
                "rating": rating,
                "label": "yellow" if decision == "REVIEW" else "red",
                "reason": f"Draft first-pass decision: {item['first_pass_decision']}. {reason}",
            }
        )
    return {
        "source_manifest": str(manifest_path.resolve()),
        "selection_notes": "Draft only. Agent/editor must perform aesthetic review before treating KEEP as final.",
        "selections": selections,
    }


def item_sort_key(item: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        item.get("duplicate_group_id") or "",
        item.get("duplicate_rank") or 9999,
        item.get("first_pass_decision") or "",
        str(item.get("relative_path") or "").lower(),
    )


def open_for_sheet(path: str, max_thumb: Tuple[int, int]) -> Optional[Image.Image]:
    try:
        image, _decoder = load_source_image(Path(path))
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail(max_thumb, Image.Resampling.LANCZOS)
        return image
    except Exception:
        return None


def draw_multiline(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], lines: Sequence[str], fill: str) -> None:
    x, y = xy
    for line in lines:
        draw.text((x, y), safe_text(line, 58), fill=fill)
        y += 14


def build_sheet(
    items: Sequence[Dict[str, Any]],
    output_path: Path,
    title: str,
    columns: int = 4,
    thumb_size: Tuple[int, int] = (260, 220),
) -> None:
    cell_w = thumb_size[0] + 26
    cell_h = thumb_size[1] + 92
    title_h = 44
    rows = max(1, math.ceil(len(items) / columns))
    sheet = Image.new("RGB", (columns * cell_w, title_h + rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    draw.rectangle([0, 0, sheet.width, title_h], fill=(32, 42, 54))
    draw.text((14, 14), safe_text(title, 120), fill="white")

    for idx, item in enumerate(items):
        row = idx // columns
        col = idx % columns
        x = col * cell_w + 13
        y = title_h + row * cell_h + 12
        source = item.get("preview_path") or item.get("path")
        image = open_for_sheet(str(source), thumb_size) if source else None
        border = (34, 139, 34) if item.get("script_duplicate_pick") else (190, 190, 190)
        if image is None:
            draw.rectangle([x, y, x + thumb_size[0], y + thumb_size[1]], outline=(200, 80, 80), width=2)
            draw.text((x + 12, y + 92), "decode failed", fill=(160, 0, 0))
        else:
            px = x + (thumb_size[0] - image.width) // 2
            py = y + (thumb_size[1] - image.height) // 2
            sheet.paste(image, (px, py))
            draw.rectangle([x, y, x + thumb_size[0], y + thumb_size[1]], outline=border, width=3)
        label_y = y + thumb_size[1] + 8
        flags = ",".join(item.get("risk_flags") or [])
        lines = [
            f"#{idx + 1} {item.get('relative_path')}",
            f"score {item.get('technical_score')}  blur {item.get('blur_score')}",
            f"{item.get('first_pass_decision')} {flags}",
        ]
        if item.get("eye_analysis_method"):
            lines.append(
                f"eye {item.get('eye_analysis_method')} ear {item.get('eye_ear_min')} closed {item.get('eye_closed_face_count')}"
            )
        draw_multiline(draw, (x, label_y), lines, fill=(30, 30, 30))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, "JPEG", quality=88)


def build_contact_sheets(
    items: List[Dict[str, Any]],
    output_dir: Path,
    max_contact_groups: int,
    risk_sheet_size: int,
) -> List[str]:
    contact_dir = output_dir / "contact_sheets"
    contact_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        group_id = item.get("duplicate_group_id")
        if group_id:
            groups[str(group_id)].append(item)
    for group_id in sorted(groups)[:max_contact_groups]:
        group_items = sorted(groups[group_id], key=lambda row: row.get("duplicate_rank") or 999)
        path = contact_dir / f"{group_id}.jpg"
        build_sheet(group_items, path, f"Duplicate group {group_id}")
        written.append(str(path))

    risk_items = [
        item
        for item in items
        if item.get("risk_flags") and item.get("first_pass_decision") != "AUTO_REVIEW_DUPLICATE"
    ]
    risk_items = sorted(risk_items, key=item_sort_key)
    for chunk_index in range(0, len(risk_items), risk_sheet_size):
        chunk = risk_items[chunk_index : chunk_index + risk_sheet_size]
        if not chunk:
            continue
        path = contact_dir / f"risk_review_{chunk_index // risk_sheet_size + 1:03d}.jpg"
        build_sheet(chunk, path, f"Risk review {chunk_index // risk_sheet_size + 1}")
        written.append(str(path))
    return written


def write_report(
    manifest: Dict[str, Any],
    output_path: Path,
    contact_sheets: Sequence[str],
) -> None:
    items = manifest["items"]
    stats = manifest["stats"]
    flag_counts = Counter(flag for item in items for flag in item.get("risk_flags") or [])
    decision_counts = Counter(item.get("first_pass_decision") for item in items)
    lines = [
        "# First Pass Photo Culling Report",
        "",
        f"- Generated: {manifest['generated_at']}",
        f"- Input: `{manifest['input_dir']}`",
        f"- Total files: {stats['total_files']}",
        f"- Decoded files: {stats['decoded_files']}",
        f"- Decode failures: {stats['decode_failures']}",
        f"- Duplicate groups: {stats['duplicate_groups']}",
        f"- Contact sheets: {len(contact_sheets)}",
        "",
        "## First-Pass Decisions",
        "",
    ]
    for decision, count in sorted(decision_counts.items()):
        lines.append(f"- {decision}: {count}")
    lines.extend(["", "## Risk Flags", ""])
    if flag_counts:
        for flag, count in flag_counts.most_common():
            lines.append(f"- {flag}: {count}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Capability Status",
            "",
            f"- numpy: {stats.get('numpy_available')}",
            f"- OpenCV: {stats.get('opencv_available')}",
            f"- rawpy: {stats.get('rawpy_available')}",
            f"- HEIC/HEIF: {stats.get('heif_available')}",
            f"- MediaPipe: {stats.get('mediapipe_available')}",
            "",
            "## Files Written",
            "",
            "- `manifest.json`",
            "- `manifest.csv`",
            "- `duplicate_groups.csv`",
            "- `selection_draft.json`",
            "- `contact_sheets/`",
            "",
            "## Next Step",
            "",
            "Inspect duplicate and risk contact sheets, load `references/aesthetic_rubric.md`, then edit `selection_draft.json` into `selection.json` with final KEEP, REVIEW, and REJECT decisions.",
            "",
            "This report is mechanical triage only. It is not a final aesthetic edit.",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="First-pass non-destructive photo culling scanner.")
    parser.add_argument("input_dir", help="Folder containing photos.")
    parser.add_argument("--output", default=None, help="Output folder. Defaults to <input>_cull.")
    parser.add_argument("--recursive", action="store_true", help="Scan nested folders.")
    parser.add_argument("--analysis-max-dim", type=int, default=1024)
    parser.add_argument("--hash-size", type=int, default=8)
    parser.add_argument("--duplicate-threshold", type=int, default=8)
    parser.add_argument("--min-width", type=int, default=1200)
    parser.add_argument("--min-height", type=int, default=800)
    parser.add_argument("--min-megapixels", type=float, default=1.0)
    parser.add_argument("--severe-blur-threshold", type=float, default=20.0)
    parser.add_argument("--soft-blur-threshold", type=float, default=65.0)
    parser.add_argument("--underexposed-mean", type=float, default=0.18)
    parser.add_argument("--overexposed-mean", type=float, default=0.86)
    parser.add_argument("--shadow-clip-threshold", type=float, default=0.35)
    parser.add_argument("--highlight-clip-threshold", type=float, default=0.12)
    parser.add_argument("--low-contrast-threshold", type=float, default=0.07)
    parser.add_argument(
        "--eye-analysis",
        choices=["auto", "none", "opencv", "mediapipe"],
        default="auto",
        help="Optional face/eye review method. MediaPipe is used when available.",
    )
    parser.add_argument("--max-faces", type=int, default=8)
    parser.add_argument("--face-min-confidence", type=float, default=0.5)
    parser.add_argument("--eye-closed-ear-threshold", type=float, default=0.18)
    parser.add_argument("--eye-asymmetry-threshold", type=float, default=0.08)
    parser.add_argument("--max-contact-groups", type=int, default=200)
    parser.add_argument("--risk-sheet-size", type=int, default=24)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input folder not found: {input_dir}")
    output_dir = Path(args.output).expanduser().resolve() if args.output else input_dir.with_name(input_dir.name + "_cull")
    output_dir.mkdir(parents=True, exist_ok=True)

    files = iter_photo_files(input_dir, args.recursive)
    items = [analyze_file(path, input_dir, args) for path in files]
    assign_risk_flags(items, args)
    assign_decisions.duplicate_threshold = args.duplicate_threshold  # type: ignore[attr-defined]
    assign_decisions(items)

    duplicate_groups = len({item["duplicate_group_id"] for item in items if item.get("duplicate_group_id")})
    stats = {
        "total_files": len(items),
        "decoded_files": sum(1 for item in items if not item.get("decode_failed")),
        "decode_failures": sum(1 for item in items if item.get("decode_failed")),
        "raw_files": sum(1 for item in items if item.get("raw_file")),
        "duplicate_groups": duplicate_groups,
        "numpy_available": np is not None,
        "opencv_available": cv2 is not None,
        "rawpy_available": rawpy is not None,
        "heif_available": HEIF_AVAILABLE,
        "mediapipe_available": mp is not None,
    }
    settings = {
        "recursive": args.recursive,
        "analysis_max_dim": args.analysis_max_dim,
        "hash_size": args.hash_size,
        "duplicate_threshold": args.duplicate_threshold,
        "min_width": args.min_width,
        "min_height": args.min_height,
        "min_megapixels": args.min_megapixels,
        "severe_blur_threshold": args.severe_blur_threshold,
        "soft_blur_threshold": args.soft_blur_threshold,
        "eye_analysis": args.eye_analysis,
        "eye_closed_ear_threshold": args.eye_closed_ear_threshold,
        "eye_asymmetry_threshold": args.eye_asymmetry_threshold,
    }
    manifest_path = output_dir / "manifest.json"
    manifest = {
        "generated_at": utc_now(),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "settings": settings,
        "stats": stats,
        "items": items,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_manifest_csv(items, output_dir / "manifest.csv")
    write_duplicate_csv(items, output_dir / "duplicate_groups.csv")
    (output_dir / "selection_draft.json").write_text(
        json.dumps(draft_selection(items, manifest_path), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    contact_sheets = build_contact_sheets(
        items,
        output_dir,
        max_contact_groups=args.max_contact_groups,
        risk_sheet_size=args.risk_sheet_size,
    )
    write_report(manifest, output_dir / "first_pass_report.md", contact_sheets)
    print(json.dumps({"output_dir": str(output_dir), "stats": stats}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
