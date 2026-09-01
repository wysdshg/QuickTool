"""v1.4 加固点专项验证（畸形剪贴板数据 + 日志轮转）。

    python tests/harden_check.py

场景一：往剪贴板塞「没有 NUL 终止符」的 CF_TEXT / CF_UNICODETEXT 畸形数据，
        验证 get_clipboard_text 按 GlobalSize 有界读、不越界、不崩溃
        （旧实现 string_at 读到 NUL 为止，遇到这种数据会越界读堆）。
场景二：日志轮转——灌 2.5MB 日志，验证 RotatingFileHandler 确实切文件。
"""
import ctypes
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qt import winapi as wa

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK]   {name} {extra}")
    else:
        fail += 1
        print(f"  [FAIL] {name} {extra}")


print("== 场景一：畸形剪贴板数据（无 NUL 终止符）==")
CF_TEXT = 1
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _put_raw(fmt, payload: bytes):
    """直接把不带终止符的原始字节塞进剪贴板（模拟坏程序/坏数据）。"""
    for _ in range(5):
        if wa.user32.OpenClipboard(None):
            break
        import time
        time.sleep(0.02)
    try:
        wa.user32.EmptyClipboard()
        handle = wa.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        ptr = wa.kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, payload, len(payload))
        wa.kernel32.GlobalUnlock(handle)
        wa.user32.SetClipboardData(fmt, handle)
    finally:
        wa.user32.CloseClipboard()


# 1) CF_TEXT：纯 ASCII、无 NUL 结尾 —— 旧版 string_at 会读越界
raw = b"malformed-no-nul-terminator-abcdefghijklmnopqrstuvwxyz0123456789"
_put_raw(CF_TEXT, raw)
got = wa.get_clipboard_text()
check("CF_TEXT 无 NUL 不越界", isinstance(got, str) and "malformed" in got,
      f"-> {got[:40]!r}")

# 2) CF_UNICODETEXT：UTF-16 无 NUL、奇数字节（截齐偶数逻辑）。
#    注意：SetClipboardData 会对无终止符的 CF_UNICODETEXT 块做系统级规范化
#    （实测 16 字节「8 字符无 NUL」被系统改为「7 字符+NUL」），所以这里只断言
#    「有界读不越界、内容主体在」——校验读取完整性请看下面带 NUL 的正常用例。
data = "畸形中文data".encode("utf-16-le")          # 无结尾 \x00
_put_raw(CF_UNICODETEXT, data)
got = wa.get_clipboard_text()
check("CF_UNICODETEXT 无 NUL 不越界", isinstance(got, str)
      and got.startswith("畸形中文dat"), f"-> {got!r}")

# 2.5) CF_UNICODETEXT：正常数据（带 NUL 终止符）—— 读取必须逐字完整
normal = "normal text 正常数据"
_put_raw(CF_UNICODETEXT, normal.encode("utf-16-le") + b"\x00\x00")
got = wa.get_clipboard_text()
check("CF_UNICODETEXT 正常数据逐字读回", got == normal, f"-> {got!r}")

# 3) CF_UNICODETEXT：奇数长度字节流（UTF-16 半截字符）
odd = data[:-1]                                    # 砍掉最后一个字节
_put_raw(CF_UNICODETEXT, odd)
got = wa.get_clipboard_text()                      # 不应抛异常
check("奇数字节不崩溃", isinstance(got, str) and len(got) >= 0,
      f"-> {got[:20]!r}")

# 4) 快照跳过非 HGLOBAL：塞一张真正的位图句柄进剪贴板再快照
try:
    hdc = wa.user32.GetDC(None)
    bmp = wa.gdi32.CreateCompatibleBitmap(hdc, 8, 8)
    for _ in range(5):
        if wa.user32.OpenClipboard(None):
            break
        import time
        time.sleep(0.02)
    try:
        wa.user32.EmptyClipboard()
        wa.user32.SetClipboardData(2, bmp)         # CF_BITMAP = 2
    finally:
        wa.user32.CloseClipboard()
    snap = wa.clipboard_snapshot()
    check("快照跳过 CF_BITMAP（非 HGLOBAL）",
          isinstance(snap, dict) and 2 not in snap,
          f"格式={sorted(snap.keys())}")
