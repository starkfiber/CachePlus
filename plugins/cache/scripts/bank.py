#!/usr/bin/env python3
"""Explicit, disposable work cache on an opt-in mounted volume (macOS/Linux)."""

try:
    import fcntl
except ImportError:  # Windows can inspect status; job execution is not supported.
    fcntl = None
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

CONFIG_PATH = Path.home() / ".config/cache/config.json"
try:
    configured = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(configured, dict):
        configured = {}
except (OSError, ValueError):
    configured = {}
configured_volume = os.environ.get("CACHE_VOLUME") or configured.get("volume")
VOLUME = Path(configured_volume).expanduser() if configured_volume else None
ROOT = VOLUME / "Disposable Jobs" if VOLUME else None
JOBS = ROOT / "jobs" if ROOT else None
LOCK = ROOT / "bank.lock" if ROOT else None
GIB = 1024 ** 3
MAX_BANK_GIB = float(os.environ.get("CACHE_LIMIT_GB", configured.get("limit_gb", 150))) * 1_000_000_000 / GIB
MIN_FREE_GIB = float(os.environ.get("CACHE_MIN_FREE_GIB", configured.get("min_free_gib", 40)))
RECOVER_TO_GIB = max(MIN_FREE_GIB, float(os.environ.get("CACHE_RECOVER_TO_GIB", configured.get("recover_to_gib", 60))))
MIN_AGE_SECONDS = 0
if MAX_BANK_GIB <= 0 or MIN_FREE_GIB < 0:
    raise SystemExit("CACHE_LIMIT_GB must be positive and CACHE_MIN_FREE_GIB cannot be negative")
SETTINGS_LOCK = threading.RLock()


def refresh_settings():
    """Refresh a long-lived API/MCP process after a CLI profile switch."""
    global VOLUME, ROOT, JOBS, LOCK, MAX_BANK_GIB, MIN_FREE_GIB, RECOVER_TO_GIB
    try:
        current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    raw = os.environ.get("CACHE_VOLUME") or current.get("volume")
    VOLUME = Path(raw).expanduser() if raw else None
    ROOT = VOLUME / "Disposable Jobs" if VOLUME else None
    JOBS = ROOT / "jobs" if ROOT else None
    LOCK = ROOT / "bank.lock" if ROOT else None
    MAX_BANK_GIB = float(os.environ.get("CACHE_LIMIT_GB", current.get("limit_gb", 150))) * 1_000_000_000 / GIB
    MIN_FREE_GIB = float(os.environ.get("CACHE_MIN_FREE_GIB", current.get("min_free_gib", 40)))
    RECOVER_TO_GIB = max(MIN_FREE_GIB, float(os.environ.get("CACHE_RECOVER_TO_GIB", current.get("recover_to_gib", 60))))
    if MAX_BANK_GIB <= 0 or MIN_FREE_GIB < 0:
        raise RuntimeError("Invalid CachePlus size or free-space limit")


def free_gib():
    return shutil.disk_usage(Path.home()).free / GIB


def owned_root():
    if VOLUME is None:
        raise RuntimeError("Set CACHE_VOLUME to a dedicated mounted cache volume; no work was started")
    if not os.path.ismount(VOLUME):
        raise RuntimeError("Configured cache volume is not mounted; refusing to write to its mount path")
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    JOBS.mkdir(mode=0o700, exist_ok=True)
    for path in (ROOT, JOBS):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError(f"Unsafe cache location: {path}")


def folder_bytes(path):
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(base) / name).lstat().st_blocks * 512
            except OSError:
                pass
        for name in dirs:
            try:
                total += (Path(base) / name).lstat().st_blocks * 512
            except OSError:
                pass
    return total


def bank_size_gib():
    try:
        result = subprocess.run([shutil.which("du") or "/usr/bin/du", "-sk", str(JOBS)],
                                capture_output=True, text=True, timeout=20, check=True)
        return int(result.stdout.split()[0]) / (1024 ** 2)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def completed_jobs(measure_sizes=False, only=None):
    if VOLUME is None or not os.path.ismount(VOLUME):
        return []
    owned_root()
    result = []
    for path in JOBS.iterdir():
        if only is not None and path.name != only:
            continue
        try:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or path.is_symlink():
                continue
            marker = path / "completed.json"
            data = json.loads(marker.read_text(encoding="utf-8"))
            if data.get("id") != path.name or not isinstance(data.get("at"), (int, float)):
                continue
            recorded = data.get("allocated_bytes")
            size = (folder_bytes(path) if measure_sizes else
                    recorded if isinstance(recorded, int) and recorded >= 0 else None)
            result.append((data["at"], path, size))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(result)


def status(deep=False):
    with SETTINGS_LOCK:
        refresh_settings()
        jobs = completed_jobs(measure_sizes=deep)
        free = free_gib()
        known = sum(item[2] or 0 for item in jobs) / GIB
        complete = all(item[2] is not None for item in jobs)
        return {"free_gib": round(free, 2),
            "completed_cache_gib": round(known, 2) if complete else None,
            "completed_cache_gib_known": round(known, 2),
            "completed_cache_size_complete": complete,
            "size_measurement": "deep" if deep else "recorded-metadata",
            "usable_after_cache_reclaim_gib": round(free, 2),
            "reclaim_note": "Cache jobs may live on another physical device; reclaim is not counted as Mac free space until measured.",
            "volume_mounted": bool(VOLUME and os.path.ismount(VOLUME)),
            "configured_volume": str(VOLUME) if VOLUME else None,
            "volume_quota_gb": None,
            "configured_bank_limit_gb": round(MAX_BANK_GIB * GIB / 1_000_000_000, 2),
            "bank_limit_gib": round(MAX_BANK_GIB, 2), "minimum_real_free_gib": MIN_FREE_GIB,
            "completed_jobs": len(jobs)}


