"""Ensure the package is importable when pytest is invoked directly.

The repo uses a src-layout. Normally `uv run pytest` (or any uv-launched process)
puts the package on the path, but a bare `python -m pytest` may not pick up the
editable install's .pth in every environment. Prepending ``src`` here makes the
test suite import-robust no matter how it is launched.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
