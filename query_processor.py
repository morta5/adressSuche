"""Advanced query processing and understanding."""

import re
from typing import List, Tuple, Dict, Set
from dataclasses import dataclass


@dataclass
class QueryToken:
    """Represents a token in the query."""
    text: str
    normalized: str
    position: int
    is_suffix: bool = False
    is_number: bool = False
    is_abbreviation: bool = False


class QueryProcessor:
    """
    Advanced query processing with German-specific optimizations.
    Handles abbreviations, synonyms, and context understanding.
    """
    
    # German street suffixes (expanded from original)
    STREET_SUFFIXES = {
        # Straße variants
        'straße', 'strasse', 'str', 'str.', 's',
        # Weg variants
        'weg', 'w', 'we',
        # Platz variants
        'platz', 'pl', 'pl.',
        # Gasse variants
        'gasse', 'g', 'ga',
        # Allee variants
        'allee', 'al', 'al.',
        # Ring variants
        'ring', 'r', 'ri',
        # Damm variants
        'damm', 'da',
        # Others
        'hof', 'park', 'berg', 'brücke', 'brucke', 'kirche',
        'markt', 'tor', 'bad', 'feld', 'grund', 'hang', 'tal',
        'wall', 'steig', 'pfad', 'winkel', 'ufer', 'promenade',
        'chaussee', 'zeile', 'graben', 'stieg', 'anger', 'plan'
    }
    
    # Suffix expansions with priorities
    SUFFIX_EXPANSIONS = {
        's': ['straße', 'strasse'],
        'str': ['straße', 'strasse'],
        'str.': ['straße', 'strasse'],
        'w': ['weg'],
        'we': ['weg'],
        'pl': ['platz'],
        'pl.': ['platz'],
        'g': ['gasse'],
        'ga': ['gasse'],
        'al': ['allee'],
        'al.': ['allee'],
        'r': ['ring'],
        'ri': ['ring'],
        'da': ['damm'],
    }
    
    # Common abbreviations in German addresses
    ABBREVIATIONS = {
        'st': ['sankt', 'saint', 'straße'],
        'dr': ['doktor', 'doctor'],
        'prof': ['professor'],
        'v': ['von', 'vom'],
        'a': ['am', 'an', 'auf'],
        'd': ['der', 'die', 'das', 'den', 'dem'],
        'gr': ['groß', 'große', 'großer', 'großen'],
        'kl': ['klein', 'kleine', 'kleiner', 'kleinen'],
        'alt': ['alte', 'alter', 'alten'],
        'neu': ['neue', 'neuer', 'neuen'],
        'ob': ['ober', 'obere', 'oberer'],
        'unt': ['unter', 'untere', 'unterer'],
    }
    
    # Synonyms and related terms
    SYNONYMS = {
        'straße': ['strasse', 'str'],
        'strasse': ['straße', 'str'],
        'platz': ['pl'],
        'gasse': ['g'],
        'weg': ['w'],
        'allee': ['al'],
    }
    
    @classmethod
    def tokenize(cls, query: str) -> List[QueryToken]:
        """
        Tokenize query into meaningful parts.
        
        Args:
            query: Search query
            
        Returns:
            List of query tokens
        """
        query = query.strip().lower()
        
        # Split by spaces and hyphens, but keep track of positions
        parts = re.split(r'([\s\-]+)', query)
        
        tokens = []
        position = 0
        
        for part in parts:
            if not part or part.isspace() or part == '-':
                continue
            
            normalized = cls._normalize_token(part)
            is_suffix = normalized in cls.STREET_SUFFIXES
            is_number = part.isdigit()
            is_abbr = normalized in cls.ABBREVIATIONS
            
            token = QueryToken(
                text=part,
                normalized=normalized,
                position=position,
                is_suffix=is_suffix,
                is_number=is_number,
                is_abbreviation=is_abbr
            )
            tokens.append(token)
            position += 1
        
        return tokens
    
    @classmethod
    def expand_query(cls, query: str) -> List[str]:
        """
        Generate expanded query variants with abbreviations, synonyms, etc.
        
        Args:
            query: Original query
            
        Returns:
            List of query variants
        """
        variants = [query]
        tokens = cls.tokenize(query)
        
        if not tokens:
            return variants
        
        # Remove common suffixes from the query for better matching
        # E.g., "kieler straße" -> "kieler", "kampstraße" -> "kampstr"
        if tokens and tokens[-1].is_suffix:
            # Remove the last suffix token
            base_tokens = tokens[:-1]
            if base_tokens:
                base_query = ' '.join(t.text for t in base_tokens)
                variants.insert(0, base_query)  # Prioritize suffix-removed version
        
        # Expand abbreviations
        abbr_variants = cls._expand_abbreviations(tokens)
        variants.extend(abbr_variants)
        
        # Expand suffixes
        suffix_variants = cls._expand_suffixes(tokens)
        variants.extend(suffix_variants)
        
        # Generate synonym variants
        synonym_variants = cls._expand_synonyms(tokens)
        variants.extend(synonym_variants)
        
        # Generate hyphen variants
        hyphen_variants = cls._generate_hyphen_variants(query)
        variants.extend(hyphen_variants)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_variants = []
        for variant in variants:
            normalized = variant.lower().strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_variants.append(variant)
        
        return unique_variants
    
    @classmethod
    def _normalize_token(cls, token: str) -> str:
        """Normalize a single token."""
        token = token.lower().strip()
        token = token.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
        token = re.sub(r'[^\w]', '', token)
        return token
    
    @classmethod
    def _expand_abbreviations(cls, tokens: List[QueryToken]) -> List[str]:
        """Expand abbreviations in tokens."""
        variants = []
        
        for i, token in enumerate(tokens):
            if token.is_abbreviation and token.normalized in cls.ABBREVIATIONS:
                expansions = cls.ABBREVIATIONS[token.normalized]
                
                for expansion in expansions:
                    # Create variant with expanded abbreviation
                    new_tokens = [t.text for t in tokens]
                    new_tokens[i] = expansion
                    variants.append(' '.join(new_tokens))
        
        return variants
    
    @classmethod
    def _expand_suffixes(cls, tokens: List[QueryToken]) -> List[str]:
        """Expand street suffixes."""
        variants = []
        
        if not tokens:
            return variants
        
        # Check last token for suffix expansion
        last_token = tokens[-1]
        if last_token.normalized in cls.SUFFIX_EXPANSIONS:
            expansions = cls.SUFFIX_EXPANSIONS[last_token.normalized]
            
            for expansion in expansions:
                new_tokens = [t.text for t in tokens[:-1]]
                new_tokens.append(expansion)
                variants.append(' '.join(new_tokens))
        
        # Also check if query ends with partial suffix
        query_text = ' '.join(t.text for t in tokens)
        for abbr, expansions in cls.SUFFIX_EXPANSIONS.items():
            if query_text.endswith(abbr):
                base = query_text[:-len(abbr)]
                for expansion in expansions:
                    variants.append(base + expansion)
        
        return variants
    
    @classmethod
    def _expand_synonyms(cls, tokens: List[QueryToken]) -> List[str]:
        """Expand synonyms in tokens."""
        variants = []
        
        for i, token in enumerate(tokens):
            if token.normalized in cls.SYNONYMS:
                synonyms = cls.SYNONYMS[token.normalized]
                
                for synonym in synonyms:
                    new_tokens = [t.text for t in tokens]
                    new_tokens[i] = synonym
                    variants.append(' '.join(new_tokens))
        
        return variants
    
    @classmethod
    def _generate_hyphen_variants(cls, query: str) -> List[str]:
        """Generate hyphen/space variants."""
        variants = []
        
        # Space to hyphen
        if ' ' in query:
            variants.append(query.replace(' ', '-'))
        
        # Hyphen to space
        if '-' in query:
            variants.append(query.replace('-', ' '))
        
        return variants
    
    @classmethod
    def extract_intent(cls, query: str) -> Dict[str, any]:
        """
        Extract user intent from query.
        
        Args:
            query: Search query
            
        Returns:
            Dictionary with intent information
        """
        tokens = cls.tokenize(query)
        
        intent = {
            'query': query,
            'tokens': len(tokens),
            'has_suffix': any(t.is_suffix for t in tokens),
            'has_abbreviation': any(t.is_abbreviation for t in tokens),
            'has_number': any(t.is_number for t in tokens),
            'is_short': len(query) < 3,
            'is_partial': False,
            'likely_complete': False
        }
        
        # Check if query looks partial or complete
        if tokens:
            last_token = tokens[-1]
            
            # If ends with a full suffix, likely complete
            if last_token.normalized in ['straße', 'strasse', 'weg', 'platz', 'gasse', 'allee']:
                intent['likely_complete'] = True
            
            # If ends with abbreviation or short suffix, likely partial
            elif last_token.normalized in ['s', 'str', 'w', 'pl', 'g', 'al']:
                intent['is_partial'] = True
            
            # Very short query is likely partial
            elif len(query) < 4:
                intent['is_partial'] = True
        
        return intent
