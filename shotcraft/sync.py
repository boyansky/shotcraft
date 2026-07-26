"""Pull new shots from the machine into the local store.

Read-only. If the machine is unreachable the exception propagates and nothing
is written, so a failed sync never leaves a partial record.

Entries are processed one at a time on purpose. Building every row in one
comprehension meant a single malformed entry aborted the whole batch after the
blobs had already been written: no id ever reached shots.jsonl, so the next
sync saw the same bad entry as new and crashed again, forever, taking the
healthy shots in the batch with it.

Rolling-window detection: any locally stored shot id missing from the machine's
current history proves the endpoint drops old shots. Reported as "vanished".
"""
from .model import flagged_row, shot_row


def sync(api, store):
    entries = api.history()          # raises MachineUnreachable; nothing written yet
    remote_ids = {e.get("id") for e in entries if isinstance(e, dict) and e.get("id")}
    known = store.shot_ids()

    rows = []
    unusable = 0
    for entry in entries:
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if not entry_id:
            # no id means no dedupe key and no telemetry filename; there is
            # nothing to store that a later sync could recognise again
            unusable += 1
            continue
        if entry_id in known:
            continue
        store.write_telemetry(entry_id, entry)   # raw blob first, always
        try:
            rows.append(shot_row(entry))
        except Exception as exc:
            # a surprised parser must never discard raw data or cost the batch
            rows.append(flagged_row(entry, f"{type(exc).__name__}: {exc}"))

    added = store.append_shots(rows)
    stored = store.load_shots()

    return {
        "added": added,
        "skipped": len(entries) - unusable - len(rows),
        "unusable": unusable,
        "vanished": sorted(known - remote_ids),
        "flagged": [r["id"] for r in rows if r.get("flags")],
        "unrated": [r["id"] for r in stored if r.get("taste") is None],
    }
