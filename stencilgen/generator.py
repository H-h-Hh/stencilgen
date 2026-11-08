"""High level: turn a Gerber manufacturing package into stencil 3D."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field

from . import geom
from . import gerber
from . import kernel as K

OUTLINE_PAT = re.compile(r"(board.?outline|edge.?cuts|outline|\.gko$|\.gm1$)", re.I)
TOP_PASTE_PAT = re.compile(r"(top.?paste|f_paste|\.gtp$)", re.I)
BOT_PASTE_PAT = re.compile(r"(bottom.?paste|b_paste|\.gbp$)", re.I)


class StencilError(RuntimeError):
    pass


def _file_function(path):
    return gerber.file_function(path)


@dataclass
class Options:
    thickness: float = 0.12
    margin: float = 0.0            # border around the board outline
    board_thickness: float = 1.6
    fixture: bool = False
    fixture_border: float = 6.0
    fixture_height: float = 4.0
    board_clearance: float = 0.15
    stencil_clearance: float = 0.10
    formats: tuple = ("step", "stl", "dxf", "svg")


@dataclass
class Report:
    source: str = ""
    board_size: tuple = (0.0, 0.0)
    board_area: float = 0.0
    sides: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def as_text(self):
        w, h = self.board_size
        lines = [f"source      : {self.source}",
                 f"board       : {w:.3f} x {h:.3f} mm  ({self.board_area:.1f} mm2)"]
        for side, info in self.sides.items():
            mw = info["min_web"]
            web = f"{mw:.3f}" if mw == mw else "n/a"
            lines.append(f"{side:<12}: {info['count']} apertures, "
                         f"{info['area']:.2f} mm2 paste, "
                         f"min web {web} mm")
            for t in info.get("trimmed", []):
                lines.append(f"              ! aperture at ({t[0]:.2f}, {t[1]:.2f}) "
                             f"{t[2]:.3f} mm2 ({t[3]:.0f}% inside board)")
        for w_ in self.warnings:
            lines.append(f"WARNING     : {w_}")
        return "\n".join(lines)


class Job:
    """One manufacturing package (zip or folder)."""

    def __init__(self, source, log=print):
        self.source = os.path.abspath(source)
        self.log = log
        self.root = None
        self.layers = {}
        self._tmp = None
        self._board = None
        self._faces = {}

    # ---------------------------------------------------------------- input
    def load(self):
        if not os.path.exists(self.source):
            raise StencilError(f"not found: {self.source}")
        if os.path.isdir(self.source):
            self.root = self.source
        elif zipfile.is_zipfile(self.source):
            self._tmp = tempfile.mkdtemp(prefix="stencilgen_")
            with zipfile.ZipFile(self.source) as z:
                z.extractall(self._tmp)
            self.root = self._tmp
        else:
            raise StencilError("expected a .zip of gerbers or a folder of gerbers")
        self.layers = self._discover(self.root)
        if "outline" not in self.layers:
            raise StencilError("no board outline layer found (GKO / Edge_Cuts / *Outline*)")
        if "top_paste" not in self.layers and "bottom_paste" not in self.layers:
            raise StencilError("no paste-mask layer found (GTP/GBP or F_Paste/B_Paste)")
        return self

    def _discover(self, root):
        found = {}
        for dirpath, _, files in os.walk(root):
            for f in sorted(files):
                p = os.path.join(dirpath, f)
                try:
                    if os.path.getsize(p) == 0:
                        continue
                except OSError:
                    continue

                # Gerber X2 states the layer role explicitly - trust it first
                ff = _file_function(p)
                if ff:
                    key = None
                    if "paste" in ff:
                        if "top" in ff:
                            key = "top_paste"
                        elif "bot" in ff:
                            key = "bottom_paste"
                    elif "profile" in ff:
                        key = "outline"
                    if key and key not in found:
                        found[key] = p
                        continue

                # otherwise fall back to the usual file naming
                if "top_paste" not in found and TOP_PASTE_PAT.search(f):
                    found["top_paste"] = p
                elif "bottom_paste" not in found and BOT_PASTE_PAT.search(f):
                    found["bottom_paste"] = p
                elif "outline" not in found and OUTLINE_PAT.search(f):
                    found["outline"] = p
        return found

    def cleanup(self):
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    # --------------------------------------------------------------- board
    def board(self):
        """(outer_pts, hole_pts_list) of the board outline."""
        if self._board is not None:
            return self._board
        lay = gerber.parse(self.layers["outline"])
        loops = gerber.region_polygons(lay) or geom.chain(lay.polylines)
        loops = [geom.dedupe(l) for l in loops if abs(gerber.area(l)) > 0.05]
        if not loops:
            raise StencilError("board outline is empty")
        loops.sort(key=lambda p: -abs(gerber.area(p)))
        self._board = (loops[0], loops[1:])
        return self._board

    def board_face(self, margin=0.0, keep_holes=True):
        outer, holes = self.board()
        if abs(margin) > 1e-9:
            return geom.offset_face([outer], margin)
        return geom.face_of([outer] + (holes if keep_holes else []))

    def inside_board(self, pt):
        outer, holes = self.board()

        def in_poly(poly):
            x, y = pt
            c = False
            n = len(poly)
            for i in range(n):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % n]
                if (y1 > y) != (y2 > y):
                    xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
                    if x < xin:
                        c = not c
            return c

        if not in_poly(outer):
            return False
        return not any(in_poly(h) for h in holes)

    # ------------------------------------------------------------ apertures
    def apertures(self, side):
        key = f"{side}_paste"
        if key not in self.layers:
            raise StencilError(f"no {side} paste layer in package")
        return gerber.polygons(gerber.parse(self.layers[key]))

    def aperture_groups(self, side):
        """Flattened rings, for drawing only."""
        pol = gerber.flash_groups(gerber.parse(self.layers[f"{side}_paste"]))
        return [c for g in pol for c in g]

    def aperture_faces(self, side):
        """(faces, ring-groups) - one face per flashed pad, macro pads fused."""
        if side in self._faces:
            return self._faces[side]
        key = f"{side}_paste"
        if key not in self.layers:
            raise StencilError(f"no {side} paste layer in package")

        groups = gerber.all_groups(gerber.parse(self.layers[key]))
        faces, kept, dropped = [], [], 0
        for rings in groups:
            rings = [geom.dedupe(r) for r in rings]
            rings = [r for r in rings if len(r) >= 3]
            if not rings:
                dropped += 1
                continue
            built = []
            for r in rings:
                try:
                    built.append(geom.face_of([r]))
                except Exception:
                    continue
            if not built:
                dropped += 1
                continue

            f = built[0]
            for g in built[1:]:          # aperture macros are a union of primitives
                try:
                    f = geom.fuse(f, g)
                except Exception:
                    pass
            if geom.area(f) > 1e-6:
                faces.append(f)
                kept.append(rings)
            else:
                dropped += 1

        if not faces:
            raise StencilError(f"{side} paste layer produced no usable apertures")
        if dropped:
            self.log(f"  warning: {dropped} degenerate aperture(s) skipped")
        self._faces[side] = (faces, kept)
        return self._faces[side]

    # ------------------------------------------------------------- analysis
    def analyse(self, side):
        faces, groups = self.aperture_faces(side)
        trimmed = []
        for f, rings in zip(faces, groups):
            p = rings[0]
            x0, y0, _, x1, y1, _ = geom.bbox(f)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if not self.inside_board((cx, cy)):
                inside = sum(1 for q in p if self.inside_board(q))
                trimmed.append((cx, cy, geom.area(f), 100.0 * inside / max(len(p), 1)))
        flat = [r for g in groups for r in g]
        return {"count": len(faces),
                "area": sum(geom.area(f) for f in faces),
                "trimmed": trimmed,
                "min_web": self.min_web(groups)}

    def min_web(self, groups, limit=800):
        """Smallest gap between *different* pads (rings of one pad are ignored)."""
        if len(groups) > limit:
            return float("nan")

        def bbox_of(g):
            xs = [p[0] for r in g for p in r]
            ys = [p[1] for r in g for p in r]
            return min(xs), min(ys), max(xs), max(ys)

        boxes = [bbox_of(g) for g in groups]
        best = float("inf")
        for i in range(len(groups)):
            x0, y0, x1, y1 = boxes[i]
            for j in range(i + 1, len(groups)):
                a0, b0, a1, b1 = boxes[j]
                if a0 > x1 + 1.0 or x0 > a1 + 1.0 or b0 > y1 + 1.0 or y0 > b1 + 1.0:
                    continue
                d = min(_poly_dist(ra, rb) for ra in groups[i] for rb in groups[j])
                if d < best:
                    best = d
        return best if best < float("inf") else float("nan")

    # -------------------------------------------------------------- stencil
    def stencil(self, side, opts):
        t = opts.thickness
        plate = geom.extrude(self.board_face(opts.margin), t)
        faces, _ = self.aperture_faces(side)
        # cut with prisms tall enough to pass through the sheet on both sides
        tools = [geom.extrude(f, t * 3.0, -t) for f in faces]
        solid = geom.cut(plate, geom.compound(tools))
        solid = geom.clean(solid)
        return solid, geom.volume(solid) / t

    # -------------------------------------------------------------- fixture
    def fixture(self, side, opts):
        out_margin = max(opts.fixture_border, opts.margin + 2.0)
        outer = self.board_face(out_margin, keep_holes=False)
        t_b, t_s = opts.board_thickness, opts.thickness
        height = opts.fixture_height
        floor = height - t_b - t_s
        if floor <= 0.6:
            raise StencilError("fixture height too small for that board/stencil thickness")

        body = geom.extrude(outer, height)

        pcb_pocket = geom.extrude(self.board_face(opts.board_clearance, keep_holes=False),
                                  t_b + t_s + 0.5, floor)
        st_pocket = geom.extrude(
            self.board_face(opts.margin + opts.stencil_clearance, keep_holes=False),
            t_s + 0.5, floor + t_b)
        body = geom.cut(body, geom.compound([pcb_pocket, st_pocket]))

        x0, y0, _, x1, y1, _ = geom.bbox(outer)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        lift_w, lift_d, lift_z = 10.0, 8.0, 0.8
        if (y1 - y0) > lift_d * 2 + 2:
            for yc in (y1 - lift_d / 2 + 2.0, y0 + lift_d / 2 - 2.0):
                body = geom.cut(body, geom.box(lift_w, lift_d, lift_z,
                                               (cx, yc, height - lift_z / 2)))

        if min(x1 - x0, y1 - y0) > 24:
            body = geom.cut(body, geom.cylinder(5.0, floor + 1.0, (cx, cy, -0.5)))

        return geom.clean(body), floor

    # --------------------------------------------------------------- export
    def export(self, shape, path, fmt):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if fmt == "step":
            geom.export_step(shape, path)
        elif fmt == "stl":
            geom.export_stl(shape, path)
        return path


def _seg_dist(p1, p2, p3, p4):
    import numpy as np

    def pt_seg(p, a, b):
        ab = b - a
        t = float(((p - a) @ ab) / max(float(ab @ ab), 1e-12))
        t = min(1.0, max(0.0, t))
        return float((((p - (a + t * ab)) ** 2).sum()) ** 0.5)

    p1, p2, p3, p4 = (np.array(v, float) for v in (p1, p2, p3, p4))
    r, s = p2 - p1, p4 - p3
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) > 1e-12:
        qp = p3 - p1
        t = (qp[0] * s[1] - qp[1] * s[0]) / den
        u_ = (qp[0] * r[1] - qp[1] * r[0]) / den
        if 0 <= t <= 1 and 0 <= u_ <= 1:
            return 0.0
    return min(pt_seg(p1, p3, p4), pt_seg(p2, p3, p4),
               pt_seg(p3, p1, p2), pt_seg(p4, p1, p2))


def _poly_dist(a, b):
    d = float("inf")
    for i in range(len(a)):
        p1, p2 = a[i], a[(i + 1) % len(a)]
        for j in range(len(b)):
            q1, q2 = b[j], b[(j + 1) % len(b)]
            dd = _seg_dist(p1, p2, q1, q2)
            if dd < d:
                d = dd
                if d < 1e-4:
                    return d
    return d
