import asyncio
import math
from sqlalchemy import text
from database import AsyncSessionLocal

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

async def test():
    async with AsyncSessionLocal() as db:
        search_lat = 54.32730000000001
        search_lon = 10.1234
        
        # Get all Kampstraße streets sorted by distance
        sql = """
            SELECT name, city, latitude, longitude
            FROM streets
            WHERE name LIKE 'Kampst%'
            ORDER BY ((latitude - :lat) * (latitude - :lat) + (longitude - :lon) * (longitude - :lon))
            LIMIT 100
        """
        res = await db.execute(text(sql), {"lat": search_lat, "lon": search_lon})
        
        print(f"Search coords: ({search_lat}, {search_lon})")
        print("\nTop 100 Kampst* streets by distance:")
        
        neumuenster_rank = None
        for i, r in enumerate(res.fetchall(), 1):
            dist = haversine(search_lat, search_lon, r._mapping['latitude'], r._mapping['longitude'])
            city = r._mapping['city']
            if city == 'Neumünster':
                print(f">>> {i}. {r._mapping['name']:30} - {city:25} (dist: {dist:.2f}km) <<<")
                neumuenster_rank = i
            elif i <= 20:
                print(f"{i}. {r._mapping['name']:30} - {city:25} (dist: {dist:.2f}km)")
        
        if neumuenster_rank:
            print(f"\nNeumünster Kampstraße rank: #{neumuenster_rank}")
        else:
            print("\nNeumünster Kampstraße not in top 100!")

asyncio.run(test())
