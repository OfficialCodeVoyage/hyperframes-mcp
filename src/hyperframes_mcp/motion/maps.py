"""Map data for the motion library: Natural Earth turned into script files the map blocks load.

``python -m hyperframes_mcp.motion map-data REGION --source DIR`` reads Natural Earth GeoJSON files (public domain) from ``DIR``,
clips them to the region's window, projects them, simplifies them and writes ``assets/motion/data/map-REGION.js``: a
script that registers one object on ``window.__MOTION_MAPS["REGION"]`` (projected SVG path strings for land, borders,
first-level regions and rivers, the projection parameters, the window and its outline, and a table of cities).

Nothing here opens a network connection: the source files are downloaded by hand (``assets/motion/data/DATA.md`` lists
the addresses at a pinned commit and the sha256 of each file, which :func:`read_geojson` checks). The projections are
written twice, here and in ``assets/motion/js/map-lib.js``; ``tests/motion/test_map_lib.py`` runs that copy under node
and compares the two.

Map units: one unit is 1/130 degree of latitude at the projection's origin (``UNITS_PER_DEGREE``), x grows to the east
and y grows to the south (the direction of SVG), so a projected path can be drawn as it is.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from hyperframes_mcp.motion import tokens as token_module
from hyperframes_mcp.util.atomic import atomic_write_bytes

DATA_DIR = token_module.MOTION_DIR / "data"
UNITS_PER_DEGREE = 130.0
SCRIPT_VERSION = 1

NE_COMMIT = "ca96624a56bd078437bca8184e78163e5039ad19"
NE_RAW = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_COMMIT}/geojson"

# sha256 of every source file at NE_COMMIT: a file that differs is refused, so a regeneration is reproducible.
SOURCE_SHA256 = {
    "ne_10m_land": "1ac90796408bc6ad6911d69448485d3c4dbf2190370080368a09976e1c9f7416",
    "ne_10m_admin_0_boundary_lines_land": "74d9c16229c095fde65943a9919e337682f044bcebccb120764f38edf3b70f4a",
    "ne_10m_admin_1_states_provinces_lines": "1a1f30ccaaf4cc9c4bde34266f0b8cbb955d3a4cf254b756912255f2ec7c75b6",
    "ne_10m_rivers_lake_centerlines": "bb854a900ecbd3b408df46d5e16e3e0f974ba55993f9d8b5c26e855273c0905a",
    "ne_10m_rivers_europe": "90712202ac46985812e90840df9acd5101599a9c649868d5bc55ee96c2fedd4c",
    "ne_10m_populated_places_simple": "fd3fa867a320cbd5c5b6bb5bc550afeec2939fb2cef688e508007282a55ac42f",
    "ne_50m_land": "e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b",
    "ne_50m_admin_0_boundary_lines_land": "2faac4f6b34386f3d21b6e018cf151f241f00e5c936d44dd17d7d9bfb147fa48",
    "ne_50m_rivers_lake_centerlines": "f286e0ce978fde999ca2d7a78c764be08542e19b63cded52b05c12d5173ccc51",
    "ne_50m_populated_places_simple": "8e70756b39fae9bcdc1e332bfc510c024c5edd3a13203ffd20092ee37b61d978",
    "ne_110m_land": "9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9",
    "ne_110m_admin_0_boundary_lines_land": "d42479fd79552cca4eec7f85fcdca717a790d29ff06be7676f1af0568c6d3f7c",
    "ne_110m_populated_places_simple": "0dbd25c9ad8bd797ddf164b067f563be5c16be2c002254eb594862377963f9dc",
}

Window = tuple[float, float, float, float]  # lon min, lat min, lon max, lat max


class MapDataError(ValueError):
    """The map data cannot be built: an unknown region, a missing or changed source file."""


# -- projections -------------------------------------------------------------------------------------


def _natural_earth(lam: np.ndarray, phi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Natural Earth projection of the unit sphere (Patterson; the polynomial of PROJ's ``natearth``)."""
    p2 = phi * phi
    p4 = p2 * p2
    x = lam * (0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4)))
    y = phi * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)))
    return x, y


