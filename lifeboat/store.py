"""Where backups live.

GitHubStore keeps them as assets of one GitHub Release ("backups"): free, durable, off the
machine that made them, and versioned by name. LocalStore is a folder, for tests and local use.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess


class LocalStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(path, exist_ok=True)

    def list(self) -> list[str]:
        return sorted(os.listdir(self.path))

    def put(self, *files: str) -> None:
        for f in files:
            shutil.copy2(f, os.path.join(self.path, os.path.basename(f)))

    def get(self, name: str, dest_dir: str) -> str:
        dst = os.path.join(dest_dir, name)
        shutil.copy2(os.path.join(self.path, name), dst)
        return dst

    def delete(self, name: str) -> None:
        os.remove(os.path.join(self.path, name))


class GitHubStore:
    def __init__(self, repo: str, tag: str = "backups"):
        self.repo = repo
        self.tag = tag

    def _gh(self, *args: str, capture: bool = True) -> str:
        return subprocess.run(["gh", *args, "-R", self.repo], check=True, capture_output=capture, text=True).stdout

    def ensure(self) -> None:
        try:
            self._gh("release", "view", self.tag, "--json", "tagName")
        except subprocess.CalledProcessError:
            self._gh("release", "create", self.tag, "--title", "Backups (managed by LIFEBOAT)", "--notes",
                     "Encrypted nightly backups and their manifests. Written and pruned by the nightly workflow. "
                     "The .enc files are AES-256-GCM encrypted; the manifests are public on purpose.")

    def list(self) -> list[str]:
        data = json.loads(self._gh("release", "view", self.tag, "--json", "assets"))
        return sorted(a["name"] for a in data["assets"])

    def put(self, *files: str) -> None:
        self._gh("release", "upload", self.tag, *files, "--clobber")

    def get(self, name: str, dest_dir: str) -> str:
        self._gh("release", "download", self.tag, "-p", name, "-D", dest_dir, "--clobber")
        return os.path.join(dest_dir, name)

    def delete(self, name: str) -> None:
        self._gh("release", "delete-asset", self.tag, name, "-y")


def from_env() -> LocalStore | GitHubStore:
    target = os.environ.get("LIFEBOAT_STORE", "local:./backups")
    kind, _, where = target.partition(":")
    if kind == "github":
        store = GitHubStore(where)
        store.ensure()
        return store
    return LocalStore(where)
