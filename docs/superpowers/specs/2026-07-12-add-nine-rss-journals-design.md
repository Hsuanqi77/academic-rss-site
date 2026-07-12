# Add Nine Research RSS Journals — Design

## Goal

Expand Paper Radar from 20 to 29 enabled journal sources with nine user-approved publications. Every valid RSS item is stored first and then classified by the existing 56 precise tags. ACS Applied Materials & Interfaces is explicitly deferred because its official feed currently returns a Cloudflare challenge to automated requests.

## Approved sources

| ID | Display name | Publisher code | Feed URL |
|---|---|---|---|
| `physical-review-applied` | Physical Review Applied | `aps` | `https://feeds.aps.org/rss/recent/prapplied.xml` |
| `nature-electronics` | Nature Electronics | `nature` | `https://www.nature.com/natelectron.rss` |
| `advanced-electronic-materials` | Advanced Electronic Materials | `wiley` | `https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=2199160X` |
| `journal-applied-physics` | Journal of Applied Physics | `aip` | `https://pubs.aip.org/rss/site_1000029/1000017.xml` |
| `apl-materials` | APL Materials | `aip` | `https://pubs.aip.org/rss/site_1000013/1000009.xml` |
| `npj-computational-materials` | npj Computational Materials | `nature` | `https://www.nature.com/npjcompumats.rss` |
| `acta-materialia` | Acta Materialia | `elsevier` | `https://rss.sciencedirect.com/publication/science/13596454` |
| `science-advances` | Science Advances | `aaas` | `https://feeds.science.org/rss/science-advances.xml` |
| `nano-micro-letters` | Nano-Micro Letters | `springer` | `https://link.springer.com/search.rss?facet-journal-id=40820` |

The Nano-Micro Letters Springer search feed returns the latest 20 articles. The journal's alternate official RSS2 feed exposes 426 entries dating to 2023 and is intentionally not used for the first import.

## Configuration and publisher grouping

`feeds.yml` remains the sole source of truth. Add the nine sources as enabled entries and extend the validated publisher set with:

- `aps` → American Physical Society
- `elsevier` → Elsevier
- `aaas` → AAAS
- `springer` → Springer Nature

The Guide uses this deterministic publisher order:

1. Nature Portfolio
2. American Physical Society
3. AIP Publishing
4. IEEE
5. Wiley
6. Elsevier
7. AAAS
8. Springer Nature

ACS is not added as a publisher or feed in this change.

## Data flow and failure behavior

The existing ingestion contract is unchanged:

1. Fetch each enabled official RSS feed.
2. Normalize and deduplicate every valid item.
3. Store all items, whether or not they currently match a topic keyword.
4. Reclassify all stored articles using the 56 precise tags.
5. Publish the validated database snapshot atomically.

No topic-based prefilter is introduced. This prevents false-negative keyword matches from permanently discarding potentially useful papers.

Feed failures continue to produce a `partial` run when at least one source succeeds. Existing articles and tags are preserved. Advanced Electronic Materials uses a valid Wiley feed, but may fail intermittently on GitHub-hosted runners like the two existing Wiley feeds; this does not block successful sources from updating.

## Generated Guide and documentation

Regenerate the marked Guide region in `docs/index.html` from configuration. The resulting page must show:

- `29 SOURCES`
- all 29 enabled feed links
- all eight publisher groups that contain feeds
- the existing eight research directions and 56 precise tags
- no ACS Applied Materials & Interfaces entry

Update README counts and publisher descriptions without changing the documented daily schedule or classification semantics.

## Testing and acceptance

Tests must lock:

- 29 enabled feeds with unique IDs and URLs
- the exact nine new names, publisher codes, and HTTPS feed URLs
- the expanded valid-publisher set and deterministic Guide publisher order
- Guide synchronization, `29 SOURCES`, 29 feed links, eight topic groups, and 56 precise tags
- absence of ACS Applied Materials & Interfaces
- Nano-Micro Letters uses the 20-result Springer search RSS rather than the 426-entry archive feed
- existing classification, database safety, workflow, frontend, and Node tests remain green

Before deployment, run the renderer check, full Python suite, Node suite, Ruff, workflow contracts, and `git diff --check`. After deployment, manually trigger Daily RSS Update and verify a database-only bot commit plus a successful Pages build. A `partial` result caused only by intermittent publisher failures is acceptable if the database validates and all successful feeds publish safely.

## Non-goals

- Bypassing Cloudflare, paywalls, logins, or publisher access controls
- Adding ACS Applied Materials & Interfaces before a stable public machine-readable feed is available
- Filtering articles before storage
- Changing the 8-direction/56-tag taxonomy
- Changing the daily 08:00 Beijing schedule
