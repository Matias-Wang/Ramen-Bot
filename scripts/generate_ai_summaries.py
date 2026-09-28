"""
批次為 ramen_data.json 內所有店家預生成 AI 摘要欄位。

產生兩個欄位：
- search_ai_summary：約 30~60 字推薦文（RECOMMEND_PROMPT）。
- info_ai_summary  ：約 150 字列點介紹（INFO_SUMMARY_PROMPT，僅對有 description 的店家）。

此為離線工具，直接呼叫 Gemini（不經過 usage_tracker），因此不會消耗 runtime
每日 LLM 配額。完成後以下列指令同步至 Firestore（既有 sync 會依 id merge
新欄位）：

    python scripts/migrate_to_firestore.py --mode=sync

用法
----
    python scripts/generate_ai_summaries.py                # 補齊兩欄位缺少的部分
    python scripts/generate_ai_summaries.py --field search # 只補 search_ai_summary
    python scripts/generate_ai_summaries.py --field info   # 只補 info_ai_summary
    python scripts/generate_ai_summaries.py --force        # 重新生成（覆蓋既有值）
    python scripts/generate_ai_summaries.py --dry-run      # 只印出結果不寫檔
    python scripts/generate_ai_summaries.py --limit 5      # 只處理前 5 間（測試用）
    python scripts/generate_ai_summaries.py --regen-over-limit
                                                           # 只重生超過硬上限者（info=150），
                                                           # 重試至達標，絕不截斷
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)
load_dotenv()

from core.prompts import RECOMMEND_PROMPT, INFO_SUMMARY_PROMPT
from skills.Search_skill import build_shop_summary

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

DATA_PATH = os.path.join("data", "ramen_data.json")
POOL_SIZE = 6

# 各欄位對應的 prompt、輸出 token 上限，以及硬性字數上限（None = 無硬上限）。
# hard_limit 供 --regen-over-limit 判斷「超標」；search 為約值無硬上限，故不重生。
FIELD_CONFIG = {
    "search_ai_summary": {"prompt": RECOMMEND_PROMPT, "tokens": 400, "hard_limit": None},
    "info_ai_summary": {"prompt": INFO_SUMMARY_PROMPT, "tokens": 600, "hard_limit": 150},
}
# 超標重生時同一筆最多重試次數（每次 temperature=0.6 產出不同內容）
MAX_REGEN_RETRIES = 5


def _generate(client: "genai.Client", model_name: str, prompt: str,
              max_output_tokens: int) -> str:
    """
    以指定 prompt 呼叫 Gemini 並回傳清理後的純文字。

    設定與 runtime skill 一致：關閉 thinking，避免 thinking tokens 吃光輸出預算
    導致文字被硬切斷。

    Parameters
    ----------
    client : genai.Client
        Gemini client 實例。
    model_name : str
        Gemini 模型名稱。
    prompt : str
        完整 prompt 內容。
    max_output_tokens : int
        輸出 token 上限。

    Returns
    -------
    str
        生成文字，失敗或內容不合格時回傳空字串。
    """
    try:
        result = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.6,
                max_output_tokens=max_output_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        raw = (result.text or "").strip()
        raw = re.sub(r"```\w*\s*", "", raw).strip()
        if not raw or any(c in raw for c in ["I will", "As an AI"]):
            return ""
        return raw
    except Exception as e:
        print(f"{RED}STEP ERROR: Gemini 生成失敗: {e}{RESET}")
        return ""


def _process_shop(shop: dict, client: "genai.Client", model_name: str,
                  do_search: bool, do_info: bool, force: bool) -> dict:
    """
    為單一店家生成需要的 AI 摘要欄位。

    Parameters
    ----------
    shop : dict
        店家資料字典。
    client : genai.Client
        Gemini client 實例。
    model_name : str
        Gemini 模型名稱。
    do_search : bool
        是否處理 search_ai_summary。
    do_info : bool
        是否處理 info_ai_summary。
    force : bool
        為真時即使欄位已有值也重新生成。

    Returns
    -------
    dict
        本次實際新增/更新的欄位（可能為空 dict）。
    """
    updates: dict = {}
    shop_summary = build_shop_summary(shop)
    name = shop.get("name") or "未知店名"

    if do_search and (force or not (shop.get("search_ai_summary") or "").strip()):
        text = _generate(
            client, model_name,
            RECOMMEND_PROMPT.format(shop_summary=shop_summary),
            max_output_tokens=400,
        )
        if text:
            updates["search_ai_summary"] = text
            print(f"{CYAN}  [search] {name}：{text}{RESET}")

    # info_ai_summary 只對有 description（收錄於知識庫）的店家生成
    if (do_info and (shop.get("description") or "").strip()
            and (force or not (shop.get("info_ai_summary") or "").strip())):
        text = _generate(
            client, model_name,
            INFO_SUMMARY_PROMPT.format(shop_summary=shop_summary),
            max_output_tokens=600,
        )
        if text:
            updates["info_ai_summary"] = text
            print(f"{CYAN}  [info]   {name}：{text[:40]}...{RESET}")

    return updates


def _regen_field_under_limit(
    shop: dict, client: "genai.Client", model_name: str, field: str, limit: int
) -> tuple:
    """
    重生成單一超標欄位，重試至長度 ≤ limit 為止（絕不截斷）。

    每次以 temperature=0.6 重新生成故產出不同；最多重試 MAX_REGEN_RETRIES 次。
    若全部重試仍超標，保留「最短的一次」結果（仍 > limit）並回報未達標，
    交由呼叫端警示，絕不硬截斷。

    Parameters
    ----------
    shop : dict
        店家資料字典。
    client : genai.Client
        Gemini client 實例。
    model_name : str
        Gemini 模型名稱。
    field : str
        欲重生的欄位名稱。
    limit : int
        該欄位的硬性字數上限。

    Returns
    -------
    tuple
        (best_text, attempts, ok)：best_text 為採用內容（全數失敗時為 None）；
        attempts 為嘗試次數；ok 表示 best_text 是否 ≤ limit。
    """
    cfg = FIELD_CONFIG[field]
    prompt = cfg["prompt"].format(shop_summary=build_shop_summary(shop))
    best = None
    for attempt in range(1, MAX_REGEN_RETRIES + 1):
        text = _generate(client, model_name, prompt, cfg["tokens"])
        if not text:
            continue
        if best is None or len(text) < len(best):
            best = text
        if len(text) <= limit:
            return text, attempt, True
    return best, MAX_REGEN_RETRIES, False


def _run_regen(targets: list, client: "genai.Client", model_name: str,
               do_search: bool, do_info: bool) -> int:
    """
    超標重生流程：找出各欄位長度超過硬上限的店家，逐筆重生至達標（不截斷）。

    只處理 hard_limit 非 None 且被 --field 選中的欄位（search 無硬上限 → 略過）。

    Parameters
    ----------
    targets : list
        本次處理的店家清單（就地更新）。
    client : genai.Client
        Gemini client 實例。
    model_name : str
        Gemini 模型名稱。
    do_search : bool
        是否納入 search_ai_summary（無硬上限，實務上會被略過）。
    do_info : bool
        是否納入 info_ai_summary。

    Returns
    -------
    int
        實際更新的欄位筆數。
    """
    selected = {"search_ai_summary": do_search, "info_ai_summary": do_info}
    regen_fields = [
        f for f, cfg in FIELD_CONFIG.items()
        if cfg["hard_limit"] is not None and selected.get(f)
    ]
    print(f"{GREEN}STEP 2: 超標重生模式，檢查欄位 {regen_fields}{RESET}")

    changed = 0
    still_over = []
    for field in regen_fields:
        limit = FIELD_CONFIG[field]["hard_limit"]
        over_shops = [s for s in targets if len(s.get(field) or "") > limit]
        print(f"{GREEN}STEP 2: {field} 超過 {limit} 字者共 {len(over_shops)} 筆{RESET}")
        for shop in over_shops:
            name = shop.get("name") or "未知店名"
            old_len = len(shop.get(field) or "")
            best, attempts, ok = _regen_field_under_limit(
                shop, client, model_name, field, limit
            )
            if best is None:
                print(f"{RED}STEP 2 ERROR: {name} 的 {field} 重生全數失敗，保留原值{RESET}")
                continue
            shop[field] = best
            changed += 1
            if ok:
                print(f"{CYAN}  [regen] {name}：{field} {old_len}→{len(best)} 字"
                      f"（第 {attempts} 次達標）{RESET}")
            else:
                still_over.append((name, len(best)))
                print(f"{YELLOW}  [regen] {name}：{field} 重試 {attempts} 次仍 "
                      f"{len(best)} 字（>{limit}），保留最短版本且不截斷{RESET}")

    if still_over:
        print(f"{YELLOW}STEP 2: 仍有 {len(still_over)} 筆超標（未截斷，需人工檢視）："
              f"{still_over}{RESET}")
    return changed


def main() -> None:
    """批次生成主流程：解析參數 → 並行生成 → 寫回 ramen_data.json。"""
    parser = argparse.ArgumentParser(description="批次生成店家 AI 摘要欄位")
    parser.add_argument(
        "--field", choices=["search", "info", "both"], default="both",
        help="要生成的欄位，預設 both",
    )
    parser.add_argument(
        "--force", action="store_true", help="即使欄位已有值也重新生成",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只印出結果，不寫回檔案",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="只處理前 N 間店家（0 = 全部）",
    )
    parser.add_argument(
        "--regen-over-limit", action="store_true",
        help="只重生成超過硬性字數上限的欄位（info=150），重試至達標，絕不截斷",
    )
    args = parser.parse_args()

    do_search = args.field in ("search", "both")
    do_info = args.field in ("info", "both")

    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL")
    if not api_key or not model_name:
        print(f"{RED}STEP ERROR: 需設定環境變數 GEMINI_API_KEY 與 GEMINI_MODEL{RESET}")
        sys.exit(1)

    print(f"{GREEN}STEP 1: 讀取 {DATA_PATH}{RESET}")
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        shops = json.load(f)
    # targets 為 shops 的子集參照（元素為同一 dict 物件），僅處理 targets，
    # 但最終寫回完整 shops，避免 --limit 把整份資料截斷成 N 筆。
    targets = shops[: args.limit] if args.limit > 0 else shops
    print(f"{GREEN}STEP 1: 全檔 {len(shops)} 間，本次處理 {len(targets)} 間"
          f"（field={args.field}, force={args.force}, dry_run={args.dry_run}）{RESET}")

    client = genai.Client(api_key=api_key)

    _t_gen = time.time()
    changed = 0

    if args.regen_over_limit:
        changed = _run_regen(targets, client, model_name, do_search, do_info)
    else:
        print(f"{GREEN}STEP 2: 並行生成 AI 摘要（pool={POOL_SIZE}）{RESET}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=POOL_SIZE) as executor:
            future_map = {
                executor.submit(
                    _process_shop, shop, client, model_name,
                    do_search, do_info, args.force,
                ): shop
                for shop in targets
            }
            for future in concurrent.futures.as_completed(future_map):
                shop = future_map[future]
                try:
                    updates = future.result()
                except Exception as e:
                    print(f"{RED}STEP 2 ERROR: {shop.get('name')} 處理失敗: {e}{RESET}")
                    continue
                if updates:
                    shop.update(updates)
                    changed += 1

    _gen_elapsed = time.time() - _t_gen
    if args.regen_over_limit:
        print(f"{GREEN}STEP 2: 超標重生完成，共 {changed} 筆欄位更新；"
              f"耗時 {_gen_elapsed:.1f}s{RESET}")
    else:
        avg = _gen_elapsed / len(targets) if targets else 0
        print(f"{GREEN}STEP 2: 完成，共 {changed} 間店家有欄位更新；"
              f"生成總耗時 {_gen_elapsed:.1f}s（{len(targets)} 間，平均 {avg:.2f}s/間）{RESET}")

    if args.dry_run:
        print(f"{YELLOW}STEP 3: --dry-run 模式，未寫回檔案{RESET}")
        return

    if changed == 0:
        print(f"{YELLOW}STEP 3: 無任何更新，略過寫檔{RESET}")
        return

    print(f"{GREEN}STEP 3: 寫回 {DATA_PATH}{RESET}")
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(shops, f, ensure_ascii=False, indent=2)
    print(f"{GREEN}STEP 3: 完成。請執行 "
          f"`python scripts/migrate_to_firestore.py --mode=sync` 同步至 Firestore{RESET}")


if __name__ == "__main__":
    main()
