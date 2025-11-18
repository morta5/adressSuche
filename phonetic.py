"""Phonetic matching algorithms for German street names."""

import re
from typing import List, Tuple


class GermanPhonetic:
    """
    German-specific phonetic encoding similar to Soundex but optimized for German.
    Handles umlauts, compound words, and German-specific phonetic patterns.
    """
    
    # German phonetic rules
    REPLACEMENTS = [
        # Umlauts and special characters
        ('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss'),
        # Double consonants
        ('bb', 'b'), ('cc', 'c'), ('dd', 'd'), ('ff', 'f'),
        ('gg', 'g'), ('hh', 'h'), ('kk', 'k'), ('ll', 'l'),
        ('mm', 'm'), ('nn', 'n'), ('pp', 'p'), ('rr', 'r'),
        ('ss', 's'), ('tt', 't'), ('zz', 'z'),
        # German-specific patterns
        ('ph', 'f'), ('th', 't'), ('ch', 'x'), ('sch', 'x'),
        ('ck', 'k'), ('qu', 'kw'), ('chs', 'ks'), ('dt', 't'),
        ('ts', 'z'), ('tz', 'z'), ('pf', 'f'),
        # Silent h
        ('ah', 'a'), ('eh', 'e'), ('ih', 'i'), ('oh', 'o'), ('uh', 'u'),
        # ei/ai sound
        ('ei', 'ai'), ('ey', 'ai'), ('ay', 'ai'),
        # eu/äu sound
        ('eu', 'oi'), ('äu', 'oi'),
        # ie sound
        ('ie', 'i'),
        # V variations
        ('v', 'f'),
        # C variations
        ('c', 'k'),
    ]
    
    @classmethod
    def encode(cls, word: str) -> str:
        """
        Encode a German word phonetically.
        
        Args:
            word: German word to encode
            
        Returns:
            Phonetic encoding
        """
        if not word:
            return ""
            
        # Convert to lowercase
        word = word.lower().strip()
        
        # Remove non-alphabetic characters except spaces and hyphens
        word = re.sub(r'[^a-zäöüß\s\-]', '', word)
        
        # Apply phonetic replacements
        for old, new in cls.REPLACEMENTS:
            word = word.replace(old, new)
        
        # Remove vowels except first letter
        if len(word) > 0:
            first_char = word[0]
            rest = word[1:]
            rest = re.sub(r'[aeiou]', '', rest)
            word = first_char + rest
        
        # Remove remaining duplicates
        result = []
        prev_char = ''
        for char in word:
            if char != prev_char:
                result.append(char)
                prev_char = char
        
        return ''.join(result)
    
    @classmethod
    def similarity(cls, word1: str, word2: str) -> float:
        """
        Calculate phonetic similarity between two words.
        
        Args:
            word1: First word
            word2: Second word
            
        Returns:
            Similarity score between 0.0 and 1.0
        """
        enc1 = cls.encode(word1)
        enc2 = cls.encode(word2)
        
        if not enc1 or not enc2:
            return 0.0
        
        if enc1 == enc2:
            return 1.0
        
        # Calculate Levenshtein-based similarity on encodings
        max_len = max(len(enc1), len(enc2))
        if max_len == 0:
            return 1.0
        
        distance = cls._levenshtein(enc1, enc2)
        return max(0.0, 1.0 - (distance / max_len))
    
    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        """Calculate Levenshtein distance."""
        if len(s1) > len(s2):
            s1, s2 = s2, s1
        
        prev_row = list(range(len(s2) + 1))
        curr_row = [0] * (len(s2) + 1)
        
        for i in range(1, len(s1) + 1):
            curr_row[0] = i
            for j in range(1, len(s2) + 1):
                cost = 0 if s1[i - 1] == s2[j - 1] else 1
                curr_row[j] = min(
                    prev_row[j] + 1,      # deletion
                    curr_row[j - 1] + 1,  # insertion
                    prev_row[j - 1] + cost # substitution
                )
            prev_row, curr_row = curr_row, prev_row
        
        return prev_row[len(s2)]


