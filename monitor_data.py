"""Shared system / weather / Jira cache for OLED and phone web monitor."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
import time

import psutil
import requests
from dotenv import load_dotenv

from jira_client import JiraClient
from music_bridge import music_bridge

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

OWM_API_KEY = os.getenv("OWM_API_KEY", "")
CITY = os.getenv("CITY", "Astana")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")
DB_PATH = os.getenv("DB_PATH", "/root/bot/bot.db")
JIRA_REFRESH = int(os.getenv("JIRA_REFRESH", 300))
WEATHER_REFRESH = int(os.getenv("WEATHER_REFRESH", 600))
IP_REFRESH = 60
DATA_REFRESH = 5


def get_ip() -> str:
    try:
        return (
            subprocess.check_output("hostname -I | cut -d' ' -f1", shell=True)
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return "127.0.0.1"


def get_oled_config() -> dict:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        res = conn.execute("SELECT key, value FROM oled_config").fetchall()
        conn.close()
        return dict(res)
    except Exception:
        return {"power": "on", "forced_screen": "-1", "scrolling_text": ""}


class MonitorData:
    def __init__(self):
        self.city = CITY
        self.weather = {
            "main": "Wait",
            "temp": "0",
            "hum": "0",
            "wind": "0",
            "desc": "loading...",
        }
        self.last_weather_time = 0
        self.cache = {
            "ip": "127.0.0.1",
            "conf": {"power": "on", "forced_screen": "-1", "scrolling_text": ""},
            "cpu": 0,
            "ram": 0,
            "ram_used_mb": 0,
            "ram_total_mb": 0,
            "fan": 0,
            "temp": 0.0,
        }
        self.jira = {
            "todo": "--",
            "in_progress": "--",
            "week_h": "--",
            "month_h": "--",
            "day_h": "--",
            "ok": False,
            "sprint_name": "",
            "todo_label": "To Do",
            "wip_label": "WIP",
            "todo_issues": [],
            "wip_issues": [],
            "columns": [],
        }
        self.last_jira_time = 0
        self.jira_client = JiraClient()
        self.scroll_text_active = ""
        self._db_mtime = None
        self._last_ip_time = 0
        self._last_data_update = 0
        self._lock = threading.Lock()
        self._started = False

    def get_cpu_temp(self) -> float:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read()) / 1000.0
        except Exception:
            return 0.0

    def get_fan_speed(self) -> int:
        try:
            with open("/sys/class/thermal/cooling_device0/cur_state", "r") as f:
                state = int(f.read().strip())
            state_map = {0: 0, 1: 50, 2: 75, 3: 100, 4: 100}
            return state_map.get(state, 0)
        except Exception:
            return 0

    def _load_oled_config(self) -> dict:
        try:
            mtime = os.path.getmtime(DB_PATH)
        except OSError:
            return self.cache.get("conf") or {
                "power": "on",
                "forced_screen": "-1",
                "scrolling_text": "",
            }
        if self._db_mtime == mtime and self.cache.get("conf"):
            return self.cache["conf"]
        conf = get_oled_config()
        self._db_mtime = mtime
        return conf

    def _clear_scrolling_text(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE oled_config SET value='' WHERE key='scrolling_text'")
            conn.commit()
            conn.close()
        except Exception:
            pass
        if "conf" in self.cache:
            self.cache["conf"]["scrolling_text"] = ""

    def update_jira_cache(self):
        stats = self.jira_client.fetch_stats()
        with self._lock:
            self.jira = {
                "todo": stats.get("todo", "--"),
                "in_progress": stats.get("in_progress", "--"),
                "week_h": stats.get("week_h", "--"),
                "month_h": stats.get("month_h", "--"),
                "day_h": stats.get("day_h", "--"),
                "ok": bool(stats.get("ok")),
                "sprint_name": stats.get("sprint_name") or "",
                "todo_label": stats.get("todo_label") or "To Do",
                "wip_label": stats.get("wip_label") or "WIP",
                "todo_issues": list(stats.get("todo_issues") or []),
                "wip_issues": list(stats.get("wip_issues") or []),
                "columns": list(stats.get("columns") or []),
            }
            self.last_jira_time = time.time()

    def update_weather_cache(self):
        if not OWM_API_KEY or OWM_API_KEY in ("YOUR_OPENWEATHERMAP_KEY", "your_api_key_here"):
            self.last_weather_time = time.time()
            return
        try:
            r = requests.get(
                f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OWM_API_KEY}&units=metric",
                timeout=3,
            ).json()
            if "weather" in r:
                with self._lock:
                    self.weather = {
                        "main": r["weather"][0]["main"],
                        "temp": f"{r['main']['temp']:.0f}",
                        "hum": f"{r['main']['humidity']}",
                        "wind": f"{r['wind']['speed']:.1f}",
                        "desc": r["weather"][0]["description"],
                    }
                    self.last_weather_time = time.time()
        except Exception:
            pass


    def _top_processes(self, limit: int = 5) -> list[dict]:
        # Prime cpu counters
        for proc in psutil.process_iter(["pid"]):
            try:
                proc.cpu_percent(interval=None)
            except (psutil.Error, Exception):
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
            except (psutil.Error, Exception):
                continue
        rows.sort(key=lambda r: (r["cpu"], r["mem"]), reverse=True)
        return rows[:limit]

    def update_data_cache(self):
        now = time.time()
        if now - self._last_ip_time >= IP_REFRESH or self.cache["ip"] == "127.0.0.1":
            self.cache["ip"] = get_ip()
            self._last_ip_time = now
        conf = self._load_oled_config()
        self.cache["conf"] = conf
        scroll_txt = (conf.get("scrolling_text") or "").strip()
        if scroll_txt and not self.scroll_text_active:
            self.scroll_text_active = scroll_txt
            self._clear_scrolling_text()
        self.cache["cpu"] = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        self.cache["ram"] = ram.percent
        self.cache["ram_used_mb"] = (ram.total - ram.available) // (1024 * 1024)
        self.cache["ram_total_mb"] = ram.total // (1024 * 1024)
        swap = psutil.swap_memory()
        self.cache["swap"] = int(swap.percent)
        self.cache["swap_used_mb"] = int(swap.used // (1024 * 1024))
        self.cache["swap_total_mb"] = int(swap.total // (1024 * 1024))
        try:
            disk = psutil.disk_usage("/")
            self.cache["disk"] = int(disk.percent)
            self.cache["disk_used_gb"] = round(disk.used / (1024 ** 3), 1)
            self.cache["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
        except Exception:
            self.cache["disk"] = 0
            self.cache["disk_used_gb"] = 0
            self.cache["disk_total_gb"] = 0
        try:
            load1, load5, load15 = os.getloadavg()
            self.cache["load_1"] = round(load1, 2)
            self.cache["load_5"] = round(load5, 2)
            self.cache["load_15"] = round(load15, 2)
        except Exception:
            self.cache["load_1"] = self.cache["load_5"] = self.cache["load_15"] = 0
        try:
            self.cache["uptime_sec"] = int(time.time() - psutil.boot_time())
        except Exception:
            self.cache["uptime_sec"] = 0
        self.cache["fan"] = self.get_fan_speed()
        self.cache["temp"] = self.get_cpu_temp()
        try:
            self.cache["top_procs"] = self._top_processes(5)
        except Exception:
            self.cache["top_procs"] = []
        self._last_data_update = now

    def _net_loop(self):
        while True:
            try:
                now = time.time()
                if now - self.last_weather_time >= WEATHER_REFRESH:
                    self.update_weather_cache()
                if now - self.last_jira_time >= JIRA_REFRESH:
                    self.update_jira_cache()
            except Exception:
                pass
            time.sleep(5)

    def _data_loop(self):
        while True:
            try:
                self.update_data_cache()
            except Exception:
                pass
            time.sleep(DATA_REFRESH)

    def start(self):
        if self._started:
            return
        self._started = True
        self.update_data_cache()
        threading.Thread(target=self._net_loop, name="monitor-net", daemon=True).start()
        threading.Thread(target=self._data_loop, name="monitor-data", daemon=True).start()

    def clear_scroll(self):
        with self._lock:
            self.scroll_text_active = ""

    def snapshot(self) -> dict:
        with self._lock:
            weather = dict(self.weather)
            jira = dict(self.jira)
            scroll = self.scroll_text_active
        conf = dict(self.cache.get("conf") or {})
        return {
            "power": conf.get("power", "on"),
            "scrolling_text": scroll,
            "city": self.city,
            "timezone": TIMEZONE,
            "system": {
                "cpu": int(self.cache.get("cpu") or 0),
                "ram": int(self.cache.get("ram") or 0),
                "ram_used_mb": int(self.cache.get("ram_used_mb") or 0),
                "ram_total_mb": int(self.cache.get("ram_total_mb") or 0),
                "swap": int(self.cache.get("swap") or 0),
                "swap_used_mb": int(self.cache.get("swap_used_mb") or 0),
                "swap_total_mb": int(self.cache.get("swap_total_mb") or 0),
                "disk": int(self.cache.get("disk") or 0),
                "disk_used_gb": float(self.cache.get("disk_used_gb") or 0),
                "disk_total_gb": float(self.cache.get("disk_total_gb") or 0),
                "load_1": float(self.cache.get("load_1") or 0),
                "load_5": float(self.cache.get("load_5") or 0),
                "load_15": float(self.cache.get("load_15") or 0),
                "uptime_sec": int(self.cache.get("uptime_sec") or 0),
                "fan": int(self.cache.get("fan") or 0),
                "temp": round(float(self.cache.get("temp") or 0), 1),
                "ip": self.cache.get("ip") or "127.0.0.1",
                "top_procs": list(self.cache.get("top_procs") or []),
            },
            "weather": weather,
            "jira": jira,
            "music": music_bridge.snapshot(),
        }
