"""Natural (numeric-aware) string sorting, e.g. "Drybox 2" before "Drybox 10"."""

import re

_CHUNK_RE = re.compile(r"(\d+)")


def natural_sort_key(value: str) -> tuple:
    """Sort key that orders embedded numbers by value, not lexicographically.

    A plain string sort puts "Drybox 10" before "Drybox 2" (character by
    character, "1" < "2"). Splitting into alternating text/digit runs and
    comparing the digit runs as integers instead gets "Drybox 2" before
    "Drybox 10", without assuming every name follows a fixed "prefix N"
    shape. `_CHUNK_RE.split` always yields text chunks at even indices and
    digit chunks at odd indices for any input, so the type at a given index
    is consistent across every key this function produces — two keys can be
    compared without ever hitting a str-vs-int mismatch mid-tuple.
    """
    return tuple(int(chunk) if chunk.isdigit() else chunk.lower() for chunk in _CHUNK_RE.split(value))
