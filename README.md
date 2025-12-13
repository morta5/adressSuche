# 🏠 Address Autocomplete API - German Address Search & Geocoding

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)

A high-performance, open-source address autocomplete and geocoding API for German addresses. Built with FastAPI and SQLite, featuring advanced fuzzy matching, phonetic search, and reverse geocoding capabilities.

## ✨ Key Features

### 🔍 **Intelligent Address Search**
- **Multi-stage retrieval**: Exact prefix matching, trigram FTS5, fuzzy matching, and phonetic search
- **Typo tolerance**: Finds addresses even with spelling mistakes (e.g., "Kiler Straße" → "Kieler Straße")
- **Query understanding**: Automatically expands German abbreviations and handles various input formats
- **City detection**: Automatically extracts city names from queries (e.g., "Hauptstraße Berlin")
- **Phonetic matching**: Uses German phonetic algorithms (Metaphone + Cologne Phonetic) for sound-alike matching

### 📍 **Reverse Geocoding**
- Find the nearest address or street from geographic coordinates
- Supports both house number matching and street segment matching
- Configurable search radius with exponential backoff
- Returns closest point on street for segment matches

### 🌐 **Address Validation**
- Validate street name and house number combinations
- Check if an address exists in the database
- Get exact coordinates for validated addresses
- Calculate distance from user location

### ⚡ **High Performance**
- **< 50ms** response time for exact prefix queries
- **< 300ms** for complex fuzzy/typo searches
- Optimized database indexes and query execution
- Caching of normalized forms and phonetic codes
- Early exit optimization to skip expensive stages

### 🎨 **Frontend Widget**
- Ready-to-use JavaScript autocomplete widget
- Single-field and multi-field input support
- Keyboard, mouse, and touch support
- Automatic house number validation
- Customizable themes (light/dark)

## 🚀 Quick Start

### Prerequisites
- Python 3.10 or higher
- SQLite 3.x

### Installation

```bash
# Clone the repository
git clone https://github.com/morta5/adressSuche.git
cd adressSuche

# Install dependencies
pip install -r requirements.txt
```

### Download Pre-built Database

A pre-populated database with German address data is available for download:

**Download**: https://cloud.farshidhakimy.de/s/4A82YZZzXtMzkxs

Place the downloaded `autocomplete.db` file in your project directory.

### Run the API Server

```bash
# Development server
python -m main

# Production server with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8001 --workers 4
```

The API will be available at `http://localhost:8001`

### Docker Deployment

```bash
# Build the image
docker build -t address-autocomplete .

# Run the container
docker run --rm -p 8001:8001 -v $(pwd):/data address-autocomplete

# With custom database path
docker run --rm -p 8001:8001 \
  -e DATABASE_URL=sqlite:////data/autocomplete.db \
  -e ASYNC_DATABASE_URL=sqlite+aiosqlite:////data/autocomplete.db \
  -v $(pwd):/data address-autocomplete
```

## 📖 API Documentation

### Interactive API Docs

Once the server is running, visit:
- **Swagger UI**: http://localhost:8001/docs
- **ReDoc**: http://localhost:8001/redoc

### Endpoints

#### 🔍 **GET /autocomplete** - Address Autocomplete

Search for street names with intelligent autocomplete.

**Parameters:**
- `query` (required, string): Search query (e.g., "Hauptstraße", "Kieler Str", "Bahnhofstr Berlin")
- `city` (optional, string): Filter results by city name
- `latitude` (optional, float): Latitude for distance-based ranking
- `longitude` (optional, float): Longitude for distance-based ranking
- `limit` (optional, integer): Maximum results (default: 10, max: 100)

**Example Requests:**

```bash
# Simple search
curl 'http://localhost:8001/autocomplete?query=bahnhofstrasse&limit=5'

# Search with typo
curl 'http://localhost:8001/autocomplete?query=banhofstrasse&limit=5'

# Search with city
curl 'http://localhost:8001/autocomplete?query=hauptstraße&city=Hamburg&limit=5'

# Search with location-based ranking
curl 'http://localhost:8001/autocomplete?query=hauptstraße&latitude=53.5511&longitude=9.9937&limit=5'

# City in query (automatically extracted)
curl 'http://localhost:8001/autocomplete?query=jungfernstieg+hamburg&limit=5'
```

