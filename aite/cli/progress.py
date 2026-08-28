"""ProgressReporter implementations (Rich console / Noop / callback for SSE)."""

from __future__ import annotations

from collections.abc import Callable

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from aite.core.interfaces import NoopProgress, ProgressReporter


class RichProgressReporter(ProgressReporter):
    def __init__(self, console=None) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.fields[note]}", justify="right"),
            console=console,
        )
        self._progress.start()
        self._task = None

    def start(self, total: int, description: str = "") -> None:
        if self._task is not None:
            self._progress.stop_task(self._task)
        self._task = self._progress.add_task(description or "working",
                                             total=total or 1, note="")

    def update(self, n: int = 1, *, note: str | None = None) -> None:
        if self._task is None:
            return
        if note is not None:
            self._progress.update(self._task, advance=n, note=note[:60])
        else:
            self._progress.update(self._task, advance=n)

    def finish(self, message: str | None = None) -> None:
        if message:
            self._progress.console.print(message)
        self._progress.stop()


class CallbackProgress(NoopProgress):
    """Bridges engine progress into an event callback (web SSE)."""

    def __init__(self, cb: Callable[[dict], None], *, every: int = 1) -> None:
        self.cb = cb
        self.every = max(every, 1)
        self._done = 0
        self._total = 0
        self._phase = ""

    def start(self, total: int, description: str = "") -> None:
        self._done = 0
        self._total = total
        self._phase = description
        self._push()

    def update(self, n: int = 1, *, note: str | None = None) -> None:
        self._done += n
        if self._done % self.every == 0:
            self._push(note=note)

    def finish(self, message: str | None = None) -> None:
        self.cb({"type": "progress", "phase": self._phase, "done": self._total,
                 "total": self._total, "message": message or ""})

    def _push(self, note: str | None = None) -> None:
        self.cb({"type": "progress", "phase": self._phase, "done": self._done,
                 "total": self._total, "note": note or ""})
