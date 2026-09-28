"""
稽核 ramen_data.json 中 description 與店家名稱是否錯置

背景：2026-06-15 曾發生「麒麟創作拉麵坊」的 description 內容其實是
「木麒麟拉麵」的 IG 食記（build_new_shops.py 擷取時對應到錯誤的貼文），
導致使用者詢問特定店家時看到完全不相關的介紹文。該筆資料已人工修正，
但當時沒有任何機制能自動抓出這類「內容錯置」問題，全靠使用者回報才發現。

原理：本專案的 description 慣例以「【店名】」開頭。對每筆店家，比對這個
方括號內的店名與 shop['name'] 的相似度（self_score）；同時找出資料庫中
「方括號內店名」比對最相似的其他店家（best_other_score，排除相同
place_id 的候選——同一 place_id 的多筆記錄是刻意保留的同店不同口味/
造訪紀錄，例如「拉麵公子」與「拉麵公子(暫停營業)」，並非錯置）。若某間
「其他店家」比自己本身更像方括號內寫的店名，且對方分數夠高，就代表這筆
description 很可能是別間店的內容，列為可疑錯置，供人工/Claude 複查
（本工具僅列出候選，不自動修改）。

使用方式：
    python scripts/check_description_shop_match.py
"""

import json
import os
import re
import difflib

DATA_PATH = os.path.join("data", "ramen_data.json")

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

# 對方分數需達此門檻，才視為「確實對應到某間登記在庫的店家」
OTHER_MATCH_THRESHOLD = 0.8
# 對方分數需比自己高出此差距，才視為「比自己更像」，避免同名相似店家誤判
MARGIN = 0.15

BRACKET_PATTERN = re.compile(r"^\s*【([^】]+)】")


def _load_shops() -> list[dict]:
    """
    讀取本地店家資料。

    Returns
    -------
    list[dict]
        店家清單，讀取失敗時回傳空清單。
    """
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"{RED}STEP ERROR: 讀取 {DATA_PATH} 失敗: {e}{RESET}")
        return []


def check_mismatches(shops: list[dict]) -> list[dict]:
    """
    掃描所有店家，找出 description 開頭店名與自身不符、且更像其他店家的可疑筆數。

    Parameters
    ----------
    shops : list[dict]
        店家清單。

    Returns
    -------
    list[dict]
        可疑錯置清單，每筆含 name、bracket_name、best_other_name、
        self_score、best_other_score。
    """
    names = [s.get("name") or "" for s in shops]
    place_ids = [s.get("place_id") for s in shops]
    flags = []

    for i, shop in enumerate(shops):
        name = shop.get("name") or ""
        place_id = shop.get("place_id")
        desc = shop.get("description") or ""
        m = BRACKET_PATTERN.match(desc)
        if not m:
            continue
        bracket_name = m.group(1).strip()

        self_score = difflib.SequenceMatcher(None, name, bracket_name).ratio()

        best_other_name, best_other_score = None, 0.0
        for j, other_name in enumerate(names):
            if j == i:
                continue
            # 同一 place_id 是刻意保留的同店多筆記錄，不算錯置候選
            if place_id and place_ids[j] == place_id:
                continue
            score = difflib.SequenceMatcher(None, other_name, bracket_name).ratio()
            if score > best_other_score:
                best_other_name, best_other_score = other_name, score

        if (
            best_other_score >= OTHER_MATCH_THRESHOLD
            and best_other_score - self_score >= MARGIN
        ):
            flags.append(
                {
                    "name": name,
                    "bracket_name": bracket_name,
                    "best_other_name": best_other_name,
                    "self_score": round(self_score, 2),
                    "best_other_score": round(best_other_score, 2),
                }
            )

    return flags


if __name__ == "__main__":
    print(f"{GREEN}STEP 1: 載入 {DATA_PATH}{RESET}")
    shops = _load_shops()
    if not shops:
        exit(1)

    print(f"{GREEN}STEP 2: 比對 description 開頭店名與 shop.name{RESET}")
    flags = check_mismatches(shops)

    if not flags:
        print(f"{CYAN}  => 未發現可疑的 description 錯置，共檢查 {len(shops)} 筆。{RESET}")
    else:
        print(f"{YELLOW}  => 發現 {len(flags)} 筆可疑錯置，需人工/Claude 複查：{RESET}")
        for f in flags:
            print(
                f"{YELLOW}  - 店家「{f['name']}」的 description 寫著「{f['bracket_name']}」，"
                f"但更像另一間登記店家「{f['best_other_name']}」"
                f"（自身分數 {f['self_score']} vs 對方分數 {f['best_other_score']}）{RESET}"
            )
        print(
            f"{CYAN}\n提醒：以上僅為候選，需人工核對是否真的是內容錯置，"
            f"或只是同品牌不同分店/狀態的店家（例如「OO」與「OO(暫停營業)」）。{RESET}"
        )
