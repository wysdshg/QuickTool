"""
纯 ctypes 的 Windows API 封装层。

覆盖四块能力，全部不依赖 pywin32 / keyboard / pyperclip / pystray：
  1. 全局热键   RegisterHotKey + 独立消息循环线程
  2. 剪贴板     全格式快照 / 还原（抓词后不污染用户剪贴板）
  3. 光标位置   GetCursorPos（悬浮窗跟随）
  4. 系统托盘   Shell_NotifyIconW + TrackPopupMenu

为什么不用第三方库：
  - keyboard / pynput 会装全局钩子，属于"键盘记录器"行为，易被杀软拦截，且要管理员权限；
  - pywin32 体积大（~30MB），PyInstaller 打包后明显变胖；
  - ctypes + stdlib 打包后 exe 可以压到 10MB 以内。
"""
import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---------------------------------------------------------------- 基础类型
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LPWSTR = ctypes.c_wchar_p
HANDLE = ctypes.c_void_p

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HANDLE, ctypes.c_uint, WPARAM, LPARAM)

# ---------------------------------------------------------------- 常量
WM_HOTKEY = 0x0312
WM_CREATE = 0x0001
WM_NCCREATE = 0x0081
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_USER = 0x0400
WM_APP = 0x8000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_APP_RELOAD = WM_APP + 1          # 自定义：热键变更，重新注册
WM_APP_TRAY = WM_APP + 2            # 自定义：托盘回调
WM_APP_DRAG_END = WM_APP + 5        # 自定义：鼠标拖选结束（+3/+4 被 main.py 占用）

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# 鼠标（迷你按钮用）
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WH_MOUSE_LL = 14

# 剪贴板
CF_TEXT = 1
CF_DIB = 8               # 设备无关位图（BITMAPINFOHEADER + 像素，无文件头）
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# 已知返回"非 HGLOBAL 句柄"的剪贴板格式（GDI 对象/所有者显示）。快照时
# 必须整类跳过、绝不调 GlobalSize——对 HBITMAP 之类调 GlobalSize 会读坏
# 堆元数据，正是 0xc0000374 堆损坏的触发点（v1.4.1 实证：崩溃栈就停在
# _hglobal_read 的 GlobalSize）。其余格式（文本/HDROP/DIB/私有格式）均为
# HGLOBAL 内存块，可安全走 GlobalSize 有界读。
_CF_NON_HGLOBAL = {
    2,          # CF_BITMAP        HBITMAP（GDI）
    9,          # CF_PALETTE       HPALETTE（GDI）
    14,         # CF_ENHMETAFILE   HENHMETAFILE（GDI）
    0x0080,     # CF_OWNERDISPLAY  所有者自绘，无标准内存布局
}

# 键盘输入
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_C = 0x43
VK_F = 0x46             # 查找：浏览器 / 阅读器 / Electron 应用通用的 Ctrl+F
VK_V = 0x56             # 粘贴
VK_SNAPSHOT = 0x2C      # PrintScreen / PrtSc

# 托盘
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
IDI_APPLICATION = 32512

# 菜单
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

# 窗口
WS_OVERLAPPED = 0x00000000
CW_USEDEFAULT = -2147483648


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_ulonglong),  # ULONG_PTR
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("ki", KEYBDINPUT),
                ("_pad", ctypes.c_ubyte * 8)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HANDLE), ("message", ctypes.c_uint),
        ("wParam", WPARAM), ("lParam", LPARAM),
        ("time", ctypes.c_uint32), ("pt", POINT), ("lPrivate", ctypes.c_uint32),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", HANDLE),
        ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
        ("lpszMenuName", LPWSTR), ("lpszClassName", LPWSTR), ("hIconSm", HANDLE),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32), ("hWnd", HANDLE), ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint), ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", HANDLE), ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint32), ("dwStateMask", ctypes.c_uint32),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeoutVersion", ctypes.c_uint32),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint32),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", HANDLE),
    ]


