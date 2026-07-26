"""Where the machine lives, and where your record lives.

Two separate locations, on purpose:

  config  ~/.config/shotcraft/config.json      which machine to talk to
  data    ~/.local/share/shotcraft/            shots.jsonl, bags.jsonl, telemetry/

Data must NOT sit inside the installed package. It did once, resolved from
__file__, which was fine for a single checkout and wrong the moment anyone
installs this: their shots would be written into site-packages and vanish on
upgrade.

Precedence for the machine address, highest first:

  1. SHOTCRAFT_API_URL / METICULOUS_API_URL   (env, for one-off overrides and CI)
  2. the config file                          (what `setup` writes)
  3. nothing — callers must ask the user to run `setup`

Both env names are honoured: METICULOUS_API_URL is the convention other tools
in this ecosystem already use, so people arrive with it already set.
"""
import json
import os
import pathlib

APP = "shotcraft"
ENV_VARS = ("SHOTCRAFT_API_URL", "METICULOUS_API_URL")


class NotConfigured(RuntimeError):
    """No machine address is known. Not an error state — just unset."""


def config_home():
    root = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
    return pathlib.Path(root) / APP


def data_home():
    if os.environ.get("SHOTCRAFT_HOME"):
        return pathlib.Path(os.environ["SHOTCRAFT_HOME"])
    root = os.environ.get("XDG_DATA_HOME") or (pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(root) / APP


def config_path():
    return config_home() / "config.json"


def load():
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


def save(**fields):
    """Merge fields into the config file, creating it if needed."""
    current = load()
    current.update(fields)
    config_home().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(current, indent=2) + "\n")
    return current


def env_base_url():
    for name in ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value.rstrip("/")
    return None


def base_url(required=True):
    """The machine address, or raise NotConfigured with a fixable message."""
    found = env_base_url() or (load().get("base_url") or "").strip().rstrip("/")
    if found:
        return found
    if not required:
        return None
    raise NotConfigured(
        "no machine configured yet — run `shotcraft setup` to find yours, "
        f"or set {ENV_VARS[0]}")
