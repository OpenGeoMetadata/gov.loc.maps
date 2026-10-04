# Operations

## First harvest

1. Run tests. Dispatch **Harvest LOC maps**, mode `pilot`. The workflow checkpoints
   state and proposes a draft PR on `harvest/pilot` after 200 records are validated.
2. Review that pilot using the release checklist. Correct mapping rules and fixtures
   on `main`, then re-run against the existing cache. Do not merge pilot metadata.
3. Dispatch mode `full`. It uses the independent production state channel. Allow
   auto-continuation for the multi-day initial inventory and fetch. Each job has
   a 700-request / 4,800-second budget; persisted LOC cooldowns are respected first.
4. The completed build opens a production metadata PR and attaches its compressed
   source snapshot to a draft release. Validate ingestion and approve the release
   checklist before merging and publishing the first collection release.
5. Set repository variable `LOC_MAPS_WEEKLY_ENABLED=true`. The weekly Monday
   08:23 UTC workflow opens or updates a review PR; it never pushes metadata to main.

Auto-continuation is capped at 120 jobs per dispatch chain. Failures stop the chain;
budget exhaustion and persisted rate-limit pauses can resume. GitHub Actions
availability, artifact storage, and organization workflow-token policy must allow
these operations. Administrators must enable Actions permission to create PRs.

## Local state and recovery

Use a different state directory and checkout/output root for pilot and production.
Never run local and GitHub harvesters concurrently against the same IP/rate-limit
budget. `--resume` is explicit documentation of the default behavior.

The SQLite database stores inventory membership, both verification passes, summary
hashes, fetch times, cache locations, misses, and errors. Cached item JSON is written
atomically. A crash between writing cache and recording success may cause one
repeat request, never a false successful fetch.

Each workflow stores a checksummed snapshot artifact for 14 days. Download a
completed build's draft-release asset for recovery after artifact expiry:

```sh
gh release download SNAPSHOT_TAG --pattern loc-state.tar.gz --dir dist/recovery
loc-maps restore --archive dist/recovery/loc-state.tar.gz --state .state/recovered
loc-maps run --state .state/recovered --mode update --new-inventory
```

Restore accepts only expected regular files, checks all hashes and SQLite integrity,
and requires an empty destination. Do not hand-copy a live SQLite database. To seed
GitHub from a local snapshot, upload it as an Actions artifact named
`loc-state-production` (or `loc-state-pilot`) before dispatching. Never mix channels.

## Failed requests and mapping exceptions

Review `job.json`, `errors.json`, and `mapping-errors.json` in the saved state.
Transient requests get three attempts with backoff. A failed item does not erase
its previous cache; publication stays blocked until current details are valid.
After correcting the cause, use `fetch --retry-failed` or dispatch with
`retry_failed=true`. Malformed responses and rate limits are not withdrawals.

Non-item search pages appear in `reports/exclusions.json`. New exclusion categories
require review: do not suppress arbitrary failures just to achieve a target count.
At 100,000 results the inventory stops before LOC's deep-paging limit. Before adding
partitioning, prove that the union covers all results, including undated and residual
items; top facets alone cannot establish coverage.

## Corrections and withdrawals

Example `overrides.json` entry:

```json
{
  "loc-maps-94686002": {
    "reason": "Reviewed correction, with source or issue link",
    "fields": {"dct_alternative_sm": ["An alternative title"]}
  }
}
```

Null removes an optional field. Identity, membership, suppression, and modification
time are managed by the pipeline. CI rejects custom Aardvark fields and invalid data.

An item absent once keeps its prior published record. Absence from a second complete
inventory produces a suppressed record plus a `withdrawn.json` entry. No automatic
file deletion occurs. More than 2% new withdrawals blocks staging. A maintainer must
review inventory evidence and explicitly run `transform --allow-large-withdrawal`
to override; the workflow intentionally has no automatic override input.

## Reproducibility and rollback

Transform cached sources without fetching them to test a new mapping version.
Unchanged records keep their existing `gbl_mdModified_dt`; report-only changes do
not trigger workflow commits. Stage manifests detect edits after validation.

Roll back a bad published metadata commit through a reviewed Git revert. Restore
the matching source checkpoint before resuming so withdrawal history remains
consistent. Never reset the checkpoint database merely because a run failed.
