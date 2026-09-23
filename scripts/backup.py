"""Create a consistent SQLite backup, including committed WAL data."""

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    if not source.is_file():
        parser.error("Source database does not exist")
    if destination.exists():
        parser.error("Destination already exists; choose a new backup filename")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive file creation prevents accidental replacement of a prior backup.
    destination.touch(exist_ok=False)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    print(f"Verified backup: {destination}")


if __name__ == "__main__":
    main()
