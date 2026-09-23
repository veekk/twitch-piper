#!/usr/bin/env python3
"""Application entry point; speech helpers remain importable for compatibility."""
from engine import *

if __name__ == '__main__':
    from qt_app import main
    raise SystemExit(main())
