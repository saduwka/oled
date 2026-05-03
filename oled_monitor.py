import time
import os
import psutil
import requests
import sqlite3
import pytz
import math
import random
import subprocess
from datetime import datetime
from dotenv import load_dotenv
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import ImageFont, ImageDraw

# Загружаем настройки
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

OWM_API_KEY = os.getenv("OWM_API_KEY", "")
CITY = os.getenv("CITY", "Astana")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")
DB_PATH = os.getenv("DB_PATH", "/root/bot/bot.db")
UPDATE_INTERVAL = 5 

try:
    FONT_L = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    FONT_M = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    FONT_S = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    FONT_XL = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
except:
    FONT_L = FONT_M = FONT_S = FONT_XL = ImageFont.load_default()

class Utils:
    @staticmethod
    def get_ip():
        try: return subprocess.check_output("hostname -I | cut -d' ' -f1", shell=True).decode("utf-8").strip()
        except: return "127.0.0.1"
    @staticmethod
    def is_bot_running():
        try:
            subprocess.check_output("pgrep -f bot.py", shell=True)
            return True
        except: return False
    @staticmethod
    def get_uptime():
        try:
            with open('/proc/uptime', 'r') as f:
                s = float(f.readline().split()[0])
                return f"{int(s//3600)}h {int((s%3600)//60)}m"
        except: return "0h 0m"

class Config:
    @staticmethod
    def get_oled_mode():
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            res = conn.execute("SELECT key, value FROM oled_config").fetchall()
            conn.close()
            return dict(res)
        except: return {"power": "on", "forced_screen": "-1", "scrolling_text": ""}

class BotData:
    @staticmethod
    def get_stats():
        d = {"pending": "0", "posted": "0", "watches": "0", "btc": "---"}
        try:
            if os.path.exists(DB_PATH):
                conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM pending"); d["pending"] = str(c.fetchone()[0])
                c.execute("SELECT COUNT(*) FROM posted"); d["posted"] = str(c.fetchone()[0])
                c.execute("SELECT COUNT(*) FROM watches"); d["watches"] = str(c.fetchone()[0])
                conn.close()
            res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=1)
            d["btc"] = f"{float(res.json()['price']):,.0f}"
        except: pass
        return d
    @staticmethod
    def get_last_trade():
        trade = {"pair": "NONE", "side": "-", "pnl": "0.0", "price": "0"}
        try:
            if os.path.exists(DB_PATH):
                conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
                c = conn.cursor()
                c.execute("SELECT pair, side, pnl, price FROM trades ORDER BY id DESC LIMIT 1")
                res = c.fetchone()
                if res: trade = {"pair": res[0], "side": res[1], "pnl": f"{res[2]:+.2f}", "price": f"{res[3]:.2f}"}
                conn.close()
        except: pass
        return trade

