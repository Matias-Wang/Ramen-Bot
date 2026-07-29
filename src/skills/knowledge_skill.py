import os
from typing import Optional

import chromadb
from google import genai
from google.genai import types

from core.prompts import KNOWLEDGE_ANSWER_PROMPT
from core.usage_tracker import check_and_increment, record_tokens
from core.llm_retry import generate_with_retry
from core import timing

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"

USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"

KNOWLEDGE_DIR = "knowledge"
CHROMA_DIR = os.path.join(KNOWLEDGE_DIR, ".chroma_db")
COLLECTION_NAME = "ramen_knowledge"
EMBED_MODEL = "models/gemini-embedding-001"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K = 3


class KnowledgeSkill:
    """
    拉麵知識庫問答技能模組，採用 RAG（檢索增強生成）架構。

    啟動時若向量索引為空，會自動讀取 knowledge/ 目錄內的文件並建立索引。
    若需強制重建索引，請刪除 knowledge/.chroma_db/ 目錄後重啟。
    """

    def __init__(self, client: genai.Client, model_name: str) -> None:
        print(f"{GREEN}STEP 0: 初始化 Knowledge Skill (RAG){RESET}")
        self.client = client
        self.model_name = model_name
        self.collection = None
        if USE_FIRESTORE:
            print(f"{GREEN}STEP 0: 使用 Firestore KNN 向量搜尋模式{RESET}")
            return
        try:
            os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
            chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
            self.collection = chroma_client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            if self.collection.count() == 0:
                print(f"{GREEN}STEP 0: 向量索引為空，開始建立文件索引{RESET}")
                self._build_index()
            else:
                print(
                    f"{GREEN}STEP 0: 已載入向量索引，共 {self.collection.count()} 個段落{RESET}"
                )
        except Exception as e:
            print(f"{RED}STEP 0 ERROR: {e}{RESET}")

    def _load_documents(self) -> list[dict]:
        """
        從 knowledge/ 目錄讀取所有 .txt 與 .md 文件。

        Returns
        -------
        list[dict]
            包含 text（文字內容）與 source（檔名）的字典清單。
        """
        docs = []
        for fname in sorted(os.listdir(KNOWLEDGE_DIR)):
            if fname.startswith(".") or not (
                fname.endswith(".txt") or fname.endswith(".md")
            ):
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

    def _chunk_text(self, text: str) -> list[str]:
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
            end = start + CHUNK_SIZE
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start += CHUNK_SIZE - CHUNK_OVERLAP
        return chunks

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        """
        呼叫 Google Embedding API 嵌入文字清單。

        Parameters
        ----------
        texts : list[str]
            要嵌入的文字清單。
        task_type : str
            嵌入任務類型（"retrieval_document" 或 "retrieval_query"）。

        Returns
        -------
        list[list[float]]
            嵌入向量清單。
        """
        embeddings = []
        for text in texts:
            result = generate_with_retry(
                lambda: self.client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=768,
                    ),
                ),
                label="embedding",
            )
            embeddings.append(result.embeddings[0].values)
        return embeddings

    def _build_index(self) -> None:
        """
        載入文件、切割段落並將嵌入向量寫入 ChromaDB。
        """
        docs = self._load_documents()
        if not docs:
            print(f"{RED}STEP 0 ERROR: knowledge/ 目錄內無任何 .txt 或 .md 文件{RESET}")
            return

        all_chunks: list[str] = []
        all_ids: list[str] = []
        all_metadatas: list[dict] = []

        for doc in docs:
            for i, chunk in enumerate(self._chunk_text(doc["text"])):
                all_chunks.append(chunk)
                all_ids.append(f"{doc['source']}_{i}")
                all_metadatas.append({"source": doc["source"]})

        print(f"{GREEN}STEP 0: 共 {len(all_chunks)} 個段落，開始嵌入（此步驟僅執行一次）{RESET}")
        try:
            embeddings = self._embed(all_chunks, task_type="retrieval_document")
            self.collection.add(
                documents=all_chunks,
                embeddings=embeddings,
                ids=all_ids,
                metadatas=all_metadatas,
            )
            print(f"{GREEN}STEP 0: 向量索引建立完成，共 {len(all_chunks)} 個段落{RESET}")
        except Exception as e:
            print(f"{RED}STEP 0 ERROR: 建立索引失敗: {e}{RESET}")

    def answer(self, query: str) -> str:
        """
        執行 RAG 流程：嵌入查詢 → 向量檢索 → Gemini 生成回答。

        Parameters
        ----------
        query : str
            使用者的拉麵相關問題。
        model : genai.GenerativeModel
            用於生成回答的 Gemini 模型。

        Returns
        -------
        str
            拉麵大師風格的回答文字。
        """
        print(f"{GREEN}STEP 2: 執行 Knowledge Skill RAG 檢索{RESET}")

        if USE_FIRESTORE:
            return self._answer_firestore(query)

        if not self.collection or self.collection.count() == 0:
            return "目前知識庫尚無資料，請先在 knowledge/ 目錄放入文件後重啟系統。"

        try:
            query_embedding = self._embed([query], task_type="retrieval_query")[0]
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(TOP_K, self.collection.count()),
            )
            retrieved_chunks: list[str] = results["documents"][0]
            context = "\n\n---\n\n".join(retrieved_chunks)
        except Exception as e:
            print(f"{RED}STEP 2 ERROR: RAG 檢索失敗: {e}{RESET}")
            return "知識庫查詢發生錯誤，請稍後再試。"

        print(f"{GREEN}STEP 3: 使用 Gemini 生成拉麵大師回答{RESET}")
        try:
            if not check_and_increment("llm_gemini"):
                return "LLM 每日使用上限已達，請明天再試。"

            prompt = KNOWLEDGE_ANSWER_PROMPT.format(context=context, query=query)
            with timing.time_llm("knowledge"):
                response = generate_with_retry(
                    lambda: self.client.models.generate_content(
                        model=self.model_name,
                        contents=prompt,
                    ),
                    label="知識問答",
                )
            if response.usage_metadata:
                record_tokens(response.usage_metadata.total_token_count or 0)

            return response.text.strip()
        except Exception as e:
            print(f"{RED}STEP 3 ERROR: 生成回答失敗: {e}{RESET}")
            return "生成回答時發生錯誤，請稍後再試。"

    def _answer_firestore(self, query: str) -> str:
        """
        Firestore KNN 模式：嵌入查詢 → find_nearest() → Gemini 生成回答。

        Parameters
        ----------
        query : str
            使用者的拉麵相關問題。

        Returns
        -------
        str
            拉麵大師風格的回答文字。
        """
        from google.cloud.firestore_v1.vector import Vector
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from services.firestore_client import get_db

        try:
            query_embedding = self._embed([query], task_type="retrieval_query")[0]
            db = get_db()
            results = (
                db.collection("ramen_knowledge")
                .find_nearest(
                    vector_field="embedding",
                    query_vector=Vector(query_embedding),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=TOP_K,
                )
                .get()
            )
            retrieved_chunks = [doc.get("content") for doc in results if doc.get("content")]
            if not retrieved_chunks:
                return "目前 Firestore 知識庫尚無資料，請先執行 migrate_knowledge_to_firestore.py。"
            context = "\n\n---\n\n".join(retrieved_chunks)
        except Exception as e:
            print(f"{RED}STEP 2 ERROR: Firestore KNN 檢索失敗: {e}{RESET}")
            return "知識庫查詢發生錯誤，請稍後再試。"

        print(f"{GREEN}STEP 3: 使用 Gemini 生成拉麵大師回答{RESET}")
        try:
            if not check_and_increment("llm_gemini"):
                return "LLM 每日使用上限已達，請明天再試。"

            prompt = KNOWLEDGE_ANSWER_PROMPT.format(context=context, query=query)
            with timing.time_llm("knowledge"):
                response = generate_with_retry(
                    lambda: self.client.models.generate_content(
                        model=self.model_name,
                        contents=prompt,
                    ),
                    label="知識問答",
                )
            if response.usage_metadata:
                record_tokens(response.usage_metadata.total_token_count or 0)

            return response.text.strip()
        except Exception as e:
            print(f"{RED}STEP 3 ERROR: 生成回答失敗: {e}{RESET}")
            return "生成回答時發生錯誤，請稍後再試。"
