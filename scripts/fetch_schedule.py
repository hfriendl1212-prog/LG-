import os, re, calendar, requests
import pandas as pd
from datetime import datetime, date, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

API_URL = os.getenv("API_URL", "https://lg-twins-lottery.pages.dev")
API_ENDPOINT = f"{API_URL}/api/admin/games/bulk-upload"
BLOCKED_OPPONENTS = ["한화"]

HOME_TEAMS = {
    "LG": "잠실야구장",
    "두산": "잠실야구장"
}

KBO_URL = "https://www.koreabaseball.com/Schedule/Schedule.aspx"

def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def get_draw_period(game_date_str):
    gd = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    month, year = gd.month, gd.year
    # 3월 경기는 예외: 3/16~3/20
    if month == 3:
        return "2026-03-16 00:00", "2026-03-20 23:59"
    # 그 외: 전월 마지막 주 월~금
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    last_day = calendar.monthrange(prev_year, prev_month)[1]
    last_date = date(prev_year, prev_month, last_day)
    # 마지막 주 월요일 계산
    dow = last_date.weekday()  # 0=월, 6=일
    last_monday = last_date - timedelta(days=dow)
    # 마지막 월요일이 전월이 아니면 한 주 앞으로
    if last_monday.month != prev_month:
        last_monday -= timedelta(days=7)
    last_friday = last_monday + timedelta(days=4)
    return f"{last_monday:%Y-%m-%d} 00:00", f"{last_friday:%Y-%m-%d} 23:59"

def get_game_time(game_date_str):
    d = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    return "14:00" if d.weekday() in (5, 6) else "18:30"

def crawl_month(driver, year_str, month_str):
    """KBO 공홈에서 특정 연월 전체 경기 크롤링"""
    driver.get(KBO_URL)
    wait = WebDriverWait(driver, 10)

    # 연도 선택
    year_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "ddlYear"))))
    year_sel.select_by_value(year_str)

    # 월 선택
    month_sel = Select(driver.find_element(By.ID, "ddlMonth"))
    month_sel.select_by_value(month_str)

    # KBO 정규시즌 선택 (value=0)
    series_sel = Select(driver.find_element(By.ID, "ddlSeries"))
    series_sel.select_by_value("0")

    import time; time.sleep(2)  # 렌더링 대기

    table = driver.find_element(By.CLASS_NAME, "tbl-type06")
    rows = table.find_elements(By.TAG_NAME, "tr")

    games = []
    current_date = None

    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if not cols:
            continue
        text_list = [c.text.strip() for c in cols]

        # 날짜 셀 감지 (예: "03.28(토)")
        date_match = re.match(r'(\d{2})\.(\d{2})\(.\)', text_list[0])
        if date_match:
            mm, dd = date_match.group(1), date_match.group(2)
            current_date = f"{year_str}-{mm}-{dd}"

        if current_date is None:
            continue

        # 경기 정보 파싱 (팀vs팀 패턴)
        # KBO 테이블: 날짜, 시간, 원정팀, 점수, 홈팀, TV, 장소 등
        if len(text_list) >= 5:
            time_col = text_list[1] if text_list[1] else ""
            away_team = text_list[2] if len(text_list) > 2 else ""
            home_team = text_list[4] if len(text_list) > 4 else ""
            stadium = text_list[6] if len(text_list) > 6 else ""

            games.append({
                "date": current_date,
                "away": away_team,
                "home": home_team,
                "stadium": stadium,
                "time": time_col
            })

    return games

def filter_jamsil_home(games, target_month_int):
    """잠실 홈 경기 중 LG 또는 두산이 홈인 경기만 필터"""
    result = []
    for g in games:
        gdate = g["date"]
        try:
            if datetime.strptime(gdate, "%Y-%m-%d").month != target_month_int:
                continue
        except:
            continue

        home = g["home"]
        stadium = g["stadium"]

        # 잠실 홈 경기 여부
        if "잠실" not in stadium:
            continue

        # LG 홈 or 두산 홈
        if "LG" in home:
            team = "LG"
        elif "두산" in home:
            team = "두산"
        else:
            continue

        opponent = g["away"]
        is_blocked = 1 if any(b in opponent for b in BLOCKED_OPPONENTS) else 0
        draw_start, draw_end = get_draw_period(gdate)
        result.append({
            "game_date": gdate,
            "game_time": get_game_time(gdate),
            "opponent_team": opponent,
            "team": team,
            "stadium": "잠실야구장",
            "draw_start_date": draw_start,
            "draw_end_date": draw_end,
            "status": "pending",
            "is_blocked": is_blocked
        })
    return result

def upload(rows):
    if not rows:
        print("업로드할 데이터 없음")
        return
    cols = ["game_date","game_time","opponent_team","team","stadium",
            "draw_start_date","draw_end_date","status","is_blocked"]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r[c]) for c in cols))
    csv_text = "\n".join(lines)

    print("--- CSV 미리보기 (첫 5행) ---")
    for l in lines[:6]:
        print(l)

    try:
        resp = requests.post(
            API_ENDPOINT,
            json={"csv_text": csv_text},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        result = resp.json()
        print(f"✅ 완료: inserted={result.get('inserted',0)}, updated={result.get('updated',0)}")
    except Exception as e:
        print(f"❌ 업로드 실패: {e}")

def main():
    now = datetime.now()
    # 다음 달 계산
    if now.month == 12:
        target_year, target_month = now.year + 1, 1
    else:
        target_year, target_month = now.year, now.month + 1

    year_str = str(target_year)
    month_str = str(target_month).zfill(2)

    print(f"🔄 {target_year}년 {target_month}월 KBO 공홈 크롤링 시작")

    driver = get_driver()
    try:
        all_games = crawl_month(driver, year_str, month_str)
        print(f"총 {len(all_games)}경기 수집 (전체)")
    finally:
        driver.quit()

    rows = filter_jamsil_home(all_games, target_month)
    print(f"잠실 홈 경기 (LG+두산): {len(rows)}경기")

    upload(rows)

if __name__ == "__main__":
    main()
