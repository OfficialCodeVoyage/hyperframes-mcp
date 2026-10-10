"""ClipBudget: the enforced per-video cap on image-to-video clips.

A video (a ``topic_id``) may start at most ``max_clips`` distinct clips, a clip may be tried
``max_attempts_per_clip`` times, the clips may add up to ``max_total_seconds``, and every
attempt together may not pass ``max_spend_usd``. The adapter calls ``begin_attempt`` before each
provider call; a refusal raises a ``ClipCapError`` and writes nothing.

Every attempt is logged in SQLite so the cap survives a restart of the runner. An attempt is
counted the moment it begins, at its estimated cost, and keeps that cost unless it ends
``rejected`` (the provider never accepted the job). So a failed attempt still counts toward
spend, and so does one whose process died before it could finish: the cap errs on the side of
refusing. Seconds count once per clip (the longest length tried), not once per attempt.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite

from hyperframes_mcp.providers.video.errors import (
    ClipAttemptsExceeded,
    ClipCountExceeded,
    ClipSecondsExceeded,
    ClipSpendExceeded,
)
from hyperframes_mcp.providers.video.settings import ClipLimits

AttemptOutcome = Literal["ok", "failed", "rejected"]
_OUTCOMES = ("ok", "failed", "rejected")
_EPSILON = 1e-9


@dataclass(frozen=True)
class ClipAttempt:
    """A begun attempt: its row id and its 1-based number within the clip."""

    id: int
    number: int


@dataclass(frozen=True)
class ClipUsage:
    """What one video has used so far."""

    clips: int
    attempts: int
    seconds: int
    spend_usd: float


class ClipBudget:
    """The per-video clip cap, persisted in SQLite at ``db_path``."""

    def __init__(self, limits: ClipLimits, db_path: str | Path) -> None:
        self.limits = limits
        self._db_path = Path(db_path)
        self._lock = asyncio.Lock()
        self._ready = False

    async def _connect(self) -> aiosqlite.Connection:
        # isolation_level=None: transactions are explicit, so BEGIN IMMEDIATE takes the write
        # lock before the checks and two processes cannot both pass the same count.
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._db_path, isolation_level=None)
        if not self._ready:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS i2v_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER NOT NULL,
                    clip_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    seconds INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ready = True
        return db

    async def begin_attempt(
        self,
        topic_id: int,
        clip_id: str,
        model: str,
        seconds: int,
        estimated_cost: float,
    ) -> ClipAttempt:
        """Count one attempt at ``estimated_cost`` or refuse it.

        Raises:
            ClipCountExceeded, ClipAttemptsExceeded, ClipSecondsExceeded, ClipSpendExceeded
        """
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    attempt = await self._insert_if_allowed(db, topic_id, clip_id, model, seconds, estimated_cost)
                except BaseException:
                    await db.execute("ROLLBACK")
                    raise
                await db.execute("COMMIT")
                return attempt
            finally:
                await db.close()

    async def _insert_if_allowed(
        self,
        db: aiosqlite.Connection,
        topic_id: int,
        clip_id: str,
        model: str,
        seconds: int,
        estimated_cost: float,
    ) -> ClipAttempt:
        limits = self.limits
        async with db.execute(
            "SELECT clip_id, COUNT(*), MAX(seconds), SUM(cost_usd) "
            "FROM i2v_attempts WHERE topic_id = ? GROUP BY clip_id",
            (topic_id,),
        ) as cursor:
            clips = {row[0]: row[1:] for row in await cursor.fetchall()}

        tries_so_far = clips.get(clip_id, (0, 0, 0.0))[0]
        if clip_id not in clips and len(clips) >= limits.max_clips:
            raise ClipCountExceeded(
                f"Video {topic_id} already has {len(clips)} clips; the cap is "
                f"{limits.max_clips}. Refusing clip {clip_id!r}."
            )
        if tries_so_far >= limits.max_attempts_per_clip:
            raise ClipAttemptsExceeded(
                f"Clip {clip_id!r} of video {topic_id} has used its {limits.max_attempts_per_clip} attempts."
            )
        total_seconds = sum(
            max(seconds, longest) if known == clip_id else longest for known, (_, longest, _) in clips.items()
        ) + (seconds if clip_id not in clips else 0)
        if total_seconds > limits.max_total_seconds:
            raise ClipSecondsExceeded(
                f"Clip {clip_id!r} would bring video {topic_id} to {total_seconds} s of "
                f"clips; the cap is {limits.max_total_seconds:g} s."
            )
        spent = sum(row[2] for row in clips.values())
        if spent + estimated_cost > limits.max_spend_usd + _EPSILON:
            raise ClipSpendExceeded(
                f"Video {topic_id} has ${spent:.2f} of clip attempts; ${estimated_cost:.2f} "
                f"more would pass the ${limits.max_spend_usd:.2f} ceiling."
            )

        number = tries_so_far + 1
        cursor = await db.execute(
            "INSERT INTO i2v_attempts "
            "(topic_id, clip_id, attempt, model, seconds, cost_usd, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'started', ?)",
            (
                topic_id,
                clip_id,
                number,
                model,
                seconds,
                estimated_cost,
                datetime.now(UTC).isoformat(),
            ),
        )
        return ClipAttempt(id=cursor.lastrowid, number=number)

    async def finish_attempt(self, attempt_id: int, outcome: AttemptOutcome) -> None:
        """Close an attempt. ``rejected`` drops its cost; ``ok`` and ``failed`` keep it."""
        if outcome not in _OUTCOMES:
            raise ValueError(f"outcome must be one of {_OUTCOMES}, got {outcome!r}")
        async with self._lock:
            db = await self._connect()
            try:
                await db.execute(
                    "UPDATE i2v_attempts SET status = ?, "
                    "cost_usd = CASE WHEN ? = 'rejected' THEN 0 ELSE cost_usd END "
                    "WHERE id = ?",
                    (outcome, outcome, attempt_id),
                )
            finally:
                await db.close()

    async def clip_spend(self, topic_id: int, clip_id: str) -> float:
        """What the attempts of one clip have cost so far (rejected attempts count as zero)."""
        async with self._lock:
            db = await self._connect()
            try:
                async with db.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0.0) FROM i2v_attempts WHERE topic_id = ? AND clip_id = ?",
                    (topic_id, clip_id),
                ) as cursor:
                    (spend,) = await cursor.fetchone()
            finally:
                await db.close()
        return spend

    async def usage(self, topic_id: int) -> ClipUsage:
        """Clips, attempts, seconds and spend of one video so far."""
        async with self._lock:
            db = await self._connect()
            try:
                async with db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(n), 0), COALESCE(SUM(s), 0), "
                    "COALESCE(SUM(c), 0.0) FROM (SELECT COUNT(*) AS n, MAX(seconds) AS s, "
                    "SUM(cost_usd) AS c FROM i2v_attempts WHERE topic_id = ? "
                    "GROUP BY clip_id)",
                    (topic_id,),
                ) as cursor:
                    clips, attempts, seconds, spend = await cursor.fetchone()
            finally:
                await db.close()
        return ClipUsage(clips, attempts, seconds, spend)
