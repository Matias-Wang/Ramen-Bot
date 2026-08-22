# === 執行模式開關（改由環境變數 LINE_TAG 控制，定義於 load_dotenv 之後）===
# LINE_TAG = 1: 正式模式 (LINE + ngrok)
# LINE_TAG = 0: 地端測試模式 (終端機輸出 + temp.json)

# === imports ===
import os
import json
import time
import threading
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
# 發訊型別（linebot.v3.messaging）與收訊型別（linebot.v3.webhooks）務必分清楚：
# v3 的 TextMessage / LocationMessage 是「要送出的」訊息，
# 而「收到的」訊息叫 TextMessageContent / LocationMessageContent。
# 兩邊同名但語意相反，混用不會 import 失敗，只會讓 handler 收不到訊息。
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
    QuickReply,
    QuickReplyItem,
    LocationAction,
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
)
from dotenv import load_dotenv

# === 外部模組匯入 ===
from core.agent_router import AgentRouter
from core.flex_handler import assemble_carousel, get_flex_bubble
from core import timing
from skills.Search_skill import (
    filter_by_location,
    generate_recommendations,
    refresh_shop_images_async,
    resolve_shop_images,
)
from core.message_dedup import is_duplicate_message
from core.usage_tracker import check_and_increment
from core.obs import emit_metric
from skills.feedback_skill import collect_report, check_pending_reports
from datetime import datetime, timezone, timedelta

# === 自定義變數 ===
RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
BLUE = '\033[94m'
CYAN = '\033[96m'
MAGAENTA = '\033[95m'
RESET = '\033[0m'

# 載入環境變數 (.env)
load_dotenv()

# 執行模式：預設 0（地端測試）；正式部署以環境變數 LINE_TAG=1 覆寫。
LINE_TAG = int(os.getenv("LINE_TAG", "0"))

# 初始化 Flask App
app = Flask(__name__)

# 初始化 LINE Bot API 與 Webhook Handler
# ApiClient 刻意採「模組層級長生命週期」而非官方範例的 per-request
# `with ApiClient(...)`：本服務的 push 全部發生在背景 daemon thread，
# 每次請求重建 client 會讓下方的連線預熱失效，第一次 push 的冷連線成本
# （實測約 22 秒）會回來。底層 urllib3 連線池本身為 thread-safe。
configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
_line_api_client = ApiClient(configuration)
line_bot_api = MessagingApi(_line_api_client)
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 初始化 AgentRouter：負責意圖解析與技能分發
router = AgentRouter(os.getenv('GEMINI_MODEL'))

# Firestore 店家快取預熱（減少第一次請求延遲）
try:
    from skills.Search_skill import _load_all_shops
    _load_all_shops()
    print(f"{GREEN}[STARTUP] Firestore 店家快取預熱完成{RESET}")
except Exception as e:
    print(f"{YELLOW}[STARTUP] Firestore 預熱失敗（非致命）: {e}{RESET}")

# Gemini API 連線預熱（降低第一次意圖解析延遲）
try:
    from google.genai import types as _genai_types
    router.client.models.generate_content(
        model=router.model_name,
        contents="hi",
        config=_genai_types.GenerateContentConfig(max_output_tokens=1),
    )
    print(f"{GREEN}[STARTUP] Gemini API 連線預熱完成{RESET}")
except Exception as e:
    print(f"{YELLOW}[STARTUP] Gemini API 連線預熱失敗（非致命）: {e}{RESET}")

# Google Maps Geocoding 連線預熱（降低第一次地理搜尋延遲）
try:
    from skills.Search_skill import _get_latlng_cached
    _get_latlng_cached("台北市")
    print(f"{GREEN}[STARTUP] Google Maps Geocoding 連線預熱完成{RESET}")
except Exception as e:
    print(f"{YELLOW}[STARTUP] Google Maps Geocoding 預熱失敗（非致命）: {e}{RESET}")

