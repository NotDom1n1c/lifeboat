"""python -m lifeboat <command>

  drill               the nightly run: restore + verify the last backup, then take the next one
  list                backups in the store, with what the retention policy keeps them as
  restore [ID]        restore a backup (default: newest) into the database and verify it
  check FILE          decrypt FILE to /dev/null: is it intact and is the key right?
  keygen              print a new random LIFEBOAT_KEY

Environment: LIFEBOAT_KEY, LIFEBOAT_STORE (local:DIR or github:OWNER/REPO), LIFEBOAT_DB_EXEC, LIFEBOAT_DB
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

from . import crypto, drill as drill_mod, store as store_mod
from .db import Database


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lifeboat", description="Backups that are restored and verified every night.")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("drill")
    d.add_argument("--out", help="also write history.json and backups.json here (for the status page)")
    sub.add_parser("list")
    r = sub.add_parser("restore")
    r.add_argument("id", nargs="?")
    c = sub.add_parser("check")
    c.add_argument("file")
    sub.add_parser("keygen")
    args = p.parse_args(argv)

    if args.cmd == "keygen":
        print(crypto.new_key())
        return 0
    if args.cmd == "check":
        with tempfile.TemporaryDirectory() as tmp:
            crypto.decrypt_file(args.file, os.path.join(tmp, "out"), crypto.load_key())
        print("intact: every chunk authenticated")
        return 0

    store = store_mod.from_env()
    db = Database(name=os.environ.get("LIFEBOAT_DB", "shop"))

    if args.cmd == "drill":
        return 0 if drill_mod.drill(db, store, crypto.load_key(), args.out) else 1

    if args.cmd == "list":
        with tempfile.TemporaryDirectory() as tmp:
            for b in drill_mod.build_catalog(store, tmp):
                print(f"{b['id']}  {b['bytes']['encrypted'] / 1e6:6.2f} MB  {b['total_rows']:>7} rows  {','.join(b['kept_as']) or 'expires'}")
        return 0

    if args.cmd == "restore":
        names = drill_mod.manifests(store)
        target = f"{args.id}.manifest.json" if args.id else names[-1]
        with tempfile.TemporaryDirectory() as tmp:
            result = drill_mod.restore_and_verify(db, store, crypto.load_key(), tmp, drill_mod.Run(), target)
        print(f"restored {result['source']}: {result['rows']} rows in {result['restore_seconds']}s, verified")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
