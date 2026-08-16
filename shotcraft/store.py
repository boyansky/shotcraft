"""JSONL persistence for shots and bags, plus per-shot telemetry blobs.

Telemetry blobs are committed to git on purpose: if the machine's history
endpoint turns out to be a rolling window, these files are the only copy.
"""
import datetime
import json
import os
import pathlib


class Store:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.shots_path = self.root / "shots.jsonl"
        self.bags_path = self.root / "bags.jsonl"
        self.grinders_path = self.root / "grinders.jsonl"
        self.dials_path = self.root / "dials.jsonl"
        self.telemetry_dir = self.root / "telemetry"

    @staticmethod
    def _atomic_write(path, text):
        """Write `text` to `path` via a temp file plus `os.replace`, never a
        truncating `open("w")`.

        A truncating write zeroes the file before the first byte of the new
        content lands, so an interrupt or a full disk mid-write leaves a
        zero-length (or partial) file where the old one used to be. For
        shots.jsonl that is total, unrecoverable loss for any shot that has
        since rolled off the machine's own history window -- the local
        archive is the only copy. `os.replace` is atomic on POSIX and
        Windows: the old file is either fully replaced or untouched, never
        caught in between.
        """
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)

    @staticmethod
    def _read_jsonl(path):
        if not path.exists():
            return []
        rows = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def load_shots(self):
        return self._read_jsonl(self.shots_path)

    def load_bags(self):
        return self._read_jsonl(self.bags_path)

    def bag_by_id(self, bag_id):
        for bag in self.load_bags():
            if bag.get("id") == bag_id:
                return bag
        return None

    def load_grinders(self):
        return self._read_jsonl(self.grinders_path)

    def grinder_by_id(self, grinder_id):
        for grinder in self.load_grinders():
            if grinder.get("id") == grinder_id:
                return grinder
        return None

    def shot_ids(self):
        return {row["id"] for row in self.load_shots()}

    def telemetry_ids(self):
        if not self.telemetry_dir.exists():
            return set()
        return {path.stem for path in self.telemetry_dir.glob("*.json")}

    def resolve_shot_id(self, prefix):
        """Full shot id from a full id or an unambiguous short prefix.

        Shot ids are uuids and nobody types 36 characters to rate a shot, so
        the CLI prints the first 8 and takes them back. Ambiguity raises
        instead of picking one: attaching a rating to the wrong shot would
        corrupt the only signal the machine cannot produce by itself.
        """
        ids = self.shot_ids() | self.telemetry_ids()
        if prefix in ids:
            return prefix
        matches = sorted(i for i in ids if i.startswith(prefix))
        if not matches:
            raise KeyError(f"no stored shot with id {prefix!r}")
        if len(matches) > 1:
            short = ", ".join(m[:8] for m in matches)
            raise ValueError(f"id {prefix!r} is ambiguous: matches {short}")
        return matches[0]

    def append_shots(self, rows):
        """Append rows whose id is not already stored. Returns count added."""
        known = self.shot_ids()
        new = []
        for r in rows:
            if r["id"] not in known:
                new.append(r)
                known.add(r["id"])
        if not new:
            return 0
        self.root.mkdir(parents=True, exist_ok=True)
        with self.shots_path.open("a") as handle:
            for row in new:
                handle.write(json.dumps(row) + "\n")
        return len(new)

    def append_bag(self, bag):
        """Append a bag unless its id already exists. Returns count added."""
        if self.bag_by_id(bag["id"]) is not None:
            return 0
        self.root.mkdir(parents=True, exist_ok=True)
        with self.bags_path.open("a") as handle:
            handle.write(json.dumps(bag) + "\n")
        return 1

    def append_grinder(self, grinder):
        """Append a grinder unless its id already exists. Returns count added."""
        if self.grinder_by_id(grinder["id"]) is not None:
            return 0
        self.root.mkdir(parents=True, exist_ok=True)
        with self.grinders_path.open("a") as handle:
            handle.write(json.dumps(grinder) + "\n")
        return 1

    def load_dials(self):
        return self._read_jsonl(self.dials_path)

    def append_dial(self, dial):
        """Append a dial-in event. Always appends: re-dialling to the same
        number on a different day is a real event, not a duplicate."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.dials_path.open("a") as handle:
            handle.write(json.dumps(dial) + "\n")
        return 1

    def update_shot(self, shot_id, fields):
        """Merge fields into one stored shot row, preserving file order."""
        rows = self.load_shots()
        for row in rows:
            if row["id"] == shot_id:
                row.update(fields)
                text = "".join(json.dumps(r) + "\n" for r in rows)
                self._atomic_write(self.shots_path, text)
                return row
        raise KeyError(f"no stored shot with id {shot_id!r}")

    def write_telemetry(self, shot_id, entry):
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)
        path = self.telemetry_dir / f"{shot_id}.json"
        self._atomic_write(path, json.dumps(entry))
        return path

    def has_telemetry(self, shot_id):
        return (self.telemetry_dir / f"{shot_id}.json").exists()

    @property
    def sync_stamp_path(self):
        return self.root / "last_sync.json"

    def write_sync_stamp(self, now=None):
        self.root.mkdir(parents=True, exist_ok=True)
        when = (now or datetime.datetime.now()).isoformat(timespec="seconds")
        self._atomic_write(self.sync_stamp_path, json.dumps({"ts": when}) + "\n")

    def sync_age_days(self, now=None):
        """Days since the last successful sync. Infinite when never synced,
        or when the stamp file is missing, unreadable, or malformed --
        callers must not feed this straight into a `.0f` format spec."""
        try:
            stamp = json.loads(self.sync_stamp_path.read_text())["ts"]
            last = datetime.datetime.fromisoformat(stamp)
        except (OSError, ValueError, KeyError):
            return float("inf")
        delta = (now or datetime.datetime.now()) - last
        return delta.total_seconds() / 86400.0
