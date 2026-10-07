# Bulk and discovery metadata import

The official [LC Labs Sanborn package](https://data.labs.loc.gov/sanborn/)
provides 50,600 atlas metadata records in one 82.4 MB JSONL download. The October 7,
2026 download took approximately 19 seconds. This is acquisition timing, not a
full-collection completion measurement. LOC documents a January 2024 compilation
from its Sanborn collection API, including digitized and non-digitized map items.

## Membership and source freshness

All 50,600 source rows explicitly contain `Sanborn Maps` in `Part_of` and `Map` in
`Original_format`. We accept this official export as historical collection
membership evidence. Requiring every identifier to appear in our incomplete live
discovery would unnecessarily exclude 37,711 records. Full-package mode pins the
reviewed source SHA-256 and rejects unreviewed replacements before writing.

This does not establish that all historical items remain in today's `/maps/`
endpoint. Newer discovery metadata enriches 12,889 matching package records.
Other discovered map items are also publishable from their search metadata,
including embedded catalog fields and explicit access flags. Source notes identify
these records as pending full-item enrichment. Complete inventory reconciliation
is still necessary. No withdrawals or complete-release claims are made.

## Crosswalk and access

Newer discovery fields and explicit access restrictions take precedence. Bulk
Title, Date, Language, Location_text, Notes, Subject_headings, Preview_url and
IIIF_manifest fill missing fields. Pipe-delimited package subjects become separate
values. The normal mapper preserves uncertain dates and avoids inferred roles.

For package-only records, Public describes access to the public catalog record.
An explicit display note explains that current item-level restrictions have not
been refreshed and directs users to LOC. The documented Sanborn collection rights
statement is attributed only for digitized content without item-specific rights.
It is not generalized to physical holdings or other LOC collections.

The package marks 35,119 records digitized and 15,481 not digitized, yet supplies
manifest-shaped links for all of them. The importer uses newer digitization flags
where available and omits package viewer/image links when digitization is not
confirmed. Non-digitized catalog items remain discoverable, with physical access
referred to LOC. Multi-sheet items retain whole-atlas manifest access where supplied.

Enriched `Location.Coordinates` are geocoded city points, not map footprints, and
are excluded. Explicit map extents in discovery metadata may pass the ordinary
geometry validator. Missing bounds and rights remain reported gaps.

## Reproduce

The exact `metadata.jsonl`, source README, and `loc-state.tar.gz` checkpoint are
preserved on draft release `source-sanborn-20261007` (visible to maintainers).
The source is also available from
`https://loc-sanborn-maps.s3.amazonaws.com/metadata.jsonl`.
Restore the checkpoint with the normal `loc-maps restore` command. It came from
[workflow run 37618971993](https://github.com/OpenGeoMetadata/gov.loc.maps/actions/runs/37618971993).

```sh
python -m loc_maps.bulk --package /path/to/metadata.jsonl \
  --checkpoint /path/to/restored/state.sqlite --root . \
  --all-package-records --include-discovered-items
loc-maps validate --root .
```

Without `--all-package-records`, package imports remain limited to discovery
matches. Without `--include-discovered-items`, non-package discoveries are omitted.
The complete candidate batch validates before writing. Existing richer records
are retained; only records explicitly marked as this bulk import's output are
updated by later bulk mapping revisions. Unchanged bytes and modification times
are preserved. Source/checkpoint files remain outside the metadata tree and Git.
The importer never changes live harvest state or marks API details fetched.

## Validation and remaining work

See `reports/sanborn-bulk.json` for actual counts and source hashes. Automated
schema validation checks reference structure, not every live viewer URL.
Viewer acceptance, full-item enrichment, historical-membership reconciliation,
and acquisition of any remaining maps are pending. The count compared with the
original 60,185 search results is not a verified completion percentage: that
baseline includes non-item entries and membership can change.
