"""OpenAI 兼容 API 封装（v1.7 RAG，仅标准库 urllib）。

双源架构（v1.7 A 凭据中心）：
  生成（chat）   → llm.provider 所选厂商（硅基流动/魔搭/智谱/DeepSeek/自定义）
  检索（embed/rerank）→ 固定硅基流动（providers.siliconflow，免费档够用）

端点三类：
  chat    POST {base}/chat/completions   —— stream SSE 解析，Qwen 系带 thinking 开关
  embed   POST {base}/embeddings         —— 批量向量化（硅基流动）
  rerank  POST {base}/rerank             —— 精排（Cohere 风格，SiliconFlow 同款）

实测参数（2026-09-04 SiliconFlow / Qwen3-8B）：
  - 必须流式：非流式全量 ~36s，流式首字 ~0.6s；
  - Qwen3 默认开 thinking（一次回答多吐几百字推理、耗时 ~4 倍）→
    厂商 thinking=True 时请求带顶层 enable_thinking=False（实测提速 ~4 倍）；
    DeepSeek/智谱等不认识该字段的可能报错 → thinking=False 不带。
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
    """双源封装：chat 走 llm.provider；embed/rerank 固定硅基流动。"""

    def __init__(self, cfg):
        # 生成端点：llm.provider 厂商（base/key/model/是否带 thinking 开关）
        self.chat_base, self.chat_key, self.chat_model = cfg.chat_ep()
        prov = cfg.provider_cfg()
        self.chat_thinking = bool(prov.get("thinking", False))
        # 检索端点：硅基流动固定
        self.retr_base, self.retr_key, self.embed_model = cfg.retrieval_ep()
        sf = (cfg.get("providers") or {}).get("siliconflow") or {}
        self.rerank_model = (sf.get("rerank_model")
                             or "BAAI/bge-reranker-v2-m3")
        self.timeout = float(cfg.get("llm.timeout") or 30)
        if not self.chat_base or not self.chat_key or not self.chat_model:
            name = cfg.get("llm.provider") or "siliconflow"
            raise RagApiError(
                f"未配置生成模型（设置页 → 凭据中心 → 「{name}」填 API Key"
                + (" / 自定义需填 Base URL 与模型" if name == "custom" else "")
                + "）")
        self._opener = _opener()

    # ------------------------------------------------------------ 基础请求
    def _post(self, base, key, path, payload, timeout=None):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{base}{path}", data=body, method="POST")
        for k, v in _headers(key).items():
            req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            raise RagApiError(f"API {e.code}：{detail}")
        except urllib.error.URLError as e:
            raise RagApiError(f"网络错误：{e.reason}")

    def _post_json(self, base, key, path, payload, timeout=None):
        raw = self._post(base, key, path, payload, timeout=timeout)
        try:
            return json.loads(raw)
        except Exception:
            raise RagApiError(f"API 返回非 JSON：{raw[:200]}")

    def _require_retrieval(self):
        """embed/rerank 依赖硅基流动 Key；缺失给引导性报错（引擎会降级）。"""
        if not self.retr_base or not self.retr_key:
            raise RagApiError(
                "检索（向量/重排）需要硅基流动 API Key："
                "设置页 → 凭据中心 → 硅基流动 填写")

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
        if self.chat_thinking:
            payload["enable_thinking"] = False  # 仅 Qwen 系等支持；其余厂商不带
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{self.chat_base}/chat/completions",
                                     data=body, method="POST")
        for k, v in _headers(self.chat_key).items():
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
        """批量向量化，返回与输入同序的向量列表（[ [float,...], ... ]）。

        固定硅基流动端点；未配硅基 Key 时抛 RagApiError（引擎降级纯 BM25）。
        """
        self._require_retrieval()
        out = []
        for i in range(0, len(texts), batch):
            part = texts[i:i + batch]
            data = self._post_json(
                self.retr_base, self.retr_key, "/embeddings",
                {"model": self.embed_model, "input": part},
                timeout=self.timeout * 2)
            arr = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
            out.extend(item.get("embedding") or [] for item in arr)
            if len(out) != i + len(part):
                raise RagApiError("embedding 返回数量与输入不符")
        return out

    # ------------------------------------------------------------ rerank
    def rerank(self, query, documents, top_n=5):
        """精排：返回按相关度降序的 [(原始下标, relevance_score), ...]。

        固定硅基流动端点（rerank 属 Cohere 风格，硅基流动提供）。
        """
        self._require_retrieval()
        if not documents:
            return []
        data = self._post_json(
            self.retr_base, self.retr_key, "/rerank",
            {"model": self.rerank_model, "query": query,
             "documents": documents,
             "top_n": min(top_n, len(documents)),
             "return_documents": False},
            timeout=self.timeout * 2)
        results = []
        for r in (data.get("results") or []):
            results.append((int(r.get("index", 0)),
                            float(r.get("relevance_score", 0.0))))
        results.sort(key=lambda x: x[1], reverse=True)
        return results
