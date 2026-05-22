# Tool Landscape

This is background for enhancement decisions. Do not make the portable workflow depend on commercial tools or heavy optional libraries.

## Mature Product Patterns

Commercial culling tools converge on the same pattern:

- first-pass technical triage
- duplicate or similar-photo grouping
- face, eye, and expression risk checks for people photos
- a fast comparison surface
- final human/editorial choice

Useful references:

- Aftershoot: automated culling categories include selected, highlights, duplicates, blurry images, and closed eyes.
- Narrative Select: focus and close-up assessment for fast comparison.
- FilterPixel: AI culling around duplicates, blur, closed eyes, and selections.
- Lightroom Assisted Culling: focus, eyes, exposure, and similar-photo workflows.
- Photo Mechanic and FastRawViewer: fast ingest, compare, ratings, and non-destructive professional culling.

## Open-Source Building Blocks

Portable baseline:

- Pillow: decoding, EXIF, thumbnails, contact sheets.
- numpy: fast numeric metrics.
- perceptual hashes: duplicate and near-duplicate grouping.

Optional enhancements:

- OpenCV: Laplacian blur, face and object utilities.
- MediaPipe Face Landmarker: face landmarks, expressions, eye and head cues.
- PyIQA: no-reference image quality metrics.
- LAION aesthetic predictor: CLIP-based aesthetic reference score.
- ExifTool: robust metadata, ratings, labels, and XMP sidecars.

## Skill Design Implication

The right agent workflow is not "AI deletes bad photos." It is:

1. Scripts remove mechanical friction.
2. Similar frames are compared as groups.
3. Aesthetic judgment follows an explicit rubric.
4. Outputs are reversible and auditable.
