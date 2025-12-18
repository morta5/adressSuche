# Inject debug code to check if Neumünster is in stage_a results
import asyncio
import pytest
from sqlalchemy import text
from database import AsyncSessionLocal
from utils import haversine_distance

@pytest.mark.asyncio
async def test_stage_a_simulation():
    """Simulate the exact_prefix stage"""
    async with AsyncSessionLocal() as db:
        query = "kampst"
        latitude = 54.32730000000001
        longitude = 10.1234
        limit = 10
        
        # Simulate exact_prefix
        def prefix_end(prefix: str) -> str:
            if not prefix:
                return ""
            chars = list(prefix)
            for i in range(len(chars) - 1, -1, -1):
                if ord(chars[i]) < 0x10FFFF:
                    chars[i] = chr(ord(chars[i]) + 1)
                    return "".join(chars[: i + 1])
            return prefix + "\uffff"
        
        qc = "kampst"
        stage_a_limit = max(500, limit * 50)
        
        params = {
            "q1_start": query,
            "q1_end": prefix_end(query),
            "q2_start": qc,
            "q2_end": prefix_end(qc),
            "user_lat": latitude,
            "user_lon": longitude,
            "limit": stage_a_limit,
        }
        
        sql = """
            SELECT id, name, city, postal_code, latitude, longitude FROM (
                SELECT id, name, city, postal_code, latitude, longitude
                FROM streets
                WHERE name >= :q1_start AND name < :q1_end
                UNION
                SELECT id, name, city, postal_code, latitude, longitude
                FROM streets
                WHERE normalized_name >= :q2_start AND normalized_name < :q2_end
            ) sub
            ORDER BY ((latitude - :user_lat) * (latitude - :user_lat) + (longitude - :user_lon) * (longitude - :user_lon))
            LIMIT :limit
        """
        
        res = await db.execute(text(sql), params)
        rows = res.fetchall()
        
        print(f"Stage A SQL returned {len(rows)} rows (limit was {stage_a_limit})")
        
        # Process like exact_prefix does
        local = []
        added = set()
        
        for r in rows:
            sid = r._mapping["id"]
            if sid in added:
                print(f"  Skipping duplicate: {r._mapping['name']} - {r._mapping['city']} (id: {sid})")
                continue
            added.add(sid)
            
            name = r._mapping["name"]
            name_lower = name.lower()
            query_lower = query.lower()
            
            if name_lower.startswith(query_lower):
                sc = 1.0
            else:
                sc = 0.97
            
            d = haversine_distance(latitude, longitude, r._mapping["latitude"], r._mapping["longitude"])
            
            # Apply distance penalty
            if sc >= 0.7:
                k = 220.0
            elif sc >= 0.5:
                k = 150.0
            else:
                k = 100.0
            penalty = 1.0 / (1.0 + (d / k))
            sc_penalized = max(0.1, sc * penalty)
            
            if r._mapping['city'] == 'Neumünster':
                print(f">>> FOUND Neumünster at position {len(local)+1}: score={sc:.2f}, penalized={sc_penalized:.3f}, dist={d:.2f}km")
            
            local.append((r._mapping['name'], r._mapping['city'], sc_penalized, d, sid))
        
        print(f"\nStage A processed {len(local)} unique results")
        
        # Check if Neumünster is in the results
        neumuenster_entries = [x for x in local if x[1] == 'Neumünster']
        if neumuenster_entries:
            for entry in neumuenster_entries:
                print(f"  Neumünster: {entry}")
        else:
            print("  Neumünster NOT in stage_a results!")

asyncio.run(test_stage_a_simulation())
