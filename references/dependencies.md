# Dependency Guide

Default behavior: inspect the photo folder first, explain the dependency profiles to the user, and ask before installing anything beyond already-available libraries.

## Install Profiles

Core profile, recommended for normal JPG/PNG/WebP/TIFF folders:

```bash
python scripts/prepare_env.py --profile core
```

Complexity: light.

Installs:

- Pillow
- numpy

Plus profile, recommended when the project includes HEIC/HEIF, RAW files, or the user wants stronger OpenCV metrics:

```bash
python scripts/prepare_env.py --profile plus
```

Complexity: medium.

Installs:

- Pillow
- numpy
- pillow-heif
- rawpy
- opencv-python-headless
- imagehash
- scikit-image

Full profile, only after explicit user approval:

```bash
python scripts/prepare_env.py --profile full
```

Complexity: heavy.

Installs everything in plus, then adds:

- mediapipe
- pyiqa

Full may take a long time, pull heavy machine-learning dependencies, or fail on some machines. Do not choose it silently.

## What To Ask The User

After inspecting the folder, say something like:

```text
I found 217 JPG files and no RAW/HEIC. Recommended profile: core.
core installs Pillow and numpy only, light complexity, enough for blur/exposure/duplicate/contact-sheet screening.
plus adds RAW/HEIC/OpenCV support, medium complexity.
full adds face-landmark and learned quality experiments, heavy complexity.
Which profile should I use?
```

If the user already has the required core libraries, skip installation and continue.

After installation, read the `pip_check` field printed by `prepare_env.py`. If it reports broken requirements, tell the user plainly and either fix the package mismatch or continue with a lower dependency profile. Do not report "full installed cleanly" while `pip check` is failing.

## What Each Library Does

Pillow

- Opens common image formats.
- Reads basic EXIF.
- Creates thumbnails and contact sheets.
- Required baseline.

numpy

- Makes blur, exposure, contrast, and color calculations faster and steadier.
- Required baseline for good speed.

pillow-heif

- Adds HEIC/HEIF support, common for iPhone photos.
- Without it, HEIC files may be listed as decode failures.

rawpy

- Opens many RAW camera files.
- Without it, the workflow should use same-stem JPEG previews or ask the user to export previews.

opencv-python-headless

- Improves blur and sharpness metrics.
- Adds basic face/eye detection options without a GUI dependency.
- "Headless" means it does not install desktop display components.

imagehash

- Provides standard perceptual hash helpers.
- The bundled scanner has its own dHash fallback, so this is helpful but not mandatory.

scikit-image

- Adds image quality and structure metrics useful for future refinements.
- Not required for the first-pass workflow.

mediapipe

- Adds stronger face landmark and eye-openness signals.
- Useful for portraits, events, weddings, and family photos.
- Enables optional EAR-style eye review: `eye_closed_review` and `eye_asymmetry_review`.
- If unavailable, the agent should avoid making confident closed-eye claims.

pyiqa

- Adds learned/no-reference image quality assessment metrics.
- Useful as a reference score, not as the final judge of beauty.
- It can be heavy because it may pull ML dependencies.

torch and torchvision

- Needed by many learned quality/aesthetic models.
- Heavy; install may fail or take time. This is optional.

ExifTool

- External command-line tool for robust metadata, ratings, labels, and XMP workflows.
- The bundled exporter can write simple XMP sidecars without it, but ExifTool is better for professional metadata round-trips.

## Practical Meaning

If full installs, the Skill can combine:

- file and EXIF inventory
- blur and exposure checks
- HEIC and RAW coverage
- duplicate grouping
- face and eye risk cues
- contact sheets
- aesthetic rubric review
- candidate and final selection lists/sidecars after model/editor review

If only core is available, the Skill still does:

- common image decoding
- blur/exposure/contrast risk
- duplicate grouping
- contact sheets
- final package export from a model/editor-reviewed selection JSON
- multi-method ensemble candidate organization using lightweight scores
- scene/visual grouping for model/user-guided selection

The final aesthetic and scene decision always belongs to the agent/editor using visual review and the rubric, not to a numeric model score.
