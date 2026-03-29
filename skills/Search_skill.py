import json
import os
import asyncio
import re
import google.generativeai as genai
from prompts import RECOMMEND_PROMPT

def filter_ramen_data(intent_data):
    """
    核心篩選邏輯：根據 AI 解析出的意圖，從本地 JSON 篩選店家。
    """
    data_path = os.path.join('data', 'ramen_data.json')
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            all_shops = json.load(f)
    except FileNotFoundError:
        print(f"錯誤：找不到 {data_path} 檔案")
        return []
    
    # 取得意圖條件
    target_location = intent_data.get('location') or ""
    target_style = intent_data.get('style') or ""

    # 過濾掉 AI 誤抓的形容詞（如「推薦」）
    if target_style in ["推薦", "好吃", "熱門"]:
        target_style = ""

    clean_target_loc = target_location.replace('市', '').replace('區', '').replace('縣', '')

    print(f"[DEBUG] 篩選條件 - 地區: '{clean_target_loc}', 口味: '{target_style}'")

    filtered_results = []
    for shop in all_shops:
        shop_loc = shop.get('location', '')
        shop_style = shop.get('style', '')

        match_location = not clean_target_loc or (clean_target_loc in shop_loc or shop_loc in clean_target_loc)
        match_style = not target_style or (target_style in shop_style)
        
        if match_location and match_style:
            filtered_results.append(shop)
            
    print(f"[DEBUG] 篩選後店家數量: {len(filtered_results)}")
    return filtered_results

# --- AI 推薦文生成邏輯 (以下省略註解以節省篇幅，內容保持不變) ---

def build_shop_summary(shop: dict) -> str:
    name = shop.get("name") or "未知店名"
    loc = shop.get("location") or ""
    style = shop.get("style") or ""
    desc = shop.get("description") or ""
    features = shop.get("features") or []
    feature_text = "、".join(features[:4]) if isinstance(features, list) else ""
    parts = [name, f"位於{loc}" if loc else "", f"風格：{style}" if style else ""]
    summary = "，".join(p for p in parts if p)
    if feature_text:
        summary += f"；特色：{feature_text}"
    if desc:
        summary += f"。簡介：{desc}"
    return summary

def get_one_recommendation(shop_summary: str, model) -> str:
    default = "點擊查看地圖了解更多。"
    try:
        prompt = RECOMMEND_PROMPT.format(shop_summary=shop_summary)
        recommend_result = model.generate_content(
            prompt,
            generation_config={"temperature": 0.6, "max_output_tokens": 1200}
        )
        def _extract_text(obj):
            for attr in ('text', 'output', 'content', 'candidates'):
                if hasattr(obj, attr): return getattr(obj, attr)
            return str(obj)
        raw = _extract_text(recommend_result).strip()
        raw = re.sub(r"```\w*\s*", "", raw).strip()
        if not raw or any(c in raw for c in "I will"): return default
        return raw
    except Exception: return default

async def get_one_recommendation_async(shop_summary: str, model):
    return await asyncio.to_thread(get_one_recommendation, shop_summary, model)

async def fetch_all_recommendations_async(summaries: list[str], model):
    tasks = [get_one_recommendation_async(s, model) for s in summaries]
    return await asyncio.gather(*tasks, return_exceptions=True)

def generate_recommendations(shops_info, model, num_shops=3):
    if not shops_info: return []
    selected = shops_info[:num_shops]
    summaries = [build_shop_summary(s) for s in selected]
    try:
        results = asyncio.run(fetch_all_recommendations_async(summaries, model))
        return [r if not isinstance(r, Exception) else "點擊查看地圖了解更多。" for r in results]
    except Exception:
        return ["點擊查看地圖了解更多。"] * len(summaries)
