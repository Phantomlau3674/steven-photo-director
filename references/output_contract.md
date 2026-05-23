# Output Contract

This skill has two visible stages:

1. Machine candidate package: scripts clean, group, flag risk, expose duplicates, and organize candidates.
2. Final package: agent/model or human has visually reviewed scenes and photo choices.

Do not blur these stages. A script-only run must not be described as final.

## Human-Readable Roots

Every visible package must include:

```text
打开这里_README.md
```

Machine candidate package:

```text
01_模型审片候选/
  精选/
  待定/
  废片/
90_过程文件/
```

Final package:

```text
01_最终结果/
  精选/
  待定/
  废片/
90_过程文件/
```

`精选` under `01_模型审片候选` means candidate top picks only. It does not mean final selected photos.

## Scanner Outputs

`scan_photos.py` writes process files:

- `manifest.json`
- `manifest.csv`
- `duplicate_groups.csv`
- `contact_sheets/`

These values are triage signals. They are not final aesthetic decisions.

## Grouping Outputs

`group_photos.py` writes a provisional scene layer:

- `场景分组/scene_XXXX/`: grouped original photos as copies or hardlinks.
- `group_overview.jpg`: one representative image per group.
- `group_contact_sheets/scene_XXXX.jpg`: visual sheet per group.
- `groups.csv`: group metadata.
- `group_choices.csv`: editable scene decision template.
- `group_assignments.json`: path-to-group mapping for candidate generation.

The agent/model must visually inspect `场景分组/`, `group_overview.jpg`, and `group_contact_sheets/` before using scene choices.

`group_choices.csv` fields:

- `include`: model/user wants this scene considered.
- `priority`: model/user wants this scene boosted.
- `exclude`: model/user wants this scene kept out.
- `scene_quality`: 0-5 model/user quality judgment for the scene.
- `scene_role`: hero / strong / support / weak / drop.
- `evidence`: why the scene should receive more or fewer picks.
- `memory_note`: temporary model memory to carry into merged review.
- `keep_count`: model/user wants an approximate count for this scene.

Only model/user-reviewed `group_choices.csv` should affect final intent. Script grouping alone is not scene understanding.

## Score-Assisted Review Boards

After scene choices are set, use `build_review_boards.py` to create token-efficient visual evidence:

```text
review_boards/
  review_board_index.md
  scene_review_memory.csv
  review_pool.csv
  01_场景分数图板/
  02_合并精审图板/
  03_超级精选逐张清单.csv   # only in super-select mode
```

Use these boards after scene selection:

- `scene_review_memory.csv`: the model's temporary memory: scene role, quality, reason, and suggested allocation.
- `01_场景分数图板/`: score-assisted candidates inside each chosen scene.
- `02_合并精审图板/`: merged cross-scene balance review.
- `03_超级精选逐张清单.csv`: one-by-one original review list only when the user chose super-select mode.

Scores decide board order only. The model/editor decides final photos.

## Selection JSON

Candidate selection JSON should include:

```json
{
  "source_manifest": "path/to/manifest.json",
  "target_keep_count": 20,
  "selection_brief": "brief",
  "review_status": "machine_triage_only_requires_model_scene_review",
  "requires_model_scene_review": true,
  "requires_face_final_review": true,
  "face_final_review_status": "required_before_delivery",
  "selection_notes": "Machine triage only...",
  "selections": [
    {
      "path": "absolute/source/file.jpg",
      "relative_path": "file.jpg",
      "decision": "KEEP",
      "rating": 5,
      "label": "green",
      "reason": "Candidate reason, not final aesthetic verdict."
    }
  ]
}
```

Final reviewed selection JSON must set:

```json
{
  "review_status": "model_scene_reviewed_final",
  "requires_model_scene_review": false
}
```

If face/expression tuning was applied, final JSON should also set:

```json
{
  "face_final_review_applied": true,
  "face_final_review_status": "applied",
  "requires_face_final_review": false
}
```

Allowed decisions:

- `KEEP`
- `REVIEW`
- `REJECT`

## Candidate Scripts

`select_photos.py` and `ensemble_select_photos.py` write script candidate packages.

They write process files under `90_过程文件/`:

- `selection.json`
- `selection_keep.txt`
- `selection_review.txt`
- `selection_reject.txt`
- `selection_summary.json`

`ensemble_select_photos.py` also writes:

- `method_votes.csv`
- `method_disagreements.csv`

By default they copy/hardlink visible folders under:

```text
01_模型审片候选/
  精选/
  待定/
  废片/
```

Both scripts must write:

- `requires_model_scene_review=true`
- `review_status=machine_triage_only_requires_model_scene_review`

Use `ensemble_select_photos.py` when the user asks for "火力全开", cross-checking, or a stronger candidate pool. Even then, it is still only candidate organization.

For normal mode, the expected path is scene choices -> `ensemble_select_photos.py` -> `build_review_boards.py --mode board` -> model board review.

For super-select mode, the expected path is scene choices -> `ensemble_select_photos.py` -> `build_review_boards.py --mode super` -> board review -> one-by-one shortlist review.

## Face Final Review Outputs

`face_final_review.py build` writes review materials inside process files:

- `00_当前精选总览.jpg`
- `01_对比图/`
- `02_逐张对比_当前与候选/`
- `03_人工调整表_face_review_choices.csv`
- `face_review_summary.json`

`face_review_choices.csv` supports:

- `keep`
- `replace` with `replacement_relative_path`
- `drop`
- `reject`

`face_final_review.py apply` writes a final package:

- `打开这里_README.md`
- `01_最终结果/精选`
- `01_最终结果/待定`
- `01_最终结果/废片`
- `90_过程文件/selection_face_final.json`
- `90_过程文件/face_final_apply_summary.json`

## Final Export

Use `export_selection.py` only after the selection JSON has been model/editor reviewed:

```bash
python scripts/export_selection.py "/path/to/reviewed_selection.json" --output "/path/to/final_delivery/90_过程文件" --copy-to "/path/to/final_delivery/01_最终结果" --file-mode hardlink
```

If `requires_model_scene_review=true`, `export_selection.py` must warn in `打开这里_README.md` that the result is still a candidate package.

## Safety Output Expectations

Outputs are local and non-destructive by default. If the user asks to publish, upload, sell, or redistribute photos, check ownership/permission and privacy concerns first.
