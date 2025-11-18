#!/usr/bin/env python3
"""
Script to fill empty city fields for streets based on postal code.

This script attempts to determine the city for streets where the city field is empty
by looking up the most common city associated with the same postal code from other streets.
"""

from sqlalchemy import text
from database import engine


def fill_empty_city_streets():
    """
    Fill empty city fields for streets using postal code lookup.
    """
    with engine.begin() as conn:
        # Get all streets with empty city
        empty_streets_query = text("""
            SELECT id, name, postal_code
            FROM streets
            WHERE (city IS NULL OR city = '')
            AND postal_code IS NOT NULL
            AND postal_code != ''
        """)

        empty_streets = conn.execute(empty_streets_query).fetchall()

        updated_count = 0

        for street in empty_streets:
            street_id, name, postal_code = street

            # Find the most common non-empty city for this postal code
            city_query = text("""
                SELECT city, COUNT(*) as count
                FROM streets
                WHERE postal_code = :postal_code
                AND city IS NOT NULL
                AND city != ''
                GROUP BY city
                ORDER BY count DESC
                LIMIT 1
            """)

            city_result = conn.execute(city_query, {"postal_code": postal_code}).fetchone()

            if city_result:
                most_common_city = city_result[0]

                # Check if a street with this name, postal_code, and city already exists
                check_query = text("""
                    SELECT COUNT(*) FROM streets
                    WHERE name = :name AND postal_code = :postal_code AND city = :city
                """)

                exists = conn.execute(check_query, {"name": name, "postal_code": postal_code, "city": most_common_city}).scalar()

                if exists == 0:
                    # Update the street
                    update_query = text("""
                        UPDATE streets
                        SET city = :city
                        WHERE id = :id
                    """)

                    conn.execute(update_query, {"city": most_common_city, "id": street_id})
                    updated_count += 1

        print(f"Updated city for {updated_count} streets.")


if __name__ == "__main__":
    fill_empty_city_streets()
