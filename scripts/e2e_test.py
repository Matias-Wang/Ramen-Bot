"""
統一端到端測試：涵蓋 Search／Info／Knowledge／Feedback 四個 Skill，
從預設問句輸入到實際輸出（Flex Bubble 或文字回答）的完整驗證。

流程與 app.py 的 _reply_to_line() 一致：AgentRouter.dispatch(question) → ...

預設（不帶參數）每個情境只隨機抽 1 題，供 pre-commit hook 等高頻率執行
情境使用，控制 Gemini／Google Maps 每日配額消耗；加上 --full 才會跑完
每個情境底下的完整題庫，供人工需要更全面驗證時手動執行。

對「可程式判斷」的項目（intent 是否正確、欄位對齊、按鈕格式、URL 合法性、
回報是否可寫入查回）自動檢查並標記 PASS/FAIL，發現問題時以非 0 exit code
結束，供 git hook 判斷是否阻止 commit；對「語意是否通順、是否切題」等需要
人工判斷的項目，僅原文列出於報告供人工確認。

資料後端依 DATA_BACKEND（同 src/）：
- local（.env 預設）：讀本機 data/ramen_data.json、ChromaDB、log/feedback_reports.json。
- firestore：讀正式 Firestore，不依賴任何不進版控的本機檔案，供 CI 與其他設備使用。
  因 E2E_TEST_MODE=1，對話日誌與回報寫入 test_ 前綴集合，不混入正式資料。

執行方式：
    python scripts/e2e_test.py          # 預設：每情境抽 1 題
    python scripts/e2e_test.py --full   # 每情境跑完整題庫
    DATA_BACKEND=firestore python scripts/e2e_test.py   # 不依賴本機資料（CI 用）

報告輸出：log/e2e_test_<YYYYMMDD_HHMM>.md（log/ 不納入版控）
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

from dotenv import load_dotenv

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
load_dotenv()
# 測試呼叫不應計入 src/core/usage_tracker.py 的每日配額（避免頻繁執行排擠
# 正式使用量），必須在匯入任何會呼叫 check_and_increment 的模組之前設定。
os.environ["E2E_TEST_MODE"] = "1"

from core.agent_router import AgentRouter  # noqa: E402
from core.flex_handler import assemble_carousel, get_flex_bubble  # noqa: E402
from skills.feedback_skill import (  # noqa: E402
    FEEDBACK_LOG_PATH,
    FIRESTORE_COLLECTION as FEEDBACK_COLLECTION,
    USE_FIRESTORE,
    check_pending_reports,
    collect_report,
)
from skills.Search_skill import _load_all_shops  # noqa: E402

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"

_BANNED_PHRASES = ["As an AI", "I will", "我是一個語言模型", "作為一個 AI"]
_TEST_USER_PREFIX = "e2e_test_user_"
_NONEXISTENT_SHOP_QUESTION = "介紹一下「海底撈拉麵宇宙總部」這家店"

SEARCH_QUESTIONS = [
    "中山區推薦的拉麵",
    "大安區有什麼豚骨拉麵",
    "信義區的拉麵",
    "中山站附近的拉麵",
]
KNOWLEDGE_QUESTIONS = [
    "豚骨拉麵跟雞白湯拉麵有什麼不同？",
    "吃拉麵有什麼禮儀需要注意？",
    "拉麵的麵條硬度可以怎麼客製化？",
    "蕎麥麵跟拉麵是同一種東西嗎？",
]
FEEDBACK_QUESTIONS = [
    "中山豚骨拉麵的地址寫錯了，應該在中山北路二段",
    "鳥人拉麵中山店的評分顯示不正確",
]


# ─── 共用 Bubble 檢查（Search／Info 共用） ──────────────────────────────────

def _check_bubble_field_alignment(shop: Dict[str, Any], bubble: Dict[str, Any]) -> List[str]:
    """檢查 bubble 文字欄位是否與來源 shop 資料精確對齊。"""
    issues = []
    body = bubble.get("body", {}).get("contents", [])
    expected_name = shop.get("name") or "未知店名"
    if not body or body[0].get("text") != expected_name:
        issues.append(f"店名不對齊：bubble='{body[0].get('text') if body else None}' "
                       f"vs shop='{expected_name}'")

    expected_loc = shop.get("location") or "未知地區"
    loc_line = body[1].get("text", "") if len(body) > 1 else ""
    if expected_loc not in loc_line:
        issues.append(f"地區欄位不對齊：bubble='{loc_line}' vs shop.location='{expected_loc}'")
    return issues


def _check_buttons(bubble: Dict[str, Any]) -> List[str]:
    """檢查 footer 按鈕（地圖 + 社群連結）格式是否合法。"""
    issues = []
    footer = bubble.get("footer", {}).get("contents", [])
    if not footer:
        issues.append("footer 無任何按鈕（缺少地圖按鈕）")
        return issues
    map_uri = footer[0].get("action", {}).get("uri", "")
    if not map_uri.startswith("https://"):
        issues.append(f"地圖按鈕 URI 非 https：{map_uri}")
    if len(footer) > 1:
        for btn in footer[1].get("contents", []):
            uri = btn.get("action", {}).get("uri", "")
            if not uri.startswith("https://"):
                issues.append(f"社群按鈕 URI 非 https：{uri}")
    return issues


def _check_hero_image(bubble: Dict[str, Any]) -> List[str]:
    """檢查 hero 圖片 URL 是否為合法 https。"""
    url = bubble.get("hero", {}).get("url", "")
    if not url.startswith("https://"):
        return [f"hero 圖片 URL 非 https：{url}"]
    return []


def _check_data_quality(shop: Dict[str, Any]) -> List[str]:
    """既有資料品質觀察，獨立列出避免與自動檢查結果混在一起。"""
    warnings = []
    if not shop.get("location"):
        warnings.append(f"「{shop.get('name')}」缺少 location 欄位，畫面會顯示「未知地區」")
    if "暫停營業" in (shop.get("name") or ""):
        warnings.append(f"「{shop.get('name')}」店名標記暫停營業，仍被推薦給使用者")
    return warnings


def _check_district_correctness(question: str, shop: Dict[str, Any]) -> List[str]:
    """若問句包含行政區名稱，檢查回傳店家的 location 是否真的屬於該行政區。"""
    import re

    m = re.search(r"([一-鿿]{2,3})(區|市|縣)", question)
    if not m:
        return []
    district_core = m.group(1)
    shop_loc = shop.get("location") or ""
    if district_core not in shop_loc:
        return [
            f"行政區比對失敗：問句指定「{district_core}{m.group(2)}」，"
            f"但店家 location='{shop_loc}'（疑似跨區誤配）"
        ]
    return []


def _check_station_distance(question: str, shop: Dict[str, Any]) -> List[str]:
    """若問句包含捷運站，檢查回傳店家是否帶有 distance_km 且在 2km 內。"""
    if "站" not in question:
        return []
    dist = shop.get("distance_km")
    if dist is None:
        return ["精確點查詢但 shop 缺少 distance_km（未走 Haversine 路徑？）"]
    if dist > 2.0:
        return [f"distance_km={dist} 超過 2km 搜尋半徑"]
    return []


# ─── Search 情境（SEARCH_BY_CRITERIA） ──────────────────────────────────────

def run_search_scenario(router: AgentRouter, questions: List[str]) -> Dict[str, Any]:
    """測試 SEARCH_BY_CRITERIA：dispatch → assemble_carousel()。"""
    cases = []
    for question in questions:
        t0 = time.time()
        result = router.dispatch(question)
        elapsed = round(time.time() - t0, 1)
        data = result.get("data", [])
        recommendations = result.get("recommendations", [])
        issues: List[str] = []
        quality_warnings: List[str] = []
        bubbles_summary = []

        if not data:
            issues.append("無任何符合店家（data 為空）")
        else:
            carousel = assemble_carousel(data, recommendations)
            bubble_list = carousel.get("contents", [])
            for i, (shop, bubble) in enumerate(zip(data, bubble_list)):
                rec_text = recommendations[i] if i < len(recommendations) else None
                shop_issues = (
                    _check_bubble_field_alignment(shop, bubble)
                    + _check_district_correctness(question, shop)
                    + _check_station_distance(question, shop)
                    + _check_buttons(bubble)
                    + _check_hero_image(bubble)
                )
                issues.extend(shop_issues)
                quality_warnings.extend(_check_data_quality(shop))
                bubbles_summary.append({
                    "name": shop.get("name"),
                    "location": shop.get("location"),
                    "style": shop.get("style"),
                    "recommendation": rec_text,
                })

        cases.append({
            "question": question,
            "elapsed_sec": elapsed,
            "intent": result.get("intent"),
            "issues": issues,
            "data_quality_warnings": quality_warnings,
            "bubbles": bubbles_summary,
            "llm_timing": result.get("timing", {}),
        })
    return {"name": "Search Skill（SEARCH_BY_CRITERIA）", "cases": cases}


# ─── Info 情境（GET_SPECIFIC_INFO） ─────────────────────────────────────────

def _pick_real_shop_questions(sample_size: int) -> List[str]:
    """從店家資料隨機取樣現有店家（排除暫停營業），組成查詢問句。

    經 _load_all_shops() 讀取，與正式搜尋同一路徑：local 讀 ramen_data.json、
    firestore 讀 ramen_shops，CI 等沒有本機資料的環境也能執行。
    """
    shops = _load_all_shops()
    candidates = [
        s["name"] for s in shops if s.get("name") and "暫停營業" not in s["name"]
    ]
    chosen = random.sample(candidates, min(sample_size, len(candidates)))
    return [f"介紹一下{name}" for name in chosen]


def run_info_scenario(router: AgentRouter, full: bool) -> Dict[str, Any]:
    """測試 GET_SPECIFIC_INFO：dispatch → get_flex_bubble()。

    輕量模式僅測 1 筆真實店家問句；--full 模式額外加測 2 筆真實店家與
    1 筆刻意不存在的店名（驗證 Places API 相似度防呆，見 PENDING.md #6）。
    """
    questions = _pick_real_shop_questions(2 if full else 1)
    if full:
        questions.append(_NONEXISTENT_SHOP_QUESTION)

    cases = []
    for question in questions:
        t0 = time.time()
        result = router.dispatch(question)
        elapsed = round(time.time() - t0, 1)
        data = result.get("data", [])
        recommendations = result.get("recommendations", [])
        issues: List[str] = []
        quality_warnings: List[str] = []
        shop_summary = None

        if data:
            shop = data[0]
            rec = recommendations[0] if recommendations else None
            bubble = get_flex_bubble(shop, rec)
            issues += _check_bubble_field_alignment(shop, bubble)
            issues += _check_buttons(bubble)
            quality_warnings += _check_data_quality(shop)
            shop_summary = {
                "name": shop.get("name"),
                "location": shop.get("location"),
                "summary": rec,
            }

        cases.append({
            "question": question,
            "elapsed_sec": elapsed,
            "intent": result.get("intent"),
            "shop_found": bool(data),
            "issues": issues,
            "data_quality_warnings": quality_warnings,
            "shop": shop_summary,
            "llm_timing": result.get("timing", {}),
        })
    return {"name": "Info Skill（GET_SPECIFIC_INFO）", "cases": cases}


# ─── Knowledge 情境（KNOWLEDGE_QUERY） ──────────────────────────────────────

def _check_knowledge_answer(message: str) -> List[str]:
    """檢查回答是否非空、是否包含 AI 自我揭露用語、長度是否合理。"""
    issues = []
    if not message or not message.strip():
        return ["回答為空字串"]
    if any(p in message for p in _BANNED_PHRASES):
        issues.append(f"回答包含 AI 自我揭露用語：{message[:30]}...")
    if len(message) < 50:
        issues.append(f"回答長度僅 {len(message)} 字，明顯低於規範的 100~200 字")
    return issues


def run_knowledge_scenario(router: AgentRouter, questions: List[str]) -> Dict[str, Any]:
    """測試 KNOWLEDGE_QUERY：dispatch → result["message"]（純文字回答）。"""
    cases = []
    for question in questions:
        t0 = time.time()
        result = router.dispatch(question)
        elapsed = round(time.time() - t0, 1)
        intent = result.get("intent")
        message = result.get("message")

        issues = []
        if intent != "KNOWLEDGE_QUERY":
            issues.append(f"非預期 intent：{intent}（預期 KNOWLEDGE_QUERY）")
        issues += _check_knowledge_answer(message or "")

        cases.append({
            "question": question,
            "elapsed_sec": elapsed,
            "intent": intent,
            "message": message,
            "issues": issues,
            "llm_timing": result.get("timing", {}),
        })
    return {"name": "Knowledge Skill（KNOWLEDGE_QUERY）", "cases": cases}


# ─── Feedback 情境（REPORT_ERROR） ──────────────────────────────────────────

def run_feedback_scenario(router: AgentRouter, questions: List[str]) -> Dict[str, Any]:
    """測試 REPORT_ERROR：dispatch → collect_report() → check_pending_reports()。

    以 e2e_test_user_ 為前綴的 user_id 寫入測試紀錄，結束後自動清除。
    """
    cases = []
    for i, question in enumerate(questions):
        test_user_id = f"{_TEST_USER_PREFIX}{i}"
        t0 = time.time()
        result = router.dispatch(question)
        elapsed = round(time.time() - t0, 1)
        intent = result.get("intent")
        data = result.get("data", [])

        issues = []
        if intent != "REPORT_ERROR":
            issues.append(f"非預期 intent：{intent}（預期 REPORT_ERROR）")

        report_info = data[0] if data else {}
        shop_name = report_info.get("shop_name")
        error_description = report_info.get("error_description", "")
        if not error_description:
            issues.append("error_description 為空")

        write_ok = collect_report(
            shop_name=shop_name, error_description=error_description, user_id=test_user_id
        )
        if not write_ok:
            issues.append("collect_report() 回傳 False，寫入失敗")

        pending = check_pending_reports()
        if not any(r.get("user_id") == test_user_id for r in pending):
            issues.append("寫入後查詢 check_pending_reports() 找不到剛寫入的回報")

        cases.append({
            "question": question,
            "elapsed_sec": elapsed,
            "intent": intent,
            "shop_name": shop_name,
            "error_description": error_description,
            "issues": issues,
            "llm_timing": result.get("timing", {}),
        })

    cleaned_up = _cleanup_test_reports()
    return {"name": "Feedback Skill（REPORT_ERROR）", "cases": cases, "cleaned_up": cleaned_up}


def _cleanup_test_reports() -> int:
    """移除本次測試寫入的回報紀錄，避免污染待處理清單。回傳移除筆數。"""
    if USE_FIRESTORE:
        return _cleanup_firestore_test_reports()
    if not os.path.exists(FEEDBACK_LOG_PATH):
        return 0
    with open(FEEDBACK_LOG_PATH, "r", encoding="utf-8") as f:
        reports: List[Dict[str, Any]] = json.load(f)
    kept = [r for r in reports if not (r.get("user_id") or "").startswith(_TEST_USER_PREFIX)]
    removed = len(reports) - len(kept)
    with open(FEEDBACK_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
    return removed


def _cleanup_firestore_test_reports() -> int:
    """移除 Firestore 測試集合中 user_id 以 e2e_test_user_ 開頭的回報。回傳移除筆數。"""
    from google.cloud.firestore_v1.base_query import FieldFilter

    from services.firestore_client import collection_name, get_db

    col = get_db().collection(collection_name(FEEDBACK_COLLECTION))
    # 前綴查詢：[prefix, prefix + \uf8ff) 涵蓋所有以 prefix 開頭的字串
    docs = (
        col.where(filter=FieldFilter("user_id", ">=", _TEST_USER_PREFIX))
        .where(filter=FieldFilter("user_id", "<", _TEST_USER_PREFIX + "\uf8ff"))
        .stream()
    )
    removed = 0
    for doc in docs:
        doc.reference.delete()
        removed += 1
    return removed


# ─── 報告組裝 ────────────────────────────────────────────────────────────────

def _fmt_llm(llm_timing: Dict[str, Any]) -> str:
    """將單筆 llm_timing 的呼叫明細組成字串，例如 'intent=0.8s, rec[0]=1.1s'。"""
    calls = llm_timing.get("llm") or []
    if not calls:
        return "—"
    return ", ".join(f"{d['name']}={d['s']}s" for d in calls)


def render_report(scenarios: List[Dict[str, Any]], full: bool) -> str:
    """將四個情境的測試結果組裝成單一 Markdown 報告。"""
    mode = "完整題庫（--full）" if full else "輕量模式（每情境 1 題）"
    lines = ["# 統一端到端測試報告", "", f"執行時間：{datetime.now():%Y-%m-%d %H:%M}",
              f"執行模式：{mode}", ""]

    total_issues = sum(len(c["issues"]) for s in scenarios for c in s["cases"])
    summary = "✅ 全部自動檢查通過" if total_issues == 0 else f"❌ 共 {total_issues} 項自動檢查失敗"
    lines.append(f"## 總結：{summary}")
    lines.append("")

    # ── 效能 KPI 匯總（重要指標）──────────────────────────────────────────
    all_cases = [c for s in scenarios for c in s["cases"]]
    elapseds = [c["elapsed_sec"] for c in all_cases]
    llm_total = round(
        sum((c.get("llm_timing") or {}).get("llm_total_s", 0) for c in all_cases), 2
    )
    llm_calls = sum((c.get("llm_timing") or {}).get("llm_count", 0) for c in all_cases)
    if elapseds:
        avg_e = round(sum(elapseds) / len(elapseds), 1)
        lines.append("## ⏱️ 效能 KPI")
        lines.append("")
        lines.append(
            f"- 端到端耗時（dispatch，含意圖解析＋Skill＋LLM）："
            f"最快 {min(elapseds)}s／最慢 {max(elapseds)}s／平均 {avg_e}s"
        )
        lines.append(f"- LLM 呼叫：共 {llm_calls} 次，累計 LLM 耗時 {llm_total}s")
        lines.append("")

    lines.append("| 情境 | 問句 | Intent | 自動檢查 | 端到端(s) | LLM(次/秒) |")
    lines.append("|---|---|---|---|---|---|")
    for s in scenarios:
        for c in s["cases"]:
            status = "✅ PASS" if not c["issues"] else f"❌ {len(c['issues'])} 項問題"
            t = c.get("llm_timing") or {}
            llm_cell = f"{t.get('llm_count', 0)}次/{t.get('llm_total_s', 0)}s"
            lines.append(
                f"| {s['name']} | {c['question']} | {c['intent']} | {status} | "
                f"{c['elapsed_sec']} | {llm_cell} |"
            )
    lines.append("")
    lines.append("---")
    lines.append("")

    for s in scenarios:
        lines.append(f"# {s['name']}")
        lines.append("")
        for c in s["cases"]:
            lines.append(f"## 問句：「{c['question']}」")
            lines.append("")
            lines.append(f"- Intent: `{c['intent']}` / 端到端耗時 {c['elapsed_sec']}s")
            t = c.get("llm_timing") or {}
            if t.get("llm"):
                lines.append(
                    f"- ⏱️ LLM 計時：{_fmt_llm(t)}"
                    f"（共 {t.get('llm_count', 0)} 次 {t.get('llm_total_s', 0)}s）"
                )
            lines.append("")
            if c["issues"]:
                lines.append("### ❌ 自動檢查發現的問題")
                for issue in c["issues"]:
                    lines.append(f"- {issue}")
                lines.append("")
            else:
                lines.append("### ✅ 自動檢查全數通過")
                lines.append("")
            if c.get("data_quality_warnings"):
                lines.append("### ⚠️ 資料品質觀察（僅供參考）")
                for w in c["data_quality_warnings"]:
                    lines.append(f"- {w}")
                lines.append("")
            if c.get("bubbles"):
                lines.append("### 店家內容（請人工確認語意是否切題）")
                lines.append("")
                for b in c["bubbles"]:
                    lines.append(f"- {b['name']}（{b['location']} · {b['style']}）："
                                  f"{b['recommendation']}")
                lines.append("")
            if c.get("shop"):
                lines.append("### 店家內容（請人工確認介紹文是否通順）")
                lines.append("")
                lines.append(f"- {c['shop']['name']}（{c['shop']['location']}）："
                              f"{c['shop']['summary']}")
                lines.append("")
            if "message" in c:
                lines.append("### 回答內容（請人工確認準確性與語氣）")
                lines.append("")
                lines.append(f"> {c['message']}")
                lines.append("")
            if "shop_name" in c and "message" not in c and not c.get("bubbles") and not c.get("shop"):
                lines.append("### 擷取內容（請人工確認 error_description 是否切題）")
                lines.append("")
                lines.append(f"- shop_name：{c['shop_name']}")
                lines.append(f"- error_description：{c['error_description']}")
                lines.append("")
            lines.append("---")
            lines.append("")
        if "cleaned_up" in s:
            lines.append(f"（測試紀錄已於結束後自動清除：{s['cleaned_up']} 筆）")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="統一端到端測試（Search/Info/Knowledge/Feedback）")
    parser.add_argument(
        "--full", action="store_true", help="每個情境跑完整題庫，預設僅隨機抽 1 題"
    )
    args = parser.parse_args()

    model_name = os.getenv("GEMINI_MODEL")
    router = AgentRouter(model_name)

    search_qs = SEARCH_QUESTIONS if args.full else [random.choice(SEARCH_QUESTIONS)]
    knowledge_qs = KNOWLEDGE_QUESTIONS if args.full else [random.choice(KNOWLEDGE_QUESTIONS)]
    feedback_qs = FEEDBACK_QUESTIONS if args.full else [random.choice(FEEDBACK_QUESTIONS)]

    scenarios = []
    try:
        print(f"{GREEN}STEP: 執行 Search Skill 情境{RESET}")
        scenarios.append(run_search_scenario(router, search_qs))

        print(f"{GREEN}STEP: 執行 Info Skill 情境{RESET}")
        scenarios.append(run_info_scenario(router, args.full))

        print(f"{GREEN}STEP: 執行 Knowledge Skill 情境{RESET}")
        scenarios.append(run_knowledge_scenario(router, knowledge_qs))

        print(f"{GREEN}STEP: 執行 Feedback Skill 情境{RESET}")
        scenarios.append(run_feedback_scenario(router, feedback_qs))
    except Exception as e:
        print(f"{RED}STEP ERROR:{e}{RESET}")
        return 1

    report = render_report(scenarios, args.full)
    out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "log")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"e2e_test_{datetime.now():%Y%m%d_%H%M}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"{GREEN}STEP: 報告已寫入 {out_path}{RESET}")

    total_issues = sum(len(c["issues"]) for s in scenarios for c in s["cases"])
    if total_issues:
        print(f"{RED}STEP: 共 {total_issues} 項自動檢查失敗，詳見報告{RESET}")
        return 1
    print(f"{GREEN}STEP: 全部自動檢查通過{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