@dataclass(frozen=True)
class Projection:
    """One of the three projections of the library, by name and parameters.

    ``equirect``: plate carree scaled by cos(lat0), a Germany-sized region (distortion under 1% inside ten degrees of
    lat0). ``lcc``: Lambert conformal conic with two standard parallels, a continent in mid latitudes (Europe), true to
    shape. ``natearth``: Natural Earth, the whole world, a compromise that looks like an atlas. ``k`` is map units per
    degree of latitude at the origin.
    """

    kind: str
    lon0: float = 0.0
    lat0: float = 0.0
    lat1: float = 0.0
    lat2: float = 0.0
    k: float = UNITS_PER_DEGREE

    def __post_init__(self) -> None:
        if self.kind not in ("equirect", "lcc", "natearth"):
            raise MapDataError(f"unknown projection {self.kind!r}")

    @property
    def radius(self) -> float:
        """Map units per radian."""
        return self.k * 180.0 / math.pi

    def project(self, lon: Any, lat: Any) -> tuple[np.ndarray, np.ndarray]:
        """Map units (x east, y south) of longitudes and latitudes in degrees (scalars or arrays)."""
        lam = np.radians(np.asarray(lon, dtype=float) - self.lon0)
        phi = np.radians(np.asarray(lat, dtype=float))
        r = self.radius
        if self.kind == "equirect":
            phi0 = math.radians(self.lat0)
            return r * lam * math.cos(phi0), -r * (phi - phi0)
        if self.kind == "natearth":
            x, y = _natural_earth(lam, phi)
            return r * x, -r * y
        phi0, phi1, phi2 = (math.radians(v) for v in (self.lat0, self.lat1, self.lat2))
        n = math.log(math.cos(phi1) / math.cos(phi2)) / math.log(
            math.tan(math.pi / 4 + phi2 / 2) / math.tan(math.pi / 4 + phi1 / 2)
        )
        f = math.cos(phi1) * math.tan(math.pi / 4 + phi1 / 2) ** n / n
        rho = r * f / np.tan(math.pi / 4 + phi / 2) ** n
        rho0 = r * f / math.tan(math.pi / 4 + phi0 / 2) ** n
        theta = n * lam
        return rho * np.sin(theta), rho * np.cos(theta) - rho0

    def point(self, lon: float, lat: float) -> tuple[float, float]:
        """The map units of one longitude and latitude."""
        x, y = self.project(lon, lat)
        return float(x), float(y)

    def as_json(self) -> dict[str, Any]:
        """The parameters the block needs to repeat this projection."""
        if self.kind == "equirect":
            return {
                "type": "equirect",
                "lon0": self.lon0,
                "lat0": self.lat0,
                "k": self.k,
            }
        if self.kind == "lcc":
            return {
                "type": "lcc",
                "lon0": self.lon0,
                "lat0": self.lat0,
                "lat1": self.lat1,
                "lat2": self.lat2,
                "k": self.k,
            }
        return {"type": "natearth", "lon0": self.lon0, "k": self.k}


@dataclass(frozen=True)
class Region:
    """A region preset: how it is projected, which window of the world it keeps and how finely it is drawn."""

    projection: Projection
    window: Window
    resolution: str  # Natural Earth scale of the land, borders and rivers: "10m", "50m" or "110m"
    tolerance: float  # Douglas-Peucker tolerance in map units
    focus_country: str | None  # ISO 3166-1 alpha-3 code: the first-level boundaries of this country are kept
    rivers: tuple[str, ...]  # river names kept ("" for every river up to river_rank)
    river_rank: int  # NE scalerank limit when rivers is empty
    city_min_population: int
    city_limit: int
    world: bool  # the window is the whole globe: no outline, the sea fills what is beyond


