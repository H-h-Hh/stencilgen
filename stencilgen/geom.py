"""Polygon utilities and the bridge to the OCC kernel."""
from __future__ import annotations

import math

from . import kernel as K

TOL = 1e-7

# re-exported so the rest of the package only talks to geom
wire_of = K.wire_of
face_of = K.face_of
offset_face = K.offset_face
extrude = K.extrude
compound = K.compound
cut = K.cut
volume = K.volume
area = K.area
bbox = K.bbox
faces = K.faces
clean = K.clean
box = K.box
cylinder = K.cylinder
translate = K.translate
export_step = K.export_step
export_stl = K.export_stl


def dedupe(pts):
    out = []
    for p in pts:
        q = (float(p[0]), float(p[1]))
        if not out or math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > TOL:
            out.append(q)
    if len(out) > 2 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= TOL:
        out.pop()
    return out


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def chain(polylines, tol=0.6):
    """Stitch outline segments that share (almost) endpoints into closed loops."""
    polys = [list(p) for p in polylines]
    out = []
    while polys:
        cur = polys.pop(0)
        changed = True
        while changed and polys:
            changed = False
            for i, p in enumerate(polys):
                if dist(cur[-1], p[0]) <= tol:
                    cur = cur + p[1:]
                elif dist(cur[-1], p[-1]) <= tol:
                    cur = cur + p[::-1][1:]
                elif dist(cur[0], p[-1]) <= tol:
                    cur = p + cur[1:]
                elif dist(cur[0], p[0]) <= tol:
                    cur = p[::-1] + cur[1:]
                else:
                    continue
                polys.pop(i)
                changed = True
                break
        out.append(cur)
    return out
