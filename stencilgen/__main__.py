"""python -m stencilgen [package.zip] -> GUI when no argument is given."""
import sys


def main():
    if len(sys.argv) > 1:
        from .cli import main as cli_main
        raise SystemExit(cli_main())
    from .gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
