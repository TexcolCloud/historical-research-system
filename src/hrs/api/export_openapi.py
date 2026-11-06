"""Export the HTTP contract used by the frontend type generator."""

import json

from hrs.api.main import create_app
from hrs.config import project_root


def main():
    destination = project_root() / "web/openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
