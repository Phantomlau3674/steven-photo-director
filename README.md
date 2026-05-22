# Steven Photo Director

Reusable agent skill for non-destructive photo culling, scene grouping, duplicate review, and face-final selection.

## What It Does

- Inspects large local photo folders before installing dependencies.
- Asks the user for dependency profile, grouping mode, culling strength, final count, and selection brief.
- Runs script-based first pass for decode errors, blur, exposure, low resolution, and near duplicates.
- Supports scene grouping so users can choose whole scenes before selecting final photos.
- Produces practical folders: `精选/`, `待定/`, `废片/`.
- Adds a face final review gate for people/portrait/social-post selections.
- Keeps originals untouched by default.

## Install

Copy this folder as `steven-photo-director` into an agent skills directory, for example:

```powershell
C:\Users\<you>\.codex\skills\steven-photo-director
C:\Users\<you>\.agents\skills\steven-photo-director
```

Then ask the agent to use `$steven-photo-director` on a photo folder.

## Dependency Profiles

The skill intentionally starts light and asks before installing heavier packages:

- `core`: Pillow + numpy.
- `plus`: HEIC/RAW/OpenCV/imagehash/scikit-image support.
- `full`: plus + MediaPipe + PyIQA for face-landmark and learned-quality experiments.

Run dependency preparation from the skill folder:

```powershell
python scripts\prepare_env.py --profile core
python scripts\prepare_env.py --profile plus
python scripts\prepare_env.py --profile full
```

## Safety

The workflow is local and non-destructive. It does not upload photos, train on photos, delete originals, or rewrite metadata unless a user explicitly asks for that.

For public demos, use owned, consented, licensed, or synthetic photos. Avoid private albums, minors, IDs, screens, addresses, and location-sensitive EXIF/GPS.

## License

No open-source license has been selected yet. Public visibility does not grant redistribution or commercial-use rights by itself.
