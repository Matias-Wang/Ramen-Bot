"""
記錄每位使用者近期已被推薦過的店家，供搜尋時排除，避免連續查詢拿到同樣三間。

情境：使用者追問「還有其他的台北拉麵嗎？」（2026-08-11 正式環境日誌），
`filter_ramen_data` 的 `random.sample` 不知道上一輪推過什麼，可能原封不動
再抽到同樣三間。

**刻意只做記憶體內的實作，兩種 DATA_BACKEND 皆同**：
本模組與 `message_dedup` 的需求不同——去重若跨實例失效會導致 Gemini 重複計費，
必須正確；而「避免重複推薦」失效的代價僅是使用者看到重複結果。生產環境
`maxScale=3`，同一使用者的連續訊息可能落在不同實例而讀不到記錄，屬可接受的
降級。若改用 Firestore，等於在每次搜尋的熱路徑上多一次讀寫，不划算。
"""

import time
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Set

# <使用者自訂變數>
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"

# 記錄保存時長。只需涵蓋一次對話中的連續追問，過期即忘——否則使用者隔天
# 查同一個地區時會莫名其妙被排除掉上次看過的店。
_TTL_SECONDS = 1800

# 上限與淘汰策略同 message_dedup：以 OrderedDict 當有序集合，超過上限淘汰最舊。
_MAX_USERS = 1000
_recent: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def shop_key(shop: Dict[str, Any]) -> str:
    """
    取得店家的識別鍵，用於判斷「是不是同一家店」。

    以 `place_id` 優先，與 `Search_skill._dedupe_by_place_id()` 對齊——
    `ramen_data.json` 是**以料理為單位**而非以店家為單位，同一家店會有多筆
    口味變體（實測 33 組同 place_id 多筆、其中 11 組連座標都不同），且
    `_dedupe_by_place_id` 在不同查詢路徑下可能留下不同的變體列。
    若改用 `id` 當鍵，使用者先問「中山站」再問「中山區」時，同一家店會以
    不同 `id` 出現而逃過排除。`place_id` 缺漏時才退回 `id` / `name`。

    Parameters
    ----------
    shop : Dict[str, Any]
        店家資料字典。

    Returns
    -------
    str
        店家識別鍵；`place_id`、`id`、`name` 皆缺時回傳空字串。
    """
    return str(shop.get("place_id") or shop.get("id") or shop.get("name") or "")


def get_recent_shop_keys(user_id: str) -> Set[str]:
    """
    取得該使用者近期已被推薦過的店家鍵。

    Parameters
    ----------
    user_id : str
        LINE 使用者 ID。

    Returns
    -------
    Set[str]
        店家識別鍵集合；無記錄或已過期則為空集合。
    """
    if not user_id:
        return set()
    entry = _recent.get(user_id)
    if entry is None:
        return set()
    if time.time() - entry["at"] > _TTL_SECONDS:
        _recent.pop(user_id, None)
        return set()
    return set(entry["keys"])


def record_shown_shops(user_id: str, shops: Iterable[Dict[str, Any]]) -> None:
    """
    記錄本次推薦給該使用者的店家，覆寫先前記錄。

    只保留最近一次的結果，不做累積——累積會讓使用者反覆查詢同一地區時
    可選店家越來越少，最終無店可推。

    Parameters
    ----------
    user_id : str
        LINE 使用者 ID。
    shops : Iterable[Dict[str, Any]]
        本次回覆給使用者的店家列表。
    """
    if not user_id:
        return
    keys = [k for k in (shop_key(s) for s in shops) if k]
    if not keys:
        return
    _recent.pop(user_id, None)
    _recent[user_id] = {"keys": keys, "at": time.time()}
    if len(_recent) > _MAX_USERS:
        _recent.popitem(last=False)  # 淘汰最舊的一筆
    print(f"{CYAN}[RECENT] 已記錄使用者近期推薦 {len(keys)} 間{RESET}")


def exclude_recent(
    user_id: str, shops: List[Dict[str, Any]], keep_at_least: int = 3
) -> List[Dict[str, Any]]:
    """
    從候選店家中排除該使用者近期已看過的，數量不足時放棄排除。

    排除後若剩不到 `keep_at_least` 間，直接回傳原始清單——寧可讓使用者看到
    重複店家，也不要因為排除而少給結果。

    Parameters
    ----------
    user_id : str
        LINE 使用者 ID。
    shops : List[Dict[str, Any]]
        候選店家列表。
    keep_at_least : int
        排除後至少須保留的店家數，低於此數則不套用排除。

    Returns
    -------
    List[Dict[str, Any]]
        排除後的店家列表；不套用排除時為原列表。
    """
    recent = get_recent_shop_keys(user_id)
    if not recent:
        return shops
    remaining = [s for s in shops if shop_key(s) not in recent]
    if len(remaining) < keep_at_least:
        print(f"{GREEN}STEP: 排除近期推薦後僅剩 {len(remaining)} 間，"
              f"不足 {keep_at_least} 間，本次不套用排除{RESET}")
        return shops
    print(f"{GREEN}STEP: 已排除近期推薦過的 {len(shops) - len(remaining)} 間店家{RESET}")
    return remaining
