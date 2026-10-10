"""The map data of the motion library: projections, clipping, simplification, the written files and their licence record."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path

import numpy as np
import pytest

from hyperframes_mcp.motion import maps, tokens
from tests.motion.map_helpers import load_script

DATA = maps.DATA_DIR
REGION_NAMES = ("germany", "europe", "world")
MAX_BYTES = 300 * 1024


def decode_path(d: str) -> list[list[tuple[float, float]]]:
    """The subpaths of a path written by ``path_data`` as absolute points (a tiny reader of M, l and z)."""
    paths = []
    for sub in re.findall(r"M[^Mz]*z?", d):
        head, _, steps = sub.rstrip("z").partition("l")
        x, y = (float(v) for v in head[1:].split(" "))
        points = [(x, y)]
        values = [float(v) for v in steps.split(" ")] if steps else []
        for dx, dy in zip(values[0::2], values[1::2], strict=True):
            x, y = x + dx, y + dy
            points.append((round(x, 1), round(y, 1)))
        paths.append(points)
    return paths


# -- projections, on known points -----------------------------------------------------------------


def test_germany_projection_reproduces_the_prototype_city_positions():
    """Plain equirectangular scaled by cos(51 deg) from lon 10, lat 51, 130 units a degree."""
    proj = maps.region("germany").projection
    known = {  # (lon, lat): the x, y of the prototype's map base
        (13.405, 52.52): (278.6, -197.6),
        (10.12, 50.82): (9.8, 23.4),
        (8.68, 50.11): (-108.0, 115.7),
        (11.03, 50.98): (84.3, 2.6),
        (12.37, 51.34): (193.9, -44.2),
    }
    for (lon, lat), (x, y) in known.items():
        px, py = proj.point(lon, lat)
        assert px == pytest.approx(x, abs=0.1)
        assert py == pytest.approx(y, abs=0.1)


def test_equirect_origin_axes_and_east_west_scale():
    proj = maps.Projection("equirect", lon0=10.0, lat0=51.0)

    assert proj.point(10.0, 51.0) == (0.0, 0.0)
    east_x, east_y = proj.point(11.0, 51.0)
    north_x, north_y = proj.point(10.0, 52.0)
    assert east_x == pytest.approx(130 * math.cos(math.radians(51)))
    assert east_y == 0.0
    assert north_x == 0.0
    assert north_y == pytest.approx(-130.0)  # north is up: y grows to the south


EUROPE = maps.region("europe").projection


def test_lcc_origin_central_meridian_and_directions():
    assert EUROPE.point(10.0, 52.0) == pytest.approx((0.0, 0.0), abs=1e-9)
    for lat in (35.0, 48.0, 60.0, 71.0):
        assert EUROPE.point(10.0, lat)[0] == pytest.approx(0.0, abs=1e-9)
    assert EUROPE.point(10.0, 60.0)[1] < 0 < EUROPE.point(10.0, 45.0)[1]
    assert EUROPE.point(20.0, 52.0)[0] > 0 > EUROPE.point(0.0, 52.0)[0]
    # the meridians converge to the north: one degree of longitude is shorter at 70 than at 40
    wide = EUROPE.point(11.0, 40.0)[0] - EUROPE.point(10.0, 40.0)[0]
    narrow = EUROPE.point(11.0, 70.0)[0] - EUROPE.point(10.0, 70.0)[0]
    assert wide > narrow > 0


def test_lcc_is_true_to_scale_along_the_standard_parallels():
    r = EUROPE.radius
    for lat in (35.0, 65.0):
        x0, y0 = EUROPE.point(10.0, lat)
        x1, y1 = EUROPE.point(10.01, lat)
        along = math.hypot(x1 - x0, y1 - y0)
        assert along == pytest.approx(r * math.cos(math.radians(lat)) * math.radians(0.01), rel=1e-5)


def test_lcc_is_conformal_the_scale_is_the_same_in_every_direction():
    r = EUROPE.radius
    step = 0.001
    for lon, lat in ((3.0, 42.0), (10.0, 52.0), (25.0, 60.0), (38.0, 47.0)):
        x, y = EUROPE.point(lon, lat)
        ex, ey = EUROPE.point(lon + step, lat)
        nx, ny = EUROPE.point(lon, lat + step)
        east = math.hypot(ex - x, ey - y) / (r * math.cos(math.radians(lat)) * math.radians(step))
        north = math.hypot(nx - x, ny - y) / (r * math.radians(step))
        assert east == pytest.approx(north, rel=1e-3)


WORLD = maps.region("world").projection


def test_natural_earth_projection_known_points():
    r = WORLD.radius

    assert WORLD.point(0.0, 0.0) == (0.0, 0.0)
    # the equator is 0.8707 of its length on the sphere, the pole line reaches y = 1.4225 R
    assert WORLD.point(180.0, 0.0)[0] == pytest.approx(r * 0.8707 * math.pi)
    assert WORLD.point(0.0, 90.0)[1] == pytest.approx(-1.4225 * r, abs=0.001 * r)
    assert WORLD.point(0.0, -90.0)[1] == pytest.approx(1.4225 * r, abs=0.001 * r)
    # the published aspect ratio of the whole map: about 1.9 to 1
    width = 2 * WORLD.point(180.0, 0.0)[0]
    height = WORLD.point(0.0, -90.0)[1] - WORLD.point(0.0, 90.0)[1]
    assert width / height == pytest.approx(1.92, abs=0.02)
    # the parallels shorten toward the poles
    assert WORLD.point(180.0, 60.0)[0] < WORLD.point(180.0, 30.0)[0] < WORLD.point(180.0, 0.0)[0]


def test_the_projections_take_arrays_and_scalars_alike():
    lon, lat = np.array([5.0, 10.0, 15.0]), np.array([48.0, 51.0, 54.0])
    for proj in (maps.region(n).projection for n in REGION_NAMES):
        xs, ys = proj.project(lon, lat)
        for i in range(3):
            assert (xs[i], ys[i]) == pytest.approx(proj.point(float(lon[i]), float(lat[i])))


def test_an_unknown_projection_or_region_is_an_error_that_lists_the_choices():
    with pytest.raises(maps.MapDataError, match="unknown projection"):
        maps.Projection("mercator")
    with pytest.raises(maps.MapDataError, match="germany, world|europe"):
        maps.region("atlantis")


# -- clipping --------------------------------------------------------------------------------------

WINDOW = (0.0, 0.0, 10.0, 10.0)


def test_a_ring_inside_the_window_is_unchanged_and_one_outside_vanishes():
    inside = np.array([[2.0, 2.0], [8.0, 2.0], [8.0, 8.0], [2.0, 8.0], [2.0, 2.0]])
    outside = inside + 20

    assert np.array_equal(maps.clip_ring(inside, WINDOW), inside[:-1])
    assert len(maps.clip_ring(outside, WINDOW)) == 0


def test_a_ring_that_crosses_the_window_is_cut_along_its_edge():
    ring = np.array([[5.0, 5.0], [15.0, 5.0], [15.0, 8.0], [5.0, 8.0], [5.0, 5.0]])
    clipped = maps.clip_ring(ring, WINDOW)

    assert clipped[:, 0].max() == pytest.approx(10.0)
    assert clipped[:, 0].min() == pytest.approx(5.0)
    assert np.all(clipped >= 0) and np.all(clipped <= 10)
    # area of the part inside: 5 wide, 3 high
    x, y = clipped[:, 0], clipped[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    assert area == pytest.approx(15.0)


def test_a_line_that_leaves_and_comes_back_is_two_runs():
    line = np.array([[2.0, 5.0], [12.0, 5.0], [12.0, 7.0], [3.0, 7.0]])
    runs = maps.clip_line(line, WINDOW)

    assert len(runs) == 2
    assert runs[0][-1].tolist() == pytest.approx([10.0, 5.0])
    assert runs[1][0].tolist() == pytest.approx([10.0, 7.0])
    assert maps.clip_line(np.array([[20.0, 20.0], [30.0, 30.0]]), WINDOW) == []
    whole = np.array([[1.0, 1.0], [5.0, 4.0], [9.0, 9.0]])
    assert np.array_equal(maps.clip_line(whole, WINDOW)[0], whole)


# -- simplification --------------------------------------------------------------------------------


def distance_to_polyline(point: np.ndarray, line: np.ndarray) -> float:
    """The shortest distance from a point to the segments of a polyline."""
    start, step = line[:-1], np.diff(line, axis=0)
    length = np.einsum("ij,ij->i", step, step)
    along = np.einsum("ij,ij->i", point - start, step) / np.where(length > 0, length, 1)
    nearest = start + np.clip(along, 0, 1)[:, None] * step
    return float(np.hypot(*(point - nearest).T).min())


def test_simplify_keeps_every_dropped_vertex_within_the_tolerance():
    rng = np.random.default_rng(7)
    walk = np.cumsum(rng.normal(size=(400, 2)), axis=0)
    for tolerance in (0.3, 1.0, 2.5):
        kept = maps.simplify(walk, tolerance)

        assert len(kept) < len(walk)
        assert np.array_equal(kept[0], walk[0]) and np.array_equal(kept[-1], walk[-1])
        assert max(distance_to_polyline(p, kept) for p in walk) <= tolerance + 1e-9
    assert len(maps.simplify(walk, 0.3)) > len(maps.simplify(walk, 2.5))


def test_simplify_ring_stays_closed_and_close_to_the_original_shape():
    angle = np.linspace(0, 2 * np.pi, 361)[:-1]
    ring = np.column_stack([100 * np.cos(angle), 60 * np.sin(angle)])
    kept = maps.simplify_ring(ring, 0.5)
    closed = np.vstack([kept, kept[:1]])

    assert 8 < len(kept) < len(ring)
    assert max(distance_to_polyline(p, closed) for p in ring) <= 0.5 + 1e-9


def test_path_data_round_trips_to_the_rounded_geometry_and_is_short():
    ring = np.array([[10.04, -3.26], [12.34, -3.26], [12.34, 0.74], [10.04, 0.74]])
    line = np.array([[0.0, 0.0], [0.5, -0.25], [5.0, 2.0]])

    closed = maps.path_data([ring], closed=True)
    opened = maps.path_data([line], closed=False)

    assert closed == "M10 -3.3l2.3 0 0 4 -2.3 0z"
    assert decode_path(closed) == [[(10.0, -3.3), (12.3, -3.3), (12.3, 0.7), (10.0, 0.7)]]
    assert decode_path(opened) == [[(0.0, 0.0), (0.5, -0.2), (5.0, 2.0)]]
    assert maps.path_data([np.array([[1.0, 1.0], [1.01, 1.01]])], closed=False) == ""  # one distinct vertex
    assert maps.path_data([line[:1]], closed=False) == ""


def test_path_numbers_are_written_the_short_way():
    assert [maps._fmt(q) for q in (123, -5, 20, 0, -100, 7, -12)] == [
        "12.3",
        "-.5",
        "2",
        "0",
        "-10",
        ".7",
        "-1.2",
    ]


# -- building a region from sources ---------------------------------------------------------------


def feature(geometry: dict, **props) -> dict:
    return {"type": "Feature", "properties": props, "geometry": geometry}


def polygon(ring: list[list[float]], *holes: list[list[float]]) -> dict:
    return {"type": "Polygon", "coordinates": [ring, *holes]}


def line(points: list[list[float]]) -> dict:
    return {"type": "LineString", "coordinates": points}


def place(
    name: str,
    lon: float,
    lat: float,
    pop: int,
    adm0: str = "XXX",
    cls: str = "Populated place",
) -> dict:
    return feature(
        {"type": "Point", "coordinates": [lon, lat]},
        name=name, nameascii=name, longitude=lon, latitude=lat, pop_max=pop, adm0_a3=adm0, featurecla=cls,
    )  # fmt: skip


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Tiny stand-ins for the six files the germany preset reads, with their digests registered as the pinned ones."""
    land = [
        feature(polygon(
            [[5, 47], [14, 47], [14, 54], [5, 54], [5, 47]],
            [[8, 50], [9, 50], [9, 51], [8, 51], [8, 50]],  # a lake
        )),
        feature(polygon([[40, 40], [41, 40], [41, 41], [40, 41], [40, 40]])),  # far outside the window
        feature(polygon([[10, 50], [10.001, 50], [10.001, 50.001], [10, 50.001], [10, 50]])),  # an islet
    ]  # fmt: skip
    files = {
        "ne_10m_land": land,
        "ne_10m_admin_0_boundary_lines_land": [feature(line([[9, 47], [9, 54]]))],
        "ne_10m_admin_1_states_provinces_lines": [
            feature(line([[6, 48], [13, 53]]), ADM0_A3="DEU"),
            feature(line([[6, 53], [13, 48]]), ADM0_A3="FRA"),
        ],
        "ne_10m_rivers_lake_centerlines": [
            feature(line([[6, 50], [8, 52], [12, 52]]), name="Rhine"),
            feature(line([[6, 51], [12, 51]]), name="Unnamed Creek"),
        ],
        "ne_10m_rivers_europe": [feature(line([[7, 49], [11, 50]]), name="Elbe")],
        "ne_10m_populated_places_simple": [
            place("Berlin", 13.4, 52.5, 3_400_000, "DEU", "Admin-0 capital"),
            place("Smalltown", 9.0, 50.0, 12_000, "DEU"),
            place("Bigtown", 7.0, 49.0, 900_000, "FRA"),
            place("Faraway", 100.0, 10.0, 9_000_000, "IND"),
        ],
    }
    for stem, features in files.items():
        data = json.dumps({"type": "FeatureCollection", "features": features}).encode()
        (tmp_path / f"{stem}.geojson").write_bytes(data)
        monkeypatch.setitem(maps.SOURCE_SHA256, stem, hashlib.sha256(data).hexdigest())
    return tmp_path


