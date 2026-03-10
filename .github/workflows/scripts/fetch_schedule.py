import requests
from bs4 import BeautifulSoup
import os
import re
from datetime import datetime, date, timedelta
import calendar

# ── 설정 ──────────────────────────────────────────
API_URL = os.environ.get("API_URL", "https://lg-twins-lottery.pages.dev")
API_ENDPOINT = f"{API_URL}/api/admin/games/bulk-upload"
BLOCKED_OPPONENTS = ["한화 이글스"]

TEAM_CONFIG = {
    "LG": {"home_stadium": "잠실야구장"},
    "두산": {"home_stadium": "잠실야구장"}
}

# ── 추첨 기간 계산 ────────────────────────────────
def get_draw_period(game_date_str):
    gd = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    month = gd.month
    year = gd.year

    if month == 3:
        return "2026-03-16 00:00", "2026-03-20 23:59"

    prev_month = month - 1
    prev_year = year
    if prev_month == 0:
        prev_month = 12
        prev_year = year - 1

    last_day = calendar.monthrange(prev_year, prev_month)[1]
    last_date = date(prev_year, prev_month, last_day)
    days_since_monday = last_date.weekday()

    if days_since_monday <= 4:
        last_monday = last_date - timedelta(days=days_since_monday)
    else:
        last_monday = last_date - timedelta(days=days_since_monday - 7)

    # 마지막주 월요일이 전전달로 넘어가면 다음주 월요일로
    if last_monday.month != prev_month:
        last_monday = last_monday + timedelta(days=7)

    last_friday = last_monday + timedelta(days=4)
    return f"{last_monday.strftime('%Y-%m-%d')} 00:00", f"{last_friday.strftime('%Y-%m-%d')} 23:59"

# ── 경기 시간 ─────────────────────────────────────
def get_game_time(game_date_str):
    d = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    return "14:00" if d.weekday() in [5, 6] else "18:30"

# ── 나무위키 파싱 ─────────────────────────────────
def fetch_wiki_schedule(team, year, target_month):
    if target_month in [3, 4]:
        month_str = "3~4월"
    else:
        month_str = f"{target_month}월"

    if team == "LG":
        wiki_name = f"LG 트윈스/{year}년/{month_str}"
    else:
        wiki_name = f"두산 베어스/{year}년/{month_str}"

    url = f"https://namu.wiki/w/{requests.utils.quote(wiki_name)}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; schedule-bot/1.0)"}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] {team} {month_str} 접근 실패: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text()
    lines = text.split('\n')

    games = []

    for line in lines:
        line = line.strip()

        # 날짜 범위 + VS 팀명 + 구장 패턴
        # 예: "4월 3일 ~ 4월 5일 VS 한화 이글스 (잠실)"
        m = re.search(
            r'(\d{1,2})월\s+(\d{1,2})일\s*[~～]\s*(\d{1,2})월\s+(\d{1,2})일\s+VS\s+(.+?)\s*[\(（](.+?)[\)）]',
            line
        )
        if not m:
            continue

        m1, d1, m2, d2, opponent, stadium = m.groups()
        opponent = opponent.strip()
        stadium = stadium.strip()

        # 잠실 홈경기만 & 원정 아닌 것만
        if '잠실' not in stadium or '원정' in stadium:
            continue

        # 날짜 범위 생성
        try:
            start = date(year, int(m1), int(d1))
            end = date(year, int(m2), int(d2))
        except ValueError:
            continue

        cur = start
        while cur <= end:
            if cur.month == target_month:
                games.append({
                    "date": cur.strftime("%Y-%m-%d"),
                    "opponent": opponent
                })
            cur += timedelta(days=1)

    print(f"  {team} {month_str}: {len(games)}경기 수집")
    return games

# ── CSV 생성 및 업로드 ────────────────────────────
def upload(rows):
    if not rows:
        print("업로드할 데이터 없음")
        return

    header = "game_date,game_time,opponent_team,team,stadium,draw_start_date,draw_end_date,status,is_blocked"
    lines = [header]
    for r in rows:
        lines.append(
            f"{r['game_date']},{r['game_time']},{r['opponent_team']},{r['team']},"
            f"{r['stadium']},{r['draw_start_date']},{r['draw_end_date']},{r['status']},{r['is_blocked']}"
        )
    csv_text = "\n".join(lines)

    print("\n--- CSV 미리보기 (첫 5행) ---")
    for l in lines[:6]:
        print(l)
    print("---\n")

    try:
        resp = requests.post(
            API_ENDPOINT,
            json={"csv_text": csv_text},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        result = resp.json()
        print(f"✅ 완료: inserted={result.get('inserted',0)}, updated={result.get('updated',0)}")
        if result.get('validation_errors'):
            print(f"⚠️  오류: {result['validation_errors'][:3]}")
    except Exception as e:
        print(f"❌ 업로드 실패: {e}")

# ── 메인 ──────────────────────────────────────────
def main():
    now = datetime.now()
    year = now.year
    target_month = now.month + 1 if now.month < 12 else 1
    if target_month == 1:
        year += 1

    print(f"🔄 {year}년 {target_month}월 자동 업데이트 시작")
    print(f"   API: {API_ENDPOINT}\n")

    all_rows = []
    for team in ["LG", "두산"]:
        games = fetch_wiki_schedule(team, year, target_month)
        for g in games:
            is_blocked = 1 if any(b in g["opponent"] for b in BLOCKED_OPPONENTS) else 0
            draw_start, draw_end = get_draw_period(g["date"])
            all_rows.append({
                "game_date": g["date"],
                "game_time": get_game_time(g["date"]),
                "opponent_team": g["opponent"],
                "team": team,
                "stadium": TEAM_CONFIG[team]["home_stadium"],
                "draw_start_date": draw_start,
                "draw_end_date": draw_end,
                "status": "pending",
                "is_blocked": is_blocked
            })

    print(f"총 {len(all_rows)}행 업로드 시도")
    upload(all_rows)

if __name__ == "__main__":
    main()
