# Culling Policy

## Non-Destructive Defaults

- Never delete source files.
- Never move source files unless the user explicitly asks.
- Never overwrite originals.
- Prefer sidecars, reports, and copied selections.
- Keep machine decisions separate from final editorial decisions.

## First-Pass Labels

The scanner writes first-pass labels. They are triage labels, not final decisions.

- `AUTO_PASS`: no strong mechanical issue found.
- `AUTO_REVIEW`: a technical risk needs visual judgment.
- `AUTO_REVIEW_DUPLICATE`: image belongs to a near-duplicate group and was not the script's top technical pick.
- `AUTO_RISK`: severe mechanical issue or decode problem.

Final decisions must be one of:

- `KEEP`
- `REVIEW`
- `REJECT`

## Common Risk Flags

- `decode_failed`: file could not be opened by the available image libraries.
- `raw_needs_preview_or_rawpy`: RAW file had no usable preview and rawpy was unavailable.
- `low_resolution`: file is too small for many uses.
- `severe_blur`: very low edge/detail score.
- `soft_focus_risk`: may be soft, needs visual check.
- `face_eye_review`: OpenCV saw a face but did not find eyes; this is only a weak review signal, not a closed-eye verdict.
- `eye_closed_review`: MediaPipe eye landmarks suggest low eye openness. Review visually before rejecting.
- `eye_asymmetry_review`: MediaPipe eye landmarks suggest a blink, wink, or one-eye issue. Review visually before rejecting.
- `relative_low_sharpness`: among the softest images in this batch.
- `underexposure_risk`: dark image or heavy shadow clipping.
- `overexposure_risk`: bright image or highlight clipping.
- `low_contrast`: flat tonal separation.
- `near_duplicate`: perceptually similar to another image.

For selected people photos, risk flags are not enough. A face can be technically clean but still unflattering, stiff, or mistimed. Treat `requires_face_final_review=true` as a required editorial gate, not as another score penalty.

## Reject Policy

Reject when the image has no clear role and one or more serious issues:

- duplicate with weaker moment, only in strict mode or after visual comparison
- missed focus with no emotional or documentary value
- blink or bad expression in a portrait
- composition blocks the subject
- accidental frame, black frame, or test shot
- exposure failure that cannot be repaired

## Keep Policy

Keep when the image has a clear role and enough technical quality for that role:

- best moment in a sequence
- strongest story or emotion
- clean hero image
- required person, object, or scene
- useful detail or transition image
- unique composition not duplicated elsewhere

## Review Policy

Use REVIEW for:

- client taste calls
- images that need an editing test
- technically flawed but emotionally strong frames
- second-best duplicates that might matter
- uncertain face or expression calls
- people-photo sequence alternates, unless the user asked for strict deduplication
