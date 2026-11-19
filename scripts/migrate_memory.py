"""Copy existing FTS5 memory rows into hierarchical ``memory_entries``.

Usage:
    uv run python scripts/migrate_memory.py
    uv run python scripts/migrate_memory.py --db data/memory.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.memory.migrate import migrate_db  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate FTS5 memory_kv rows into memory_entries")
    parser.add_argument(
        "--db",
        default="",
        help="Path to memory.db (default: LA_DATA_DIR/memory.db)",
    )
    args = parser.parse_args(argv)
    result = migrate_db(args.db or None)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
