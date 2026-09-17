import math
import os
import random
import sqlite3
import subprocess
import threading
import time
from datetime import datetime

import psutil
import pytz
import requests
from dotenv import load_dotenv
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import ImageFont

from jira_client import JiraClient

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

OWM_API_KEY = os.getenv("OWM_API_KEY", "")
CITY = os.getenv("CITY", "Astana")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")
DB_PATH = os.getenv("DB_PATH", "/root/bot/bot.db")
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", 5))
JIRA_REFRESH = int(os.getenv("JIRA_REFRESH", 300))
WEATHER_REFRESH = int(os.getenv("WEATHER_REFRESH", 600))

FRAME_SLEEP = 0.2
SCROLL_SLEEP = 0.04
SCROLL_STEP = 3
IP_REFRESH = 60
DATA_REFRESH = 5

try:
    FONT_L = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    FONT_M = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    FONT_S = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    FONT_XL = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
except Exception:
    FONT_L = FONT_M = FONT_S = FONT_XL = ImageFont.load_default()


class Utils:
    @staticmethod
    def get_ip():
        try:
            return subprocess.check_output(
                "hostname -I | cut -d' ' -f1", shell=True
            ).decode("utf-8").strip()
        except Exception:
            return "127.0.0.1"


class Config:
    @staticmethod
    def get_oled_mode():
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            res = conn.execute("SELECT key, value FROM oled_config").fetchall()
            conn.close()
            return dict(res)
        except Exception:
            return {"power": "on", "forced_screen": "-1", "scrolling_text": ""}


