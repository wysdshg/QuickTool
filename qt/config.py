"""配置与本地缓存。

存储策略：
  优先读取「程序同目录/config.json」（绿色便携模式，可放 U 盘）；
  不存在则写入 %APPDATA%\\QuickTool\\config.json。
"""
import copy
import json
import os
import sys
import threading
import winreg
from collections import OrderedDict

from . import winapi as _wa

APP_NAME = "QuickTool"

DEFAULTS = {
    # ---------------- 快捷键 ----------------
    "hotkey_translate": "Ctrl+Q",     # 划词翻译（被占用时自动顺延到 Ctrl+Alt+T 等）
    "hotkey_settings": "Ctrl+Alt+S",
    "hotkey_quit": "Ctrl+Alt+Q",
    "hotkey_ocr": "Ctrl+Alt+A",       # 截图翻译（被占用时自动顺延）
    "hotkey_pin": "Ctrl+Prtsc",       # 截图对照小窗（框选截屏置顶对照）
    "hotkey_note": "Ctrl+Alt+N",      # 置顶便签（划词后钉到屏幕，可累积多段）
    "hotkey_rag": "Ctrl+Alt+R",       # 快捷提问 RAG（划词或键入问题 → 本地库检索回答）

    # ---------------- 翻译引擎 ----------------
    "engine": "mymemory",                 # 主引擎
    "fallback_chain": ["offline"],        # 主引擎失败后依次尝试
    "source_lang": "auto",
    "target_lang": "zh-CN",

    # 大模型（OpenAI 兼容）：DeepSeek / 硅基流动 / 智谱 / 通义 / Ollama / OpenAI 均可用
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.2,
    },
    # ---------------- RAG 快捷问答（v1.7：本地知识库检索 + 大模型生成） ----------------
    # 检索/索引全部本地（sqlite FTS5 + 可选云端 bge-m3 向量），零第三方依赖；
    # 生成/向量/重排走 OpenAI 兼容 API，默认硅基流动免费档，可自由换服务商。
    "rag": {
        "api": {
            "base_url": "https://api.siliconflow.cn/v1",
            "api_key": "",          # 落盘为 DPAPI 密文（见 _SECRET_FIELDS），内存为明文
            "chat_model": "Qwen/Qwen3-8B",
            "embed_model": "BAAI/bge-m3",
            "rerank_model": "BAAI/bge-reranker-v2-m3",
            "timeout": 30,          # 常规请求超时（秒）
            "thinking_off": True,   # Qwen3 关思考模式：提速 ~4 倍（实测 20.9s->5.4s）
        },
        "kb": {
            "dir": "",              # 留空 = 与 config.json 同目录的 kb\（便携模式随 exe 走）
            "chunk_chars": 500,     # chunk 目标字符数（中文按字符）
            "chunk_overlap": 50,    # 相邻 chunk 重叠字符（防切词腰斩）
        },
        "retrieval": {
            "fusion_variants": 3,   # ① RAG-Fusion 变体数
            "bm25_top_k": 30,       # ② BM25 路召回数
            "vector_top_k": 30,     # ② bge-m3 路召回数
            "rrf_top_k": 20,        # ③ RRF 融合后取 TOP-20
            "rerank_top_k": 5,      # ④ Rerank 精排后 TOP-5
            "enable_vector": True,  # 向量路开关（平台无 embedding 时关）
            "enable_rerank": True,  # Rerank 开关（平台无 rerank 端点时关）
        },
    },

    # ---------------- 界面 ----------------
    "popup": {
        "theme": "dark",        # dark | light
        "alpha": 0.97,
        "auto_hide_ms": 0,      # 0 = 不自动隐藏
        "max_width": 520,
        "font_size": 13,
        "follow_cursor": True,
    },

    # ---------------- 其他 ----------------
    "max_chars": 3000,          # 超长文本截断，避免拖慢接口
    "autostart": False,
    "enable_tray": True,
    "ocr_lang": "auto",         # 截图 OCR 语言；auto = 优先 en-US，否则首个可用语言
    # 选中文字后自动显示『译』迷你按钮（点一下翻译；0.5s 内不重复触发）
    "mini_button": True,
    "mini_button_maxlen": 200,  # 超过此长度不弹按钮，避免误抓整页
}

_LANG_OPTIONS = [
    ("zh-CN", "简体中文"), ("zh-TW", "繁体中文"), ("en", "英语"),
    ("ja", "日语"), ("ko", "韩语"), ("fr", "法语"), ("de", "德语"),
    ("es", "西班牙语"), ("ru", "俄语"),
]

