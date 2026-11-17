#!/usr/bin/env python3
"""Conservative local assessment and explicit entry point for Cache."""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HOME = Path.home().resolve()
CONFIG_PATH = HOME / ".config/cache/config.json"


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(CONFIG_PATH)


def profiles(config=None):
    config = load_config() if config is None else config
    found = config.get("profiles", {})
    if not isinstance(found, dict):
        found = {}
    if config.get("volume") and not found:
        found = {"default": {key: config[key] for key in
                             ("volume", "limit_gb", "min_free_gib", "recover_to_gib") if key in config}}
    return found


def validate_mount(raw_path):
    selected = Path(raw_path).expanduser().resolve(strict=True)
    if not selected.is_dir() or selected == Path("/") or not os.path.ismount(selected):
        raise ValueError("Select an existing mounted volume, not a folder or the root disk")
    if not os.access(selected, os.W_OK):
        raise ValueError("The mounted volume is not writable by this user")
    return selected


def activate(config, name):
    profile = profiles(config)[name]
    for key in ("volume", "limit_gb", "min_free_gib", "recover_to_gib"):
        config[key] = profile[key]
    config["active_profile"] = name
    return config


for key, value in load_config().items():
    environment_key = {"volume": "CACHE_VOLUME", "limit_gb": "CACHE_LIMIT_GB",
                       "min_free_gib": "CACHE_MIN_FREE_GIB", "recover_to_gib": "CACHE_RECOVER_TO_GIB"}.get(key)
    if environment_key and environment_key not in os.environ:
        os.environ[environment_key] = str(value)

VOLUME = Path(os.environ["CACHE_VOLUME"]).expanduser() if os.environ.get("CACHE_VOLUME") else None
JOBS = VOLUME / "Disposable Jobs/jobs" if VOLUME else None
PACKAGED_BANK = Path(__file__).with_name("bank.py")
GIB = 1024 ** 3
GENERATED_NAMES = {"build", "dist", ".next", "node_modules", "target", "deriveddata", ".cxx", "__pycache__"}


