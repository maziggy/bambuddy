"""Color comparison utilities for RFID/firmware color matching."""


def colors_similar(hex_a: str, hex_b: str, threshold: int = 50) -> bool:
    """Compare two RRGGBB(AA) hex colors with tolerance for RFID/firmware variations.

    Uses Euclidean RGB distance. Alpha channel (bytes 7-8) is ignored.
    Default threshold of 50 accommodates typical RFID read variations
    (e.g. 7CC4D5 vs 56B7E6 = distance ~43.6) while rejecting clearly
    different colors (e.g. red vs blue = distance ~360).
    """
    a = hex_a.strip().upper()
    b = hex_b.strip().upper()
    if a == b:
        return True
    if len(a) < 6 or len(b) < 6:
        return False
    try:
        ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    except ValueError:
        return False
    dist = ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5
    return dist <= threshold
