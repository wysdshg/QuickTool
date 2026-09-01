"""运行日志系统（v1.4 新增，针对"托盘静默消失"问题）。

为什么需要：
    2026-08-30 取证发现程序以 0xc0000374（堆损坏）静默崩溃（Windows 事件日志
    Application Error 1000），当日 3 次。堆损坏发生在 C 层，Python 异常捕获
    不到、windowed exe 又没有 stdout——没有日志时只能事后翻事件日志拿异常码，
    拿不到崩溃前最后一步在干什么。本模块把"崩溃前现场"记到磁盘。

日志位置（跟随配置的便携逻辑，与 config.json 同一侧）：
    便携模式   <exe 或项目目录>/logs/QuickTool.log
    安装模式   %APPDATA%/QuickTool/logs/QuickTool.log
轮转：单文件 2MB，保留 3 个历史（QuickTool.log.1 ~ .3）。

崩溃取证三层：
    1. faulthandler  —— 访问违例（0xC0000005 等）时自动把各线程 Python 栈
                        dump 到 logs/crash.log（堆损坏 0xc0000374 在 ntdll
                        内部抛，faulthandler 接不到，但仍保留作兜底）；
    2. 三处异常钩子  —— sys.excepthook / threading.excepthook /
                        Tk.report_callback_exception，任何线程的未捕获异常
                        都带完整 traceback 落盘（windowed exe 无 stdout，
                        这是唯一的 Python 级异常出口）；
    3. 关键事件埋点  —— 启动/热键注册/抓词/翻译/OCR/托盘/退出全程留痕，
                        崩溃后看最后一条即可知道死前在做什么。

设计铁律：日志系统自己绝不能把主程序搞挂——所有写日志动作失败一律吞掉。
"""
import faulthandler
import logging
import os
import sys
import threading
import traceback

APP_NAME = "QuickTool"
_MAX_BYTES = 2 * 1024 * 1024      # 单文件 2MB
_BACKUP_COUNT = 3

# ---------------------------------------------------------------- 日志目录
def log_dir():
    """跟随 config.json 的便携逻辑：程序同目录优先，否则 %APPDATA%。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    portable = os.path.join(base, "logs")
    try:
        os.makedirs(portable, exist_ok=True)
        # 试写一个探针文件确认目录真的可写（exe 放在只读目录时降级）
        probe = os.path.join(portable, ".probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return portable
    except Exception:
        pass
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    fallback = os.path.join(root, APP_NAME, "logs")
    try:
        os.makedirs(fallback, exist_ok=True)
    except Exception:
        pass
    return fallback


_logger = None
_crash_fp = None          # faulthandler 持有的文件句柄，进程生命周期内不关


def _thread_name():
    return threading.current_thread().name


def setup_logging():
    """初始化日志（进程内只调一次，重复调用返回同一 logger）。"""
    global _logger, _crash_fp
    if _logger is not None:
        return _logger
    d = log_dir()
    _logger = logging.getLogger(APP_NAME)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(os.path.join(d, "QuickTool.log"),
                                 maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
                                 encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(fh)
    except Exception:
        pass                                    # 磁盘不可写 → 退化为内存 logger
    # 源码运行时同步打到 stderr，方便开发调试；windowed exe 无 stdout 无所谓
    if "--selftest" not in sys.argv:
        try:
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter(
                "[%(threadName)s] %(message)s"))
            _logger.addHandler(sh)
        except Exception:
            pass
    install_excepthooks(_logger)
    enable_faulthandler(d)
    return _logger


def get_logger():
    """任何模块拿 logger 用；setup 之前调用也能安全拿到（未初始化则先初始化）。"""
    return _logger if _logger is not None else setup_logging()


def log_event(tag, detail=""):
    """关键事件统一入口：log_event("CAPTURE_START", "len=32")。"""
    try:
        get_logger().info(f"{tag} {detail}".strip())
    except Exception:
        pass


def log_exc(tag):
    """记录当前异常的完整 traceback（在 except 块里调用）。"""
    try:
        get_logger().error(f"{tag}\n{traceback.format_exc()}")
    except Exception:
        pass


# ---------------------------------------------------------------- 异常钩子
def install_excepthooks(logger):
    """三处异常钩子全部接管，任何线程的未捕获异常都落盘。

    - sys.excepthook：主线程未捕获异常（Tk mainloop 里的一般走第 3 个钩子）；
    - threading.excepthook：worker 线程未捕获异常（v1.3 的三个 worker 都有
      try/except 包裹，但包裹外的代码——比如 queue.get 本身——漏了就会静默死线程）；
    - Tk.report_callback_exception：Tk 回调（after/bind/事件）里的异常，
      默认只打 stderr——windowed exe 下等于扔进黑洞。
    """
    def _log_sys(exc_type, exc_value, exc_tb):
        # 注意签名必须是 (type, value, tb) 三参——sys.excepthook 就是这样被
        # Python 调用的。曾经误写成单参 args，导致任何未捕获异常都先触发
        # "Error in sys.excepthook"，日志钩子自己先炸（v1.4 白盒测试抓到）。
        try:
            logger.error("UNCAUGHT-MAIN\n"
                         "".join(traceback.format_exception(
                             exc_type, exc_value, exc_tb)))
        except Exception:
            pass
    sys.excepthook = _log_sys

    def _log_threading(args):
        try:
            name = args.thread.name if args.thread else "?"
            logger.error(f"UNCAUGHT-THREAD [{name}]\n"
                         "".join(traceback.format_exception(
                             args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:
            pass
    threading.excepthook = _log_threading
    return logger


def install_tk_hook(root, logger):
    """Tk 回调异常钩子（必须在 root 创建后调用）。

    Tk callback 里的异常默认走 sys.stderr —— windowed exe 下等于黑洞，
    这是"程序莫名其妙没反应/退出"却查无对证的经典盲区。
    """
    def _report(exc, val, tb):
        try:
            logger.error("UNCAUGHT-TK\n"
                         "".join(traceback.format_exception(exc, val, tb)))
        except Exception:
            pass
        # 链回 sys.excepthook（已接管），保证行为与默认一致
        try:
            sys.excepthook(exc, val, tb)
        except Exception:
            pass
    try:
        root.report_callback_exception = _report
    except Exception:
        pass


# ---------------------------------------------------------------- 崩溃转储
def enable_faulthandler(d):
    """访问违例（0xC0000005 等 C 层崩溃）时把各线程 Python 栈 dump 到 crash.log。

    注意：堆损坏 0xc0000374 是 ntdll 在堆操作时主动抛的异常，faulthandler
    接不到它——但相关的越界读写若是 0xC0000005 形态仍会被捕获。保留作兜底。
    """
    global _crash_fp
    try:
        if _crash_fp is None:
            _crash_fp = open(os.path.join(d, "crash.log"),
                             "a", encoding="utf-8", buffering=1)
        faulthandler.enable(_crash_fp, all_threads=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 心跳
def heartbeat(extra=""):
    """定期心跳：进程活着但一行日志都没有时，无法区分"空闲"和"假死"。"""
    try:
        names = [t.name for t in threading.enumerate()]
        get_logger().info(f"HEARTBEAT threads={len(names)} {names} {extra}".strip())
    except Exception:
        pass