def has_open_files(path):
    try:
        lsof = shutil.which("lsof")
        if not lsof:
            return True
        result = subprocess.run([lsof, "-nP", "+D", str(path)],
                                capture_output=True, timeout=20)
        return result.returncode != 1 or bool(result.stderr)
    except (OSError, subprocess.SubprocessError):
        return True


def prune(force=False, only=None):
    if fcntl is None:
        raise RuntimeError("Cache job reclaim currently requires macOS or Linux")
    if VOLUME is None or not os.path.ismount(VOLUME):
        return {"removed_jobs": [], **status()}
    owned_root()
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = time.time()
        jobs = completed_jobs(measure_sizes=True, only=only)
        total = sum(item[2] for item in jobs) / GIB
        removed = []
        needs_space = free_gib() < MIN_FREE_GIB
        for at, path, size in jobs:
            if only is not None and path.name != only:
                continue
            target_free = RECOVER_TO_GIB if needs_space else MIN_FREE_GIB
            if not force and total <= MAX_BANK_GIB and free_gib() >= target_free:
                break
            if now - at < MIN_AGE_SECONDS or has_open_files(path):
                continue
            shutil.rmtree(path)
            total -= size / GIB
            removed.append(path.name)
        return {"removed_jobs": removed, **status()}


def run_job(label, command):
    if fcntl is None:
        raise RuntimeError("Cache job execution currently requires macOS or Linux")
    owned_root()
    if free_gib() < MIN_FREE_GIB:
        prune()
    if free_gib() < MIN_FREE_GIB:
        raise RuntimeError("Less than 40 GiB physically free; cannot start a new cache job")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
    if not safe_label:
        raise ValueError("A cache job needs a non-empty label")
    job = JOBS / f"slot-{safe_label}"
    lock_path = ROOT / f"slot-{safe_label}.lock"
    slot_lock = lock_path.open("w")
    try:
        fcntl.flock(slot_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        slot_lock.close()
        raise RuntimeError(f"Cache job {safe_label} is already running")
    with LOCK.open("w") as global_lock:
        fcntl.flock(global_lock, fcntl.LOCK_EX)
        job.mkdir(mode=0o700, exist_ok=True)
        info = job.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or job.is_symlink():
            slot_lock.close()
            raise RuntimeError(f"Unsafe cache job location: {job}")
        if has_open_files(job):
            slot_lock.close()
            raise RuntimeError(f"Cache job {safe_label} still has open files; refusing slot reuse")
        marker = job / "completed.json"
        marker.unlink(missing_ok=True)
    tmp = job / "tmp"
    cache = job / "cache"
    target = job / "target"
    for path in (tmp, cache, target):
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or path.is_symlink():
            slot_lock.close()
            raise RuntimeError(f"Unsafe cache subdirectory: {path}")
    env = os.environ.copy()
    env.update({"TMPDIR": str(tmp) + "/", "XDG_CACHE_HOME": str(cache),
                "npm_config_cache": str(cache / "npm"), "PIP_CACHE_DIR": str(cache / "pip"),
                "UV_CACHE_DIR": str(cache / "uv"), "CARGO_TARGET_DIR": str(target)})
    print(f"Reusable disposable cache: {job}", flush=True)
    process = None
    try:
        process = subprocess.Popen(command, env=env, start_new_session=True)
        code = None
        checks = 0
        while code is None:
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                checks += 1
                bank_gib = bank_size_gib() if checks % 3 == 0 else None
                if (os.path.ismount(VOLUME) and free_gib() >= MIN_FREE_GIB
                        and (checks % 3 != 0 or (bank_gib is not None and bank_gib < MAX_BANK_GIB))):
                    continue
                # This wrapper is only for disposable work; stop it before it fills the Mac.
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                code = 75
                print("Stopped disposable job: free-space buffer or bank limit reached", file=sys.stderr)
    finally:
        group_alive = False
        if process is not None:
            try:
                os.killpg(process.pid, 0)
                group_alive = True
            except ProcessLookupError:
                pass
            except PermissionError:
                group_alive = True
        if group_alive:
            print("Cache child process is still running; slot remains non-reclaimable", file=sys.stderr)
        elif process is not None and code == 0:
            with LOCK.open("w") as global_lock:
                fcntl.flock(global_lock, fcntl.LOCK_EX)
                marker.write_text(json.dumps({"id": job.name, "at": time.time(),
                                              "allocated_bytes": folder_bytes(job)}) + "\n", encoding="utf-8")
                os.chmod(marker, 0o600)
        slot_lock.close()
    return code


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "status":
        if len(sys.argv) > 3 or (len(sys.argv) == 3 and sys.argv[2] != "--deep"):
            raise SystemExit("usage: bank.py status [--deep]")
        print(json.dumps(status(deep="--deep" in sys.argv[2:]), indent=2))
    elif action == "prune":
        print(json.dumps(prune(), indent=2))
    elif action == "reclaim":
        exact = sys.argv[2] if len(sys.argv) == 3 else None
        if len(sys.argv) > 3 or (exact is not None and
                                  (not exact.startswith("slot-") or
                                   not all(c.isalnum() or c in "-_" for c in exact))):
            raise SystemExit("reclaim accepts at most one exact slot name")
        print(json.dumps(prune(force=True, only=exact), indent=2))
    elif action == "run" and len(sys.argv) > 4 and sys.argv[3] == "--":
        raise SystemExit(run_job(sys.argv[2], sys.argv[4:]))
    else:
        raise SystemExit("usage: bank.py status|prune|reclaim|run LABEL -- COMMAND [ARGS...]")
