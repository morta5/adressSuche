# Address Autocomplete Project

This project provides an advanced address autocomplete API and frontend widget, using open geodata sources for address suggestions.

## Features
- **BK-Tree based fuzzy search** with Levenshtein distance for symmetric typo tolerance
- Multi-stage retrieval: exact prefix, trigram FTS, SQL fuzzy on normalized_search
- Query understanding with German abbreviations and suffix expansion
- Phonetic retrieval and reranking (German + Cologne phonetic)
- Fast address autocomplete API
- Frontend JavaScript widget for address fields
- Import and search using open datasets
- Dockerized for easy deployment

## Fuzzy Search with Typo Tolerance

The system uses a BK-Tree (Burkhard-Keller Tree) data structure for efficient typo-tolerant search:

- **Symmetric error tolerance**: Works regardless of whether the typo is in the query or the indexed data
- **Levenshtein distance**: Standard edit distance metric for measuring string similarity
- **Multiple indices**: Normalized text, phonetic codes (German + Cologne), and consonant skeletons
- **High performance**: O(log n) average lookup time for fuzzy matching

### Build the Fuzzy Index

After importing data, build the BK-Tree index:

```bash
python build_fuzzy_index.py
```

Options:
- `--force, -f`: Rebuild index even if it exists
- `--batch-size N`: Process N streets at a time (default: 10000)
- `--output PATH`: Custom output path for the index

The index is automatically rebuilt when using the import scripts.

## Data Sources & Attribution
- [Esri Deutschland, CC BY 4.0](https://arcg.is/0nXTyK):
  - Address data is imported from this dataset. You must credit Esri Deutschland as required by the [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/).
- [OpenStreetMap (OSM)](https://www.openstreetmap.org/copyright):
  - Additional address and street data is imported from OSM. You must credit OpenStreetMap contributors as required by the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).

**Both data sources are credited in the frontend autocomplete popup, in accordance with their licenses.**

## Quick Start

### Pre-built Database
A pre-populated database with address data is available for download:
https://cloud.farshidhakimy.de/s/ScfdTePfPc3oaR6

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
```

### Import Data
```bash
python import_addresses_csv.py <path_to_csv>
python import_osm.py
```

The fuzzy search index is automatically rebuilt after importing data.

### Use the Frontend Widget
```html
<script src="frontend/address-autocomplete.js"></script>
<!-- See frontend/examples/ for usage -->
```

## Testing

Run the test suite:

```bash
pytest test_fuzzy_search.py -v
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