except Exception as exc:
    check("快照跳过 CF_BITMAP", False, repr(exc))

# 5) 还原位图剪贴板后不崩溃
try:
    snap = wa.clipboard_snapshot()
    wa.set_clipboard_text("临时覆盖")
    wa.clipboard_restore(snap)
    check("带位图的剪贴板快照/还原安全", True)
except Exception as exc:
    check("带位图的剪贴板快照/还原安全", False, repr(exc))

# 6) set_clipboard_text 空串/长文本（边界）
check("空串写入安全", wa.set_clipboard_text("") is True
      or wa.get_clipboard_text() != None)
big = "x" * 200000
wa.set_clipboard_text(big)
check("20 万字符写入读回", wa.get_clipboard_text() == big)

print("\n== 场景二：日志轮转 ==")
from qt import logging_setup as ls

log = ls.setup_logging()
log_dir = ls.log_dir()
main_log = os.path.join(log_dir, "QuickTrans.log")
size0 = os.path.getsize(main_log) if os.path.exists(main_log) else 0
for i in range(1500):                               # 灌 >2MB 触发轮转
    log.info("ROTATE-PROBE line=%d %s", i, "y" * 1800)
for h in log.handlers:
    try:
        h.flush()
    except Exception:
        pass
files = sorted(os.listdir(log_dir))
rolled = [f for f in files if f.startswith("QuickTrans.log")]
check("日志文件发生轮转", len(rolled) >= 2, f"-> {rolled}")
size1 = os.path.getsize(main_log)
check("主日志未无限膨胀", size1 <= 2 * 1024 * 1024 + 4096, f"{size0} -> {size1} 字节")
# 清掉轮转测试产生的日志（纯测试污染，避免下次排查时误读）
try:
    for f in rolled:
        os.remove(os.path.join(log_dir, f))
except Exception:
    pass

print("\n== 场景三：并发剪贴板访问压力（v1.4.1 加锁回归）==")
# 模拟真实并发：CaptureWorker（get/snapshot）+ 主线程（set）同时打剪贴板。
# 修复前：OpenClipboard 竞态 → GetClipboardData 失效句柄 → 堆损坏 0xc0000374
# （进程直接死，栈停在 _hglobal_read 的 GlobalSize）。修复后：全部操作被
# _CLIP_LOCK 串行化，多线程高并发必须不崩、无异常。
import threading

_snap0 = wa.clipboard_snapshot()          # 用户当前剪贴板，测完还原
_results = []
_errors = []


def _cb_worker(wid, n):
    for i in range(n):
        try:
            op = (wid + i) % 3
            if op == 0:
                _results.append(("get", len(wa.get_clipboard_text())))
            elif op == 1:
                s = wa.clipboard_snapshot()
                _results.append(("snap", len(s or {})))
            else:
                okk = wa.set_clipboard_text(f"thread-{wid}-{i}")
                _results.append(("set", 1 if okk else 0))
        except Exception as exc:
            _errors.append((wid, repr(exc)))


threads = [threading.Thread(target=_cb_worker, args=(w, 150)) for w in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("并发 4 线程×150 次操作无异常", not _errors, f"errors={_errors[:3]}")
check("全部操作完成", len(_results) == 4 * 150, f"ops={len(_results)}")
wa.set_clipboard_text("final-consistency-check-888")
check("加锁后写读一致性", wa.get_clipboard_text() == "final-consistency-check-888")
try:
    wa.clipboard_restore(_snap0)
    check("测试后还原用户剪贴板", True)
except Exception as exc:
    check("测试后还原用户剪贴板", False, repr(exc))

print(f"\n==== 通过 {ok} 项，失败 {fail} 项 ====")
sys.exit(1 if fail else 0)
