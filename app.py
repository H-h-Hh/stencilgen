"""Single entry point used by `python app.py` and by the packaged exe.

No arguments  -> GUI
With a path   -> command line run (so a .zip can be dropped straight onto the exe)
"""
import multiprocessing
import sys


def _hide_console_for_gui():
    """Frozen console build: drop the console window when starting the GUI."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            import ctypes
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass


def main():
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:
        from stencilgen.cli import main as cli_main
        raise SystemExit(cli_main())
    _hide_console_for_gui()
    from stencilgen.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