# LINE API 連線預熱（降低第一次 push_message 延遲）
# 舊版呼叫的 get_quota() 在目前 linebot SDK 已不存在（正確方法為
# get_message_quota()），導致 AttributeError 被吞掉、預熱從未真正執行，
# 造成正式環境第一次 push_message 需承擔冷連線成本（實測約 22 秒）。
try:
    line_bot_api.get_message_quota()
    print(f"{GREEN}[STARTUP] LINE API 連線預熱完成{RESET}")
except Exception as e:
    print(f"{YELLOW}[STARTUP] LINE API 連線預熱失敗（非致命）: {e}{RESET}")

# 推薦文 Gemini Client Pool 建立並同時預熱（3 個獨立 client，確保 STEP 3 真並行）
try:
    from skills.Search_skill import init_rec_client_pool
    init_rec_client_pool(
        api_key=os.getenv("GEMINI_API_KEY", ""),
        model_name=os.getenv("GEMINI_MODEL", ""),
    )
except Exception as e:
    print(f"{YELLOW}[STARTUP] 推薦文 Client Pool 預熱失敗（非致命）: {e}{RESET}")

# 回報佇列待處理確認（啟動時自動列出，提醒開發者有待處理的使用者回報）
try:
    check_pending_reports()
except Exception as e:
    print(f"{YELLOW}[STARTUP] 回報佇列讀取失敗（非致命）: {e}{RESET}")

# Firestore gRPC 心跳啟動（DATA_BACKEND=firestore 時才啟動，防止 gRPC 連線閒置超時）
if os.getenv("DATA_BACKEND", "local") == "firestore":
    try:
        from services.firestore_client import start_heartbeat
        start_heartbeat()
    except Exception as e:
        print(f"{YELLOW}[STARTUP] Firestore 心跳啟動失敗（非致命）: {e}{RESET}")


@app.route("/callback", methods=['POST'])
def callback() -> str:
    """
    LINE Webhook 入口點。
    """
    # 缺 X-Line-Signature（掃描器/畸形請求）時直接回 400，
    # 避免 KeyError 造成 500 與錯誤日誌噪音。
    signature = request.headers.get('X-Line-Signature')
    if not signature:
        abort(400)
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


