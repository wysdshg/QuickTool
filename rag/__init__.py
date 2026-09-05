"""QuickTool RAG 本地知识库检索（v1.7）。

目标：解决「AI 回答里大量不懂的专业名词、需反复追问打断阅读」。
管线（工业级五阶段，默认全部启用，各阶段可配置开关）：
  ① RAG-Fusion：生成 3 个问题变体并行召回（待接 LLM）
  ② 两路召回：FTS5 BM25（本模块，本地零成本）+ bge-m3 向量（云端，待接）
  ③ RRF 融合排序
  ④ Rerank 精排 TOP-20 -> TOP-5（云端 bge-reranker）
  ⑤ 大模型流式生成（OpenAI 兼容，可自由配置）

分层约束：
  - rag/ 为纯逻辑包：不 import tkinter，可独立单测；只依赖 qt.config / qt.winapi。
  - 当前已落地：splitter（标题感知切 chunk）+ store（kb.sqlite/FTS5/trigram）
    + bm25（本地检索：MATCH 主路 + LIKE 兜底）。
  - 待落地：vectors / fusion / rerank / gen / api（SSE）→ engine 总装 → UI。
"""
from .splitter import split_text
from .store import KbStore
from . import bm25
from . import vectors
from .api import RagApi, RagApiError

__all__ = ["split_text", "KbStore", "bm25", "vectors", "RagApi",
           "RagApiError", "search"]

__version__ = "0.2.0"   # rag 子包自迭代版本，独立于 QuickTool 主版本


def search(store: KbStore, query_text: str, top_k: int = 8):
    """便捷入口：本地检索（当前 = BM25 主路 + LIKE 兜底；后续并入向量/RRF）。"""
    return bm25.search(store, query_text, top_k=top_k)