# 落盘自动 DPAPI 加密的密钥字段（点分路径）。值在内存中保持明文（引擎/设置页
# 直接读），save() 时替换为 "__dpapi__:<base64>" 密文，load() 时自动解回明文。
# 旧版明文配置零手动迁移：load 读到明文原样保留，首次 save 即自动转密文。
_SECRET_FIELDS = ("llm.api_key", "rag.api.api_key")
_ENC_PREFIX = "__dpapi__:"


def _portable_path():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "config.json")


def _appdata_path():
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(root, APP_NAME, "config.json")


class Config:
    def __init__(self):
        self._lock = threading.RLock()
        self.path = self._resolve_path()
        self.data = json.loads(json.dumps(DEFAULTS))
        self.load()

    @staticmethod
    def _resolve_path():
        p = _portable_path()
        if os.path.isfile(p):
            return p
        return _appdata_path()

    def _merge(self, base, override):
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._merge(base[k], v)
            else:
                base[k] = v

    def load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._merge(self.data, json.load(f))
            except Exception:
                pass            # 配置坏了就用默认值，绝不让程序起不来
        self._decrypt_secrets()  # "__dpapi__:" 密文解回明文（失败保留原值）
        return self.data

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            out = self._encrypted_copy()    # 密钥字段转 DPAPI 密文再落盘
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ------------------------------------------------------------------ 密钥加密
    def _decrypt_secrets(self):
        """把内存中带 _ENC_PREFIX 的密钥字段解回明文（load 后调用）。

        解密失败（换机/换账户导致密文与当前凭据不匹配）时静默保留原密文：
        引擎会得到无效 key 而报错，设置页重填一次即可，程序绝不因此崩溃。
        """
        for dotted in _SECRET_FIELDS:
            parts = dotted.split(".")
            node = self.data
            for p in parts[:-1]:
                node = node.get(p) if isinstance(node, dict) else None
                if node is None:
                    break
            if not isinstance(node, dict):
                continue
            val = node.get(parts[-1])
            if isinstance(val, str) and val.startswith(_ENC_PREFIX):
                try:
                    node[parts[-1]] = _wa.dpapi_decrypt(val[len(_ENC_PREFIX):])
                except Exception:
                    pass

    def _encrypted_copy(self):
        """deepcopy 后把非空明文密钥替换为 DPAPI 密文（save 用，不改内存态）。

        DPAPI 不可用（异常环境）时降级写明文并继续——保证功能可用优先。
        """
        data = copy.deepcopy(self.data)
        for dotted in _SECRET_FIELDS:
            parts = dotted.split(".")
            node = data
            for p in parts[:-1]:
                node = node.get(p) if isinstance(node, dict) else None
                if node is None:
                    break
            if not isinstance(node, dict):
                continue
            val = node.get(parts[-1])
            if isinstance(val, str) and val and \
                    not val.startswith(_ENC_PREFIX):
                try:
                    node[parts[-1]] = _ENC_PREFIX + _wa.dpapi_encrypt(val)
                except Exception:
                    pass
        return data

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted, value):
        with self._lock:
            parts = dotted.split(".")
            node = self.data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    # ------------------------------- 开机自启 -------------------------------
    def apply_autostart(self, enable=None):
        """写 HKCU\\...\\Run，不需要管理员权限。"""
        if enable is None:
            enable = bool(self.data.get("autostart"))
        exe = f'"{sys.executable}"' if getattr(sys, "frozen", False) else \
              f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
        key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                if enable:
                    winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, exe)
                else:
                    try:
                        winreg.DeleteValue(k, APP_NAME)
                    except FileNotFoundError:
                        pass
            return True, ""
        except Exception as e:
            return False, str(e)


# ---------------------------------------------------------------- 翻译缓存
class Cache:
    """内存 LRU 缓存。命中时 0 延迟、0 流量，长文阅读体验差别很大。"""

    def __init__(self, capacity=1000):
        self.capacity = capacity
        self._d = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(text, engine, src, tgt):
        return f"{engine}|{src}|{tgt}|{text}"

    def get(self, k):
        with self._lock:
            if k in self._d:
                self._d.move_to_end(k)
                return self._d[k]
            return None

    def put(self, k, v):
        with self._lock:
            self._d[k] = v
            self._d.move_to_end(k)
            while len(self._d) > self.capacity:
                self._d.popitem(last=False)
