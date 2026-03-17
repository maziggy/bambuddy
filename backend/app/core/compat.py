"""Compatibility shims for older Python versions."""

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

    class StrEnum(str, Enum):
        """Drop-in replacement for enum.StrEnum on Python < 3.11."""
