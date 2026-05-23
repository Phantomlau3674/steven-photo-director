# Steven Photo Director

Reusable agent skill for non-destructive photo culling, scene grouping, duplicate comparison, standout rescue, portrait review, and human-readable delivery folders.

## What It Does

- Inspects a photo folder before installing dependencies.
- Asks for dependency profile, scene grouping, culling strength, final count, brief, review mode, editorial depth, preference seeds, and portrait importance.
- Uses scripts only for first-pass cleanup, grouping, risk flags, contact sheets, and review-board generation.
- Requires the model/editor to decide scenes, story value, portraits, expressions, and final KEEP choices.
- Keeps originals untouched by default.

## Editorial Depth

- `light`: fast default. Scene overview, 1-3 standout candidates per scene, merged review boards, and final reverse-audit prompt.
- `standard`: serious selection. Adds similar-frame comparison boards, near-miss review, and final reverse audit.
- `full`: people/social/post-worthy work. Adds portrait crop boards, pairwise tournament, and user preference-seed expansion.

## Output

Every run should point the user to:

```text
打开这里_README.md
```

Draft candidate package:

```text
01_模型审片候选/
  精选/
  待定/
  未入选/
  废片/
90_过程文件/
```

Final package after model/editor review:

```text
01_最终结果/
  精选/
  待定/
  未入选/
  废片/
90_过程文件/
```

`未入选` means usable but not selected. `废片/REJECT` is only for clearly unusable photos.

## Install

Copy this folder as `steven-photo-director` into an agent skills directory, for example:

```powershell
C:\Users\<you>\.codex\skills\steven-photo-director
C:\Users\<you>\.agents\skills\steven-photo-director
```

Then ask the agent to use `$steven-photo-director` on a photo folder.

## Dependency Profiles

Show the user the profiles before installing:

```powershell
python scripts\prepare_env.py --explain
```

- `core`: Pillow + numpy, light.
- `plus`: HEIC/RAW/OpenCV/imagehash/scikit-image, medium.
- `full`: plus + MediaPipe + PyIQA, heavy.

Do not choose a heavy profile silently.

## Safety

The workflow is local and non-destructive. It does not upload photos, train on photos, delete originals, or rewrite metadata unless the user explicitly asks.

For public demos, use owned, consented, licensed, or synthetic photos. Avoid private albums, minors, IDs, screens, addresses, and location-sensitive EXIF/GPS.
