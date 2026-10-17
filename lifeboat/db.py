"""MariaDB access through the official client tools.

The commands run through a prefix (LIFEBOAT_DB_EXEC), e.g. `docker exec -i -e MYSQL_PWD=... <container>`,
so the tool works the same against a CI service container, a local container or a real server.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import shlex
import shutil
import subprocess
import time


class Database:
    def __init__(self, name: str = "shop", user: str = "root", exec_prefix: str | None = None):
        self.name = name
        self.user = user
        self.prefix = shlex.split(exec_prefix if exec_prefix is not None else os.environ.get("LIFEBOAT_DB_EXEC", ""))

    def _cmd(self, tool: str, *args: str) -> list[str]:
        return [*self.prefix, tool, "-u", self.user, *args]

    def sql(self, statement: str, database: str | None = "") -> str:
        """Run SQL, return tab-separated rows without headers."""
        db = self.name if database == "" else database
        args = ["-B", "-N", "--raw", "-e", statement] + ([db] if db else [])
        return subprocess.run(self._cmd("mariadb", *args), check=True, capture_output=True, text=True).stdout

    def script(self, sql_text: str, database: str | None = "") -> None:
        db = self.name if database == "" else database
        subprocess.run(self._cmd("mariadb", *([db] if db else [])), input=sql_text, check=True, capture_output=True, text=True)

    def server_version(self) -> str:
        return self.sql("SELECT VERSION()", database=None).strip()

    def wait_ready(self, seconds: int = 60) -> None:
        end = time.time() + seconds
        while True:
            try:
                self.sql("SELECT 1", database=None)
                return
            except subprocess.CalledProcessError:
                if time.time() > end:
                    raise
                time.sleep(2)

    def recreate(self) -> None:
        self.sql(f"DROP DATABASE IF EXISTS `{self.name}`; CREATE DATABASE `{self.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci", database=None)

    # ------------------------------------------------------------ dump / restore
    def dump(self, out_gz: str) -> int:
        """Consistent logical dump (one transaction), gzip-compressed. Returns raw size in bytes."""
        cmd = self._cmd("mariadb-dump", "--single-transaction", "--routines", "--triggers", "--events",
                        "--hex-blob", "--skip-dump-date", "--default-character-set=utf8mb4", self.name)
        raw = 0
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc, gzip.open(out_gz, "wb", compresslevel=9) as gz:
            for chunk in iter(lambda: proc.stdout.read(1 << 16), b""):
                raw += len(chunk)
                gz.write(chunk)
        if proc.returncode != 0:
            raise RuntimeError(f"mariadb-dump failed with exit code {proc.returncode}")
        return raw

    def restore(self, dump_gz: str) -> None:
        self.recreate()
        with gzip.open(dump_gz, "rb") as gz, subprocess.Popen(self._cmd("mariadb", self.name), stdin=subprocess.PIPE) as proc:
            shutil.copyfileobj(gz, proc.stdin)
            proc.stdin.close()
        if proc.returncode != 0:
            raise RuntimeError(f"restore failed with exit code {proc.returncode}")

    # ------------------------------------------------------------ verification
    def tables(self) -> list[str]:
        return sorted(t for t in self.sql("SHOW TABLES").split("\n") if t)

    def fingerprint(self) -> dict[str, dict]:
        """Row count and SHA-256 of every table's content, in primary-key order.
        Same data => same fingerprint, independent of how the server stores it."""
        result = {}
        for table in self.tables():
            out = self.sql(f"SELECT * FROM `{table}` ORDER BY 1")
            rows = out.count("\n")
            result[table] = {"rows": rows, "sha256": hashlib.sha256(out.encode()).hexdigest()}
        return result
