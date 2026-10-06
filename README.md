# Library of Congress Maps — incomplete pilot preview

**This branch contains 126 validated Aardvark item records plus one collection record.**
Browse [metadata-aardvark/](metadata-aardvark/). Only 126 of the 200 selected pilot
items have been fetched; this is not the complete LOC collection. Do not merge this
preview as the production release. The production harvester continues independently.

# Project documentation

Python tools and generated [OGM Aardvark](https://opengeometadata.org/ogm-aardvark/)
metadata for the catalog items returned by [LOC Maps](https://www.loc.gov/maps/).
This is an independent OpenGeoMetadata project, **not an official Library of
Congress product**. It follows the publishing conventions of
[gov.usgs.htmc](https://github.com/OpenGeoMetadata/gov.usgs.htmc).

## Publication status

**The default branch contains an incomplete preview: 126 item records plus one
collection record.** Browse [metadata-aardvark/](metadata-aardvark/). The full
collection harvest is not yet complete; the collection record and preview report
identify this limitation.

The recovery workflow can seed a full harvest from the verified pilot cache,
retaining all downloaded sources while requiring a new, complete inventory.
See [recovery operations](docs/operations.md#recovering-the-merged-incomplete-preview).
The first production release still requires [the release checklist](docs/release-checklist.md).
Weekly updates remain disabled until that release is verified.

## Coverage

One record represents one LOC catalog item, including an entire multi-sheet map
or atlas. Items without coordinates or downloadable images remain discoverable.
LOC Maps returned 60,185 search results during initial research on 2026-10-04;
that is neither a permanent total nor a count of individual sheets.

Legacy catalog-only maps are retained as `loc-maps-bibid:<number>` records using
LOC’s embedded catalog metadata. Unrecognized identifiers are retained in a review
queue; they do not stop discovery and fetching, but do block publication.

Search results also include descriptive web pages without `/item/` identifiers.
These are accounted for in `reports/exclusions.json`, not silently discarded or
turned into fabricated catalog items. Two complete inventory passes must agree
on unique membership and the reported total, including these exclusions.

The project links to LOC resources; it does not mirror map images. No inferred
place extents, image georeferencing, sheet-level records, or index maps are
created. Geography is emitted only for unambiguous source-supplied extents.

## Quick start

Requires Python 3.11+ and uv on Linux or macOS. CI and the commands below use the
checked-in dependency lockfile.

```sh
uv sync --frozen --extra test
. .venv/bin/activate
pytest -q

# First build a 200-item pilot, retaining checkpoints if interrupted.
loc-maps run --mode pilot --state .state/pilot --dry-run
loc-maps validate --root .staging
loc-maps publish --state .state/pilot --dry-run

# Production uses a separate checkout/output root and state directory.
loc-maps run --mode full --state .state/production --max-requests 700 --resume
```

`run` sequences inventory, fetch, transform, validation, and local publication.
Exit **75** means the job paused safely for a request/time budget or LOC cooldown;
repeat the same command to resume. Exit **1** requires inspecting the error report.
`--dry-run` prevents changes to published files, but still downloads to the cache
and builds reviewable staging output. There is no Git push in the CLI.

Separate commands are available for every stage:

```sh
loc-maps inventory --mode full --state .state/production
loc-maps fetch --state .state/production
loc-maps transform --state .state/production
loc-maps validate --root .staging
loc-maps publish --state .state/production --dry-run
loc-maps publish --state .state/production
loc-maps status --state .state/production
loc-maps snapshot --state .state/production --archive dist/source.tar.gz
loc-maps restore --state .state/recovered --archive dist/source.tar.gz
```

## Layout and identifiers

```text
metadata-aardvark/
  loc-maps.json                 collection record
  <three hash characters>/
    loc-maps-<identifier>.json   one catalog item
loc_maps/                       harvester, mapper, validator, pinned schema
tests/fixtures/                 small source fixtures; not a bulk cache
reports/latest.json             inventory, changes, coverage, review status
reports/exclusions.json         non-item search results
withdrawn.json                  suppressed records and withdrawal dates
overrides.json                  reviewed corrections with reasons
```

Record IDs preserve numeric and Sanborn identifiers. Characters outside the URL
unreserved set are percent-encoded, including a literal percent sign; unquoting
the ID suffix recovers the exact LOC identifier path. The first three characters
of the record ID's SHA-256 hash determine its directory. No `layers.json` is needed.

Large source responses, the SQLite checkpoint database, source hashes, retrieval
times, and mapping provenance live under the ignored state directory. Workflow
artifacts retain compressed snapshots for 14 days. Daily recovery checkpoints and completed builds also create
draft GitHub releases with durable source snapshots. Snapshots contain SHA-256
checksums and are validated before restore.

## Updates and safety

- Search pages contain 100 results. A full inventory is verified by a second pass.
- One process per checkpoint directory; workflow jobs also share one concurrency
  group. All requests and retries use a persisted 30-second minimum rest after each response,
  increasing after throttling and recovering gradually after successful requests.
- LOC currently documents 20 JSON API requests/minute. This project makes at most
  two requests/minute, with one request at a time. Throttling and HTML challenges pause requests for at least an
  hour, honoring longer `Retry-After` values. No proxy or challenge bypass is used.
- New and changed summaries trigger detail fetches. Cached items are refreshed
  after 90 days even when their summaries have not changed.
- An unchanged record retains exactly the same bytes and modification timestamp.
- Missing items are suppressed only after two verified inventories. Withdrawals
  exceeding 2% of active published items require an explicit reviewed override.
- Incomplete inventories, fetch failures, invalid metadata, missing state history,
  and modified staging files block publication.
- A query reaching 100,000 results stops safely. Automated date partitioning is
  not implemented: a tested partition strategy including undated/residual items
  is required if LOC grows past its deep-paging limit or pagination becomes unreliable.

The first full fetch needs at least 500 hours (about three weeks) at the configured rate for 60,000
items, plus inventory traversal, network delays, and retries. GitHub Actions runs
bounded batches and can dispatch continuations; it does not require one multi-day
job. See [operations](docs/operations.md).

## Metadata, corrections, and rights

See the [field crosswalk](docs/crosswalk.md). Generated files should not be edited
by hand. Fix the mapper or add a reviewed `overrides.json` entry with a reason and
field changes; null removes an optional field. Lifecycle and identity fields
cannot be overridden.

Original code is MIT licensed. Project-authored metadata contributions are
dedicated under CC0 1.0. LOC source metadata and map content retain their applicable
rights; nothing here declares every map public domain. Individual rights and
access statements are preserved. See [LICENSE](LICENSE),
[LICENSE-METADATA](LICENSE-METADATA), and [NOTICE](NOTICE).

## Sources

- [LOC JSON API](https://www.loc.gov/apis/json-and-yaml/)
- [LOC API limits](https://www.loc.gov/apis/json-and-yaml/working-within-limits/)
- [OGM reference URIs](https://opengeometadata.org/reference-uris/)
- [GeoBlacklight schema at the pinned revision](https://github.com/geoblacklight/geoblacklight/blob/691aed7c7762b498afdcc95147717d3ba1f8ebf0/schema/geoblacklight-schema-aardvark.json)

### Incremental production acquisition

Production `run` now keeps a durable union of discovered sources and alternates ten
item-detail attempts with one search page. Page results and the next-page cursor
commit together. Changing totals or membership never clear the queue. Existing
inventory checkpoints migrate automatically; `import_discovery_run` can recover
saved discoveries from an older production Actions checkpoint without replacing
current caches, lifecycle history, request pacing, or cooldowns.

The `status` output's `discovery` field reports the acquisition cursor, observed
source totals, unique discoveries (including excluded/review sources), unresolved
identifiers, and pending item details. Its cursor supersedes the legacy `run`
inventory cursor. A second traversal adds discoveries missed as search pages move.
Neither traversal proves complete coverage of a changing collection. After both
traversals and all eligible item attempts, acquisition stops with
`review_required`; per-item failures remain recorded for inspection. It does not
publish, infer absences, or withdraw records. Publishing incremental acquisitions
requires a separate reviewed coverage audit; the current strict publication gate
intentionally rejects this state. The pilot workflow is unchanged.

All search and item requests share the existing sequential client: at least 30
seconds of rest after a response, persisted adaptive pacing, and immediate
persisted cooldowns on blocking/overload responses. Recovery imports make no LOC
requests. Ordinary continuations restore the newest checkpoint automatically.