class ColognePhonetic:
    """
    Kölner Phonetik (Cologne Phonetic) - German phonetic algorithm.
    More accurate than Soundex for German names.
    """
    
    @classmethod
    def encode(cls, word: str) -> str:
        """
        Encode word using Cologne Phonetic algorithm.
        
        Args:
            word: German word to encode
            
        Returns:
            Phonetic code
        """
        if not word:
            return ""
        
        word = word.lower().strip()
        word = word.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
        
        # Remove non-alphabetic characters
        word = re.sub(r'[^a-z]', '', word)
        
        if not word:
            return ""
        
        code = []
        prev_code = ''
        
        for i, char in enumerate(word):
            curr_code = cls._char_code(char, i, word)
            
            # Skip duplicates and '-'
            if curr_code and curr_code != '-' and curr_code != prev_code:
                code.append(curr_code)
                prev_code = curr_code
        
        return ''.join(code)
    
    @classmethod
    def _char_code(cls, char: str, pos: int, word: str) -> str:
        """Get phonetic code for a character."""
        # A, E, I, J, O, U, Y = 0
        if char in 'aeijouы':
            return '0'
        
        # H = -
        if char == 'h':
            return '-'
        
        # B = 1
        if char == 'b':
            return '1'
        
        # P (not before H) = 1
        if char == 'p':
            if pos + 1 < len(word) and word[pos + 1] == 'h':
                return '3'
            return '1'
        
        # D, T (not before C, S, Z) = 2
        if char in 'dt':
            if pos + 1 < len(word) and word[pos + 1] in 'csz':
                return '8'
            return '2'
        
        # F, V, W = 3
        if char in 'fvw':
            return '3'
        
        # G, K, Q = 4
        if char in 'gkq':
            return '4'
        
        # C = 4 or 8
        if char == 'c':
            if pos == 0:
                if pos + 1 < len(word) and word[pos + 1] in 'ahkloqrux':
                    return '4'
                return '8'
            if pos > 0 and word[pos - 1] in 'sz':
                return '8'
            if pos + 1 < len(word) and word[pos + 1] in 'ahkoqux':
                return '4'
            return '8'
        
        # X (not after C, K, Q) = 48
        if char == 'x':
            if pos > 0 and word[pos - 1] in 'ckq':
                return '8'
            return '48'
        
        # L = 5
        if char == 'l':
            return '5'
        
        # M, N = 6
        if char in 'mn':
            return '6'
        
        # R = 7
        if char == 'r':
            return '7'
        
        # S, Z = 8
        if char in 'sz':
            return '8'
        
        return ''


def _tokenize(text: str) -> List[str]:
    """Split text into lowercase tokens for phonetic processing."""

    if not text:
        return []
    tokens = re.split(r"[\s\-]+", text.lower())
    return [t for t in tokens if t]


def german_phonetic_phrase(text: str) -> str:
    """Return deterministic German phonetic code for complete phrases."""

    codes = [GermanPhonetic.encode(token) for token in _tokenize(text)]
    return ''.join(code for code in codes if code)


def cologne_phonetic_phrase(text: str) -> str:
    """Return deterministic Cologne phonetic code for complete phrases."""

    codes = [ColognePhonetic.encode(token) for token in _tokenize(text)]
    return ''.join(code for code in codes if code)


def phonetic_forms(text: str) -> Tuple[str, str]:
    """Helper returning both phrase-level phonetic encodings."""

    return german_phonetic_phrase(text), cologne_phonetic_phrase(text)


def phonetic_match_score(query: str, street: str) -> float:
    """
    Calculate combined phonetic match score.
    
    Args:
        query: Search query
        street: Street name
        
    Returns:
        Phonetic similarity score (0.0 to 1.0)
    """
    # Try both phonetic algorithms and take the best score
    german_score = GermanPhonetic.similarity(query, street)

    # Cologne phonetic comparison
    cologne_query = ColognePhonetic.encode(query)
    cologne_street = ColognePhonetic.encode(street)
    
    if cologne_query and cologne_street:
        if cologne_query == cologne_street:
            cologne_score = 1.0
        else:
            max_len = max(len(cologne_query), len(cologne_street))
            distance = GermanPhonetic._levenshtein(cologne_query, cologne_street)
            cologne_score = max(0.0, 1.0 - (distance / max_len)) if max_len > 0 else 0.0
    else:
        cologne_score = 0.0
    
    # Return weighted average (Cologne is more accurate for German)
    return 0.4 * german_score + 0.6 * cologne_score
