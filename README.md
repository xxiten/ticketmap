# ticketmap

A Python script that fetches open on-site tickets from the Visoma ticketing system and renders them as an interactive geographical map using Folium/Leaflet. The map is saved to `/var/www/html/index.html` for web serving.

## Features

- Fetches live ticket data from the Visoma API on every run (no stale cache)
- Geocodes ticket addresses via OpenStreetMap/Nominatim with a persistent local cache (`geo_cache.json`) to avoid redundant lookups
- Three-level geocoding fallback: full address → address with South Tyrol context → municipality only
- Colour-coded markers by ticket status: red (open), orange (in progress), green (done), black (approximate location)
- Radius filter — only shows tickets within a configurable distance from a center point
- Warning panel on the map listing tickets that could not be precisely located
- Multilingual UI: German, Italian, English
- Company logo and generation timestamp displayed on the map

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
| `language` | UI language: `de`, `it`, or `en` |

## Usage

```bash
python generate.py
python generate.py --language it
python generate.py -l en
```

### Arguments

| Argument | Description |
|---|---|
| `--language`, `-l` | Map UI language (`de` / `it` / `en`), default: `de` |

## Files

| File | Description |
|---|---|
| `generate.py` | Main script |
| `config.json` | Configuration (API token, center point, radius, language) |
| `geo_cache.json` | Auto-generated geocoding cache (Nominatim results) |

## Notes

- The script respects Nominatim's usage policy by waiting 1 second between geocoding requests.
- Addresses are only geocoded once and cached permanently in `geo_cache.json`. Delete this file to force re-geocoding.
- The API token in `config.json` is sensitive — do not commit it to a public repository.

