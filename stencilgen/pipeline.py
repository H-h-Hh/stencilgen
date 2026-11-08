"""End-to-end run: package in, stencil + fixture files out."""
from __future__ import annotations

import os

from . import geom
from .generator import Job, Options, Report, StencilError


def _polys_path(outer, holes, polys, size=1100, pad=20):
    xs = [p[0] for p in outer] + [p[0] for q in polys for p in q]
    ys = [p[1] for p in outer] + [p[1] for q in polys for p in q]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    s = (size - 2 * pad) / max(x1 - x0, y1 - y0, 1e-6)

    def xy(p):
        return (pad + (p[0] - x0) * s, pad + (y1 - p[1]) * s)

    def d(pts):
        return "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in (xy(p) for p in pts)) + " Z"

    body = [f'<path d="{d(outer)}" fill="#2b2b2b" fill-rule="evenodd"/>']
    for h in holes:
        body.append(f'<path d="{d(h)}" fill="#ffffff"/>')
    for p in polys:
        body.append(f'<path d="{d(p)}" fill="#ffffff"/>')
    w = hgt = size
    return w, hgt, "\n".join(body)


def preview_svg(path, outer, holes, polys, title=""):
    w, h, body = _polys_path(outer, holes, polys)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'viewBox="0 0 {w} {h}">\n'
           f'<rect width="100%" height="100%" fill="#ffffff"/>\n{body}\n'
           f'<text x="12" y="{h-8}" font-family="sans-serif" font-size="14" '
           f'fill="#666">{title}</text>\n</svg>\n')
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return path


def write_dxf(path, outer, holes, polys):
    try:
        import ezdxf
    except ImportError:
        return None
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$INSUNITS"] = 4
    doc.layers.add("STENCIL_OUTLINE", color=1)
    doc.layers.add("STENCIL_APERTURE", color=3)
    msp = doc.modelspace()
    msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "STENCIL_OUTLINE"})
    for h in holes:
        msp.add_lwpolyline(h, close=True, dxfattribs={"layer": "STENCIL_OUTLINE"})
    for p in polys:
        msp.add_lwpolyline(p, close=True, dxfattribs={"layer": "STENCIL_APERTURE"})
    doc.saveas(path)
    return path


def run(source, opts=None, outdir=None, log=print):
    opts = opts or Options()
    job = Job(source, log=log).load()
    try:
        outer, holes = job.board()
        bb = job.board_face()
        bx0, by0, _, bx1, by1, _ = geom.bbox(bb)

        stem = os.path.splitext(os.path.basename(source.rstrip("\\/")))[0]
        outdir = outdir or os.path.join(os.path.dirname(os.path.abspath(source)),
                                        f"{stem}_stencil")
        os.makedirs(outdir, exist_ok=True)

        rep = Report(source=source,
                     board_size=(bx1 - bx0, by1 - by0),
                     board_area=geom.area(bb))
        log(f"board {rep.board_size[0]:.2f} x {rep.board_size[1]:.2f} mm, "
            f"{len(holes)} internal cutout(s)")

        sides = [s for s in ("top", "bottom") if f"{s}_paste" in job.layers]
        if not sides:
            raise StencilError("no paste layer")

        built = {}
        for side in sides:
            log(f"[{side}] building stencil ...")
            solid, face_area = job.stencil(side, opts)
            base = os.path.join(outdir, f"stencil_{side}")
            if "step" in opts.formats:
                job.export(solid, base + ".step", "step")
            if "stl" in opts.formats:
                job.export(solid, base + ".stl", "stl")
            polys = job.aperture_groups(side)
            if "dxf" in opts.formats and write_dxf(base + ".dxf", *job.board(), polys):
                pass
            if "svg" in opts.formats:
                preview_svg(base + ".svg", outer, holes, polys,
                            f"{side} stencil  t={opts.thickness} mm")
            info = job.analyse(side)
            info["face_area"] = face_area
            rep.sides[side] = info
            built[side] = solid
            mw = info["min_web"]
            web = f"{mw:.3f}" if mw == mw else "n/a"
            log(f"  {info['count']} apertures, {info['area']:.2f} mm2, "
                f"min web {web} mm")
            if info["trimmed"]:
                rep.warnings.append(
                    f"{side}: {len(info['trimmed'])} aperture(s) outside the board outline "
                    f"were cut away")

        if opts.fixture:
            side = "top" if "top" in built else sides[0]
            log(f"[fixture] building frame for the {side} stencil ...")
            body, floor = job.fixture(side, opts)
            job.export(body, os.path.join(outdir, "fixture.step"), "step")
            job.export(body, os.path.join(outdir, "fixture.stl"), "stl")
            fx0, fy0, fz0, fx1, fy1, fz1 = geom.bbox(body)
            log(f"  frame {fx1-fx0:.2f} x {fy1-fy0:.2f} "
                f"x {fz1-fz0:.2f} mm, floor {floor:.3f} mm")

        with open(os.path.join(outdir, "report.txt"), "w", encoding="utf-8") as f:
            f.write(rep.as_text() + "\n")
        log(f"done -> {outdir}")
        return rep, outdir
    finally:
        job.cleanup()
