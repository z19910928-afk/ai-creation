#!/usr/bin/env python3
"""Start Moyu from the project root with ``python server.py``."""

import sys

from backend import server as service


if __name__ == "__main__":
    service.main()
else:
    # Keep existing callers on the same mutable service module.
    sys.modules[__name__] = service
