# Map data of the motion library

Three scripts, one per region, that the map blocks load: `map-germany.js`, `map-europe.js`, `map-world.js`. Each registers one
object on `window.__MOTION_MAPS["<region>"]`. They are generated, never edited: `python -m hyperframes_mcp.motion map-data REGION --source DIR`
(code in `src/hyperframes_mcp/motion/maps.py`) reads Natural Earth GeoJSON files from `DIR` and writes the script into this
folder (`src/hyperframes_mcp/assets/motion/data`, or `--out`).

## Source and licence

Natural Earth (https://www.naturalearthdata.com), Tom Patterson and Nathaniel Vaughn Kelso and other contributors. Terms of use,
read on 2026-10-05 at https://www.naturalearthdata.com/about/terms-of-use/: "All versions of Natural Earth raster + vector map data
found on this website are in the public domain. You may use the maps in any manner, including modifying the content and design,
electronic dissemination, and offset printing ... No permission is needed to use Natural Earth. Crediting the authors is
unnecessary." So the scripts here are public domain data (derived from public domain data by our own code) and need no credit; a
credit line may still say "Made with Natural Earth".

The files are the `geojson/` folder of https://github.com/nvkelso/natural-earth-vector at commit
`ca96624a56bd078437bca8184e78163e5039ad19` (the last commit on `master`, 2022-06-02). `maps.py` refuses a source file whose sha256 is
not the one below, so a regeneration is reproducible. Download each with
`curl -L -o DIR/STEM.geojson https://raw.githubusercontent.com/nvkelso/natural-earth-vector/ca96624a56bd078437bca8184e78163e5039ad19/geojson/STEM.geojson`.

| STEM | Bytes | sha256 | Used for |
|---|---|---|---|
| `ne_10m_land` | 10157965 | `1ac90796408bc6ad6911d69448485d3c4dbf2190370080368a09976e1c9f7416` | germany: land and coast |
| `ne_10m_admin_0_boundary_lines_land` | 2284669 | `74d9c16229c095fde65943a9919e337682f044bcebccb120764f38edf3b70f4a` | germany: country borders |
| `ne_10m_admin_1_states_provinces_lines` | 21092537 | `1a1f30ccaaf4cc9c4bde34266f0b8cbb955d3a4cf254b756912255f2ec7c75b6` | germany: first-level regions (the German states; lines with `ADM0_A3` = `DEU`) |
| `ne_10m_rivers_lake_centerlines` | 7307743 | `bb854a900ecbd3b408df46d5e16e3e0f974ba55993f9d8b5c26e855273c0905a` | germany: the named rivers (Rhine, Elbe, Weser, Main, Danube, Oder, Neckar, Moselle, Ems) |
| `ne_10m_rivers_europe` | 2534505 | `90712202ac46985812e90840df9acd5101599a9c649868d5bc55ee96c2fedd4c` | germany: the same names where this file draws them (Elbe, Neckar, Oder) |
| `ne_10m_populated_places_simple` | 4932147 | `fd3fa867a320cbd5c5b6bb5bc550afeec2939fb2cef688e508007282a55ac42f` | germany: cities |
| `ne_50m_land` | 1636166 | `e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b` | europe: land and coast |
| `ne_50m_admin_0_boundary_lines_land` | 760189 | `2faac4f6b34386f3d21b6e018cf151f241f00e5c936d44dd17d7d9bfb147fa48` | europe: country borders |
| `ne_50m_rivers_lake_centerlines` | 827318 | `f286e0ce978fde999ca2d7a78c764be08542e19b63cded52b05c12d5173ccc51` | europe: rivers up to scalerank 6 |
| `ne_50m_populated_places_simple` | 850767 | `8e70756b39fae9bcdc1e332bfc510c024c5edd3a13203ffd20092ee37b61d978` | europe: cities |
| `ne_110m_land` | 138160 | `9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9` | world: land and coast |
| `ne_110m_admin_0_boundary_lines_land` | 340010 | `d42479fd79552cca4eec7f85fcdca717a790d29ff06be7676f1af0568c6d3f7c` | world: country borders |
| `ne_110m_populated_places_simple` | 166071 | `0dbd25c9ad8bd797ddf164b067f563be5c16be2c002254eb594862377963f9dc` | world: cities |

### Cities that are not in the data

Small towns a story may need are not in the `cities` table of `map-germany.js` (Marburg, Fulda, Eisenach, Gotha, Hanau,
Aschaffenburg and Bad Hersfeld are not), so a shot list gives such a pin its `lon` and `lat` itself. The tests check the
Germany projection against known projected city positions (`tests/motion/test_maps.py`).

## What is in a script

| Key | Holds |
|---|---|
| `projection` | the projection and its parameters (below) |
| `window` | longitude and latitude of the south west and north east corners of what the file keeps |
| `outline` | the window's boundary projected, as a polygon (`null` for the world: the sea fills what the window does not) |
| `bbox` | the projected extent of the window |
| `land` | land polygons (and their holes) as one SVG path, fill rule even-odd |
| `borders` | country borders on land, one SVG path |
| `regions` | first-level region borders of the focus country (Germany only; `""` elsewhere) |
| `rivers` | named or major rivers (`""` for the world) |
| `cities` | `{name ASCII: {name, lon, lat, pop}}`: the capitals, everything of the focus country and the cities over a population limit, at most 300 to 400 |
| `tolerance` | the Douglas-Peucker tolerance used, in map units |

Map units: x grows to the east, y to the south, one unit is 1/130 degree of latitude at the projection's origin. A path is written
as an absolute `M` and relative `l` steps in tenths of a unit; every vertex is rounded to 0.1 unit first, and the steps are
differences of rounded values, so a path does not drift.

## Regions, projections, simplification

| Region | Window (lon, lat) | Projection and why | Source scale | Tolerance | Size |
|---|---|---|---|---|---|
| `germany` | -4 to 24, 45 to 57 | equirectangular scaled by cos 51 degrees, origin 10 E 51 N (a window ten degrees wide has under 1% distortion) | 1:10m | 0.15 units, 0.0012 degree, about 130 m; at the deepest zoom intended (3.5 degrees wide) one screen pixel | about 170 KB |
| `europe` | -25 to 45, 33 to 72 | Lambert conformal conic, parallels 35 and 65, origin 10 E 52 N (true to shape over a continent in mid latitudes, meridians converge to the north as on a printed map) | 1:50m | 0.5 units, 0.0038 degree, about 430 m; under a pixel at 20 degrees wide | about 135 KB |
| `world` | -180 to 180, -58 to 84 | Natural Earth (a compromise projection that looks like an atlas, the poles are lines 0.52 of the equator long; Tom Patterson's design in the polynomial form of PROJ's `natearth`) | 1:110m | 1.0 unit, about 0.9 km, below the generalisation of the 1:110m source itself | about 95 KB |

Each file stays under 300 KB (a test checks it). The 1:50m world is 303 KB at tolerance 4.0 and 545 KB at 1.0, which is why the
world preset uses the 1:110m data: it is for whole-world and continent-wide views, and `europe` or `germany` are for closer ones.
A border shared by two countries is stored once: borders come from Natural Earth's boundary lines, not from country polygons.

Projections were tested on known points (`tests/motion/test_maps.py`): the Germany one against known projected city positions;
the conic on its central meridian, its standard parallels (true scale) and conformality (equal scale in every direction); Natural
Earth on the equator length, the pole line height (1.4225 of the radius) and the published aspect ratio of the whole map (1.9 to 1).
The block repeats the three projections in script (`assets/motion/js/map-lib.js`); a test runs that file under node and compares it
with the Python on a grid of points.

## Borders are the modern ones

Natural Earth draws the boundaries as they are today (it tags disputed ones in its `FEATURECLA`), so a country or state border
in a block is today's. A story set before 1991 (or 1990 for Germany) must use `borders: "none"` or tell
the viewer that the lines are modern; the example variable sets of `tests/motion/test_map_route.py` use `borders: "none"`. There are no historical
borders in the library yet. The later option is OpenHistoricalMap (https://www.openhistoricalmap.org): its data is dedicated to the
public domain under a Creative Commons CC0 dedication "except where otherwise noted", and some individual features carry CC BY or
CC BY-SA in their `license` tag, so an import must read that tag per feature (read on 2026-10-05, https://www.openhistoricalmap.org/copyright).
`historical-basemaps` is GPL-3.0 and must not be bundled.

## Not in the data

Lakes and the sea's shapes beyond the coast are not drawn (a lake such as Lake Ladoga is land), rivers exist only for `germany` and
`europe`, and there are no labels in the data: names come from the shot list (or its `cities` table).
