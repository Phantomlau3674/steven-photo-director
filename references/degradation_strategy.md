# Degradation Strategy

The Skill must remain usable by a general-purpose agent on an ordinary machine. Prefer graceful partial output over failure.

## Dependency Levels

Level 0: file inventory only

- No Pillow or image decoder available.
- Output a file manifest with paths, sizes, extensions, and a clear blocker.
- Ask the user to install Pillow or provide JPEG previews.

Level 1: portable default

- Pillow available.
- Run decode checks, EXIF extraction, exposure risk, low resolution checks, perceptual hashes, duplicate grouping, and contact sheets.
- This is the expected baseline.

Level 2: numeric enhancement

- Pillow plus numpy available.
- Use faster and more stable sharpness, contrast, and colorfulness metrics.
- This is used automatically when numpy is installed.

Level 3: vision enhancement

- Optional OpenCV, MediaPipe, PyIQA, LAION aesthetic predictor, or ExifTool available.
- Add sharper blur metrics, face/eye risk checks, learned quality scores, and XMP metadata only when useful.

## File Format Fallbacks

JPEG, PNG, TIFF, BMP, and WebP usually work through Pillow.

HEIC/HEIF may need `pillow-heif`. If decode fails, keep the item in the manifest with `decode_failed` and ask for JPEG previews or install HEIF support.

RAW formats may not decode through Pillow. If the folder contains RAW files:

- First look for same-stem JPEG previews in the folder.
- If previews exist, screen the previews and keep RAW paths in notes.
- If no previews exist, ask the user to export small JPEG previews from Lightroom, Capture One, camera software, or FastRawViewer.

## Large Folder Fallbacks

For very large folders:

- Keep contact sheets capped with `--max-contact-groups`.
- Use duplicate grouping first; it gives the largest time savings.
- If full visual review is too large, review only duplicate picks and risk sheets first.
- Export a draft selection and ask for a second pass by category, time range, or rating target.

## Aesthetic Fallbacks

If the agent cannot visually inspect images:

- Do not claim final aesthetic judgment.
- Produce a mechanical cull only.
- Mark candidates as `REVIEW`, not `KEEP`, except when the user explicitly requests automatic selection.

If visual inspection is available but context is limited:

- Review contact sheets in batches.
- Keep notes short and decision-oriented.
- Prefer fewer high-confidence KEEP items over broad uncertain selection.

## Failure Handling

The agent should still produce:

- `first_pass_report.md`
- `manifest.csv`
- `manifest.json`
- a clear list of what could not be evaluated
- the next command or user action needed to improve coverage
