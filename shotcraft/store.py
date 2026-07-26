"""JSONL persistence for shots and bags, plus per-shot telemetry blobs.

Telemetry blobs are committed to git on purpose: if the machine's history
endpoint turns out to be a rolling window, these files are the only copy.
"""
import json
import pathlib


class Store:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.shots_path = self.root / "shots.jsonl"
        self.bags_path = self.root / "bags.jsonl"
        self.telemetry_dir = self.root / "telemetry"

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

    def update_shot(self, shot_id, fields):
        """Merge fields into one stored shot row, preserving file order."""
        rows = self.load_shots()
        for row in rows:
            if row["id"] == shot_id:
                row.update(fields)
                with self.shots_path.open("w") as handle:
                    for r in rows:
                        handle.write(json.dumps(r) + "\n")
                return row
        raise KeyError(f"no stored shot with id {shot_id!r}")

    def write_telemetry(self, shot_id, entry):
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)
        path = self.telemetry_dir / f"{shot_id}.json"
        path.write_text(json.dumps(entry))
        return path

    def has_telemetry(self, shot_id):
        return (self.telemetry_dir / f"{shot_id}.json").exists()
