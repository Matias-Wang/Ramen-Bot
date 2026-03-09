
# === 在檔案頂部定義開關 ===
# LINE_TAG = 1: 正式模式 (LINE + ngrok)
# LINE_TAG = 0: 地端測試模式 (終端機輸出)
LINE_TAG = 0

# === imports ===
import os
import json
import re
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
from dotenv import load_dotenv

# === external helpers ===
from processor import filter_ramen_data  # 依賴本地資料庫查詢邏輯
from flex_handler import assemble_carousel  # 依賴 Flex Message 組裝邏輯
# prompt text for Gemini
from prompts import SYSTEM_INSTRUCTION, RECOMMEND_PROMPT_TEMPLATE

RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
BLUE = '\033[94m'
CYAN = '\033[96m'
MAGAENTA = '\033[95m'
RESET = '\033[0m'

# === configuration / constants ===
load_dotenv()  # 載入 .env 中的環境變數


app = Flask(__name__)
# LINE 設定，可於 .env 或環境變數中修改
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Gemini / Google API 設定
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

# 建立 Gemini 模型實例，若需調整可在此修改
model = genai.GenerativeModel(
    model_name="gemini-3-flash-preview",
    system_instruction=SYSTEM_INSTRUCTION
)

# === helper functions ===
#
# Example of intent dict produced by Gemini:
# {
#     "intent": "search",
#     "location": "南港",
#     "style": "豚骨",
#     "ui_tag": "CAROUSEL"  # or "TEXT"
# }
#
# Example of results list (returned by filter_ramen_data):
# [
#     {"name": "拉麵店A", "location": "南港", "style": "豚骨", "address": "XX路123號"},
#     {"name": "拉麵店B", "location": "信義", "style": "魚介", "address": "YY街45號"},
# ]




def extract_text(obj):
    """從 Gemini 回傳物件中取出最有可能的文字欄位。
    
    generate_content() 回傳的是 GenerateContentResponse 物件，
    可能包含以下屬性（優先順序）：
      - obj.text: 主要輸出文字（最常見）
      - obj.output: 備選輸出欄位
      - obj.content: 內容物件
      - obj.candidates: 候選回應列表
    
    若上述都沒有，則轉換為 str()。
    """
    for attr in ('text', 'output', 'content', 'candidates'):
        if hasattr(obj, attr):
            return getattr(obj, attr)
    return str(obj)


def parse_intent(response):
    """解析 Gemini 的回應並回傳 dict 形式的 intent。
    
    參數 response：
        - 來自 model.generate_content(user_text) 的 GenerateContentResponse 物件
        - 本函式使用 extract_text() 從中抽取文字，預期取得 JSON 字串
    
    例外：若找不到 JSON 或解析失敗會拋出 ValueError。
    
    範例回傳：
        {"intent": "search", "location": "台北", "style": "鹽味", "ui_tag": "TEXT"}
    """
    raw = extract_text(response)
    raw = re.sub(r'```(?:json)?', '', str(raw))  # 去掉 ``` 代碼區
    m = re.search(r'(\{.*\})', raw, re.S)
    if not m:
        raise ValueError('無法從回應抽出 JSON')
    return json.loads(m.group(1).replace("'", '"'))