class OledMonitor:
    def __init__(self):
        try: self.device = ssd1306(i2c(port=1, address=0x3C))
        except: exit(1)
        self.device.clear()
        self.tz = pytz.timezone(TIMEZONE)
        self.weather = {
            "main": "Wait",
            "temp": "0",
            "hum": "0",
            "wind": "0",
            "desc": "loading..."
        }
        self.last_weather_time = 0
        
        # Состояние Дино
        self.dino_x = 100
        self.dino_direction = 1 # 1 вправо, -1 влево
        self.dino_frame = 0
        
        self.cache = {
            "stats": {"pending": "0", "posted": "0", "watches": "0", "btc": "---"},
            "trade": {"pair": "NONE", "side": "-", "pnl": "0.0", "price": "0"},
            "ip": "127.0.0.1", "bot_alive": False, "conf": {"power": "on", "forced_screen": "-1", "scrolling_text": ""},
            "cpu": 0, "ram": 0
        }
        self.scroll_x = 128
        self.is_scrolling = False
        self.scroll_text_active = ""
        self.saved_screen = None

    def draw_weather_icon(self, draw, x, y, condition, is_night=False):
        # Иконки 20x20 для лучшей детализации
        condition = condition.lower()
        if "clear" in condition:
            if is_night:
                # Луна
                draw.ellipse((x+4, y+2, x+16, y+14), outline="white", fill="white")
                draw.ellipse((x+8, y+0, x+20, y+12), fill="black")
            else:
                # Солнце
                draw.ellipse((x+5, y+5, x+15, y+15), outline="white", fill="white")
                for i in range(8):
                    angle = i * 45
                    rad = math.radians(angle)
                    x1 = x + 10 + math.cos(rad) * 6
                    y1 = y + 10 + math.sin(rad) * 6
                    x2 = x + 10 + math.cos(rad) * 10
                    y2 = y + 10 + math.sin(rad) * 10
                    draw.line((x1, y1, x2, y2), fill="white")
        elif "cloud" in condition:
            # Облако
            draw.ellipse((x+2, y+10, x+10, y+18), fill="white")
            draw.ellipse((x+6, y+6, x+16, y+18), fill="white")
            draw.ellipse((x+12, y+10, x+19, y+18), fill="white")
            if is_night:
                draw.ellipse((x+12, y+2, x+18, y+8), outline="white", fill="white")
                draw.ellipse((x+14, y+1, x+20, y+7), fill="black")
        elif "rain" in condition or "drizzle" in condition:
            # Дождь
            draw.ellipse((x+4, y+6, x+16, y+14), fill="white")
            for i in range(3):
                dx = i * 5
                draw.line((x+6+dx, y+15, x+4+dx, y+19), fill="white")
        elif "snow" in condition:
            # Снежинка
            for i in range(3):
                angle = i * 60
                rad = math.radians(angle)
                x1 = x + 10 - math.cos(rad) * 8
                y1 = y + 10 - math.sin(rad) * 8
                x2 = x + 10 + math.cos(rad) * 8
                y2 = y + 10 + math.sin(rad) * 8
                draw.line((x1, y1, x2, y2), fill="white")
        else:
            # Туман
            for i in range(4):
                draw.line((x+2, y+6+i*4, x+18, y+6+i*4), fill="white")

    def draw_scrolling_text(self, draw, text):
        font = FONT_XL # Используем ОГРОМНЫЙ шрифт
        text_w = draw.textlength(text, font=font)
        # Центрируем (64 - 40) // 2 = 12
        draw.text((self.scroll_x, 12), text, font=font, fill="white")
        self.scroll_x -= 1 # Самый плавный шаг - 1 пиксель
        if self.scroll_x < -text_w:
            self.scroll_x = 128
            self.is_scrolling = False
            self.scroll_text_active = ""
            self.saved_screen = None
            # Очищаем БД только один раз при завершении
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE oled_config SET value='' WHERE key='scrolling_text'")
            conn.commit()
            conn.close()
            # Обновляем локальный конфиг, чтобы не зацикливалось
            if "conf" in self.cache:
                self.cache["conf"]["scrolling_text"] = ""

    def draw_dino(self, draw):
        """Анимация динозавра Chrome в стиле оригинала"""
        x = int(self.dino_x)
        y = 1
        
        # Логика движения
        self.dino_x += self.dino_direction * 0.5
        self.dino_frame = (self.dino_frame + 1) % 4
        
        if self.dino_x > 112: self.dino_direction = -1
        if self.dino_x < 85: self.dino_direction = 1

        # Битмап Дино (14x14)
        dino_base = [
            "      XXXXXXXX",
            "      XX XXXXX",
            "      XXXXXXXX",
            "      XXXX    ",
            "      XXXXXXXX",
            "X    XXXXXXXX ",
            "XX  XXXXXXXX  ",
            "XXXXXXXXXXXX  ",
            " XXXXXXXXXX   ",
            "  XXXXXXXX    ",
            "   XXXXXX     "
        ]
        
        # Анимация ног
        if self.dino_frame == 1:
            legs = ["    X  XX     ", "    XX        "]
        elif self.dino_frame == 3:
            legs = ["   XX  X      ", "       XX     "]
        else:
            legs = ["   XX  XX     ", "   XX  XX     "]
            
        dino_full = dino_base + legs

        for row_y, row_str in enumerate(dino_full):
            for col_x, char in enumerate(row_str):
                if char == "X":
                    # Отражаем по горизонтали, если идет влево
                    if self.dino_direction == 1:
                        px = x + col_x
                    else:
                        px = x + (13 - col_x)
                    draw.point((px, y + row_y), fill="white")

    def draw_header(self, draw, title):
        draw.text((0, 0), title, font=FONT_S, fill="white")
        self.draw_dino(draw)

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
        self.draw_header(draw, "СИСТЕМА")
        draw.text((0, 16), f"ЦП: {self.cache['cpu']}%", font=FONT_S, fill="white")
        draw.rectangle((60, 18, 120, 24), outline="white")
        draw.rectangle((61, 19, 61 + int(self.cache['cpu']*58/100), 23), fill="white")
        draw.text((0, 30), f"ОЗУ: {self.cache['ram']}%", font=FONT_S, fill="white")
        draw.rectangle((60, 32, 120, 38), outline="white")
        draw.rectangle((61, 33, 61 + int(self.cache['ram']*58/100), 37), fill="white")
        draw.text((0, 48), f"IP: {self.cache['ip']}", font=FONT_S, fill="white")

    def screen_weather(self, draw):
        dt = datetime.now(self.tz)
        is_night = dt.hour < 6 or dt.hour > 21
        
        # Хедер
        days = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
        self.draw_header(draw, f"{days[dt.weekday()]} {dt.strftime('%d.%m')} {CITY}")
        
        # Разделяем экран: Слева время, Справа погода
        # Время (используем L для компактности)
        draw.text((0, 18), dt.strftime("%H:%M"), font=FONT_L, fill="white")
        draw.text((55, 24), dt.strftime(":%S"), font=FONT_S, fill="white")
        
        # Разделительная линия
        draw.line((72, 18, 72, 50), fill="white")
        
        # Погода справа
        w = self.weather
        self.draw_weather_icon(draw, 88, 16, w['main'], is_night)
        draw.text((82, 40), f"{w['temp']}°C", font=FONT_M, fill="white")
        
        # Влажность и ветер в самом низу
        draw.text((5, 52), f"Вл:{w['hum']}% Ветер:{w['wind']}м/с", font=FONT_S, fill="white")

    def screen_bot(self, draw):
        self.draw_header(draw, "БОТ ДАШБОРД")
        d, alive = self.cache["stats"], self.cache["bot_alive"]
        draw.text((0, 18), "[ РАБОТАЕТ ]" if alive else "[ ОСТАНОВЛЕН ]", font=FONT_S, fill="white")
        draw.text((0, 32), f"Очередь: {d['pending']}", font=FONT_S, fill="white")
        draw.text((0, 42), f"Постов : {d['posted']}", font=FONT_S, fill="white")
        draw.text((0, 52), f"BTC  : ${d['btc']}", font=FONT_S, fill="white")

    def screen_trader(self, draw):
        self.draw_header(draw, "ТРЕЙДЕР LIVE")
        t = self.cache["trade"]
        draw.text((0, 18), f"{t['pair']}", font=FONT_M, fill="white")
        draw.text((95, 18), f"{t['side']}", font=FONT_S, fill="white")
        draw.line((0, 34, 128, 34), fill="white")
        draw.text((0, 38), f"PnL: {t['pnl']}%", font=FONT_S, fill="white")
        draw.text((0, 50), f"Цена: {t['price']}", font=FONT_S, fill="white")

    def update_data_cache(self):
        self.cache["stats"] = BotData.get_stats()
        self.cache["trade"] = BotData.get_last_trade()
        self.cache["ip"] = Utils.get_ip()
        self.cache["bot_alive"] = Utils.is_bot_running()
        self.cache["conf"] = Config.get_oled_mode()
        self.cache["cpu"] = psutil.cpu_percent()
        self.cache["ram"] = psutil.virtual_memory().percent
        if (time.time() - self.last_weather_time > 600) and OWM_API_KEY != "YOUR_OPENWEATHERMAP_KEY":
            try:
                r = requests.get(f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OWM_API_KEY}&units=metric", timeout=1).json()
                if 'weather' in r:
                    self.weather = {
                        "main": r['weather'][0]['main'],
                        "temp": f"{r['main']['temp']:.0f}",
                        "hum": f"{r['main']['humidity']}",
                        "wind": f"{r['wind']['speed']:.1f}",
                        "desc": r['weather'][0]['description']
                    }
                    self.last_weather_time = time.time()
            except: pass

    def start(self):
        self.draw_welcome()
        screens = [self.screen_system, self.screen_weather, self.screen_bot, self.screen_trader]
        idx, last_data_update, last_screen_switch = 0, 0, time.time()
        while True:
            try:
                now = time.time()
                if now - last_data_update >= 5:
                    self.update_data_cache()
                    last_data_update = now
                
                conf = self.cache.get("conf", {})
                if conf.get("power") == "off":
                    self.device.hide()
                    time.sleep(1)
                    continue
                else:
                    self.device.show()

                forced = int(conf.get("forced_screen", -1))
                scroll_txt = conf.get("scrolling_text", "")
                
                # Логика определения текущего экрана
                if forced != -1:
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
                    # Очищаем БД
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute("UPDATE oled_config SET value='' WHERE key='scrolling_text'")
                    conn.commit()
                    conn.close()
                    self.cache["conf"]["scrolling_text"] = ""
                
                if self.is_scrolling:
                    with canvas(self.device) as draw:
                        self.draw_scrolling_text(draw, self.scroll_text_active)
                    # Убираем sleep или делаем минимальным для плавности при шаге 1px
                    time.sleep(0.001) 
                else:
                    with canvas(self.device) as draw:
                        screens[current_idx](draw)
                    time.sleep(0.1)
            except Exception as e:
                # print(f"Error: {e}") # Debugging
                time.sleep(1)

if __name__ == "__main__":
    OledMonitor().start()
