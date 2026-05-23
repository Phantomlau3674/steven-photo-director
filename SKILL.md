---
name: steven-photo-director
description: Use this skill to cull, group, compare, and deliver large local photo folders with a non-destructive AI-assisted workflow. Use when the user gives many photos and wants waste removal, scene grouping, duplicate comparison, people/portrait review, or a best-X set. Scripts only perform first-pass cleanup, grouping, risk flags, contact sheets, and candidate organization; final scene understanding and final photo decisions must be made by the agent/model through visual review.
---

# Steven Photo Director

Use this skill when the user gives a folder of photos and wants a clean, human-readable delivery.

Default to non-destructive work: never delete, move, overwrite, or rewrite originals unless the user explicitly asks.

## Hard Rules

- Treat scripts as assistants, not judges.
- Scripts may inspect, decode, score technical risk, group visually similar/scene-like photos, expose duplicates, build contact sheets, and organize a candidate pool.
- Scripts must not be presented as having made the final aesthetic, scene, portrait, or best-X decision.
- Scene grouping is only a first pass. The agent/model must visually inspect scene folders/contact sheets, decide what each scene is, whether it matters, and how many images to keep from it.
- Never begin visual review from a global top-N list, global score ranking, or "the highest ranked few photos." That lets the script choose the story.
- The required visual order is: scene overview -> each scene/contact sheet -> choose scenes and per-scene counts -> merge chosen scenes into a review pool -> final cross-scene review.
- People photos require model/editor review before final delivery. Do not finalize faces, expressions, posture, or "好不好看" from program score alone.
- Face/expression review is part of the single final result workflow, not a separate final output.
- Do not silently choose dependency profile, scene grouping, culling strength, final count, review mode, editorial depth, preference seeds, or brief unless the user explicitly asks for unattended auto mode.

## Output Shape

Every user-facing run must have one obvious entry file:

```text
打开这里_README.md
```

After script triage, output only a candidate package:

```text
01_模型审片候选/
  精选/
  待定/
  未入选/
  废片/
90_过程文件/
```

After model/editor visual review, output the final package:

```text
01_最终结果/
  精选/
  待定/
  未入选/
  废片/
90_过程文件/
```

Keep JSON, CSV, manifests, votes, reports, contact sheets, and audit files inside `90_过程文件/`.

## Required User Gate

After inspecting the folder and before running scan/selection, ask all missing choices together. Do not default to `core`, `balanced`, no grouping, board mode, or light/standard/full silently.

```text
I found <N> photos, formats <...>, about <size>.

Choose before I run:
1. Dependency profile: core / plus / full.
2. Scene grouping first? yes/no.
3. Culling strength: gentle / balanced / strict.
4. Final count X, if you want a 精选 set.
5. Brief: for example people first, emotion, scene variety, social-post usable shots.
6. Review mode: board / super-select.
   - board: scene boards + score-assisted merged boards; token-efficient default.
   - super-select: after scene choices and boards, open a short one-by-one list for very fine final picking.
7. Editorial depth: light / standard / full.
   - light: scan, scene overview, 1-3 standout photos per scene, merged boards, final contact sheet/rescue prompt.
   - standard: light + similar-frame comparison boards, near-miss board, final reverse audit.
   - full: standard + portrait crop boards, pairwise tournament, and preference-seed expansion.
8. Preference seeds, if any: filenames the user already likes, such as IMG_20231004_190645.jpg.
9. Portrait/people importance: normal / important / critical.
```

Explain profiles briefly:

- `core`: Pillow + numpy; simple JPG/PNG/WebP/TIFF.
- `plus`: HEIC/RAW/OpenCV/imagehash/scikit-image; stronger technical screening.
- `full`: MediaPipe/PyIQA/PyTorch where available; better people, portrait, closed-eye, and quality checks.

Recommendation rules:

- Recommend `core` only for simple non-portrait JPG/PNG/WebP/TIFF folders where the user wants fast rough organization.
- Recommend `plus` for HEIC/RAW, larger batches, travel/events, or serious technical cleanup.
- Recommend `full` for people, portraits, family, weddings, social posts, closed-eye/expression risk, or when the user says "火力全开".
- Still ask the user to choose even when the recommendation is obvious.

If the user says auto/unattended:

```text
profile=full if already installed or accepted, otherwise plus
grouping=yes
cull-style=balanced
selection_method=ensemble
review_mode=board
editorial_depth=light
file-mode=hardlink
```

If no final count is supplied, run scan + optional grouping only, then ask for the final count and brief.

## Dependency Setup

Inspect first:

```bash
python scripts/inspect_photo_project.py "/path/to/photos" --recursive
```

Before installing, show the user the dependency levels and complexity:

```bash
python scripts/prepare_env.py --explain
```

Ask before installing. Then run the chosen profile:

```bash
python scripts/prepare_env.py --profile <core|plus|full>
```

Read the printed `imports` and `pip_check`. Do not claim full capability if imports are missing or `pip_check` reports broken requirements.

