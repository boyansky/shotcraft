"""Read history entries from a directory of {shot_id}.json blobs.

An ingest path for a directory of history blobs, for when the host running
the CLI cannot reach the machine directly. A drop-in for MeticulousAPI's
`history()` so sync's diffing, flagging and idempotency are unchanged.
"""
import json
import pathlib


class SourceUnreadable(RuntimeError):
    """The blob directory could not be read. Changes nothing; safe to retry."""


class BlobSource:
    def __init__(self, directory):
        self.directory = pathlib.Path(directory)
        # named rather than counted: a file that failed to parse is a fact the
        # operator has to be able to act on, and a bare count is not actionable
        self.skipped = []

    def history(self):
        if not self.directory.is_dir():
            raise SourceUnreadable(f"{self.directory}: not a directory")
        entries = []
        self.skipped = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                parsed = json.loads(path.read_text())
            except (OSError, ValueError):
                # one bad blob must not block the whole ingest, and must not
                # pass unmentioned either
                self.skipped.append(path.name)
                continue
            if not isinstance(parsed, dict):
                # valid JSON that is not an object (a bare list, number,
                # string...) has no `id`, `time` or `profile` to speak of.
                # `_sort_time`'s `.get()` would raise AttributeError on it
                # further down, escaping the except above entirely and
                # aborting the whole ingest -- the exact "one bad blob costs
                # the batch" failure this module exists to prevent.
                self.skipped.append(path.name)
                continue
            entries.append(parsed)
        # newest first, mirroring the machine. An entry with no usable time
        # sorts oldest rather than raising mid-ingest.
        entries.sort(key=lambda e: _sort_time(e), reverse=True)
        return entries


def _sort_time(entry):
    value = entry.get("time")
    return value if isinstance(value, (int, float)) else float("-inf")
