---
name: steven-photo-director
description: Use this skill to screen, cull, rank, and organize large photo folders with a non-destructive agent workflow. It combines script-based first-pass checks for blur, exposure, low resolution, decode failures, and near-duplicate groups with a second-pass aesthetic review that produces KEEP, REVIEW, and REJECT selection lists. It is portable for general-purpose agents and includes explicit degradation paths when optional vision libraries are unavailable.
metadata:
  short-description: Non-destructive AI-assisted photo culling
---

# Steven Photo Director

Use this skill when the user provides a folder of photos and wants help selecting the best images, finding duplicates, rejecting obvious technical failures, or building a final keep/review/reject list.

The default behavior is non-destructive: never delete originals or rewrite photo metadata unless the user explicitly asks for that. Write reports, sidecars, copied selections, and lists into an output folder.

## Quick Start

1. Get the photo folder path. If the path is missing, ask for it.
2. Inspect the photo project before installing anything:

```bash
python scripts/inspect_photo_project.py "/path/to/photos" --recursive
```

3. Explain the dependency choice to the user and ask before installing. Use `references/dependencies.md` to list the profiles, libraries, approximate complexity, and what each profile unlocks. For ordinary JPG/PNG folders, recommend `core`.
4. After the user chooses a profile, prepare dependencies:

```bash
python scripts/prepare_env.py --profile core
```

Use `--profile plus` only when HEIC, RAW, or stronger OpenCV metrics are worth the install. Use `--profile full` only when the user explicitly wants face-landmark or learned image-quality experiments and accepts heavier dependencies.

5. Stop for a user decision gate before scanning or selecting, unless the user already supplied these choices or explicitly asked for unattended automatic mode.

Ask all missing choices together:

```text
I found <N> photos, formats <...>, about <size>. Recommended dependency profile: <core/plus/full>.
Before I run the cull, choose:
1. Scene grouping first? yes/no. Recommended: yes for trips, portraits, events, and mixed folders.
2. Culling strength: balanced / gentle / strict. Recommended: balanced.
   - gentle: almost no automatic waste; keeps most borderline files in 待定.
   - balanced: obvious severe blur/technical failures go to 废片; people duplicates stay 待定.
   - strict: aggressive dedupe and technical rejection.
3. Final count X, if you want a 精选 folder now.
4. Selection brief, for example: "prefer people, emotion, scene variety, and social-post usable shots."
```

Do not run `scan_photos.py`, `group_photos.py`, `select_photos.py`, or `ensemble_select_photos.py` before this gate, except for read-only inventory and dependency checks.

If the user says "auto" or asks not to be interrupted, use:

```text
grouping=yes, cull-style=balanced, selection_method=ensemble, file-mode=hardlink
```

If no final count is provided, run scan + grouping only, then stop and ask for the final count.

6. Run the first-pass scanner after the decision gate:

```bash
python scripts/scan_photos.py "/path/to/photos" --output "/path/to/photo_cull_output" --recursive
```

On Windows, use the available Python executable and quote paths:

```powershell
python .\scripts\scan_photos.py "D:\photos\shoot" --output "D:\photos\shoot_cull" --recursive
```

7. If the user chose scene grouping, run grouping mode:

```bash
python scripts/group_photos.py "/path/to/photo_cull_output/manifest.json" --output "/path/to/photo_cull_output/groups" --file-mode hardlink
```

This writes:

- `场景分组/scene_XXXX/`
- `group_overview.jpg`
- `group_contact_sheets/`
- `group_choices.csv`
- `group_assignments.json`

Let the user browse `场景分组/` or `group_overview.jpg`. They can edit `group_choices.csv`:

- `include`: select only from included/priority groups when any include exists
- `priority`: boost this group
- `exclude`: keep out of final selection
- `keep_count`: optional target count for that group

If the user says no, skip grouping and continue to direct selection.

8. Confirm the final count and selection brief if still missing.

Examples:

- "Select 20 clean travel highlights."
- "Select 9 photos for a social post, prefer people, emotion, and variety."
- "Separate obvious rejects and give me 50 client-safe picks."

9. Create practical output folders:

```bash
python scripts/select_photos.py "/path/to/photo_cull_output/manifest.json" --keep-count 20 --brief "clean travel highlights" --cull-style balanced --output "/path/to/photo_cull_output/final_selection"
```