def generate_recommendation(shops):
    """呼叫 Gemini 產生簡短的推薦文，最多前 5 間店。
    
    參數 shops：
        - list[dict]，來自 filter_ramen_data() 的篩選結果
        - 會將前 5 間店轉為 JSON 並組入 prompt
    
    流程：
        1. 將 shops 轉成 JSON 字串（shops_preview）
        2. 使用 RECOMMEND_PROMPT_TEMPLATE 組出提示詞
        3. 呼叫 model.generate_content(prompt) 取得 GenerateContentResponse
        4. 用 extract_text() 抽出文字，移除 markdown fence，並 strip()
    
    回傳：
        推薦文字串，例如「此店豚骨湯頭濃郁，叉燒軟嫩多汁」
    """
    shops_preview = json.dumps(shops[:5], ensure_ascii=False)
    prompt = RECOMMEND_PROMPT_TEMPLATE.format(shops_preview=shops_preview)
    rec_resp = model.generate_content(
        prompt,
        generation_config={
                "temperature": 0.7,
                "max_output_tokens": 200,
                "top_p": 0.85,
                "top_k": 10
            }
        # temperature=0.7,
        # max_output_tokens=200,
        # top_k = 10,
        # top_p = 0.85,
        # # safety_settings=
        # stream=False,
        # tools=None,
        # tool_config=None
    )
    rec_text = extract_text(rec_resp)
    return re.sub(r'```(?:json)?', '', str(rec_text)).strip()




# === webhook / request handlers ===
# LINE webhook 入口，驗證簽章後轉交給 handler
@app.route("/callback", methods=['POST'])
def callback():
    """Line webhook 入口，驗證簽章後轉交給 handler。"""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# 定義「收到文字訊息」時的動作，當有人傳送文字給機器人時，這個函式會被觸發
@handler.add(MessageEvent, message = TextMessage)
def handle_message(event):
    """Line 收到文字訊息的主要流程。
    1. 取得 user_text
    2. 呼叫 parse_intent() 進行第一層 Gemini 分析
    3. 交給 filter_ramen_data() 進行本地篩選
    4. 若需要，可呼叫 generate_recommendation() 取得 AI 推薦文
    5. 根據 ui_tag 決定回傳格式，並呼叫 LINE API
    """
    user_text = event.message.text

    # 定義日誌函式，所有 debug 訊息都寫入檔案
    import datetime
    def log_debug(msg):
        try:
            with open('error.log', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now()}] {msg}\n")
                f.flush()
        except Exception:
            pass

    stage = 'start'
    log_debug(f"stage={stage}")
    try:
        # 第一步：LLM解析使用者意圖
        stage = 'parse_intent'
        log_debug(f"stage={stage}")
        # model.generate_content(user_text) 傳入使用者訊息，
        # 根據 SYSTEM_INSTRUCTION 回傳 GenerateContentResponse 物件
        model_result = model.generate_content(user_text)
        log_debug(f"Gemini raw response: {repr(model_result)}")
        intent = parse_intent(model_result)

        # 第二步：function本地過濾
        stage = 'filter_data'
        log_debug(f"stage={stage}")
        results = filter_ramen_data(intent)
        if not results:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text='找不到符合條件的拉麵店，請試著提供更多或不同的條件。')
            )
            return

        # (選配) 第三步：生成推薦文案
        recommendation = None
        try:
            stage = 'generate_recommendation'
            log_debug(f"stage={stage}")
            recommendation = generate_recommendation(results)
        except Exception:
            # 忽略推薦文生成失敗，不影響主流程
            recommendation = None

        # 第四步：根據 ui_tag 組裝回覆
        stage = 'assemble_reply'
        log_debug(f"stage={stage}")
        ui_tag = intent.get('ui_tag') if isinstance(intent, dict) else None
        # if ui_tag and str(ui_tag).upper() == 'CAROUSEL':
        #     # 準備 Carousel Flex message
        #     bubbles = []
        #     for shop in results[:5]:
        #         title = shop.get('name') or shop.get('shop') or '不明店名'
        #         loc = shop.get('location') or '不明地區'
        #         style = shop.get('style') or '不明口味'
        #         addr = shop.get('address') or ''
        #         body_lines = f"{loc} / {style}\n{addr}"
        #         if recommendation:
        #             body_lines += f"\n{recommendation}"
        #         bubble = {
        #             "type": "bubble",
        #             "body": {
        #                 "type": "box",
        #                 "layout": "vertical",
        #                 "contents": [
        #                     {"type": "text", "text": title, "weight": "bold", "size": "md"},
        #                     {"type": "text", "text": body_lines, "wrap": True, "size": "sm"}
        #                 ]
        #             }
        #         }
            #     bubbles.append(bubble)

            # flex = FlexSendMessage(alt_text='拉麵推薦', contents={"type": "carousel", "contents": bubbles})
            # line_bot_api.reply_message(event.reply_token, flex)

        if ui_tag and str(ui_tag).upper() == 'CAROUSEL':
            carousel_contents = assemble_carousel(results, recommendation)
            flex = FlexSendMessage(alt_text='拉麵推薦', contents=carousel_contents)
            line_bot_api.reply_message(event.reply_token, flex)

        else: # ui_tag 不明或非 CAROUSEL，預設回覆純文字列表
            # 預設純文字回覆
            items = []
            for i, s in enumerate(results[:5]):
                name = s.get('name') or s.get('shop') or '不明店名'
                loc = s.get('location') or '不明地區'
                style = s.get('style') or '不明口味'
                addr = s.get('address') or ''
                items.append(f"{i+1}. {name} ({loc} / {style}) {addr}")

            reply_text = f"找到 {len(results)} 間符合條件的店：\n" + "\n".join(items)
            if recommendation:
                reply_text += "\n\n推薦詞：\n" + recommendation

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    except Exception as e:
        # 任一層出錯都回覆忙碌訊息，並寫入錯誤日誌
        msg = f"handle_message error at stage '{stage}': {repr(e)}"
        log_debug(msg)
        import traceback
        with open('error.log', 'a', encoding='utf-8') as f:
            traceback.print_exc(file=f)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text='系統忙碌中，請稍後再試。'))
        except Exception as inner:
            log_debug(f"reply failed: {repr(inner)}")

