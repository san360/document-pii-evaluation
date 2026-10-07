"""In-memory tracking for in-flight runs.

A redaction run can take minutes — OCR, several API calls per chunk, polling —
so the HTTP request that starts one returns immediately and the browser follows
along by polling. Each job keeps an ordered event log rather than only a
percentage, because *which* stage is slow is the thing an operator actually
wants to see.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MAX_JOBS = 50


@dataclass
class JobEvent:
    stage: str
    message: str
    at: str
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "message": self.message,
            "at": self.at,
            "detail": self.detail or {},
        }


@dataclass
class Job:
    run_id: str
    filename: str
    status: str = "running"
    error: str | None = None
    started_at: float = field(default_factory=time.perf_counter)
    events: list[JobEvent] = field(default_factory=list)
    result: dict | None = None
    task: asyncio.Task | None = None

    def record(self, stage: str, message: str, detail: dict | None = None) -> None:
        self.events.append(
            JobEvent(
                stage=stage,
                message=message,
                at=datetime.now(timezone.utc).isoformat(),
                detail=detail,
            )
        )
        logger.info("[%s] %s: %s", self.run_id, stage, message)

    def as_dict(self, since: int = 0) -> dict:
        return {
            "run_id": self.run_id,
            "filename": self.filename,
            "status": self.status,
            "error": self.error,
            "elapsed_ms": int((time.perf_counter() - self.started_at) * 1000),
            "event_count": len(self.events),
            "events": [e.as_dict() for e in self.events[since:]],
            "result": self.result if self.status in {"succeeded", "partial", "failed"} else None,
        }


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, run_id: str, filename: str) -> Job:
        self._prune()
        job = Job(run_id=run_id, filename=filename)
        self._jobs[run_id] = job
        return job

    def get(self, run_id: str) -> Job | None:
        return self._jobs.get(run_id)

    def active(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.status == "running"]

    def _prune(self) -> None:
        if len(self._jobs) <= MAX_JOBS:
            return
        finished = [
            (run_id, job)
            for run_id, job in self._jobs.items()
            if job.status != "running"
        ]
        finished.sort(key=lambda item: item[1].started_at)
        for run_id, _ in finished[: len(self._jobs) - MAX_JOBS]:
            self._jobs.pop(run_id, None)


registry = JobRegistry()
