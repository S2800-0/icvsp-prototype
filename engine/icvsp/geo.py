"""Small-distance geometry. An equirectangular approximation is accurate to well
under a metre over the few hundred metres the engine ever compares."""
from __future__ import annotations

import math

EARTH_R = 6_371_000.0


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Move a lat/lon point by metres north and east."""
    dlat = north_m / EARTH_R
    dlon = east_m / (EARTH_R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def local_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """(east, north) in metres of a point relative to an origin."""
    x = math.radians(lon - lon0) * EARTH_R * math.cos(math.radians((lat + lat0) / 2))
    y = math.radians(lat - lat0) * EARTH_R
    return x, y


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x, y = local_xy(lat2, lon2, lat1, lon1)
    return math.hypot(x, y)


def heading_diff(a: float, b: float) -> float:
    """Smallest angle between two compass headings, 0–180."""
    return abs((a - b + 180) % 360 - 180)


def along_and_across(lat: float, lon: float, lat0: float, lon0: float, heading_deg: float) -> tuple[float, float]:
    """Position of a point relative to an origin, measured along a heading
    (positive = ahead of the origin) and across it (positive = to the right)."""
    x, y = local_xy(lat, lon, lat0, lon0)
    h = math.radians(heading_deg)
    along = x * math.sin(h) + y * math.cos(h)
    across = x * math.cos(h) - y * math.sin(h)
    return along, across
