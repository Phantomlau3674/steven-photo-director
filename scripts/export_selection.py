#!/usr/bin/env python3
"""Export reviewed photo culling decisions into readable folders and sidecars."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List
from xml.sax.saxutils import escape


DECISION_ORDER = ("KEEP", "REVIEW", "UNSELECTED", "REJECT")
DECISIONS = set(DECISION_ORDER)
DEFAULT_RATING = {"KEEP": 5, "REVIEW": 3, "UNSELECTED": 2, "REJECT": 1}
DEFAULT_LABEL = {"KEEP": "green", "REVIEW": "yellow", "UNSELECTED": "blue", "REJECT": "red"}
FOLDER_NAMES = {
    "zh": {"KEEP": "精选", "REVIEW": "待定", "UNSELECTED": "未入选", "REJECT": "废片"},
    "en": {"KEEP": "KEEP", "REVIEW": "REVIEW", "UNSELECTED": "UNSELECTED", "REJECT": "REJECT"},
}
DRAFT_RESULT_DIR = "01_模型审片候选"
FINAL_RESULT_DIR = "01_最终结果"
HUMAN_RESULT_DIR = FINAL_RESULT_DIR
PROCESS_DIR = "90_过程文件"
README_NAME = "打开这里_README.md"


def load_selection(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "selections" not in data or not isinstance(data["selections"], list):
        raise SystemExit("selection.json must contain a list field named 'selections'.")
    for index, item in enumerate(data["selections"], 1):
        decision = str(item.get("decision", "")).upper()
        if decision not in DECISIONS:
            raise SystemExit(f"Invalid decision at selection #{index}: {decision}")
        item["decision"] = decision
        item.setdefault("rating", DEFAULT_RATING[decision])
        item.setdefault("label", DEFAULT_LABEL[decision])
        if not item.get("path"):
            raise SystemExit(f"Selection #{index} is missing 'path'.")
    return data


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


def write_list(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    lines = []
    for item in rows:
        reason = str(item.get("reason") or "").replace("\r", " ").replace("\n", " ")
        lines.append(f"{item['path']}\t{item.get('rating')}\t{item.get('label')}\t{reason}")
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def decision_folder_paths(copy_to: Path, folder_style: str = "zh") -> Dict[str, Path]:
    folder_names = FOLDER_NAMES.get(folder_style, FOLDER_NAMES["zh"])
    return {decision: copy_to / folder_name for decision, folder_name in folder_names.items()}


def write_open_here(
    output_dir: Path,
    *,
    title: str,
    result_dir: Path | None = None,
    process_dir: Path | None = None,
    selection_json: Path | None = None,
    summary_json: Path | None = None,
    counts: Dict[str, Any] | None = None,
    next_action: str | None = None,
    status: str | None = None,
    extra_lines: List[str] | None = None,
) -> Path:
    lines = [
        f"# {title}",
        "",
        "先看这里。这个目录是给人打开的交付包，不需要先读 JSON/CSV。",
        "",
    ]
    if result_dir:
        folders = decision_folder_paths(result_dir)
        lines.extend(
            [
                "## 主要结果",
                "",
                f"- 精选: `{folders['KEEP']}`",
                f"- 待定: `{folders['REVIEW']}`",
                f"- 未入选: `{folders['UNSELECTED']}`",
                f"- 废片: `{folders['REJECT']}`",
                "",
            ]
        )
    if status:
        lines.extend(["## 状态", "", status, ""])
    if counts:
        lines.extend(
            [
                "## 数量",
                "",
                f"- 精选: {counts.get('KEEP', 0)}",
                f"- 待定: {counts.get('REVIEW', 0)}",
                f"- 未入选: {counts.get('UNSELECTED', 0)}",
                f"- 废片: {counts.get('REJECT', 0)}",
                "",
            ]
        )
    if next_action:
        lines.extend(["## 下一步", "", next_action, ""])
    if process_dir or selection_json or summary_json:
        lines.extend(["## 过程文件", ""])
        if process_dir:
            lines.append(f"- 过程文件目录: `{process_dir}`")
        if selection_json:
            lines.append(f"- 机器可读选择表: `{selection_json}`")
        if summary_json:
            lines.append(f"- 运行摘要: `{summary_json}`")
        lines.append("")
    if extra_lines:
        lines.extend(extra_lines)
        lines.append("")
    path = output_dir / README_NAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def xmp_text(item: Dict[str, Any]) -> str:
    rating = int(item.get("rating") or DEFAULT_RATING[item["decision"]])
    label = escape(str(item.get("label") or DEFAULT_LABEL[item["decision"]]))
    reason = escape(str(item.get("reason") or ""))
    decision = escape(item["decision"])
    return f"""<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:steven="https://example.local/steven-photo-director/1.0/"
      xmp:Rating="{rating}"
      xmp:Label="{label}"
      steven:Decision="{decision}"
      steven:Reason="{reason}" />
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""


def write_xmp_sidecars(rows: List[Dict[str, Any]], xmp_dir: Path) -> List[str]:
    xmp_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for item in rows:
        source = Path(item["path"])
        target = unique_destination(xmp_dir, Path(source.name + ".xmp"))
        target.write_text(xmp_text(item), encoding="utf-8")
        written.append(str(target))
    return written


