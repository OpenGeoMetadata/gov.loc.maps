# Implementation verification — 2026-10-04

Completed checks:

- 64 offline tests pass in the locked Python environment, covering mapping,
  identities, dates, geometry, throttling, resumability, withdrawals, restoration,
  snapshot integrity, and workflow continuation/recovery.
- GitHub's Validate workflow has passed for the published implementation.
- A clean dependency installation from `uv.lock` succeeded.
- 59 real LOC responses were cached before transferring the pilot to GitHub
  Actions. All 59 mapped and passed the pinned schema and semantic validation.
- The existing `ogm-api` repository's actual Aardvark reader loaded those 59
  records plus their collection record and preserved every ID and nested path.
  This was a reader compatibility check, not database or search-index ingestion.
- A 30-record source-metadata spot check covered title, dates, places, rights
  presence, access classification, and reference availability. It exposed
  normalized search dates that concealed catalog uncertainty and date ranges;
  mapping version 2 includes the resulting regression fixes.
- The pilot checkpoint was restored successfully by GitHub Actions. Its remaining
  fetches continue there; the initial full harvest is configured to follow its PR.

Release acceptance still outstanding:

- Complete the 200-item pilot, then inspect its full coverage report and rendered
  thumbnail, image, multi-sheet, and download behavior.
- Ingest the pilot into nonproduction OGM API and GeoBlacklight instances. Neither
  service was running locally during these checks; no production service was used.
- Complete and reconcile the full collection harvest, review the production PR,
  publish the first release, and enable weekly updates.

These outstanding checks are deliberately not marked complete in the generated
report or release checklist. Draft snapshots and pilot branches are not a complete
collection release.
