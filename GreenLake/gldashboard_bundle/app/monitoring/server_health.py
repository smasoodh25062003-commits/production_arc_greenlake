"""Host / VM health metrics for the admin monitoring page."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Optional

_APP_STARTED_AT = time.time()

_DEFAULT_SERVICE = os.environ.get("GREENLAKE_SERVICE_NAME", "greenlake")


def _fmt_bytes(n: Optional[float]) -> str:
    if n is None:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_uptime(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _level(pct: Optional[float]) -> str:
    if pct is None:
        return "unknown"
    if pct >= 90:
        return "bad"
    if pct >= 75:
        return "warn"
    return "ok"


def _service_status(name: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": name,
        "state": "unknown",
        "active": False,
        "detail": None,
    }
    try:
        active = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=3,
        )
        state = (active.stdout or "").strip() or "unknown"
        info["state"] = state
        info["active"] = state == "active"

        show = subprocess.run(
            ["systemctl", "show", name, "--property=ActiveEnterTimestamp", "--value"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if show.returncode == 0 and (show.stdout or "").strip():
            info["detail"] = (show.stdout or "").strip()
    except FileNotFoundError:
        info["detail"] = "systemctl not available"
    except Exception as exc:
        info["detail"] = str(exc)
    return info


def _proc_metrics() -> dict[str, Any]:
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        return {
            "pid": proc.pid,
            "memory_bytes": mem.rss,
            "memory_human": _fmt_bytes(mem.rss),
            "threads": proc.num_threads(),
        }
    except Exception:
        return {"pid": os.getpid()}


def get_server_health() -> dict[str, Any]:
    """Collect CPU, memory, disk, uptime, and service status (best-effort)."""
    now = datetime.now(timezone.utc).isoformat()
    app_uptime_sec = time.time() - _APP_STARTED_AT

    health: dict[str, Any] = {
        "available": True,
        "collected_at": now,
        "hostname": platform.node() or "—",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "app_uptime_sec": int(app_uptime_sec),
        "app_uptime_human": _fmt_uptime(app_uptime_sec),
        "process": _proc_metrics(),
        "service": _service_status(_DEFAULT_SERVICE),
        "cpu_percent": None,
        "cpu_count": None,
        "load_avg": None,
        "memory": {},
        "disk": {},
        "system_uptime_sec": None,
        "system_uptime_human": "—",
    }

    try:
        import psutil

        health["cpu_count"] = psutil.cpu_count(logical=True)
        health["cpu_percent"] = round(psutil.cpu_percent(interval=0.2), 1)

        vm = psutil.virtual_memory()
        health["memory"] = {
            "total_bytes": vm.total,
            "used_bytes": vm.used,
            "available_bytes": vm.available,
            "percent": round(vm.percent, 1),
            "total_human": _fmt_bytes(vm.total),
            "used_human": _fmt_bytes(vm.used),
            "available_human": _fmt_bytes(vm.available),
            "level": _level(vm.percent),
        }

        boot = psutil.boot_time()
        sys_uptime = time.time() - boot
        health["system_uptime_sec"] = int(sys_uptime)
        health["system_uptime_human"] = _fmt_uptime(sys_uptime)

        if hasattr(os, "getloadavg"):
            load = os.getloadavg()
            health["load_avg"] = {
                "1m": round(load[0], 2),
                "5m": round(load[1], 2),
                "15m": round(load[2], 2),
            }
    except ImportError:
        health["available"] = False
        health["error"] = "psutil not installed"
    except Exception as exc:
        health["error"] = str(exc)

    # Disk usage (stdlib fallback works without psutil)
    try:
        root = os.environ.get("GREENLAKE_DISK_PATH", "/")
        usage = shutil.disk_usage(root)
        pct = round((usage.used / usage.total) * 100, 1) if usage.total else 0
        health["disk"] = {
            "path": root,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent": pct,
            "total_human": _fmt_bytes(usage.total),
            "used_human": _fmt_bytes(usage.used),
            "free_human": _fmt_bytes(usage.free),
            "level": _level(pct),
        }
    except Exception as exc:
        health["disk"] = {"error": str(exc), "level": "unknown"}

    if health.get("cpu_percent") is not None:
        health["cpu_level"] = _level(health["cpu_percent"])

    return health
