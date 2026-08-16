# shotcraft

```
          ⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠞⠉⠉⠓⠲⠤⣄⣀⠀⠀⠀⠀⠀
          ⠀⠀⠀⠀⠀⠀⠀⢀⡴⠋⠀⠀⠀⠀⠀⠀⠀⠈⠙⠒⠦⢤⣀
          ⠤⠴⠚⠉⠉⠉⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
          ╭─────────────────────╮
          │                     │
          ╲▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒╱
           ╲▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒╱
            ╰─────────────────╯

  █▀▀ █  █ █▀▀█ ▀█▀ █▀▀ █▀▀█ █▀▀█ █▀▀ ▀█▀
  ▀▀█ █▀▀█ █  █  █  █   █▄▄▀ █▄▄█ █▀▀  █ 
  ▀▀▀ ▀  ▀ ▀▀▀▀  ▀  ▀▀▀ ▀  ▀ ▀  ▀ ▀    ▀ 

         intent · telemetry · taste
```

A read-only shot record for Meticulous espresso machines: profile intent,
machine telemetry, and your own taste, in one place.

The mark is an extraction curve drawn in braille, rising off a shot glass whose
rim doubles as the x-axis.

## Safety

**This tool never writes to the machine.** GET requests only. Profiles are
authored and validated locally and installed by hand.

## Install

    uv tool install shotcraft        # or: pipx install shotcraft
    shotcraft setup

Python 3.11+. **No runtime dependencies** — standard library only.

## Setup

    shotcraft setup            # find your machine and remember it
    shotcraft setup --show     # where config and data live, and what is in effect
    shotcraft setup --url http://192.168.1.5   # skip discovery

`setup` looks for your machine by mDNS first, then sweeps your subnet asking
each host for `/api/v1/machine`, and only offers you addresses that answered
like a real Meticulous. Nothing is written until you confirm.

Config lives in `~/.config/shotcraft/config.json`; your record lives in
`~/.local/share/shotcraft/`, never inside the package. Override either with
`SHOTCRAFT_API_URL` (or `METICULOUS_API_URL`) and `SHOTCRAFT_HOME`.

## Use

    shotcraft sync             # pull new shots, list the ones awaiting a rating
    shotcraft sync --from DIR  # ingest from a directory of blob files instead
    shotcraft bag              # register a new bag of beans
    shotcraft bags             # list bags, marking the current one
    shotcraft grinder          # register a grinder
    shotcraft grinders         # list registered grinders
    shotcraft dial <value>     # record a re-dial: grind, and dose if it changed
    shotcraft rate <shot_id>   # taste it blind, then see what the machine did
    shotcraft report           # corpus summary with sample sizes
    shotcraft check <shot_id>  # did the shot track its profile?
    shotcraft nudge            # one line for a shell prompt hook, else silent

`shotcraft` is a wrapper on your PATH that sets `PYTHONPATH` and runs the
package, so it works from any directory. Without it, the equivalent is
`python3 -m shotcraft.cli <command>` from the repo root. The CLI takes its
displayed name from however you invoked it, so an alias stays consistent.

`<shot_id>` is the short id printed by `sync` and `report`; the full uuid works
too, and so does any unambiguous prefix of it.

A typical loop: pull the shot with `sync`, drink it and `rate` it, then look at
`report` and `check`. Every human field goes in through `rate`, `bag`,
`grinder` or `dial`, which validate what you type.

**Never hand-edit `shots.jsonl` to enter a rating.** Yield, time and ratio sit
on the same line as the taste fields, so editing the file means rating the shot
while looking at the machine's verdict, which is the one thing that destroys
taste as an independent signal. `rate` shows you the profile name and the time
and nothing else, on purpose.

### Getting reminded

`nudge` prints at most one line, at most once a day, reads only local files and
opens no socket, so it is safe in a shell prompt:

    # ~/.zshrc
    autoload -Uz add-zsh-hook
    add-zsh-hook precmd shotcraft_nudge
    shotcraft_nudge() { shotcraft nudge 2>/dev/null }

It is the trigger. Terminals get opened constantly; `shotcraft` does not.

## Rating protocol

Rate **before** looking at telemetry, time, or yield. Seeing the numbers first
turns taste into an echo of the machine instead of an independent signal.
Rate on the first two sips, within ~30 seconds. Rough and fast beats blank.

Four prompts on the common path: bag confirmation, then the taste call below,
then an optional note. A same-day previous shot adds a fifth (`vs previous
shot`).

| lean      | s sour · b bitter · x both · - balanced |
| intensity | 1 slight · 2 clear · 3 badly  (skipped when lean is `-`) |
| vs previous shot | b better · w worse · = same  (optional: only asked when another shot from that day is already in the record, rated or not — it was still tasted) |

`both` is a real answer, not a compromise: a shot can be sour and bitter at
once, which is what uneven extraction tastes like, and collapsing that onto a
single direction would record the most diagnostic shot as the least.

Dose and grind are no longer typed at rating time. They resolve from the
dial-in log (`dial`) instead, because they are properties of the setup, not
of the cup, and asking again every morning is exactly the kind of keystroke
that killed the previous version of this tool.

Ratings are stamped with the scale version in force (`taste_schema`, now 3).
Rows from older versions are shown but never pooled: a v1 or v2 rating renders
with a `v1`/`v2` prefix, because neither the single sour/bitter axis of v1 nor
the four absolute scales of v2 can be converted into schema 3's direction
without inventing the direction.

## Tests

    python3 -m unittest discover -s tests

## Contributing

Issues and pull requests welcome, especially from people who own one of these
machines. Bug reports are most useful with the output of `shotcraft setup
--show` and, if a shot is involved, its `check` output.

    python3 -m unittest discover -s tests     # 383 tests, no network needed

Tests run against captured fixtures and never contact a machine. If you add a
feature that talks to one, keep the impure part injectable so it stays that way.

### Design rules that are load-bearing

These are not style preferences. Each exists because breaking it makes the
tool lie, and each is pinned by a test:

- **Read-only, always.** `GET` only, no exceptions. This talks to a
  pressurised appliance with no staging environment. A test scans `api.py`'s
  own source for write verbs and fails if one appears.
- **`rate` never shows machine numbers.** Not yield, time, ratio, pressure or
  flow. If you see "38 seconds" before scoring, your palate reaches for the
  answer that fits and taste stops being independent evidence. A test captures
  both the prompts and stdout to enforce it.
- **`evidence_level` never returns "validated".** The corpus is small and grows
  slowly. A tool that speaks confidently at n=4 is worse than no tool, because
  its conclusions would be acted on.
- **`taste_schema` is versioned and ratings are never migrated across
  versions.** A v1 rating genuinely cannot say whether a 0 meant "balanced" or
  "sour and bitter at once", so converting it would invent data. Schema 3 keeps
  a `both` lean value for the same underlying reason: a shot can be sour and
  bitter at once, and collapsing that onto a single direction would record the
  most diagnostic shot as the least.
- **`nudge` never raises and never opens a socket.** It sits in a shell prompt
  hook, so a corrupt `shots.jsonl` or a stale stamp file must degrade to
  silence, never to a traceback on every new terminal.
- **Zero runtime dependencies.** Standard library only.

## Not affiliated with Meticulous

shotcraft is an independent, unofficial tool. It is not a product of
Meticulous Home, and they do not support it. Please do not raise shotcraft
issues with them.
