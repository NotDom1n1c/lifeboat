"""Grandfather-father-son retention: keep the newest backup of each of the last
N days, N ISO weeks and N months. Everything else may be deleted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Policy:
    daily: int = 7
    weekly: int = 4
    monthly: int = 6


def plan(backups: list[tuple[str, datetime]], policy: Policy = Policy()) -> tuple[dict[str, list[str]], list[str]]:
    """backups: (id, created_at). Returns ({id: [reasons]}, [ids to delete])."""
    newest_first = sorted(backups, key=lambda b: b[1], reverse=True)
    keep: dict[str, list[str]] = {}

    def take(bucket_of, limit: int, label: str) -> None:
        seen: list = []
        for bid, at in newest_first:
            bucket = bucket_of(at)
            if bucket in seen:
                continue
            if len(seen) >= limit:
                break
            seen.append(bucket)
            keep.setdefault(bid, []).append(label)

    take(lambda d: d.date(), policy.daily, "daily")
    take(lambda d: d.isocalendar()[:2], policy.weekly, "weekly")
    take(lambda d: (d.year, d.month), policy.monthly, "monthly")

    # the newest backup is always kept, whatever the policy says
    if newest_first:
        keep.setdefault(newest_first[0][0], []).append("latest")

    delete = [bid for bid, _ in newest_first if bid not in keep]
    return keep, delete
