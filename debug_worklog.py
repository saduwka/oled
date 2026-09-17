"""Диагностика: построчная разбивка worklog за сегодня/неделю по задачам.

Запуск на Raspberry Pi (там, где лежит .env):

    cd /root/oled
    python3 debug_worklog.py

Печатает для каждой задачи, где currentUser залогировал время за последний
месяц: дату каждой записи (после конвертации в TIMEZONE из .env), сколько
секунд, и в какие бакеты (day/week/month) она попадает. Так видно, какая
именно запись даёт лишние 7h30m относительно отчёта в самой Jira.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

from jira_client import JiraClient, format_hours, load_jira_env


def main() -> None:
    cfg = load_jira_env()
    client = JiraClient(cfg)
    if not client.configured():
        print("Jira не настроена (.env) - проверь JIRA_BASE_URL/EMAIL/API_TOKEN")
        return

    account_id = client._myself_account_id()
    print(f"account_id = {account_id}")

    now = datetime.now(client.tz)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"now={now}  day_start={day_start.date()}  week_start={week_start.date()}  month_start={month_start.date()}")

    month_str = month_start.strftime("%Y-%m-%d")
    issue_keys = client._search_keys(
        f'worklogAuthor = currentUser() AND worklogDate >= "{month_str}"'
    )
    print(f"\nЗадачи с worklog за месяц (issue_keys), всего {len(issue_keys)}: {issue_keys}")

    day_total = 0
    week_total = 0
    month_total = 0
    rows = []

    for key in issue_keys:
        start_at = 0
        while True:
            r = requests.get(
                f"{client.base_url}/rest/api/3/issue/{key}/worklog",
                auth=client.auth,
                headers=client.headers,
                params={"startAt": start_at, "maxResults": 100},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            for wl in data.get("worklogs", []):
                author = (wl.get("author") or {}).get("accountId")
                if author != account_id:
                    continue
                started = wl.get("started", "")
                try:
                    started_norm = started.replace("Z", "+00:00")
                    if len(started_norm) >= 5 and started_norm[-5] in "+-" and started_norm[-3] != ":":
                        started_norm = f"{started_norm[:-2]}:{started_norm[-2:]}"
                    wl_dt = datetime.fromisoformat(started_norm)
                    if wl_dt.tzinfo is None:
                        wl_dt = client.tz.localize(wl_dt)
                    wl_local = wl_dt.astimezone(client.tz)
                except Exception as exc:
                    print(f"  !! {key}: failed to parse started={started!r}: {exc}")
                    continue

                seconds = int(wl.get("timeSpentSeconds") or 0)
                wl_date = wl_local.date()
                in_day = day_start and wl_date >= day_start.date()
                in_week = wl_date >= week_start.date()
                in_month = wl_date >= month_start.date()

                if in_month:
                    month_total += seconds
                    if in_week:
                        week_total += seconds
                    if in_day:
                        day_total += seconds

                rows.append(
                    (key, wl.get("id"), wl_local.isoformat(), started, seconds,
                     in_day, in_week, in_month)
                )

            start_at += len(data.get("worklogs", []))
            if start_at >= int(data.get("total", 0)) or not data.get("worklogs"):
                break

    print(f"\n{'issue':<10} {'worklogId':<10} {'local_started':<32} {'raw_started':<30} {'sec':>6} {'H:MM':>7}  day week month")
    for key, wl_id, local_iso, raw, seconds, in_day, in_week, in_month in sorted(rows, key=lambda x: x[2]):
        hm = f"{seconds // 3600}:{(seconds % 3600) // 60:02d}"
        print(
            f"{key:<10} {str(wl_id):<10} {local_iso:<32} {raw:<30} {seconds:>6} {hm:>7}  "
            f"{'D' if in_day else ' '}    {'W' if in_week else ' '}    {'M' if in_month else ' '}"
        )

    print(f"\nTOTAL day={format_hours(day_total)} ({day_total}s)  week={format_hours(week_total)} ({week_total}s)  month={format_hours(month_total)} ({month_total}s)")


if __name__ == "__main__":
    main()
