"""Shared system metrics + oled_config helpers for the phone dashboard and
the (optional, currently masked) physical SSD1306 OLED monitor.

Both entry points read the same Raspberry Pi system stats and the same
`oled_config` table in the bot's sqlite database, so that logic lives here
once instead of being duplicated across web_monitor / monitor_data and
oled_monitor.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import time

import psutil

DB_PATH = os.getenv("DB_PATH", "/root/bot/bot.db")

DEFAULT_OLED_CONFIG = {"power": "on", "forced_screen": "-1", "scrolling_text": ""}


def get_ip() -> str:
    try:
        return (
            subprocess.check_output("hostname -I | cut -d' ' -f1", shell=True)
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return "127.0.0.1"


def get_cpu_temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read()) / 1000.0
    except Exception:
        return 0.0


def get_fan_speed() -> int:
    try:
        with open("/sys/class/thermal/cooling_device0/cur_state", "r") as f:
            state = int(f.read().strip())
        state_map = {0: 0, 1: 50, 2: 75, 3: 100, 4: 100}
        return state_map.get(state, 0)
    except Exception:
        return 0


def read_oled_config(db_path: str | None = None) -> dict:
    """Read the `oled_config` key/value table (read-only connection)."""
    try:
        conn = sqlite3.connect(f"file:{db_path or DB_PATH}?mode=ro", uri=True)
        res = conn.execute("SELECT key, value FROM oled_config").fetchall()
        conn.close()
        return dict(res)
    except Exception:
        return dict(DEFAULT_OLED_CONFIG)


def clear_scrolling_text(db_path: str | None = None) -> None:
    try:
        conn = sqlite3.connect(db_path or DB_PATH)
        conn.execute("UPDATE oled_config SET value='' WHERE key='scrolling_text'")
        conn.commit()
        conn.close()
    except Exception:
        pass


class OledConfigCache:
    """mtime-gated cache around read_oled_config so pollers avoid hammering sqlite."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        self._mtime: float | None = None
        self._value: dict = dict(DEFAULT_OLED_CONFIG)

    def get(self) -> dict:
        try:
            mtime = os.path.getmtime(self.db_path)
        except OSError:
            return self._value
        if self._mtime == mtime and self._value:
            return self._value
        self._value = read_oled_config(self.db_path)
        self._mtime = mtime
        return self._value

    def clear_scrolling_text(self) -> None:
        clear_scrolling_text(self.db_path)
        self._value["scrolling_text"] = ""


def top_processes(limit: int = 5) -> list[dict]:
    """Top processes by CPU then memory. Primes psutil's cpu_percent counters
    first, so callers should expect a short (~0.12s) blocking sleep."""
    for proc in psutil.process_iter(["pid"]):
        try:
            proc.cpu_percent(interval=None)
        except Exception:
            pass
    time.sleep(0.12)
    rows: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            info = proc.info
            cpu = float(proc.cpu_percent(interval=None) or 0.0)
            mem = float(info.get("memory_percent") or 0.0)
            rows.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "name": (info.get("name") or "?")[:40],
                    "cpu": round(cpu, 1),
                    "mem": round(mem, 1),
                }
            )
        except Exception:
            continue
    rows.sort(key=lambda r: (r["cpu"], r["mem"]), reverse=True)
    return rows[:limit]


def system_snapshot(include_top_procs: bool = False, top_procs_limit: int = 5) -> dict:
    """One-shot read of CPU/RAM/swap/disk/load/uptime/fan/temp."""
    snap: dict = {}
    snap["cpu"] = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    snap["ram"] = ram.percent
    snap["ram_used_mb"] = (ram.total - ram.available) // (1024 * 1024)
    snap["ram_total_mb"] = ram.total // (1024 * 1024)
    swap = psutil.swap_memory()
    snap["swap"] = int(swap.percent)
    snap["swap_used_mb"] = int(swap.used // (1024 * 1024))
    snap["swap_total_mb"] = int(swap.total // (1024 * 1024))
    try:
        disk = psutil.disk_usage("/")
        snap["disk"] = int(disk.percent)
        snap["disk_used_gb"] = round(disk.used / (1024**3), 1)
        snap["disk_total_gb"] = round(disk.total / (1024**3), 1)
    except Exception:
        snap["disk"] = 0
        snap["disk_used_gb"] = 0
        snap["disk_total_gb"] = 0
    try:
        load1, load5, load15 = os.getloadavg()
        snap["load_1"] = round(load1, 2)
        snap["load_5"] = round(load5, 2)
        snap["load_15"] = round(load15, 2)
    except Exception:
        snap["load_1"] = snap["load_5"] = snap["load_15"] = 0
    try:
        snap["uptime_sec"] = int(time.time() - psutil.boot_time())
    except Exception:
        snap["uptime_sec"] = 0
    snap["fan"] = get_fan_speed()
    snap["temp"] = get_cpu_temp()
    if include_top_procs:
        try:
            snap["top_procs"] = top_processes(top_procs_limit)
        except Exception:
            snap["top_procs"] = []
    return snap