## Workflow

### 1. Scan

```bash
python scripts/scan_photos.py "/path/to/photos" --output "/path/to/photo_cull_output" --recursive
```

Scanner outputs are process files. They do not decide final taste.

### 2. First-Pass Scene Grouping

Run when the user chose grouping:

```bash
python scripts/group_photos.py "/path/to/photo_cull_output/manifest.json" --output "/path/to/photo_cull_output/90_过程文件/groups" --file-mode hardlink
```

The script-created groups are provisional. The agent/model must inspect:

- `90_过程文件/groups/场景分组/`
- `90_过程文件/groups/group_overview.jpg`
- `90_过程文件/groups/group_contact_sheets/`

Then the agent/model decides scene meaning and priority before opening any global Top-N candidate list. If useful, write those decisions into `group_choices.csv`:

- `include`: model/user wants this scene considered.
- `priority`: model/user wants this scene boosted.
- `exclude`: model/user wants this scene kept out.
- `scene_quality`: 0-5 model/user judgment after seeing the scene board.
- `scene_role`: hero / strong / support / weak / drop.
- `evidence`: why the scene deserves more or fewer picks.
- `memory_note`: short temporary memory to carry into merged review.
- `keep_count`: model/user wants an approximate count for this scene.

Never treat `groups.csv` or script group scores as the scene decision.

Mandatory scene-first protocol:

1. Open `group_overview.jpg` to understand the whole shoot.
2. Open `group_contact_sheets/scene_XXXX.jpg` or `场景分组/scene_XXXX/` for every plausible scene.
3. Mark each scene `include`, `priority`, `exclude`, or leave blank.
4. Fill `scene_quality`, `scene_role`, `evidence`, and `memory_note`.
5. Add `keep_count` when a good scene deserves more picks or a weak scene deserves fewer.
6. Only after this, build the merged candidate pool.

If the agent cannot visually review scene groups, stop after grouping and tell the user this is only a scene pack. Do not continue by reading high-score files.

### 3. Build A Candidate Pool

Only build the candidate pool after scene choices are model/user-reviewed. For small folders where the user explicitly waived grouping, say that scene-first review was waived.

```bash
python scripts/ensemble_select_photos.py "/path/to/photo_cull_output/manifest.json" --keep-count 20 --brief "model-reviewed scenes" --group-choices "/path/to/photo_cull_output/90_过程文件/groups/group_choices.csv" --group-assignments "/path/to/photo_cull_output/90_过程文件/groups/group_assignments.json" --file-mode hardlink --output "/path/to/photo_cull_output/selection_work"
```

Fallback only when grouping was explicitly waived:

```bash
python scripts/ensemble_select_photos.py "/path/to/photo_cull_output/manifest.json" --keep-count 20 --brief "grouping explicitly waived by user" --cull-style balanced --file-mode hardlink --output "/path/to/photo_cull_output/selection_work"
```

This stage writes:

```text
selection_work/打开这里_README.md
selection_work/01_模型审片候选/精选
selection_work/01_模型审片候选/待定
selection_work/01_模型审片候选/废片
selection_work/90_过程文件/
```

This is not final. `精选` here means merged review candidates after scene selection, not global score winners.

### 4. Build Score-Assisted Review Boards

After scene choices exist, combine the model's scene memory with script scores:

```bash
python scripts/build_review_boards.py "/path/to/photo_cull_output/manifest.json" --group-choices "/path/to/photo_cull_output/90_过程文件/groups/group_choices.csv" --group-assignments "/path/to/photo_cull_output/90_过程文件/groups/group_assignments.json" --method-votes "/path/to/photo_cull_output/selection_work/90_过程文件/method_votes.csv" --target-count 20 --mode board --editorial-depth light --output "/path/to/photo_cull_output/selection_work/90_过程文件/review_boards"
```

If the user chose super-select mode at startup:

```bash
python scripts/build_review_boards.py "/path/to/photo_cull_output/manifest.json" --group-choices "/path/to/photo_cull_output/90_过程文件/groups/group_choices.csv" --group-assignments "/path/to/photo_cull_output/90_过程文件/groups/group_assignments.json" --method-votes "/path/to/photo_cull_output/selection_work/90_过程文件/method_votes.csv" --target-count 20 --mode super --editorial-depth full --output "/path/to/photo_cull_output/selection_work/90_过程文件/review_boards"
```

Use:

- `scene_review_memory.csv` to remember why good scenes get more picks and weak scenes get fewer.
- `00_单张出彩候选/` to rescue 1-3 possible standout photos from every scene, even weak scenes.
- `01_场景分数图板/` to compare scored candidates inside each chosen scene.
- `02_合并精审图板/` to balance the full set across scenes.
- `03_超级精选逐张清单.csv` only in super-select mode.
- `04_相似图对比板/` in standard/full mode to force side-by-side comparison of duplicates, same pose, or same scene.
- `05_用户偏好种子扩展/` in full mode when the user names liked photos.
- `06_人像局部放大板/` in full mode for expression, hands, posture, hair blocking, and background cut lines.
- `07_最终反向审查/` always for the audit questions; with a reviewed selection JSON it also writes final contact sheet, near-miss board, and unselected standout rescue board.
- `08_二选一锦标赛/` in full mode for forced pairwise decisions.

