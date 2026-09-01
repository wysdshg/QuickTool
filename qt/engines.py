"""翻译引擎（全部基于标准库 urllib，不引入 requests）。

每个引擎实现同一个接口：
    translate(text, src, tgt, cfg) -> str
失败时抛 EngineError，由 translator.py 走回退链。
"""
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request

from .config import Config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class EngineError(Exception):
    pass


# ------------------------------------------------------------------ 工具
def http_json(url, data=None, headers=None, timeout=8.0, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    return json.loads(raw)


def http_form(url, fields, headers=None, timeout=8.0):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    hdrs = {"Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": UA}
    hdrs.update(headers or {})
    return http_json(url, data=body, headers=hdrs, timeout=timeout)


def chunked(text, size=4500):
    """长文本按段落切块，逐块翻译后拼接（各家接口都有单次长度上限）。"""
    if len(text) <= size:
        return [text]
    parts, buf = [], ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > size:
            if buf:
                parts.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        parts.append(buf)
    return parts or [text]


# ------------------------------------------------------------------ 1. MyMemory
class MyMemoryEngine:
    name = "mymemory"
    label = "MyMemory（免费 · 无需 Key）"
    need_key = False
    url = "https://api.mymemory.translated.net/get"

    def translate(self, text, src, tgt, cfg):
        # MyMemory 不支持 auto，且语种代码不带地区后缀（zh-CN -> zh）
        src_code = (src or "en").split("-")[0]
        if src_code in ("", "auto"):
            src_code = "en"
        pair = f"{src_code}|{(tgt or 'zh-CN').split('-')[0]}"
        outs = []
        for piece in chunked(text, 450):        # 免费接口对单次长度敏感
            data = http_json(
                f"{self.url}?{urllib.parse.urlencode({'q': piece, 'langpair': pair})}",
                headers={"User-Agent": UA}, timeout=8.0)
            if data.get("responseStatus") not in (200, "200"):
                raise EngineError(data.get("responseDetails") or "MyMemory 返回异常")
            out = (data.get("responseData") or {}).get("translatedText") or ""
            if "MYMEMORY WARNING" in out:
                raise EngineError("MyMemory 今日免费额度已用完")
            outs.append(out)
        return "\n".join(outs).strip()


# ------------------------------------------------------------------ 2. Google（非官方端点）
class GoogleGtxEngine:
    name = "google"
    label = "Google 网页接口（免费 · 国内常被墙）"
    need_key = False
    url = "https://translate.googleapis.com/translate_a/single"

    def translate(self, text, src, tgt, cfg):
        query = urllib.parse.urlencode({
            "client": "gtx", "sl": src or "auto", "tl": tgt or "zh-CN",
            "dt": "t", "q": text[:4500],
        })
        data = http_json(f"{self.url}?{query}", headers={"User-Agent": UA}, timeout=5.0)
        try:
            return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
        except Exception as exc:
            raise EngineError(f"Google 返回结构异常：{exc}")


# ------------------------------------------------------------------ 3. 大模型（OpenAI 兼容）
class LLMEngine:
    name = "llm"
    label = "大模型 API（DeepSeek / 硅基流动 / 智谱 / 通义 / Ollama）"
    need_key = True

    SYSTEM = ("You are a professional translation engine. "
              "Translate the user's text into {tgt}. "
              "Output ONLY the translation, no explanation, no quotes. "
              "Preserve the original line breaks, punctuation style and formatting.")

    def translate(self, text, src, tgt, cfg):
        llm = cfg.get("llm") or {}
        base = (llm.get("base_url") or "").rstrip("/")
        key = (llm.get("api_key") or "").strip()
        model = llm.get("model") or "deepseek-chat"
        if not base or not key:
            raise EngineError("未配置大模型 API（设置里填 base_url / api_key / model）")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self.SYSTEM.format(tgt=tgt)},
                {"role": "user", "content": text},
            ],
            "temperature": float(llm.get("temperature", 0.2)),
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {key}",
                   "User-Agent": UA}
        data = http_json(f"{base}/chat/completions", data=body, headers=headers, timeout=30.0)
        try:
            out = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise EngineError(f"大模型返回异常：{exc}")
        return re.sub(r"^\s*(译文|翻译结果)[:：]\s*", "", out.strip())


