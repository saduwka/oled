# Phone Dashboard for SADU OS

Статус-монитор системы и бота. **Единственный вывод — веб-дашборд на телефоне** (Redmi Note 12 и т.п.). Физический OLED SSD1306 отключён: юнит `oled_monitor.service` замаскирован (`systemctl mask`).

## Возможности

- **System:** CPU, RAM, FAN, температура, IP.
- **Часы и погода:** HH:MM, дата/город, OpenWeatherMap.
- **Jira:** спринт, TODO/WIP списки задач, часы за неделю и месяц.
- **Яндекс.Музыка:** now-playing (обложка, прогресс, pause/next/stop / Моя волна) через mpv на Pi.
- **Бегущая строка:** `scrolling_text` из `oled_config` в БД.
- **Power:** `power=off` в БД гасит экран на телефоне.
- **Landscape:** дашборд под горизонтальный экран.
- **Keep-awake:** Screen Wake Lock + silent video fallback; лёгкий pixel-shift против выгорания AMOLED.
- **Live refresh:** опрос `GET /api/status` каждую секунду.

## Требования

- **Python 3.7+**
- **Библиотеки:** `psutil`, `requests`, `pytz`, `python-dotenv` (+ `yandex-music` для обложек/волны через gamebot).

## Установка

```bash
cd /root/oled
pip install psutil requests pytz python-dotenv
cp .env.example .env   # заполните ключи
```

## Настройка .env

См. `.env.example`. Важно для телефона:

- `WEB_HOST` / `WEB_PORT` — по умолчанию `0.0.0.0:8080`
- Остальные ключи (OWM, Jira, `DB_PATH`) как раньше

## Запуск

```bash
sudo systemctl enable --now phone_monitor.service
# или вручную:
python3 web_monitor.py
```

На телефоне (та же Wi‑Fi, что и Pi):

```
http://<IP-малины>:8080
```

Пример: `http://192.168.10.30:8080`

### Киоск на Redmi Note 12

1. Подключите телефон к той же Wi‑Fi, что и Raspberry Pi.
2. Поверните телефон **горизонтально** и зафиксируйте ориентацию.
3. Chrome → «Добавить на главный экран»; один раз тапните по экрану (активирует keep-awake).
4. Дисплей → Сон → «Никогда» / макс.; Батарея → браузер → без ограничений.
5. Опционально: Fully Kiosk Browser.
6. Держите телефон на зарядке.

### API

- `GET /api/status` — JSON: `power`, `scrolling_text`, `city`, `timezone`, `system`, `weather`, `jira`, `music`
- `POST /api/music` — `{"action":"pause|next|stop|wave"}`
- `GET /api/music/cover/<track_id>` — прокси обложки

## Физический OLED (выключен)

Юнит `oled_monitor.service` **замаскирован**. Связанные `oled-boot-loader` / `oled-console-*` disabled.

Код [`oled_monitor.py`](oled_monitor.py) оставлен (Telegram OLED-меню). Чтобы снова включить железо:

```bash
sudo systemctl unmask oled_monitor.service
# restore unit from oled_monitor.service.disabled if needed
sudo systemctl enable --now oled_monitor.service
```
