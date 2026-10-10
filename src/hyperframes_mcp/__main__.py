"""``python -m hyperframes_mcp`` runs the same command line as the ``hyperframes-mcp`` command."""

import sys

from hyperframes_mcp.server.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
