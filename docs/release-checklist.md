# Initial release acceptance

This is an evidence log, not an assertion that acceptance has already passed.

- [ ] Pilot contains 200 catalog items; query coverage and documented exclusions reviewed.
- [ ] At least 30 varied records reviewed against their LOC pages. Record IDs, reviewer,
  date, and findings in a release PR attachment or `reports/manual-review.json`.
- [ ] Include single maps, Sanborn atlases, international maps, uncertain dates,
  records without bounds, and access-restricted/non-digitized items when returned.
- [ ] Verify thumbnail display, IIIF/single-image viewing, all-sheet access, download
  links, and rights wording. A metadata URL check alone does not verify rendering.
- [ ] Full inventory has two agreeing passes and reconciles published current items
  plus non-item exclusions to its recorded LOC total.
- [ ] Zero unreviewed fetch or mapping failures; all output passes pinned schema and
  semantic validation. Geometry gaps are reported, not silently estimated.
- [ ] Ingest pilot into a nonproduction OGM API and GeoBlacklight viewer; verify
  search, facets, collection links, bounds/no-bounds cases, and suppression/restoration.
- [ ] Rebuilding the same source snapshot produces byte-identical metadata.
- [ ] Production PR reviewed and merged; draft release includes inventory date,
  mapping version, coverage report, and a restorable source snapshot.
- [ ] Repository maintainers assigned; Actions can create review PRs.
- [ ] Enable `LOC_MAPS_WEEKLY_ENABLED=true` only after release acceptance.