def test_a_region_is_built_from_its_sources(sources):
    data = maps.build_region("germany", sources)

    assert data["region"] == "germany"
    assert data["projection"] == {
        "type": "equirect",
        "lon0": 10.0,
        "lat0": 51.0,
        "k": 130.0,
    }
    assert data["window"] == [-4.0, 45.0, 24.0, 57.0]
    land = decode_path(data["land"])
    assert len(land) == 2  # the square and its lake; the far polygon and the islet are gone
    assert len(decode_path(data["borders"])) == 1
    assert len(decode_path(data["regions"])) == 1  # the German line only, not the French one
    assert len(decode_path(data["rivers"])) == 2  # Rhine and Elbe, not the unnamed creek
    assert set(data["cities"]) == {
        "Berlin",
        "Smalltown",
        "Bigtown",
    }  # the focus country and the big ones in the window
    assert data["cities"]["Berlin"] == {
        "name": "Berlin",
        "lon": 13.4,
        "lat": 52.5,
        "pop": 3_400_000,
    }
    x0, y0, x1, y1 = data["bbox"]
    assert (x0, x1) == pytest.approx((-14 * 81.81, 14 * 81.81), abs=0.1)
    assert (y0, y1) == pytest.approx((-6 * 130, 6 * 130), abs=0.1)


