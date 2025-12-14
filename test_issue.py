#!/usr/bin/env python3
"""Test script for the issue with city extraction and filtering."""

import asyncio
import logging
import os

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configure database
os.environ['DATABASE_URL'] = 'sqlite:///autocomplete.db'
os.environ['ASYNC_DATABASE_URL'] = 'sqlite+aiosqlite:///autocomplete.db'

from main import _extract_city_from_query, _get_known_cities
from httpx import AsyncClient, ASGITransport
from main import app

async def test_city_extraction():
    """Test city extraction."""
    print("\n" + "="*80)
    print("TESTING CITY EXTRACTION")
    print("="*80)
    
    known_cities = _get_known_cities('autocomplete.db')
    print(f'Total known cities: {len(known_cities)}')
    
    test_queries = [
        'kampstraße neum',
        'kampstraße neumuenster',
        'kampstraße neumünster',
    ]
    
    for query in test_queries:
        street_query, city = _extract_city_from_query(query, known_cities)
        print(f'\nQuery: {query!r}')
        print(f'  -> Street: {street_query!r}')
        print(f'  -> City: {city!r}')

async def test_autocomplete():
    """Test autocomplete API."""
    print("\n" + "="*80)
    print("TESTING AUTOCOMPLETE API")
    print("="*80)
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        test_cases = [
            {'query': 'kampstraße neum', 'lat': 53.9187, 'lon': 9.8786},
            {'query': 'kampstraße neumuenster', 'lat': 53.9187, 'lon': 9.8786},
            {'query': 'kampstraße neumünster', 'lat': 53.9187, 'lon': 9.8786},
        ]
        
        for tc in test_cases:
            params = {
                'query': tc['query'],
                'limit': 10,
                'latitude': tc['lat'],
                'longitude': tc['lon']
            }
            
            print(f"\n\nQuery: {tc['query']!r}")
            print(f"Params: {params}")
            
            response = await client.get('/autocomplete', params=params)
            results = response.json()
            
            print(f'Status: {response.status_code}')
            print(f'Results: {len(results)}')
            
            # Look for Neumünster results
            neumuenster_results = [r for r in results if 'neumünster' in r['city'].lower()]
            if neumuenster_results:
                print('\n  Neumünster results found:')
                for r in neumuenster_results:
                    print(f"    - {r['name']}, {r['city']} (score: {r['match_score']:.2f})")
            else:
                print('\n  No Neumünster results found!')
                print('  Top 5 results:')
                for i, r in enumerate(results[:5]):
                    print(f"    {i+1}. {r['name']}, {r['city']} (score: {r['match_score']:.2f}, dist: {r.get('distance_km', 'N/A')} km)")

async def main():
    await test_city_extraction()
    await test_autocomplete()

if __name__ == '__main__':
    asyncio.run(main())
