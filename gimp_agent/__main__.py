"""python -m gimp_agent entrypoint."""

from __future__ import annotations

import sys

from gimp_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