def _reply_to_line(
    user_text: str, user_id: str, current_time: str, received_at: float
) -> None:
    """
    在背景執行緒中處理訊息並以 push_message 回覆 LINE。
    改用 push_message 取代 reply_message，根治 reply token 60 秒過期導致的延遲問題。

    Parameters
    ----------
    user_text : str
        使用者原始輸入文字。
    user_id : str
        LINE 使用者 ID，用於 push_message。
    current_time : str
        台北時間字串，傳入 AgentRouter 供 Gemini 判斷相對時間用語。
    received_at : float
        webhook 收到訊息當下的 time.time() 時戳，用於量測端到端 KPI。
    """
    _t_start = received_at
    result = None
    print(f"{CYAN}[TIMER] 開始處理訊息: {user_text!r}{RESET}")
    try:
        result = router.dispatch(user_text, current_time=current_time)
        print(f"{CYAN}[TIMER] dispatch 完成，耗時 {time.time() - _t_start:.1f}s{RESET}")

        intent = result.get('intent')
        # 回覆前檢查圖片網址存活；過期者本次用預設圖，待回覆送出後才更新網址
        data, stale_image_shops = resolve_shop_images(result.get('data', []))
        recommendations = result.get('recommendations', [])
        ui_tag = result.get('ui_tag')

        # FALLBACK：dispatch 頂層捕捉到的嚴重錯誤，或需要使用者分享位置
        if intent == 'FALLBACK':
            check_and_increment("line_api")
            # LOCATION_REQUEST：使用者文字提及「附近」但無可解析地名（純文字
            # 訊息本身無座標），附加 LINE 原生位置分享 Quick Reply，讓使用者
            # 一鍵分享後觸發 handle_location 走真正的座標篩選。
            quick_reply = None
            if ui_tag == 'LOCATION_REQUEST':
                quick_reply = QuickReply(items=[
                    QuickReplyItem(action=LocationAction(label='分享位置'))
                ])
            text_message = TextMessage(
                text=result.get('message', '系統發生錯誤，請稍後再試。'),
                quick_reply=quick_reply,
            )
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[text_message])
            )
            return

        # 錯誤回報收集
        if intent == "REPORT_ERROR":
            report_info = data[0] if data else {}
            collect_report(
                shop_name=report_info.get("shop_name"),
                error_description=report_info.get("error_description", ""),
                user_id=user_id,
            )
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(
                        text="感謝您的回報！已記錄您的意見，我們將盡快確認並修正相關資料。"
                    )],
                )
            )
            return

        if intent == 'KNOWLEDGE_QUERY':
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(
                        text=result.get('message', '知識庫查詢失敗，請稍後再試。')
                    )],
                )
            )
            return

        # 無結果 fallback
        if not data:
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(
                        text='找不到符合條件的拉麵店，請試著提供更多或不同的條件。'
                    )],
                )
            )
            return

        # Carousel 輪播
        _t_push = time.time()
        if ui_tag == "CAROUSEL":
            carousel_contents = assemble_carousel(data, recommendations)
            flex = FlexMessage(
                alt_text="拉麵推薦",
                contents=FlexContainer.from_dict(carousel_contents),
            )
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[flex])
            )
        elif intent == "GET_SPECIFIC_INFO" and len(data) == 1:
            rec = recommendations[0] if recommendations else None
            bubble = get_flex_bubble(data[0], rec)
            flex = FlexMessage(
                alt_text=data[0].get("name", "店家資訊"),
                contents=FlexContainer.from_dict(bubble),
            )
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[flex])
            )
        else:
            items = []
            for i, s in enumerate(data[:5]):
                name = s.get("name") or "不明店名"
                loc = s.get("location") or "不明地區"
                style = s.get("style") or "不明口味"
                items.append(f"{i+1}. {name} ({loc} / {style})")
            reply_text = f"找到 {len(data)} 間店：\n" + "\n".join(items)
            if recommendations:
                reply_text += "\n\n推薦詞：\n" + "\n".join(
                    f"{i+1}. {r}" for i, r in enumerate(recommendations)
                )
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id, messages=[TextMessage(text=reply_text)]
                )
            )
        print(f"{GREEN}[TIMER] push_message 完成，耗時 {time.time() - _t_push:.1f}s，"
              f"全程總耗時 {time.time() - _t_start:.1f}s{RESET}")

        # 回覆已送出，此時才更新過期的圖片網址（不與回覆搶資源）
        refresh_shop_images_async(stale_image_shops)

    except Exception as e:
        print(f"{RED}ERROR in _reply_to_line: {e} | 全程耗時 {time.time() - _t_start:.1f}s{RESET}")
        try:
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text='系統忙碌中，請稍後再試。')],
                )
            )
        except Exception as reply_err:
            print(f"{RED}ERROR: push_message 失敗（確認使用者是否加好友）: {reply_err}{RESET}")
    finally:
        # 效能 KPI：webhook 收到 → 回覆推播完成的端到端耗時 + 各 LLM 呼叫明細
        llm_snap = (result or {}).get("timing") or timing.snapshot(None)
        kpi = {**llm_snap, "total_s": round(time.time() - _t_start, 2)}
        print(f"{GREEN}[KPI][webhook] {timing.format_kpi(kpi)}{RESET}")
        # 結構化事件（供 Cloud Logging 建立 FALLBACK 率 / 延遲告警）
        emit_metric(
            "request",
            intent=(result or {}).get("intent", "ERROR"),
            total_s=kpi["total_s"],
        )


