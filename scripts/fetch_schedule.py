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
SEASON_MONTHS = ["03", "04", "05", "06", "07", "08", "09", "10"]

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
    m = re.match(r'(.+?)vs(.+)', team_str, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None

def crawl_month(driver, year_str, month_str):
    games = []
    try:
        driver.get(KBO_URL)
        wait = WebDriverWait(driver, 15)

        year_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "ddlYear"))))
        year_sel.select_by_value(year_str)
        time.sleep(1)

        month_sel = Select(driver.find_element(By.ID, "ddlMonth"))
        month_sel.select_by_value(month_str)
        time.sleep(1)

        series_sel = Select(driver.find_element(By.ID, "ddlSeries"))
        try:
            series_sel.select_by_visible_text("KBO 정규시즌 일정")
        except:
            series_sel.select_by_index(1)
        time.sleep(2)

        table = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "tbl-type06")))
        rows = table.find_elements(By.TAG_NAME, "tr")

        current_date = None
        for row in rows:
            ths = row.find_elements(By.TAG_NAME, "th")
            for th in ths:
                th_text = th.text.strip()
                date_match = re.match(r'(\d{2})\.(\d{2})\(.+\)', th_text)
                if date_match:
                    mm, dd = date_match.group(1), date_match.group(2)
                    current_date = f"{year_str}-{mm}-{dd}"

            cols = row.find_elements(By.TAG_NAME, "td")
            if not cols:
                continue

            text_list = [c.text.strip() for c in cols]

            if text_list:
                date_match = re.match(r'(\d{2})\.(\d{2})\(.+\)', text_list[0])
                if date_match:
                    mm, dd = date_match.group(1), date_match.group(2)
                    current_date = f"{year_str}-{mm}-{dd}"

            if current_date is None:
                continue

            team_str = None
            for cell_text in text_list:
                if 'vs' in cell_text.lower():
                    team_str = cell_text
                    break

            if not team_str:
                continue

            away_team, home_team = parse_teams(team_str)
            if not away_team or not home_team:
                continue

            stadium = text_list[-1] if len(text_list) > 1 else ""
            if not stadium or len(stadium) < 2:
                for cell_text in text_list:
                    if '잠실' in cell_text or '구장' in cell_text:
                        stadium = cell_text
                        break

            # ✅ 취소 감지: 비고란에 취소 관련 텍스트 또는 스코어 없음
            is_cancelled = False
            for cell_text in text_list:
                if any(kw in cell_text for kw in ["취소", "우천", "그라운드사정", "강우", "콜드"]):
                    is_cancelled = True
                    break

            # ✅ 스코어 감지: "숫자:숫자" 패턴이 없고 날짜가 오늘 이전이면 취소로 간주
            score_found = any(re.search(r'\d+:\d+', cell_text) for cell_text in text_list)
            game_date_obj = datetime.strptime(current_date, "%Y-%m-%d").date()
            if not score_found and game_date_obj < date.today() and not is_cancelled:
                # 과거 경기인데 스코어도 없고 취소 표기도 없으면 일단 그냥 진행
                pass

            games.append({
                "date": current_date,
                "away": away_team,
                "home": home_team,
                "stadium": stadium,
                "is_cancelled": is_cancelled
            })

    except Exception as e:
        print(f"  [{month_str}월] 크롤링 오류: {e}")

    seen = set()
    unique_games = []
    for g in games:
        key = (g["date"], g["away"], g["home"])
        if key not in seen:
            seen.add(key)
            unique_games.append(g)

    cancelled_count = sum(1 for g in unique_games if g["is_cancelled"])
    print(f"  [{month_str}월] {len(unique_games)}경기 수집 (취소 감지: {cancelled_count}건)")
    return unique_games

def filter_jamsil_home(games):
    result = []
    for g in games:
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
        is_blocked = 1 if (team == "두산" and any(b in opponent for b in BLOCKED_OPPONENTS)) else 0
        draw_start, draw_end = get_draw_period(g["date"])

        # ✅ 취소된 경기는 status를 cancelled로 세팅
        status = "cancelled" if g.get("is_cancelled") else "pending"

        result.append({
            "game_date": g["date"],
            "game_time": get_game_time(g["date"]),
            "opponent_team": opponent,
            "team": team,
            "stadium": "잠실야구장",
            "draw_start_date": draw_start,
            "draw_end_date": draw_end,
            "status": status,
            "is_blocked": is_blocked,
            # ✅ 신규/취소 여부를 API에 전달
            "is_cancelled": 1 if g.get("is_cancelled") else 0
        })
    return result

def upload(rows):
    if not rows:
        print("업로드할 데이터 없음")
        return
    cols = ["game_date", "game_time", "opponent_team", "team", "stadium",
            "draw_start_date", "draw_end_date", "status", "is_blocked", "is_cancelled"]
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
        print(f"✅ 완료: inserted={result.get('inserted', 0)}, updated={result.get('updated', 0)}, cancelled={result.get('cancelled', 0)}")
        if result.get("validation_errors"):
            print(f"⚠️ 검증 오류: {result['validation_errors'][:3]}")
        if result.get("insert_errors"):
            print(f"❌ 저장 오류: {result['insert_errors'][:3]}")
    except Exception as e:
        print(f"❌ 업로드 실패: {e}")

def main():
    year_str = str(datetime.now().year)
    print(f"🔄 {year_str}년 시즌 전체 KBO 크롤링 시작")
    print(f"   API: {API_ENDPOINT}")
    print(f"   대상 월: {SEASON_MONTHS}")

    driver = get_driver()
    all_games = []
    try:
        for month_str in SEASON_MONTHS:
            games = crawl_month(driver, year_str, month_str)
            all_games.extend(games)
            time.sleep(1)
    finally:
        driver.quit()

    rows = filter_jamsil_home(all_games)
    print(f"\n전체 잠실 홈 경기 (LG+두산): {len(rows)}경기")
    upload(rows)

if __name__ == "__main__":
    main()