def test_the_land_of_a_region_sits_inside_its_window(sources):
    data = maps.build_region("germany", sources)
    x0, y0, x1, y1 = data["bbox"]

    for ring in decode_path(data["land"]):
        for x, y in ring:
            assert x0 - 0.1 <= x <= x1 + 0.1
            assert y0 - 0.1 <= y <= y1 + 0.1
    assert data["outline"][0] != data["outline"][1] and len(data["outline"]) > 4


def test_a_missing_or_changed_source_file_is_refused(sources):
    (sources / "ne_10m_land.geojson").unlink()
    with pytest.raises(maps.MapDataError, match="ne_10m_land.geojson is missing"):
        maps.build_region("germany", sources)

    (sources / "ne_10m_land.geojson").write_text('{"features": []}')
    with pytest.raises(maps.MapDataError, match="not the file of commit"):
        maps.build_region("germany", sources)


def test_the_script_is_deterministic_and_registers_the_region(sources):
    first = maps.render_script(maps.build_region("germany", sources))
    second = maps.render_script(maps.build_region("germany", sources))

    assert first == second
    assert first.startswith("// Generated by python -m hyperframes_mcp.motion map-data germany")
    assert 'window.__MOTION_MAPS = window.__MOTION_MAPS || {};\nwindow.__MOTION_MAPS["germany"] = {' in first
    assert first.isascii()