class OledMonitor:
    def __init__(self):
        try:
            self.device = ssd1306(
                i2c(
                    port=int(os.getenv("OLED_I2C_PORT", 1)),
                    address=int(os.getenv("OLED_I2C_ADDRESS", "0x3C"), 16),
                )
            )
        except Exception:
            raise SystemExit(1)
        self.device.clear()
        self.tz = pytz.timezone(TIMEZONE)
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
            "ok": False,
        }
        self.last_jira_time = 0
        self.jira_client = JiraClient()
        self.scroll_x = 128
        self.is_scrolling = False
        self.scroll_text_active = ""
        self.saved_screen = None
        self._display_on = True
        self._last_frame_key = None
        self._db_mtime = None
        self._last_ip_time = 0
        self._lock = threading.Lock()

    def get_cpu_temp(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.read()) / 1000.0
        except Exception:
            return 0.0

    def get_fan_speed(self):
        try:
            with open("/sys/class/thermal/cooling_device0/cur_state", "r") as f:
                state = int(f.read().strip())
            state_map = {0: 0, 1: 50, 2: 75, 3: 100, 4: 100}
            return state_map.get(state, 0)
        except Exception:
            return 0

    def _load_oled_config(self):
        try:
            mtime = os.path.getmtime(DB_PATH)
        except OSError:
            return self.cache.get("conf") or {"power": "on", "forced_screen": "-1", "scrolling_text": ""}
        if self._db_mtime == mtime and self.cache.get("conf"):
            return self.cache["conf"]
        conf = Config.get_oled_mode()
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

    def draw_cpu_icon(self, draw, x, y, usage):
        draw.rectangle((x, y, x + 14, y + 14), outline="white")
        for i in range(3):
            draw.line((x + 3 + i * 4, y - 2, x + 3 + i * 4, y), fill="white")
            draw.line((x + 3 + i * 4, y + 14, x + 3 + i * 4, y + 16), fill="white")
            draw.line((x - 2, y + 3 + i * 4, x, y + 3 + i * 4), fill="white")
            draw.line((x + 14, y + 3 + i * 4, x + 16, y + 3 + i * 4), fill="white")
        if usage > 5:
            dots = 2 + int(usage / 20)
            for _ in range(dots):
                dx = random.randint(2, 12)
                dy = random.randint(2, 12)
                draw.point((x + dx, y + dy), fill="white")

    def draw_ram_icon(self, draw, x, y):
        draw.rectangle((x, y + 2, x + 16, y + 10), outline="white")
        for i in range(4):
            draw.line((x + 2 + i * 4, y + 10, x + 2 + i * 4, y + 12), fill="white")

    def _fan_angle(self, speed_pct):
        if speed_pct > 0:
            anim_speed = (speed_pct / 25) ** 2
            return (time.time() * anim_speed * 40) % 360
        return 0

    def draw_fan_icon(self, draw, x, y, speed_pct):
        cx, cy = x + 8, y + 8
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill="white")
        angle_offset = self._fan_angle(speed_pct)
        for i in range(3):
            angle = math.radians(i * 120 + angle_offset)
            x2 = cx + math.cos(angle) * 7
            y2 = cy + math.sin(angle) * 7
            draw.line((cx, cy, x2, y2), fill="white", width=2)

    def draw_weather_icon(self, draw, x, y, condition, is_night=False):
        condition = condition.lower()
        if "clear" in condition:
            if is_night:
                draw.ellipse((x + 4, y + 2, x + 16, y + 14), outline="white", fill="white")
                draw.ellipse((x + 8, y + 0, x + 20, y + 12), fill="black")
            else:
                draw.ellipse((x + 5, y + 5, x + 15, y + 15), outline="white", fill="white")
                for i in range(8):
                    rad = math.radians(i * 45)
                    x1 = x + 10 + math.cos(rad) * 6
                    y1 = y + 10 + math.sin(rad) * 6
                    x2 = x + 10 + math.cos(rad) * 10
                    y2 = y + 10 + math.sin(rad) * 10
                    draw.line((x1, y1, x2, y2), fill="white")
        elif "cloud" in condition:
            draw.ellipse((x + 2, y + 10, x + 10, y + 18), fill="white")
            draw.ellipse((x + 6, y + 6, x + 16, y + 18), fill="white")
            draw.ellipse((x + 12, y + 10, x + 19, y + 18), fill="white")
            if is_night:
                draw.ellipse((x + 12, y + 2, x + 18, y + 8), outline="white", fill="white")
                draw.ellipse((x + 14, y + 1, x + 20, y + 7), fill="black")
        elif "rain" in condition or "drizzle" in condition:
            draw.ellipse((x + 4, y + 6, x + 16, y + 14), fill="white")
            for i in range(3):
                dx = i * 5
                draw.line((x + 6 + dx, y + 15, x + 4 + dx, y + 19), fill="white")
        elif "snow" in condition:
            for i in range(3):
                rad = math.radians(i * 60)
                x1 = x + 10 - math.cos(rad) * 8
                y1 = y + 10 - math.sin(rad) * 8
                x2 = x + 10 + math.cos(rad) * 8
                y2 = y + 10 + math.sin(rad) * 8
                draw.line((x1, y1, x2, y2), fill="white")
        else:
            for i in range(4):
                draw.line((x + 2, y + 6 + i * 4, x + 18, y + 6 + i * 4), fill="white")

    def _text_width(self, text, font):
        try:
            return font.getlength(text)
        except Exception:
            try:
                box = font.getbbox(text)
                return box[2] - box[0]
            except Exception:
                return len(text) * 24

    def step_scroll(self, text):
        self.scroll_x -= SCROLL_STEP
        text_w = self._text_width(text, FONT_XL)
        if self.scroll_x < -text_w:
            self.scroll_x = 128
            self.is_scrolling = False
            self.scroll_text_active = ""
            self.saved_screen = None
            self._clear_scrolling_text()

    def draw_scrolling_text(self, draw, text):
        draw.text((self.scroll_x, 12), text, font=FONT_XL, fill="white")

    def draw_header(self, draw, title):
        draw.text((0, 0), title, font=FONT_S, fill="white")

    def draw_welcome(self):
        self.device.clear()
        for i in range(0, 101, 10):
            with canvas(self.device) as draw:
                draw.text((30, 0), "SADU OS v3.2", font=FONT_S, fill="white")
                draw.rectangle((10, 30, 118, 40), outline="white")
                fill_width = 12 + (i * 104 // 100)
                draw.rectangle((12, 32, fill_width, 38), fill="white")
                draw.text((42, 45), "ЗАГРУЗКА...", font=FONT_S, fill="white")
            time.sleep(0.08)
        time.sleep(1)

    def screen_system(self, draw):
        self.draw_header(draw, "SYSTEM STATUS")
        self.draw_cpu_icon(draw, 2, 14, self.cache["cpu"])
        draw.text((22, 13), f"CPU: {self.cache['cpu']}%", font=FONT_S, fill="white")
        draw.rectangle((22, 23, 87, 27), outline="white")
        draw.rectangle((23, 24, 23 + int(self.cache["cpu"] * 63 / 100), 26), fill="white")
        self.draw_ram_icon(draw, 2, 34)
        used = int(self.cache.get("ram_used_mb") or 0)
        total = int(self.cache.get("ram_total_mb") or 0)
        pct = int(self.cache.get("ram") or 0)
        draw.text((22, 33), f"{used}/{total}MB {pct}%", font=FONT_S, fill="white")
        draw.rectangle((22, 43, 87, 47), outline="white")
        draw.rectangle((23, 44, 23 + int(pct * 63 / 100), 46), fill="white")
        self.draw_fan_icon(draw, 105, 12, self.cache["fan"])
        draw.text((100, 28), "FAN", font=FONT_S, fill="white")
        draw.text((100, 37), f"{self.cache['fan']}%", font=FONT_S, fill="white")
        draw.text((95, 46), f"{self.cache['temp']:.1f}C", font=FONT_S, fill="white")
        draw.text((0, 55), f"IP: {self.cache['ip']}", font=FONT_S, fill="white")

    def screen_weather(self, draw):
        dt = datetime.now(self.tz)
        is_night = dt.hour < 6 or dt.hour > 21
        days = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
        self.draw_header(draw, f"{days[dt.weekday()]} {dt.strftime('%d.%m')} {CITY}")
        draw.text((0, 18), dt.strftime("%H:%M"), font=FONT_L, fill="white")
        draw.text((55, 24), dt.strftime(":%S"), font=FONT_S, fill="white")
        draw.line((72, 18, 72, 50), fill="white")
        with self._lock:
            w = dict(self.weather)
        self.draw_weather_icon(draw, 88, 16, w["main"], is_night)
        draw.text((82, 40), f"{w['temp']}°C", font=FONT_M, fill="white")
        draw.text((5, 52), f"Вл:{w['hum']}% Ветер:{w['wind']}м/с", font=FONT_S, fill="white")

    def screen_jira(self, draw):
        with self._lock:
            j = dict(self.jira)
        title = "ENG" if j.get("ok") else "ENG ERR"
        self.draw_header(draw, title)
        draw.text((0, 18), f"TODO  {j['todo']}", font=FONT_M, fill="white")
        draw.text((70, 18), f"WIP  {j['in_progress']}", font=FONT_M, fill="white")
        draw.text((0, 36), f"WEEK   {j['week_h']}", font=FONT_M, fill="white")
        draw.text((0, 50), f"MONTH  {j['month_h']}", font=FONT_M, fill="white")

    def update_jira_cache(self):
        stats = self.jira_client.fetch_stats()
        with self._lock:
            self.jira = {
                "todo": stats.get("todo", "--"),
                "in_progress": stats.get("in_progress", "--"),
                "week_h": stats.get("week_h", "--"),
                "month_h": stats.get("month_h", "--"),
                "ok": bool(stats.get("ok")),
            }
            self.last_jira_time = time.time()

    def update_weather_cache(self):
        if not OWM_API_KEY or OWM_API_KEY == "YOUR_OPENWEATHERMAP_KEY":
            self.last_weather_time = time.time()
            return
        try:
            r = requests.get(
                f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OWM_API_KEY}&units=metric",
                timeout=1,
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

    def update_data_cache(self):
        now = time.time()
        if now - self._last_ip_time >= IP_REFRESH or self.cache["ip"] == "127.0.0.1":
            self.cache["ip"] = Utils.get_ip()
            self._last_ip_time = now
        self.cache["conf"] = self._load_oled_config()
        self.cache["cpu"] = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        self.cache["ram"] = ram.percent
        self.cache["ram_used_mb"] = (ram.total - ram.available) // (1024 * 1024)
        self.cache["ram_total_mb"] = ram.total // (1024 * 1024)
        self.cache["fan"] = self.get_fan_speed()
        self.cache["temp"] = self.get_cpu_temp()

    def _frame_key(self, screen_idx):
        dt = datetime.now(self.tz)
        c = self.cache
        with self._lock:
            j = tuple(self.jira.get(k) for k in ("todo", "in_progress", "week_h", "month_h", "ok"))
            w = tuple(self.weather.get(k) for k in ("main", "temp", "hum", "wind"))
        if self.is_scrolling:
            return ("scroll", int(self.scroll_x), self.scroll_text_active)
        clock = dt.strftime("%H:%M:%S") if screen_idx == 1 else dt.strftime("%H:%M")
        return (
            screen_idx,
            clock,
            int(c.get("cpu") or 0),
            int(c.get("ram") or 0),
            int(c.get("ram_used_mb") or 0),
            int(c.get("fan") or 0),
            round(float(c.get("temp") or 0), 1),
            c.get("ip"),
            j,
            w,
        )

    def start(self):
        self.draw_welcome()
        threading.Thread(target=self._net_loop, name="oled-net", daemon=True).start()
        screens = [self.screen_system, self.screen_weather, self.screen_jira]
        idx, last_data_update, last_screen_switch = 0, 0, time.time()
        while True:
            try:
                now = time.time()
                if now - last_data_update >= DATA_REFRESH:
                    self.update_data_cache()
                    last_data_update = now

                conf = self.cache.get("conf", {})
                if conf.get("power") == "off":
                    if self._display_on:
                        self.device.hide()
                        self._display_on = False
                        self._last_frame_key = None
                    time.sleep(1)
                    continue
                if not self._display_on:
                    self.device.show()
                    self._display_on = True
                    self._last_frame_key = None

                forced = int(conf.get("forced_screen", -1))
                scroll_txt = conf.get("scrolling_text", "")

                if forced != -1 and forced < len(screens):
                    current_idx = forced
                else:
                    if now - last_screen_switch >= UPDATE_INTERVAL:
                        idx = (idx + 1) % len(screens)
                        last_screen_switch = now
                    current_idx = idx

                if scroll_txt and not self.is_scrolling:
                    self.is_scrolling = True
                    self.scroll_text_active = scroll_txt
                    self.scroll_x = 128
                    self.saved_screen = current_idx
                    self._clear_scrolling_text()
                    self._last_frame_key = None

                if self.is_scrolling:
                    self.step_scroll(self.scroll_text_active)
                    if self.is_scrolling:
                        key = self._frame_key(current_idx)
                        if key != self._last_frame_key:
                            with canvas(self.device) as draw:
                                self.draw_scrolling_text(draw, self.scroll_text_active)
                            self._last_frame_key = key
                        time.sleep(SCROLL_SLEEP)
                        continue
                    self._last_frame_key = None

                key = self._frame_key(current_idx)
                if key != self._last_frame_key:
                    with canvas(self.device) as draw:
                        screens[current_idx](draw)
                    self._last_frame_key = key
                time.sleep(FRAME_SLEEP)
            except Exception:
                time.sleep(1)


if __name__ == "__main__":
    OledMonitor().start()
