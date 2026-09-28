"""
從 data/resource/ 原始 IG 匯出資料建立新店家候選清單

掃描 data/resource/*/your_instagram_activity/media/posts_1.json，
排除已存在於 ramen_data.json 的貼文與空白文案後，
透過 Gemini 判斷是否為拉麵食記並提取結構化欄位（description 維持原始文案，不重寫），
再以 Google Places API (New) 驗證營業狀態、座標並取得真實店家照片，
最後輸出候選清單至 data/ramen_data_new_<時間戳>.json，
供人工確認內容無誤後，執行 scripts/append_new_shops.py 合併進 ramen_data.json。

注意：IG 官方「下載你的資料」匯出包不含貼文的真實短碼（shortcode）/permalink，
故 social_links 無法自動產生有效的 IG 連結，需人工確認後手動補上。

使用方式：
    python scripts/build_new_shops.py
"""

import glob
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from google import genai

from core.usage_tracker import check_and_increment, record_tokens
from services.google_maps import GoogleMapsService

load_dotenv()

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

RESOURCE_GLOB = os.path.join(
    "data", "resource", "*", "your_instagram_activity", "media", "posts_1.json"
)
RAMEN_DATA_PATH = os.path.join("data", "ramen_data.json")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
BATCH_SIZE = 10


