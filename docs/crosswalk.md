# LOC → Aardvark crosswalk (mapping version 2)

The mapper reads the full item response and its nested display metadata.
It accepts scalar, list, and facet-label objects. Empty optional fields are omitted;
unknown fields are never emitted into Aardvark.

| Aardvark field | LOC source / behavior |
| --- | --- |
| `id` | `loc-maps-` plus reversibly encoded canonical item path |
| `dct_title_s` | `title`; missing title blocks the record |
| `dct_alternative_sm` | `other_title` |
| `dct_description_sm` | Description, notes, contributors, and publication statement; HTML normalized to text |
| `dct_creator_sm` | Explicit `creator` / `creators` only |
| `dct_publisher_sm` | Explicit `publisher` / `publishers` only |
| `dct_subject_sm` | Prefer full `subject_headings`, then subjects / subject |
| `dct_language_sm` | Preserve supplied codes; normalize a documented set of common language names to ISO 639-2 bibliographic codes; retain unfamiliar labels |
| `dct_temporal_sm` | Preserve source date / date-issued text, including uncertainty |
| `dct_issued_s` | Valid unambiguous ISO year, month, or day |
| `gbl_indexYear_im`, `gbl_dateRange_drsim` | Valid exact date or explicit `YYYY/YYYY` range; use range start as index year |
| `dct_spatial_sm` | Source location labels; no geocoding |
| `locn_geometry`, `dcat_bbox`, `dcat_centroid` | Explicit coordinate extent only; see below |
| `dct_rights_sm` | Item rights advisory, otherwise source rights statement |
| `dct_accessRights_s` | Boolean `access_restricted` → Restricted/Public; missing or malformed value blocks mapping |
| `gbl_displayNote_sm` | Access advisory |
| `dct_identifier_sm` | Canonical item URL, source aliases and digital identifiers |
| `schema_provider_s` | Library of Congress |
| `gbl_resourceClass_sm` | Maps |
| `gbl_resourceType_sm` | Fire insurance maps for Sanborn or explicit matching genre; Topographic maps for explicit genre |
| `pcdm_memberOf_sm` | `loc-maps` |
| `dct_format_s` | Only when known download formats agree; TIFF is not GeoTIFF |
| `gbl_mdModified_dt` | Time of verified inventory when meaningful published metadata changes; stable otherwise |
| `gbl_mdVersion_s` | Aardvark |
| `gbl_suppressed_b` | True after two complete inventories establish absence; removed on return |

Undifferentiated contributors remain labeled in the description. The publication
statement is retained without attempting to parse a publisher role from prose.
If publication prose records an uncertain year or date range, it is retained in
temporal text and numeric dates are omitted even when LOC's normalized search date
looks exact. A question mark attached only to a place name does not affect dates.
Rights availability and access availability are different: a public landing page
does not grant a content license. No blanket `dct_license_sm` is emitted.

## Geometry

Supported inputs are a single `ENVELOPE(west,east,north,south)`, a labeled extent
object, or a LOC hemisphere/DMS range such as
`(W 77°15ʹ--W 77°00ʹ/N 39°00ʹ--N 38°45ʹ).`. Numeric ranges, axis order, and minute /
second limits are checked. Unsupported forms, multiple extents, point coordinates,
pixel dimensions, zero-area bounds, and antimeridian crossings are omitted and
reported as unparsed. This avoids replacing a crossing extent with a near-global
box. A bounding box alone does not mark an image as georeferenced.

## References

| URI key | Behavior |
| --- | --- |
| `http://schema.org/url` | Canonical LOC item page, always present |
| `http://schema.org/thumbnailUrl` | Source thumbnail URL, stripping dimension fragment |
| `http://iiif.io/api/presentation#manifest` | Explicit `iiif_manifest_url` only |
| `http://iiif.io/api/image` | Explicit `info.json` only for a single-image item without a manifest |
| `http://schema.org/downloadUrl` | Labeled TIFF, JPEG2000, PDF, ZIP URLs actually supplied by LOC; omitted for restricted items |

Multi-sheet resources stay together. Their manifest or LOC landing page provides
full access; a first-sheet image is not presented as the entire atlas. Resource
URLs and original coordinate values are retained in the snapshot provenance.
