# === 在檔案頂部定義開關 ===
# LINE_TAG = 1: 正式模式 (LINE + ngrok)
# LINE_TAG = 0: 地端測試模式 (終端機輸出)
LINE_TAG = 0

# === imports ===
import os
import json
import re
import asyncio
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
from prompts import IDENTIFY_INSTRUCTION_PROMPT, RECOMMEND_PROMPT

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

# 建立 Gemini 模型實例；
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL")
identify_model = genai.GenerativeModel(
    model_name = GEMINI_MODEL_NAME,
    system_instruction = IDENTIFY_INSTRUCTION_PROMPT
)
# 推薦文專用模型（不帶意圖解析的 IDENTIFY_INSTRUCTION_PROMPT，避免輸出格式錯亂）
recommend_model = genai.GenerativeModel(model_name = GEMINI_MODEL_NAME)

# ===========================================================================
# 通用函數
# ===========================================================================
def extract_text(obj):    # 從 Gemini 回傳物件抽出文字
    """
    從 Gemini 回傳物件中取出最有可能的文字欄位。
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
    print(f"{YELLOW}extract_text() 回傳: {obj}{RESET}")
    return str(obj)

def parse_intent(response):    # 解析 Gemini 的回傳物件，回傳 dict 形式的 intent
    """
    解析 Gemini 的回應並回傳 dict 形式的 intent。
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

def build_shop_summary(shop: dict) -> str:
    """將單一店家 dict 組成一筆易讀的中文描述（供推薦 LLM 使用）。"""
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


def get_one_recommendation(shop_summary: str) -> str:
    """針對一間店的描述呼叫推薦 LLM（同步），回傳一句推薦文。失敗則回傳預設句。"""
    default = "點擊查看地圖了解更多。"
    try:
        # prompt = RECOMMEND_PROMPT.replace("{shop_summary}", shop_summary)
        prompt = RECOMMEND_PROMPT.format(shop_summary = shop_summary)
        print(f"{YELLOW}prompt: {prompt}{RESET}")
        recommend_result = recommend_model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.6,
                "max_output_tokens": 1200,
                "top_p": 0.85,
                "top_k": 10,
            },
        )
        # print(f"{YELLOW}Gemini recommendation raw response: {recommend_result.candidates[0].content.parts[0].text}{RESET}")
        print(f"{YELLOW}Gemini recommendation raw response: {recommend_result}{RESET}")
        raw = extract_text(recommend_result).strip()
        raw = re.sub(r"```\w*\s*", "", raw).strip()
        if not raw or any(c in raw for c in "I will"):
            return default
        return raw
    except Exception:
        return default

async def get_one_recommendation_async(shop_summary: str) -> str:
    """非同步包裝：在執行緒中呼叫同步 Gemini API，不阻塞 event loop。"""
    return await asyncio.to_thread(get_one_recommendation, shop_summary)


