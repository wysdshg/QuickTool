"""翻译调度：缓存 + 引擎回退链 + 长文本截断。"""
from .config import Cache
from .engines import ENGINES, EngineError


class Translator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cache = Cache(capacity=1000)

    def _chain(self):
        main = self.cfg.get("engine", "mymemory")
        fallbacks = list(self.cfg.get("fallback_chain") or [])
        seen, chain = set(), []
        for name in [main] + fallbacks:
            if name in ENGINES and name not in seen:
                seen.add(name)
                chain.append(name)
        return chain

    def translate(self, text):
        """返回 (译文, 引擎名, 是否成功)。永不抛异常——失败也要把原因显示给用户。"""
        src = self.cfg.get("source_lang", "auto")
        tgt = self.cfg.get("target_lang", "zh-CN")
        max_chars = int(self.cfg.get("max_chars", 3000) or 3000)
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]

        errors = []
        for name in self._chain():
            cls = ENGINES.get(name)
            if cls is None:
                continue
            ck = Cache.key(text, name, src, tgt)
            hit = self.cache.get(ck)
            if hit is not None:
                return hit, f"{cls.label} · 缓存", True
            try:
                out = cls().translate(text, src, tgt, self.cfg)
            except EngineError as exc:
                errors.append(f"{name}: {exc}")
                continue
            except Exception as exc:                      # 网络/超时/解析
                errors.append(f"{name}: {type(exc).__name__} {exc}")
                continue
            if out and out.strip():
                if truncated:
                    out += f"\n\n（原文超过 {max_chars} 字符，已截断）"
                self.cache.put(ck, out)
                return out, cls.label, True

        detail = "\n".join(f"· {e}" for e in errors) or "没有可用的翻译引擎"
        return detail, "翻译失败", False