# ------------------------------------------------------------------ 4. DeepL
class DeepLEngine:
    name = "deepl"
    label = "DeepL API（50 万字符/月免费）"
    need_key = True

    def translate(self, text, src, tgt, cfg):
        cfg_deepl = cfg.get("deepl") or {}
        key = (cfg_deepl.get("api_key") or "").strip()
        if not key:
            raise EngineError("未配置 DeepL API Key")
        host = "api-free.deepl.com" if cfg_deepl.get("free", True) else "api.deepl.com"
        data = http_form(
            f"https://{host}/v2/translate",
            {"text": text, "source_lang": (src or "EN").split("-")[0].upper(),
             "target_lang": (tgt or "ZH").split("-")[0].upper()},
            headers={"Authorization": f"DeepL-Auth-Key {key}"}, timeout=15.0)
        try:
            return "".join(t["text"] for t in data["translations"]).strip()
        except Exception as exc:
            raise EngineError(f"DeepL 返回异常：{exc}")


# ------------------------------------------------------------------ 5. 离线兜底
class OfflineEngine:
    """断网 / 接口全挂时的兜底：内置高频词库。

    只覆盖单词级查词，长句会直接提示需要联网。想要完整离线词典，
    把 ECDICT（77 万词）转成 word<TAB>释义 存到 data/ecdict.txt 即可自动加载。
    """
    name = "offline"
    label = "离线词库（单词级 · 断网可用）"
    need_key = False
    _lock = threading.Lock()
    _words = None

    @classmethod
    def _data_dir(cls):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return os.path.join(base, "data")
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    @classmethod
    def _load(cls):
        with cls._lock:
            if cls._words is not None:
                return cls._words
            words = {}
            mini = os.path.join(cls._data_dir(), "mini_dict.json")
            try:
                with open(mini, "r", encoding="utf-8") as f:
                    words.update({k.lower(): v for k, v in json.load(f).items()})
            except Exception:
                pass
            extra = os.path.join(cls._data_dir(), "ecdict.txt")
            if os.path.isfile(extra):
                try:
                    with open(extra, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            w, _, d = line.partition("\t")
                            if w and d.strip():
                                words.setdefault(w.strip().lower(), d.strip())
                except Exception:
                    pass
            cls._words = words
            return words

    _SUFFIXES = [("ies", "y"), ("es", ""), ("s", ""), ("ing", ""), ("ed", ""),
                 ("ly", ""), ("er", ""), ("est", "")]

    def translate(self, text, src, tgt, cfg):
        words = self._load()
        token = re.sub(r"[^\w'\- ]", " ", text.strip()).strip()
        if not words:
            return "[离线] 词库未加载"
        if " " in token or len(token) > 32:
            return "[离线] 长句需要联网翻译，当前仅支持单词/短语查询"
        for cand in self._candidates(token.lower()):
            if cand in words:
                return words[cand]
        raise EngineError(f"[离线] 词库中未收录「{token}」")

    @classmethod
    def _candidates(cls, w):
        yield w
        for suf, rep in cls._SUFFIXES:
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                yield w[: -len(suf)] + rep
        if w.endswith("ed") and len(w) > 4:
            yield w[:-1]                       # stopped -> stoppe -> 再 -e
        if w.endswith("ing") and len(w) > 5:
            yield w[:-3] + "e"


ENGINES = {cls.name: cls for cls in
           (MyMemoryEngine, GoogleGtxEngine, LLMEngine, DeepLEngine, OfflineEngine)}


def engine_display_names():
    return [(name, cls.label, cls.need_key) for name, cls in ENGINES.items()]


def available_engines(cfg: Config):
    """过滤掉没配 Key 的引擎，供设置界面下拉框使用。"""
    out = []
    for name, cls in ENGINES.items():
        if cls.need_key:
            sec = cfg.get(name) or {}
            if not (sec.get("api_key") or "").strip():
                continue
        out.append((name, cls.label))
    return out
