"""
QuickTool —— 极简 Windows 效率工具集（划词翻译 / 截图 OCR / 截图对照 / 置顶便签）。
运行时零第三方依赖，底层全部基于 Win32 API。

线程模型（四个线程，靠 queue.Queue 单向通信）：
    主线程          tkinter 事件循环（悬浮窗 / 设置界面）
    Win32Thread     隐藏窗口 + 消息循环：全局热键、托盘、鼠标拖选钩子
                    —— 只做「轻量判断 + 投递消息」，绝不干阻塞的活
    CaptureWorker   抓词（模拟 Ctrl+C + 等剪贴板，最坏 ~0.7s）
    JobWorker       慢任务：联网翻译 / 截图 OCR（秒级）

跨线程状态纪律：共享可变属性必须消除或用队列/消息建立 happens-before——
翻译文本、OCR 图片路径、便签来源标题一律放进队列 payload 随事件流动
（v1.6.7 ③ 审计修复），不再「一个属性多个线程随手读写」。

为什么要把重活从 Win32Thread 挪走：它同时是 WH_MOUSE_LL 鼠标钩子的宿主
线程，低层钩子回调靠这个线程的消息泵驱动。一旦被同步阻塞，系统派发鼠标
消息就会变慢，表现为「拖选一下鼠标卡一下」——尤其是没选中内容时，抓词要
等满 timeout×retries。实测修复前钩子线程往返延迟 800ms，修复后 0ms
（见 tests/lag_workers.py）。

Tk 不是线程安全的，绝不能跨线程直接操作控件，一律走队列。

用法：
    python main.py                源码运行
    python main.py --settings     启动后直接打开设置
"""
import ctypes
import os
import queue
import sys
import tempfile
import threading
import traceback

# ---- 让 Tk 在高分屏上不糊（必须在创建任何窗口前调用）------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)      # 系统级 DPI 感知
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

from qt import winapi as wa
from qt import logging_setup as ls
from qt import ocr
from qt.capture import get_selected_text, looks_translatable, normalize_text
from qt.config import Config
from qt.translator import Translator
from qt.ui import (MiniButton, NoteWindow, PinWindow, Popup,
                   RegionSelector, Settings)

APP_VERSION = "1.6.9"

HK_TRANSLATE, HK_SETTINGS, HK_QUIT, HK_OCR, HK_PIN, HK_NOTE = 1, 2, 3, 4, 5, 6
WM_APP_TRAY_TOGGLE = wa.WM_APP + 3
# 注：WM_APP+4/+6 曾是「迷你按钮翻译 / OCR 运行」的 Win32 线程中转消息，
# v1.6.7 ③ 已改为直接入 job_q（文本/图片路径随 payload 走），故删除。

# 各功能键被占用时的自动顺延列表（本机 Ctrl+Alt+T/S 常被输入法/网盘占用）。
# 注意：必须用 HK_* 整数做键——_register_hotkeys 传的是 hid（int），
# 之前误用字符串键导致 .get(hid) 永远查不到，顺延链形同虚设（已修）。
HOTKEY_CANDIDATES = {
    HK_TRANSLATE: ["Ctrl+Q", "Ctrl+Alt+T", "Ctrl+Alt+F1", "Ctrl+Alt+D",
                   "Ctrl+Alt+G", "Ctrl+Alt+X", "Ctrl+Shift+T", "Alt+T"],
    HK_SETTINGS: ["Ctrl+Alt+S", "Ctrl+Alt+Shift+S", "Ctrl+Alt+,",
                  "Ctrl+Alt+O", "Ctrl+Alt+P", "Ctrl+Alt+F8"],
    HK_QUIT: ["Ctrl+Alt+Q", "Ctrl+Alt+Shift+Q", "Ctrl+Alt+X"],
    HK_OCR: ["Ctrl+Alt+A", "Ctrl+Alt+I", "Ctrl+Alt+P", "Ctrl+Alt+F9"],
    HK_PIN: ["Ctrl+Prtsc", "Ctrl+Alt+W", "Ctrl+Alt+D", "Ctrl+Alt+E"],
    # 置顶便签：N = Note；顺延要避开上面已用的 D/E/W
    HK_NOTE: ["Ctrl+Alt+N", "Ctrl+Alt+M", "Ctrl+Alt+B", "Ctrl+Alt+K"],
}

TRAY_SETTINGS, TRAY_QUIT, TRAY_OCR, TRAY_PIN, TRAY_NOTE = (1001, 1002,
                                                            1003, 1004, 1005)
TRAY_ITEMS = [(TRAY_SETTINGS, "设置"), (TRAY_OCR, "截图翻译"),
              (TRAY_PIN, "截图对照"), (TRAY_NOTE, "置顶便签"),
              (0, None), (TRAY_QUIT, "退出")]

E2E_LOG = os.path.join(tempfile.gettempdir(), "QuickTool_e2e.log")


