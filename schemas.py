"""Pydantic schemas for API request/response validation."""

from pydantic import BaseModel, ConfigDict
from typing import Optional


class StreetAutocompleteResponse(BaseModel):
    """Response model for street autocomplete."""

    model_config = ConfigDict(from_attributes=True)

    street_id: int
    name: str
    city: str
    postal_code: Optional[str] = None
    latitude: float
    longitude: float
    distance_km: Optional[float] = None
    match_score: Optional[float] = None


class AddressValidationResponse(BaseModel):
    """Response model for address validation."""

    model_config = ConfigDict(from_attributes=True)

    exists: bool
    address_id: Optional[int] = None
    street_name: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    house_number: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    distance_km: Optional[float] = None
    # Populated by /validate_voice when the match came from phonetic geo-scan
    # rather than exact/prefix/phonetic-prefix lookup. Values: "exact", "phonetic_voice"
    match_type: Optional[str] = None
    # Phonetic similarity score [0.0–1.0] set when match_type="phonetic_voice"
    confidence: Optional[float] = None
