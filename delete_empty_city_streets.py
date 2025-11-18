#!/usr/bin/env python3
"""
Script to delete streets with empty city where a duplicate with non-empty city exists.

This script identifies streets where the city field is empty (NULL or empty string)
and removes them only if there exists another street entry with the same name and postal_code
but with a non-empty city.
"""

from sqlalchemy import text
from database import engine


def delete_empty_city_streets():
    """
    Delete streets with empty city if a duplicate with non-empty city exists.
    """
    with engine.begin() as conn:
        # First, count how many will be deleted for logging
        count_query = text("""
            SELECT COUNT(*) FROM streets
            WHERE (city IS NULL OR city = '')
            AND EXISTS (
                SELECT 1 FROM streets s2
                WHERE s2.name = streets.name
                AND s2.postal_code = streets.postal_code
                AND s2.city IS NOT NULL
                AND s2.city != ''
                AND s2.id != streets.id
            )
        """)

        result = conn.execute(count_query)
        count = result.scalar()

        if count > 0:
            print(f"Found {count} streets with empty city that have duplicates with non-empty city.")

            # Perform the deletion
            delete_query = text("""
                DELETE FROM streets
                WHERE (city IS NULL OR city = '')
                AND EXISTS (
                    SELECT 1 FROM streets s2
                    WHERE s2.name = streets.name
                    AND s2.postal_code = streets.postal_code
                    AND s2.city IS NOT NULL
                    AND s2.city != ''
                    AND s2.id != streets.id
                )
            """)

            conn.execute(delete_query)
            print(f"Deleted {count} streets.")
        else:
            print("No streets found that match the deletion criteria.")


if __name__ == "__main__":
    delete_empty_city_streets()
