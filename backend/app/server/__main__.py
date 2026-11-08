from __future__ import annotations

import uvicorn


def main() -> None:
    # Factory mode: uvicorn calls create_app() once it starts, so importing
    # backend.app.server.main never builds the app or touches the data folder.
    uvicorn.run(
        "backend.app.server.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=8765,
        reload=False,
    )


if __name__ == "__main__":
    main()
