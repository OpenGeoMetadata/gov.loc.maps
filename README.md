# Library of Congress Maps — OpenGeoMetadata

Python tools and generated [OGM Aardvark](https://opengeometadata.org/ogm-aardvark/)
metadata for the catalog items returned by [LOC Maps](https://www.loc.gov/maps/).
This is an independent OpenGeoMetadata project, **not an official Library of
Congress product**. It follows the publishing conventions of
[gov.usgs.htmc](https://github.com/OpenGeoMetadata/gov.usgs.htmc).

## Publication status

The harvester and pilot are being established. A complete collection release is
not yet available. Pilot records belong on `harvest/pilot` and must not be merged
as a complete collection. The first production release requires the checks in
[the release checklist](docs/release-checklist.md). Weekly updates remain disabled
until that release is verified.

## Coverage

One record represents one LOC catalog item, including an entire multi-sheet map
or atlas. Items without coordinates or downloadable images remain discoverable.
LOC Maps returned 60,185 search results during initial research on 2026-10-04;
that is neither a permanent total nor a count of individual sheets.

Search results also include descriptive web pages without `/item/` identifiers.
These are accounted for in `reports/exclusions.json`, not silently discarded or
turned into fabricated catalog items. Two complete inventory passes must agree
on unique membership and the reported total, including these exclusions.

The project links to LOC resources; it does not mirror map images. No inferred
place extents, image georeferencing, sheet-level records, or index maps are
created. Geography is emitted only for unambiguous source-supplied extents.

## Quick start

Requires Python 3.11+ on Linux or macOS.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
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
artifacts retain compressed snapshots for 14 days. Completed builds also create
a draft GitHub release with a durable source snapshot. Snapshots contain SHA-256
checksums and are validated before restore.

## Updates and safety

- Search pages contain 100 results. A full inventory is verified by a second pass.
- One process per checkpoint directory; workflow jobs also share one concurrency
  group. All requests and retries use a persisted 6.1-second minimum interval.
- LOC currently documents 20 JSON API requests/minute. This project uses less
  than 10/minute. Throttling and HTML challenges pause requests for at least an
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

The first full fetch needs roughly 100 hours at the configured rate for 60,000
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
