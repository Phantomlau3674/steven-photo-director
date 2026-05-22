#!/usr/bin/env python3
"""Export final photo culling decisions into lists, optional copies, and XMP sidecars."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List
from xml.sax.saxutils import escape


DECISIONS = {"KEEP", "REVIEW", "REJECT"}
DEFAULT_RATING = {"KEEP": 5, "REVIEW": 3, "REJECT": 1}
DEFAULT_LABEL = {"KEEP": "green", "REVIEW": "yellow", "REJECT": "red"}
FOLDER_NAMES = {
    "zh": {"KEEP": "精选", "REVIEW": "待定", "REJECT": "废片"},
    "en": {"KEEP": "KEEP", "REVIEW": "REVIEW", "REJECT": "REJECT"},
}


def load_selection(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
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
        name = source.name + ".xmp"
        target = unique_destination(xmp_dir, Path(name))
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
    folder_names = FOLDER_NAMES.get(folder_style, FOLDER_NAMES["zh"])
    for folder_name in folder_names.values():
        (copy_to / folder_name).mkdir(parents=True, exist_ok=True)
    for item in rows:
        source = Path(item["path"])
        if not source.exists():
            copied.append({"source": str(source), "target": "", "status": "missing"})
            continue
        decision_dir = copy_to / folder_names[item["decision"]]
        decision_dir.mkdir(parents=True, exist_ok=True)
        target = unique_destination(decision_dir, source)
        try:
            status = materialize_file(source, target, file_mode)
        except Exception as exc:
            if file_mode == "hardlink":
                shutil.copy2(source, target)
                status = f"copied_after_hardlink_failed:{type(exc).__name__}"
            else:
                raise
        copied.append({"source": str(source), "target": str(target), "status": status})
    return copied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export final KEEP/REVIEW/REJECT photo selections.")
    parser.add_argument("selection_json", help="Path to selection.json.")
    parser.add_argument("--output", default=None, help="Output folder. Defaults to selection_json parent.")
    parser.add_argument("--copy-to", default=None, help="Optional folder for copied KEEP/REVIEW/REJECT files.")
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
    by_decision = {decision: [item for item in rows if item["decision"] == decision] for decision in DECISIONS}
    write_list(output_dir / "selection_keep.txt", by_decision["KEEP"])
    write_list(output_dir / "selection_review.txt", by_decision["REVIEW"])
    write_list(output_dir / "selection_reject.txt", by_decision["REJECT"])

    copied = []
    if args.copy_to:
        copied = copy_rows(rows, Path(args.copy_to).expanduser().resolve(), args.folder_style, args.file_mode)

    xmp_written = []
    if args.xmp:
        xmp_dir = Path(args.xmp_dir).expanduser().resolve() if args.xmp_dir else output_dir / "xmp_sidecars"
        xmp_written = write_xmp_sidecars(rows, xmp_dir)

    summary = {
        "source_selection": str(selection_path),
        "counts": dict(Counter(item["decision"] for item in rows)),
        "output_dir": str(output_dir),
        "copied": copied,
        "xmp_sidecars": xmp_written,
    }
    (output_dir / "selection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
