#!/usr/bin/env python3
"""Build or rebuild the BK-Tree fuzzy search index from the database.

This script reads all streets from the database and creates the BK-Tree
indices for typo-tolerant search. It should be run:
- After initial data import
- After significant data updates
- When the index files are missing or corrupted

Usage:
    python build_fuzzy_index.py [--force] [--batch-size N]
    
Options:
    --force         Rebuild index even if it exists
    --batch-size N  Process N streets at a time (default: 10000)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import select, func

from database import SessionLocal, init_db, engine
from models import Street
from fuzzy_search import FuzzySearchIndex, DEFAULT_BKTREE_PATH


def build_index(
    output_path: Optional[Path] = None,
    batch_size: int = 10000,
    force: bool = False,
    verbose: bool = True
) -> FuzzySearchIndex:
    """Build the BK-Tree index from the database.
    
    Args:
        output_path: Path to save the index (uses default if not specified)
        batch_size: Number of streets to process per batch
        force: Rebuild even if index exists
        verbose: Print progress information
        
    Returns:
        The built FuzzySearchIndex
    """
    output_path = output_path or DEFAULT_BKTREE_PATH
    
    # Check if index already exists
    if output_path.exists() and not force:
        if verbose:
            print(f"Index already exists at {output_path}")
            print("Use --force to rebuild")
        return FuzzySearchIndex.load(output_path)
    
    if verbose:
        print("=" * 60)
        print("Building BK-Tree Fuzzy Search Index")
        print("=" * 60)
    
    # Initialize database
    init_db()
    
    # Create new index
    index = FuzzySearchIndex(path=output_path)
    
    # Get total count
    with SessionLocal() as session:
        total_count = session.execute(
            select(func.count()).select_from(Street)
        ).scalar() or 0
    
    if verbose:
        print(f"Total streets in database: {total_count:,}")
        print(f"Batch size: {batch_size:,}")
        print()
    
    if total_count == 0:
        if verbose:
            print("No streets found in database. Index will be empty.")
        index.save(output_path)
        return index
    
    # Process in batches
    start_time = time.time()
    processed = 0
    
    with SessionLocal() as session:
        offset = 0
        
        while offset < total_count:
            # Fetch batch
            stmt = (
                select(Street)
                .order_by(Street.id)
                .offset(offset)
                .limit(batch_size)
            )
            streets = session.execute(stmt).scalars().all()
            
            if not streets:
                break
            
            # Add to index
            for street in streets:
                index.add_street(
                    street_id=street.id,
                    name=street.name,
                    city=street.city,
                    postal_code=street.postal_code,
                    latitude=street.latitude,
                    longitude=street.longitude,
                    regional_key=getattr(street, 'regional_key', None),
                    borough=getattr(street, 'borough', None),
                    suburb=getattr(street, 'suburb', None),
                )
                processed += 1
            
            offset += batch_size
            
            if verbose:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                pct = (processed / total_count) * 100
                print(f"\rProcessed: {processed:,} / {total_count:,} ({pct:.1f}%) - {rate:.0f} streets/sec", end="")
    
    if verbose:
        print()
        print()
    
    # Save index
    if verbose:
        print("Saving index to disk...")
    
    index.save(output_path)
    
    elapsed = time.time() - start_time
    
    if verbose:
        print()
        print("=" * 60)
        print("Index Build Complete")
        print("=" * 60)
        print(f"Streets indexed: {processed:,}")
        print(f"Time elapsed: {elapsed:.1f} seconds")
        print(f"Average rate: {processed / elapsed:.0f} streets/sec")
        print(f"Index saved to: {output_path}")
        print()
        
        # Print index stats
        stats = index.stats()
        print("Index Statistics:")
        print(f"  Total streets: {stats['total_streets']:,}")
        print(f"  Normalized tree depth: {stats['normalized_tree']['depth']}")
        print(f"  Phonetic German tree depth: {stats['phonetic_german_tree']['depth']}")
        print(f"  Phonetic Cologne tree depth: {stats['phonetic_cologne_tree']['depth']}")
        print(f"  Consonant tree depth: {stats['consonant_tree']['depth']}")
    
    return index


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build BK-Tree fuzzy search index from database"
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Rebuild index even if it exists"
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=10000,
        help="Number of streets to process per batch (default: 10000)"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output path for the index (uses default if not specified)"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output"
    )
    
    args = parser.parse_args()
    
    output_path = Path(args.output) if args.output else None
    
    try:
        build_index(
            output_path=output_path,
            batch_size=args.batch_size,
            force=args.force,
            verbose=not args.quiet
        )
    except KeyboardInterrupt:
        print("\nBuild interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError building index: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