async def fetch_all_recommendations_async(summaries: list[str]) -> list[str]:
    """並行呼叫推薦 LLM（async/await + gather）；單一失敗不影響其他，該格回傳預設句。"""
    default = "點擊查看地圖了解更多。"
    tasks = [get_one_recommendation_async(s) for s in summaries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[str] = []
    for i, r in enumerate[str | BaseException](results):
        if isinstance(r, Exception):
            print(f"{RED}recommendation[{i}] error: {r}{RESET}")
            out.append(default)
        else:
            out.append(r)
    return out

def generate_recommendation(shops_info: list[dict], num_shops: int = 5) -> list[str] | None:
    """依店家列表產生「每間店一筆」推薦文，以 async/await 並行呼叫 LLM，回傳與店家順序對應的 list[str]。"""
    if not shops_info:
        return None
    selected = shops_info[:num_shops]
    summaries: list[str] = [build_shop_summary(content) for content in selected]
    print(f"{YELLOW}shop_summaries (共 {len(summaries)} 筆):{RESET}")
    for i, s in enumerate(summaries):
        print(f"  [{i}] {s[:80]}...")
    try:
        recommendations = asyncio.run(fetch_all_recommendations_async(summaries))
    except Exception as e:
        print(f"{RED}fetch recommendations error: {e}{RESET}")
        recommendations = ["點擊查看地圖了解更多。"] * len(summaries)
    print(f"{YELLOW}recommendations: {recommendations}{RESET}")
    return recommendations


# ===========================================================================
# LINE 相關函數
# ===========================================================================

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
        # 根據 IDENTIFY_INSTRUCTION_PROMPT 回傳 GenerateContentResponse 物件
        model_result = identify_model.generate_content(user_text)
        log_debug(f"Gemini raw response: {model_result}")
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

        # (選配) 第三步：生成推薦文案（每間店一筆，並行呼叫 LLM）
        recommendations = None
        try:
            stage = 'generate_recommendation'
            log_debug(f"stage={stage}")
            recommendations = generate_recommendation(results)
        except Exception:
            recommendations = None

        # 第四步：根據 ui_tag 組裝回覆
        stage = 'assemble_reply'
        log_debug(f"stage={stage}")
        ui_tag = intent.get('ui_tag') if isinstance(intent, dict) else None

        if ui_tag and str(ui_tag).upper() == 'CAROUSEL':
            carousel_contents = assemble_carousel(results, recommendations)
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
            if recommendations:
                if isinstance(recommendations, list):
                    reply_text += "\n\n推薦詞：\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(recommendations))
                else:
                    reply_text += "\n\n推薦詞：\n" + recommendations

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    except Exception as e:
        # 任一層出錯都回覆忙碌訊息，並寫入錯誤日誌
        msg = f"handle_message error at stage '{stage}': {e}"
        log_debug(msg)
        import traceback
        with open('error.log', 'a', encoding='utf-8') as f:
            traceback.print_exc(file=f)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text='系統忙碌中，請稍後再試。'))
        except Exception as inner:
            log_debug(f"reply failed: {inner}")

# ===========================================================================
# 地端測試用函數
# ===========================================================================
def main_process(user_text):
    """將原本在 handle_message 裡的邏輯獨立出來，方便重複使用"""
    try:
        # 第一層：LLM 解析意圖
        print(f"{CYAN}STEP 1：呼叫 Gemini 解析意圖...{RESET}")
        print(f"{GREEN}使用者輸入: {user_text}{RESET}")
        model_result = identify_model.generate_content(user_text)
        print(f"{GREEN}Gemini 原始回應: {model_result.candidates!r}{RESET}")
        print(f"{CYAN}{type(model_result)}{RESET}")
        
        print(f"{CYAN}STEP 2：解析意圖中...{RESET}") 
        intent = parse_intent(model_result)
        print(f"{GREEN}解析結果: {intent}{RESET}")
        
        # 第二層：function 本地資料篩選
        print(f"{CYAN}STEP 3：根據意圖篩選資料...{RESET}")
        results = filter_ramen_data(intent)
        print(f"{GREEN}篩選結果: {results}{RESET}")
        if not results:
            return "找不到符合條件的拉麵店。", intent, None

        # 第三層：生成 LLM 推薦語（每間店一筆 list）
        print(f"{CYAN}STEP 4：生成推薦文案...{RESET}")
        recommendations = generate_recommendation(results)
        print(f"{GREEN}推薦結果: {recommendations}{RESET}")

        # 回傳最終處理結果
        return results, intent, recommendations
    except Exception as e:
        print(f"{RED}發生錯誤: {e}{RESET}")
        return f"發生錯誤: {e}", None, None




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
            final_respond, intent, recommendations = main_process(test_input)
            
            print("=" * 30)
            print(f"【AI 解析意圖】: {intent}")
            print("-" * 30)
            print(f"【篩選店家結果】: {final_respond}")
            print("-" * 30)
            rec_list = recommendations or []
            print("【AI 推薦文案】:", rec_list if not rec_list else "\n".join(f"  {i+1}. {r}" for i, r in enumerate(rec_list)))
            print("=" * 30)
            