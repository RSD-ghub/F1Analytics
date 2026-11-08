"""The live race feed: FastF1's recorder writing a file, and this service reading it.

``start`` runs ``python -m fastf1.livetiming save FILE`` as a child process.
It writes every feed message to ``FILE`` the moment it arrives, and a task
here reads new lines every half second into ``live_timing.LiveTiming``. The
prediction service polls ``/live/status`` and, when the leader's lap moves,
``/live/snapshot``.

A recording is kept, so a race can be **replayed** through the same path
later (``start(..., recording="2026-21-....txt", speed=20)``): lines are fed
at ``speed`` times the pace they were recorded at (0 = all at once). That is
how the feed is tested between race weekends.

**Signing in.** The recorder needs the F1TV token from the Data health
page. The timing topics themselves would arrive without one, but FastF1
3.9.2's ``--no-auth`` hands signalrcore 1.0.2 a ``None`` token factory, which
raises a TypeError before it connects (found recording São Paulo 2026), and
FastF1 does the same when its sign-in fails. So ``start`` refuses, with a
reason, when F1TV is not connected. The child reads the stored token; its
stdin is closed, so FastF1 can never stop to ask for a password.

**Redis Streams.** When ``REDIS_URL`` is set, each lap the leader completes
(and each change of session status) is also added to ``f1:live:laps``, so the
prediction service forecasts it at once instead of on its next poll. Without
Redis nothing changes: the prediction service polls ``/live/status``.

One race at a time: starting another stops the first.
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from app.services import f1tv_auth
from f1_common import streams
from app.services.live_timing import LiveTiming, parse_line

logger = logging.getLogger(__name__)

#: How often new lines are read from the recording, seconds.
TAIL_INTERVAL = 0.5
#: After the chequered flag the recorder keeps going this long (late race
#: control messages, final statuses), then stops itself. Seconds.
AFTER_FINISH = 15 * 60


class LiveFeedError(RuntimeError):
    pass


def f1tv_connected() -> bool:
    try:
        return bool(f1tv_auth.status().get("connected"))
    except Exception:
        return False


def safe_recording(directory: str, name: str) -> str:
    """A recording's path, only ever inside ``directory``."""
    base = os.path.basename(name or "")
    if not base or base != name or base.startswith("."):
        raise LiveFeedError("recording must be a file name in the recordings folder")
    path = os.path.join(directory, base)
    if not os.path.isfile(path):
        raise LiveFeedError("no recording named {}".format(base))
    return path


