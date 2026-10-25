"""Kept for the modules that import StrEnum from here.

Python 3.11 is the minimum, so this is enum.StrEnum itself; new code can
import it from enum directly.
"""

from enum import StrEnum

__all__ = ["StrEnum"]