REGIONS: dict[str, Region] = {
    # The window holds the wide first view of a Germany shot (24 degrees of longitude) with some margin.
    "germany": Region(
        projection=Projection("equirect", lon0=10.0, lat0=51.0),
        window=(-4.0, 45.0, 24.0, 57.0),
        resolution="10m",
        tolerance=0.15,
        focus_country="DEU",
        rivers=(
            "Rhein",
            "Rhine",
            "Elbe",
            "Weser",
            "Main",
            "Donau",
            "Danube",
            "Oder",
            "Odra",
            "Neckar",
            "Mosel",
            "Moselle",
            "Ems",
            "Labe",
        ),
        river_rank=0,
        city_min_population=150_000,
        city_limit=400,
        world=False,
    ),
    "europe": Region(
        projection=Projection("lcc", lon0=10.0, lat0=52.0, lat1=35.0, lat2=65.0),
        window=(-25.0, 33.0, 45.0, 72.0),
        resolution="50m",
        tolerance=0.5,
        focus_country=None,
        rivers=(),
        river_rank=6,
        city_min_population=300_000,
        city_limit=300,
        world=False,
    ),
    "world": Region(
        projection=Projection("natearth"),
        window=(-180.0, -58.0, 180.0, 84.0),
        resolution="110m",
        tolerance=1.0,
        focus_country=None,
        rivers=(),
        river_rank=-1,
        city_min_population=1_000_000,
        city_limit=300,
        world=True,
    ),
}


def region(name: str) -> Region:
    """The preset called ``name``; the error lists what there is."""
    if name not in REGIONS:
        raise MapDataError(f"no region {name!r}; available: {', '.join(sorted(REGIONS))}")
    return REGIONS[name]


# -- reading the sources -----------------------------------------------------------------------------


def source_stems(preset: Region) -> list[str]:
    """The Natural Earth file stems (no folder, no extension) that building ``preset`` reads."""
    res = preset.resolution
    stems = [f"ne_{res}_land", f"ne_{res}_admin_0_boundary_lines_land"]
    if preset.focus_country:
        stems.append(f"ne_{res}_admin_1_states_provinces_lines")
    if preset.rivers or preset.river_rank > 0:
        stems.append(f"ne_{res}_rivers_lake_centerlines")
        if res == "10m":
            stems.append("ne_10m_rivers_europe")
    stems.append(f"ne_{res}_populated_places_simple")
    return stems


def read_geojson(source: Path, stem: str) -> dict[str, Any]:
    """Read ``source/<stem>.geojson`` after checking its sha256 against the pinned one."""
    path = Path(source) / f"{stem}.geojson"
    if not path.is_file():
        raise MapDataError(f"{path} is missing: download {NE_RAW}/{stem}.geojson (see assets/motion/data/DATA.md)")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256[stem]:
        raise MapDataError(
            f"{path} is not the file of commit {NE_COMMIT[:8]}: sha256 {digest}, expected {SOURCE_SHA256[stem]}"
        )
    return json.loads(data)


def _lines(feature: dict[str, Any]) -> list[np.ndarray]:
    geometry = feature["geometry"]
    if geometry["type"] == "LineString":
        parts = [geometry["coordinates"]]
    elif geometry["type"] == "MultiLineString":
        parts = geometry["coordinates"]
    else:
        return []
    return [np.asarray(part, dtype=float)[:, :2] for part in parts if len(part) >= 2]


def _polygons(feature: dict[str, Any]) -> list[list[np.ndarray]]:
    """Each polygon of a feature as its rings (outer ring first)."""
    geometry = feature["geometry"]
    if geometry["type"] == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        return []
    return [[np.asarray(ring, dtype=float)[:, :2] for ring in poly] for poly in polygons]


# -- clipping ----------------------------------------------------------------------------------------


