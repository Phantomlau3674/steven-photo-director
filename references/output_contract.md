# Output Contract

## Scanner Outputs

`manifest.json`

```json
{
  "generated_at": "ISO-8601 timestamp",
  "input_dir": "absolute input path",
  "settings": {},
  "stats": {},
  "items": []
}
```

Each item includes:

- `path`: absolute source path
- `relative_path`: path relative to the input folder
- `width`, `height`, `megapixels`
- `capture_time`
- `camera_make`, `camera_model`, `lens_model`
- `decoder`
- `blur_score`
- `brightness_mean`
- `shadow_clip_pct`
- `highlight_clip_pct`
- `contrast`
- `colorfulness`
- `face_count`
- `eye_count`
- `eye_analysis_method`
- `face_landmark_count`
- `eye_ear_min`, `eye_ear_avg`
- `eye_ear_left_min`, `eye_ear_right_min`
- `eye_closed_face_count`
- `eye_asymmetry_face_count`
- `eye_analysis_error`
- `dhash`
- `duplicate_group_id`
- `duplicate_rank`
- `script_duplicate_pick`
- `risk_flags`
- `technical_score`
- `first_pass_decision`

`manifest.csv` is the same data in spreadsheet-friendly form.

`duplicate_groups.csv` lists only near-duplicate groups.

`contact_sheets/` contains JPG sheets for duplicate groups and risk review.

## Grouping Outputs

`group_photos.py` writes a user-browsable scene grouping layer:

- `场景分组/scene_XXXX/`: grouped original photos as copies or hardlinks.
- `group_overview.jpg`: one representative image per group.
- `group_contact_sheets/scene_XXXX.jpg`: visual sheet per group.
- `groups.csv`: group metadata.
- `group_choices.csv`: user-editable scene selection template.
- `group_assignments.json`: path-to-group mapping for ensemble selection.

`group_choices.csv` supports:

- `include`: select only from included/priority groups when any include exists.
- `priority`: boost this group while keeping other non-excluded groups eligible.
- `exclude`: keep this group out of final selection.
- `keep_count`: optional group-level target count.

## Final Selection JSON

Create `selection.json` in this shape:

```json
{
  "source_manifest": "path/to/manifest.json",
  "selection_notes": "short summary",
  "requires_face_final_review": true,
  "face_keep_count": 12,
  "face_final_review_status": "required_before_delivery",
  "selections": [
    {
      "path": "absolute/source/file.jpg",
      "relative_path": "file.jpg",
      "decision": "KEEP",
      "rating": 5,
      "label": "green",
      "reason": "Best expression and cleanest composition in duplicate group dup_0001."
    }
  ]
}
```

Allowed decisions:

- `KEEP`
- `REVIEW`
- `REJECT`

Suggested ratings:

- `5`: KEEP hero or final delivery candidate
- `4`: KEEP alternate
- `3`: REVIEW
- `1`: REJECT
- `0`: hard reject or unreadable

## Export Outputs

`select_photos.py` is the preferred user-facing export. It writes:

- `selection.json`
- `selection_keep.txt`
- `selection_review.txt`
- `selection_reject.txt`
- `selection_summary.json`
- copied folders:
  - `精选/`
  - `待定/`
  - `废片/`

Default culling style is `balanced`: severe technical failures go to `废片/`, while duplicate alternates and people-photo candidates go to `待定/`, not `废片/`. Use `--cull-style gentle` for almost no automatic waste, and `--cull-style strict` for aggressive cleanup.

`ensemble_select_photos.py` writes the same user-facing folders and adds:

- `method_votes.csv`
- `method_disagreements.csv`

Use it when the user asks for stronger cross-checking or a more trustworthy X-photo selection.

Both selection scripts also write these face-review gate fields into `selection.json` and `selection_summary.json`:

- `face_keep_count`: number of selected KEEP rows with detected face/eye signals.
- `people_brief_hint`: whether the user brief sounds people/portrait/social oriented.
- `requires_face_final_review`: true when selected face rows exist or the brief asks for people-facing output.
- `face_final_review_status`: `required_before_delivery` or `not_required_by_detected_content`.

When `requires_face_final_review` is true, treat the copied KEEP folder as a draft until the face final review is applied or explicitly waived.

When `ensemble_select_photos.py` receives `--group-choices`, final selection respects the user's scene choices and records `scene_group_id` in `selection.json` and `method_votes.csv`.

## Face Final Review Outputs

`face_final_review.py build` writes:

- `face_keep_overview.jpg`: current selected people/portrait candidates.
- `comparison_sheets/`: one sheet per selected image, with current KEEP plus same-scene/same-sequence alternatives.
- `face_review_sets/`: browsable folders with `current_keep/` and `alternatives/` for each selected image.
- `face_review_sets.csv`: index of files materialized into the review sets.
- `face_review_choices.csv`: editable replacement template.
- `face_review_instructions.md`
- `face_review_summary.json`

`--review-scope auto` reviews selected KEEP rows with face/eye signals. If face detection data is unavailable, it falls back to all KEEP rows so people-heavy sets still get a manual face pass. Use `--review-scope all` when the user wants every selected image checked, or `--review-scope faces` when only detected face rows should be included.

`face_review_choices.csv` supports:

- `keep`
- `replace` with `replacement_relative_path`
- `drop`
- `reject`

`face_final_review.py apply` writes:

- `selection_face_final.json`
- final `精选/`, `待定/`, `废片/` folders
- `face_final_apply_summary.json`

After apply, `selection_face_final.json` sets `face_final_review_applied=true`, `face_final_review_status=applied` or `applied_with_warnings`, and clears `requires_face_final_review` unless unresolved CSV actions remain.

## Safety Output Expectations

Outputs are local and non-destructive by default. If the user asks to publish, upload, sell, or redistribute photos, check ownership/permission and privacy concerns first.

`export_selection.py` writes:

- `selection_keep.txt`
- `selection_review.txt`
- `selection_reject.txt`
- `selection_summary.json`

When called with `--copy-to`, it also creates:

- `精选/`, `待定/`, `废片/` by default
- `KEEP/`, `REVIEW/`, `REJECT/` when called with `--folder-style en`

When called with `--xmp`, it writes sidecar files next to the exported lists unless `--xmp-dir` is provided.