def fix_mojibake(text: str) -> str:
    """修正 IG 官方匯出資料常見的 Latin-1/UTF-8 雙重編碼亂碼。"""
    try:
        return text.encode("latin1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def parse_llm_json(text: str) -> list:
    """從 LLM 回應中解析 JSON list，容錯處理 markdown code block。"""
    text = text.strip()
    if text.startswith("```"):
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def load_existing_ids() -> set[str]:
    """
    讀取現有 ramen_data.json 的所有貼文 id，用於排除重複處理。

    Returns
    -------
    set[str]
        現有店家的 id 集合，讀取失敗時回傳空集合。
    """
    try:
        with open(RAMEN_DATA_PATH, "r", encoding="utf-8") as f:
            shops = json.load(f)
        return {s["id"] for s in shops}
    except Exception as e:
        print(f"{RED}STEP 1 ERROR: 讀取 {RAMEN_DATA_PATH} 失敗: {e}{RESET}")
        return set()


def collect_candidate_posts(existing_ids: set[str]) -> list[dict]:
    """
    掃描 data/resource/ 下所有匯出包的 posts_1.json，
    取出尚未存在於 ramen_data.json、且文案非空的候選貼文。

    Parameters
    ----------
    existing_ids : set[str]
        已存在於 ramen_data.json 的 id 集合，用於跳過重複貼文。

    Returns
    -------
    list[dict]
        候選貼文清單，每筆含 id、description（原始文案，未經改寫）。
    """
    candidates: list[dict] = []
    seen_ids: set[str] = set()

    for posts_path in sorted(glob.glob(RESOURCE_GLOB)):
        with open(posts_path, "r", encoding="utf-8") as f:
            posts = json.load(f)

        for post in posts:
            media = post.get("media", [])
            if not media:
                continue

            uri = media[0].get("uri", "")
            media_id = Path(uri).stem
            if not media_id or media_id in existing_ids or media_id in seen_ids:
                continue

            raw_title = post.get("title") or media[0].get("title", "")
            description = fix_mojibake(raw_title).strip()
            if not description:
                continue

            seen_ids.add(media_id)
            candidates.append({"id": media_id, "description": description})

    return candidates


def run_llm_extraction(posts: list[dict], client: genai.Client) -> list[dict]:
    """
    Stage 1：批次呼叫 Gemini 判斷是否為拉麵食記並結構化提取。

    Parameters
    ----------
    posts : list[dict]
        候選貼文清單。
    client : genai.Client
        已初始化的 Gemini 客戶端。

    Returns
    -------
    list[dict]
        判定為拉麵食記的店家清單（符合 ramen_data.json 欄位格式）。
    """
    results: list[dict] = []
    total_batches = (len(posts) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        batch = posts[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
        print(
            f"{CYAN}STEP 2: LLM 批次 {batch_idx + 1}/{total_batches}"
            f"（{len(batch)} 筆）{RESET}"
        )

        if not check_and_increment("llm_gemini"):
            print(f"{RED}STEP 2 ERROR: llm_gemini 每日配額已達上限，停止處理{RESET}")
            break

        batch_for_llm = [
            {"id": p["id"], "description": p["description"][:600]} for p in batch
        ]
        prompt = (
            "判斷以下IG貼文是否與「拉麵食記」相關，並對 is_ramen=true 的項目提取結構化資訊。\n"
            "只回覆 JSON List，不要任何前言、結語或 markdown。\n"
            "不要改寫或摘要原始文案，只需提取以下欄位。\n\n"
            "口味標籤（擇一）：豚骨、雞白湯、醬油、味噌、煮干、魚介、鹽味、二郎系、家系、沾麵、其他、限定\n\n"
            "輸出欄位：\n"
            "[\n"
            '  {"id":"原始id","is_ramen":true,"name":"店家名稱或null","location":"地區或null",'
            '"style":"口味標籤或null","price_range":"如250-350或null","rating":評分1-5或null,'
            '"features":["特色1"]},\n'
            '  {"id":"原始id","is_ramen":false}\n'
            "]\n\n"
            f"資料：{json.dumps(batch_for_llm, ensure_ascii=False)}"
        )

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            if response.usage_metadata:
                record_tokens(response.usage_metadata.total_token_count or 0)
            batch_results = parse_llm_json(response.text)
        except Exception as e:
            print(f"{RED}STEP 2 ERROR: 批次 {batch_idx + 1} LLM 失敗: {e}{RESET}")
            continue

        for result in batch_results:
            if not result.get("is_ramen"):
                continue

            post_data = next((p for p in batch if p["id"] == result["id"]), None)
            if not post_data:
                continue

            results.append(
                {
                    "id": result["id"],
                    "name": result.get("name"),
                    "location": result.get("location"),
                    "address": None,
                    "coordinates": {"lat": None, "lng": None},
                    "style": result.get("style"),
                    "price_range": result.get("price_range"),
                    "rating": result.get("rating"),
                    "features": result.get("features") or [],
                    "description": post_data["description"],
                    "map_url": None,
                    "image_url": None,
                    "social_links": [
                        {"label": "IG介紹", "url": None},
                        {"label": None, "url": None},
                        {"label": None, "url": None},
                    ],
                    "place_id": None,
                    "user_ratings_total": None,
                    "opening_hours": None,
                    "last_updated": None,
                }
            )

        time.sleep(2)

    return results


def verify_with_places_api(
    shops: list[dict], gmaps: GoogleMapsService
) -> list[dict]:
    """
    Stage 2：呼叫 Places API (New) 驗證營業狀態、取得座標與地址並補上真實
    店家照片，過濾已永久歇業的店家。

    Parameters
    ----------
    shops : list[dict]
        待驗證的店家清單。
    gmaps : GoogleMapsService
        已初始化的 Google Maps 服務。

    Returns
    -------
    list[dict]
        通過驗證的店家清單；找不到 Places 照片的店家 image_url 維持 None
        （而非使用本機檔案路徑，避免寫入無效的 URL）。
    """
    verified: list[dict] = []

    for shop in shops:
        name = shop.get("name")
        if not name:
            verified.append(shop)
            continue

        result = gmaps.verify_shop_status(name, shop.get("location") or "")
        if result:
            if result.get("business_status") == "CLOSED_PERMANENTLY":
                print(f"{YELLOW}STEP 3: 已永久歇業，跳過 - {name}{RESET}")
                continue
            shop["place_id"] = result.get("place_id")
            shop["coordinates"] = result.get("coordinates", shop["coordinates"])
            if result.get("address"):
                shop["address"] = result["address"]

            if shop["place_id"]:
                shop["image_url"] = gmaps.get_photo_by_place_id(shop["place_id"])
                shop["opening_hours"] = gmaps.get_opening_hours_by_place_id(shop["place_id"])

        verified.append(shop)
        time.sleep(0.5)

    return verified


def main() -> None:
    """主流程：建立新店家候選清單。"""
    print(f"{GREEN}STEP 1: 掃描 data/resource/ 並比對既有 ramen_data.json{RESET}")
    try:
        existing_ids = load_existing_ids()
        candidates = collect_candidate_posts(existing_ids)
        print(f"  找到 {len(candidates)} 筆候選貼文（已排除既有與空白文案）")
    except Exception as e:
        print(f"{RED}STEP 1 ERROR:{e}{RESET}")
        return

    if not candidates:
        print(f"{CYAN}  => 沒有新貼文需要處理。{RESET}")
        return

    print(f"{GREEN}STEP 2: 初始化 Gemini 與 Google Maps 服務{RESET}")
    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        gmaps = GoogleMapsService()
    except Exception as e:
        print(f"{RED}STEP 2 ERROR:{e}{RESET}")
        return

    ramen_results = run_llm_extraction(candidates, client)
    print(f"{GREEN}STEP 2 完成: 篩選出 {len(ramen_results)} 筆拉麵店家{RESET}")

    if not ramen_results:
        print(f"{CYAN}  => 沒有判定為拉麵食記的貼文。{RESET}")
        return

    print(f"{GREEN}STEP 3: 開始地理驗證（共 {len(ramen_results)} 筆）{RESET}")
    try:
        ramen_results = verify_with_places_api(ramen_results, gmaps)
        print(f"{GREEN}STEP 3 完成: {len(ramen_results)} 筆通過驗證{RESET}")
    except Exception as e:
        print(f"{RED}STEP 3 ERROR:{e}{RESET}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join("data", f"ramen_data_new_{timestamp}.json")
    print(f"{GREEN}STEP 4: 寫入候選清單 {output_path}{RESET}")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(ramen_results, f, ensure_ascii=False, indent=2)
        print(
            f"{GREEN}STEP 4 完成: 已寫入 {len(ramen_results)} 筆，"
            f"請確認內容後執行：python scripts/append_new_shops.py {output_path}{RESET}"
        )
    except Exception as e:
        print(f"{RED}STEP 4 ERROR:{e}{RESET}")


if __name__ == "__main__":
    main()
