# Safety And Rights

This is practical risk guidance, not legal advice.

## Low-Risk Defaults

The Skill is designed to be low risk:

- It runs locally.
- It does not upload photos.
- It does not train a model on the user's photos.
- It does not delete or overwrite originals by default.
- It writes copied files, hardlinks, reports, JSON, CSV, and sidecars only.

## Photo Rights

The user should process photos they own, licensed photos, or photos they have permission to handle.

If the photos were taken by the user, the user is usually the initial copyright owner. If the photos were taken by an employee, contractor, studio, wedding photographer, or another person, ownership can depend on the agreement and local law.

Do not publish, sell, or redistribute someone else's photos just because the files are present on disk.

## Public Demo / Douyin Release

When the user wants to post a demo, tutorial, or growth content publicly:

- Use photos taken by the user, generated demo photos, public-domain/clearly licensed photos, or photos where the photographer and subjects have agreed to public use.
- Do not show private client albums, wedding albums, family albums, children, IDs, addresses, screens, chats, invoices, room numbers, or location-sensitive EXIF/GPS information.
- Prefer resized screenshots, blurred faces, or synthetic/demo albums when teaching the workflow.
- Avoid publishing full-resolution originals in the demo package.
- Do not claim the Skill "understands beauty perfectly" or "automatically deletes bad photos." Safer positioning: it groups, triages, cross-checks, and helps a human/agent make selections.
- If showing before/after folders, make clear that originals are not deleted and outputs are copies or hardlinks.
- If distributing the Skill package publicly, include the Skill files and scripts only; do not bundle third-party dependency wheels or user photos unless their licenses and permissions are clear.
- If a commercial version is planned, do a dependency license review and add a clear license for the Skill itself.

## Privacy And Faces

Face/person photos can be sensitive even when copyright is not an issue.

Keep outputs local unless the user explicitly asks to share them. For public posting, remind the user to consider consent, minors, private locations, IDs, screens, addresses, and other personal information visible in the frame.

For Douyin-style demos, subject consent matters even when the uploader owns the copyright. A person may be okay with private culling but not okay with their face, pregnancy, child, home, or travel location being used as public marketing material.

## Open-Source Dependencies

The Skill package itself contains scripts and requirement files, not third-party source code or binary packages. If a user installs dependencies with `pip`, those packages keep their own licenses.

For personal use and internal local processing, this is usually low risk. If the Skill becomes a commercial product or is redistributed with bundled dependencies, check dependency licenses and transitive dependencies first.

Core dependencies are intentionally minimal:

- Pillow
- numpy

Optional profiles can add heavier packages such as OpenCV, rawpy, MediaPipe, or PyIQA. Ask before installing optional packages.

For public sharing, keep the dependency story simple:

- Core demo: Pillow + numpy.
- Mention optional packages as optional, not required.
- Do not promise that optional ML libraries will install everywhere.
- If packaging a public release, avoid shipping downloaded model weights unless their licenses allow redistribution.

## Operational Risks

Use hardlinks or copies, never move/delete originals unless explicitly requested.

When using `--file-mode hardlink`, files appear in output folders without duplicating disk data on the same volume. Deleting a hardlink in the output folder should not delete the original path, but avoid destructive cleanup commands unless the target path is verified.
