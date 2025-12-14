"""Database models for street autocomplete API."""
from typing import cast

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Index,
    ForeignKey,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import declarative_base, relationship

from phonetic import german_phonetic_phrase, cologne_phonetic_phrase
from utils import normalize_compact, normalize_string, consonant_key

Base = declarative_base()


class Street(Base):
    """Street model for storing street names and their base locations."""

    __tablename__ = 'streets'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    normalized_name = Column(String, index=True)
    city = Column(String, nullable=False, index=True)
    postal_code = Column(String, index=True)
    regional_key = Column(String, index=True)
    borough = Column(String, index=True)
    suburb = Column(String, index=True)
    # Base latitude/longitude for the street (e.g., street center)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    normalized_name = Column(String, nullable=False, index=True, default="")
    normalized_search = Column(String, nullable=False, index=True, default="")
    phonetic_german = Column(String, nullable=False, index=True, default="")
    phonetic_cologne = Column(String, nullable=False, index=True, default="")
    consonant_key = Column(String, nullable=False, index=True, default="")

    # Relationship to addresses
    addresses = relationship("Address", back_populates="street", cascade="all, delete-orphan")
    # Relationship to street segments for reverse geocoding
    segments = relationship("StreetSegment", back_populates="street", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_street_name_city', 'name', 'city'),
        Index('idx_street_location', 'latitude', 'longitude'),
        Index('idx_street_normalized_name', 'normalized_name'),
        Index('idx_street_city_lower', text('LOWER(city)')),
        UniqueConstraint('name', 'postal_code', 'city', name='uq_street_name_city'),
    )


class Address(Base):
    """Address model for storing individual house numbers on streets."""

    __tablename__ = 'addresses'

    id = Column(Integer, primary_key=True, index=True)
    street_id = Column(Integer, ForeignKey('streets.id'), nullable=False, index=True)
    house_number = Column(String, nullable=False, index=True)
    # Specific latitude/longitude for this address
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    # Relationship to street
    street = relationship("Street", back_populates="addresses")

    __table_args__ = (
        Index('idx_street_house', 'street_id', 'house_number'),
        Index('idx_address_location', 'latitude', 'longitude'),
        UniqueConstraint('street_id', 'house_number', name='uq_address_street_house'),
    )


class StreetSegment(Base):
    """Street segment model for storing line segments of streets for reverse geocoding.
    
    Each street can have multiple segments representing its geometry. This allows
    efficient reverse geocoding by finding the nearest segment to a given point.
    """

    __tablename__ = 'street_segments'

    id = Column(Integer, primary_key=True, index=True)
    street_id = Column(Integer, ForeignKey('streets.id'), nullable=False, index=True)
    # Start point of segment
    start_lat = Column(Float, nullable=False)
    start_lon = Column(Float, nullable=False)
    # End point of segment
    end_lat = Column(Float, nullable=False)
    end_lon = Column(Float, nullable=False)
    # Bounding box for efficient spatial queries
    min_lat = Column(Float, nullable=False, index=True)
    max_lat = Column(Float, nullable=False, index=True)
    min_lon = Column(Float, nullable=False, index=True)
    max_lon = Column(Float, nullable=False, index=True)

    # Relationship to street
    street = relationship("Street", back_populates="segments")

    __table_args__ = (
        Index('idx_segment_street', 'street_id'),
        Index('idx_segment_bbox', 'min_lat', 'max_lat', 'min_lon', 'max_lon'),
        UniqueConstraint('street_id', 'start_lat', 'start_lon', 'end_lat', 'end_lon', name='uq_street_segment'),
    )


def _set_normalized_name(target: "Street") -> None:
    normalized_original = cast(str, getattr(target, "name", "") or "")
    target.normalized_name = normalize_compact(normalized_original)  # type: ignore[assignment]
    target.normalized_search = normalize_string(normalized_original)  # type: ignore[assignment]
    target.phonetic_german = german_phonetic_phrase(normalized_original)  # type: ignore[assignment]
    target.phonetic_cologne = cologne_phonetic_phrase(normalized_original)  # type: ignore[assignment]
    target.consonant_key = consonant_key(normalized_original)  # type: ignore[assignment]


@event.listens_for(Street, "before_insert")
def _street_before_insert(mapper, connection, target: "Street"):
    _set_normalized_name(target)


@event.listens_for(Street, "before_update")
def _street_before_update(mapper, connection, target: "Street"):
    if getattr(target, "name", None) is not None:
        _set_normalized_name(target)
