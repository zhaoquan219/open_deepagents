from __future__ import annotations

import argparse
from collections.abc import Sequence

from app.core.config import Settings, get_settings
from app.core.database import DatabaseState


def init_database(settings: Settings | None = None) -> None:
    resolved_settings = settings or get_settings()
    if resolved_settings.sqlite_file_path is not None:
        resolved_settings.sqlite_file_path.parent.mkdir(parents=True, exist_ok=True)

    database = DatabaseState.from_settings(resolved_settings)
    try:
        database.initialize_schema()
    finally:
        database.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Manage the open_deepagents backend database.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create or migrate the configured database schema.")

    args = parser.parse_args(argv)
    if args.command == "init":
        init_database()
        print("Database schema is ready.")


if __name__ == "__main__":
    main()
