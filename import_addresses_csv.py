"""Import addresses from a CSV into the database.

The CSV is expected to contain coordinates in EPSG:3857 (Web Mercator).
This script converts them to WGS84 (EPSG:4326) before storing.

It will avoid inserting duplicate addresses by checking for an existing
street (by name+city) and an existing address (street_id + house_number).

The script tries several common header names so it works with different
CSV formats (English/German, different providers).
"""
from __future__ import annotations

import argparse
import csv
import math
import traceback
from typing import Optional, Sequence

# Delay importing SQLAlchemy-backed modules until we actually write to the DB so
# the script can run in --preview mode on systems that don't have SQLAlchemy
# installed.



R_MAJOR = 6378137.0  # Web Mercator (EPSG:3857) sphere radius


def webmercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convert EPSG:3857 (Web Mercator) x/y (meters) to WGS84 lat/lon (degrees).

    Args:
        x: Easting (meters)
        y: Northing (meters)

    Returns:
        (latitude, longitude) in decimal degrees (EPSG:4326)
    """
    lon = (x / R_MAJOR) * (180.0 / math.pi)
    lat = (math.pi / 2 - 2 * math.atan(math.exp(-y / R_MAJOR))) * (180.0 / math.pi)
    return lat, lon


def find_first(row: dict, keys: Sequence[str]) -> Optional[str]:
    """Return the first non-empty value in row for given possible keys."""
    for k in keys:
        if k in row:
            v = row[k]
            if v is None:
                continue
            v = v.strip()
            if v != "":
                return v
    return None


COMMON_STREET_KEYS = [
    "street",
    "Street",
    "STRASSE",
    "STR",
    "name",
    "Name",
    "STREET_NAME",
    "STR_NAME",
    "Straßenname",
    "STRASSENNAME",
    "strassenname",
    "StraßenName",
]

COMMON_HOUSE_KEYS = ["house_number", "HOUSE_NUMBER", "Hausnummer", "HAUSNUMMER", "HNR", "housenumber", "house"]

COMMON_CITY_KEYS = ["city", "City", "ORT", "Locality", "locality", "town", "municipality", "gemeinde"]
COMMON_CITY_KEYS += ["Ortsname", "ORTSNAME", "ortsname", "OrtsName", "Gemeindename", "GEMEINDENAME", "gemeindename"]

COMMON_POSTAL_KEYS = ["postal_code", "PostalCode", "PLZ", "postcode", "ZIP", "postal"]

COMMON_X_KEYS = ["x", "X", "xcoord", "X_COORD", "EASTING", "Easting", "east", "lon", "longitude_x", "x_m"]
COMMON_Y_KEYS = ["y", "Y", "ycoord", "Y_COORD", "NORTHING", "Northing", "north", "lat", "latitude_y", "y_m"]


def parse_geometry_wkt(wkt: str) -> Optional[tuple[float, float]]:
    """Try to parse a WKT POINT or "x y" geometry into (x, y).

    Accepts strings like "POINT (x y)" or "x y".
    """
    if not wkt:
        return None
    w = wkt.strip()
    if w.upper().startswith("POINT"):
        # POINT (x y)
        try:
            inner = w[w.find("(") + 1 : w.rfind(")")]
            parts = inner.split()
            if len(parts) >= 2:
                return float(parts[0]), float(parts[1])
        except Exception:
            return None
    else:
        # maybe "x y"
        parts = w.split()
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except Exception:
                return None
    return None


def import_addresses(csv_path: str, commit_every: int = 1000, dry_run: bool = False) -> None:
    """Import addresses from csv_path into the project's database.

    The CSV should contain coordinates in EPSG:3857. The function will convert
    to WGS84 (EPSG:4326) before inserting. Existing addresses (street + house_number)
    are skipped.
    """
    # Import DB bindings lazily so the module can be imported / previewed without
    # requiring the SQLAlchemy package to be installed.
    from database import SessionLocal, init_db
    from models import Street, Address

    init_db()
    db = SessionLocal()

    inserted_streets = 0
    inserted_addresses = 0
    skipped_existing = 0
    skipped_no_coords = 0

    # Performance caches
    # street_cache: key -> Street ORM object
    street_cache: dict[str, object] = {}
    # address_sets: key -> set(house_number) for quick duplicate checking
    address_sets: dict[str, set[str]] = {}

    # Buffers for batched ORM objects to reduce DB round-trips
    address_buffer: list[object] = []

    # Sizes for batching
    address_batch_size = min(1000, max(100, commit_every))
    street_flush_every = 200  # flush after creating this many new street objects

    new_street_count = 0

    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for i, row in enumerate(reader, start=1):
                # Find fields using common header names
                street_name = find_first(row, COMMON_STREET_KEYS)
                house_number = find_first(row, COMMON_HOUSE_KEYS)
                # Prefer Gemeindename (full municipality name). If present,
                # extract everything before the first comma.
                raw_gemeinde = None
                for key in ("Gemeindename", "GEMEINDENAME", "gemeindename"):
                    if key in row and row[key]:
                        raw_gemeinde = row[key].strip()
                        break

                if raw_gemeinde:
                    # use substring before first comma
                    city = raw_gemeinde.split(",", 1)[0].strip()
                else:
                    city = find_first(row, COMMON_CITY_KEYS) or ""
                postal = find_first(row, COMMON_POSTAL_KEYS)

                # Try geometry fields
                x_val = find_first(row, COMMON_X_KEYS)
                y_val = find_first(row, COMMON_Y_KEYS)
                if not x_val or not y_val:
                    # try common geometry column
                    geom = row.get("geometry") or row.get("geom") or row.get("WKT") or row.get("wkt")
                    if geom:
                        parsed = parse_geometry_wkt(geom)
                        if parsed:
                            x_val, y_val = str(parsed[0]), str(parsed[1])

                if not street_name or not house_number:
                    # essential data missing, skip
                    continue

                # Parse coords
                try:
                    x = float(x_val) if x_val is not None else None
                    y = float(y_val) if y_val is not None else None
                except Exception:
                    x = y = None

                if x is None or y is None:
                    skipped_no_coords += 1
                    # we require coordinates to store addresses (models enforce non-null)
                    continue

                # Convert EPSG:3857 -> WGS84
                # Prefer pyproj if available for robust reprojection, fall back
                # to the simple Web-Mercator math helper otherwise.
                transformer = None
                try:
                    from pyproj import Transformer

                    transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
                except Exception:
                    transformer = None

                if transformer:
                    # Transformer.transform returns (lon, lat) when always_xy=True
                    lon_t, lat_t = transformer.transform(x, y)
                    lat, lon = float(lat_t), float(lon_t)
                else:
                    lat, lon = webmercator_to_wgs84(x, y)

                # Normalize small values
                street_name = street_name.strip()
                house_number = house_number.strip()
                city = city.strip()

                # Use cache key for street (lowercased name + city)
                skey = f"{street_name.lower()}|{str(postal)}|{city.lower()}"

                street = street_cache.get(skey)

                if street is None:
                    # First time we see this street: try to find in DB
                    street = (
                        db.query(Street)
                        .filter(Street.name.ilike(street_name))
                        .filter(Street.postal_code == postal)
                        .filter(Street.city.ilike(city))
                        .first()
                    )

                    if street:
                        # Load existing house numbers for this street once
                        existing_hns = set(
                            r[0]
                            for r in db.query(Address.house_number)
                            .filter(Address.street_id == street.id)
                            .all()
                        )
                        address_sets[skey] = existing_hns
                    else:
                        # Create new street object and add to session (not committed yet)
                        street = Street(
                            name=street_name,
                            city=city or "",
                            postal_code=postal,
                            latitude=lat,
                            longitude=lon,
                        )
                        if not dry_run:
                            db.add(street)
                        new_street_count += 1
                        inserted_streets += 1
                        # new street has no addresses yet
                        address_sets[skey] = set()

                    street_cache[skey] = street

                # Quick duplicate check using cached set
                hset = address_sets.get(skey)
                if house_number in hset:
                    skipped_existing += 1
                    continue

                # Create Address ORM object referencing street ORM object
                address = Address(
                    street=street,
                    house_number=house_number,
                    latitude=lat,
                    longitude=lon,
                )

                # Buffer and track
                if not dry_run:
                    # ensure the address object is part of the session so flush() picks it up
                    db.add(address)
                address_buffer.append(address)
                hset.add(house_number)
                inserted_addresses += 1

                # Periodic flush of buffered addresses and new streets
                if not dry_run and (len(address_buffer) >= address_batch_size or new_street_count >= street_flush_every):
                    # Use a transaction flush to persist pending ORM objects and assign ids
                    try:
                        db.flush()
                    except Exception as e:
                        print(f"Flush error at row {i}: {e}")
                        db.rollback()

                    address_buffer.clear()
                    new_street_count = 0

                # Periodic commit
                if not dry_run and (inserted_addresses % commit_every == 0):
                    try:
                        db.commit()
                    except Exception as e:
                        print(f"Commit error at row {i}: {e}")
                        db.rollback()

            # final flush/commit
            if not dry_run:
                try:
                    if address_buffer:
                        db.flush()
                    db.commit()
                except Exception as e:
                    print(f"Final commit error: {e}")
                    db.rollback()

    except FileNotFoundError:
        print(f"CSV file not found: {csv_path}")
    except Exception as exc:
        print(f"Error while importing: {exc}")
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

    print(f"Import finished. Streets inserted: {inserted_streets}")
    print(f"Addresses inserted: {inserted_addresses}")
    print(f"Addresses skipped (existing): {skipped_existing}")
    print(f"Rows skipped (no coords): {skipped_no_coords}")


def preview_csv(csv_path: str, rows: int = 5) -> None:
    """Print CSV headers and a few sample rows with detected mapping hints."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            print("Headers:")
            print(reader.fieldnames)
            print()
            print(f"Showing up to {rows} sample rows:")
            for i, row in enumerate(reader, start=1):
                if i > rows:
                    break
                # show a compact preview with detected values
                street_name = find_first(row, COMMON_STREET_KEYS)
                house_number = find_first(row, COMMON_HOUSE_KEYS)
                city = find_first(row, COMMON_CITY_KEYS)
                postal = find_first(row, COMMON_POSTAL_KEYS)
                x_val = find_first(row, COMMON_X_KEYS) or row.get("geometry") or row.get("geom")
                y_val = find_first(row, COMMON_Y_KEYS)
                parsed_geom = None
                if x_val and y_val:
                    parsed_geom = (x_val, y_val)
                elif x_val and not y_val:
                    parsed = parse_geometry_wkt(x_val)
                    if parsed:
                        parsed_geom = parsed

                print(f"Row {i}:")
                print(f"  street (detected): {street_name}")
                print(f"  house_number (detected): {house_number}")
                print(f"  city (detected): {city}")
                print(f"  postal (detected): {postal}")
                print(f"  coords (raw/parsed): {parsed_geom}")
                print("  sample keys -> values:")
                # show a few keys to avoid giant output
                shown = 0
                for k, v in row.items():
                    if v is None or v == "":
                        continue
                    print(f"    {k}: {v}")
                    shown += 1
                    if shown >= 6:
                        break
                print()
    except FileNotFoundError:
        print(f"CSV file not found: {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import addresses CSV (EPSG:3857) into DB")
    parser.add_argument("csv", help="Path to CSV file to import")
    parser.add_argument("--commit-every", type=int, default=1000, help="Commit every N inserted addresses")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB; only print stats")
    parser.add_argument("--preview", action="store_true", help="Print headers and a few sample rows and exit")

    args = parser.parse_args()
    if args.preview:
        preview_csv(args.csv)
        return

    import_addresses(args.csv, commit_every=args.commit_every, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