def materialize_file(source: Path, target: Path, file_mode: str) -> str:
    if file_mode == "hardlink":
        os.link(source, target)
        return "hardlinked"
    shutil.copy2(source, target)
    return "copied"


def copy_rows(
    rows: List[Dict[str, Any]],
    copy_to: Path,
    folder_style: str = "zh",
    file_mode: str = "copy",
) -> List[Dict[str, str]]:
    copied = []
    folder_paths = decision_folder_paths(copy_to, folder_style)
    for folder in folder_paths.values():
        folder.mkdir(parents=True, exist_ok=True)
    for item in rows:
        source = Path(item["path"])
        if not source.exists():
            copied.append({"source": str(source), "target": "", "status": "missing"})
            continue
        decision_dir = folder_paths[item["decision"]]
        decision_dir.mkdir(parents=True, exist_ok=True)
        target = unique_destination(decision_dir, source)
        status = file_mode
        try:
            status = materialize_file(source, target, file_mode)
        except OSError as exc:
            if file_mode == "hardlink":
                shutil.copy2(source, target)
                status = f"copied_after_hardlink_failed:{type(exc).__name__}"
            else:
                raise
        copied.append({"source": str(source), "target": str(target), "status": status})
    return copied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export KEEP/REVIEW/UNSELECTED/REJECT photo selections after model/editor review.")
    parser.add_argument("selection_json", help="Path to selection.json.")
    parser.add_argument("--output", default=None, help="Output folder. Defaults to selection_json parent.")
    parser.add_argument("--copy-to", default=None, help="Optional folder for copied KEEP/REVIEW/UNSELECTED/REJECT files.")
    parser.add_argument("--folder-style", choices=["zh", "en"], default="zh", help="Folder names for --copy-to.")
    parser.add_argument("--file-mode", choices=["copy", "hardlink"], default="copy", help="Use hardlink to avoid extra disk usage on the same volume.")
    parser.add_argument("--xmp", action="store_true", help="Write XMP sidecars into output/xmp_sidecars or --xmp-dir.")
    parser.add_argument("--xmp-dir", default=None, help="Folder for XMP sidecars.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selection_path = Path(args.selection_json).expanduser().resolve()
    data = load_selection(selection_path)
    output_dir = Path(args.output).expanduser().resolve() if args.output else selection_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = data["selections"]
    by_decision = {decision: [item for item in rows if item["decision"] == decision] for decision in DECISION_ORDER}
    write_list(output_dir / "selection_keep.txt", by_decision["KEEP"])
    write_list(output_dir / "selection_review.txt", by_decision["REVIEW"])
    write_list(output_dir / "selection_unselected.txt", by_decision["UNSELECTED"])
    write_list(output_dir / "selection_reject.txt", by_decision["REJECT"])

    copied = []
    copy_to_path = None
    if args.copy_to:
        copy_to_path = Path(args.copy_to).expanduser().resolve()
        copied = copy_rows(rows, copy_to_path, args.folder_style, args.file_mode)

    xmp_written = []
    if args.xmp:
        xmp_dir = Path(args.xmp_dir).expanduser().resolve() if args.xmp_dir else output_dir / "xmp_sidecars"
        xmp_written = write_xmp_sidecars(rows, xmp_dir)

    counts = dict(Counter(item["decision"] for item in rows))
    summary_path = output_dir / "selection_summary.json"
    review_status = str(data.get("review_status") or "")
    if data.get("requires_model_scene_review", False):
        status = "警告：这个 selection.json 仍标记为需要模型场景审片；导出结果只能当候选包，不能当最终精选。"
        title = "Steven Photo Director 模型审片候选"
    elif data.get("requires_face_final_review", False):
        status = "警告：这个 selection.json 仍标记为需要人脸/表情终审；导出结果不能当最终精选，除非用户明确放弃人脸特调。"
        title = "Steven Photo Director 待终审结果"
    elif data.get("face_final_review_applied"):
        status = "已应用人脸/表情终审，并按 selection.json 导出最终结果。"
        title = "Steven Photo Director 最终结果"
    elif review_status in {"model_reviewed_final", "model_scene_reviewed_final"}:
        status = "已按模型/人工场景审片后的 selection.json 导出最终结果。"
        title = "Steven Photo Director 最终结果"
    else:
        status = "已按 selection.json 导出；请确认该文件已经由模型/人工场景审片更新，不是原始机器候选。"
        title = "Steven Photo Director 导出结果"

    readme_path = None
    if copy_to_path:
        delivery_root = copy_to_path.parent if copy_to_path.name in {DRAFT_RESULT_DIR, FINAL_RESULT_DIR} else output_dir
        readme_path = write_open_here(
            delivery_root,
            title=title,
            result_dir=copy_to_path,
            process_dir=output_dir,
            selection_json=selection_path,
            summary_json=summary_path,
            counts=counts,
            status=status,
        )

    summary = {
        "source_selection": str(selection_path),
        "counts": counts,
        "output_dir": str(output_dir),
        "copied": copied,
        "xmp_sidecars": xmp_written,
        "review_status": review_status,
        "requires_model_scene_review": data.get("requires_model_scene_review", False),
        "requires_face_final_review": data.get("requires_face_final_review", False),
        "open_here": str(readme_path) if readme_path else None,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