For high-stakes or "firepower" selection, use the ensemble selector instead:

```bash
python scripts/ensemble_select_photos.py "/path/to/photo_cull_output/manifest.json" --keep-count 20 --brief "clean travel highlights" --cull-style balanced --file-mode hardlink --output "/path/to/photo_cull_output/ensemble_selection"
```

It cross-checks multiple methods and writes `method_votes.csv` plus `method_disagreements.csv`.

If the user edited `group_choices.csv`, pass it into ensemble selection:

```bash
python scripts/ensemble_select_photos.py "/path/to/photo_cull_output/manifest.json" --keep-count 20 --brief "user-selected scenes" --group-choices "/path/to/photo_cull_output/groups/group_choices.csv" --file-mode hardlink --output "/path/to/photo_cull_output/ensemble_selection"
```

This writes `selection.json`, text lists, and copied folders:

- `精选/`
- `待定/`
- `废片/`

Default `--cull-style balanced` is people-friendly but still creates a real waste folder:

- `精选/`: the requested X-image machine draft.
- `待定/`: good alternates, sequence variants, expression differences, and soft-but-possibly-useful frames.
- `废片/`: clear technical failures such as severe blur, decode failure, or low resolution.

Use `--cull-style gentle` when the user wants almost no automatic waste. Use `--cull-style strict` only when the user explicitly wants aggressive deduplication.

For large folders on the same disk, add `--file-mode hardlink` to create usable output folders without duplicating file contents.

Check `selection_summary.json` after selection. If `requires_face_final_review` is true, the `KEEP` folder is only a machine draft, not a final delivery. Build and apply the face final review pack before telling the user the people/portrait selection is finished.

10. For people, portrait, family, wedding, event, or social-post selections, run the face final review gate before treating `精选/` as final:

```bash
python scripts/face_final_review.py build "/path/to/photo_cull_output/ensemble_selection/selection.json" "/path/to/photo_cull_output/manifest.json" --group-assignments "/path/to/photo_cull_output/groups/group_assignments.json" --review-scope auto --output "/path/to/photo_cull_output/face_final_review"
```

Review `face_keep_overview.jpg`, `comparison_sheets/`, or the browsable `face_review_sets/` folders, then edit `face_review_choices.csv`:

- `keep`: current selected face is good.
- `replace`: set `replacement_relative_path` to a better same-scene/same-sequence candidate.
- `drop`: move current selected face to `待定`.
- `reject`: move current selected face to `废片`.

Apply the edits:

```bash
python scripts/face_final_review.py apply "/path/to/photo_cull_output/ensemble_selection/selection.json" "/path/to/photo_cull_output/manifest.json" "/path/to/photo_cull_output/face_final_review/face_review_choices.csv" --output "/path/to/photo_cull_output/face_final"
```

If the user explicitly accepts the machine draft without face tuning, record that waiver in the final summary. Otherwise, for face-heavy sets, final user-facing folders should come from `face_final/`, not directly from `ensemble_selection/folders/`.

11. Read these files only when deeper audit is needed:

- `first_pass_report.md`
- `manifest.csv`
- `manifest.json`
- `duplicate_groups.csv`
- `contact_sheets/`

12. Visually inspect `精选/`, selected scene folders, and duplicate contact sheets. For duplicate groups, compare images within the group rather than judging each file alone.
13. Load `references/aesthetic_rubric.md` before making final aesthetic calls.
14. If needed, edit `selection.json` and re-export:

```bash
python scripts/export_selection.py "/path/to/photo_cull_output/final_selection/selection.json" --output "/path/to/photo_cull_output/final_selection" --copy-to "/path/to/photo_cull_output/final_selection/folders"
```

Use copy folders by default because they are the user-facing result. Still do not delete or move originals unless the user explicitly asks.

## Workflow

### First Pass: Mechanical Triage

The scanner flags:

- decode failures
- very low resolution
- severe blur and soft focus risk
- underexposure and overexposure risk
- low contrast
- near-duplicate groups using perceptual hashing
- optional face/eye review signals when MediaPipe or OpenCV is available

Treat these as review signals, not final verdicts. Motion blur, low-key exposure, grain, silhouettes, closed eyes, and imperfect sharpness can be intentional.