# ---------------------------------------------------------------- 函数签名
user32.RegisterHotKey.restype = wt.BOOL
user32.RegisterHotKey.argtypes = [HANDLE, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.UnregisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [HANDLE, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HANDLE, ctypes.c_uint, ctypes.c_uint]
user32.PostThreadMessageW.argtypes = [ctypes.c_uint32, ctypes.c_uint, WPARAM, LPARAM]
user32.PostMessageW.argtypes = [HANDLE, ctypes.c_uint, WPARAM, LPARAM]
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.CreateWindowExW.restype = HANDLE
user32.CreateWindowExW.argtypes = [ctypes.c_uint32, LPWSTR, LPWSTR, ctypes.c_uint32,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   HANDLE, HANDLE, HANDLE, ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [HANDLE, ctypes.c_uint, WPARAM, LPARAM]
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetWindowLongW.argtypes = [HANDLE, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetForegroundWindow.argtypes = [HANDLE]
user32.DestroyMenu.argtypes = [HANDLE]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.OpenClipboard.argtypes = [HANDLE]
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.SetClipboardData.argtypes = [ctypes.c_uint, HANDLE]
user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
user32.VkKeyScanW.restype = ctypes.c_short
user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.DestroyWindow.argtypes = [HANDLE]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.CreatePopupMenu.restype = HANDLE
user32.AppendMenuW.argtypes = [HANDLE, ctypes.c_uint, ctypes.c_size_t, LPWSTR]
user32.TrackPopupMenu.argtypes = [HANDLE, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, HANDLE, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.SetForegroundWindow.argtypes = [HANDLE]
user32.DestroyMenu.argtypes = [HANDLE]
user32.LoadIconW.restype = HANDLE
user32.LoadIconW.argtypes = [HANDLE, ctypes.c_void_p]   # 传资源 ID（整数）时用 c_void_p
user32.LoadImageW.restype = HANDLE
user32.LoadImageW.argtypes = [HANDLE, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]

kernel32.GetModuleHandleW.restype = HANDLE
kernel32.GetModuleHandleW.argtypes = [LPWSTR]
kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
kernel32.GlobalAlloc.restype = HANDLE
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [HANDLE]
kernel32.GlobalUnlock.argtypes = [HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [HANDLE]
kernel32.GlobalFree.restype = HANDLE
kernel32.GlobalFree.argtypes = [HANDLE]

shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [ctypes.c_uint32, ctypes.POINTER(NOTIFYICONDATAW)]

user32.OpenClipboard.argtypes = [HANDLE]
user32.EmptyClipboard.argtypes = []
user32.CloseClipboard.argtypes = []
user32.GetClipboardData.restype = HANDLE
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.SetClipboardData.restype = HANDLE
user32.SetClipboardData.argtypes = [ctypes.c_uint, HANDLE]
user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
user32.EnumClipboardFormats.restype = ctypes.c_uint
user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
user32.AddClipboardFormatListener.restype = wt.BOOL
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte,
                               ctypes.c_uint32, ctypes.c_size_t]
user32.SetForegroundWindow.argtypes = [HANDLE]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetClassNameW.argtypes = [HANDLE, LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = HANDLE
user32.IsWindowVisible.argtypes = [HANDLE]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [HANDLE, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = HANDLE
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, wt.DWORD, LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL

# 任务栏重建广播：explorer 重启后所有托盘图标都会被系统收走，托盘程序必须
# 监听这个广播消息并重新 Shell_NotifyIconW，否则"程序活着但托盘图标没了"。
def register_taskbar_created_msg():
    user32.RegisterWindowMessageW.restype = ctypes.c_uint
    user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
    return user32.RegisterWindowMessageW("TaskbarCreated")


# ---------------------------------------------------------------- 热键
class HotkeySpec:
    """把 'Ctrl+Alt+T' 这类字符串解析成 RegisterHotKey 需要的 (mod, vk)。"""

    _MODS = {
        "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
        "alt": MOD_ALT, "menu": MOD_ALT,
        "shift": MOD_SHIFT,
        "win": MOD_WIN, "super": MOD_WIN,
    }
    _VK_NAMES = {
        "esc": 0x1B, "space": 0x20, "enter": 0x0D, "tab": 0x09,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E,
        "pgup": 0x21, "pgdn": 0x22,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
        "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
        "f11": 0x7A, "f12": 0x7B,
        # PrintScreen 的别名：Tk 录制给的是 "Print"，用户可能手写 PrtSc 等
        "prtsc": VK_SNAPSHOT, "prtscr": VK_SNAPSHOT,
        "printscreen": VK_SNAPSHOT, "snapshot": VK_SNAPSHOT,
        "print": VK_SNAPSHOT,
    }

    @classmethod
    def parse(cls, text):
        """'Ctrl+Alt+T' -> (mods, vk)；解析失败抛 ValueError。"""
        parts = [p.strip().lower() for p in str(text).split("+") if p.strip()]
        if not parts:
            raise ValueError("快捷键为空")
        mods = 0
        key = None
        for p in parts:
            if p in cls._MODS:
                mods |= cls._MODS[p]
            else:
                key = p
        if key is None:
            raise ValueError(f"缺少主键：{text}")
        if key in cls._VK_NAMES:
            vk = cls._VK_NAMES[key]
        elif len(key) == 1:
            scan = user32.VkKeyScanW(key.upper())      # 传字符，不是 ord()
            vk = scan & 0xFF
            if scan == -1 or vk == 0xFF:
                raise ValueError(f"无法识别的按键：{key}")
        else:
            raise ValueError(f"无法识别的按键：{key}")
        return mods | MOD_NOREPEAT, vk


user32.VkKeyScanW.restype = ctypes.c_short
user32.VkKeyScanW.argtypes = [ctypes.c_wchar]


def register_hotkey(hwnd, hotkey_id, hotkey_str):
    mods, vk = HotkeySpec.parse(hotkey_str)
    ok = user32.RegisterHotKey(hwnd, hotkey_id, mods, vk)
    return bool(ok), ctypes.get_last_error()


def unregister_hotkey(hwnd, hotkey_id):
    user32.UnregisterHotKey(hwnd, hotkey_id)


# ---------------------------------------------------------------- 键盘模拟
def _send_input(inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def _key(vk, up=False):
    return INPUT(INPUT_KEYBOARD,
                 KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0),
                 (ctypes.c_ubyte * 8)())


def release_modifiers():
    """先抬起 Ctrl/Alt/Shift/Win。

    不这样做的话：用户按的是 Ctrl+Alt+T，此时 Ctrl、Alt 都处于按下状态，
    紧接着发送的 Ctrl+C 会被系统理解成 Ctrl+Alt+C，复制根本不会触发。
    这是所有划词翻译工具最容易踩的坑。
    """
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN):
        _send_input([_key(vk, up=True)])


def send_ctrl_c():
    _send_input([
        _key(VK_CONTROL), _key(VK_C),
        _key(VK_C, up=True), _key(VK_CONTROL, up=True),
    ])


def send_ctrl_v():
    _send_input([
        _key(VK_CONTROL), _key(VK_V),
        _key(VK_V, up=True), _key(VK_CONTROL, up=True),
    ])


def send_ctrl_f():
    """合成 Ctrl+F：让目标应用自己打开查找框（回搜功能的基础）。

    为什么不用 UIA / 可访问性树去定位原文：虚拟滚动的 AI 对话界面屏幕外
    内容拿不到，Electron 应用又常常不开可访问性。而「让应用自己搜」这条路
    对所有有搜索框的程序都成立，且不需要任何权限。
    """
    _send_input([
        _key(VK_CONTROL), _key(VK_F),
        _key(VK_F, up=True), _key(VK_CONTROL, up=True),
    ])


def get_foreground_window():
    """当前前台窗口句柄（热键触发时记录，用于「回搜」切回原应用）。"""
    return user32.GetForegroundWindow()


def set_foreground(hwnd):
    """把窗口提到前台。SetForegroundWindow 有诸多限制（如前台进程未授权时
    只闪任务栏），但用户刚从那个窗口按过热键，通常能成功；失败不抛异常。"""
    if not hwnd:
        return False
    try:
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        return False


def get_window_title(hwnd):
    """取窗口标题（便签记录来源用）。失败返回空串，绝不抛异常。"""
    if not hwnd:
        return ""
    try:
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""


# ---------------------------------------------------------------- 窗口类型识别
# 控制台/终端窗口对 Ctrl+C 语义敏感：有活动选区→复制；无选区→把 Ctrl+C 当
# 『中断信号』发给前台程序组（正在跑的程序会被杀、shell 会换行）。QuickTool
# 的抓词全靠模拟 Ctrl+C，所以在这些窗口里绝不能盲发。
_CONSOLE_CLASSES = (
    "ConsoleWindowClass",             # conhost：cmd / PowerShell 的传统宿主
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal（1.18+ 主窗口）
    "TerminalWindowClass",            # Windows Terminal（部分版本/旧版）
    "mintty",                         # Git Bash 默认终端
)
_CONSOLE_PROCESSES = (
    "conhost.exe", "openconsole.exe", "windowsterminal.exe", "mintty.exe",
)
_WS_EX_TOPMOST = 0x00000008


def get_window_class(hwnd):
    """取窗口类名；失败返回空串，绝不抛异常。"""
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(hwnd, buf, 256):
            return ""
        return buf.value
    except Exception:
        return ""


def window_from_point(x, y):
    """屏幕坐标 (x, y) 正下方的最顶层窗口句柄（截图遮罩判定用）。"""
    try:
        return user32.WindowFromPoint(POINT(int(x), int(y)))
    except Exception:
        return 0


def _process_name(hwnd):
    """窗口所属进程的可执行文件名（小写）；失败返回空串。"""
    pid = wt.DWORD()
    if not hwnd or not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return ""
    # PROCESS_QUERY_LIMITED_INFORMATION：不要求管理员也能查路径
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wt.DWORD(260)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(h)


def is_console_window(hwnd):
    """是否控制台/终端宿主窗口：在这里自动发 Ctrl+C = 给程序喂中断。"""
    if not hwnd:
        return False
    if get_window_class(hwnd) in _CONSOLE_CLASSES:
        return True
    return _process_name(hwnd) in _CONSOLE_PROCESSES


def is_overlay_window(hwnd):
    """是否『置顶 + 盖满屏幕』的遮罩层（系统/第三方截图工具的选框）。

    用户按 PrtSc 后出现截图遮罩，在遮罩里拖动框选会被 MouseDragWatcher 误判
    成"拖选文字"，松手后盲发 Ctrl+C 砸到随后的窗口上（cmd 收到＝中断＝多行）。
    用『topmost + 覆盖 ≥90% 虚拟屏』这个几何特征把截图会话认出来并跳过。
    """
    if not hwnd:
        return False
    try:
        if not user32.IsWindowVisible(hwnd):
            return False
        if not (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & _WS_EX_TOPMOST):
            return False
        r = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        if vw <= 0 or vh <= 0:             # 取不到虚拟屏时退回主屏
            vw, vh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        return ((r.right - r.left) >= vw * 0.9
                and (r.bottom - r.top) >= vh * 0.9)
    except Exception:
        return False


def is_drag_blocked_at(x, y):
    """拖选按下点所在窗口是否『不该自动抓词』。返回原因串或空串：
    'console'=控制台（Ctrl+C 会被当中断）；'overlay'=截图遮罩（框选被当划词）。"""
    hwnd = window_from_point(x, y)
    if is_console_window(hwnd):
        return "console"
    if is_overlay_window(hwnd):
        return "overlay"
    return ""


def get_cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def get_screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


user32.GetWindowRect.argtypes = [HANDLE, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wt.BOOL


class MONITORINFO(ctypes.Structure):
    """GetMonitorInfoW 输出结构。cbSize 必须由调用方预填结构体大小。"""
    _fields_ = [("cbSize", wt.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wt.DWORD)]


user32.MonitorFromPoint.argtypes = [POINT, wt.DWORD]
user32.MonitorFromPoint.restype = HANDLE
user32.GetMonitorInfoW.argtypes = [HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL


def get_work_area():
    """主显示器工作区（排除任务栏），用于把悬浮窗夹在可见范围内。"""
    r = RECT()
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_virtual_screen():
    """虚拟桌面矩形 (x, y, w, h)——所有显示器外接矩形（v1.6.8 ⑤ 多显示器）。

    主屏不一定在虚拟原点：副屏可放在主屏左/上方，此时 x/y 为负
    （例：主屏在右、副屏 1920 宽居左 → x=-1920）。截图遮罩必须盖
    整个虚拟屏，否则副屏上无法框选；抓屏坐标也必须是虚拟屏语义
    （BitBlt 的屏幕 DC 就是虚拟屏坐标系）。
    """
    x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    if w <= 0 or h <= 0:              # 取不到虚拟屏时退回主屏
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return int(x), int(y), int(w), int(h)


def get_work_area_at(x, y):
    """点 (x, y) 所在显示器的工作区（排除任务栏）。坐标语义=虚拟屏坐标。

    SPI_GETWORKAREA 只返回主屏工作区：副屏上拖选后『译便』按钮/对照窗用
    它夹紧，落点会被错误夹回主屏（离鼠标十万八千里）。这里用
    MonitorFromPoint 定位真实所在屏（MONITOR_DEFAULTTONEAREST=2，指针在两屏
    边界缝里也能落到最近屏），取该屏 rcWork。任何失败退回主屏 get_work_area。
    """
    try:
        hmon = user32.MonitorFromPoint(POINT(int(x), int(y)), 2)
        if hmon:
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcWork
                return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return get_work_area()


def force_foreground(hwnd):
    """让隐藏窗口成为前台窗口（弹托盘菜单前必须，否则菜单拿不到键盘焦点）。

    SetForegroundWindow 对从未显示过的隐藏窗口几乎必然失败（Windows 的前台
    抢占限制）。经典解法是模拟一次 Alt 键按下/抬起，骗过系统的前台切换守卫，
    然后重试——很多托盘程序都这么干。
    """
    if user32.SetForegroundWindow(hwnd):
        return True
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    return bool(user32.SetForegroundWindow(hwnd))


user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                         ctypes.c_void_p, ctypes.c_uint]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000


def set_tool_window(hwnd, on=True):
    """把窗口从任务栏/Alt-Tab 里藏起来（悬浮窗不该在任务栏留图标）。"""
    if not hwnd:
        return
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if on:
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    else:
        style &= ~WS_EX_TOOLWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


user32.GetWindowLongW.argtypes = [HANDLE, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long


# ---------------------------------------------------------------- 剪贴板
_MAX_CLIP_DATA = 8 * 1024 * 1024      # 单格式数据上限 8MB，防止异常句柄拖爆内存

# 进程内剪贴板互斥锁（v1.4.1）：剪贴板是系统/进程全局资源，OpenClipboard
# 只允许一个打开者。CaptureWorker（抓词）与主线程（复制按钮）并发访问时，
# 不串行化会导致 GetClipboardData 返回失效句柄 → GlobalSize/GlobalLock 读坏
# 堆 → 0xc0000374 静默崩溃。所有 OpenClipboard 操作必须在本锁内完成。
# 注意：锁只加在"会 OpenClipboard"的函数上；_hglobal_read 等纯内存读的
# 辅助函数不加锁（调用者已持锁），否则 Lock 嵌套会死锁。
_CLIP_LOCK = threading.Lock()


def _hglobal_read(handle, limit=_MAX_CLIP_DATA):
    """安全读取一个 HGLOBAL 的全部内容；失败返回 None。

    加固点（v1.4）：GlobalSize 拿不到大小（返回 0）说明 handle 很可能不是
    全局内存块（如 CF_BITMAP 的 HBITMAP、增强图元文件的句柄），绝不盲读——
    string_at 无界读越界正是堆损坏/访问违例的常见来源。

    加固点（v1.4.1）：调用方必须已持有 _CLIP_LOCK；本函数不对非 HGLOBAL
    格式做 GlobalSize 探测（快照循环已按 _CF_NON_HGLOBAL 整类跳过）。
    """
    size = kernel32.GlobalSize(handle)
    if not size or size > limit:
        return None
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(handle)


def clipboard_snapshot(max_formats=64):
    """把剪贴板所有格式的数据原样抓一份到内存，用于事后还原。

    只快照确认是 HGLOBAL 且 GlobalSize 可用的格式——CF_BITMAP 等非内存块
    格式直接跳过（它们本来就是 GDI 句柄，按内存读/还原都是错的）。

    v1.4.1：整个 OpenClipboard→读取→CloseClipboard 全程持 _CLIP_LOCK，
    与 get/set/restore 串行化，杜绝并发访问剪贴板的竞态堆损坏。
    """
    with _CLIP_LOCK:
        if not user32.OpenClipboard(None):
            return None
        snap = {}
        try:
            fmt = 0
            for _ in range(max_formats):
                fmt = user32.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                # 非 HGLOBAL 格式（GDI 句柄/所有者显示）：整类跳过，
                # 绝不调 GlobalSize/GlobalLock（读坏堆的触发点）
                if fmt in _CF_NON_HGLOBAL:
                    continue
                handle = user32.GetClipboardData(fmt)
                if not handle:
                    continue
                data = _hglobal_read(handle)
                if data is not None:
                    snap[fmt] = data
        finally:
            user32.CloseClipboard()
        return snap


def clipboard_restore(snap):
    """还原剪贴板；传 None 表示放弃还原（例如抓词前剪贴板打不开）。"""
    if snap is None:
        return
    with _CLIP_LOCK:
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()
            for fmt, data in snap.items():
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
                if not handle:
                    continue
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    kernel32.GlobalFree(handle)
                    continue
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(fmt, handle):
                    kernel32.GlobalFree(handle)      # 失败要自己释放，否则泄漏
        finally:
            user32.CloseClipboard()


def get_clipboard_text():
    with _CLIP_LOCK:
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                handle = user32.GetClipboardData(CF_TEXT)
                if not handle:
                    return ""
                raw = _hglobal_read(handle)
                if raw is None:
                    return ""
                # CF_TEXT 未必有终止 NUL：按 GlobalSize 截到块内再解 mbcs
                return raw.split(b"\x00", 1)[0].decode("mbcs", "ignore")
            raw = _hglobal_read(handle)
            if raw is None:
                return ""
            # CF_UNICODETEXT：截齐到偶数字节再按 UTF-16 解码，取到第一个 NUL 为止
            raw = raw[:len(raw) - len(raw) % 2]
            text = raw.decode("utf-16-le", "ignore")
            return text.split("\x00", 1)[0]
        finally:
            user32.CloseClipboard()


def set_clipboard_text(text):
    """把译文写回剪贴板（复制按钮用）。全程 NULL 守卫，失败不碰 memmove。"""
    with _CLIP_LOCK:
        for _ in range(5):
            if user32.OpenClipboard(None):
                break
            import time
            time.sleep(0.02)
        else:
            return False
        try:
            user32.EmptyClipboard()
            data = (text + "\x00").encode("utf-16-le")
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)          # 失败自己释放，成功归系统
                return False
            return True
        finally:
            user32.CloseClipboard()


def set_clipboard_image(bmp_bytes):
    """把抓屏 BMP 字节放进剪贴板（CF_DIB），任意应用可直接 Ctrl+V 粘贴。

    与 Win+Shift+S 同一逻辑：BMP 文件 = BITMAPFILEHEADER(14B) + DIB，
    剪贴板只要 DIB（BITMAPINFOHEADER 起、自底向上像素，与抓屏输出一致），
    跳过 14 字节文件头即可。CF_DIB 是 HGLOBAL 内存块，快照/还原兼容
    （不在 _CF_NON_HGLOBAL 黑名单），不影响 v1.4.1 的加固逻辑。

    失败（打不开剪贴板 / 分配失败）返回 False，调用方自行决定是否提示。
    """
    if not bmp_bytes or len(bmp_bytes) <= 14 or bmp_bytes[:2] != b"BM":
        return False
    dib = bmp_bytes[14:]                 # 去掉 BITMAPFILEHEADER
    with _CLIP_LOCK:
        import time
        for _ in range(5):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            return False
        try:
            # 同系统截屏：新截图替换剪贴板旧内容（EmptyClipboard）
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
            if not handle:
                return False
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(ptr, dib, len(dib))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_DIB, handle):
                kernel32.GlobalFree(handle)          # 失败自己释放
                return False
            return True
        finally:
            user32.CloseClipboard()


def clipboard_has_dib():
    """剪贴板当前是否有 CF_DIB 位图（截图对照的探针/自检用）。"""
    with _CLIP_LOCK:
        if not user32.OpenClipboard(None):
            return False
        try:
            fmt = 0
            while True:
                fmt = user32.EnumClipboardFormats(fmt)
                if not fmt:
                    return False
                if fmt == CF_DIB:
                    return True
        finally:
            user32.CloseClipboard()


def get_clipboard_sequence():
    """剪贴板序号：变了才说明复制真的生效了，比 sleep(0.2) 可靠得多。"""
    return user32.GetClipboardSequenceNumber()


# ---------------------------------------------------------------- 消息循环
_PROCS = {}      # 防止 WNDPROC 回调被 GC


def create_hidden_window(on_message, class_name="QuickToolHiddenWindow"):
    """创建一个不可见窗口承载热键消息与托盘回调。"""
    hinstance = kernel32.GetModuleHandleW(None)

    def _wndproc(hwnd, msg, wparam, lparam):
        # 这两个消息必须交给 DefWindowProc，返回 0 会让 CreateWindowExW 静默失败
        # （不设置 GetLastError，极难排查）
        if msg in (WM_CREATE, WM_NCCREATE):
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        try:
            handled = on_message(hwnd, msg, wparam, lparam)
        except Exception:                                  # 不让异常打死消息循环
            handled = False
        if handled:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(_wndproc)
    _PROCS[class_name] = proc

    wcex = WNDCLASSEXW()
    wcex.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wcex.lpfnWndProc = proc
    wcex.hInstance = hinstance
    wcex.lpszClassName = class_name
    if not user32.RegisterClassExW(ctypes.byref(wcex)):
        err = ctypes.get_last_error()
        if err not in (1410,):        # ERROR_CLASS_ALREADY_EXISTS
            raise OSError(f"RegisterClassExW 失败，错误码 {err}")

    hwnd = user32.CreateWindowExW(0, class_name, "QuickTool", WS_OVERLAPPED,
                                  0, 0, 0, 0, None, None, hinstance, None)
    if not hwnd:
        raise OSError(f"CreateWindowExW 失败，错误码 {ctypes.get_last_error()}")
    return hwnd


def message_loop(on_error=None):
    """Win32 消息循环。

    GetMessageW 有三种返回：>0 正常、0 收到 WM_QUIT、**-1 出错**。
    旧版 `> 0` 的写法会把 -1 当退出条件之外的情况直接跳过循环——出错时
    线程静默死亡（钩子/托盘/热键全部失效，且无任何提示）。v1.4 起 -1
    走 on_error 回调（main.py 里记日志），连续出错则退出循环避免忙转。
    """
    msg = MSG()
    errors = 0
    while True:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret == -1:
            errors += 1
            try:
                if on_error:
                    on_error(errors, ctypes.get_last_error())
            except Exception:
                pass
            if errors >= 10:
                break
            continue
        if ret <= 0:
            break
        errors = 0
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def quit_message_loop():
    user32.PostThreadMessageW(kernel32.GetCurrentThreadId(), WM_QUIT, 0, 0)


def post_message(hwnd, msg, wparam=0, lparam=0):
    return bool(user32.PostMessageW(hwnd, msg, wparam, lparam))


# ---------------------------------------------------------------- 鼠标拖选钩子
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", ctypes.c_uint32),
                ("flags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
user32.SetWindowsHookExW.restype = HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, HANDLE, ctypes.c_uint32]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.UnhookWindowsHookEx.argtypes = [HANDLE]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [HANDLE, ctypes.c_int, WPARAM, LPARAM]


class MouseDragWatcher:
    """低层鼠标钩子：『按下左键 → 拖动 → 松开』视为一次拖选文字；
    另外『双击选词』（常见于英文阅读，位移≈0）也触发抓词。

    - 安装在 Win32 消息循环线程上（低层钩子的回调由安装线程的消息泵驱动）；
    - 回调里绝不干活：只记坐标、发一条自定义消息，抓词放到消息循环里做，
      避免在系统钩子回调内重入 SendInput / 剪贴板；
    - ignore_rect 非空时（迷你按钮所在矩形），该区域的鼠标事件不触发抓词，
      防止点自己的按钮又弹一遍按钮。
    """

    DRAG_THRESHOLD = 6          # 按下/弹起位移超过 6px 才算拖选（单击不触发）
    COOLDOWN = 0.8              # 两次触发之间的最小间隔，防连发

    def __init__(self, on_drag_end):
        self._on_drag_end = on_drag_end          # 回调 (x0,y0,x1,y1)：按下点与弹起点
        self._proc = None
        self._hook = None
        self._down = None                        # (x, y) 按下位置
        self._dblclk = None                      # (x, y) 双击按下位置（双击选词）
        self._last_fire = 0.0
        # v1.6.7 ③（P1 ③ 收尾）：跨线程字段——写=主线程（main._sync_drag_ignore，
        # 迷你按钮显示/隐藏时更新），读=本钩子线程（_inside_ignore，低层钩子热
        # 路径）。安全论证：值恒为 None 或四元组，CPython 属性赋值/读取在 GIL
        # 下原子，读者只见旧值或完整新值，绝不半写；更新时机在 MiniButton 创建
        # 之后，用户点击按钮必然晚于写操作几十 ms，读到的已是新矩形。故不加锁
        # （钩子热路径零锁优先），依赖 GIL + 写入时机即可。
        self.ignore_rect = None                  # (left, top, right, bottom)

    def start(self):
        if self._hook:
            return True
        self._proc = HOOKPROC(self._callback)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        return bool(self._hook)

    def stop(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _inside_ignore(self, x, y):
        r = self.ignore_rect
        if not r:
            return False
        left, top, right, bottom = r
        return left <= x <= right and top <= y <= bottom

    def _fire(self, x0, y0, x1, y1):
        """统一触发点：防连发 + 忽略自身按钮区域。回调携带 (按下点, 弹起点)。

        按下点供 main 判断『这次拖选发生在哪里』——截图遮罩里框选、控制台里
        QuickEdit 拖选都不该走自动抓词（会盲发 Ctrl+C 造成中断）。
        """
        if self._inside_ignore(x1, y1):
            return
        now = __import__("time").perf_counter()
        if now - self._last_fire >= self.COOLDOWN:
            self._last_fire = now
            try:
                self._on_drag_end(x0, y0, x1, y1)
            except Exception:
                pass

    def _callback(self, n_code, wparam, lparam):
        if n_code >= 0:
            try:
                data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x1, y1 = data.pt.x, data.pt.y
                if wparam == WM_LBUTTONDOWN:
                    self._down = (x1, y1)
                elif wparam == WM_LBUTTONDBLCLK:
                    self._dblclk = (x1, y1)      # 双击：记录选词起点
                elif wparam == WM_LBUTTONUP:
                    if self._down:               # —— 拖选结束 ——
                        x0, y0 = self._down
                        self._down = None
                        self._dblclk = None
                        if max(abs(x1 - x0), abs(y1 - y0)) >= self.DRAG_THRESHOLD:
                            self._fire(x0, y0, x1, y1)
                    elif self._dblclk:           # —— 双击选词结束（松开）——
                        x0, y0 = self._dblclk
                        self._dblclk = None
                        if max(abs(x1 - x0), abs(y1 - y0)) <= self.DRAG_THRESHOLD:
                            self._fire(x0, y0, x1, y1)
            except Exception:
                pass
        return user32.CallNextHookEx(self._hook, n_code, wparam, lparam)


# ---------------------------------------------------------------- 托盘
class Tray:
    """极简托盘：图标 + 右键菜单。失败时静默降级，不影响热键功能。"""

    def __init__(self, hwnd, tip="QuickTool", callback_msg=WM_APP_TRAY):
        self.hwnd = hwnd
        self.msg = callback_msg
        self.added = False
        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = callback_msg
        self.nid.hIcon = self._load_icon()
        self.nid.szTip = tip[:127]

    @staticmethod
    def _load_icon(size=32):
        """优先用 exe 自带图标（PyInstaller 打包后资源 ID 为 1）。"""
        IMAGE_ICON, LR_DEFAULTCOLOR = 1, 0x00000000
        hinst = kernel32.GetModuleHandleW(None)
        h = user32.LoadImageW(hinst, 1, IMAGE_ICON, size, size, LR_DEFAULTCOLOR)
        if not h:
            h = user32.LoadImageW(hinst, 1, IMAGE_ICON, 16, 16, LR_DEFAULTCOLOR)
        if not h:
            h = user32.LoadIconW(None, IDI_APPLICATION)
        return h

    def add(self):
        self.added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid)))
        return self.added

    def remove(self):
        if self.added:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            self.added = False

    def popup_menu(self, items):
        """弹出右键菜单，返回被点击项的 id（items 为 [(id, 文本), ...]，id<=0 为分隔符）。"""
        menu = user32.CreatePopupMenu()
        for item_id, label in items:
            if item_id <= 0:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                user32.AppendMenuW(menu, MF_STRING, item_id, label)
        force_foreground(self.hwnd)              # 隐藏窗口抢前台，菜单才有键盘焦点
        x, y = get_cursor_pos()
        cmd = user32.TrackPopupMenu(
            menu, TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            x, y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)   # 让菜单正常收尾
        user32.DestroyMenu(menu)
        return cmd


# ---------------------------------------------------------------- 截图抓屏
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_uint32 * 1)]


user32.GetDC.argtypes = [HANDLE]
user32.GetDC.restype = HANDLE
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.CreateCompatibleDC.argtypes = [HANDLE]
gdi32.CreateCompatibleBitmap.restype = HANDLE
gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = HANDLE
gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
gdi32.BitBlt.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, HANDLE, ctypes.c_int, ctypes.c_int,
                         ctypes.c_uint32]
gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO),
                            ctypes.c_uint]
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteDC.argtypes = [HANDLE]


def grab_screen_bmp(x, y, w, h):
    """抓取屏幕指定区域，返回 BMP 文件字节。

    本进程已 SetProcessDPIAwareness(1)，坐标是物理像素；
    BitBlt + CAPTUREBLT 可抓分层窗口。32bpp BI_RGB，自底向上行序与
    BMP 文件格式天然一致，直接拼文件头即可，无需 PIL。
    """
    SRCCOPY, CAPTUREBLT, DIB_RGB_COLORS = 0x00CC0020, 0x40000000, 0
    if w <= 0 or h <= 0:
        raise ValueError(f"截图区域尺寸非法：{w}x{h}")
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY | CAPTUREBLT):
            raise OSError("BitBlt 抓屏失败")
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = h          # 正值 = 自底向上，与 BMP 一致
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0     # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi),
                               DIB_RGB_COLORS):
            raise OSError("GetDIBits 读取像素失败")
        pixel_size = w * h * 4
        file_size = 14 + 40 + pixel_size
        header = (b"BM"
                  + file_size.to_bytes(4, "little")
                  + b"\x00\x00\x00\x00"
                  + (54).to_bytes(4, "little")
                  + (40).to_bytes(4, "little")       # BITMAPINFOHEADER.biSize
                  + w.to_bytes(4, "little", signed=True)
                  + h.to_bytes(4, "little", signed=True)
                  + (1).to_bytes(2, "little")        # biPlanes
                  + (32).to_bytes(2, "little")       # biBitCount
                  + b"\x00\x00\x00\x00"              # biCompression = BI_RGB
                  + pixel_size.to_bytes(4, "little")
                  + b"\x00" * 16)                    # 分辨率/调色板字段全 0
        return header + buf.raw[:pixel_size]
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, hdc)