def e2e_log(tag):
    """--selftest 模式下把关键节点写文件（windowed exe 没有 stdout）。"""
    if "--selftest" not in sys.argv:
        return
    line = f"EVENT:{tag}"
    print(line, flush=True)
    try:
        with open(E2E_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class App:
    def __init__(self):
        self.cfg = Config()
        self.translator = Translator(self.cfg)
        self.q = queue.Queue()
        self.popup = None
        self.popup_open = False
        self.settings = None
        self.mini_btn = None
        self.drag = None                # 鼠标拖选钩子（Win32 线程）
        # ---- 跨线程状态（v1.6.7 ③ 审计后剩余项 + 归属注释）----
        # _selecting：写=主线程（open_ocr/open_pin 及选区回调 finally），
        #   读=Win32 拖选守卫。GIL 下读的是连贯快照；时序靠选区回调的
        #   finally 复位与 WM_APP_DRAG_END 消息配对（见 v1.5.3 竞态修复）。
        self._selecting = False         # 截图选区流程进行中/刚结束（防钩子抢拖拽）
        # _last_source：单写者=Win32 线程（热键/托盘 Note 按下瞬间抓前台标题）；
        #   读=CaptureWorker._capture_for_note——写后经 q("note")->主线程->
        #   cap_q 整链入队，队列同步保证读者必见最新值。拖选路径不再读它：
        #   来源标题随 drag 请求 payload 直达迷你按钮（见 _handle_drag_end）。
        self._last_source = ""          # 便签段头显示来源（Note 触发时记录）
        # _mini_source：迷你按钮携带的来源（主线程专属，无跨线程读写）
        self._mini_source = ""          # 最近一次 mini_show 的来源窗口标题
        self.ocr_selector = None        # 截图框选遮罩（主线程）
        self.pin_wins = []              # 截图对照小窗列表（可多个并存）
        self.note_win = None            # 置顶便签（单窗口累积，不并存多个）
        # 后台 worker 队列：重活一律不放进 Win32 消息循环线程（见 _capture_worker）
        self._cap_q = queue.Queue()     # 抓词（模拟 Ctrl+C + 等剪贴板）
        self._cap_lock = threading.Lock()   # 非阻塞 acquire 当作"进行中"标志
        self._job_q = queue.Queue()     # 慢任务：联网翻译 / 截图 OCR
        self.root = None
        self.hwnd = None
        self.tray = None
        self._taskbar_created = 0    # explorer 重启广播消息号（_win_thread 里注册）
        # _shutting_down：写=主线程（quit 入口置位后永不复位）；读=Win32 线程
        #   （_handle_drag_end/_do_translate 退出尾部守卫，v1.6.7 ③）。跨线程读
        #   原子 bool，GIL 下安全；只读一次不构成判断窗口。
        self._shutting_down = False

    # ============================================================ 启动
    def run(self):
        ls.setup_logging()                       # 尽早初始化，越早越好
        log = ls.get_logger()
        log.info("BOOT QuickTool v%s pid=%s frozen=%s log_dir=%s",
                 APP_VERSION, os.getpid(), bool(getattr(sys, "frozen", False)),
                 ls.log_dir())
        e2e_log("BOOT")
        self.root = tk.Tk()
        self.root.withdraw()                     # 常驻后台，不显示主窗口
        ls.install_tk_hook(self.root, log)       # Tk 回调异常也落盘
        threading.Thread(target=self._win_thread, daemon=True,
                         name="Win32Thread").start()
        threading.Thread(target=self._capture_worker, daemon=True,
                         name="CaptureWorker").start()
        threading.Thread(target=self._job_worker, daemon=True,
                         name="JobWorker").start()
        self._poll()
        # 心跳：每 5 分钟一条，证明"进程还活着"——区分空闲与假死
        self.root.after(300_000, self._heartbeat)
        if "--settings" in sys.argv:
            self.root.after(300, self.open_settings)
        if "--selftest" in sys.argv:
            self.root.after(2000, self._selftest)
        log.info("MAINLOOP-START")
        self.root.mainloop()
        log.info("MAINLOOP-END")

    # ============================================================ 自检
    def _selftest(self):
        """自动化测试用：按顺序走一遍主流程并打印标记，最后自动退出。

        标记同时写文件——windowed 版 exe 没有 stdout，只能靠文件回传。
        """
        e2e_log(f"HOTKEY={self.cfg.get('hotkey_translate')}")
        e2e_log("READY")
        sample = "artificial intelligence is reshaping the software industry"
        out, engine, ok = self.translator.translate(sample)
        e2e_log(f"TRANSLATE_OK={ok}")
        self._show_popup(sample, out, engine, ok)
        self.root.after(100, lambda: e2e_log("POPUP_OK"))
        self.root.after(600, self._test_tray_open_settings)
        self.root.after(1000, self._test_mini_translate)
        self.root.after(2400, self._test_pin)   # 对照窗链路（不与 OCR 撞时序）
        self.root.after(2900, self._test_note)  # 便签链路：pin 收尾后、OCR 之前
        self.root.after(3200, self._test_ocr)   # MINI_DONE(3s) 之后跑，OCR 完自己收尾

    def _test_pin(self):
        """截图对照链路自检：抓一小块屏幕 -> 进剪贴板 -> PinWindow -> 关闭。"""
        try:
            left, top, right, bottom = wa.get_work_area()
            w, h = min(300, right - left), min(200, bottom - top)
            data = wa.grab_screen_bmp(left + 40, top + 40, w, h)
            # 与真实链路一致：截图自动进剪贴板（CF_DIB）；先快照用户剪贴板
            # 测完还原，不自检污染用户剪贴板。
            self._clip_snap = wa.clipboard_snapshot()
            clip_ok = wa.set_clipboard_image(data)
            win = PinWindow(self, data, index=1)
            self.pin_wins.append(win)
            ok = bool(win and not win.closed)
            e2e_log(f"PIN_SHOWN={ok}")
            e2e_log(f"PIN_CLIP={clip_ok and wa.clipboard_has_dib()}")
            self.root.after(200, self._close_pin_after_test)
        except Exception as exc:
            e2e_log(f"PIN_SHOWN=False:{type(exc).__name__}")

    def _close_pin_after_test(self):
        for w in list(self.pin_wins):
            w.close()
        snap = getattr(self, "_clip_snap", None)
        if snap:
            wa.clipboard_restore(snap)
            self._clip_snap = None

    def _test_note(self):
        """置顶便签链路自检：创建 -> 追加累积 -> 迷你按钮『便』-> 关闭。"""
        try:
            self.open_note("e2e note probe alpha segment", "E2E")
            ok1 = bool(self.note_win and not self.note_win.closed)
            self.open_note("second probe segment for accumulation", "E2E2")
            total = self.note_win.count if self.note_win else 0
            # v1.6.3：迷你按钮『便』走 mini_note 分支——跳过抓词直接入便签，
            # 与按 Ctrl+Alt+N 终点相同。这里验证它能正确累积（应到 3 段）。
            self.mini_note("third segment via mini note button")
            total2 = self.note_win.count if self.note_win else 0
            e2e_log(f"NOTE_SHOWN={ok1}")
            e2e_log(f"NOTE_TOTAL={total}")
            e2e_log(f"MINI_NOTE_TOTAL={total2}")
            self.root.after(200, self._close_note_after_test)
        except Exception as exc:
            e2e_log(f"NOTE_SHOWN=False:{type(exc).__name__}")

    def _close_note_after_test(self):
        if self.note_win and not self.note_win.closed:
            self.note_win.close()

    def _test_ocr(self):
        """截图翻译链路自检：探测 OCR 语言包可用性。

        真实识别质量依赖系统语言包，这里只验证 PowerShell 桥通不通、
        有几个可用语言，e2e 据此打印报告（0 个也算通过，不算失败）。
        """
        try:
            langs = ocr.available_langs()
        except Exception:
            langs = []
        e2e_log(f"OCR_LANGS={len(langs)}")
        self.root.after(300, lambda: e2e_log("QUIT"))
        self.root.after(400, self.quit)

    def _test_tray_open_settings(self):
        """模拟托盘图标双击打开设置：验证 lParam 低 16 位事件比较的修复。

        高 16 位放图标 ID（Tray.uID=1），之前直接比较整个 lParam 永远失败。
        断言升级：不只检查窗口对象存在，还要求 state == normal 且已映射——
        否则 withdrawn（不可见）这种 bug 会漏网（历史上踩过：窗口建了但
        transient 到隐藏 root 导致永远 invisible，用户报"点设置没反应"）。
        """
        if self.hwnd:
            wa.post_message(self.hwnd, wa.WM_APP_TRAY, 1,
                            wa.WM_LBUTTONDBLCLK | (1 << 16))
            self.root.after(300, self._check_settings_visible)

    def _check_settings_visible(self, tries=10):
        s = self.settings
        if s and s.winfo_exists():
            try:
                if s.state() == "normal":
                    self.root.update_idletasks()
                    e2e_log(f"SETTINGS_OK={bool(s.winfo_ismapped())}")
                    return
            except Exception:
                pass
        if tries > 0:
            self.root.after(100,
                            lambda: self._check_settings_visible(tries - 1))
        else:
            e2e_log("SETTINGS_OK=False")

    def _test_mini_translate(self):
        """模拟点击迷你翻译按钮：应关闭旧悬浮窗并弹出新译文。"""
        sample = "machine learning models require large datasets"
        self.mini_translate(sample)
        self.root.after(2000,
                        lambda: e2e_log(f"MINI_DONE={self.popup_open}"))

    # ============================================================ Win32 线程
    def _heartbeat(self):
        ls.heartbeat()
        if not self._shutting_down:
            self.root.after(300_000, self._heartbeat)

    def _win_thread(self):
        log = ls.get_logger()
        try:
            self.hwnd = wa.create_hidden_window(self._on_message)
            log.info("HWND-CREATED hwnd=%s", self.hwnd)
        except Exception as exc:
            log_exc_local = traceback.format_exc()
            log.error("HWND-FAIL\n%s", log_exc_local)
            self.q.put(("fatal", f"创建隐藏窗口失败：{exc}\n热键与托盘功能不可用。"))
            return
        self._taskbar_created = wa.register_taskbar_created_msg()
        if self.cfg.get("enable_tray", True):
            self._tray_add()
        failed, notes = self._register_hotkeys()
        for note in notes:
            self.q.put(("info", note))
        if failed:
            self._report_hotkey_failure(failed)
        if self.cfg.get("mini_button", True):
            self.drag = wa.MouseDragWatcher(self._on_drag_end)
            if not self.drag.start():
                self.drag = None
            log.info("DRAG-WATCHER started=%s", bool(self.drag))
        wa.message_loop(
            on_error=lambda n, err:
                log.error("MSGLOOP-ERROR count=%s lasterr=%s", n, err))
        log.info("MSGLOOP-EXIT")                 # 正常退出或连续出错
        if self.tray:
            self.tray.remove()
        if self.drag:
            self.drag.stop()

    # ---- 迷你按钮：拖选文字结束后自动抓词并请求显示按钮 ----
    def _on_drag_end(self, x0, y0, x1, y1):
        """钩子回调（轻量，仅记录坐标）由消息循环处理，避免在钩子内做重活。

        带 (按下点, 弹起点)：按下点决定『这次拖选发生在哪类窗口』——截图遮罩
        框选、控制台 QuickEdit 拖选都不该自动抓词；弹起点是迷你按钮落点。
        """
        self._drag_box = (x0, y0, x1, y1)
        wa.post_message(self.hwnd, wa.WM_APP_DRAG_END)

    def _handle_drag_end(self):
        """消息循环里处理拖选结束：只做轻量判断，抓词投递给后台线程。

        这里绝不能同步抓词——本线程是鼠标钩子的宿主，卡住就等于卡鼠标。
        """
        # v1.6.7 ③（P1 ③ 收尾）：退出尾部守卫——quit 已开始（主线程置
        # _shutting_down 后关闭各窗）时，消息队列里排着的拖选/热键消息不应
        # 再触发抓词。跨线程读原子 bool，GIL 下安全（见 _shutting_down 归属）。
        if self._shutting_down:
            return
        if self._selecting:                  # 截图选区进行中/刚结束：钩子别抢
            return
        if self.ocr_selector:                    # 截图框选拖动不是划词，别抢
            return
        if any(not w.closed for w in self.pin_wins):   # 对照窗开着也不抢
            return
        if self.note_win and not self.note_win.closed:  # 便签窗开着也不抢
            return
        if self.popup_open:                      # 悬浮窗开着就不打扰
            return
        if not (self.cfg.get("mini_button", True)):
            return
        if self.mini_btn and not self.mini_btn.closed:
            return
        x0, y0, x1, y1 = getattr(self, "_drag_box", (0, 0, 0, 0))
        # 按下点所在窗口不能安全自动抓词的场景（v1.6.6）：
        #   console  —— 控制台里 Ctrl+C 无选区=中断信号，拖选会被当划词误发；
        #   overlay  —— 系统/第三方截图工具遮罩里拖动框选，也会被误判成划词，
        #               松手后盲发 Ctrl+C 砸到随后的 cmd 上造成中断/多行。
        reason = wa.is_drag_blocked_at(x0, y0)
        if reason:
            ls.get_logger().info("DRAG-SKIP reason=%s down=(%s,%s)", reason, x0, y0)
            return
        # 此刻前台仍是用户正在读的那个窗口（刚松开鼠标），记下标题当来源。
        # v1.6.7 ③：标题随 drag 请求 payload 流动（cap_q -> mini_show），
        # 便签归属绑定在本次拖选事件上；不再写共享 _last_source 等主线程
        # mini_note 稍后自行读取——那会被更晚的热键来源写覆盖（错标段头）。
        hwnd = wa.get_foreground_window()
        title = wa.get_window_title(hwnd) if hwnd else ""
        self._request_capture("drag", (x1, y1, title))

    # ============================================================ 后台 worker
    # 为什么需要这两个 worker：
    #   Win32 消息循环线程同时是 WH_MOUSE_LL 鼠标钩子的宿主线程，低层钩子回调
    #   靠这个线程的消息泵驱动。一旦它被同步阻塞，系统派发鼠标消息就会整体变慢，
    #   表现为"拖选一下鼠标卡一下"。而下面这些活全是阻塞的：
    #     - 抓词：模拟 Ctrl+C 后轮询等剪贴板，没选中内容时要等满
    #       timeout×retries（0.45s×2）——实测鼠标消息间隔尖峰 467ms；
    #     - 联网翻译：网络往返，通常 1~3s；
    #     - 截图 OCR：PowerShell 子进程，1~3s。
    #   所以 Win32 线程只留"轻量判断 + 投递消息"，重活全部挪到这里。
    #   抓词与慢任务分成两个线程，避免翻译排队拖住拖选弹按钮的响应速度。
    def _request_capture(self, kind, payload=None):
        """投递一次抓词请求。上一次还没做完就直接丢弃（防连发堆积）。"""
        if not self._cap_lock.acquire(blocking=False):
            ls.get_logger().info("CAPTURE-SKIP busy kind=%s", kind)
            return
        ls.get_logger().info("CAPTURE-START kind=%s", kind)
        self._cap_q.put((kind, payload))

    def _capture_worker(self):
        """抓词专用线程（CaptureWorker）：模拟 Ctrl+C + 等剪贴板。"""
        while True:
            kind, payload = self._cap_q.get()
            try:
                if kind == "drag":
                    self._capture_for_drag(payload)
                elif kind == "hotkey":
                    self._capture_for_hotkey()
                elif kind == "note":
                    self._capture_for_note()
            except Exception:
                ls.log_exc("CAPTURE-ERROR")
                traceback.print_exc()
            finally:
                self._cap_lock.release()

    def _capture_for_drag(self, pt):
        x, y, src = pt or (0, 0, "")
        # 首轮 0.25s 快速失败：多数程序复制是瞬时的，没选中内容时尽早收手
        text = get_selected_text(first_timeout=0.25)
        ls.get_logger().info("CAPTURE-DONE kind=drag len=%s", len(text or ""))
        if not text or not looks_translatable(text):
            return
        if len(text) > int(self.cfg.get("mini_button_maxlen", 200)):
            return
        self.q.put(("mini_show", (x, y, text, src)))

    def _capture_for_hotkey(self):
        text = get_selected_text(first_timeout=0.25)
        if not text:
            self.q.put(("toast", ("未获取到选中文本",
                                  "请先选中文字，再按快捷键。\n"
                                  "部分自绘程序（如某些 PDF 阅读器）不支持复制，可用截图 OCR 方案。")))
            return
        if not looks_translatable(text):
            self.q.put(("toast", ("这段内容看起来不是自然语言", text[:160])))
            return
        self._translate_text(text)

    def _capture_for_note(self):
        """便签抓词：不做「是否自然语言」过滤——代码、术语、公式也该能钉。"""
        text = get_selected_text(first_timeout=0.25)
        if not text:
            self.q.put(("toast", ("未获取到选中文本",
                                  "请先选中文字，再按置顶便签快捷键。")))
            return
        self.q.put(("note_text", (text, self._last_source)))

    def _job_worker(self):
        """慢任务线程（JobWorker）：联网翻译 / 截图 OCR。"""
        while True:
            kind, payload = self._job_q.get()
            try:
                if kind == "translate":
                    self._do_translate_job(payload)
                elif kind == "ocr":
                    self._run_ocr(payload)
            except Exception:
                ls.log_exc(f"JOB-ERROR kind={kind}")
                traceback.print_exc()

    def _tray_add(self):
        if self.tray and self.tray.added:
            return
        self.tray = wa.Tray(self.hwnd, "QuickTool 划词翻译")
        ok = self.tray.add()
        ls.get_logger().info("TRAY-ADD ok=%s", ok)

    def _tray_remove(self):
        if self.tray:
            self.tray.remove()
            self.tray = None
            ls.get_logger().info("TRAY-REMOVE")

    def _hotkey_map(self):
        return [(HK_TRANSLATE, self.cfg.get("hotkey_translate")),
                (HK_SETTINGS, self.cfg.get("hotkey_settings")),
                (HK_QUIT, self.cfg.get("hotkey_quit")),
                (HK_OCR, self.cfg.get("hotkey_ocr")),
                (HK_PIN, self.cfg.get("hotkey_pin")),
                (HK_NOTE, self.cfg.get("hotkey_note"))]

    def _try_register(self, hid, hotkey):
        try:
            return wa.register_hotkey(self.hwnd, hid, hotkey)
        except Exception as exc:
            return False, str(exc)

    _HOTKEY_CFG = {HK_TRANSLATE: "hotkey_translate",
                   HK_SETTINGS: "hotkey_settings",
                   HK_QUIT: "hotkey_quit",
                   HK_OCR: "hotkey_ocr",
                   HK_PIN: "hotkey_pin",
                   HK_NOTE: "hotkey_note"}

    def _maybe_upgrade_hotkey(self, hid, hotkey):
        """顺延产物自动升级：配置里若存的是『被占用后顺延』的临时组合（即候选列表
        中的非首选项），而首选（如 Ctrl+Q）当前空闲，就自动升回首选并写回配置。

        防止这种尴尬：某次 Ctrl+Q 恰好被别的程序占用、程序顺延并持久化了
        Ctrl+Alt+F1，之后 Ctrl+Q 空出来了也一直用着别扭的组合。
        用户手动设置的自定义组合（不在候选列表里）不会被改动。
        """
        cands = HOTKEY_CANDIDATES.get(hid, [])
        if not cands:
            return None
        first = cands[0]
        if str(hotkey).lower() == first.lower():
            return None
        if str(hotkey).lower() not in [c.lower() for c in cands]:
            return None                       # 用户自定义组合，尊重
        ok, _ = self._try_register(hid, first)
        if ok:
            try:
                self.cfg.set(self._HOTKEY_CFG[hid], first)
                self.cfg.save()
            except Exception:
                pass
            return first
        return None

    def _register_hotkeys(self):
        """注册全部热键。任一组合被占用（1409）都自动顺延到该键位的候选列表。

        1409（ERROR_HOTKEY_ALREADY_REGISTERED）非常常见：输入法、显卡面板、
        网盘、IDE 都会抢 Ctrl+Alt+* 系列。与其让用户一脸懵地发现快捷键没反应，
        不如直接换一个、把结果持久化并告诉他。

        两个历史坑（都已修）：
        - HOTKEY_CANDIDATES 曾用字符串做键，而这里用 hid（int）查询，顺延
          逻辑从未生效——表现为每次启动都弹同一个"注册失败"。
        - 顺延结果曾只对划词翻译键持久化，其他键每次启动都重新报一遍。
        另外本程序内部不允许两个功能抢同一组合（后注册的直接走顺延）。
        """
        labels = {HK_TRANSLATE: "划词翻译", HK_SETTINGS: "打开设置",
                  HK_QUIT: "退出程序", HK_OCR: "截图翻译", HK_PIN: "截图对照",
                  HK_NOTE: "置顶便签"}
        failed, notes = [], []
        used = set()
        for hid, hotkey in self._hotkey_map():
            label = labels.get(hid, str(hid))
            upgraded = self._maybe_upgrade_hotkey(hid, hotkey)
            if upgraded:
                notes.append(f"{label}快捷键已自动升级为 {upgraded}"
                             f"（原 {hotkey} 是占用后的临时顺延）")
                hotkey = upgraded
            wa.unregister_hotkey(self.hwnd, hid)
            ok, err = False, None
            if str(hotkey).lower() not in used:      # 程序内组合不重复
                ok, err = self._try_register(hid, hotkey)
            else:
                err = "1409"
            if not ok:
                for cand in HOTKEY_CANDIDATES.get(hid, []):
                    if cand.lower() == str(hotkey).lower() \
                            or cand.lower() in used:
                        continue
                    ok2, _ = self._try_register(hid, cand)
                    if ok2:
                        notes.append(f"{label}快捷键 {hotkey} "
                                     f"已被其他程序占用，已自动改用 {cand}")
                        self.cfg.set(self._HOTKEY_CFG[hid], cand)   # 全部持久化
                        self.cfg.save()
                        hotkey, ok = cand, True
                        break
            if ok:
                used.add(str(hotkey).lower())
                ls.get_logger().info("HOTKEY-OK hid=%s combo=%s", hid, hotkey)
            else:
                ls.get_logger().warning("HOTKEY-FAIL hid=%s combo=%s err=%s",
                                        hid, hotkey, err)
                failed.append((hid, f"{label} {hotkey}"
                                    f"（错误码 {err}，1409 = 已被其他程序占用）"))
        return failed, notes

    def _report_hotkey_failure(self, failed):
        """划词翻译键失败才算致命；其余键失败降级为提示（托盘仍可用全部功能）。"""
        hard = [m for hid, m in failed if hid == HK_TRANSLATE]
        soft = [m for hid, m in failed if hid != HK_TRANSLATE]
        if hard:
            self.q.put(("fatal", "以下快捷键注册失败：\n" + "\n".join(hard)
                                 + "\n\n请在设置里换成别的组合。"))
        if soft:
            self.q.put(("info", "以下快捷键暂不可用（对应功能仍可从托盘菜单"
                                "使用，下次启动自动换用空闲组合）：\n"
                                + "\n".join(soft)))

    def _on_message(self, hwnd, msg, wparam, lparam):
        if self._taskbar_created and msg == self._taskbar_created:
            # explorer 重启会把托盘图标全部收走，广播此消息 → 重新挂图标。
            # 之前没处理：程序明明活着，用户却因为"托盘图标消失"以为它退了。
            ls.get_logger().info("TASKBAR-CREATED re-add tray")
            if self.cfg.get("enable_tray", True):
                self.tray.added = False            # 强制重新 ADD
                self._tray_add()
            return True
        if msg == wa.WM_APP_RELOAD:
            failed, notes = self._register_hotkeys()
            if failed:
                self._report_hotkey_failure(failed)
            for note in notes:
                self.q.put(("info", note))
            return True
        if msg == WM_APP_TRAY_TOGGLE:
            self._tray_add() if wparam else self._tray_remove()
            return True
        if msg == wa.WM_HOTKEY:
            if wparam == HK_TRANSLATE:
                self._do_translate()
            elif wparam == HK_OCR:
                self.q.put(("ocr_select", None))
            elif wparam == HK_PIN:
                self.q.put(("pin_select", None))
            elif wparam == HK_NOTE:
                # 此刻前台还是用户正在读的窗口（抓词模拟 Ctrl+C 后焦点可能
                # 变化），立刻记下标题当便签来源
                hwnd = wa.get_foreground_window()
                self._last_source = wa.get_window_title(hwnd)
                self.q.put(("note", None))
            elif wparam == HK_SETTINGS:
                self.q.put(("settings", None))
            elif wparam == HK_QUIT:
                self.q.put(("quit", None))
            return True
        if msg == wa.WM_APP_DRAG_END:
            self._handle_drag_end()
            return True
        if msg == wa.WM_APP_TRAY:
            # lParam 低 16 位是鼠标事件，高 16 位是图标 ID（Tray.uID=1），
            # 必须掩掉高 16 位再比较，否则任何点击都永远匹配不上
            ev = lparam & 0xFFFF
            if ev == wa.WM_LBUTTONDBLCLK:
                self.q.put(("settings", None))
            elif ev == wa.WM_RBUTTONUP:
                cmd = self.tray.popup_menu(TRAY_ITEMS) if self.tray else 0
                if cmd == TRAY_SETTINGS:
                    self.q.put(("settings", None))
                elif cmd == TRAY_OCR:
                    self.q.put(("ocr_select", None))
                elif cmd == TRAY_PIN:
                    self.q.put(("pin_select", None))
                elif cmd == TRAY_NOTE:
                    hwnd = wa.get_foreground_window()
                    self._last_source = wa.get_window_title(hwnd)
                    self.q.put(("note", None))
                elif cmd == TRAY_QUIT:
                    self.q.put(("quit", None))
            return True
        return False

    def _do_translate(self):
        """热键路径（Win32 线程）：只做判断与投递，绝不同步抓词。"""
        if self._shutting_down:              # v1.6.7 ③：退出尾部热键不再响应
            return
        if self.popup_open:                      # 再按一次热键 = 关闭悬浮窗
            self.q.put(("close_popup", None))
            return
        self._request_capture("hotkey")

    def _translate_text(self, text):
        """统一的翻译入口（任何线程都可调用）：只入队，联网在 JobWorker 里跑。"""
        text = (text or "").strip()
        if not text:
            return
        self._job_q.put(("translate", text))

    def _do_translate_job(self, text):
        """真正执行翻译（JobWorker 线程）：loading -> 联网 -> result。"""
        self.q.put(("loading", text))
        log = ls.get_logger()
        log.info("TRANSLATE-START len=%s", len(text))
        out, engine, ok = self.translator.translate(text)
        log.info("TRANSLATE-END engine=%s ok=%s len=%s", engine, ok, len(out or ""))
        self.q.put(("result", (text, out, engine, ok)))

    # ============================================================ 主线程
    def _poll(self):
        if self._shutting_down:
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                try:
                    self._dispatch(kind, payload)
                except Exception:               # 单个事件出错不能拖垮整个轮询
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _root_alive(self):
        try:
            return bool(self.root) and bool(self.root.winfo_exists())
        except Exception:
            return False

    def _dispatch(self, kind, payload):
        # messagebox 会开启嵌套事件循环，期间可能已经走完退出流程，
        # 回到这里时 root 已销毁，再建窗口会抛 "Too early to use font"
        if self._shutting_down or not self._root_alive():
            return
        if kind == "loading":
            self._show_popup(payload, "翻译中…", "", True, loading=True)
        elif kind == "result":
            text, out, engine, ok = payload
            if self.popup and not self.popup.closed:
                self.popup.update(text, out, engine, ok)
            else:
                self._show_popup(text, out, engine, ok)
        elif kind == "toast":
            title, body = payload
            self._show_popup("", body, title, True)
        elif kind == "close_popup":
            if self.popup:
                self.popup.close()
        elif kind == "settings":
            self.open_settings()
        elif kind == "ocr_select":
            self.open_ocr()
        elif kind == "pin_select":
            self.open_pin()
        elif kind == "note":
            self._request_capture("note")
        elif kind == "note_text":
            text, source = payload
            self.open_note(text, source)
        elif kind == "pin_close":
            for w in list(self.pin_wins):
                w.close()
        elif kind == "ocr_result":
            ok, text, err = payload
            self._handle_ocr_result(ok, text, err)
        elif kind == "mini_show":
            x, y, text, src = payload
            self._show_mini_button(x, y, text, src)
        elif kind == "mini_hide":
            self._hide_mini_button()
        elif kind == "quit":
            self.quit()
        elif kind == "fatal":
            # 不用模态 messagebox——它会卡住主线程，导致队列里的
            # 设置/翻译事件全部积压。改用非阻塞悬浮提示。
            self._show_popup("", payload, "QuickTool 提示", True)
        elif kind == "info":
            self._show_popup("", payload, "QuickTool 提示", True)

    def _show_popup(self, text, result, engine, ok, loading=False):
        if self.popup and not self.popup.closed:
            self.popup.close()
        self.popup = Popup(self, text, result, engine, ok=ok, loading=loading)
        self.popup_open = True
        e2e_log(f"POPUP_SHOWN:{engine}")

    # ------------------------------------------------------------ 迷你按钮
    def _show_mini_button(self, x, y, text, source=""):
        if self.popup_open:
            return
        self._hide_mini_button()
        self.mini_btn = MiniButton(self, x, y, text)
        self._mini_source = source or ""    # 主线程专属：随按钮携带的来源
        self._sync_drag_ignore()

    def _hide_mini_button(self):
        if self.mini_btn:
            self.mini_btn.close()
        self.mini_btn = None
        self._sync_drag_ignore()

    def _sync_drag_ignore(self):
        """把迷你按钮矩形同步给鼠标钩子：自己点按钮不应再触发抓词。"""
        rect = None
        if self.mini_btn and not self.mini_btn.closed:
            try:
                rect = self.mini_btn.get_rect()
            except Exception:
                rect = None
        if self.drag:
            self.drag.ignore_rect = rect

    def mini_translate(self, text):
        """迷你按钮『译』点击（主线程调用）：翻译文本直接入 job 队列。"""
        self._hide_mini_button()
        if not (text or "").strip():
            return
        if self.popup_open:
            self.popup.close()
        # v1.6.7 ③：不再写 _mini_pending 再经 WM_APP_MINI_TRANSLATE 投给
        # Win32 线程读——_translate_text 本就是「任何线程只入队」，主线程
        # 直调即可；旧写法在连续两次点击/OCR 完成时会互相覆盖待译文本。
        self._translate_text(text)

    def mini_note(self, text):
        """迷你按钮『便』点击（主线程）：把文字钉进置顶便签。

        等价于按 Ctrl+Alt+N，区别只是文字在拖选结束时已经抓好了，不必再走
        一次「模拟 Ctrl+C + 等剪贴板」。来源窗口标题在拖选时随 payload 到
        mini_show（_mini_source，主线程专属），不再读跨线程的 _last_source。
        """
        self._hide_mini_button()
        if not (text or "").strip():
            return
        self.open_note(text, self._mini_source or "")

    # ------------------------------------------------------------ 截图翻译
    def open_ocr(self):
        """进入截图选区（主线程）。遮罩已开着就不重复弹。"""
        if self.ocr_selector:
            return
        self._selecting = True              # 选区流程标志：钩子别把拖拽当划词
        self.ocr_selector = RegionSelector(self, self._ocr_region_selected)

    def _ocr_region_selected(self, x, y, w, h):
        """选区完成（主线程）：立即 GDI 抓屏（毫秒级），OCR 交 JobWorker。"""
        try:
            self.ocr_selector = None
            try:
                data = wa.grab_screen_bmp(x, y, w, h)
                path = os.path.join(tempfile.gettempdir(), "QuickTool_ocr.bmp")
                with open(path, "wb") as f:
                    f.write(data)
            except Exception as exc:
                self.q.put(("toast", ("截图失败", str(exc))))
                return
            self.q.put(("toast", ("QuickTool 截图", "正在识别文字…")))
            # v1.6.7 ③：图片路径随 job payload 直达 JobWorker（1~3s 的
            # PowerShell OCR 不进主线程也不进钩子线程），不再经共享属性
            # _ocr_img + WM_APP_OCR_RUN 中转——避免「旧 job 未开跑就被
            # 下一次选区覆盖路径」的跨线程错拿。
            self._job_q.put(("ocr", path))
        finally:
            # 同 _pin_region_selected：选区流程结束才放行钩子
            self._selecting = False

    def _run_ocr(self, img):
        """JobWorker 线程：PowerShell 子进程跑 OCR（约 1~3s，不碰 UI）。"""
        if not img:
            return
        log = ls.get_logger()
        log.info("OCR-START img=%s", img)
        lang = ocr.pick_lang(self.cfg.get("ocr_lang", "auto"),
                             ocr.available_langs())
        if not lang:
            log.info("OCR-NO-LANG")
            self.q.put(("ocr_result", (False, "", "no_lang")))
            return
        ok, text, _tag, err = ocr.ocr_image_file(img, lang)
        log.info("OCR-END ok=%s len=%s err=%s", ok, len(text or ""), err)
        self.q.put(("ocr_result", (ok, text, err)))

    def _handle_ocr_result(self, ok, text, err):
        """主线程：OCR 完成后走统一翻译管线（loading → result 同热键路径）。"""
        if not ok:
            if err == "no_lang":
                hint = ("没有可用的 OCR 语言包。请到 Windows 设置 → 时间和语言 → "
                        "语言 → 首选语言 → 选项，下载「文本识别」语言功能；"
                        "或在 QuickTool 设置里指定 OCR 语言。")
            else:
                hint = err or "未知错误"
            self.q.put(("toast", ("截图识别失败", hint)))
            return
        text = normalize_text(text or "")
        if not text.strip():
            self.q.put(("toast", ("未识别到文字",
                                  "请框选包含清晰文字的区域，太小或太模糊会识别失败。")))
            return
        # v1.6.7 ③：OCR 文本直接入翻译队列（同 mini_translate 改动），
        # 不再经共享属性 _mini_pending + WM_APP_MINI_TRANSLATE 中转。
        self._translate_text(text)

    # ------------------------------------------------------------ 截图对照
    PIN_MAX = 5                      # 最多同时存在的对照窗数量

    def open_pin(self):
        """进入截图对照选区（主线程）。遮罩已开着就不重复弹。"""
        if self.ocr_selector:
            return
        self._selecting = True              # 选区流程标志：钩子别把拖拽当划词
        self.ocr_selector = RegionSelector(
            self, self._pin_region_selected,
            title="拖拽框选要截取的屏幕区域 · Esc 取消（对照小窗）")

    def _pin_region_selected(self, x, y, w, h):
        """选区完成（主线程）：GDI 抓屏（毫秒级）-> 置顶对照小窗。

        支持多个对照窗并存：新截图追加到 pin_wins 列表（不再替换旧的）。
        达到 PIN_MAX 上限时提示先关闭一个。
        """
        try:
            self.ocr_selector = None
            active = [p for p in self.pin_wins if not p.closed]
            if len(active) >= self.PIN_MAX:
                self.q.put(("toast", ("截图对照已满",
                                      f"最多同时保留 {self.PIN_MAX} 个对照窗，"
                                      "请先关闭一个（Esc 或右上角 ✕）。")))
                return
            try:
                data = wa.grab_screen_bmp(x, y, w, h)
            except Exception as exc:
                self.q.put(("toast", ("截图失败", str(exc))))
                return
            # 同 Win+Shift+S：截图自动进剪贴板（CF_DIB），可立即 Ctrl+V 粘贴。
            # 失败不阻塞——只是不能粘贴，小窗照常显示。
            clip_ok = wa.set_clipboard_image(data)
            if not clip_ok:
                ls.get_logger().info("PIN-CLIP-FAIL")
            try:
                win = PinWindow(self, data, index=len(active) + 1)
                self.pin_wins.append(win)
            except Exception as exc:
                ls.log_exc("PIN-FAIL")
                self.q.put(("toast", ("截图对照失败", str(exc))))
                return
            ls.get_logger().info("PIN-SHOW size=%sx%s total=%s",
                                 w, h, len(active) + 1)
            e2e_log(f"PIN_SHOWN total={len(active) + 1}")
            e2e_log(f"PIN_CLIP={clip_ok}")
        finally:
            # 整个选区处理完成才放行钩子：期间 Win32 线程若执行
            # _handle_drag_end（拖拽 LEFTUP 投递的 WM_APP_DRAG_END 消息），
            # 看到 _selecting=True 就不会误判成划词去动剪贴板，避免把
            # 刚写入的 CF_DIB 还原覆盖（v1.5.3 竞态修复）。
            self._selecting = False

    def open_note(self, text, source=""):
        """置顶便签：单窗口累积——已有便签就追加一段，没有就新建。"""
        if self.note_win is not None and not self.note_win.closed:
            self.note_win.append(text, source)
            ls.get_logger().info("NOTE-APPEND total=%s", self.note_win.count)
        else:
            try:
                self.note_win = NoteWindow(self, text, source)
            except Exception as exc:
                ls.log_exc("NOTE-FAIL")
                self.q.put(("toast", ("便签创建失败", str(exc))))
                return
            ls.get_logger().info("NOTE-SHOW chars=%s", len(text))
        e2e_log(f"NOTE_SHOWN count={self.note_win.count}")

    def open_settings(self):
        if self.settings is not None:
            try:
                if self.settings.winfo_exists():
                    self.settings.lift()
                    self.settings.focus_force()
                    return
            except Exception:
                self.settings = None            # 已销毁，重建
        self.settings = Settings(self)
        # 新建窗口也确保显示在前台（窗口自身已 deiconify，这里再补一层）
        try:
            self.settings.lift()
            self.settings.focus_force()
        except Exception:
            pass

    def open_config_dir(self):
        path = os.path.dirname(self.cfg.path)
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)
        except Exception:
            pass

    def reload_hotkeys(self):
        if self.hwnd:
            wa.post_message(self.hwnd, wa.WM_APP_RELOAD)

    def toggle_tray(self, enable):
        if self.hwnd:
            wa.post_message(self.hwnd, WM_APP_TRAY_TOGGLE, 1 if enable else 0)

    # ============================================================ 退出
    def quit(self):
        if self._shutting_down:
            return
        ls.get_logger().info("QUIT-START")
        self._shutting_down = True
        try:
            self.cfg.save()
        except Exception:
            pass
        if self.popup:
            self.popup.close()
        if self.mini_btn:
            self.mini_btn.close()
        for w in list(self.pin_wins):
            w.close()
        if self.note_win:
            self.note_win.close()
        if self.hwnd:
            wa.post_message(self.hwnd, wa.WM_DESTROY)   # 触发 PostQuitMessage
        self.root.after(150, self._destroy)

    def _destroy(self):
        ls.get_logger().info("QUIT-END")
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    ls.setup_logging()                           # 比 App() 更早，配置加载也留痕
    App().run()


if __name__ == "__main__":
    main()