**Response:**

```json
[
  {
    "street_id": 12345,
    "name": "Hauptstraße",
    "city": "Hamburg",
    "postal_code": "20095",
    "latitude": 53.5511,
    "longitude": 9.9937,
    "match_score": 0.98,
    "distance_km": 0.5
  }
]
```

#### ✅ **GET /validate** - Address Validation

Validate a specific address (street + house number).

**Parameters:**
- `street_name` (required, string): Street name to validate
- `house_number` (required, string): House number to validate
- `city` (optional, string): City name filter
- `latitude` (optional, float): Latitude for distance calculation
- `longitude` (optional, float): Longitude for distance calculation

**Example Request:**

```bash
curl 'http://localhost:8001/validate?street_name=Hauptstraße&house_number=42&city=Hamburg'
```

**Response:**

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

#### 📍 **GET /reverse** - Reverse Geocoding

Find the nearest address or street from geographic coordinates.

**Parameters:**
- `latitude` (required, float): Latitude coordinate
- `longitude` (required, float): Longitude coordinate
- `max_distance_km` (optional, float): Maximum search radius in km (default: 0.1 km = 100m)

**Behavior:**
1. First tries to find the nearest house number within the specified distance
2. If no house number found, falls back to finding the nearest street segment
3. Returns the closest point on the street for segment matches

**Example Requests:**

```bash
# Default search radius (100m)
curl 'http://localhost:8001/reverse?latitude=53.5511&longitude=9.9937'

# Custom search radius (1km)
curl 'http://localhost:8001/reverse?latitude=53.5511&longitude=9.9937&max_distance_km=1.0'
```

**Response (house number found):**

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

**Response (only street found):**

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
  "distance_km": 0.05
}
```

## 🎨 Frontend Widget

The project includes a ready-to-use JavaScript widget for address autocomplete.

### Basic Usage

```html
<!DOCTYPE html>
<html>
<head>
  <title>Address Autocomplete</title>
</head>
<body>
  <input type="text" id="address-input" placeholder="Enter address..." />

  <script src="frontend/address-autocomplete.js"></script>
  <script>
    const autocomplete = new AddressAutocomplete({
      input: '#address-input',
      apiUrl: 'http://localhost:8001',
      onComplete: function(address) {
        console.log('Selected address:', address);
      }
    });
  </script>
</body>
</html>
```

### Configuration Options

```javascript
new AddressAutocomplete({
  // Input field (required)
  input: '#address-input',  // or HTMLElement
  
  // API URL (required)
  apiUrl: 'http://localhost:8001',
  
  // Callbacks
  onComplete: function(address) {},    // Called when address is selected
  onValidate: function(result) {},     // Called after validation
  
  // Behavior
  validateOnComplete: true,             // Auto-validate after selection
  debounceMs: 300,                      // Debounce delay
  minChars: 2,                          // Min characters before search
  maxSuggestions: 10,                   // Max suggestions to show
  
  // Proximity search
  proximity: { lat: 53.5511, lon: 9.9937 },
  
  // Styling
  theme: 'light',  // or 'dark'
});
```

See `frontend/examples/` for more examples.

## 📊 Data Sources & Attribution

This project uses open geodata sources:

- **[Esri Deutschland](https://arcg.is/0nXTyK)** - Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- **[OpenStreetMap](https://www.openstreetmap.org/copyright)** - Licensed under [Open Database License (ODbL)](https://www.openstreetmap.org/copyright)

**Attribution is required** when using this API. Both data sources are credited in the frontend autocomplete popup as required by their licenses.

### Import Data

```bash
# Import from CSV (Esri Deutschland format)
python import_addresses_csv.py <path_to_csv>

