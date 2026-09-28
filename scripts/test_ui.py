"""
本地端到端測試介面（Streamlit）：模擬使用者在 LINE 上與拉麵機器人互動。

與 scripts/e2e_test.py 的差別：e2e_test 只呼叫 AgentRouter.dispatch 做自動斷言；
本介面則直接執行 app.py 的 `_reply_to_line` / `_reply_location`，也就是正式環境
背景執行緒實際跑的那一段，涵蓋意圖解析、Skill 執行、推薦文快取、Flex 組裝、
Firestore 讀寫，唯一差別是「送往 LINE 的 push_message 被攔截回傳給畫面」。

設計取捨：
- 不在 app.py 新增 /test/webhook 之類繞過簽章的路由。Dockerfile 是 `COPY . .`，
  src/ 整包進映像檔，任何繞過 X-Line-Signature 的路由都會跟著上線成為後門。
  改為本檔案在同一個 process 內 import app 並替換 `app.line_bot_api`，
  測試能力只存在於本機，且本檔案已列入 .dockerignore。
- 一律連線真實 Firestore（DATA_BACKEND=firestore）並固定開啟 E2E_TEST_MODE=1：
  - 日誌寫入 test_conversation_logs、test_feedback_reports（測試專用集合，
    見 services.firestore_client.collection_name），不混入正式使用者資料。
  - 店家摘要快取 search_ai_summary / info_ai_summary 與圖片網址仍寫回正式
    ramen_shops：兩者是店家資料本身，與誰觸發無關。
  - 所有每日配額（llm_gemini、google_maps_api、line_api）皆不計入。
    固定開啟而非逐次切換，避免回覆後才啟動的背景執行緒（圖片更新）
    在旗標還原後被計入正式配額。

執行方式：
    pip install -r requirements-dev.txt
    streamlit run scripts/test_ui.py
"""

import io
import json
import os
import random
import re
import sys
import threading
import time
import uuid
from contextlib import redirect_stdout
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))
load_dotenv(os.path.join(_ROOT, ".env"))
# 各模組在 import 當下就讀取 DATA_BACKEND 決定走本地檔案或 Firestore，
# 因此必須在 import app 之前設定；本介面的目的就是驗證真實 Firestore 路徑。
os.environ["DATA_BACKEND"] = "firestore"
# 測試流量旗標：配額不計入、日誌改寫 test_ 集合（整個行程固定開啟）
os.environ["E2E_TEST_MODE"] = "1"

# <使用者自訂變數>
RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
BLUE = '\033[94m'
CYAN = '\033[96m'
MAGAENTA = '\033[95m'
RESET = '\033[0m'

DEFAULT_USER_ID = "U_test_simulator"
FLEX_SIMULATOR_URL = "https://developers.line.biz/flex-simulator/"

# 熱門地點預設座標（緯度, 經度）
LOCATION_PRESETS: Dict[str, tuple[float, float]] = {
    "捷運中山站": (25.052685, 121.520392),
    "台北車站": (25.047760, 121.517050),
    "南港展覽館": (25.055355, 121.617520),
    "西門町": (25.042131, 121.508160),
    "捷運頂溪站（永和）": (25.013807, 121.515462),
}
CUSTOM_LOCATION = "自訂座標"

_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")


class _TeeStdout(io.TextIOBase):
    """同時寫入原本的 stdout 與記憶體緩衝，讓管線日誌在終端機與畫面上都看得到。"""

    def __init__(self, original: Any) -> None:
        self._original = original
        self.buffer_text = io.StringIO()

    def write(self, text: str) -> int:
        self._original.write(text)
        self.buffer_text.write(text)
        return len(text)

    def flush(self) -> None:
        self._original.flush()


class _CapturingLineApi:
    """
    取代 app.line_bot_api 的替身：攔截 push_message，不實際送往 LINE。

    Parameters
    ----------
    on_push : Callable[[Any], None]
        收到 PushMessageRequest 時的回呼。
    """

    def __init__(self, on_push: Callable[[Any], None]) -> None:
        self._on_push = on_push

    def push_message(
        self, push_message_request: Any, *args: Any, **kwargs: Any
    ) -> None:
        """攔截推播請求並交給回呼，簽名與 MessagingApi.push_message 相容。"""
        self._on_push(push_message_request)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"測試介面未模擬 MessagingApi.{name}()")


