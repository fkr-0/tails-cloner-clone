# Checksum-First Image Selection Spec

Date: 2026-05-03
Project: tails-cloner-clone
Status: Proposed

## Goal
For every image operation where an image is obtained, selected, or downloaded, present checksum data in a prominent, first-class UI position so users can compare trust evidence before writing to a device.

## Scope
Applies to all image flows:
- Running Tails source image (embedded ISO path)
- Local ISO/IMG file selected via file picker
- Remote version selected from catalog (download path once remote mode is implemented)

## UX Principles
- Checksum is always visible near the selected source summary, not hidden in secondary panels.
- Comparison state is explicit and color-coded: `MATCH`, `MISMATCH`, `MISSING`, `UNVERIFIED`.
- Install/Upgrade action requires an explicit user acknowledgement when checksum is `MISMATCH` or `UNVERIFIED`.
- Long hashes are fully accessible (copy button), but abbreviated in compact rows.

## Required Comparison Sources
For every selected image, surface and compare against both:
- Source A: Official checksum published on tails.net homepage/release page.
- Source B: Checksum metadata derived from repository-backed metadata APIs/catalog documents.

## Data Model Additions
Add checksum tracking fields to app state for current source selection:
- `selected_image_checksum_local` (computed SHA256 of selected file/image)
- `selected_image_checksum_tails_homepage` (authoritative reference)
- `selected_image_checksum_repo_metadata` (reference from API/catalog metadata)
- `selected_image_checksum_match_homepage` (`true`/`false`/`unknown`)
- `selected_image_checksum_match_repo` (`true`/`false`/`unknown`)
- `selected_image_checksum_overall_status` (`MATCH`/`MISMATCH`/`MISSING`/`UNVERIFIED`)

## UI Changes
## Source panel additions
- Add a dedicated "Checksum Verification" block directly under source selection details.
- Show rows:
  - `Computed SHA256` (for selected local/downloaded image, or N/A for not-yet-materialized remote selection)
  - `tails.net reference SHA256`
  - `Repository metadata SHA256`
  - `Verification status` (prominent badge)

## Action gating
- If status is `MATCH`, allow normal Install/Upgrade.
- If status is `MISSING` or `UNVERIFIED`, show warning and require checkbox: "I understand checksum could not be verified".
- If status is `MISMATCH`, show blocking warning by default with explicit override path behind a secondary confirmation dialog.

## Remote download flow requirements
When remote download mode is enabled:
1. Fetch selected version metadata.
2. Resolve checksum from tails.net reference endpoint.
3. Resolve checksum from repository metadata endpoint.
4. Download image.
5. Compute local SHA256 incrementally while downloading or immediately after.
6. Populate comparison block before enabling write action.

## Local file selection requirements
When user picks local ISO/IMG:
1. Compute SHA256 immediately (background thread with progress text).
2. Try matching version metadata by filename pattern and/or selected version context.
3. Fetch both reference checksums when version can be inferred or user-selected.
4. Present comparison status and gating state.

## Running Tails source requirements
For running Tails clone source:
1. Identify source ISO path.
2. Compute SHA256 of source ISO.
3. Fetch reference checksums for detected running version.
4. Display comparison and gating state the same way as other modes.

## Error handling
- Network failures: preserve computed checksum and mark remote references as `MISSING` with retry action.
- Parse failures: show which source failed (`tails.net` vs `repo metadata`).
- Conflicting references (A != B): mark `UNVERIFIED` and require explicit override.

## Telemetry / audit (local log)
On clone attempt, write a structured verification snapshot:
- selected version
- computed checksum
- tails.net checksum
- repo metadata checksum
- effective status
- whether override was used

## Acceptance Criteria
1. Any selected or downloaded image has a visible checksum verification block without extra clicks.
2. Users can compare computed checksum against both tails.net and repository metadata values in one place.
3. `MISMATCH` is visually prominent and blocks accidental writes.
4. Test coverage includes checksum-state transitions for success/failure/conflict paths.
