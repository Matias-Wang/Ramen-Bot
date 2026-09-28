"""
資料飛輪分析腳本（A3）：把雲端同步回地端的對話日誌與回報轉為可行動洞察。

產出：
1. 意圖（skill）分布 —— 看使用者主要在用哪個功能。
2. 熱門查詢地區 / 店名 —— 需求熱點。
3. 資料盲區 —— 該查詢實際跑一次搜尋後回不出任何店家的地區 / 店名（指導人工補店）。
4. 待處理使用者回報數 —— 提醒 feedback 修正別漏。

地區盲區的判定方式（2026-08-21 修正）：
  直接呼叫搜尋主路徑 `filter_ramen_data()`，以「真的查不到店家」為盲區定義。
  舊版改以字串比對店家的 `location` 欄位，但該欄位存的是**行政區**
  （如「臺北市中山區」），而使用者多以**站名 / 地標**查詢（「中山站」「忠孝復興」）。
  站名查詢實際走 Geocoding + Haversine 半徑、根本不比對 `location`，
  因此能正常回結果卻被誤判為盲區（實測 6 個盲區中有 5 個是這種偽陽性）。
  改為重用主路徑後，判定與使用者真實體驗一致，也不必再維護第二套比對規則。

資料來源（皆不進版控，需先執行 scripts/fetch_cloud_data.py 同步）：
  data_logs/tracking_conversations.jsonl
  data_logs/tracking_feedbacks.json
  data/ramen_data.json

用法：
  PYTHONUTF8=1 python scripts/analyze_conversations.py [--top 10]
"""

import argparse
import contextlib
import difflib
import io
import json
import os
import sys
from collections import Counter
from typing import Any, Optional

from dotenv import load_dotenv

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
load_dotenv()
# 本腳本為離線分析工具，其 Geocoding 呼叫不應排擠正式使用者的每日配額。
# 必須在匯入任何會呼叫 check_and_increment 的模組之前設定。
os.environ["E2E_TEST_MODE"] = "1"

from skills.Search_skill import filter_ramen_data  # noqa: E402

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

CONV_PATH = os.path.join("data_logs", "tracking_conversations.jsonl")
FEEDBACK_PATH = os.path.join("data_logs", "tracking_feedbacks.json")
RAMEN_DATA_PATH = os.path.join("data", "ramen_data.json")

# 店名模糊比對門檻（與 info_skill 一致）；低於此視為資料庫查無。
FUZZY_MATCH_THRESHOLD = 0.5


def _load_conversations() -> list[dict]:
    """讀取對話日誌 JSONL，每行一筆；缺檔或壞行皆略過。"""
    if not os.path.exists(CONV_PATH):
        print(f"{RED}STEP 1 ERROR: 找不到 {CONV_PATH}，請先執行 fetch_cloud_data.py{RESET}")
        return []
    records: list[dict] = []
    with open(CONV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"{YELLOW}STEP 1: 略過無法解析的一行{RESET}")
    return records


def _load_shops() -> list[dict]:
    """讀取 ramen_data.json 供盲區交叉比對；缺檔回傳空清單。"""
    try:
        with open(RAMEN_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"{YELLOW}STEP: 找不到 {RAMEN_DATA_PATH}，盲區比對將略過{RESET}")
        return []


def _location_has_results(location: str) -> bool:
    """
    以搜尋主路徑實跑一次，判斷該地區查詢是否回得出店家。

    重用 `filter_ramen_data()` 而非自行比對，確保盲區判定與使用者真實體驗一致
    （行政區走字串比對、站名/地標走 Geocoding + Haversine，兩條路徑都涵蓋）。

    Parameters
    ----------
    location : str
        使用者查詢中的地區字串（可能是行政區、站名或地標）。

    Returns
    -------
    bool
        能回出至少一間店家為 True；查無為 False。判定失敗時回傳 True
        （寧可漏報，也不要產生誤導補店方向的偽陽性）。
    """
    if not location:
        return True
    try:
        # filter_ramen_data 會印大量 STEP 訊息，會蓋掉分析報告，故吞掉其輸出。
        with contextlib.redirect_stdout(io.StringIO()):
            results = filter_ramen_data({"location": location})
        return bool(results)
    except Exception as e:
        print(f"{YELLOW}STEP 4: 地區「{location}」判定失敗，視為非盲區：{e}{RESET}")
        return True


def _shop_in_db(shop_name: str, shops: list[dict]) -> bool:
    """店名是否能在資料庫模糊比對到（完全比對或相似度 >= 門檻）。"""
    if not shop_name:
        return True
    best = 0.0
    for s in shops:
        name = s.get("name") or ""
        if shop_name == name:
            return True
        best = max(best, difflib.SequenceMatcher(None, shop_name, name).ratio())
    return best >= FUZZY_MATCH_THRESHOLD


def _extract_arg(record: dict, key: str) -> Optional[str]:
    """從紀錄的 args 取出指定欄位（去空白），空值回傳 None。"""
    val = (record.get("args") or {}).get(key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _print_counter(title: str, counter: Counter, top: int) -> None:
    """列印計數器前 N 名。"""
    print(f"{CYAN}{title}{RESET}")
    if not counter:
        print("  （無資料）")
        return
    for name, cnt in counter.most_common(top):
        print(f"  {cnt:>4}  {name}")


def analyze(top: int = 10) -> None:
    """執行完整分析並列印報告。"""
    print(f"{GREEN}STEP 1: 讀取對話日誌與資料來源{RESET}")
    records = _load_conversations()
    shops = _load_shops()
    print(f"  對話筆數：{len(records)}；資料庫店家數：{len(shops)}")
    if not records:
        return

    # STEP 2: 意圖分布
    print(f"{GREEN}STEP 2: 意圖（skill）分布{RESET}")
    skills = Counter(r.get("predicted_skill", "unknown") for r in records)
    _print_counter("  各 skill 次數：", skills, top)

    # STEP 3: 熱門地區 / 店名
    print(f"{GREEN}STEP 3: 熱門查詢地區 / 店名{RESET}")
    locations = Counter(
        loc for r in records if (loc := _extract_arg(r, "location"))
    )
    shop_names = Counter(
        sn for r in records if (sn := _extract_arg(r, "shop_name"))
    )
    _print_counter("熱門地區：", locations, top)
    _print_counter("熱門店名：", shop_names, top)

    # STEP 4: 資料盲區（以搜尋主路徑實跑，查無結果者才算盲區）
    print(f"{GREEN}STEP 4: 資料盲區（實跑搜尋後查無結果，指導人工補店）{RESET}")
    blind_locations = Counter(
        {loc: c for loc, c in locations.items() if not _location_has_results(loc)}
    )
    blind_shops = Counter(
        {sn: c for sn, c in shop_names.items() if not _shop_in_db(sn, shops)}
    )
    _print_counter("盲區地區：", blind_locations, top)
    _print_counter("盲區店名：", blind_shops, top)

    # STEP 5: 待處理回報
    print(f"{GREEN}STEP 5: 待處理使用者回報{RESET}")
    try:
        with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
            feedbacks = json.load(f)
        print(f"  回報總數：{len(feedbacks)}")
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"{YELLOW}  找不到 {FEEDBACK_PATH}，略過{RESET}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="對話日誌資料飛輪分析")
    parser.add_argument("--top", type=int, default=10, help="各榜單顯示前 N 名")
    args = parser.parse_args()
    try:
        analyze(top=args.top)
    except Exception as e:
        print(f"{RED}ANALYZE ERROR: {e}{RESET}")
