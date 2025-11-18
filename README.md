# Address Autocomplete Project

This project provides an advanced address autocomplete API and frontend widget, using open geodata sources for address suggestions.

## Features
- Multi-stage retrieval: exact prefix, trigram FTS, SQL fuzzy on normalized_search
- Query understanding with German abbreviations and suffix expansion
- Phonetic retrieval and reranking (German + Cologne phonetic)
- Fast address autocomplete API
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

### Local
```bash
python -m v2.main
```

### Docker
Build:
```bash
docker build -t autocomplete-v2 ./v2
```
Run:
```bash
docker run --rm -p 8001:8001 -v $(pwd)/v2:/data autocomplete-v2
```
ENV override (optional):
```bash
-e DATABASE_URL=sqlite:////data/autocomplete.db -e ASYNC_DATABASE_URL=sqlite+aiosqlite:////data/autocomplete.db
```

### Try the API
```bash
curl 'http://localhost:8001/autocomplete?query=bahnof%20str&limit=5'
curl 'http://localhost:8001/autocomplete?query=schiler%20allee&limit=5'
```

### Import Data
```bash
python import_addresses_csv.py
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

