# prompt definitions extracted from app.py for reuse and easier editing

SYSTEM_INSTRUCTION = """
你是一位拉麵需求分析師。請將使用者的話轉換為 JSON 格式：
{"intent": "search", "location": "地區", "style": "口味", "ui_tag": "CAROUSEL"或"TEXT"}
若資訊缺失請給 null。只輸出 JSON，不要有贅字。
"""

# template for generating recommendation text; expects `shops_preview` to be formatted JSON string
RECOMMEND_PROMPT_TEMPLATE = (
    "請為以下拉麵店各寫一段一句到兩句的推薦文(中文)：{shops_preview}"
)
