import json
from pathlib import Path

from .api import create_app


def main():
    app = create_app()
    root = Path(__file__).resolve().parents[4]
    destination = root / "services/research-platform/openapi.json"
    destination.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
