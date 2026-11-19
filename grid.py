"""
Area tiling for the Google Maps scraper.

Google Maps shows at most ~120 places for one search, however big the area.
To cover a whole city (or district) we split its bounding box into small map
tiles, search each tile, and split any tile that comes back "full" into four
smaller tiles — so dense neighbourhoods get searched finely and empty ones
are covered with a single search.
"""

import math
import re
from urllib.parse import quote_plus

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "GoogleMapScraper-gridsearch/1.0 (personal data collection script)"

MIN_ZOOM, MAX_ZOOM = 12, 19


# --------------------------------------------------------------------------- geocoding

def geocode_bbox(area, timeout=15):
    """Look up the bounding box of *area* (e.g. 'Mumbai') via OpenStreetMap Nominatim.

    Returns (south, west, north, east) or None if nothing was found.
    """
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": area, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=timeout,
        )
        r.raise_for_status()
        results = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not results:
        return None
    south, north, west, east = (float(x) for x in results[0]["boundingbox"])
    return south, west, north, east


def parse_bbox(text):
    """Parse 'south,west,north,east' into a tuple of floats."""
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox needs 4 numbers: south,west,north,east")
    south, west, north, east = parts
    if not (south < north and west < east):
        raise ValueError("bbox must satisfy south < north and west < east")
    return south, west, north, east


# --------------------------------------------------------------------------- cells

def _deg_per_km(lat):
    return 1 / 111.0, 1 / (111.0 * max(math.cos(math.radians(lat)), 0.01))


def make_cells(bbox, cell_km):
    """Cover *bbox* with square-ish cells about *cell_km* wide.

    A cell is a dict: {lat, lng (centre), km (side length), depth}.
    """
    south, west, north, east = bbox
    mid_lat = (south + north) / 2
    dlat_km, dlng_km = _deg_per_km(mid_lat)
    step_lat, step_lng = cell_km * dlat_km, cell_km * dlng_km

    rows = max(1, math.ceil((north - south) / step_lat))
    cols = max(1, math.ceil((east - west) / step_lng))
    cells = []
    for r in range(rows):
        for c in range(cols):
            lat = south + (r + 0.5) * (north - south) / rows
            lng = west + (c + 0.5) * (east - west) / cols
            cells.append({"lat": lat, "lng": lng, "km": cell_km, "depth": 0})
    return cells


def subdivide(cell):
    """Split a cell into 4 half-size cells."""
    dlat_km, dlng_km = _deg_per_km(cell["lat"])
    q = cell["km"] / 4
    out = []
    for sy in (-1, 1):
        for sx in (-1, 1):
            out.append({
                "lat": cell["lat"] + sy * q * dlat_km,
                "lng": cell["lng"] + sx * q * dlng_km,
                "km": cell["km"] / 2,
                "depth": cell["depth"] + 1,
            })
    return out


def cell_key(cell):
    return f"{cell['lat']:.5f},{cell['lng']:.5f},{cell['km']:.3f}"


def zoom_for(cell):
    """Map zoom level at which the cell just fits in a ~800 px tall map view."""
    metres = cell["km"] * 1000
    z = math.log2(156543.03 * math.cos(math.radians(cell["lat"])) * 800 / metres)
    return max(MIN_ZOOM, min(MAX_ZOOM, int(z)))


def cell_url(query, cell):
    q = quote_plus(query)
    return (
        f"https://www.google.com/maps/search/{q}/"
        f"@{cell['lat']:.6f},{cell['lng']:.6f},{zoom_for(cell)}z?hl=en"
    )


# --------------------------------------------------------------------------- links

_PLACE_ID_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)")
_COORD_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")


def place_key(href):
    """Stable identity of a place link (Google's feature id, else the path)."""
    m = _PLACE_ID_RE.search(href)
    return m.group(1) if m else href.split("?")[0]


def link_coords(href):
    m = _COORD_RE.search(href)
    return (float(m.group(1)), float(m.group(2))) if m else None


def in_bbox(coords, bbox, margin_km=0.0):
    """True if (lat, lng) lies inside bbox (grown by margin_km). Unknown coords pass."""
    if coords is None:
        return True
    south, west, north, east = bbox
    dlat_km, dlng_km = _deg_per_km((south + north) / 2)
    lat, lng = coords
    return (
        south - margin_km * dlat_km <= lat <= north + margin_km * dlat_km
        and west - margin_km * dlng_km <= lng <= east + margin_km * dlng_km
    )


def in_cell(coords, cell):
    """True if (lat, lng) lies inside the square tile *cell*."""
    if coords is None:
        return False
    dlat_km, dlng_km = _deg_per_km(cell["lat"])
    half = cell["km"] / 2
    return (abs(coords[0] - cell["lat"]) <= half * dlat_km
            and abs(coords[1] - cell["lng"]) <= half * dlng_km)


