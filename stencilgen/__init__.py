"""stencilgen - solder-paste stencil 3D generator from Gerber files."""

from .generator import Job, Options, Report, StencilError
from .pipeline import run, preview_svg, write_dxf

__version__ = "1.0.0"
__all__ = ["Job", "Options", "Report", "StencilError", "run", "preview_svg", "write_dxf"]