# Import from OpenStreetMap
python import_osm.py
```

## 🛠️ Technical Details

### Architecture

- **Backend**: FastAPI (async Python web framework)
- **Database**: SQLite with FTS5 full-text search
- **Search Algorithms**:
  - Exact prefix matching (indexed range queries)
  - Trigram-based FTS5 (BM25 ranking)
  - Fuzzy matching with Levenshtein distance
  - Phonetic search (German Metaphone + Cologne Phonetic)
  - Consonant skeleton matching

### Performance Characteristics

| Query Type | Response Time | Example |
|------------|---------------|---------|
| Exact prefix | < 50ms | "Hauptstraße" |
| Trigram FTS | < 100ms | "hauptstr" |
| Fuzzy/Typo | < 300ms | "banhofstrasse" (typo) |
| Phonetic | < 500ms | "Kiler Straße" → "Kieler Straße" |
| Reverse geocode | < 100ms | lat/lon lookup |

### Database Schema

```sql
-- Streets table with normalized fields
CREATE TABLE streets (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,     -- Compact normalized (no spaces)
  normalized_search TEXT NOT NULL,   -- Normalized with spaces
  city TEXT NOT NULL,
  postal_code TEXT,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  phonetic_german TEXT NOT NULL,     -- German phonetic code
  phonetic_cologne TEXT NOT NULL,    -- Cologne phonetic code
  consonant_key TEXT NOT NULL        -- Consonant skeleton
);

-- FTS5 virtual table for trigram search
CREATE VIRTUAL TABLE street_trigram USING fts5(
  content='streets',
  content_rowid='id',
  tokenize='trigram'
);

-- Addresses with house numbers
CREATE TABLE addresses (
  id INTEGER PRIMARY KEY,
  street_id INTEGER NOT NULL,
  house_number TEXT NOT NULL,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  FOREIGN KEY (street_id) REFERENCES streets(id)
);

-- Street segments for reverse geocoding
CREATE TABLE street_segments (
  id INTEGER PRIMARY KEY,
  street_id INTEGER NOT NULL,
  start_lat REAL NOT NULL,
  start_lon REAL NOT NULL,
  end_lat REAL NOT NULL,
  end_lon REAL NOT NULL,
  min_lat REAL NOT NULL,  -- Bounding box
  max_lat REAL NOT NULL,
  min_lon REAL NOT NULL,
  max_lon REAL NOT NULL,
  FOREIGN KEY (street_id) REFERENCES streets(id)
);
```

## 🧪 Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest test_api_cases.py -v

# Run with database (if available)
pytest test_api_cases.py -v -k "not skipif"
```

## 📝 License

**Project Code**: GNU Affero General Public License v3.0 (AGPLv3)

This means:
- ✅ You can use, modify, and distribute this code
- ✅ You can use it commercially
- ⚠️ You must disclose the source code of your modifications
- ⚠️ You must license your modifications under AGPLv3
- ⚠️ If you run a modified version as a web service, you must make the source available

See [LICENSE](license.txt) for details.

**Data**: See [Data Sources & Attribution](#-data-sources--attribution) above

## 🤝 Contributing

Contributions are welcome! This is an open-source project.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 🐛 Issues & Support

- **Bug reports**: [GitHub Issues](https://github.com/morta5/adressSuche/issues)
- **Feature requests**: [GitHub Issues](https://github.com/morta5/adressSuche/issues)
- **Questions**: [GitHub Discussions](https://github.com/morta5/adressSuche/discussions)

## 📚 Related Projects

- [Pelias](https://github.com/pelias/pelias) - Modular geocoding system
- [Nominatim](https://github.com/osm-search/Nominatim) - OSM geocoding tool
- [Photon](https://github.com/komoot/photon) - Lightning-fast geocoder

## 🙏 Acknowledgments

- **Esri Deutschland** for providing high-quality address data
- **OpenStreetMap contributors** for comprehensive street data
- **FastAPI** for the excellent web framework
- All contributors to this open-source project

---

**Made with ❤️ for the open-source community**