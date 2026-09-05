"""OpenAI 兼容 API 封装（v1.7 RAG，仅标准库 urllib）。

覆盖三个端点（模型可自由配置，默认硅基流动免费档）：
  chat    POST {base}/chat/completions   —— stream SSE 解析，默认关 thinking
  embed   POST {base}/embeddings         —— 批量向量化
  rerank  POST {base}/rerank             —— 精排（Cohere 风格，SiliconFlow 同款）

实测参数（2026-09-04 SiliconFlow / Qwen3-8B）：
  - 必须流式：非流式全量 ~36s，流式首字 ~0.6s；
  - Qwen3 默认开 thinking（一次回答多吐几百字推理、耗时 ~4 倍）→
    默认请求带顶层 enable_thinking=False（rag.api.thinking_off 可关）。
  - 直连（绕系统代理，实测系统代理对 api.siliconflow.cn 会 502）。
"""
import json
import urllib.error
import urllib.request


class RagApiError(Exception):
    pass


def _headers(key):
    return {"Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class RagApi:
    """从 qt.config 的 rag.api 段读取 base_url / api_key / 三模型。"""

    def __init__(self, cfg):
        api = cfg.get("rag.api") or {}
        self.base = (api.get("base_url") or "").rstrip("/")
        self.key = (api.get("api_key") or "").strip()
        self.chat_model = api.get("chat_model") or "Qwen/Qwen3-8B"
        self.embed_model = api.get("embed_model") or "BAAI/bge-m3"
        self.rerank_model = api.get("rerank_model") or "BAAI/bge-reranker-v2-m3"
        self.timeout = float(api.get("timeout") or 30)
        self.thinking_off = bool(api.get("thinking_off", True))
        if not self.base or not self.key:
            raise RagApiError("未配置 RAG API（设置页 → RAG 快捷问答 填写并保存）")
        self._opener = _opener()

    # ------------------------------------------------------------ 基础请求
    def _post(self, path, payload, timeout=None):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{self.base}{path}", data=body, method="POST")
        for k, v in _headers(self.key).items():
            req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            raise RagApiError(f"API {e.code}：{detail}")
        except urllib.error.URLError as e:
            raise RagApiError(f"网络错误：{e.reason}")

    def _post_json(self, path, payload, timeout=None):
        raw = self._post(path, payload, timeout=timeout)
        try:
            return json.loads(raw)
        except Exception:
            raise RagApiError(f"API 返回非 JSON：{raw[:200]}")

    # ------------------------------------------------------------ chat（流式）
    def chat(self, messages, max_tokens=1024, temperature=0.3,
             on_delta=None, timeout=None):
        """流式调用 chat/completions；on_delta(content, reasoning) 逐段回调。

        返回完整正文（不含 reasoning）。取消/异常分别抛 RagApiError。
        """
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.thinking_off:
            payload["enable_thinking"] = False   # SiliconFlow/Qwen3；未知服务一般忽略
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{self.base}/chat/completions",
                                     data=body, method="POST")
        for k, v in _headers(self.key).items():
            req.add_header(k, v)
        content, reasoning = [], []
        try:
            with self._opener.open(req, timeout=timeout or self.timeout * 3) as resp:
                for line in resp:
                    line = line.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                    except Exception:
                        continue
                    delta = (d.get("choices") or [{}])[0].get("delta") or {}
                    c = delta.get("content") or ""
                    r = delta.get("reasoning_content") or ""
                    if c:
                        content.append(c)
                    if r:
                        reasoning.append(r)
                    if on_delta and (c or r):
                        on_delta(c, r)
        except urllib.error.HTTPError as e:
            raise RagApiError(f"chat {e.code}："
                              f"{e.read().decode('utf-8', 'ignore')[:300]}")
        except urllib.error.URLError as e:
            raise RagApiError(f"网络错误：{e.reason}")
        return "".join(content).strip()

    def chat_once(self, messages, max_tokens=1024, temperature=0.3):
        """非流式便捷调用（RAG-Fusion 变体生成这类短输出用）。"""
        return self.chat(messages, max_tokens=max_tokens,
                         temperature=temperature)

    # ------------------------------------------------------------ embedding
    def embed(self, texts, batch=32):
        """批量向量化，返回与输入同序的向量列表（[ [float,...], ... ]）。"""
        out = []
        for i in range(0, len(texts), batch):
            part = texts[i:i + batch]
            data = self._post_json("/embeddings",
                                   {"model": self.embed_model,
                                    "input": part},
                                   timeout=self.timeout * 2)
            arr = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
            out.extend(item.get("embedding") or [] for item in arr)
            if len(out) != i + len(part):
                raise RagApiError("embedding 返回数量与输入不符")
        return out

    # ------------------------------------------------------------ rerank
    def rerank(self, query, documents, top_n=5):
        """精排：返回按相关度降序的 [(原始下标, relevance_score), ...]。"""
        if not documents:
            return []
        data = self._post_json("/rerank",
                               {"model": self.rerank_model,
                                "query": query, "documents": documents,
                                "top_n": min(top_n, len(documents)),
                                "return_documents": False},
                               timeout=self.timeout * 2)
        results = []
        for r in (data.get("results") or []):
            results.append((int(r.get("index", 0)),
                            float(r.get("relevance_score", 0.0))))
        results.sort(key=lambda x: x[1], reverse=True)
        return results