### Second Pass: Agent Review

Use the scanner output to reduce effort:

- Review duplicate groups first; choose the strongest moment, not merely the sharpest frame.
- For people photos, never treat non-picked duplicates as automatic waste; expressions, gaze, hands, and client taste can make a second frame valuable.
- Review high-risk images next; keep technically flawed images only when their moment, mood, or information value is strong.
- Review the remaining pass set for story, emotion, composition, and usefulness.

Make decisions as:

- `KEEP`: final usable image.
- `REVIEW`: maybe useful, needs human/client taste, editing test, or context.
- `REJECT`: not useful enough to keep in the active selection.

Do not over-select. A good cull usually has a clear reason for each KEEP.

### Ensemble Review

When the user wants extra rigor, run `ensemble_select_photos.py`. It compares:

- technical quality score
- duplicate-group representative score
- aesthetic proxy score for exposure, contrast, color, and usable sharpness
- people-friendly score that avoids dumping sequence alternates into waste

Treat `method_disagreements.csv` as the review queue: these images were liked by at least one method but missed the final count.

### Scene Grouping Mode

When the user wants control before selection, run `group_photos.py`. This groups photos by visual continuity and shooting sequence, then creates browsable folders. The user can choose whole scenes instead of individual files. Use this for trips, portraits, events, weddings, family shoots, or any shoot with obvious scene changes.

The grouping mode is not a semantic classifier. It is a practical browsing layer: scene runs, near-duplicate clusters, and visual shifts. The agent should still use the aesthetic rubric inside chosen groups.

### Face Final Review

For people-heavy projects, machine scoring is not enough. The agent must build a final face review pack before calling the selection final. Use `face_final_review.py` to compare each selected face with same-scene, same-duplicate, and nearby alternatives.

Selection scripts write `requires_face_final_review`. Treat `true` as a hard workflow gate: the agent may show the draft, but should not present the people/portrait keep set as finished until `face_final_review.py apply` has produced the final folders or the user has explicitly waived the face review.

Judge:

- eyes and blink risk
- mouth shape and expression timing
- face angle and jaw tension
- hands, shoulders, and posture
- whether the face feels flattering and natural
- whether the image still has a role if the face is imperfect

Do not reduce this to a beauty score. Treat it as human taste and expression timing.

## Output Rules

Use the contract in `references/output_contract.md`.

Minimum final deliverables:

- `selection.json`
- `精选/`
- `待定/`
- `废片/`
- when grouping was requested: `场景分组/`, `group_overview.jpg`, and `group_choices.csv`
- when people photos are selected: `face_keep_overview.jpg`, `comparison_sheets/`, `face_review_choices.csv`, and `face_final/` unless the user waived face tuning
- `selection_keep.txt`
- `selection_review.txt`
- `selection_reject.txt`
- a short final summary with the requested count, actual selected count, and output folder

## Dependency Consent

Do not silently install the full stack. Before installing dependencies, show the user:

- detected file count and format mix
- recommended profile
- libraries that will be installed
- expected complexity: light, medium, or heavy
- what will not work if optional libraries are skipped

Default recommendation:

- `core` for JPG/PNG/WebP/TIFF culling.
- `plus` for HEIC, RAW, or stronger OpenCV-assisted metrics.
- `full` only for explicit face-landmark or learned quality/aesthetic experiments.

Do not stop the cull only because optional packages are unavailable. Record missing capabilities in the report and continue.

Closed-eye review requires `full` or an environment that already has MediaPipe. Without MediaPipe, the Skill can still use OpenCV as a weak face/eye review signal, but it must not claim reliable closed-eye detection.

## Safety And Rights

For personal/local use, this Skill has low copyright risk because it processes the user's own local files and does not publish, train on, or upload photos by default. Still follow `references/safety_and_rights.md` for ownership, privacy, face photos, and open-source dependency notes.

If the user intends to post a Douyin/video demo, read `references/safety_and_rights.md` first and steer them toward a public-safe demo album: owned or consented photos, no private EXIF/GPS, no client/private albums, and no bundled third-party dependency binaries.

## Research Basis

For tool selection and enhancement ideas, read `references/tool_landscape.md`. Keep it as background only; the Skill's default path should remain portable and non-destructive.