def _reply_location(
    lat: float, lng: float, user_id: str, received_at: float
) -> None:
    """
    在背景執行緒中處理 LINE 位置訊息：以使用者座標找最近的拉麵店並回覆。

    不經過 AgentRouter 意圖解析（位置訊息無文字語意），直接跑 Haversine
    比對店家快取，複用推薦文生成與 Carousel 組裝。

    Parameters
    ----------
    lat : float
        使用者所在緯度。
    lng : float
        使用者所在經度。
    user_id : str
        LINE 使用者 ID，用於 push_message。
    received_at : float
        webhook 收到訊息當下的 time.time() 時戳，用於量測端到端 KPI。
    """
    _t_start = received_at
    # 本路徑不經 dispatch，須自行開啟計時收集器讓 generate_recommendations 累積
    records = timing.begin_collection()
    print(f"{CYAN}[TIMER] 開始處理位置訊息: ({lat}, {lng}){RESET}")
    try:
        results, nearest_km = filter_by_location(lat, lng)

        # 半徑內找不到：明白告知搜尋範圍與最近一間距離
        if not results:
            if nearest_km is not None:
                text = (
                    f"已在您位置 5 公里內搜尋，找不到拉麵店。\n"
                    f"最近的一間約在 {nearest_km} 公里外，若想擴大範圍，"
                    f"可直接用文字告訴我（例如「附近 10 公里的拉麵」）。"
                )
            else:
                text = "已在您位置 5 公里內搜尋，目前找不到可推薦的拉麵店。"
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id, messages=[TextMessage(text=text)]
                )
            )
            return

        recommendations = generate_recommendations(
            results, router.client, router.model_name, num_shops=len(results)
        )
        # 回覆前檢查圖片網址存活；過期者本次用預設圖，待回覆送出後才更新網址
        results, stale_image_shops = resolve_shop_images(results)
        carousel_contents = assemble_carousel(results, recommendations)
        flex = FlexMessage(
            alt_text="附近的拉麵推薦",
            contents=FlexContainer.from_dict(carousel_contents),
        )
        check_and_increment("line_api")
        line_bot_api.push_message(
            PushMessageRequest(to=user_id, messages=[flex])
        )
        print(f"{GREEN}[TIMER] 位置訊息回覆完成，全程總耗時 {time.time() - _t_start:.1f}s{RESET}")

        # 回覆已送出，此時才更新過期的圖片網址
        refresh_shop_images_async(stale_image_shops)

    except Exception as e:
        print(f"{RED}ERROR in _reply_location: {e} | 全程耗時 {time.time() - _t_start:.1f}s{RESET}")
        try:
            check_and_increment("line_api")
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text='系統忙碌中，請稍後再試。')],
                )
            )
        except Exception as reply_err:
            print(f"{RED}ERROR: push_message 失敗（確認使用者是否加好友）: {reply_err}{RESET}")
    finally:
        # 效能 KPI：webhook 收到 → 回覆推播完成的端到端耗時 + 推薦文 LLM 明細
        _total_s = round(time.time() - _t_start, 2)
        kpi = timing.snapshot(records, time.time() - _t_start)
        print(f"{GREEN}[KPI][webhook] {timing.format_kpi(kpi)}{RESET}")
        # 結構化事件（供 Cloud Logging 建立延遲告警）
        emit_metric("location_request", total_s=_total_s)


TAIPEI_TZ = timezone(timedelta(hours=8))


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event: MessageEvent) -> None:
    """
    LINE 訊息事件入口。立即返回（非阻塞），由背景執行緒處理 AI 邏輯與回覆。
    改用 user_id 傳遞至 _reply_to_line，使用 push_message 取代 reply_message。

    僅處理一對一私聊（source.type == "user"），群組/多人聊天室訊息直接忽略；
    並以 message.id 去重，避免 LINE webhook 重送觸發重複的 Gemini 呼叫。
    """
    received_at = time.time()
    if event.source.type != "user":
        print(f"{YELLOW}STEP: 來源非一對一私聊（{event.source.type}），忽略此訊息{RESET}")
        return

    if is_duplicate_message(event.message.id):
        return

    user_text = event.message.text
    user_id = event.source.user_id
    current_time = datetime.fromtimestamp(
        event.timestamp / 1000, tz=TAIPEI_TZ
    ).strftime("%Y-%m-%d %H:%M")
    thread = threading.Thread(
        target=_reply_to_line,
        args=(user_text, user_id, current_time, received_at),
        daemon=True,
    )
    thread.start()