class PipelineHarness:
    """
    在同一個 process 內載入 app.py，並以替身攔截所有對外的 LINE 回覆。

    除 LINE 推播與 line_api 配額計數外，其餘邏輯（Gemini、Google Maps、
    Firestore）皆為正式程式碼的真實呼叫。

    Notes
    -----
    Streamlit 每次互動都會重跑整支腳本，本物件透過 st.cache_resource 保持單例；
    若本檔案修改後快取失效而重建，原始函式存放在 app 模組上，避免重複包裝。
    """

    def __init__(self) -> None:
        print(f"{GREEN}STEP 0: 載入 app.py（含 Firestore / Gemini / Maps 預熱）{RESET}")
        import app as bot_app
        from core import agent_router, timing
        from core.conversation_logger import _INTENT_TO_SKILL
        from linebot.v3.webhooks import Event
        from skills import Search_skill

        self._app = bot_app
        self._agent_router = agent_router
        self._timing = timing
        self._intent_to_skill = _INTENT_TO_SKILL
        self._event_cls = Event
        self._lock = threading.Lock()
        self._capture: Optional[Dict[str, Any]] = None

        originals = getattr(bot_app, "_TEST_UI_ORIGINALS", None)
        if originals is None:
            originals = {
                "check_and_increment": bot_app.check_and_increment,
                "dispatch": bot_app.router.dispatch,
                "parse_intent": bot_app.router._parse_intent_json,
                "generate_recommendations": Search_skill.generate_recommendations,
                "summarize_description": Search_skill.summarize_description,
            }
            bot_app._TEST_UI_ORIGINALS = originals
        self._originals = originals
        self._install_patches()
        print(f"{GREEN}STEP 0: 測試替身安裝完成，LINE 推播已改為攔截{RESET}")

    # --- 替身安裝 ---

    def _install_patches(self) -> None:
        """替換 LINE 推播、line_api 配額，並包裝意圖解析與快取檢查以蒐集除錯資訊。"""
        bot_app = self._app
        orig = self._originals

        bot_app.line_bot_api = _CapturingLineApi(self._on_push)

        def _quota(key: str, count: int = 1) -> bool:
            # app.py 只對 line_api 計數；本介面不會真的送出 LINE 訊息，不可佔用配額
            if key == "line_api":
                if self._capture is not None:
                    self._capture["line_api_skipped"] += count
                print(f"{YELLOW}STEP: 測試介面攔截推播，line_api 不計入配額（{count} 則）{RESET}")
                return True
            return orig["check_and_increment"](key, count)

        bot_app.check_and_increment = _quota

        def _parse_intent(response: Any) -> dict:
            intent_data = orig["parse_intent"](response)
            if self._capture is not None:
                self._capture["intent_data"] = dict(intent_data)
            return intent_data

        def _dispatch(*args: Any, **kwargs: Any) -> dict:
            result = orig["dispatch"](*args, **kwargs)
            if self._capture is not None:
                self._capture["dispatch_result"] = result
            return result

        bot_app.router._parse_intent_json = _parse_intent
        bot_app.router.dispatch = _dispatch

        def _generate_recommendations(
            shops_info: List[Dict[str, Any]],
            client: Any,
            model_name: str,
            num_shops: int = 3,
        ) -> List[str]:
            # 與 Search_skill.generate_recommendations 相同的快取判準：
            # 取前 num_shops 間，search_ai_summary 非空即命中
            self._note_cache("search_ai_summary", (shops_info or [])[:num_shops])
            return orig["generate_recommendations"](
                shops_info, client, model_name, num_shops=num_shops
            )

        def _summarize_description(
            shop: Dict[str, Any], client: Any, model_name: str
        ) -> str:
            self._note_cache("info_ai_summary", [shop])
            return orig["summarize_description"](shop, client, model_name)

        # 兩個呼叫端都是 `from ... import` 取得函式參照，須各自替換
        self._agent_router.generate_recommendations = _generate_recommendations
        self._agent_router.summarize_description = _summarize_description
        bot_app.generate_recommendations = _generate_recommendations

    def _on_push(self, request: Any) -> None:
        """記錄被攔截的 PushMessageRequest（轉為 LINE API 實際收到的 JSON 結構）。"""
        if self._capture is None:
            return
        for message in request.messages:
            self._capture["messages"].append(message.to_dict())

    def _note_cache(self, field: str, shops: List[Dict[str, Any]]) -> None:
        """記錄每間店的 AI 摘要快取命中狀況（於呼叫原函式之前檢查）。"""
        if self._capture is None:
            return
        for shop in shops:
            self._capture["cache_checks"].append({
                "shop": shop.get("name") or "不明店名",
                "field": field,
                "hit": bool((shop.get(field) or "").strip()),
            })

    # --- LINE Webhook Payload 組裝 ---

    @staticmethod
    def _build_payload(user_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        組裝與 LINE 平台送達 /callback 相同結構的 Webhook body。

        Parameters
        ----------
        user_id : str
            模擬的 LINE 使用者 ID。
        message : Dict[str, Any]
            message 物件（text 或 location）。

        Returns
        -------
        Dict[str, Any]
            Webhook body（含單一 MessageEvent）。
        """
        return {
            "destination": "U_test_bot_destination",
            "events": [{
                "type": "message",
                "mode": "active",
                "timestamp": int(time.time() * 1000),
                "webhookEventId": uuid.uuid4().hex.upper()[:26],
                "deliveryContext": {"isRedelivery": False},
                "replyToken": uuid.uuid4().hex,
                "source": {"type": "user", "userId": user_id},
                "message": message,
            }],
        }

    def _parse_event(self, payload: Dict[str, Any]) -> Any:
        """以 LINE SDK 相同的反序列化方式（Event.from_dict）解析事件。"""
        return self._event_cls.from_dict(payload["events"][0])

    @staticmethod
    def _message_id() -> str:
        """
        產生與 LINE 格式相近的 18 位數訊息 ID。

        Returns
        -------
        str
            隨機訊息 ID。
        """
        return str(random.randint(10**17, 10**18 - 1))

    # --- 對外介面 ---

    def send_text(self, user_id: str, text: str) -> Dict[str, Any]:
        """
        模擬使用者傳送文字訊息，走 app._reply_to_line 完整管線。

        Parameters
        ----------
        user_id : str
            模擬的 LINE 使用者 ID。
        text : str
            使用者輸入文字。

        Returns
        -------
        Dict[str, Any]
            本次呼叫的回覆訊息與除錯資訊。
        """
        payload = self._build_payload(user_id, {
            "id": self._message_id(),
            "type": "text",
            "text": text,
            "quoteToken": uuid.uuid4().hex,
        })
        event = self._parse_event(payload)
        # 以下欄位萃取與 app.handle_message 一致
        received_at = self._timing.webhook_received_at(event.timestamp)
        current_time = datetime.fromtimestamp(
            event.timestamp / 1000, tz=self._app.TAIPEI_TZ
        ).strftime("%Y-%m-%d %H:%M")
        event_user_id = event.source.user_id
        user_text = event.message.text

        def _run() -> None:
            self._app._reply_to_line(
                user_text, event_user_id, current_time, received_at
            )

        return self._run("text", payload, _run)

    def send_location(
        self,
        user_id: str,
        latitude: float,
        longitude: float,
        address: str,
    ) -> Dict[str, Any]:
        """
        模擬使用者分享位置，走 app._reply_location 完整管線。

        Parameters
        ----------
        user_id : str
            模擬的 LINE 使用者 ID。
        latitude : float
            緯度。
        longitude : float
            經度。
        address : str
            位置訊息附帶的地址文字（LINE 位置訊息的 address 欄位）。

        Returns
        -------
        Dict[str, Any]
            本次呼叫的回覆訊息與除錯資訊。
        """
        payload = self._build_payload(user_id, {
            "id": self._message_id(),
            "type": "location",
            "title": "位置訊息",
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
        })
        event = self._parse_event(payload)
        # 以下欄位萃取與 app.handle_location 一致
        received_at = self._timing.webhook_received_at(event.timestamp)
        event_user_id = event.source.user_id
        lat = event.message.latitude
        lng = event.message.longitude

        def _run() -> None:
            self._app._reply_location(lat, lng, event_user_id, received_at)

        return self._run("location", payload, _run)

    def _run(
        self,
        kind: str,
        payload: Dict[str, Any],
        fn: Callable[[], None],
    ) -> Dict[str, Any]:
        """
        在鎖內執行單次管線呼叫，並整理成畫面需要的結果結構。

        Parameters
        ----------
        kind : str
            訊息類型（"text" 或 "location"）。
        payload : Dict[str, Any]
            本次模擬的 LINE Webhook body。
        fn : Callable[[], None]
            實際執行 app 回覆流程的函式。

        Returns
        -------
        Dict[str, Any]
            本次呼叫的回覆訊息與除錯資訊（見 _summarize）。
        """
        with self._lock:
            self._capture = {
                "messages": [],
                "intent_data": None,
                "dispatch_result": None,
                "cache_checks": [],
                "line_api_skipped": 0,
            }
            # 先開一個空收集器，避免讀到上一次請求殘留的計時紀錄
            self._timing.begin_collection()
            tee = _TeeStdout(sys.stdout)
            error = None
            t0 = time.time()
            print(f"{GREEN}STEP 1: 執行 {kind} 訊息管線{RESET}")
            try:
                with redirect_stdout(tee):
                    fn()
            except Exception as e:
                error = str(e)
                print(f"{RED}STEP 1 ERROR:{e}{RESET}")
            finally:
                elapsed = time.time() - t0
                records = self._timing.current_records()
                capture = self._capture
                self._capture = None

        return self._summarize(kind, payload, capture, records, elapsed, tee, error)

    def _summarize(
        self,
        kind: str,
        payload: Dict[str, Any],
        capture: Dict[str, Any],
        records: Any,
        elapsed: float,
        tee: _TeeStdout,
        error: Optional[str],
    ) -> Dict[str, Any]:
        """
        把攔截到的資料整理成大腦決策、快取、效能三類除錯資訊。

        Parameters
        ----------
        kind : str
            訊息類型（"text" 或 "location"）。
        payload : Dict[str, Any]
            本次模擬的 LINE Webhook body。
        capture : Dict[str, Any]
            攔截器收集到的訊息、意圖與快取紀錄。
        records : Any
            timing 模組的計時收集器。
        elapsed : float
            端到端耗時（秒）。
        tee : _TeeStdout
            本輪管線輸出的緩衝。
        error : Optional[str]
            執行失敗時的錯誤訊息，成功為 None。

        Returns
        -------
        Dict[str, Any]
            畫面渲染所需的結果結構。
        """
        if kind == "location":
            message = payload["events"][0]["message"]
            intent = "LOCATION"
            skill = "Search_skill.filter_by_location"
            args = {"latitude": message["latitude"], "longitude": message["longitude"]}
            ui_tag = "CAROUSEL"
        else:
            result = capture["dispatch_result"] or {}
            intent_data = capture["intent_data"] or {}
            intent = result.get("intent", "ERROR")
            skill = self._intent_to_skill.get(intent, "（未進入 Skill）")
            # 與 conversation_logger 相同：參數排除意圖與 UI 標籤本身
            args = {
                k: v for k, v in intent_data.items() if k not in ("intent", "ui_tag")
            }
            ui_tag = result.get("ui_tag")

        checks = capture["cache_checks"]
        cache_hit = all(c["hit"] for c in checks) if checks else None
        return {
            "kind": kind,
            "payload": payload,
            "messages": capture["messages"],
            "latency_s": round(elapsed, 2),
            "kpi": self._timing.snapshot(records, elapsed),
            "intent": intent,
            "skill": skill,
            "args": args,
            "ui_tag": ui_tag,
            "cache_checks": checks,
            "cache_hit": cache_hit,
            "line_api_skipped": capture["line_api_skipped"],
            "log": _ANSI_PATTERN.sub("", tee.buffer_text.getvalue()),
            "error": error,
        }


# === Streamlit 畫面 ===


@st.cache_resource(show_spinner="載入機器人管線中（首次約需 10~30 秒預熱）…")
def get_harness() -> PipelineHarness:
    """建立並快取 PipelineHarness 單例。"""
    return PipelineHarness()


def _escape_markdown(text: str) -> str:
    """跳脫 Markdown 特殊字元，讓機器人原文照實顯示，並保留換行。"""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text).replace("\n", "  \n")


def _copy_button(text: str, label: str) -> None:
    """
    渲染一鍵複製按鈕（瀏覽器端 Clipboard API，失敗時退回 execCommand）。

    Parameters
    ----------
    text : str
        要複製的文字。
    label : str
        按鈕文字。
    """
    # 以 JSON 字串字面值嵌入，並跳脫 </ 避免內容提前結束 <script>
    js_text = json.dumps(text, ensure_ascii=False).replace("</", "<\\/")
    components.html(
        f"""
<button id="copy" style="padding:6px 14px;border-radius:6px;border:1px solid #06C755;
  background:#06C755;color:#fff;font-size:14px;cursor:pointer">{label}</button>
<span id="status" style="margin-left:8px;font:13px sans-serif;color:#888"></span>
<script>
const text = {js_text};
const status = document.getElementById("status");
document.getElementById("copy").onclick = async () => {{
  try {{
    await navigator.clipboard.writeText(text);
    status.textContent = "已複製";
  }} catch (e) {{
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    status.textContent = ok ? "已複製" : "複製失敗，請改用下方 JSON 區塊右上角的複製圖示";
  }}
}};
</script>
""",
        height=44,
    )


def _render_bot_message(message: Dict[str, Any]) -> None:
    """
    依 LINE 訊息型別渲染單則機器人回覆。

    Parameters
    ----------
    message : Dict[str, Any]
        攔截到的 LINE 訊息（to_dict 後的 camelCase 結構）。
    """
    msg_type = message.get("type")
    if msg_type == "text":
        st.markdown(_escape_markdown(message.get("text", "")))
        items = (message.get("quickReply") or {}).get("items") or []
        if items:
            labels = "、".join(
                f"［{(i.get('action') or {}).get('label', '?')}］" for i in items
            )
            st.caption(f"Quick Reply：{labels}")
    elif msg_type == "flex":
        contents = message.get("contents") or {}
        is_carousel = contents.get("type") == "carousel"
        count = len(contents.get("contents") or []) if is_carousel else 1
        st.markdown(
            f"**Flex Message**（{contents.get('type', '?')}，{count} 張）"
            f" · altText：{_escape_markdown(message.get('altText', ''))}"
        )
        st.json(contents, expanded=False)
        flex_text = json.dumps(contents, ensure_ascii=False, indent=2)
        _copy_button(flex_text, "📋 一鍵複製 Flex JSON")
        with st.expander("Flex JSON 原文"):
            st.caption(
                f"貼到 [LINE Flex Message Simulator]({FLEX_SIMULATOR_URL})"
                " 的 View as JSON"
            )
            st.code(flex_text, language="json")
    else:
        st.json(message)


def _render_debug(turn: Dict[str, Any]) -> None:
    """
    渲染單次呼叫的除錯與效能面板。

    Parameters
    ----------
    turn : Dict[str, Any]
        對話紀錄中的單輪資料（含 result）。
    """
    result = turn["result"]
    if result["error"]:
        st.error(f"測試介面執行錯誤：{result['error']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("端到端耗時", f"{result['latency_s']:.2f}s")
    cache_label = {True: "True", False: "False", None: "N/A"}[result["cache_hit"]]
    col2.metric("摘要快取命中", cache_label)
    col3.metric("LLM 呼叫", f"{result['kpi']['llm_count']} 次")

    st.markdown("**大腦決策**")
    st.json({
        "intent": result["intent"],
        "skill": result["skill"],
        "ui_tag": result["ui_tag"],
        "arguments": result["args"],
    })

    st.markdown("**摘要快取（search_ai_summary / info_ai_summary）**")
    if result["cache_checks"]:
        st.dataframe(result["cache_checks"], hide_index=True, width="stretch")
    else:
        st.caption("本次未查詢摘要快取（知識問答、回報、要求位置或查無店家）。")

    st.markdown("**LLM / 配額 I/O 耗時明細**")
    if result["kpi"]["llm"]:
        st.dataframe(result["kpi"]["llm"], hide_index=True, width="stretch")
    else:
        st.caption("無計時紀錄。")
    st.caption(
        f"LLM 合計 {result['kpi']['llm_total_s']}s；"
        f"攔截推播 {result['line_api_skipped']} 則（未計入 line_api 配額）"
    )

    with st.expander("模擬的 LINE Webhook Payload"):
        st.json(result["payload"])
    with st.expander("攔截到的 push_message（原始 JSON）"):
        st.json(result["messages"])
    with st.expander("管線日誌"):
        st.code(result["log"] or "（無輸出）", language="text")


def _apply_preset() -> None:
    """下拉選單切換預設地點時，自動帶入對應經緯度。"""
    preset = st.session_state["preset"]
    if preset in LOCATION_PRESETS:
        st.session_state["lat"], st.session_state["lng"] = LOCATION_PRESETS[preset]


def main() -> None:
    """Streamlit 進入點。"""
    st.set_page_config(page_title="Ramen-Bot 測試台", page_icon="🍜", layout="wide")

    state = st.session_state
    state.setdefault("history", [])
    first_preset = next(iter(LOCATION_PRESETS))
    state.setdefault("preset", first_preset)
    state.setdefault("lat", LOCATION_PRESETS[first_preset][0])
    state.setdefault("lng", LOCATION_PRESETS[first_preset][1])

    try:
        harness = get_harness()
    except Exception as e:
        print(f"{RED}STEP 0 ERROR:{e}{RESET}")
        st.error(f"載入機器人管線失敗：{e}")
        st.info("請確認 .env 內 GEMINI_API_KEY、GOOGLE_CLOUD_PROJECT_ID 等設定，"
                "以及本機已完成 `gcloud auth application-default login`。")
        st.stop()

    # --- 側邊欄：模擬控制面板 ---
    with st.sidebar:
        st.header("🍜 模擬控制面板")
        user_id = (
            st.text_input("User ID", value=DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
        )

        st.subheader("📍 模擬定位")
        st.selectbox(
            "熱門地點",
            [*LOCATION_PRESETS, CUSTOM_LOCATION],
            key="preset",
            on_change=_apply_preset,
        )
        st.number_input(
            "Latitude", -90.0, 90.0, key="lat", step=0.0001, format="%.6f"
        )
        st.number_input(
            "Longitude", -180.0, 180.0, key="lng", step=0.0001, format="%.6f"
        )
        send_location = st.button("📍 發送位置訊息", width="stretch")

        st.divider()
        st.caption(
            f"Firestore：`{os.getenv('GOOGLE_CLOUD_PROJECT_ID', '?')}` / "
            f"`{os.getenv('FIRESTORE_DATABASE', '(default)')}`  \n"
            "測試日誌寫入 `test_conversation_logs`、`test_feedback_reports`；  \n"
            "店家 `search_ai_summary` / `info_ai_summary` 快取寫回正式 "
            "`ramen_shops`；配額皆不計入。"
        )
        if st.button("🗑️ 清除對話紀錄", width="stretch"):
            state["history"] = []

    user_text = st.chat_input("輸入訊息，例如「附近的拉麵」「永和豚骨推薦」")

    # --- 送出請求 ---
    if send_location:
        lat, lng = float(state["lat"]), float(state["lng"])
        preset = state["preset"]
        is_preset = LOCATION_PRESETS.get(preset) == (lat, lng)
        address = preset if is_preset else CUSTOM_LOCATION
        with st.spinner(f"處理位置訊息（{address}）…"):
            result = harness.send_location(user_id, lat, lng, address)
        state["history"].append({
            "user_id": user_id,
            "display": f"📍 {address}（{lat:.6f}, {lng:.6f}）",
            "result": result,
        })
    elif user_text:
        with st.spinner("機器人思考中…"):
            result = harness.send_text(user_id, user_text)
        state["history"].append(
            {"user_id": user_id, "display": user_text, "result": result}
        )

    # --- 對話歷史 & 除錯面板 ---
    chat_col, debug_col = st.columns([3, 2], gap="large")
    history: List[Dict[str, Any]] = state["history"]

    with chat_col:
        st.subheader("💬 對話")
        if not history:
            st.caption("從下方輸入框送出文字，或從側邊欄發送位置訊息開始測試。")
        for turn in history:
            with st.chat_message("user"):
                st.caption(turn["user_id"])
                st.markdown(_escape_markdown(turn["display"]))
            with st.chat_message("assistant", avatar="🍜"):
                messages = turn["result"]["messages"]
                if not messages:
                    st.caption("（機器人沒有推播任何訊息，請看除錯面板的管線日誌）")
                for message in messages:
                    _render_bot_message(message)
                st.caption(f"⏱ {turn['result']['latency_s']:.2f}s")

    with debug_col:
        st.subheader("🔧 除錯與效能")
        if history:
            options = list(range(len(history)))
            selected = st.selectbox(
                "檢視第幾輪",
                options,
                index=len(options) - 1,
                format_func=lambda i: f"#{i + 1} {history[i]['display'][:20]}",
            )
            _render_debug(history[selected])
        else:
            st.caption("尚無呼叫紀錄。")


main()
