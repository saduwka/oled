"""Shared system / weather / Jira cache for the phone web monitor."""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests
from dotenv import load_dotenv

from jira_client import JiraClient
from music_bridge import music_bridge
from system_stats import OledConfigCache, get_ip, system_snapshot

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

OWM_API_KEY = os.getenv("OWM_API_KEY", "")
CITY = os.getenv("CITY", "Astana")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")
JIRA_REFRESH = int(os.getenv("JIRA_REFRESH", 300))
WEATHER_REFRESH = int(os.getenv("WEATHER_REFRESH", 600))
IP_REFRESH = 60
DATA_REFRESH = 5

_PLACEHOLDER_OWM_KEYS = {"YOUR_OPENWEATHERMAP_KEY", "your_api_key_here", ""}

EMPTY_WEATHER = {"main": "Wait", "temp": "0", "hum": "0", "wind": "0", "desc": "loading..."}
EMPTY_JIRA = {
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


class MonitorData:
    def __init__(self) -> None:
        self.city = CITY
        self.weather: dict[str, Any] = dict(EMPTY_WEATHER)
        self.last_weather_time = 0.0
        self.jira: dict[str, Any] = dict(EMPTY_JIRA)
        self.last_jira_time = 0.0
        self.jira_client = JiraClient()

        self._oled_config = OledConfigCache()
        self.scroll_text_active = ""
        self.cache: dict[str, Any] = {"ip": "127.0.0.1"}
        self._last_ip_time = 0.0
        self._last_data_update = 0.0
        self._lock = threading.Lock()
        self._started = False

    # -- Jira -----------------------------------------------------------
    def update_jira_cache(self) -> None:
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

    # -- Weather ----------------------------------------------------------
    def update_weather_cache(self) -> None:
        if not OWM_API_KEY or OWM_API_KEY in _PLACEHOLDER_OWM_KEYS:
            self.last_weather_time = time.time()
            return
        try:
            r = requests.get(
                "http://api.openweathermap.org/data/2.5/weather",
                params={"q": CITY, "appid": OWM_API_KEY, "units": "metric"},
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

    # -- System / oled_config ----------------------------------------------
    def update_data_cache(self) -> None:
        now = time.time()
        if now - self._last_ip_time >= IP_REFRESH or self.cache.get("ip") == "127.0.0.1":
            self.cache["ip"] = get_ip()
            self._last_ip_time = now

        conf = self._oled_config.get()
        self.cache["conf"] = conf
        scroll_txt = (conf.get("scrolling_text") or "").strip()
        if scroll_txt and not self.scroll_text_active:
            self.scroll_text_active = scroll_txt
            self._oled_config.clear_scrolling_text()

        self.cache.update(system_snapshot(include_top_procs=True, top_procs_limit=5))
        self._last_data_update = now

    # -- Background loops ---------------------------------------------------
    def _net_loop(self) -> None:
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

    def _data_loop(self) -> None:
        while True:
            try:
                self.update_data_cache()
            except Exception:
                pass
            time.sleep(DATA_REFRESH)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.update_data_cache()
        threading.Thread(target=self._net_loop, name="monitor-net", daemon=True).start()
        threading.Thread(target=self._data_loop, name="monitor-data", daemon=True).start()

    def clear_scroll(self) -> None:
        with self._lock:
            self.scroll_text_active = ""

    # -- Public snapshot -------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            weather = dict(self.weather)
            jira = dict(self.jira)
            scroll = self.scroll_text_active
        conf = dict(self.cache.get("conf") or {})
        c = self.cache
        return {
            "power": conf.get("power", "on"),
            "scrolling_text": scroll,
            "city": self.city,
            "timezone": TIMEZONE,
            "system": {
                "cpu": int(c.get("cpu") or 0),
                "ram": int(c.get("ram") or 0),
                "ram_used_mb": int(c.get("ram_used_mb") or 0),
                "ram_total_mb": int(c.get("ram_total_mb") or 0),
                "swap": int(c.get("swap") or 0),
                "swap_used_mb": int(c.get("swap_used_mb") or 0),
                "swap_total_mb": int(c.get("swap_total_mb") or 0),
                "disk": int(c.get("disk") or 0),
                "disk_used_gb": float(c.get("disk_used_gb") or 0),
                "disk_total_gb": float(c.get("disk_total_gb") or 0),
                "load_1": float(c.get("load_1") or 0),
                "load_5": float(c.get("load_5") or 0),
                "load_15": float(c.get("load_15") or 0),
                "uptime_sec": int(c.get("uptime_sec") or 0),
                "fan": int(c.get("fan") or 0),
                "temp": round(float(c.get("temp") or 0), 1),
                "ip": c.get("ip") or "127.0.0.1",
                "top_procs": list(c.get("top_procs") or []),
            },
            "weather": weather,
            "jira": jira,
            "music": music_bridge.snapshot(),
        }