def _clip_against(points: np.ndarray, axis: int, value: float, keep_greater: bool) -> np.ndarray:
    """One Sutherland-Hodgman pass: the ring's part on one side of an axis-aligned line."""
    if len(points) == 0:
        return points
    d = points[:, axis] - value
    inside = d >= 0 if keep_greater else d <= 0
    nxt_points = np.roll(points, -1, axis=0)
    nxt_d = np.roll(d, -1)
    crossing = inside != np.roll(inside, -1)
    t = np.zeros(len(points))
    t[crossing] = d[crossing] / (d[crossing] - nxt_d[crossing])
    inter = points + t[:, None] * (nxt_points - points)
    out = np.empty((2 * len(points), 2))
    out[0::2] = points
    out[1::2] = inter
    keep = np.empty(2 * len(points), dtype=bool)
    keep[0::2] = inside
    keep[1::2] = crossing
    return out[keep]


def clip_ring(ring: np.ndarray, window: Window) -> np.ndarray:
    """The part of a polygon ring inside the window (longitude, latitude), as a ring; empty when none is inside."""
    x0, y0, x1, y1 = window
    pts = ring[:-1] if len(ring) > 1 and np.array_equal(ring[0], ring[-1]) else ring
    for axis, value, keep_greater in (
        (0, x0, True),
        (0, x1, False),
        (1, y0, True),
        (1, y1, False),
    ):
        pts = _clip_against(pts, axis, value, keep_greater)
        if len(pts) == 0:
            return pts
    return pts


def clip_line(line: np.ndarray, window: Window) -> list[np.ndarray]:
    """The runs of a polyline inside the window (Liang-Barsky on each segment); a run leaves and re-enters as two."""
    x0, y0, x1, y1 = window
    runs: list[list[np.ndarray]] = []
    current: list[np.ndarray] | None = None
    for p, q in pairwise(line):
        dx, dy = q[0] - p[0], q[1] - p[1]
        t0, t1 = 0.0, 1.0
        for pk, qk in (
            (-dx, p[0] - x0),
            (dx, x1 - p[0]),
            (-dy, p[1] - y0),
            (dy, y1 - p[1]),
        ):
            if pk == 0:
                if qk < 0:
                    t0, t1 = 1.0, 0.0
                    break
                continue
            r = qk / pk
            if pk < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                break
        if t0 > t1:
            current = None
            continue
        a = p if t0 == 0.0 else np.array([p[0] + t0 * dx, p[1] + t0 * dy])
        b = q if t1 == 1.0 else np.array([p[0] + t1 * dx, p[1] + t1 * dy])
        if current is None or t0 > 0.0:
            current = [a]
            runs.append(current)
        current.append(b)
        if t1 < 1.0:
            current = None
    return [np.asarray(run) for run in runs if len(run) >= 2]


def _within(box_min: np.ndarray, box_max: np.ndarray, window: Window) -> bool:
    x0, y0, x1, y1 = window
    return not (box_max[0] < x0 or box_min[0] > x1 or box_max[1] < y0 or box_min[1] > y1)


# -- simplifying and writing paths -------------------------------------------------------------------


