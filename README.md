# ticketmap

A Python script that fetches open on-site tickets from the Visoma ticketing system and renders them as an interactive geographical map using Folium/Leaflet. The map is saved to `/var/www/html/index.html` for web serving.

## Features

- Fetches live ticket data from the Visoma API on every run (no stale cache)
- Geocodes ticket addresses via **Photon** (Komoot) with a persistent local cache (`geo_cache.json`) to avoid redundant lookups
- Cache is saved incrementally after each new geocoding result — interrupted runs resume where they left off
- Three-level geocoding fallback: full address → address with South Tyrol context → municipality only
- Colour-coded markers by ticket status: red (open), orange (in progress), green (done), black (approximate location)
- Marker clustering — overlapping markers are grouped and expand on zoom
- Radius filter — only shows tickets within a configurable distance from a center point
- Warning table (top-right) listing tickets that could not be precisely located
- Multilingual UI: German, Italian, English
- Company logo (embedded, no external requests) and generation timestamp displayed top-center on the map
- Atomic output — map is written to a temp file then moved into place, preventing a corrupt live file on crash

## Requirements

```
folium
geopy
requests
```

Install with:

```bash
pip install folium geopy requests
```

## Configuration

Edit `config.json`:

```json
{
    "api_token": "YOUR_API_TOKEN",
    "center_point": [46.4983, 11.3547],
    "radius_km": 120,
    "language": "de"
}
```

| Key | Description |
|---|---|
| `api_token` | Visoma API authentication token |
| `center_point` | `[latitude, longitude]` of the map center / radius origin |
| `radius_km` | Only show tickets within this radius (km) |
| `language` | UI language: `de`, `it`, or `en` — can be overridden with `--language` |

## Usage

```bash
python generate.py
python generate.py --language it
python generate.py -l en
```

### Arguments

| Argument | Description |
|---|---|
| `--language`, `-l` | Map UI language (`de` / `it` / `en`), overrides `config.json` |

## Files

| File | Description |
|---|---|
| `generate.py` | Main script |
| `config.json` | Configuration (API token, center point, radius, language) |
| `geo_cache.json` | Auto-generated geocoding cache (Photon results) — safe to delete to force re-geocoding |

## Notes

- The Photon geocoder (by Komoot) is used instead of the public Nominatim API to avoid rate limiting on bulk geocoding runs.
- Addresses are geocoded once and cached permanently in `geo_cache.json`. The cache is written after every new entry so progress is never lost on interruption.
- The API token in `config.json` is sensitive — do not commit it to a public repository.

