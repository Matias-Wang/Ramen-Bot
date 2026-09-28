"""
Firestore 知識庫向量索引建立腳本

讀取 knowledge/ 目錄下的所有 .md / .txt 文件，切分段落後
以 Google Embedding API 嵌入，批次寫入 Firestore ramen_knowledge collection。

執行前必須先在 GCP 建立 Firestore 向量索引，指令如下：
  gcloud firestore indexes composite create \\
    --collection-group=ramen_knowledge \\
    --query-scope=COLLECTION \\
    --field-config field-path=embedding,vector-config='{"dimension":"768","flat": {}}' \\
    --project=YOUR_PROJECT_ID

索引建立約需 2-5 分鐘，完成後才可執行本腳本。

使用方式：
  python scripts/migrate_knowledge_to_firestore.py
  python scripts/migrate_knowledge_to_firestore.py --force   # 清空後重建
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
from google import genai
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector
from google.genai import types

load_dotenv()

# <使用者自訂變數>
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
MAGAENTA = "\033[95m"
RESET = "\033[0m"

KNOWLEDGE_DIR = "knowledge"
COLLECTION = "ramen_knowledge"
EMBED_MODEL = "models/gemini-embedding-001"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _load_documents() -> list[dict]:
    """
    從 knowledge/ 目錄讀取所有 .txt 與 .md 文件。

    Returns
    -------
    list[dict]
        包含 text 與 source 的字典清單。
    """
    docs = []
    for fname in sorted(os.listdir(KNOWLEDGE_DIR)):
        if fname.startswith(".") or not (fname.endswith(".txt") or fname.endswith(".md")):
            continue
        fpath = os.path.join(KNOWLEDGE_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                docs.append({"text": text, "source": fname})
        except Exception as e:
            print(f"{RED}ERROR: 讀取 {fname} 失敗: {e}{RESET}")
    return docs


def _chunk_text(text: str) -> list[str]:
    """
    將文字切割為有重疊的段落。

    Parameters
    ----------
    text : str
        原始文字內容。

    Returns
    -------
    list[str]
        非空白段落清單。
    """
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + CHUNK_SIZE]
        if chunk.strip():
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _embed(client: genai.Client, text: str) -> list[float]:
    """
    呼叫 Google Embedding API 嵌入單一文字。

    Parameters
    ----------
    client : genai.Client
        Gemini client（google-genai，與 src/skills/knowledge_skill.py 相同 SDK）。
    text : str
        要嵌入的文字。

    Returns
    -------
    list[float]
        768 維嵌入向量。
    """
    result = client.models.embed_content(
        model=EMBED_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="retrieval_document",
            output_dimensionality=768,
        ),
    )
    return result.embeddings[0].values


def run(force: bool = False) -> None:
    """
    執行知識庫遷移。

    Parameters
    ----------
    force : bool
        若為 True，先清空 ramen_knowledge collection 再重建。
    """
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    db = firestore.Client(
        project=os.getenv("GOOGLE_CLOUD_PROJECT_ID"),
        database=os.getenv("FIRESTORE_DATABASE", "(default)"),
    )
    col = db.collection(COLLECTION)

    print(f"{GREEN}STEP 1: 載入 knowledge/ 目錄文件{RESET}")
    docs = _load_documents()
    if not docs:
        print(f"{RED}ERROR: knowledge/ 目錄內無任何文件{RESET}")
        return
    print(f"  找到 {len(docs)} 個文件：{[d['source'] for d in docs]}")

    if force:
        print(f"{YELLOW}STEP 1.5: --force 模式，清空現有 {COLLECTION} collection{RESET}")
        for doc in col.stream():
            doc.reference.delete()
        print(f"  清空完成")

    print(f"{GREEN}STEP 2: 切分段落並嵌入{RESET}")
    all_chunks: list[dict] = []
    for doc in docs:
        for i, chunk in enumerate(_chunk_text(doc["text"])):
            all_chunks.append({
                "doc_id": f"{doc['source']}_{i}",
                "content": chunk,
                "source": doc["source"],
            })
    print(f"  共 {len(all_chunks)} 個段落，開始嵌入（需呼叫 {len(all_chunks)} 次 Embedding API）")

    print(f"{GREEN}STEP 3: 批次寫入 Firestore{RESET}")
    BATCH_SIZE = 500
    written = 0

    for i in range(0, len(all_chunks), BATCH_SIZE):
        batch = db.batch()
        for item in all_chunks[i:i + BATCH_SIZE]:
            embedding = _embed(client, item["content"])
            data = {
                "content": item["content"],
                "source": item["source"],
                "embedding": Vector(embedding),
            }
            batch.set(col.document(item["doc_id"]), data)
        batch.commit()
        written += len(all_chunks[i:i + BATCH_SIZE])
        print(f"{CYAN}  => {written}/{len(all_chunks)} 段落已寫入{RESET}")

    print(f"\n{MAGAENTA}{'=' * 40}")
    print(f"完成！共 {written} 個段落寫入 Firestore {COLLECTION}")
    print(f"{'=' * 40}{RESET}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知識庫向量索引遷移至 Firestore")
    parser.add_argument("--force", action="store_true", help="清空後重建索引")
    args = parser.parse_args()
    run(force=args.force)