def test_every_preset_names_only_sources_that_are_pinned():
    for name in REGION_NAMES:
        for stem in maps.source_stems(maps.region(name)):
            assert re.fullmatch(r"[0-9a-f]{64}", maps.SOURCE_SHA256[stem]), stem


# -- the shipped files ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", REGION_NAMES)
def test_each_shipped_map_file_is_under_300_kb_and_well_formed(name):
    path = DATA / f"map-{name}.js"
    preset = maps.region(name)

    assert path.stat().st_size < MAX_BYTES
    data = load_script(name)
    assert data["region"] == name
    assert data["version"] == maps.SCRIPT_VERSION
    assert data["projection"] == preset.projection.as_json()
    assert data["window"] == list(preset.window)
    assert data["tolerance"] == preset.tolerance
    assert data["land"].startswith("M") and data["land"].endswith("z")
    assert data["borders"].startswith("M")
    assert (data["outline"] is None) == preset.world
    x0, y0, x1, y1 = data["bbox"]
    assert x0 < x1 and y0 < y1
    assert len(data["cities"]) > 100


def test_the_germany_file_has_first_level_regions_and_rivers_and_the_others_have_none_of_the_regions():
    assert load_script("germany")["regions"].startswith("M")
    assert load_script("germany")["rivers"].startswith("M")
    assert load_script("europe")["regions"] == ""
    assert load_script("europe")["rivers"].startswith("M")
    assert load_script("world")["rivers"] == ""


