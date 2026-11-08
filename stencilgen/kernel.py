"""Thin OpenCASCADE (OCP) layer - the few operations stencilgen needs.

Using OCP directly instead of CadQuery keeps the packaged app ~4x smaller:
CadQuery's import chain drags in VTK, SciPy, numba and casadi, none of which
a prismatic stencil or a stepped frame ever touches.
"""
from __future__ import annotations

import math

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform)
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_JoinType
from OCP.GProp import GProp_GProps
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.StlAPI import StlAPI_Writer
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Face, TopoDS_Shape, TopoDS_Wire
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec


# --------------------------------------------------------------------------- #
# construction
# --------------------------------------------------------------------------- #
def wire_of(pts) -> TopoDS_Wire:
    """Closed polygonal wire on z=0."""
    mk = BRepBuilderAPI_MakeWire()
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
            continue
        mk.Add(BRepBuilderAPI_MakeEdge(
            gp_Pnt(float(a[0]), float(a[1]), 0.0),
            gp_Pnt(float(b[0]), float(b[1]), 0.0)).Edge())
    if not mk.IsDone():
        raise ValueError("could not build wire")
    return mk.Wire()


def face_of(rings) -> TopoDS_Face:
    """Planar face from [outer, hole, ...] point rings (mm, z=0)."""
    mk = BRepBuilderAPI_MakeFace(wire_of(rings[0]), True)
    for h in rings[1:]:
        # inner rings must run opposite to the outer one, or OCC adds them instead
        mk.Add(TopoDS.Wire_s(wire_of(h).Reversed()))
    mk.Build()
    if not mk.IsDone():
        raise ValueError("could not build face")
    f = mk.Face()
    if bbox(f)[5] - bbox(f)[2] > 1e-6:  # not planar in z
        raise ValueError("face is not planar")
    return f


def extrude(face, height, z0=0.0) -> TopoDS_Shape:
    s = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, float(height))).Shape()
    return translate(s, (0, 0, z0)) if z0 else s


def box(dx, dy, dz, center=(0, 0, 0)) -> TopoDS_Shape:
    b = BRepPrimAPI_MakeBox(gp_Pnt(-dx / 2, -dy / 2, -dz / 2), dx, dy, dz).Shape()
    return translate(b, center)


def cylinder(radius, height, base=(0, 0, 0)) -> TopoDS_Shape:
    """Cylinder along +Z starting at `base`."""
    c = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), radius, height).Shape()
    return translate(c, base)


def translate(shape, vec) -> TopoDS_Shape:
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(*[float(v) for v in vec]))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def compound(shapes) -> TopoDS_Shape:
    shapes = [s for s in shapes if s is not None]
    if len(shapes) == 1:
        return shapes[0]
    b = BRep_Builder()
    c = TopoDS_Compound()
    b.MakeCompound(c)
    for s in shapes:
        b.Add(c, s)
    return c


# --------------------------------------------------------------------------- #
# booleans
# --------------------------------------------------------------------------- #
def cut(shape, tool) -> TopoDS_Shape:
    op = BRepAlgoAPI_Cut(shape, tool)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("boolean cut failed")
    return op.Shape()


def fuse(a, b) -> TopoDS_Shape:
    op = BRepAlgoAPI_Fuse(a, b)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("boolean fuse failed")
    return op.Shape()


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #
def volume(shape) -> float:
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return p.Mass()


def area(shape) -> float:
    p = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, p)
    return p.Mass()


def bbox(shape):
    """(xmin, ymin, zmin, xmax, ymax, zmax)"""
    b = Bnd_Box()
    b.SetGap(0.0)
    BRepBndLib.Add_s(shape, b)
    return b.Get()


def faces(shape):
    out = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        out.append(TopoDS.Face_s(ex.Current()))
        ex.Next()
    return out


def wires(shape):
    out = []
    ex = TopExp_Explorer(shape, TopAbs_WIRE)
    while ex.More():
        out.append(TopoDS.Wire_s(ex.Current()))
        ex.Next()
    return out


def clean(shape) -> TopoDS_Shape:
    up = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    up.Build()
    return up.Shape()


def offset_face(rings, margin) -> TopoDS_Face:
    """Grow the outer ring of a face by `margin` mm."""
    base = face_of([rings[0]])
    w = wires(base)[0]
    best = None
    for sign in (1.0, -1.0):
        op = BRepOffsetAPI_MakeOffset(w, GeomAbs_JoinType.GeomAbs_Arc, False)
        op.Perform(sign * margin)
        if not op.IsDone():
            continue
        for cw in wires(op.Shape()):
            mk = BRepBuilderAPI_MakeFace(cw, True)
            if not mk.IsDone():
                continue
            f = mk.Face()
            a = area(f)
            if best is None or a > best[0]:
                best = (a, f)
    if best is None or best[0] <= area(base):
        raise RuntimeError("outline offset failed (self-intersecting outline?)")
    return best[1]


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def export_step(shape, path):
    # the writer must exist before the interface statics take effect
    w = STEPControl_Writer()
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    # all faces are planar + straight edges, so surface curves only bloat the file
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 0)
    if not w.Transfer(shape, STEPControl_AsIs):
        raise RuntimeError("STEP transfer failed")
    if w.Write(path) != 1:  # IFSelect_RetDone
        raise RuntimeError("STEP write failed")
    return path


def export_stl(shape, path, deflection=0.02, angular=0.2):
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)
    w = StlAPI_Writer()
    w.ASCIIMode = False
    if not w.Write(shape, path):
        raise RuntimeError("STL write failed")
    return path
