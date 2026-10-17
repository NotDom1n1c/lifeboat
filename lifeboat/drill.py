"""The nightly run. A backup only counts once it has been restored, so every night:

  1. download last night's backup
  2. decrypt it (GCM authentication proves it is intact)
  3. tamper test: a copy with one flipped byte MUST be rejected
  4. restore it into an empty database and time it (that is the real recovery time)
  5. verify every table against the fingerprint recorded at backup time
  6. simulate a business day on the restored data
  7. take, encrypt and upload the next backup
  8. apply the retention policy

If anything in 1-5 fails, the chain stops: no new backup is taken from a database that
did not restore correctly, and the run fails loudly.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone

from . import __version__, crypto, retention, shop
from .crypto import IntegrityError
from .db import Database

PREFIX = "shop-"
HISTORY = "history.json"
CATALOG = "backups.json"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def parse_stamp(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Run:
    """Collects the steps of one run, prints them for the CI log and keeps them for the status page."""

    def __init__(self):
        self.steps: list[dict] = []

    def step(self, name: str):
        run = self

        class _Step:
            def __enter__(self):
                self.t0 = time.perf_counter()
                self.rec = {"name": name, "status": "ok", "detail": ""}
                print(f"==> {name}", flush=True)
                return self.rec

            def __exit__(self, exc_type, exc, tb):
                self.rec["seconds"] = round(time.perf_counter() - self.t0, 2)
                if exc:
                    self.rec["status"] = "failed"
                    self.rec["detail"] = f"{exc_type.__name__}: {exc}"
                print(f"    {self.rec['status']} in {self.rec['seconds']}s {self.rec['detail']}", flush=True)
                run.steps.append(self.rec)
                return False

        return _Step()


def load_json(store, name: str, workdir: str, default):
    if name not in store.list():
        return default
    with open(store.get(name, workdir), encoding="utf-8") as f:
        return json.load(f)


def manifests(store) -> list[str]:
    return sorted(n for n in store.list() if n.startswith(PREFIX) and n.endswith(".manifest.json"))


def take_backup(db: Database, store, key: bytes, workdir: str, run: Run) -> dict:
    created = utcnow()
    bid = PREFIX + stamp(created)
    gz, enc, man = (os.path.join(workdir, bid + ext) for ext in (".sql.gz", ".sql.gz.enc", ".manifest.json"))
    with run.step("backup: mariadb-dump --single-transaction") as s:
        t0 = time.perf_counter()
        raw = db.dump(gz)
        dump_seconds = round(time.perf_counter() - t0, 2)
        fingerprint = db.fingerprint()
        s["detail"] = f"{raw / 1e6:.2f} MB SQL, {sum(t['rows'] for t in fingerprint.values())} rows"
    with run.step("backup: encrypt (AES-256-GCM)") as s:
        crypto.encrypt_file(gz, enc, key)
        s["detail"] = f"{os.path.getsize(enc) / 1e6:.2f} MB encrypted"
    manifest = {
        "id": bid,
        "created_at": created.isoformat(timespec="seconds"),
        "tool": f"lifeboat {__version__}",
        "database": db.name,
        "server_version": db.server_version(),
        "file": os.path.basename(enc),
        "bytes": {"sql": raw, "gzip": os.path.getsize(gz), "encrypted": os.path.getsize(enc)},
        "sha256": sha256_file(enc),
        "dump_seconds": dump_seconds,
        "tables": fingerprint,
        "total_rows": sum(t["rows"] for t in fingerprint.values()),
    }
    with open(man, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with run.step("upload to the backup store") as s:
        store.put(enc, man)
        s["detail"] = os.path.basename(enc)
    return manifest


def restore_and_verify(db: Database, store, key: bytes, workdir: str, run: Run, manifest_name: str) -> dict:
    """Steps 1-5. Raises on the first failure."""
    with run.step("download last backup") as s:
        man_path = store.get(manifest_name, workdir)
        with open(man_path, encoding="utf-8") as f:
            manifest = json.load(f)
        enc = store.get(manifest["file"], workdir)
        actual = sha256_file(enc)
        if actual != manifest["sha256"]:
            raise IntegrityError(f"sha256 mismatch: {actual[:12]} != {manifest['sha256'][:12]}")
        s["detail"] = f"{manifest['id']} ({manifest['bytes']['encrypted'] / 1e6:.2f} MB, sha256 ok)"
    gz = os.path.join(workdir, "restore.sql.gz")
    with run.step("decrypt and authenticate") as s:
        crypto.decrypt_file(enc, gz, key)
        s["detail"] = "every chunk passed GCM authentication"
    with run.step("tamper test: a modified copy must be rejected") as s:
        bad = os.path.join(workdir, "tampered.enc")
        shutil.copyfile(enc, bad)
        with open(bad, "r+b") as f:
            f.seek(os.path.getsize(bad) // 2)
            byte = f.read(1)
            f.seek(-1, os.SEEK_CUR)
            f.write(bytes([byte[0] ^ 0x01]))
        try:
            crypto.decrypt_file(bad, os.path.join(workdir, "tampered.out"), key)
        except IntegrityError as err:
            s["detail"] = f"rejected as expected ({err})"
        else:
            raise AssertionError("a tampered backup was accepted")
    with run.step("restore into an empty database") as s:
        t0 = time.perf_counter()
        db.restore(gz)
        restore_seconds = round(time.perf_counter() - t0, 2)
        s["detail"] = f"{restore_seconds}s"
    with run.step("verify every table against the manifest") as s:
        live = db.fingerprint()
        expected = manifest["tables"]
        problems = []
        for table, exp in expected.items():
            got = live.get(table)
            if got is None:
                problems.append(f"{table}: missing")
            elif got != exp:
                problems.append(f"{table}: rows {got['rows']} vs {exp['rows']}, checksum {'ok' if got['sha256'] == exp['sha256'] else 'differs'}")
        problems += [f"{t}: unexpected table" for t in live if t not in expected]
        if problems:
            raise AssertionError("; ".join(problems))
        s["detail"] = f"{len(expected)} tables, {manifest['total_rows']} rows identical"
    return {"source": manifest["id"], "restore_seconds": restore_seconds, "rows": manifest["total_rows"], "tables": len(manifest["tables"])}


def drill(db: Database, store, key: bytes, out_dir: str | None = None) -> bool:
    run = Run()
    started = utcnow()
    workdir = tempfile.mkdtemp(prefix="lifeboat-")
    history = load_json(store, HISTORY, workdir, [])
    entry = {"run": len(history) + 1, "started_at": started.isoformat(timespec="seconds"), "status": "passed",
             "ci_run": os.environ.get("GITHUB_RUN_ID"), "restore": None, "backup": None, "activity": None, "retention": None}
    try:
        db.wait_ready()
        names = manifests(store)
        if names:
            entry["restore"] = restore_and_verify(db, store, key, workdir, run, names[-1])
        else:
            with run.step("genesis: no backup yet, seeding the shop database") as s:
                entry["activity"] = shop.seed(db)
                s["detail"] = json.dumps(entry["activity"])
        if names:
            with run.step("business day: new customers and orders") as s:
                entry["activity"] = shop.business_day(db, day=started.date().isoformat(), seed_value=entry["run"])
                s["detail"] = json.dumps(entry["activity"])
        m = take_backup(db, store, key, workdir, run)
        entry["backup"] = {k: m[k] for k in ("id", "created_at", "bytes", "sha256", "dump_seconds", "total_rows")}
        with run.step("retention (7 daily, 4 weekly, 6 monthly)") as s:
            all_ids = [n[: -len(".manifest.json")] for n in manifests(store)]
            keep, delete = retention.plan([(i, parse_stamp(i[len(PREFIX):])) for i in all_ids])
            for bid in delete:
                store.delete(bid + ".sql.gz.enc")
                store.delete(bid + ".manifest.json")
            entry["retention"] = {"kept": len(keep), "deleted": delete}
            s["detail"] = f"kept {len(keep)}, deleted {len(delete)}"
    except Exception as err:  # the step already recorded what failed
        entry["status"] = "failed"
        entry["error"] = f"{type(err).__name__}: {err}"
    entry["steps"] = run.steps
    entry["finished_at"] = utcnow().isoformat(timespec="seconds")

    # publish history + catalog for the status page, even when the run failed
    history = (history + [entry])[-400:]
    catalog = build_catalog(store, workdir)
    for name, data in ((HISTORY, history), (CATALOG, catalog)):
        path = os.path.join(workdir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        store.put(path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            shutil.copy(path, os.path.join(out_dir, name))
    shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nRun {entry['run']}: {entry['status'].upper()}" + (f" - {entry.get('error')}" if entry["status"] == "failed" else ""))
    return entry["status"] == "passed"


def build_catalog(store, workdir: str) -> list[dict]:
    names = manifests(store)
    ids = [n[: -len(".manifest.json")] for n in names]
    keep, _ = retention.plan([(i, parse_stamp(i[len(PREFIX):])) for i in ids])
    out = []
    for n in names:
        with open(store.get(n, workdir), encoding="utf-8") as f:
            m = json.load(f)
        out.append({"id": m["id"], "created_at": m["created_at"], "bytes": m["bytes"], "sha256": m["sha256"],
                    "total_rows": m["total_rows"], "tables": {t: v["rows"] for t, v in m["tables"].items()},
                    "server_version": m["server_version"], "kept_as": keep.get(m["id"], [])})
    return out