def test_the_cities_table_holds_the_towns_a_route_needs():
    cities = load_script("germany")["cities"]
    for name, (lon, lat) in {
        "Berlin": (13.4, 52.5),
        "Leipzig": (12.4, 51.3),
        "Erfurt": (11.0, 51.0),
    }.items():
        assert cities[name]["lon"] == pytest.approx(lon, abs=0.1)
        assert cities[name]["lat"] == pytest.approx(lat, abs=0.2)
    assert "Paris" in load_script("europe")["cities"]
    assert "Tokyo" in load_script("world")["cities"]


def test_the_shipped_germany_path_stays_inside_its_bounding_box():
    data = load_script("germany")
    x0, y0, x1, y1 = data["bbox"]
    for key in ("land", "borders", "regions", "rivers"):
        for part in decode_path(data[key]):
            for x, y in part:
                assert x0 - 0.2 <= x <= x1 + 0.2, key
                assert y0 - 0.2 <= y <= y1 + 0.2, key


@pytest.mark.skipif(
    "MOTION_NE_DIR" not in os.environ,
    reason="set MOTION_NE_DIR to the folder of Natural Earth GeoJSON files (assets/motion/data/DATA.md)",
)
@pytest.mark.parametrize("name", REGION_NAMES)
def test_regenerating_from_the_pinned_sources_reproduces_the_shipped_file(name):
    rebuilt = maps.render_script(maps.build_region(name, Path(os.environ["MOTION_NE_DIR"])))

    assert rebuilt == (DATA / f"map-{name}.js").read_text(encoding="utf-8")


# -- the licence record ---------------------------------------------------------------------------


def test_data_md_records_source_licence_commit_and_what_was_done():
    notes = (DATA / "DATA.md").read_text(encoding="utf-8")

    assert "Natural Earth" in notes and "public domain" in notes
    assert maps.NE_COMMIT in notes
    assert "nvkelso/natural-earth-vector" in notes
    assert "modern" in notes and "OpenHistoricalMap" in notes and "CC0" in notes
    for name in REGION_NAMES:
        preset = maps.region(name)
        assert f"map-{name}.js" in notes
        assert str(preset.tolerance) in notes
        assert (
            preset.projection.kind in notes
            or {
                "equirect": "equirectangular",
                "lcc": "Lambert",
                "natearth": "Natural Earth",
            }[preset.projection.kind]
            in notes
        )
    for stem, digest in maps.SOURCE_SHA256.items():
        assert stem in notes and digest in notes


def test_third_party_names_the_map_data_and_its_licence():
    third_party = (tokens.MOTION_DIR / "THIRD_PARTY.md").read_text(encoding="utf-8")

    assert "map-data" in third_party
    assert "Natural Earth" in third_party and "public domain" in third_party
    assert maps.NE_COMMIT in third_party


# -- the command line -----------------------------------------------------------------------------
