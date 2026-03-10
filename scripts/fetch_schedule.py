import os, re, calendar, requests, time
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
KBO_URL = "https://www.koreabaseball.com/Schedule/Schedule.aspx"

def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def get_draw_period(game_date_str):
    gd = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    month, year = gd.month, gd.year
    if month == 3:
        return "2026-03-16 00:00", "2026-03-20 23:59"
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    last_day = calendar.monthrange(prev_year, prev_month)[1]
    last_date = date(prev_year, prev_month, last_day)
    dow = last_date.weekday()
    last_monday = last_date - timedelta(days=dow)
    if last_monday.month != prev_month:
        last_monday -= timedelta(days=7)
    last_friday = last_monday + timedelta(days=4)
    return f"{last_monday:%Y-%m-%d} 00:00", f"{last_friday:%Y-%m-%d} 23:59"

def get_game_time(game_date_str):
    d = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    return "14:00" if d.weekday() in (5, 6) else "18:30"

def parse_teams(team_str):
    """
    'KIAvsLG' → away='KIA', home='LG'
    'vs' 기준으로 분리, 뒤쪽이 홈팀
    """
    m = re.match(r'(.+?)vs(.+)', team_str, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None

def crawl_month(year_str, month_str):
    driver = get_driver()
    games = []
    try:
        driver.get(KBO_URL)
        wait = WebDriverWait(driver, 15)

        # 연도 선택
        year_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "ddlYear"))))
        year_sel.select_by_value(year_str)
        time.sleep(1)

        # 월 선택
        month_sel = Select(driver.find_element(By.ID, "ddlMonth"))
        month_sel.select_by_value(month_str)
        time.sleep(1)

        # KBO 정규시즌 일정 선택
        series_sel = Select(driver.find_element(By.ID, "ddlSeries"))
        try:
            series_sel.select_by_visible_text("KBO 정규시즌 일정")
        except:
            series_sel.select_by_index(1)
        time.sleep(2)

        table = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "tbl-type06")))
        rows = table.find_elements(By.TAG_NAME, "tr")
        print(f"  총 행 수: {len(rows)}")

        current_date = None
        for row in rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if not cols:
                continue
            text_list = [c.text.strip() for c in cols]

            # 날짜 감지: '04.01(수)' 형태
            date_match = re.match(r'(\d{2})\.(\d{2})\(.+\)', text_list[0])
            if date_match:
                mm, dd = date_match.group(1), date_match.group(2)
                current_date = f"{year_str}-{mm}-{dd}"

            if current_date is None:
                continue

            # 경기 데이터 행: text_list[2]에 'XXXvsYYY' 패턴 존재
            if len(text_list) < 8:
                continue

            team_str = text_list[2]
            if "vs" not in team_str.lower():
                continue

            away_team, home_team = parse_teams(team_str)
            if not away_team or not home_team:
                continue

            stadium = text_list[7]  # 구장은 index 7

            games.append({
                "date": current_date,
                "away": away_team,
                "home": home_team,
                "stadium": stadium
            })

        print(f"  KBO 크롤링 완료: {len(games)}경기 수집")

    except Exception as e:
        print(f"  크롤링 오류: {e}")
    finally:
        driver.quit()
    return games

def filter_jamsil_home(games, target_month_int):
    result = []
    for g in games:
        try:
            if datetime.strptime(g["date"], "%Y-%m-%d").month != target_month_int:
                continue
        except:
            continue

        if "잠실" not in g["stadium"]:
            continue

        home = g["home"]
        if "LG" in home:
            team = "LG"
        elif "두산" in home:
            team = "두산"
        else:
            continue

        opponent = g["away"]
        is_blocked = 1 if any(b in opponent for b in BLOCKED_OPPONENTS) else 0
        draw_start, draw_end = get_draw_period(g["date"])

        result.append({
            "game_date": g["date"],
            "game_time": get_game_time(g["date"]),
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

    print("--- CSV 미리보기 (첫 6행) ---")
    for l in lines[:7]:
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
        if result.get("validation_errors"):
            print(f"⚠️ 오류: {result['validation_errors'][:3]}")
    except Exception as e:
        print(f"❌ 업로드 실패: {e}")

def main():
    now = datetime.now()
    if now.month == 12:
        target_year, target_month = now.year + 1, 1
    else:
        target_year, target_month = now.year, now.month + 1

    year_str  = str(target_year)
    month_str = str(target_month).zfill(2)

    print(f"🔄 {target_year}년 {target_month}월 KBO 공홈 크롤링 시작")
    print(f"   API: {API_ENDPOINT}")

    all_games = crawl_month(year_str, month_str)
    rows = filter_jamsil_home(all_games, target_month)
    print(f"잠실 홈 경기 (LG+두산): {len(rows)}경기")
    upload(rows)

if __name__ == "__main__":
    main()
