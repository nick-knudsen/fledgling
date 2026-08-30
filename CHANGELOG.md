# Changelog

Notable changes to Fledgling. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):
entries are grouped as `Added`, `Changed`, `Fixed`, or `Removed`.

Fledgling deploys continuously rather than cutting versioned releases, so entries below are grouped by date instead of a version number.


## 2026-08-30

### Added
- CI: typechecking and linting
- Carto API key

## 2026-08-16

### Added
- This changelog.

## 2026-03-19

### Added
- Donation button.
- Ingestion pipeline for eBird sampling-event data.

## 2026-03-11

### Added
- Named trips: save, rename, and reload itineraries, with a sidebar UI for managing saved trips and a
  walkthrough step introducing trip saving.
- Discord community button.

### Changed
- Save button disabled when a trip has no unsaved changes.

### Removed
- The closed-beta wall — Fledgling opened to the public.

## 2026-03-02

### Added
- Automated, partitioned data pipeline with restartable stages, streaming processing for very large input
  files, and a single-world-file entry point with dry-run support.
- Type-ahead search on search-area dropdowns, with parent-selection-driven backpropagation of child regions.
- Fuzzy species search (Fuse.js).
- Automatic sampling (every Nth day) and error handling for very large or open-ended time-span queries.

### Fixed
- Route points crossing the antimeridian no longer drew across separate copies of the world map.
- Disambiguated states, provinces, and counties that share a name across different countries/states.
- Added input validation on the number of search results, defaulting to 5.
- Long detection-probability percentages no longer wrapped/overflowed in the UI.
- Dropdown search no longer matched mid-string; child dropdowns stay hidden until a parent is selected; a new
  search now returns to the Explore tab.

### Removed
- Temporarily disabled driving-time search while the OSRM integration was reworked.
- Retired the older standalone eBird data pipeline script in favor of the automated one.

## 2026-02-19

### Added
- Itinerary/plan tab: drag-and-drop hotspot ordering, a route line on the map, and a running count of
  expected and marginal potential lifers as hotspots are added or removed.
- Persistent map shared across tabs, with itinerary hotspots highlighted and ordered marker stacking.
- Welcome dialog and guided site tour for new users.
- Hotspot search within the plan tab.

### Changed
- Marginal potential-lifer counts recalculate and route data caches as the itinerary changes, instead of
  recomputing everything from scratch.
- Restyled the plan tab and stat cards; mobile styling improvements.

## 2026-02-11

### Added
- Hotspot ranking using a lower-bound Wilson score against a user's target species list.
- Map and hotspot-list frontend, with life/year list toggle, expandable per-hotspot species detail, and
  personal eBird checklist upload to compute a life list.
- Multi-select search areas across counties, states/provinces, and countries.
- Driving-time estimates via the OSRM API, including using the user's current location.
- Recently-observed species (last 30 days) per hotspot, via the eBird API.
- Species search backed by stored taxonomy.
- Docker-based deployment, deploy script, and initial VPS hosting setup.
- Basic usage analytics (GoatCounter).
