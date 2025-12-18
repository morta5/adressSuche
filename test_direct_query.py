import asyncio
import pytest
from sqlalchemy import text
from database import AsyncSessionLocal
import math

def haversine(lat1, lon1, lat2, lon2):
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return c * 6371

@pytest.mark.asyncio
async def test():
    async with AsyncSessionLocal() as db:
        search_lat = 54.32730000000001
        search_lon = 10.1234
        
        # Query exactly what stage_a does
        sql = """
            SELECT id, name, city, postal_code, latitude, longitude FROM (
                SELECT id, name, city, postal_code, latitude, longitude
                FROM streets
                WHERE name >= 'kampst' AND name < 'kampsu'
                UNION
                SELECT id, name, city, postal_code, latitude, longitude
                FROM streets
                WHERE normalized_name >= 'kampst' AND normalized_name < 'kampsu'
            ) sub
            ORDER BY ((latitude - :user_lat) * (latitude - :user_lat) + (longitude - :user_lon) * (longitude - :user_lon))
            LIMIT 500
        """
        
        res = await db.execute(text(sql), {"user_lat": search_lat, "user_lon": search_lon})
        rows = res.fetchall()
        
        print(f"SQL returned {len(rows)} results")
        print("\nLooking for Neumünster...")
        
        neumuenster_found = False
        for i, r in enumerate(rows, 1):
            if r._mapping['city'] == 'Neumünster':
                dist = haversine(search_lat, search_lon, r._mapping['latitude'], r._mapping['longitude'])
                print(f">>> #{i}. {r._mapping['name']:35} - {r._mapping['city']:25} (id: {r._mapping['id']}, dist: {dist:.2f}km)")
                neumuenster_found = True
                break
        
        if not neumuenster_found:
            print("Neumünster NOT in SQL results!")

asyncio.run(test())