def simplify(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Douglas-Peucker: every dropped vertex lies within ``tolerance`` of the kept polyline."""
    n = len(points)
    if n < 3:
        return points
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = points[a], points[b]
        seg = points[a + 1 : b]
        dx, dy = q - p
        length = math.hypot(dx, dy)
        if length == 0.0:
            dist = np.hypot(seg[:, 0] - p[0], seg[:, 1] - p[1])
        else:
            dist = np.abs(dx * (seg[:, 1] - p[1]) - dy * (seg[:, 0] - p[0])) / length
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            k = a + 1 + i
            keep[k] = True
            stack.append((a, k))
            stack.append((k, b))
    return points[keep]


def simplify_ring(ring: np.ndarray, tolerance: float) -> np.ndarray:
    """Douglas-Peucker on a closed ring, split at the vertex farthest from the first so the two halves are open."""
    if len(ring) < 4:
        return ring
    far = int(np.argmax(np.hypot(*(ring - ring[0]).T)))
    first = simplify(ring[: far + 1], tolerance)
    second = simplify(np.vstack([ring[far:], ring[:1]]), tolerance)
    return np.vstack([first[:-1], second[:-1]])


def _fmt(q: int) -> str:
    """A number in tenths of a unit written the short way: 12.3, -.5, 2."""
    sign = "-" if q < 0 else ""
    whole, frac = divmod(abs(q), 10)
    if frac == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole or ''}.{frac}"


def path_data(parts: Iterable[np.ndarray], closed: bool) -> str:
    """SVG path data for projected parts: an absolute ``M`` and relative ``l`` steps in tenths of a unit.

    Vertices are rounded to 0.1 map unit first and the steps are differences of the rounded values, so the
    path never drifts from the rounded geometry. A part with fewer than two (three when closed) distinct
    vertices is left out.
    """
    out: list[str] = []
    for part in parts:
        q = np.rint(part * 10).astype(np.int64)
        if len(q) > 1:
            q = q[np.r_[True, np.any(np.diff(q, axis=0) != 0, axis=1)]]
        if closed and len(q) > 1 and np.array_equal(q[0], q[-1]):
            q = q[:-1]
        if len(q) < (3 if closed else 2):
            continue
        steps = np.diff(q, axis=0).ravel()
        text = f"M{_fmt(int(q[0, 0]))} {_fmt(int(q[0, 1]))}l" + " ".join(_fmt(int(v)) for v in steps)
        out.append(text + ("z" if closed else ""))
    return "".join(out)


# -- building a region -------------------------------------------------------------------------------


def _land(preset: Region, collection: dict[str, Any]) -> str:
    proj = preset.projection
    small = 3 * preset.tolerance
    rings: list[np.ndarray] = []
    for feature in collection["features"]:
        for polygon in _polygons(feature):
            outer = polygon[0]
            if not _within(outer.min(axis=0), outer.max(axis=0), preset.window):
                continue
            for ring in polygon:
                clipped = clip_ring(ring, preset.window)
                if len(clipped) < 3:
                    continue
                x, y = proj.project(clipped[:, 0], clipped[:, 1])
                xy = np.column_stack([x, y])
                if np.ptp(xy[:, 0]) < small and np.ptp(xy[:, 1]) < small:
                    continue  # an islet smaller than the tolerance
                rings.append(simplify_ring(xy, preset.tolerance))
    return path_data(rings, closed=True)


def _line_layer(preset: Region, features: Iterable[dict[str, Any]], tolerance: float | None = None) -> str:
    proj = preset.projection
    tol = preset.tolerance if tolerance is None else tolerance
    parts: list[np.ndarray] = []
    seen: set[tuple[Any, ...]] = set()
    for feature in features:
        for line in _lines(feature):
            if not _within(line.min(axis=0), line.max(axis=0), preset.window):
                continue
            for run in clip_line(line, preset.window):
                x, y = proj.project(run[:, 0], run[:, 1])
                xy = simplify(np.column_stack([x, y]), tol)
                key = (round(float(xy[0, 0]), 0), round(float(xy[0, 1]), 0), len(xy))
                if key in seen:
                    continue
                seen.add(key)
                parts.append(xy)
    return path_data(parts, closed=False)


def _river_features(preset: Region, source: Path) -> list[dict[str, Any]]:
    stems = [s for s in source_stems(preset) if "rivers" in s]
    names = set(preset.rivers)
    kept: list[dict[str, Any]] = []
    for stem in stems:
        for feature in read_geojson(source, stem)["features"]:
            props = feature["properties"]
            if names:
                if (props.get("name") or "").strip() in names:
                    kept.append(feature)
            elif (props.get("scalerank") or 99) <= preset.river_rank:
                kept.append(feature)
    return kept


def _outline(preset: Region) -> list[list[float]]:
    """The window's boundary projected, one point per half degree: where the data ends."""
    x0, y0, x1, y1 = preset.window
    step = 0.5
    lons = np.arange(x0, x1 + step / 2, step)
    lats = np.arange(y0, y1 + step / 2, step)
    ring = np.vstack(
        [
            np.column_stack([lons, np.full(len(lons), y1)]),
            np.column_stack([np.full(len(lats), x1), lats[::-1]]),
            np.column_stack([lons[::-1], np.full(len(lons), y0)]),
            np.column_stack([np.full(len(lats), x0), lats]),
        ]
    )
    x, y = preset.projection.project(ring[:, 0], ring[:, 1])
    return [[round(float(a), 1), round(float(b), 1)] for a, b in zip(x, y, strict=True)]


def _cities(preset: Region, collection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    x0, y0, x1, y1 = preset.window
    candidates = []
    for feature in collection["features"]:
        p = feature["properties"]
        lon, lat = float(p["longitude"]), float(p["latitude"])
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        population = int(p.get("pop_max") or 0)
        capital = str(p.get("featurecla", "")).startswith("Admin-0 capital")
        in_focus = preset.focus_country is not None and p.get("adm0_a3") == preset.focus_country
        if population >= preset.city_min_population or capital or in_focus:
            candidates.append((population, p["nameascii"], p["name"], lon, lat))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    cities: dict[str, dict[str, Any]] = {}
    for population, key, name, lon, lat in candidates:
        if key in cities or len(cities) >= preset.city_limit:
            continue
        cities[key] = {
            "name": name,
            "lon": round(lon, 3),
            "lat": round(lat, 3),
            "pop": population,
        }
    return dict(sorted(cities.items()))


def build_region(name: str, source: Path) -> dict[str, Any]:
    """The data object of a region, built from the Natural Earth files in ``source``."""
    preset = region(name)
    res = preset.resolution
    land = _land(preset, read_geojson(source, f"ne_{res}_land"))
    borders = _line_layer(
        preset,
        read_geojson(source, f"ne_{res}_admin_0_boundary_lines_land")["features"],
    )
    regions = ""
    if preset.focus_country:
        lines = read_geojson(source, f"ne_{res}_admin_1_states_provinces_lines")["features"]
        regions = _line_layer(
            preset,
            (f for f in lines if f["properties"].get("ADM0_A3") == preset.focus_country),
        )
    rivers = ""
    if preset.rivers or preset.river_rank > 0:
        rivers = _line_layer(preset, _river_features(preset, source), preset.tolerance / 2)
    cities = _cities(
        preset,
        read_geojson(source, f"ne_{res}_populated_places_simple"),
    )
    ring = _outline(preset)
    xs, ys = [c[0] for c in ring], [c[1] for c in ring]
    return {
        "version": SCRIPT_VERSION,
        "region": name,
        "projection": preset.projection.as_json(),
        "window": list(preset.window),
        "outline": None if preset.world else ring,
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "tolerance": preset.tolerance,
        "land": land,
        "borders": borders,
        "regions": regions,
        "rivers": rivers,
        "cities": cities,
    }


def render_script(data: dict[str, Any]) -> str:
    """The text of ``map-REGION.js``: deterministic, so regenerating from the same sources changes nothing."""
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    return (
        f"// Generated by python -m hyperframes_mcp.motion map-data {data['region']}: Natural Earth (public domain), simplified\n"
        "// and projected. Do not edit; see assets/motion/data/DATA.md.\n"
        "window.__MOTION_MAPS = window.__MOTION_MAPS || {};\n"
        f'window.__MOTION_MAPS["{data["region"]}"] = {body};\n'
    )


def write_region(name: str, source: Path, out_dir: Path = DATA_DIR) -> Path:
    """Build ``name`` and write ``out_dir/map-NAME.js``; returns the path."""
    text = render_script(build_region(name, source))
    path = Path(out_dir) / f"map-{name}.js"
    atomic_write_bytes(path, text.encode("utf-8"))
    return path
