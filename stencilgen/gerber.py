"""RS-274X (Gerber) reader tuned for solder-paste stencils.

Handles what paste-mask and board-outline layers actually use:
  * %FS / %MO units (mm or inch)
  * apertures C / R / O and aperture macros (generic interpreter)
  * G36/G37 polygon regions
  * G01/G02/G03 moves (G75 multi-quadrant), D01/D02/D03
All returned coordinates are millimetres, in the file's own (top-view) frame.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

ARC_CHORD_MM = 0.01


# --------------------------------------------------------------------------- #
# geometry primitives
# --------------------------------------------------------------------------- #
def circle(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * k / n),
             cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def rect(cx, cy, w, h):
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
            (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]


def _rot(pts, cx, cy, ang):
    c, s = math.cos(ang), math.sin(ang)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c)
            for x, y in pts]


def obround(cx, cy, w, h, n=24):
    """Stadium: straight sides + semicircular ends."""
    if abs(w - h) < 1e-9:
        return circle(cx, cy, w / 2.0, 2 * n)
    if w < h:
        return _rot(obround(cx, cy, h, w, n), cx, cy, math.pi / 2)
    r, half = h / 2.0, (w - h) / 2.0
    pts = []
    for k in range(n + 1):
        a = -math.pi / 2 + math.pi * k / n
        pts.append((cx + half + r * math.cos(a), cy + r * math.sin(a)))
    for k in range(n + 1):
        a = math.pi / 2 + math.pi * k / n
        pts.append((cx - half + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def rounded_rect(cx, cy, w, h, r, n=8):
    r = min(r, w / 2.0, h / 2.0)
    if r <= 1e-9:
        return rect(cx, cy, w, h)
    hw, hh = w / 2.0 - r, h / 2.0 - r
    pts = []
    for sx, sy, a0 in ((1, -1, -math.pi / 2), (1, 1, 0.0),
                       (-1, 1, math.pi / 2), (-1, -1, math.pi)):
        for k in range(n + 1):
            a = a0 + (math.pi / 2) * k / n
            pts.append((cx + sx * hw + r * math.cos(a), cy + sy * hh + r * math.sin(a)))
    return pts


def area(poly):
    a = 0.0
    for i, (x1, y1) in enumerate(poly):
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return a / 2.0


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def file_function(path):
    """Gerber X2 %TF.FileFunction% value, lower-cased, or None."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
    except OSError:
        return None
    m = re.search(r"%TF\.FileFunction,([^*]+)\*%", head)
    return m.group(1).strip().lower() if m else None


@dataclass
class Layer:
    path: str = ""
    units: str = "mm"
    apertures: dict = field(default_factory=dict)
    macros: dict = field(default_factory=dict)       # macro name -> primitive strings
    regions: list = field(default_factory=list)      # [[contour, ...], ...]
    flashes: list = field(default_factory=list)      # [(x, y, shape, params)]
    polylines: list = field(default_factory=list)    # [[(x, y), ...], ...]


def _tokenize(txt):
    out, i, n = [], 0, len(txt)
    while i < n:
        c = txt[i]
        if c == "%":
            j = txt.index("%", i + 1)
            out.append(txt[i:j + 1])
            i = j + 1
        elif c in "*\r\n":
            i += 1
        else:
            j = txt.find("*", i)
            if j < 0:
                break
            t = txt[i:j].strip()
            if t:
                out.append(t)
            i = j + 1
    return out


def _num(tok, ch):
    m = re.search(ch + r"(-?\d+)", tok)
    return int(m.group(1)) if m else None


def _arc_pts(p0, p1, c, ccw):
    r = math.hypot(p0[0] - c[0], p0[1] - c[1])
    if r <= 1e-9:
        return [p1]
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
    if abs(sweep) < 1e-12:
        sweep = 2 * math.pi if ccw else -2 * math.pi
    step = (2 * math.acos(max(-1.0, min(1.0, 1 - ARC_CHORD_MM / r)))
            if r > ARC_CHORD_MM else math.pi / 8)
    n = max(2, int(abs(sweep) / max(step, 1e-3)) + 1)
    return [(c[0] + r * math.cos(a0 + sweep * k / n),
             c[1] + r * math.sin(a0 + sweep * k / n)) for k in range(1, n + 1)]


