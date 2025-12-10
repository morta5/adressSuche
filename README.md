# Address Autocomplete Project

This project provides an advanced address autocomplete API and frontend widget, using open geodata sources for address suggestions.

## Features
- Multi-stage retrieval: exact prefix, trigram FTS, SQL fuzzy on normalized_search
- Query understanding with German abbreviations and suffix expansion
- Phonetic retrieval and reranking (German + Cologne phonetic)
- Fast address autocomplete API
- **Reverse geocoding**: Find nearest address from coordinates
- Frontend JavaScript widget for address fields
- Import and search using open datasets
- Dockerized for easy deployment

## Data Sources & Attribution
- [Esri Deutschland, CC BY 4.0](https://arcg.is/0nXTyK):
  - Address data is imported from this dataset. You must credit Esri Deutschland as required by the [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/).
- [OpenStreetMap (OSM)](https://www.openstreetmap.org/copyright):
  - Additional address and street data is imported from OSM. You must credit OpenStreetMap contributors as required by the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).

**Both data sources are credited in the frontend autocomplete popup, in accordance with their licenses.**

## Quick Start

### Pre-built Database
A pre-populated database with address data is available for download:
https://cloud.farshidhakimy.de/s/4A82YZZzXtMzkxs

Download and place it in your project directory before starting the server.

### Local
```bash
python -m main
```

### Docker
Build:
```bash
docker build -t autocomplete-v2 .
```
Run:
```bash
docker run --rm -p 8001:8001 -v $(pwd):/data autocomplete-v2
```
ENV override (optional):
```bash
-e DATABASE_URL=sqlite:////data/autocomplete.db -e ASYNC_DATABASE_URL=sqlite+aiosqlite:////data/autocomplete.db
```

### Try the API
```bash
# Exact query
curl 'http://localhost:8001/autocomplete?query=bahnhofstrasse&limit=5'

# Query with typo (missing 'h')
curl 'http://localhost:8001/autocomplete?query=banhofstrasse&limit=5'

# Query with typo (missing 'l')
curl 'http://localhost:8001/autocomplete?query=schilerstrasse&limit=5'

# Reverse geocoding - find nearest address from coordinates
curl 'http://localhost:8001/reverse?latitude=53.5511&longitude=9.9937'

# Reverse geocoding with custom max distance (1km)
curl 'http://localhost:8001/reverse?latitude=53.5511&longitude=9.9937&max_distance_km=1.0'
```

## API Endpoints

### GET /autocomplete
Search for street names with autocomplete functionality.

**Parameters:**
- `query` (required): Search query string
- `city` (optional): Filter by city name
- `latitude`, `longitude` (optional): Coordinates for distance-based ranking
- `limit` (optional): Maximum results (default: 10)

### GET /validate
Validate a specific address (street + house number).

**Parameters:**
- `street_name` (required): Street name to validate
- `house_number` (required): House number to validate
- `city` (optional): City name filter
- `latitude`, `longitude` (optional): Coordinates for distance calculation

### GET /reverse
Find the nearest address or street from geographic coordinates.

**Behavior:**
1. First tries to find the nearest house number within the specified distance
2. If no house number is found, falls back to finding the nearest street segment
3. When matching a street (without house number), returns the closest point on the street

**Parameters:**
- `latitude` (required): Latitude coordinate
- `longitude` (required): Longitude coordinate
- `max_distance_km` (optional): Maximum search radius in kilometers (default: 0.1 km = 100m)

**Response:**
Returns the same structure as `/validate`:

When a house number is found:
```json
{
  "exists": true,
  "address_id": 12345,
  "street_name": "Hauptstraße",
  "city": "Hamburg",
  "postal_code": "20095",
  "house_number": "42",
  "latitude": 53.5511,
  "longitude": 9.9937,
  "distance_km": 0.02
}
```

When only a street is found (no house number nearby):
```json
{
  "exists": true,
  "address_id": null,
  "street_name": "Hauptstraße",
  "city": "Hamburg",
  "postal_code": "20095",
  "house_number": null,
  "latitude": 53.5511,
  "longitude": 9.9937,
  "distance_km": 0.01
}
```

If no address or street is found within the maximum distance, returns:
```json
{
  "exists": false
}
```

### Import Data
```bash
python import_addresses_csv.py <path_to_csv>
python import_osm.py
```

### Use the Frontend Widget
```html
<script src="frontend/address-autocomplete.js"></script>
<!-- See frontend/examples/ for usage -->
```

## License
- Project code: GNU Affero General Public License v3.0 (AGPLv3). See LICENSE file for details.
- Data: See above for data source licenses (CC BY 4.0 for Esri Deutschland, ODbL for OSM)

## Attribution (Required by Data Licenses)
- This project uses data from:
  - [Esri Deutschland, CC BY 4.0](https://arcg.is/0nXTyK)
  - [OpenStreetMap contributors, ODbL](https://www.openstreetmap.org/copyright)
- Both sources are credited in the frontend autocomplete popup as required by their licenses.

## Credits
- Esri Deutschland (CC BY 4.0)
- OpenStreetMap contributors (ODbL)