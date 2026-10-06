# Operations

## First harvest

1. Run tests. Dispatch **Harvest LOC maps**, mode `pilot`. The workflow checkpoints
   state and proposes a draft PR on `harvest/pilot` after 200 records are validated.
2. Review that pilot using the release checklist. Correct mapping rules and fixtures
   on `main`, then re-run against the existing cache. Do not merge pilot metadata.
   Set `bootstrap_full=true` to start the full fetch automatically after the pilot
   PR is created; this does not bypass any release acceptance checks.
3. Dispatch mode `full`. It uses the independent production state channel. Allow
   auto-continuation for the multi-day initial inventory and fetch. Each job has
   a 700-request / 4,800-second budget; persisted LOC cooldowns are respected first.
4. The completed build opens a production metadata PR and attaches its compressed
   source snapshot to a draft release. Validate ingestion and approve the release
   checklist before merging and publishing the first collection release.
5. Set repository variable `LOC_MAPS_WEEKLY_ENABLED=true`. The weekly Monday
   08:23 UTC workflow opens or updates a review PR; it never pushes metadata to main.

Explicitly enabled auto-continuation runs bounded jobs until completion. Failures stop the chain;
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

## Recovering the merged incomplete preview

The merged 126-item preview can be promoted explicitly without pretending that
its 200-item pilot inventory covers the full collection. Dispatch `harvest.yml`
with `mode=full`, `seed_pilot=true`, and `auto_continue=true`. Enable repository
variable `LOC_MAPS_RECOVERY_ENABLED=true` for backup recovery checks every 20 minutes for safely paused
full-harvest jobs. This recovery workflow is separate from weekly updates.

The bootstrap restores the saved pilot only when no production checkpoint exists.
It requires a complete pilot inventory, the incomplete-preview marker, and an exact
match between each published item and its cached source mapping (apart from the
metadata timestamp). Unknown or altered records stop bootstrap. The conversion
retains source responses, cooldowns, and pilot history, resets withdrawal counters,
and starts a new full inventory requiring two matching passes. Unfetched pilot
items are fetched with the rest of the full inventory; no second pilot publication
is needed. Once production state exists, all subsequent jobs use that state.

Throttle responses double the persisted request interval, capped at 300 seconds.
After 100 successful requests, it decreases by 20%, never below 30 seconds of rest after each response.
A paused harvest queues its own continuation. Each job waits up to one hour for
the saved cooldown without contacting LOC, then either harvests or checkpoints
and queues another wait. The 160-minute job timeout accommodates a one-hour wait
plus the 80-minute fetch budget and setup. There is no arbitrary total-job cutoff.
Completion requires no further dispatch. Failed or cancelled runs produce
a failed recovery check requiring inspection; they are not blindly restarted.
Disable `LOC_MAPS_RECOVERY_ENABLED` to stop scheduled recovery.

The first successful checkpoint each UTC day is also saved in a draft release
named `checkpoint-production-YYYY-MM-DD` (or `checkpoint-pilot-YYYY-MM-DD`). These
are incomplete recovery snapshots, not collection releases, and survive Actions
artifact expiry. More recent per-batch artifacts remain the preferred recovery
source. If all artifacts expire, restoration falls back to a durable snapshot;
it may repeat work since that snapshot. A failed bootstrap is never uploaded as
production state. `status.json` distinguishes inventory completion from overall
job completion and includes cache count, error count, pacing, and cooldown.

LOC also returns Geography and Map Division archival collection finding aids,
such as the Heezen-Tharp collection (`hdl.loc.gov/loc.gmd/eadgmd.gm017012`). These
have no catalog `/item/` identifier and are explicitly counted in exclusions as
`non-item-finding-aid`. HTTP, HTTPS, and protocol-relative variants normalize to
one HTTPS URL. This exception is restricted to that reviewed finding-aid path;
other unknown hosts and handle types are retained for identifier review before publication.

## Legacy catalog records and unresolved identifiers

Legacy `catalog.loc.gov/vwebv/holdingsInfo?bibId=…` and
`catalog.loc.gov/cgi-bin/Pwebrecon.cgi?BBID=…` links represent real map records.
They normalize to one HTTPS holdings URL. When no `/item/` alias is supplied,
use a stable `loc-maps-bibid:<number>` ID. The literal colon separates this
namespace from percent-encoded `/item/` identifiers. Existing item IDs do not change.

LOC's search API embeds catalog metadata for these entries. After the verified
inventory, the fetch stage caches that complete search-result object and its
embedded `item` and `resources` data, without requesting the catalog website as
JSON or inventing a `/item/<bibId>/` URL. Provenance labels this source
`loc-search-embedded-catalog`, and coverage reports count it separately from
item-API responses. It is not claimed to provide fields absent from the source.

Unknown identifiers, or catalog results without embedded metadata, are retained
in `identifier-review.json` with their source metadata. They count toward inventory
reconciliation but are not silently excluded or published. Supported records can
continue through inventory and fetching; unresolved entries block transformation
and publication, including publishing a previously staged build. Status reports
show the unresolved count. Resolve these through source-backed identifier rules,
then repeat inventory before publication. Reviewed non-item pages and finding aids
remain separate, explicit exclusions.

Three failed attempts on temporary network errors or JSON HTTP 5xx responses now
persist a cooldown and pause for automatic recovery. HTTP 404, invalid metadata,
and publication validation failures still require inspection. Rate-limit and HTML
challenge responses retain their longer cooldown behavior.


## Conservative request policy

The default for new and resumed checkpoints is at most two requests per minute:
one sequential request, followed by at least 30 seconds of rest after its response.
Retries share this same limiter. An older checkpoint cannot restore faster pacing;
slower pacing and all existing cooldowns are preserved across jobs.

HTTP 503, 429, 403, and HTML challenges pause immediately, without rapid retries.
Consecutive overloads use 1, 2, 4, 8, 16, then 24-hour cooldowns, always honoring
longer Retry-After values. After 100 successful responses the overload streak resets
and pacing can recover gradually, never below the 30-second floor. The identifying
User-Agent, 100-result inventory pages, cached records, and single-workflow
concurrency remain in place. No local companion harvester runs alongside GitHub.

At this pace 60,000 individual item requests alone need at least 500 hours (about
three weeks), plus inventory requests, response time, and cooldowns. Completion
estimates should reflect actual checkpoint progress rather than treating slow but
healthy harvesting as failure.


## Continuation and fallback recovery

Cooldown resumption does not depend solely on GitHub cron. The successful paused
job saves its checkpoint before dispatching its next job, and that job enforces
the saved deadline before making any LOC requests. This uses runner time while
waiting, trading efficiency for a direct continuation path. Set `auto_continue=false`
on a manually resumed job to stop the chain after that job; do not clear cooldowns.

The backup recovery workflow checks at minutes 7, 27, and 47. Its own concurrency
group prevents it from displacing a pending harvest. It checks for active harvests
before dispatching and respects cooldowns. GitHub schedules and runner availability
are external dependencies; the observed October 5 failure was a hosted runner
allocation failure before any recovery step executed. A missing runner can still
require manual recovery. Use the recovery workflow's optional `max_requests=3`
input for a short verification batch; subsequent harvest continuations use the
normal budget. A scheduled check never overrides a failed or cancelled harvest.