def parse(path):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        txt = fh.read()

    fs = re.search(r"%FSLAX(\d)(\d)Y(\d)(\d)", txt)
    dec = int(fs.group(2)) if fs else 5
    mo = re.search(r"%MO(MM|IN)", txt)
    units = "in" if (mo and mo.group(1) == "IN") else "mm"
    k = (10.0 ** -dec) * (25.4 if units == "in" else 1.0)   # file int -> mm
    u = 25.4 if units == "in" else 1.0                       # aperture unit -> mm

    lay = Layer(path=path, units=units)
    ap = {}
    macros = {}
    order = None
    x = y = 0.0
    mode = 1
    in_region = False
    cur = None
    contours = []
    stroke = None

    def close_stroke():
        nonlocal stroke
        if stroke and len(stroke) > 1:
            lay.polylines.append(stroke)
        stroke = None

    for tok in _tokenize(txt):
        if tok.startswith("%"):
            raw = tok[1:-1] if tok.endswith("%") else tok.strip("%")
            if raw.startswith("AM"):
                parts = [p.strip() for p in raw[2:].split("*") if p.strip()]
                if parts:
                    macros[parts[0]] = parts[1:]
                continue
            body = raw.rstrip("*").strip()
            if body.startswith("ADD"):
                m = re.match(r"ADD(\d+)([A-Za-z_.]+),?(.*)$", body)
                if m:
                    d = int(m.group(1))
                    shape = m.group(2)
                    raw = m.group(3).strip()
                    prm = [float(v) for v in raw.split("X")] if raw else []
                    ap[d] = (shape, prm)
            continue
        if tok in ("M02", "M00", "M01"):
            break
        if tok == "G36":
            in_region, cur, contours = True, None, []
            continue
        if tok == "G37":
            if cur:
                contours.append(cur)
            if contours:
                lay.regions.append(contours)
            in_region, cur, contours = False, None, []
            continue
        if tok in ("G74", "G75", "G54"):
            continue

        g = _num(tok, "G")
        if g in (1, 2, 3) and not re.search(r"[XY]", tok):
            mode = g
            continue
        if g in (2, 3):
            mode = g

        d = _num(tok, "D")
        nx, ny = _num(tok, "X"), _num(tok, "Y")
        if d is not None and d >= 10 and nx is None and ny is None:
            order = d
            continue
        if nx is None and ny is None:
            continue

        tx = nx * k if nx is not None else x
        ty = ny * k if ny is not None else y

        if d == 2:
            if in_region and cur:
                contours.append(cur)
                cur = None
            close_stroke()
            x, y = tx, ty
            continue

        if d == 3:
            shape, prm = ap.get(order, ("C", [0.2]))
            lay.flashes.append((tx, ty, shape, [v * u for v in prm]))
            x, y = tx, ty
            continue

        if mode in (2, 3):
            i_off = (_num(tok, "I") or 0) * k
            j_off = (_num(tok, "J") or 0) * k
            pts = _arc_pts((x, y), (tx, ty), (x + i_off, y + j_off), mode == 3)
        else:
            pts = [(tx, ty)]

        if in_region:
            if cur is None:
                cur = [(x, y)]
            cur.extend(pts)
        else:
            if stroke is None:
                stroke = [(x, y)]
            stroke.extend(pts)
        x, y = tx, ty

    if cur:
        contours.append(cur)
        lay.regions.append(contours)
    close_stroke()
    lay.apertures = ap
    lay.macros = macros
    return lay


# --------------------------------------------------------------------------- #
# polygons
# --------------------------------------------------------------------------- #
def _translate(pts, dx, dy):
    return [(x + dx, y + dy) for x, y in pts]


def _rotate(pts, deg):
    if not deg:
        return pts
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def _val(expr, prm):
    """Evaluate a macro field: literal, $n reference, or simple arithmetic."""
    e = str(expr).strip()
    if not e:
        return 0.0
    e = re.sub(r"\$(\d+)", lambda m: "(" + repr(float(prm[int(m.group(1)) - 1])) + ")", e)
    e = e.replace("x", "*").replace("X", "*")
    try:
        return float(eval(e, {"__builtins__": {}}, {}))  # noqa: S307 - digits/operators only
    except Exception:
        return 0.0


