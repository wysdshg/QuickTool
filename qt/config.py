"""配置与本地缓存。

存储策略：
  优先读取「程序同目录/config.json」（绿色便携模式，可放 U 盘）；
  不存在则写入 %APPDATA%\\QuickTool\\config.json。
"""
import json
import os
import sys
import threading
import winreg
from collections import OrderedDict

APP_NAME = "QuickTool"

DEFAULTS = {
    # ---------------- 快捷键 ----------------
    "hotkey_translate": "Ctrl+Q",     # 划词翻译（被占用时自动顺延到 Ctrl+Alt+T 等）
    "hotkey_settings": "Ctrl+Alt+S",
    "hotkey_quit": "Ctrl+Alt+Q",
    "hotkey_ocr": "Ctrl+Alt+A",       # 截图翻译（被占用时自动顺延）
    "hotkey_pin": "Ctrl+Prtsc",       # 截图对照小窗（框选截屏置顶对照）

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
    "deepl": {"api_key": "", "free": True},

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
        return self.data

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

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
