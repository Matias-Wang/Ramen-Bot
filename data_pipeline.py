"""
IG 拉麵資料清理 Pipeline
依據 data/data_cleaning_rule.md 執行三階段處理：
Stage 1+2: Gemini LLM 批次過濾 + 結構化提取
Stage 3: Google Maps Places API (New) 驗證
"""

import json
import os
import sys
import time
import re
from pathlib import Path
from typing import Optional

import requests
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
PLACES_NEW_BASE_URL = "https://places.googleapis.com/v1"
OUTPUT_PATH = "data/ramen_data_20260426.json"
CHECKPOINT_PATH = "data/ramen_data_checkpoint.json"


def media_id_to_ig_url(media_id: str) -> str:
    """將 Instagram media ID 轉換為貼文 URL。"""
    n = int(media_id)
    result = []
    while n > 0:
        n, rem = divmod(n, 64)
        result.append(ALPHABET[rem])
    shortcode = "".join(reversed(result))
    return f"https://www.instagram.com/p/{shortcode}/"


def load_posts1_titles() -> dict[str, str]:
    """從 posts_1.json 建立 media_id -> title 對照表。"""
    try:
        with open("data/media/posts_1.json", "r", encoding="utf-8") as f:
            posts = json.load(f)
    except Exception as e:
        print(f"{RED}STEP 1 ERROR: 讀取 posts_1.json 失敗: {e}{RESET}")
        return {}

    id_to_title: dict[str, str] = {}
    for p in posts:
        title = p.get("title", "").strip()
        if not title:
            for m in p.get("media", []):
                if m.get("title", "").strip():
                    title = m["title"].strip()
                    break
        for m in p.get("media", []):
            uri = m.get("uri", "")
            file_id = Path(uri).stem
            if file_id and file_id not in id_to_title:
                id_to_title[file_id] = title
    return id_to_title


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