def _thick_line(p1, p2, width):
    """Segment drawn with a round-ended stroke, as [body, cap, cap]."""
    (x1, y1), (x2, y2) = p1, p2
    L = math.hypot(x2 - x1, y2 - y1)
    r = width / 2.0
    if L < 1e-9:
        return [circle(x1, y1, r)]
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    body = _translate(_rotate(rect(0, 0, L, width), ang), mx, my)
    return [body, circle(x1, y1, r), circle(x2, y2, r)]


def _macro_rings(prims, prm):
    """Evaluate an aperture macro into a list of rings (all treated as dark)."""
    rings = []
    for tok in prims:
        f = [s.strip() for s in tok.split(",")]
        try:
            code = int(float(f[0]))
        except Exception:
            continue
        try:
            if code == 1:                                   # circle
                d, cx, cy = _val(f[2], prm), _val(f[3], prm), _val(f[4], prm)
                rings.append(circle(cx, cy, abs(d) / 2.0))
            elif code == 20:                                # vector line
                # 20,exposure,width,x1,y1,x2,y2,rotation
                w = abs(_val(f[2], prm))
                p1 = (_val(f[3], prm), _val(f[4], prm))
                p2 = (_val(f[5], prm), _val(f[6], prm))
                rot = _val(f[7], prm) if len(f) > 7 else 0.0
                p1, p2 = _rotate([p1, p2], rot)
                rings.extend(_thick_line(p1, p2, w))
            elif code == 21:                                # center line
                # 21,exposure,width,height,cx,cy,rotation
                w, h = abs(_val(f[2], prm)), abs(_val(f[3], prm))
                cx, cy = _val(f[4], prm), _val(f[5], prm)
                rot = _val(f[6], prm) if len(f) > 6 else 0.0
                rings.append(_translate(_rotate(rect(0, 0, w, h), rot), cx, cy))
            elif code == 4:                                 # outline
                n = int(_val(f[2], prm))
                coords = f[3:3 + 2 * n]
                pts = [(_val(coords[2 * i], prm), _val(coords[2 * i + 1], prm))
                       for i in range(n)]
                rot = _val(f[3 + 2 * n], prm) if len(f) > 3 + 2 * n else 0.0
                rings.append(_rotate(pts, rot))
            elif code == 5:                                 # regular polygon
                n = max(3, int(_val(f[2], prm)))
                cx, cy = _val(f[3], prm), _val(f[4], prm)
                d = abs(_val(f[5], prm))
                rot = _val(f[6], prm) if len(f) > 6 else 0.0
                ring = [(d / 2.0 * math.cos(2 * math.pi * k / n),
                         d / 2.0 * math.sin(2 * math.pi * k / n)) for k in range(n)]
                rings.append(_translate(_rotate(ring, rot), cx, cy))
            elif code in (6, 7):                            # moire / thermal
                cx, cy = _val(f[2], prm), _val(f[3], prm)
                d = abs(_val(f[4], prm)) or abs(_val(f[5], prm))
                rings.append(circle(cx, cy, d / 2.0))
        except (IndexError, ValueError):
            continue
    return rings


def _std_rings(shape, prm):
    s = shape.upper()
    if s.startswith("C"):
        return [circle(0, 0, prm[0] / 2.0)]
    if s.startswith("R"):
        return [rect(0, 0, prm[0], prm[1] if len(prm) > 1 else prm[0])]
    if s.startswith("O"):
        return [obround(0, 0, prm[0], prm[1] if len(prm) > 1 else prm[0])]
    return [circle(0, 0, prm[0] / 2.0)]


def flash_groups(lay):
    """One entry per flashed aperture; each entry is a list of rings."""
    macros = {k.lower(): v for k, v in lay.macros.items()}
    groups = []
    for (x, y, shape, prm) in lay.flashes:
        if not prm and shape.lower() not in macros:
            continue
        rings = macros.get(shape.lower())
        rings = _macro_rings(rings, prm) if rings else _std_rings(shape, prm)
        rings = [r for r in rings if len(r) >= 3]
        if rings:
            groups.append([_translate(r, x, y) for r in rings])
    return groups


def region_polygons(lay):
    return [c for contours in lay.regions for c in contours if len(c) >= 3]


def flash_polygons(lay):
    return [ring for group in flash_groups(lay) for ring in group]


def all_groups(lay):
    """Every aperture as a ring group: regions first, then flashes."""
    return [[c] for c in region_polygons(lay)] + flash_groups(lay)


def polygons(lay):
    return region_polygons(lay) + flash_polygons(lay)
