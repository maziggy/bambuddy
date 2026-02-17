"""Printer model normalization utilities.

Converts 3MF printer model names (e.g., "Bambu Lab X1 Carbon") to
normalized short names (e.g., "X1C") that match database storage.
"""

# Map from 3MF printer_model strings to normalized short names
PRINTER_MODEL_MAP = {
    "Bambu Lab X1 Carbon": "X1C",
    "Bambu Lab X1": "X1",
    "Bambu Lab X1E": "X1E",
    "Bambu Lab P1S": "P1S",
    "Bambu Lab P1P": "P1P",
    "Bambu Lab P2S": "P2S",
    "Bambu Lab A1": "A1",
    "Bambu Lab A1 Mini": "A1 Mini",
    "Bambu Lab A1 mini": "A1 Mini",
    "Bambu Lab H2D": "H2D",
    "Bambu Lab H2D Pro": "H2D Pro",
    "Bambu Lab H2C": "H2C",
    "Bambu Lab H2S": "H2S",
}

# Map from printer_model_id (internal codes in slice_info.config) to short names
# These are the codes Bambu Studio uses internally
PRINTER_MODEL_ID_MAP = {
    # X1 series
    "C11": "X1C",
    "C12": "X1",
    "C13": "X1E",
    # P1 series
    "P1P": "P1P",
    "P1S": "P1S",
    # P2 series
    "P2S": "P2S",
    # A1 series
    "A11": "A1",
    "A12": "A1 Mini",
    "N1": "A1",
    "N2S": "A1 Mini",
    "A04": "A1 Mini",
    # H2 series (Office/H series)
    "O1D": "H2D",
    "O1E": "H2D Pro",  # Some devices report O1E
    "O2D": "H2D Pro",  # Some devices report O2D
    "O1C": "H2C",
    "O1S": "H2S",
}


# Rod/rail type classification for maintenance tasks.
# Carbon rods: X1, P1, P2S series (CoreXY with carbon fiber rods)
# Linear rails: A1, H2 series (linear rail motion system)
# Values must be uppercase with spaces stripped for normalized comparison.
CARBON_ROD_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "X1",
        "X1C",
        "X1E",
        "P1P",
        "P1S",
        "P2S",
        # Internal codes
        "C11",  # X1C
        "C12",  # X1
        "C13",  # X1E
        "N7",  # P2S
    ]
)

LINEAR_RAIL_MODELS = frozenset(
    [
        # Display names (uppercase, no spaces)
        "A1",
        "A1MINI",
        "H2D",
        "H2DPRO",
        "H2C",
        "H2S",
        # Internal codes
        "N1",  # A1
        "N2S",  # A1 Mini
        "A04",  # A1 Mini (alternate)
        "A11",  # A1
        "A12",  # A1 Mini
        "O1D",  # H2D
        "O1E",  # H2D Pro
        "O2D",  # H2D Pro (alternate)
        "O1C",  # H2C
        "O1S",  # H2S
    ]
)


def get_rod_type(model: str | None) -> str | None:
    """Return the rod/rail type for a printer model.

    Returns:
        "carbon" for X1/P1/P2S series (carbon fiber rods),
        "linear_rail" for A1/H2 series (linear rails),
        None for unknown models.
    """
    if not model:
        return None
    normalized = model.strip().upper().replace(" ", "").replace("-", "")
    if normalized in CARBON_ROD_MODELS:
        return "carbon"
    if normalized in LINEAR_RAIL_MODELS:
        return "linear_rail"
    return None


def normalize_printer_model_id(model_id: str | None) -> str | None:
    """Convert printer_model_id (internal code) to normalized short name.

    Args:
        model_id: The printer_model_id from slice_info.config (e.g., "C11", "O1D")

    Returns:
        Normalized short name (e.g., "X1C", "H2D") or the original ID if unknown.
    """
    if not model_id:
        return None

    # Check known mappings
    if model_id in PRINTER_MODEL_ID_MAP:
        return PRINTER_MODEL_ID_MAP[model_id]

    # Return original if unknown (might already be a short name)
    return model_id


def normalize_printer_model(raw_model: str | None) -> str | None:
    """Convert 3MF printer_model to normalized short name.

    Args:
        raw_model: The printer_model string from 3MF metadata
            (e.g., "Bambu Lab X1 Carbon")

    Returns:
        Normalized short name (e.g., "X1C") or None if input is empty.
        Unknown models have "Bambu Lab " prefix stripped.
    """
    if not raw_model:
        return None

    # Check known mappings first
    if raw_model in PRINTER_MODEL_MAP:
        return PRINTER_MODEL_MAP[raw_model]

    # Strip "Bambu Lab " prefix for unknown models
    stripped = raw_model.replace("Bambu Lab ", "").strip()
    return stripped or None