Scores are evidence for ordering review boards, not final decisions.

Depth rule:

- `light` is the default for speed. It builds scene boards, standout candidates, merged boards, and the final audit prompt.
- `standard` is for serious selection. It adds similar-frame comparison boards, near-miss surfaces, and final reverse audit.
- `full` is for people/portrait/social-post work. It adds crop boards, pairwise tournament, and optional preference-seed expansion.

### 5. Mandatory Model Review

Load `references/aesthetic_rubric.md` before final aesthetic calls.

The agent/model must visually review:

- `90_过程文件/review_boards/scene_review_memory.csv`
- `90_过程文件/review_boards/00_单张出彩候选/`
- `90_过程文件/review_boards/01_场景分数图板/`
- `90_过程文件/review_boards/02_合并精审图板/`
- `90_过程文件/review_boards/04_相似图对比板/`, when present
- `90_过程文件/review_boards/07_最终反向审查/`, when present
- `90_过程文件/method_disagreements.csv`
- duplicate contact sheets
- risk contact sheets
- scene group folders/contact sheets first, if not already reviewed

Do not read "the top 20 highest scored photos" as the first model-review step. If that happens, restart from the scene overview.

In board mode, open originals only for ambiguous ties, faces, expressions, and final confidence checks.

In super-select mode, after the boards, follow `03_超级精选逐张清单.csv` and inspect originals one by one. Do not run super-select unless the user chose it at startup or explicitly asks later.

Review by scene and sequence, not by isolated ranking. For each scene, decide:

- what the scene is
- whether it belongs in the final story/set
- which frame has the best moment, expression, composition, and usefulness
- whether alternates belong in 待定, 未入选, or 废片
- whether any scene-level standout deserves rescue even if its scene is weak
- whether the final set has semantic repeats or missed heroes

After model/editor review, create or update a reviewed selection JSON:

```json
{
  "review_status": "model_scene_reviewed_final",
  "requires_model_scene_review": false,
  "selections": []
}
```

### 6. People And Face Tuning

For people, portraits, family, weddings, events, or social posts, build face review materials:

```bash
python scripts/face_final_review.py build "/path/to/photo_cull_output/selection_work/90_过程文件/selection.json" "/path/to/photo_cull_output/manifest.json" --group-assignments "/path/to/photo_cull_output/90_过程文件/groups/group_assignments.json" --review-scope auto --output "/path/to/photo_cull_output/selection_work/90_过程文件/02_人脸表情终审"
```

Review:

- `00_当前精选总览.jpg`
- `01_对比图/`
- `02_逐张对比_当前与候选/`
- `03_人工调整表_face_review_choices.csv`

Apply face/expression decisions into the final package:

```bash
python scripts/face_final_review.py apply "/path/to/photo_cull_output/selection_work/90_过程文件/selection.json" "/path/to/photo_cull_output/manifest.json" "/path/to/photo_cull_output/selection_work/90_过程文件/02_人脸表情终审/03_人工调整表_face_review_choices.csv" --output "/path/to/photo_cull_output/final_delivery" --file-mode hardlink
```

If the user explicitly waives face tuning, record that in the final summary.

### 7. Export Final Result

When the reviewed selection JSON has `requires_model_scene_review=false`, export the final package:

```bash
python scripts/export_selection.py "/path/to/reviewed_selection.json" --output "/path/to/final_delivery/90_过程文件" --copy-to "/path/to/final_delivery/01_最终结果" --file-mode hardlink
```

The final delivery must contain:

```text
final_delivery/打开这里_README.md
final_delivery/01_最终结果/精选
final_delivery/01_最终结果/待定
final_delivery/01_最终结果/未入选
final_delivery/01_最终结果/废片
final_delivery/90_过程文件/
```

## Final Answer

Tell the user:

- exact path to `打开这里_README.md`
- exact path to `01_模型审片候选` or `01_最终结果`
- counts for 精选 / 待定 / 未入选 / 废片
- current status: machine candidate, model-reviewed final, or face-review-applied final
- whether face tuning was applied, waived, or still required

Never lead with `manifest.json`, `selection.json`, or CSV paths.

## Safety

For public demos or Douyin use, read `references/safety_and_rights.md`. Use owned, consented, licensed, or synthetic photos; avoid private albums, minors, IDs, screens, addresses, and GPS/EXIF leaks.

## References

- Use `references/output_contract.md` for exact output fields.
- Use `references/culling_policy.md` for reject/review/keep policy.
- Use `references/degradation_strategy.md` when libraries or visual tools are missing.
- Use `references/tool_landscape.md` only as background.