def verify_shop_status(api_key: str, name: str, location: str = "") -> Optional[dict]:
    """
    Stage 3: Places API (New) 驗證店家狀態與取得 place_id。
    Field Masking 僅請求 places.businessStatus, places.id,
    places.location, places.formattedAddress。
    """
    query = f"{location} {name} 拉麵".strip() if location else f"{name} 拉麵"
    print(f"{CYAN}STEP 3: 驗證 - {query}{RESET}")

    try:
        url = f"{PLACES_NEW_BASE_URL}/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.id,"
                "places.businessStatus,"
                "places.location,"
                "places.formattedAddress"
            ),
        }
        body = {
            "textQuery": query,
            "languageCode": "zh-TW",
            "maxResultCount": 1,
        }
        response = requests.post(url, json=body, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        places = data.get("places", [])
        if not places:
            return None

        place = places[0]
        loc = place.get("location", {})
        return {
            "place_id": place.get("id"),
            "business_status": place.get("businessStatus", "OPERATIONAL"),
            "coordinates": {
                "lat": loc.get("latitude"),
                "lng": loc.get("longitude"),
            },
            "address": place.get("formattedAddress"),
        }
    except Exception as e:
        print(f"{RED}STEP 3 ERROR: 驗證 {name} 失敗: {e}{RESET}")
        return None


def run_stage2(ig_data: list[dict], model) -> list[dict]:
    """Stage 1+2: 批次過濾與結構化提取。"""
    ramen_results: list[dict] = []
    batch_size = 10
    total_batches = (len(ig_data) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        batch_start = batch_idx * batch_size
        batch = ig_data[batch_start : batch_start + batch_size]

        print(
            f"{CYAN}STEP 2: 批次 {batch_idx + 1}/{total_batches} "
            f"(筆 {batch_start + 1}~{batch_start + len(batch)}){RESET}"
        )

        batch_for_llm = [
            {"id": p["id"], "description": (p.get("description") or "")[:600]}
            for p in batch
        ]

        prompt = (
            "判斷以下IG貼文是否與「拉麵食記」相關，並對 is_ramen=true 的項目提取資訊。\n"
            "只回覆 JSON List，不要任何前言、結語或 markdown。\n\n"
            "口味標籤（擇一）：豚骨、雞白湯、醬油、味噌、煮干、魚介、鹽味、二郎系、家系、沾麵、其他、限定\n\n"
            "輸出欄位：\n"
            "[\n"
            '  {"id":"原始id","is_ramen":true,"name":"店家名稱或null","location":"台北市行政區或null",'
            '"style":"口味標籤或null","price_range":"如250-350或null","rating":評分1-5或null,'
            '"features":["特色1"],"description":"100字內精簡中文描述或null"},\n'
            '  {"id":"原始id","is_ramen":false}\n'
            "]\n\n"
            f"資料：{json.dumps(batch_for_llm, ensure_ascii=False)}"
        )

        try:
            response = model.generate_content(prompt)
            batch_results = parse_llm_json(response.text)
        except Exception as e:
            print(f"{RED}STEP 2 ERROR: 批次 {batch_idx + 1} LLM 失敗: {e}{RESET}")
            time.sleep(10)
            continue

        for result in batch_results:
            if not result.get("is_ramen"):
                continue

            post_data = next(
                (p for p in batch if p["id"] == result["id"]), None
            )
            if not post_data:
                continue

            ig_url = media_id_to_ig_url(result["id"])

            ramen_results.append(
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
                    "description": result.get("description"),
                    "map_url": None,
                    "image_url": post_data.get("image_url", ""),
                    "social_links": [
                        {"label": "我的 IG", "url": ig_url},
                        {"label": None, "url": None},
                        {"label": None, "url": None},
                    ],
                    "place_id": None,
                    "user_ratings_total": None,
                    "last_updated": None,
                }
            )

        time.sleep(10)

    return ramen_results


def main() -> None:
    # ── 初始化 ──────────────────────────────────────────────
    print(f"{GREEN}STEP 0: 初始化 Pipeline{RESET}")
    try:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        model = genai.GenerativeModel(model_name)
        maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
        print(
            f"{GREEN}STEP 0: Gemini={model_name}, "
            f"Maps={'OK' if maps_api_key else '未設定'}{RESET}"
        )
    except Exception as e:
        print(f"{RED}STEP 0 ERROR: {e}{RESET}")
        return

    # ── STEP 1: 載入與補充資料 ───────────────────────────────
    print(f"{GREEN}STEP 1: 載入 instagram_data.json{RESET}")
    try:
        with open("data/instagram_data.json", "r", encoding="utf-8") as f:
            ig_data: list[dict] = json.load(f)

        posts1_titles = load_posts1_titles()
        enriched = 0
        for entry in ig_data:
            if not entry.get("description", "").strip():
                title = posts1_titles.get(entry["id"], "")
                if title:
                    entry["description"] = title
                    enriched += 1

        print(
            f"{GREEN}STEP 1: 載入 {len(ig_data)} 筆，"
            f"補充描述 {enriched} 筆{RESET}"
        )
    except Exception as e:
        print(f"{RED}STEP 1 ERROR: {e}{RESET}")
        return

    # ── STEP 2: 批次過濾 + 結構化提取 ─────────────────────────
    ramen_results: list[dict] = []

    if os.path.exists(CHECKPOINT_PATH):
        print(f"{YELLOW}STEP 2: 發現 checkpoint，直接載入跳過 LLM 處理{RESET}")
        try:
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                ramen_results = json.load(f)
            print(f"{GREEN}STEP 2: 從 checkpoint 載入 {len(ramen_results)} 筆{RESET}")
        except Exception as e:
            print(f"{RED}STEP 2 ERROR: 讀取 checkpoint 失敗: {e}，重新處理{RESET}")
            ramen_results = []

    if not ramen_results:
        print(f"{GREEN}STEP 2: 開始批次過濾與結構化提取（每批 10 筆）{RESET}")
        ramen_results = run_stage2(ig_data, model)

        print(
            f"{GREEN}STEP 2 完成: 找到 {len(ramen_results)} 筆拉麵資料{RESET}"
        )

        try:
            with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
                json.dump(ramen_results, f, ensure_ascii=False, indent=2)
            print(f"{CYAN}STEP 2: Checkpoint 已儲存至 {CHECKPOINT_PATH}{RESET}")
        except Exception as e:
            print(f"{YELLOW}STEP 2: Checkpoint 儲存失敗: {e}{RESET}")
    else:
        print(f"{GREEN}STEP 2 完成: 共 {len(ramen_results)} 筆{RESET}")

    # ── STEP 3: 地理驗證 (Places API New) ────────────────────
    if maps_api_key:
        print(f"{GREEN}STEP 3: 開始地理驗證（共 {len(ramen_results)} 筆）{RESET}")
        verified: list[dict] = []

        for entry in ramen_results:
            name = entry.get("name")
            if not name:
                verified.append(entry)
                continue

            location = entry.get("location") or ""
            result = verify_shop_status(maps_api_key, name, location)

            if result:
                status = result.get("business_status", "OPERATIONAL")
                if status == "CLOSED_PERMANENTLY":
                    print(f"{YELLOW}STEP 3: 已永久歇業，跳過 - {name}{RESET}")
                    continue
                entry["place_id"] = result.get("place_id")
                entry["coordinates"] = result.get(
                    "coordinates", {"lat": None, "lng": None}
                )
                if result.get("address"):
                    entry["address"] = result["address"]

            verified.append(entry)
            time.sleep(0.5)

        ramen_results = verified
        print(
            f"{GREEN}STEP 3 完成: {len(ramen_results)} 筆通過驗證{RESET}"
        )
    else:
        print(f"{YELLOW}STEP 3: 跳過（未設定 GOOGLE_MAPS_API_KEY）{RESET}")

    # ── STEP 4: 寫入輸出 ─────────────────────────────────────
    print(f"{GREEN}STEP 4: 寫入 {OUTPUT_PATH}{RESET}")
    try:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(ramen_results, f, ensure_ascii=False, indent=2)
        print(
            f"{GREEN}STEP 4 完成: 已寫入 {OUTPUT_PATH}，"
            f"共 {len(ramen_results)} 筆拉麵資料{RESET}"
        )
    except Exception as e:
        print(f"{RED}STEP 4 ERROR: {e}{RESET}")


if __name__ == "__main__":
    main()
