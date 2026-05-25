# === 在檔案頂部定義開關 ===
# LINE_TAG = 1: 正式模式 (LINE + ngrok)
# LINE_TAG = 0: 地端測試模式 (終端機輸出 + temp.json)
LINE_TAG = 0

# === imports ===
import os
import json
import threading
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
from dotenv import load_dotenv

# === 外部模組匯入 ===
from core.agent_router import AgentRouter
from core.flex_handler import assemble_carousel
from core.usage_tracker import check_and_increment

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

# 初始化 Flask App
app = Flask(__name__)

# 初始化 LINE Bot API 與 Webhook Handler
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 初始化 AgentRouter：負責意圖解析與技能分發
router = AgentRouter(os.getenv('GEMINI_MODEL'))


@app.route("/callback", methods=['POST'])
def callback():
    """
    LINE Webhook 入口點。
    """
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


def _reply_to_line(user_text: str, user_id: str) -> None:
    """
    在背景執行緒中處理訊息並以 push_message 回覆 LINE。
    改用 push_message 取代 reply_message，根治 reply token 60 秒過期導致的延遲問題。

    Parameters
    ----------
    user_text : str
        使用者原始輸入文字。
    user_id : str
        LINE 使用者 ID，用於 push_message。
    """
    try:
        result = router.dispatch(user_text)

        intent = result.get('intent')
        data = result.get('data', [])
        recommendations = result.get('recommendations', [])
        ui_tag = result.get('ui_tag')

        # FALLBACK：dispatch 頂層捕捉到的嚴重錯誤
        if intent == 'FALLBACK':
            check_and_increment("line_api")
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text=result.get('message', '系統發生錯誤，請稍後再試。'))
            )
            return

        # 知識庫問答
        if intent == 'KNOWLEDGE_QUERY':
            check_and_increment("line_api")
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text=result.get('message', '知識庫查詢失敗，請稍後再試。'))
            )
            return

        # 無結果 fallback
        if not data:
            check_and_increment("line_api")
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text='找不到符合條件的拉麵店，請試著提供更多或不同的條件。')
            )
            return

        # Carousel 輪播
        if ui_tag == 'CAROUSEL':
            carousel_contents = assemble_carousel(data, recommendations)
            flex = FlexSendMessage(alt_text='拉麵推薦', contents=carousel_contents)
            check_and_increment("line_api")
            line_bot_api.push_message(user_id, flex)
        else:
            items = [
                f"{i+1}. {s.get('name', '不明店名')} ({s.get('location', '不明地區')} / {s.get('style', '不明口味')})"
                for i, s in enumerate(data[:5])
            ]
            reply_text = f"找到 {len(data)} 間店：\n" + "\n".join(items)
            if recommendations:
                reply_text += "\n\n推薦詞：\n" + "\n".join(
                    f"{i+1}. {r}" for i, r in enumerate(recommendations)
                )
            check_and_increment("line_api")
            line_bot_api.push_message(user_id, TextSendMessage(text=reply_text))

    except Exception as e:
        print(f"{RED}ERROR in _reply_to_line: {e}{RESET}")
        try:
            check_and_increment("line_api")
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text='系統忙碌中，請稍後再試。')
            )
        except Exception as reply_err:
            print(f"{RED}ERROR: 回覆 LINE 失敗: {reply_err}{RESET}")


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    LINE 訊息事件入口。立即返回（非阻塞），由背景執行緒處理 AI 邏輯與回覆。
    改用 user_id 傳遞至 _reply_to_line，使用 push_message 取代 reply_message。
    """
    user_text = event.message.text
    user_id = event.source.user_id
    thread = threading.Thread(
        target=_reply_to_line,
        args=(user_text, user_id),
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

            if res.get('recommendations'):
                print(f"{GREEN}STEP 3: 正在生成 AI 推薦文案...{RESET}")

            print(f"{GREEN}STEP 4: 正在進行 UI 組裝與 temp.json 輸出...{RESET}")
            try:
                intent = res.get('intent')
                if intent in ('KNOWLEDGE_QUERY', 'FALLBACK'):
                    msg = res.get('message') or '（無回答）'
                    label = '知識庫回答' if intent == 'KNOWLEDGE_QUERY' else '系統錯誤訊息'
                    print(f"{CYAN}  => {label}：\n{msg}{RESET}")
                elif res.get('ui_tag') == 'CAROUSEL' and res.get('data'):
                    carousel_contents = assemble_carousel(res['data'], res.get('recommendations'))
                    with open('temp.json', 'w', encoding='utf-8') as f:
                        json.dump(carousel_contents, f, ensure_ascii=False, indent=2)
                    print(f"{CYAN}  => 成功！Flex Message JSON 已寫入 temp.json{RESET}")
                else:
                    print(f"{BLUE}  => 此意圖建議使用文字回覆，未生成 Carousel JSON。{RESET}")
            except Exception as e:
                print(f"{RED}STEP 4 ERROR：{e}{RESET}")

            print(f"{MAGAENTA}{'='*50}{RESET}")