@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event: MessageEvent) -> None:
    """
    LINE 位置訊息事件入口。立即返回（非阻塞），由背景執行緒處理定位推薦與回覆。

    沿用 handle_message 的兩道防護：僅處理一對一私聊、並以 message.id 去重。
    """
    received_at = time.time()
    if event.source.type != "user":
        print(f"{YELLOW}STEP: 來源非一對一私聊（{event.source.type}），忽略此位置訊息{RESET}")
        return

    if is_duplicate_message(event.message.id):
        return

    user_id = event.source.user_id
    lat = event.message.latitude
    lng = event.message.longitude
    thread = threading.Thread(
        target=_reply_location,
        args=(lat, lng, user_id, received_at),
        daemon=True,
    )
    thread.start()


if __name__ == "__main__":
    if LINE_TAG == 1:
        app.run(port=5000)
    else:
        print(f"{CYAN}--- 地端測試模式啟動 ---{RESET}")
        while True:
            test_input = input(f"\n{YELLOW}>> 請輸入提問 (輸入 'exit' 離開): {RESET}")
            if test_input.lower() == 'exit':
                break

            print(f"{GREEN}STEP 1: 正在呼叫 Gemini 解析使用者意圖...{RESET}")
            res = router.dispatch(test_input)

            print(f"{GREEN}STEP 2: 執行對應 Skill 並獲取資料 (Intent: {res['intent']})...{RESET}")
            print(f"  - 找到店家數量: {len(res.get('data', []))}")
            res['data'], _stale = resolve_shop_images(res.get('data') or [])

            if res.get('recommendations'):
                print(f"{GREEN}STEP 3: 正在生成 AI 推薦文案...{RESET}")

            print(f"{GREEN}STEP 4: 正在進行 UI 組裝與 temp.json 輸出...{RESET}")
            try:
                intent = res.get('intent')
                if intent in ('KNOWLEDGE_QUERY', 'FALLBACK'):
                    msg = res.get('message') or '（無回答）'
                    label = '知識庫回答' if intent == 'KNOWLEDGE_QUERY' else '系統錯誤訊息'
                    print(f"{CYAN}  => {label}：\n{msg}{RESET}")
                elif intent == "REPORT_ERROR":
                    report_info = (res.get("data") or [{}])[0]
                    collect_report(
                        shop_name=report_info.get("shop_name"),
                        error_description=report_info.get("error_description", ""),
                        user_id="local_test_user",
                    )
                    print(f"{CYAN}  => 回報已記錄。{RESET}")
                elif res.get('ui_tag') == 'CAROUSEL' and res.get('data'):
                    carousel_contents = assemble_carousel(res['data'], res.get('recommendations'))
                    with open('temp.json', 'w', encoding='utf-8') as f:
                        json.dump(carousel_contents, f, ensure_ascii=False, indent=2)
                    print(f"{CYAN}  => 成功！Flex Carousel JSON 已寫入 temp.json{RESET}")
                elif intent == 'GET_SPECIFIC_INFO' and res.get('data'):
                    rec = res['recommendations'][0] if res.get('recommendations') else None
                    bubble = get_flex_bubble(res['data'][0], rec)
                    with open('temp.json', 'w', encoding='utf-8') as f:
                        json.dump(bubble, f, ensure_ascii=False, indent=2)
                    print(f"{CYAN}  => 成功！Flex Bubble JSON 已寫入 temp.json{RESET}")
                else:
                    print(f"{BLUE}  => 此意圖建議使用文字回覆，未生成 Flex JSON。{RESET}")
            except Exception as e:
                print(f"{RED}STEP 4 ERROR:{e}{RESET}")

            print(f"{MAGAENTA}{'='*50}{RESET}")
