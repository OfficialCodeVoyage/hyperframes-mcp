// Pure helpers of the map blocks of the motion library (blocks/map-route.html): the projections of src/hyperframes_mcp/motion/maps.py written
// again in script, the camera, label measuring from the label font's advance widths, and the label placement that never
// overlaps. No DOM, no clock, no randomness: tests/motion/test_map_lib.py loads this file under node and compares it to
// the Python projections and to the font files.
//
// Copyright 2026 the hyperframes-mcp contributors. Hand-written. The Lambert conformal conic follows Snyder, Map Projections: A
// Working Manual (USGS Professional Paper 1395); the Natural Earth projection is its polynomial (Tom Patterson's projection,
// approximated by Bojan Savric with Patterson and Bernhard Jenny; coefficients as in PROJ natearth.c, MIT). See DATA.md.
(function (root) {
  "use strict";

  var RAD = Math.PI / 180;

  // The three projections: map units, x east and y south, `p` is the projection object of a map-REGION.js file.
  function makeProjection(p) {
    var radius = (p.k * 180) / Math.PI;
    if (p.type === "equirect") {
      var cosLat0 = Math.cos(p.lat0 * RAD);
      return function (lon, lat) {
        return [radius * (lon - p.lon0) * RAD * cosLat0, -radius * (lat - p.lat0) * RAD];
      };
    }
    if (p.type === "natearth") {
      return function (lon, lat) {
        var lam = (lon - p.lon0) * RAD;
        var phi = lat * RAD;
        var p2 = phi * phi;
        var p4 = p2 * p2;
        return [
          radius * lam * (0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4))),
          -radius * phi * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4))),
        ];
      };
    }
    if (p.type === "lcc") {
      var phi0 = p.lat0 * RAD;
      var phi1 = p.lat1 * RAD;
      var phi2 = p.lat2 * RAD;
      var n =
        Math.log(Math.cos(phi1) / Math.cos(phi2)) /
        Math.log(Math.tan(Math.PI / 4 + phi2 / 2) / Math.tan(Math.PI / 4 + phi1 / 2));
      var f = (Math.cos(phi1) * Math.pow(Math.tan(Math.PI / 4 + phi1 / 2), n)) / n;
      var rho0 = (radius * f) / Math.pow(Math.tan(Math.PI / 4 + phi0 / 2), n);
      return function (lon, lat) {
        var rho = (radius * f) / Math.pow(Math.tan(Math.PI / 4 + (lat * RAD) / 2), n);
        var theta = n * (lon - p.lon0) * RAD;
        return [rho * Math.sin(theta), rho * Math.cos(theta) - rho0];
      };
    }
    throw new Error("map-route: the map data has an unknown projection " + p.type);
  }

  // The camera: the visible width in degrees of longitude at its latitude sets the scale (pixels per map unit).
  function cameraPose(project, cam, width) {
    var centre = project(cam.lon, cam.lat);
    var west = project(cam.lon - cam.span / 2, cam.lat);
    var east = project(cam.lon + cam.span / 2, cam.lat);
    return { cx: centre[0], cy: centre[1], s: width / Math.hypot(east[0] - west[0], east[1] - west[1]) };
  }

  // The transform of the map group that puts the camera pose `from` (progress 0) to `to` (progress 1) on a canvas
  // of width x height: the scale moves in log steps and the centre in straight ones, so a zoom feels even.
  function cameraTransform(from, to, progress, width, height) {
    var s = from.s * Math.pow(to.s / from.s, progress);
    var cx = from.cx + (to.cx - from.cx) * progress;
    var cy = from.cy + (to.cy - from.cy) * progress;
    return { x: width / 2 - cx * s, y: height / 2 - cy * s, scale: s };
  }

  function insidePolygon(x, y, poly) {
    var inside = false;
    for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      var a = poly[i];
      var b = poly[j];
      if (a[1] > y !== b[1] > y && x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
    }
    return inside;
  }

  // A Catmull-Rom spline through the points ({x, y}) as cubic Beziers, as an SVG path.
  function splinePath(points) {
    var p = [points[0]].concat(points, [points[points.length - 1]]);
    var d = "M" + p[1].x.toFixed(1) + " " + p[1].y.toFixed(1);
    for (var i = 1; i < p.length - 2; i++) {
      var c1x = p[i].x + (p[i + 1].x - p[i - 1].x) / 6;
      var c1y = p[i].y + (p[i + 1].y - p[i - 1].y) / 6;
      var c2x = p[i + 1].x - (p[i + 2].x - p[i].x) / 6;
      var c2y = p[i + 1].y - (p[i + 2].y - p[i].y) / 6;
      d += " C" + [c1x, c1y, c2x, c2y, p[i + 1].x, p[i + 1].y].map(function (v) { return v.toFixed(1); }).join(" ");
    }
    return d;
  }

  // Meridians and parallels at a round step for a view `span` degrees wide, inside the window [lon0, lat0, lon1, lat1].
  function graticulePath(project, win, span) {
    var steps = [0.5, 1, 2, 5, 10, 15, 30];
    var step = steps[0];
    steps.forEach(function (candidate) {
      if (candidate <= span / 4) step = candidate;
    });
    function range(a, b, by) {
      var out = [];
      for (var v = Math.ceil(a / by) * by; v <= b + 1e-9; v += by) out.push(Math.round(v * 1e6) / 1e6);
      return out;
    }
    function line(points) {
      return (
        "M" +
        points
          .map(function (q) {
            var xy = project(q[0], q[1]);
            return xy[0].toFixed(1) + " " + xy[1].toFixed(1);
          })
          .join("L")
      );
    }
    var along = Math.max(1, Math.min(step, 2));
    var parts = [];
    range(win[0], win[2], step).forEach(function (lon) {
      parts.push(line(range(win[1], win[3], along).map(function (lat) { return [lon, lat]; })));
    });
    range(win[1], win[3], step).forEach(function (lat) {
      parts.push(line(range(win[0], win[2], along).map(function (lon) { return [lon, lat]; })));
    });
    return parts.join("");
  }

  // Advance widths in font units of U+0020 on (0 where the font has no glyph): IBM Plex Sans Condensed SemiBold up to
  // U+017E, Newsreader (its default instance) up to U+007E. tests/motion/test_map_lib.py compares them to the fonts.
  var ADVANCE_LABEL = {"first":32,"upm":1000,"w":[212,284,429,582,533,870,644,235,303,303,514,540,274,362,274,401,540,540,540,540,540,540,540,540,540,540,282,282,540,540,540,447,811,606,600,581,625,544,523,641,651,387,494,614,473,737,651,642,583,642,601,546,524,624,583,861,595,566,540,299,401,299,540,502,540,507,544,464,544,505,328,492,532,252,255,511,268,802,532,506,544,544,357,449,334,533,476,739,488,483,452,329,346,329,540,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,212,284,491,520,560,575,346,515,540,712,395,494,540,362,420,540,422,540,319,320,540,539,603,316,540,320,383,494,728,757,718,439,606,606,606,606,606,606,859,581,544,544,544,544,387,387,387,387,626,651,642,642,642,642,642,540,641,624,624,624,624,566,582,613,507,507,507,507,507,507,782,464,505,505,505,505,254,254,254,254,533,532,506,506,506,506,506,540,512,533,533,533,533,483,544,483,606,507,606,507,606,506,581,464,581,464,581,464,581,464,625,580,626,543,544,505,544,505,544,505,543,505,544,505,641,500,641,500,641,500,641,500,651,532,657,531,387,254,387,254,387,254,388,254,387,254,589,507,494,255,614,511,511,473,268,473,268,473,293,473,376,481,268,651,532,651,532,651,532,801,649,531,642,506,642,506,642,506,896,818,601,357,601,357,601,357,546,449,546,449,551,449,546,449,524,334,524,334,528,340,624,533,624,533,624,533,624,533,624,533,625,532,861,739,566,483,566,540,452,540,452,540,452]};
  var ADVANCE_DISPLAY = {"first":32,"upm":2000,"w":[444,517,682,1100,1100,1523,1390,379,542,542,859,1000,461,685,440,784,1100,1100,1100,1100,1100,1100,1100,1100,1100,1100,484,519,1000,1000,1000,820,1756,1375,1250,1381,1495,1237,1142,1464,1559,646,844,1415,1168,1910,1500,1525,1179,1525,1240,1067,1298,1498,1335,1871,1387,1327,1243,634,784,636,1000,820,1000,864,1046,829,1062,879,648,950,1073,534,469,1032,513,1591,1095,1009,1081,1044,755,707,643,1023,995,1428,965,998,817,618,484,618,1000]};

  // The width in pixels of a line of text from the advance widths (kerning ignored; 4% is added for it and for the
  // weight), so a box drawn from it holds the text the browser sets. `tracking` is letter-spacing in em.
  function textWidth(text, size, table, tracking) {
    var total = 0;
    for (var i = 0; i < text.length; i++) {
      var index = text.charCodeAt(i) - table.first;
      var advance = index >= 0 && index < table.w.length ? table.w[index] : 0;
      total += (advance || table.upm * 0.58) / table.upm;
    }
    return (total + (tracking || 0) * text.length) * size * 1.04;
  }

  // Greedy line breaks, the way the browser breaks a paragraph that has a max-width.
  function wrapLines(text, size, table, maxWidth) {
    var lines = [];
    var line = "";
    text
      .split(/\s+/)
      .filter(Boolean)
      .forEach(function (word) {
        var next = line ? line + " " + word : word;
        if (line && textWidth(next, size, table, 0) > maxWidth) {
          lines.push(line);
          line = word;
        } else {
          line = next;
        }
      });
    if (line) lines.push(line);
    return lines;
  }

  var SIDES = ["right", "left", "top", "bottom"];
  var OPPOSITE = { right: "left", left: "right", top: "bottom", bottom: "top" };

  // The order a label tries its sides in: the hint, the side opposite to it, then the other two.
  function sideOrder(hint) {
    var across = hint === "right" || hint === "left" ? ["top", "bottom"] : ["right", "left"];
    return [hint, OPPOSITE[hint]].concat(across);
  }

  // The box of a label beside a dot: `item` has the dot centre x, y, the dot radius r and the text size w by h.
  function labelBox(item, side, gap) {
    var x = item.x;
    var y = item.y;
    var near = item.r + gap;
    if (side === "right") return { x0: x + near, y0: y - item.h / 2, x1: x + near + item.w, y1: y + item.h / 2 };
    if (side === "left") return { x0: x - near - item.w, y0: y - item.h / 2, x1: x - near, y1: y + item.h / 2 };
    if (side === "top") return { x0: x - item.w / 2, y0: y - near - item.h, x1: x + item.w / 2, y1: y - near };
    if (side === "bottom") return { x0: x - item.w / 2, y0: y + near, x1: x + item.w / 2, y1: y + near + item.h };
    return { x0: x - item.w / 2, y0: y - item.h / 2, x1: x + item.w / 2, y1: y + item.h / 2 };
  }

  function overlaps(a, b, pad) {
    return a.x0 < b.x1 + pad && a.x1 > b.x0 - pad && a.y0 < b.y1 + pad && a.y1 > b.y0 - pad;
  }

  function within(box, rect) {
    return box.x0 >= rect.x0 && box.x1 <= rect.x1 && box.y0 >= rect.y0 && box.y1 <= rect.y1;
  }

  function crossesLine(box, points, pad) {
    for (var i = 0; i < points.length; i++) {
      var q = points[i];
      if (q[0] > box.x0 - pad && q[0] < box.x1 + pad && q[1] > box.y0 - pad && q[1] < box.y1 + pad) return true;
    }
    return false;
  }

  // Place the labels in the order given (the caller puts the focus first). Each takes the first side that keeps it inside
  // the safe rect and clear of every hard box (dots, corner text, labels already placed); of those, the first that also
  // keeps off the soft points (the route). A region label has no dot and only one place. No free side is an error, never
  // an overlap. Returns {id: {side, box}}.
  function placeLabels(items, hard, soft, safe, gap, pad) {
    var placed = [];
    var result = {};
    items.forEach(function (item) {
      var sides = item.region ? ["centre"] : sideOrder(item.side);
      var clear = null;
      var onRoute = null;
      for (var i = 0; i < sides.length && !clear; i++) {
        var box = labelBox(item, sides[i], gap);
        if (!within(box, safe)) continue;
        var blocked = hard.concat(placed).some(function (other) {
          return overlaps(box, other, pad);
        });
        if (blocked) continue;
        if (crossesLine(box, soft, pad)) {
          onRoute = onRoute || { side: sides[i], box: box };
        } else {
          clear = { side: sides[i], box: box };
        }
      }
      var chosen = clear || onRoute;
      if (!chosen) {
        throw new Error(
          "map-route: no room for the label of pin " + item.id + " (tried " + sides.join(", ") + "): move the camera, " +
            "give the pin another labelSide or drop a pin",
        );
      }
      placed.push(chosen.box);
      result[item.id] = chosen;
    });
    return result;
  }

  // ---- reading the variables: JSON text or the value itself, checked, with errors that name the variable ----------

  var KINDS = ["city", "focus", "region", "via"];
  var MAX_PINS = 6; // the limit of things moving at once (tokens.json, limits); one pin with its dot and label is one thing

  function parseJson(name, value) {
    if (typeof value !== "string") return value;
    try {
      return JSON.parse(value);
    } catch (err) {
      throw new Error("map-route: " + name + " is not valid JSON (" + err.message + ")");
    }
  }

  function readCamera(name, raw) {
    var cam = parseJson(name, raw);
    ["lon", "lat", "span"].forEach(function (key) {
      if (!cam || typeof cam[key] !== "number" || !Number.isFinite(cam[key])) {
        throw new Error("map-route: " + name + " needs numbers lon, lat and span");
      }
    });
    if (cam.span <= 0 || cam.span > 360) throw new Error("map-route: " + name + ".span must be between 0 and 360 degrees");
    return cam;
  }

  function findCity(table, id) {
    if (table[id]) return table[id];
    var wanted = id.toLowerCase();
    for (var key in table) {
      if (key.toLowerCase() === wanted || table[key].name.toLowerCase() === wanted) return table[key];
    }
    return null;
  }

  // The pins as {id, kind, label, side, lon, lat}: a pin without lon and lat is looked up in the region's cities table.
  function readPins(raw, cities, region) {
    var list = parseJson("pins", raw);
    if (!Array.isArray(list) || list.length === 0) throw new Error("map-route: pins must be a list with at least one pin");
    var seen = {};
    var pins = list.map(function (pin, index) {
      var at = "pins[" + index + "]";
      if (!pin || typeof pin !== "object" || typeof pin.id !== "string" || !pin.id) throw new Error("map-route: " + at + " needs an id");
      if (seen[pin.id]) throw new Error("map-route: pin id " + pin.id + " appears twice");
      seen[pin.id] = true;
      var kind = pin.kind === undefined ? "city" : pin.kind;
      if (KINDS.indexOf(kind) < 0) throw new Error("map-route: " + at + ".kind must be city, focus, region or via");
      var lon = pin.lon;
      var lat = pin.lat;
      var city = null;
      if (lon === undefined || lat === undefined) {
        city = findCity(cities || {}, pin.id);
        if (!city) throw new Error("map-route: pin " + pin.id + " has no lon and lat and is not in the cities table of " + region);
        lon = city.lon;
        lat = city.lat;
      }
      if (typeof lon !== "number" || typeof lat !== "number" || !Number.isFinite(lon) || !Number.isFinite(lat)) {
        throw new Error("map-route: pin " + pin.id + " needs numbers lon and lat");
      }
      var label = "";
      if (kind !== "via") {
        label = typeof pin.label === "string" && pin.label.trim() ? pin.label.trim() : city ? city.name : "";
        if (!label) throw new Error("map-route: pin " + pin.id + " needs a label");
      }
      var side = pin.labelSide === undefined ? "right" : pin.labelSide;
      if (SIDES.indexOf(side) < 0) throw new Error("map-route: pin " + pin.id + ": labelSide must be right, left, top or bottom");
      return { id: pin.id, kind: kind, label: label, side: side, lon: lon, lat: lat };
    });
    var shown = pins.filter(function (pin) {
      return pin.kind !== "via";
    });
    if (shown.length > MAX_PINS) throw new Error("map-route: at most " + MAX_PINS + " pins with a label or a dot (got " + shown.length + ")");
    return pins;
  }

  // The pin ids of the route, in order, each one a pin; [] is no route and one id is an error.
  function readRoute(raw, pins) {
    var ids = parseJson("route", raw);
    if (!Array.isArray(ids)) throw new Error("map-route: route must be a list of pin ids");
    if (ids.length === 1) throw new Error("map-route: a route needs at least two pins");
    var known = {};
    pins.forEach(function (pin) {
      known[pin.id] = true;
    });
    ids.forEach(function (id) {
      if (!known[id]) throw new Error("map-route: route names " + id + ", which is not a pin");
    });
    return ids;
  }

  // ---- the date and the caption in a top corner -------------------------------------------------------------------

  // The corner for the date and the caption: the left one unless a dot or the route is in its box, then the right one.
  // `dots` are boxes, `route` points. Returns {side, box} or null when there is no text; throws when both corners are taken.
  function cornerLayout(yearLabel, caption, sizes, safe, dots, route, pad) {
    var width = 0;
    var height = 0;
    if (yearLabel) {
      // the date wraps at the same width as the caption, as the browser wraps it
      var yearLines = wrapLines(yearLabel, sizes.year, ADVANCE_DISPLAY, sizes.captionWidth);
      width = Math.max.apply(
        null,
        yearLines.map(function (line) {
          return textWidth(line, sizes.year, ADVANCE_DISPLAY, 0) * 1.06;
        }),
      );
      height = yearLines.length * sizes.year * 1.1;
    }
    if (caption) {
      var lines = wrapLines(caption, sizes.label, ADVANCE_LABEL, sizes.captionWidth);
      width = Math.max.apply(
        null,
        [width].concat(
          lines.map(function (line) {
            return textWidth(line, sizes.label, ADVANCE_LABEL, 0);
          }),
        ),
      );
      height += (yearLabel ? sizes.gap : 0) + lines.length * sizes.label * 1.25;
    }
    if (width === 0) return null;
    var candidates = [
      { side: "left", box: { x0: safe.x0, y0: safe.y0, x1: safe.x0 + width, y1: safe.y0 + height } },
      { side: "right", box: { x0: safe.x1 - width, y0: safe.y0, x1: safe.x1, y1: safe.y0 + height } },
    ];
    var free = candidates.filter(function (candidate) {
      return (
        !dots.some(function (dot) {
          return overlaps(candidate.box, dot, pad);
        }) && !crossesLine(candidate.box, route, pad)
      );
    })[0];
    if (!free) throw new Error("map-route: both top corners are taken by pins or the route; move the camera to free one for the date and caption");
    return free;
  }

  // ---- the timeline: when everything starts, and how long the slot has to be ------------------------------------------

  function wordCount(text) {
    return text.split(/\s+/).filter(Boolean).length;
  }

  // The schedule. `t` holds the token durations (enter, exit, micro, staggerTotal, cinematicMin, cinematicMax, readMin,
  // readPerWord); `shown` the pins with a dot or a label, in order. The map moves first (2.0 s, the cinematic maximum, plus
  // a second per unit of log zoom and per screen width of pan, at most 4 s); the pins then arrive one after another (the
  // stagger of the whole group at most `staggerTotal`), a label 0.2 s after its dot; the route follows, then the date and
  // the caption; the hold is max(read_min, read_per_word per word), then the fade out. Returns every start time and `needed`,
  // the least number of seconds the slot may have.
  function planTimeline(input) {
    var t = input.t;
    var move = 0;
    if (!input.locked) {
      var pan = Math.hypot(
        (input.poseTo.cx - input.poseFrom.cx) * input.poseTo.s,
        (input.poseTo.cy - input.poseFrom.cy) * input.poseTo.s,
      );
      move = Math.min(4, t.cinematicMax + Math.abs(Math.log(input.poseTo.s / input.poseFrom.s)) + pan / input.width);
    }
    var start = Math.max(t.enter, move);
    var stagger = input.shown.length > 1 ? Math.min(0.25, t.staggerTotal / (input.shown.length - 1)) : 0;
    var pinsEnd = start;
    var arrivals = input.shown.map(function (pin, index) {
      var at = start + index * stagger;
      var labelAt = pin.kind === "region" ? at : at + 0.2;
      pinsEnd = Math.max(pinsEnd, labelAt + t.enter, pin.kind === "region" ? 0 : at + t.micro);
      return { id: pin.id, at: at, labelAt: labelAt };
    });
    var hasRoute = input.routeLength > 0;
    var routeSeconds = hasRoute ? Math.min(t.cinematicMax, Math.max(t.cinematicMin, input.routeLength / 700)) : 0;
    var yearAt = pinsEnd;
    var captionAt = input.yearLabel ? yearAt + 0.25 : yearAt;
    var entranceEnd = Math.max(
      pinsEnd,
      hasRoute ? pinsEnd + routeSeconds : 0,
      input.yearLabel ? yearAt + t.enter : 0,
      input.caption ? captionAt + t.enter : 0,
    );
    var words = input.shown.reduce(function (sum, pin) {
      return sum + wordCount(pin.label);
    }, wordCount(input.yearLabel) + wordCount(input.caption));
    var hold = Math.max(t.readMin, t.readPerWord * words);
    return {
      moveSeconds: move,
      arrivals: arrivals,
      pinsEnd: pinsEnd,
      routeAt: pinsEnd,
      routeSeconds: routeSeconds,
      yearAt: yearAt,
      captionAt: captionAt,
      entranceEnd: entranceEnd,
      hold: hold,
      needed: entranceEnd + hold + t.exit,
    };
  }

  // The error text for a slot that is too short, or "" when `durationSeconds` fits the plan.
  function slotProblem(plan, durationSeconds, exitSeconds) {
    var duration = Number.parseFloat(durationSeconds);
    if (Number.isFinite(duration) && duration >= plan.needed) return "";
    return (
      "map-route: durationSeconds " + durationSeconds + " is shorter than the " + plan.needed.toFixed(2) +
      " s it needs (map move " + plan.moveSeconds.toFixed(2) + " s, pins and route to " + plan.entranceEnd.toFixed(2) +
      " s, a hold of " + plan.hold.toFixed(2) + " s, fade out " + exitSeconds.toFixed(2) + " s)"
    );
  }

  var lib = {
    MAX_PINS: MAX_PINS,
    readCamera: readCamera,
    readPins: readPins,
    readRoute: readRoute,
    cornerLayout: cornerLayout,
    planTimeline: planTimeline,
    slotProblem: slotProblem,
    ADVANCE_LABEL: ADVANCE_LABEL,
    ADVANCE_DISPLAY: ADVANCE_DISPLAY,
    makeProjection: makeProjection,
    cameraPose: cameraPose,
    cameraTransform: cameraTransform,
    insidePolygon: insidePolygon,
    splinePath: splinePath,
    graticulePath: graticulePath,
    textWidth: textWidth,
    wrapLines: wrapLines,
    placeLabels: placeLabels,
  };
  root.__MOTION_MAP_LIB = lib;
  if (typeof module !== "undefined" && module.exports) module.exports = lib;
})(typeof window !== "undefined" ? window : globalThis);