def process_ramen_request(user_text):
    """將原本在 handle_message 裡的邏輯獨立出來，方便重複使用"""
    try:
        # 第一層：AI 解析意圖
        print(f"{CYAN}STEP 1：呼叫 Gemini 解析意圖...{RESET}")
        print(f"{GREEN}使用者輸入: {user_text}{RESET}")
        model_result = model.generate_content(user_text)
        print(f"{GREEN}Gemini 原始回應: {repr(model_result)}{RESET}")
        print(f"{CYAN}{type(model_result)}{RESET}")
        print(f"{CYAN}STEP 2：解析意圖中...{RESET}") 
        intent = parse_intent(model_result)
        print(f"{GREEN}解析結果: {intent}{RESET}")
        # 第二層：本地資料篩選
        print(f"{CYAN}STEP 3：根據意圖篩選資料...{RESET}")
        results = filter_ramen_data(intent)
        print(f"{GREEN}篩選結果: {results}{RESET}")
        if not results:
            return "找不到符合條件的拉麵店。", intent, None

        # 第三層：生成 AI 推薦語
        recommendation = generate_recommendation(results)
        
        # 回傳處理結果
        return results, intent, recommendation
    except Exception as e:
        print(f"{RED}發生錯誤: {repr(e)}{RESET}")
        return f"發生錯誤: {repr(e)}", None, None




# === application entry point ===
if __name__ == "__main__":
    if LINE_TAG == 1:
        print("--- 正式模式：LINE 機器人伺服器啟動中 ---")
        app.run(port=5000)

    else:
        print("--- 地端測試模式：直接在終端機對話 ---")
        print("請輸入您的拉麵需求 (輸入 'exit' 結束)：")
        while True:
            test_input = input(">> 使用者提問: ")
            if test_input.lower() == 'exit':
                break
            
            # 模擬執行
            res, intent, rec = process_ramen_request(test_input)
            
            print("-" * 30)
            print(f"【AI 解析意圖】: {intent}")
            print(f"【篩選店家結果】: {res}")
            print(f"【AI 推薦文案】: {rec}")
            print("-" * 30)