# --------------------------------------------------------------------------- boundary polygons
# A "polygon" here is a list of rings [[(lat, lng), ...], ...]; the first ring is the outline and
# any others are holes. An area is a list of such polygons (a MultiPolygon).

def _geojson_to_polys(geom):
    """GeoJSON Polygon/MultiPolygon -> list of polygons of (lat, lng) rings."""
    if not geom:
        return []
    if geom["type"] == "Polygon":
        raw = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        raw = geom["coordinates"]
    else:
        return []
    return [[[(pt[1], pt[0]) for pt in ring] for ring in poly] for poly in raw]


def _geocode_once(area, timeout=20):
    """Look up *area* on OpenStreetMap Nominatim, preferring its true boundary polygon.

    Returns {"name", "bbox", "polys", "exact"} or None. "exact" is False when OSM only
    knows the place as a point, in which case the box is a rough square around it.
    """
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": area, "format": "jsonv2", "limit": 10,
                    "polygon_geojson": 1, "polygon_threshold": 0.0005},
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=timeout,
        )
        r.raise_for_status()
        results = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not results:
        return None

    def bbox_of(res):
        south, north, west, east = (float(x) for x in res["boundingbox"])
        return south, west, north, east

    # 1) first real boundary polygon (administrative areas beat everything else)
    ranked = sorted(
        (x for x in results if (x.get("geojson") or {}).get("type") in ("Polygon", "MultiPolygon")),
        key=lambda x: 0 if x.get("category") == "boundary" else 1,
    )
    if ranked:
        res = ranked[0]
        polys = _geojson_to_polys(res["geojson"])
        if polys:
            return {"name": res.get("display_name", area), "bbox": bbox_of(res),
                    "polys": polys, "exact": True}

    # 2) point-only: fall back to the rough box
    res = results[0]
    return {"name": res.get("display_name", area), "bbox": bbox_of(res), "polys": None, "exact": False}


def _ring_contains(ring, lat, lng):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_area(coords, polys):
    """True if (lat, lng) is inside the area (None = unknown coords -> True)."""
    if coords is None or not polys:
        return True
    lat, lng = coords
    for poly in polys:
        # even-odd across outline + holes
        if sum(_ring_contains(ring, lat, lng) for ring in poly) % 2 == 1:
            return True
    return False


def area_bbox(polys):
    lats = [p[0] for poly in polys for ring in poly for p in ring]
    lngs = [p[1] for poly in polys for ring in poly for p in ring]
    return min(lats), min(lngs), max(lats), max(lngs)


def cell_touches_area(cell, polys):
    """True if the square tile overlaps the area (checked with sample points + outline vertices)."""
    if not polys:
        return True
    dlat_km, dlng_km = _deg_per_km(cell["lat"])
    hl, hg = cell["km"] / 2 * dlat_km, cell["km"] / 2 * dlng_km
    for fy in (-1, -0.5, 0, 0.5, 1):
        for fx in (-1, -0.5, 0, 0.5, 1):
            if point_in_area((cell["lat"] + fy * hl, cell["lng"] + fx * hg), polys):
                return True
    for poly in polys:                       # an outline poking into a tile that has no sample inside
        for lat, lng in poly[0]:
            if abs(lat - cell["lat"]) <= hl and abs(lng - cell["lng"]) <= hg:
                return True
    return False


def geocode_area(area, timeout=20):
    """Like _geocode_once, but if OSM only knows *area* as a point, also try
    '<area> district' and '<area> city' to find a real boundary."""
    import time
    first = None
    for i, q in enumerate((area, f"{area} district", f"{area} city")):
        if i:
            time.sleep(1.1)                      # Nominatim allows ~1 request/second
        res = _geocode_once(q, timeout)
        if res is None:
            continue
        if res["exact"]:
            return res
        first = first or res
    return first


def load_polygon_file(path):
    """Read a GeoJSON file (Polygon, MultiPolygon, Feature or FeatureCollection).

    Returns (bbox, polys). Lets you draw your own boundary, e.g. at geojson.io.
    """
    import json
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    geoms = []

    def walk(node):
        t = node.get("type")
        if t == "FeatureCollection":
            for ft in node.get("features", []):
                walk(ft)
        elif t == "Feature":
            walk(node.get("geometry") or {})
        elif t == "GeometryCollection":
            for gm in node.get("geometries", []):
                walk(gm)
        elif t in ("Polygon", "MultiPolygon"):
            geoms.append(node)

    walk(data)
    polys = [p for gm in geoms for p in _geojson_to_polys(gm)]
    if not polys:
        raise ValueError("no Polygon found in the GeoJSON file")
    return area_bbox(polys), polys
