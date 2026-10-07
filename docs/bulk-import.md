# Sanborn bulk import

The official [LC Labs Sanborn package](https://data.labs.loc.gov/sanborn/)
provides 50,600 atlas metadata records in one 82.4 MB JSONL download. Our
October 7, 2026 download took approximately 19 seconds. This measures download
performance, not end-to-end collection completion. The package describes a
January 2024 source compilation, so it is a historical baseline.

The first import adds 12,888 items. One matching record already existed and was
preserved. Combined with the pilot, this branch contains 13,014 map items and
one collection record. Every record passes the pinned schema and semantic checks.

## Scope and crosswalk

Only exact canonical identifiers present in the existing maps discovery checkpoint
are eligible. Of 50,600 source records, 12,889 match; 37,711 await scope verification.
Discovery membership is evidence of inclusion, not evidence that enumeration is complete.

Newer discovery fields and access restrictions take precedence. Bulk Title, Date,
Language, Location_text, Notes, Subject_headings, Preview_url and IIIF_manifest
fill missing fields. Pipe-delimited package subject headings are split into values.
The normal mapper preserves uncertain dates. No contributor roles are inferred.
Number_of_files identifies multi-sheet items; the explicit package IIIF manifest
provides whole-atlas access. This importer does not download any images.

The package's enriched Location.Coordinates are city points from geocoding. They
are never used for geometry. Missing footprints remain missing. Sanborn-specific
collection rights attribution is used only when individual rights are absent;
individual rights and explicit discovery access restrictions take precedence.
This collection statement does not apply to other LOC maps.

Each imported record displays its historical package provenance. Existing records
are never overwritten, suppressed, or deleted. The importer neither marks full
API details fetched nor changes inventory/checkpoint state. Subsequent API
processing can enrich these records through the existing review process.

## Reproduce

Download the official `https://loc-sanborn-maps.s3.amazonaws.com/metadata.jsonl`.
Verify its SHA-256 against `reports/sanborn-bulk.json`. Restore the versioned
`loc-state-production` artifact from [run 37618971993](https://github.com/OpenGeoMetadata/gov.loc.maps/actions/runs/37618971993)
with the normal `loc-maps restore` command. The daily recovery snapshot provides
an additional copy if workflow artifacts expire. Use the checkpoint from that run
for identical scope; later checkpoints can include more records.

From this checkout, with dependencies installed:

```sh
python -m loc_maps.bulk --package /path/to/metadata.jsonl \
  --checkpoint /path/to/restored/state.sqlite --root .
loc-maps validate --root .
```

The importer validates the complete candidate batch before writing. Rebuilding
preserves existing bytes and modification timestamps. Its JSON stdout reports
additions for that invocation; the committed report records the original import.
Source responses and checkpoints stay outside the metadata tree and Git.

## Remaining work

Reconcile the remaining Sanborn identifiers against the endpoint inventory;
acquire the non-Sanborn records through an appropriate bulk or API source; and
perform viewer acceptance testing. This batch does not claim full coverage or a
completed initial release. Aardvark validation verifies reference structure, not
live availability of every manifest or thumbnail. No withdrawals are authorized
by this partial inventory.
