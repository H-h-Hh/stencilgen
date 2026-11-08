"""Command line entry point:  python -m stencilgen <package.zip|folder> [options]"""
from __future__ import annotations

import argparse
import os
import sys

from .generator import Options, StencilError
from .pipeline import run


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="stencilgen",
        description="Generate solder-paste stencil 3D models from a Gerber "
                    "package (zip or folder).")
    p.add_argument("source", help="gerber .zip or folder")
    p.add_argument("-t", "--thick", type=float, default=0.12,
                   help="stencil thickness in mm (default 0.12)")
    p.add_argument("-m", "--margin", type=float, default=0.0,
                   help="border around the board outline in mm (default 0)")
    p.add_argument("-o", "--out", default=None, help="output folder")
    p.add_argument("--board-thickness", type=float, default=1.6,
                   help="PCB thickness in mm, used by the fixture (default 1.6)")
    p.add_argument("--fixture", action="store_true", help="also build the holder frame")
    p.add_argument("--fixture-border", type=float, default=6.0)
    p.add_argument("--fixture-height", type=float, default=4.0)
    p.add_argument("--board-clearance", type=float, default=0.15)
    p.add_argument("--stencil-clearance", type=float, default=0.10)
    p.add_argument("--no-dxf", action="store_true", help="skip the 2D cut drawing")
    p.add_argument("--no-stl", action="store_true", help="skip the mesh export")
    args = p.parse_args(argv)

    fmt = ["step", "svg"]
    if not args.no_dxf:
        fmt.append("dxf")
    if not args.no_stl:
        fmt.append("stl")

    opts = Options(thickness=args.thick, margin=args.margin,
                   board_thickness=args.board_thickness,
                   fixture=args.fixture,
                   fixture_border=args.fixture_border,
                   fixture_height=args.fixture_height,
                   board_clearance=args.board_clearance,
                   stencil_clearance=args.stencil_clearance,
                   formats=tuple(dict.fromkeys(fmt)))

    try:
        rep, outdir = run(args.source, opts, outdir=args.out)
    except StencilError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print()
    print(rep.as_text())
    print(f"output: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
