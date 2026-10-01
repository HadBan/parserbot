import os
import re
import json
import subprocess
import asyncio
from datetime import datetime, timedelta, timezone
import aiohttp
from bs4 import BeautifulSoup

GROUP_URL = "http://87.255.240.138/rasp/cg23.htm"
OUT_FILE = "schedule.json"
REPO_DIR = os.environ.get("REPO_DIR", "/app")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
INTERVAL_SEC = int(os.environ.get("INTERVAL_SEC", "3600"))
MSK = timezone(timedelta(hours=3))


async def fetch_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(url) as resp:
            print(f"[SCHED] HTTP {resp.status}")
            if resp.status != 200:
                return None
            return await resp.text()


def extract_para_info(cell, para_num, time_str):
    subject_a = cell.find("a", class_="z1")
    room_a = cell.find("a", class_="z2")
    teacher_a = cell.find("a", class_="z3")
    if not subject_a:
        return None
    return {
        "para": para_num,
        "time": time_str,
        "subject": subject_a.get_text(strip=True),
        "room": room_a.get_text(strip=True) if room_a else "—",
        "teacher": teacher_a.get_text(strip=True) if teacher_a else "—",
    }


def parse_schedule(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="inf")
    if not table:
        print("[SCHED] Таблица не найдена")
        return {}
    days = {}
    current_date = None
    current_day = None
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        if cells[0].get("rowspan") == "7":
            if current_date and current_day:
                days[current_date] = current_day
            text = cells[0].get_text(separator="\n").strip()
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
            current_date = m.group(1) if m else text
            current_day = {1: [], 2: []}
            continue
        if not current_date or len(cells) < 2:
            continue
        para_text = cells[0].get_text(separator=" ").strip()
        if "Пара" not in para_text:
            continue
        m = re.match(r"(\d+)\s*Пара:?\s*(\d{1,2}[.:]\d{2}\s*-\s*\d{1,2}[.:]\d{2})?", para_text)
        if not m:
            continue
        para_num = int(m.group(1))
        time_str = m.group(2) or ""
        data_cells = cells[1:]
        if len(data_cells) == 1 and data_cells[0].get("colspan") == "2":
            cell = data_cells[0]
            if "ur" in (cell.get("class") or []):
                info = extract_para_info(cell, para_num, time_str)
                if info:
                    current_day[1].append(info)
                    current_day[2].append(info)
        elif len(data_cells) == 2:
            left, right = data_cells
            if "ur" in (left.get("class") or []):
                info = extract_para_info(left, para_num, time_str)
                if info:
                    current_day[1].append(info)
            if "ur" in (right.get("class") or []):
                info = extract_para_info(right, para_num, time_str)
                if info:
                    current_day[2].append(info)
    if current_date and current_day:
        days[current_date] = current_day
    return days


def git_push():
    try:
        subprocess.run(["git", "-C", REPO_DIR, "config", "user.email", "parser@bot"], check=True)
        subprocess.run(["git", "-C", REPO_DIR, "config", "user.name", "Parser"], check=True)
        subprocess.run(["git", "-C", REPO_DIR, "add", "schedule.json"], check=True)
        subprocess.run(["git", "-C", REPO_DIR, "commit", "-m",
                        f"update schedule {datetime.now(MSK).isoformat()}"], check=False)
        subprocess.run(["git", "-C", REPO_DIR, "push"], check=True)
        print("[SCHED] ✅ Запушено в GitHub")
    except subprocess.CalledProcessError as e:
        print(f"[SCHED] ❌ Git ошибка: {e}")


async def parse_and_push():
    print(f"=== Парсинг {datetime.now(MSK).isoformat()} ===")
    html = await fetch_html(GROUP_URL)
    if not html:
        print("[SCHED] ❌ Не смог загрузить сайт")
        return
    days = parse_schedule(html)
    if not days:
        print("[SCHED] ❌ Не спарсилось")
        return

    out_path = os.path.join(REPO_DIR, OUT_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(MSK).isoformat(), "days": days}, f, ensure_ascii=False, indent=2)
    print(f"[SCHED] ✅ Сохранено {len(days)} дней")

    git_push()


async def main():
    print(f"✅ Parser cron запущен. Интервал: {INTERVAL_SEC} сек")
    print(f"REPO_DIR: {REPO_DIR}")
    print(f"GITHUB_REPO: {GITHUB_REPO}")

    if GITHUB_REPO and GITHUB_TOKEN and not os.path.exists(os.path.join(REPO_DIR, ".git")):
        try:
            os.makedirs(REPO_DIR, exist_ok=True)
            url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
            print(f"[SCHED] Клонирую {GITHUB_REPO}...")
            subprocess.run(["git", "clone", url, REPO_DIR], check=True)
            print(f"[SCHED] ✅ Склонировано")
        except Exception as e:
            print(f"[SCHED] ❌ Ошибка клонирования: {e}")

    while True:
        try:
            await parse_and_push()
        except Exception as e:
            print(f"[SCHED] Ошибка: {e}")
        print(f"[SCHED] Сплю {INTERVAL_SEC} сек...")
        await asyncio.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())