def under(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def size_gib(path):
    if not path.exists():
        return None
    try:
        result = subprocess.run([shutil.which("du") or "/usr/bin/du", "-sk", str(path)], capture_output=True,
                                text=True, timeout=20, check=True)
        return round(int(result.stdout.split("\t", 1)[0]) * 1024 / GIB, 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def no_open_files(path):
    try:
        lsof = shutil.which("lsof")
        if not lsof:
            return False
        result = subprocess.run([lsof, "-nP", "+D", str(path)],
                                capture_output=True, timeout=20)
        return result.returncode == 1 and not result.stderr
    except (OSError, subprocess.SubprocessError):
        return False


def assess(raw_path):
    supplied = Path(raw_path).expanduser().absolute()
    path = supplied.resolve(strict=False)
    result = {"path": str(supplied), "allocated_gib": size_gib(supplied),
              "decision": "review", "safe_to_reclaim": False,
              "reason": "Unknown contents; inspect before routing or changing files."}
    if supplied.is_symlink():
        result.update(decision="keep", reason="Symlink target and ownership require a separate review.")
        return result
    if JOBS and under(path, JOBS) and path.parent == JOBS and path.name.startswith("slot-"):
        marker = path / "completed.json"
        try:
            info = path.lstat()
            data = json.loads(marker.read_text(encoding="utf-8"))
            if (stat.S_ISDIR(info.st_mode) and hasattr(os, "getuid") and info.st_uid == os.getuid()
                    and data.get("id") == path.name and isinstance(data.get("at"), (int, float))
                    and no_open_files(path)):
                result.update(decision="reclaimable", safe_to_reclaim=True,
                              reason="Exact owned bank slot is complete and has no open handles. Reclaim via the bank only.")
                return result
        except (OSError, ValueError, TypeError):
            pass
        result.update(decision="keep", reason="Bank slot is active, unverified, or has open handles.")
        return result
    if under(path, HOME / ".codex"):
        result.update(decision="keep", reason="Codex chats, indexes, plugins or temporary state may be needed for continuity.")
        return result
    if any(part in {".git", "sources"} for part in path.parts):
        result.update(decision="keep", reason="Git or synced source material is persistent.")
        return result
    if under(path, Path("/System")) or under(path, Path("/Library")):
        result.update(decision="keep", reason="System-managed location; this plugin must not reclaim it.")
        return result
    if under(path, HOME / "Library/Application Support"):
        result.update(decision="keep", reason="Application Support can contain unique app state.")
        return result
    if path.name.lower() in GENERATED_NAMES or under(path, HOME / "Library/Developer/Xcode/DerivedData"):
        result.update(decision="review", reason="Likely generated output; verify active use and regeneration before any offload or reclaim.")
        return result
    if any(under(path, HOME / name) for name in ("Desktop", "Documents", "Downloads", "Projects", "Pictures", "Movies", "Music")):
        result.update(decision="keep", reason="User or project data is persistent unless an exact generated subfolder is verified.")
    return result


def bank_call(arguments):
    if not PACKAGED_BANK.is_file():
        raise SystemExit("Cache bank is unavailable; no cache action was taken")
    return subprocess.call([sys.executable, str(PACKAGED_BANK), *arguments])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("status", help="Show physical free space and completed bank jobs")
    configure = commands.add_parser("configure", help="Choose an existing mounted cache volume")
    configure.add_argument("volume")
    configure.add_argument("--name", default="default", help="Reusable profile name")
    configure.add_argument("--kind", choices=("internal", "usb", "external", "nas", "cloud-mount"),
                           default="external", help="Storage type; cloud-mount requires your own mounted drive")
    configure.add_argument("--limit-gb", type=float, default=150)
    configure.add_argument("--min-free-gib", type=float, default=40)
    commands.add_parser("backends", help="List configured storage profiles and mount state")
    use = commands.add_parser("use", help="Select a configured mounted storage profile")
    use.add_argument("name")
    review = commands.add_parser("assess", help="Conservatively classify one exact path")
    review.add_argument("path")
    run = commands.add_parser("run", help="Run explicitly disposable work in a guarded slot")
    run.add_argument("--disposable", action="store_true", required=True)
    run.add_argument("label")
    run.add_argument("command", nargs=argparse.REMAINDER)
    reclaim = commands.add_parser("reclaim", help="Preview or reclaim completed bank jobs")
    reclaim.add_argument("--yes", action="store_true", help="Reclaim the previewed completed jobs")
    args = parser.parse_args(argv)
    if args.action == "configure":
        try:
            selected = validate_mount(args.volume)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if args.limit_gb <= 0 or args.min_free_gib < 0:
            parser.error("limit must be positive and minimum free space cannot be negative")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args.name):
            parser.error("profile name must use letters, digits, underscores or hyphens")
        config = load_config()
        config["profiles"] = profiles(config)
        config["profiles"][args.name] = {"volume": str(selected), "kind": args.kind,
                                         "limit_gb": args.limit_gb, "min_free_gib": args.min_free_gib,
                                         "recover_to_gib": max(60, args.min_free_gib)}
        activate(config, args.name)
        save_config(config)
        print(json.dumps({"configured": config, "path": str(CONFIG_PATH)}, indent=2))
        return 0
    if args.action == "backends":
        config = load_config()
        items = []
        for name, profile in profiles(config).items():
            path = Path(profile["volume"])
            items.append({"name": name, "kind": profile.get("kind", "external"),
                          "path": str(path), "mounted": os.path.ismount(path),
                          "active": config.get("active_profile", "default") == name})
        print(json.dumps({"backends": items}, indent=2))
        return 0
    if args.action == "use":
        config = load_config()
        found = profiles(config)
        if args.name not in found:
            parser.error("unknown profile")
        try:
            validate_mount(found[args.name]["volume"])
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        activate(config, args.name)
        save_config(config)
        print(json.dumps({"active_profile": args.name, "volume": config["volume"]}, indent=2))
        return 0
    if args.action == "assess":
        print(json.dumps(assess(args.path), indent=2))
        return 0
    if args.action == "run":
        command = args.command[1:] if args.command and args.command[0] == "--" else args.command
        if not command or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args.label):
            parser.error("run requires a safe label and a command after --")
        return bank_call(["run", args.label, "--", *command])
    if args.action == "reclaim":
        preview = [assess(str(path)) for path in sorted(JOBS.glob("slot-*"))] if JOBS else []
        ready = [item for item in preview if item["safe_to_reclaim"]]
        print(json.dumps({"reclaimable_jobs": ready,
                          "total_allocated_gib": round(sum(item["allocated_gib"] or 0 for item in ready), 3),
                          "will_reclaim": args.yes}, indent=2), flush=True)
        if not args.yes:
            return 0
        for item in ready:
            if bank_call(["reclaim", Path(item["path"]).name]) != 0:
                return 1
        return 0
    return bank_call([args.action])


if __name__ == "__main__":
    raise SystemExit(main())