class LiveFeed:
    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.timing: Optional[LiveTiming] = None
        self.mode = ""
        self.path = ""
        self.started_at = ""
        self.error = ""
        self._process: Optional[subprocess.Popen] = None
        self._task: Optional[asyncio.Task] = None
        self._finished_at: Optional[float] = None
        self._announced: Optional[tuple] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, season: int, round_number: int, names: Mapping[int, str],
                    recording: str = "", speed: float = 0.0) -> Dict[str, Any]:
        await self.stop()
        os.makedirs(self.directory, exist_ok=True)
        path = safe_recording(self.directory, recording) if recording else ""
        if not path and not f1tv_connected():
            raise LiveFeedError(
                "connect F1TV on the Data health page first: the live recorder "
                "cannot connect without its token")
        self.timing = LiveTiming(season, round_number, names)
        self.error = ""
        self._finished_at = None
        self._announced = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        if path:
            self.mode = "replay"
            self.path = path
            self._task = asyncio.create_task(self._replay(path, speed))
        else:
            self.mode = "live"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.path = os.path.join(
                self.directory, "{}-{:02d}-{}.txt".format(season, round_number, stamp))
            self._process = self._spawn(self.path)
            self._task = asyncio.create_task(self._tail(self.path))
        logger.info("live feed started: %s %s-%s from %s", self.mode, season,
                    round_number, self.path)
        return self.status()

    def _spawn(self, path: str) -> subprocess.Popen:
        command = [sys.executable, "-m", "fastf1.livetiming", "save", path,
                   "--timeout", "0"]
        log = open(path + ".log", "a")  # noqa: SIM115 - the child keeps its own copy
        try:
            return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            log.close()

    async def _tail(self, path: str) -> None:
        """Read the recording as it grows, until stopped or well past the flag."""
        position = 0
        partial = ""
        try:
            while True:
                if os.path.exists(path):
                    with open(path) as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position = handle.tell()
                    if chunk:
                        lines = (partial + chunk).split("\n")
                        partial = lines.pop()  # a line still being written
                        self.timing.feed_lines(lines)
                        await self._announce()
                if self._process is not None and self._process.poll() is not None:
                    self.error = "the recorder exited (code {}); see {}.log".format(
                        self._process.returncode, os.path.basename(path))
                    logger.warning("live feed: %s", self.error)
                    return
                if self._past_finish():
                    logger.info("live feed: race finished, stopping the recorder")
                    self._terminate()
                    return
                await asyncio.sleep(TAIL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep the service up whatever the feed sends
            logger.exception("live feed tail failed")
            self.error = str(exc)
            self._terminate()

    async def _replay(self, path: str, speed: float) -> None:
        """Feed a recording through, at ``speed`` times its own pace."""
        previous = None
        try:
            with open(path) as handle:
                for line in handle:
                    parsed = parse_line(line)
                    if parsed is None:
                        continue
                    when = _when(parsed[2])
                    if speed > 0 and when is not None:
                        if previous is not None and when > previous:
                            await asyncio.sleep(min((when - previous) / speed, 30.0))
                        previous = when
                    self.timing.apply(*parsed)
                    if parsed[0] in ("TimingData", "SessionStatus"):
                        await self._announce()
                    if speed <= 0 and self.timing.messages % 2000 == 0:
                        await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("live replay failed")
            self.error = str(exc)

    async def _announce(self) -> None:
        """Add the lap to ``f1:live:laps`` when it or the session status moved."""
        if self.timing is None or not streams.enabled():
            return
        now = (self.timing.season, self.timing.round, self.timing.lap(), self.timing.status())
        if now == self._announced:
            return
        self._announced = now
        await streams.publish(streams.LAPS, {
            "season": now[0], "round": now[1], "lap": now[2], "status": now[3],
            "started_at": self.started_at, "mode": self.mode})

    def _past_finish(self) -> bool:
        if self.timing is None or not self.timing.finished():
            return False
        now = asyncio.get_running_loop().time()
        if self._finished_at is None:
            self._finished_at = now
        return now - self._finished_at > AFTER_FINISH

    def _terminate(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, AttributeError):
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    async def stop(self) -> Dict[str, Any]:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._terminate()
        return self.status()

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "running": self.running,
            "mode": self.mode,
            "recording": os.path.basename(self.path) if self.path else "",
            "started_at": self.started_at,
            "error": self.error,
        }
        if self.timing is not None:
            out.update(self.timing.summary())
        return out

    def snapshot(self) -> Dict[str, Any]:
        if self.timing is None:
            raise LiveFeedError("no live feed has been started")
        return dict(self.timing.snapshot(), running=self.running, mode=self.mode)

    def recordings(self):
        if not os.path.isdir(self.directory):
            return []
        return sorted((n for n in os.listdir(self.directory) if n.endswith(".txt")),
                      reverse=True)


def _when(stamp: str) -> Optional[float]:
    if not stamp:
        return None
    try:
        text = stamp.replace("Z", "+00:00")
        # F1 writes up to seven fractional digits; Python reads six.
        if "." in text:
            head, tail = text.split(".", 1)
            digits = "".join(c for c in tail if c.isdigit())
            text = "{}.{}{}".format(head, digits[:6].ljust(6, "0"), tail[len(digits):])
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


_feed: Optional[LiveFeed] = None


def get_feed(directory: str) -> LiveFeed:
    global _feed
    if _feed is None:
        _feed = LiveFeed(directory)
    return _feed
