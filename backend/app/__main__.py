"""`python -m backend.app` keeps working: it delegates to the server entrypoint."""

from __future__ import annotations

from backend.app.server.__main__ import main

if __name__ == "__main__":
    main()
