# QuickTool · 极简 Windows 效率工具集

划词翻译 / 截图 OCR / 截图对照 / 置顶便签 / RAG 快捷问答，一个常驻托盘的小工具箱。运行时**零第三方依赖**（唯一例外：PDF 解析用 pypdf），单文件 exe 约 **24 MB**，常驻内存 **~26 MB**，冷启动 **~1.1 秒**，绿色免安装。

| 快捷键 / 操作 | 功能 |
|---|---|
| `Ctrl+Q` | 划词翻译（被占用时自动顺延到 `Ctrl+Alt+T` → `Ctrl+Alt+F1` → …） |
| **鼠标拖选文字** | 松手后自动在光标旁弹出「译」「便」两个圆形迷你按钮：点「译」即翻译，点「便」把这段文字钉进置顶便签（等同 `Ctrl+Alt+N`，v1.6.3 新增） |
| `Ctrl+Alt+A` | 截图翻译（OCR，被占用自动顺延） |
| `Ctrl+Prtsc` | 截图对照（框选截屏 → 置顶小窗，v1.5 新增；v1.5.2 起自动进剪贴板可 Ctrl+V，小窗内 `Ctrl+S` 存 PNG） |
| `Ctrl+Alt+N` | 置顶便签（选中文字钉到置顶小窗，可累积多段，v1.6 新增；被占用自动顺延） |
| `Ctrl+Alt+R` | RAG 快捷问答（选中术语/问题 → 本地知识库检索 + 大模型流式回答，可多窗并存，v1.7 新增；被占用自动顺延） |
| `Ctrl+Alt+S` | 打开设置（被占用自动顺延到 `Ctrl+Alt+Shift+S`） |
| `Ctrl+Alt+Q` | 退出程序（被占用自动顺延） |
| `Esc` / 点击别处 / 再按一次热键 | 关闭悬浮窗 |

> 本仓库交付：**可运行的 `dist/QuickTool.exe` + 完整源码 + 本文档**（exe 与各版本产物同时发布在 GitHub Releases）。
> 源码全部由 Python 标准库实现（tkinter / ctypes / urllib / winreg / sqlite3），唯一第三方依赖例外是 PDF 文本提取用的 pypdf（纯 Python、零原生扩展，不参与翻译/问答主链路）。
>
> GitHub：https://github.com/wysdshg/QuickTool

---

## 目录
1. [开源方案调研与选型](#一开源方案调研与选型)
2. [技术选型](#二技术选型)
3. [项目结构](#三项目结构)
4. [核心实现](#四核心实现)
5. [翻译接口对比与接入](#五翻译接口对比与接入)
6. [配置说明](#六配置说明)
7. [打包成 exe 与发布](#七打包成-exe-与发布)
8. [方案优缺点与适用场景](#八方案优缺点与适用场景)
9. [自动化测试](#九自动化测试)
10. [后续扩展方向](#十后续扩展方向)
11. [版本演进记录（踩坑大事记）](#十一版本演进记录踩坑大事记)

---

## 一、开源方案调研与选型

GitHub 上「选中文本 + 快捷键 + 悬浮窗翻译」方向的成熟项目，调研结果如下：

| 项目 | 技术栈 | 体积 | 关键特性 | 与本需求的距离 |
|---|---|---|---|---|
| [Pot / pot-desktop](https://github.com/pot-app/pot-desktop) | Tauri + Rust | 安装包 20MB+，需 WebView2 | 全平台、20+ 翻译接口、OCR、插件系统、多接口对比 | 功能远超需求；改造成本高（Rust）；WebView2 常驻内存 40MB+ |
| [STranslate](https://github.com/ZGGSONG/STranslate) | C# WPF / .NET | 解压后 60MB+ | Windows 多引擎划词 + OCR、绿色便携、内置离线 OCR | 依赖 .NET 运行时；想改引擎/精简需啃 WPF 代码 |
| [SnapTranslate](https://github.com/ChenAI-TGF/SnapTranslate) | Python + tkinter | ~10MB | `Ctrl+L` 划词翻译、悬浮窗、生词本 | **最接近**：同样是 RegisterHotKey + 模拟 Ctrl+C；但①直接冲掉用户剪贴板不还原 ②只有 Google 接口（国内实测不通）③无托盘 ④无回退链 |
| [FlashTrans](https://github.com/SantaChains/FlashTrans) | Python + PySide6 + CTranslate2 | 依赖 + 模型 300MB+ | F1 划词、截图 OCR、离线 MarianMT | 离线是亮点但太重，模型下载 300MB+ |
| [WinLens](https://github.com/marco-beltrame/WinLens) | C# / WinUI | ~30MB | OCR 原位覆盖翻译、`Ctrl+Alt+T` | 偏 OCR 场景，非划词 |
| [PopTrans](https://github.com/ifocus9/PopTrans) | Go + Wails + llama.cpp | 模型 1GB+ | 本地 CPU 推理翻译 | 极客向，模型体量不适合"极简" |
| [Transgemi](https://github.com/hazemAI/Transgemi) | Python + WinOCR + LLM | — | 选区翻译、多 LLM 服务 | OCR 思路好，但依赖 LLM Key + Windows OCR 限制 |

### 为什么不直接改造它们

- **太重**：Pot / STranslate / FlashTrans / PopTrans 要么绑运行时、要么绑 WebView2、要么拖几百 MB 模型，和"极简轻量、启动快、内存低"的目标冲突。
- **太脆**：SnapTranslate 的技术路线（热键 + 模拟 Ctrl+C）完全正确，但其实现有三个致命伤——不还原剪贴板、Google 接口在国内不可达、没有回退与缓存。改造它的成本 ≈ 重写。
- **太偏**：WinLens 等走 OCR 路线，解决的是"选不中文字"的问题，不是本需求。

### 借鉴了它们的哪些设计

| 来源 | 借鉴点 |
|---|---|
| SnapTranslate | `RegisterHotKey` 注册全局热键 + 模拟 `Ctrl+C` 抓选区的技术路线 |
| Pot | 多引擎 + 失败自动回退链 + 结果缓存 |
| STranslate | 绿色便携（config.json 随程序走，可放 U 盘） |
| FlashTrans / Transgemi | 离线兜底思路（内置词库 / 可选本地大模型） |

结论：**自研极简实现（起步时约 800 行，随功能演化至今核心约 6000 行），把踩过的坑全部填掉，比"改造现成项目"性价比高得多**。

---

## 二、技术选型

### 候选方案对比

| 方案 | exe 体积 | 常驻内存 | 冷启动 | 依赖 | 二次开发 |
|---|---|---|---|---|---|
| **Python + tkinter + ctypes（本方案）** | **24.2 MB**（含 pypdf；不含 PDF 支持约 12.5 MB） | **~9 MB** | **~1.1 s** | 标准库 + pypdf | 门槛低 |
| C# WPF / WinForms | 60MB+ | 30~60MB | ~0.5s | .NET 运行时 | 中 |
| Tauri v2 (Rust) | 3~5MB 安装包 | 40MB+（WebView2） | ~1s | WebView2 | 高（Rust） |
| Electron | 150MB+ | 150MB+ | 2~4s | 自带 Chromium | 中 |
| Go + Win32 | 8~12MB | ~10MB | ~0.1s | 无 | 高（需交叉编译 UI） |

### 为什么是 Python + tkinter + ctypes

1. **零第三方依赖**（翻译/抓词/热键/托盘/悬浮窗全链路）：GUI 用内置 tkinter；热键/剪贴板/托盘全部用 `ctypes` 直调 Win32 API；网络用内置 `urllib`；知识库用内置 `sqlite3`。
2. **易改**：Python 读起来像伪代码，改引擎、加功能都很快，符合"轻量工具持续演化"的需求。
3. **不碰坑**：开发链简单（一台有 Python 的 Windows 就能打包），没有 Rust/WebView2/.NET 的工具链负担。

### 唯一的第三方依赖例外：pypdf

PDF 文档库导入（v1.7）引入 **pypdf**——纯 Python、零原生扩展，是中文 PDF 的 CID 字体必须解 ToUnicode 才能还原中文的必然选择（实测零依赖手写解流对中文 100% 乱码）。它只参与 PDF 导入链路，翻译/问答/划词等主功能不依赖它，打包经 spec excludes 瘦身后 exe 12.5MB → 24MB（详见 4.13）。

### 刻意砍掉的东西

| 常见做法 | 为什么不用 |
|---|---|
| `keyboard` / `pynput` 监听热键 | 它们装的是**全局键盘钩子**，属于"键盘记录器"行为，极易被杀软/EDR 拦截，部分场景还要管理员权限。`RegisterHotKey` 是系统级机制，无钩子、无权限要求 |
| `pywin32` 操作 Win32 | 打包后体积 +30MB 起；`ctypes` 声明几个结构体就够 |
| `requests` 发请求 | 只是 GET/POST + JSON，`urllib` 完全够用，少一个依赖 |
| `pyperclip` | 剪贴板需要"全格式快照/还原"（不只文本），`pyperclip` 做不到，直接用 Win32 剪贴板 API |
| 系统托盘库 `pystray` | 自己用 `Shell_NotifyIconW` 实现，约 60 行，省掉 Pillow 依赖 |

---

## 三、项目结构

```
QuickTool/
├── main.py                  # 入口：五线程模型、热键路由、事件分发、RAG worker
├── QuickTool.spec               # PyInstaller 打包配置（含 pypdf 瘦身 excludes）
├── build.bat / run.bat      # 一键打包 / 源码运行
├── requirements.txt         # 运行时依赖：无（打包机另需 pyinstaller + pypdf）
├── RELEASE_NOTES_v1.7.*.md  # 各版本发布说明（同步到 GitHub Release）
├── assets/icon.ico          # 程序图标
├── data/
│   ├── mini_dict.json       # 内置离线词库（306 词，断网兜底）
│   └── ecdict.txt           # 可选：完整离线词典（放这里自动加载）
├── qt/
│   ├── winapi.py            # 纯 ctypes：全局热键 / 剪贴板 / 光标 / 托盘 / 抓屏 / 鼠标钩子
│   ├── capture.py           # 选中文本提取 + PDF 断词清洗
│   ├── engines.py           # 翻译引擎（MyMemory/Google/LLM/离线）
│   ├── translator.py        # 引擎调度：LRU 缓存 + 失败回退链
│   ├── config.py            # 配置读写 + 凭据中心（五厂商/DPAPI 加密）+ 开机自启
│   ├── logging_setup.py     # 三层崩溃取证 + 心跳 + 轮转日志
│   ├── ocr.py               # Windows.Media.Ocr 桥（内嵌 PowerShell）
│   ├── ui.py                # 悬浮窗 / 设置 / 便签 / 对照窗 / 选区遮罩 / 迷你按钮
│   └── ragui.py             # RAG 问答窗 + 文档库管理窗
├── rag/                     # RAG 引擎（v1.7，纯标准库 + 云端 embedding/rerank）
│   ├── engine.py            # 五阶段总装：Fusion → 双路召回 → RRF → Rerank → 生成
│   ├── api.py               # OpenAI 兼容 API 封装（chat/embed/rerank，SSE 流式）
│   ├── store.py             # 本地知识库（sqlite FTS5 trigram）
│   ├── splitter.py          # fence-aware 切分（代码块原子化）
│   ├── bm25.py / vectors.py # BM25 检索 / 向量化与缺失补算
│   ├── fusion.py            # 查询变体生成 + RRF 融合
│   └── pdftext.py           # PDF 逐页提取 + 质量体检
└── tests/
    ├── smoke.py             # 冒烟测试（272 项断言）
    ├── e2e.py               # 端到端：真实按键触发全链路（源码 + 打包双跑）
    ├── perf.py              # 体测：体积 / 启动 / 内存 / 热键退出
    └── …                    # 其余专项/诊断脚本见第九节（本地开发用，不入库）
```

---

## 四、核心实现

### 4.1 全局热键：`RegisterHotKey`

```python
# qt/winapi.py
user32.RegisterHotKey(hwnd, hotkey_id, mods, vk)   # mods = MOD_CONTROL|MOD_ALT|MOD_NOREPEAT

# main.py 的消息路由（Win32 线程）
def _on_message(self, hwnd, msg, wparam, lparam):
    if msg == wa.WM_HOTKEY:
        if wparam == HK_TRANSLATE:
            self._do_translate()          # 在独立线程执行，不阻塞界面
        ...
```

用独立线程 + `GetMessageW` 消息循环监听，完全不碰键盘钩子。这个实现里有三个真实踩过的坑：

1. **修饰键残留**（最容易翻车）：用户按的是 `Ctrl+Alt+T`，此刻 Ctrl/Alt 都处于按下状态。如果不先把它们抬起就发 `Ctrl+C`，系统会收到 `Ctrl+Alt+C`，复制根本不触发。所以抓词前必须：
   ```python
   def release_modifiers():
       for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN):
           _send_input([_key(vk, up=True)])     # 先全部抬起
   ```
2. **热键被占用（错误码 1409）**：实测这台机器上 `Ctrl+Alt+T`、`Ctrl+Alt+S` 已经被其他软件占用（输入法/显卡面板/网盘/IDE 都会抢 `Ctrl+Alt+*`）。程序启动时若发现占用，**自动顺延**到下一个空闲组合（`Ctrl+Alt+F1` → `Ctrl+Alt+D` → …）并弹窗告知，而不是让用户面对"快捷键没反应"。
3. **`MOD_NOREPEAT`**：按住热键不放不会连续触发。

### 4.2 选中文本提取

Windows 没有"读取任意窗口当前选区"的通用 API，主流三条路线：

| 路线 | 原理 | 兼容性 | 代价 |
|---|---|---|---|
| **A. 模拟 Ctrl+C + 读剪贴板（本方案）** | `SendInput` 发 Ctrl+C → 监听剪贴板变化 → 读取 | 浏览器/PDF/Word/记事本/IDE/远程桌面都行 | 会短暂占用剪贴板，必须快照还原 |
| B. UI Automation `TextPattern` | 通过 UIA 接口直接读选中文本 | 不污染剪贴板，但自绘 UI/PDF/游戏不支持 | 依赖 comtypes/pywin32 |
| C. `WM_GETTEXT` / `SendMessage` | 只覆盖标准 Edit/RichEdit 控件 | 很窄 | — |

选 A，并做了三重保护：

```python
# qt/capture.py
def get_selected_text(timeout=0.45, retries=2):
    snapshot = wa.clipboard_snapshot()      # ① 全格式快照（含图片/文件）
    prev_seq = wa.get_clipboard_sequence()  # ② 用序列号判断复制是否完成，不盲等
    ...send Ctrl+C，轮询序列号...
    wa.clipboard_restore(snapshot)          # ③ 无论成败都还原，用户剪贴板无感
```

**组合键分步注入（v1.7.2）**：`send_ctrl_c` 曾把 Ctrl↓/C↓/C↑/Ctrl↑ 塞进同一个 SendInput 批次零间隔发送——Word 的异步输入管线会丢修饰键、把组合键当裸字符提交，表现为「拖选后选区被清除、原地多一个 c」。已改为分步注入并留键间隔（Ctrl↓ → 25ms → C↓↑ → 20ms → Ctrl↑），真机验证 Word 抓词正常（详见十一节 v1.7.2 条目）。

**PDF 断词清洗**：从论文/PDF 复制出来的文本常带换行断词（`the perfor-\nmance`），直接丢给翻译引擎会把词拆成两半。`normalize_text()` 会做断词合并、空白折叠、段落边界保留。

### 4.3 线程模型（v1.7 起为五个线程）

```
┌─ 主线程（Tk 事件循环）──────────────┐   ┌─ Win32 线程（独立消息循环）────┐
│ 悬浮窗 / 设置 / 便签 / RAG 界面      │   │ 全局热键 / 托盘消息 / 鼠标钩子  │
│ queue.Queue.poll(50ms)  ←────────── │   │ 只做轻量判断 + 投递消息         │
└─────────────────────────────────────┘   └───────────────────────────────┘
┌─ CaptureWorker ─────────┐  ┌─ JobWorker ──────────┐  ┌─ RagWorker ────────┐
│ 抓词（Ctrl+C + 等剪贴板）│  │ 联网翻译 / 截图 OCR   │  │ RAG 检索 + 流式生成 │
└─────────────────────────┘  └──────────────────────┘  └────────────────────┘
```

Tk 不是线程安全的，所有线程**只通过 `queue.Queue` 单向通信**，绝不跨线程操作控件。抓词/翻译/OCR/RAG 各用独立 worker，互不排队拖拽。

### 4.4 悬浮窗

- 无边框、置顶、半透明（`overrideredirect` + `-topmost` + `-alpha`）
- 从任务栏/Alt-Tab 隐藏（`WS_EX_TOOLWINDOW`）
- 出现在光标右下角，自动夹在工作区内（`SPI_GETWORKAREA`，避开任务栏）
- `Esc` / 点击别处 / 再按热键 / 自动隐藏（可配置）四种方式关闭
- 深色/浅色两种主题，字体大小、宽度、不透明度可调
- 翻译过程中先显示"翻译中…"，结果返回后原地刷新，不闪烁

### 4.5 迷你翻译按钮（拖选即出）

不想记快捷键？**用鼠标拖选一段英文，或双击选中一个词，松手后光标旁自动浮现「译」「便」两个圆形迷你按钮（并排 54px）**：点「译」即翻译，点「便」直接把已抓好的文字钉进便签（省一次剪贴板往返），2.2 秒不动自动消失（鼠标移上去即暂停计时）。按钮做到尽量小、半透明、不挡视野。

实现分三层：

1. **低层鼠标钩子 `WH_MOUSE_LL`**（`qt/winapi.py` 的 `MouseDragWatcher`）：监听 `WM_LBUTTONDOWN` 记录按下坐标、`WM_LBUTTONUP` 时若位移 ≥ 6px 判定为"拖选"；`WM_LBUTTONDBLCLK` 判定为"双击选词"（英文阅读最常见的选词方式），双击后的松开也会触发。普通单击不触发。钩子装在 Win32 消息循环线程上，**回调里只发一条自定义消息，绝不直接干活**——抓词（模拟 Ctrl+C）放到消息循环里做，避免在系统钩子回调内重入注入与剪贴板。
2. **抓词与过滤**：拖选结束后在消息循环里调 `get_selected_text()`，经 `looks_translatable` + 长度上限（默认 200 字符）过滤，防止误抓整页。
3. **按钮 UI**（`qt/ui.py` 的 `MiniButton`）：圆形、置顶、半透明、无边框；hover 高亮；点击后把文本交给 Win32 线程翻译（不阻塞界面）；按钮矩形会同步给鼠标钩子作为 `ignore_rect`——**点自己按钮不会又触发一次抓词**。

两个防打扰细节：悬浮窗打开期间不弹按钮；两次触发间隔 ≥ 1s（防连发）。

### 4.6 系统托盘

`Shell_NotifyIconW` 自实现（约 60 行）：右键菜单 = 设置 / 截图翻译 / 退出；双击打开设置。托盘添加失败时静默降级，不影响热键功能。

> ⚠️ 踩坑记录：托盘回调消息（`uCallbackMessage`）的 `lParam` 是 32 位值，**低 16 位才是鼠标事件**（`WM_RBUTTONUP` 等），高 16 位是图标 ID。早期版本直接拿整个 `lParam` 与 `WM_RBUTTONUP` 比较，永远不相等 → 右键菜单能弹出但点任何项都没反应。修复：`ev = lparam & 0xFFFF`。

### 4.7 截图翻译（OCR，v1.2 新增）

**解决"有些地方选不中文字"的场景**（受保护 PDF、软件界面、图片里的英文）：按截图热键（默认 `Ctrl+Alt+A`，被占用自动顺延）或点托盘菜单"截图翻译" → 全屏半透明遮罩上拖拽框选 → 自动 OCR 识别 → 走统一翻译管线弹译文悬浮窗。Esc / 右键取消。

技术方案（借鉴 STranslate 的交互，但 OCR 后端完全不同）：

| 环节 | 实现 | 为什么 |
|---|---|---|
| 框选遮罩 | `qt/ui.py` 的 `RegionSelector`（Tk 全屏 `overrideredirect` + 半透明 + 橡皮筋矩形） | 纯 Tk，零依赖；注意不能对隐藏 root 调 `transient()`（同 Settings 的 withdrawn 坑） |
| 抓屏 | `qt/winapi.py` 的 `grab_screen_bmp`（GDI `BitBlt + CAPTUREBLT`，纯 ctypes，自拼 32bpp BMP 文件头） | 本进程 DPI 感知，坐标即物理像素，**避开子进程 DPI 虚拟化坐标错位的坑**（实测踩过：DPI 不感知的进程抓 125% 缩放屏会抓偏） |
| OCR | `qt/ocr.py`：内嵌 PowerShell 脚本调用 **Windows 10/11 内置的 Windows.Media.Ocr**（WinRT） | 完全离线、无 API Key、无第三方依赖，契合零依赖铁律；PaddleOCR/Tesseract 都要 pip 依赖或外部二进制，打包体积和杀软风险都不可接受。PS 5.1 通过反射拿 `GetAwaiter` 兜 WinRT 异步（PS7 反而不支持 WinRT 投射） |
| 语言包 | `AvailableRecognizerLanguages` 探测，`auto` = 优先 `en-US`（主场景英→中），可在设置里指定 | OCR 语言取决于系统安装的语言（设置 → 时间和语言 → 语言 → 选项 → 下载"文本识别"）；无语言包时给出明确引导提示 |

⚠️ 已知限制：Windows OCR 对"屏幕清晰文字"效果很好，但手写体/艺术字/低分辨率截图效果一般；首次使用若提示"没有可用的 OCR 语言包"，按提示安装语言功能即可。OCR 结果会先过 `normalize_text` 清洗（合并断词换行）再翻译。

> ⚠️ 踩坑记录（热键顺延，v1.2 修复）：`HOTKEY_CANDIDATES` 曾用字符串做键（`"settings"`），而注册逻辑用 `hid`（int）查询 → `.get()` 永远查不到，**整条顺延链是死代码**——表现为每次启动都弹同一个"Ctrl+Alt+S 注册失败"。修复：改用 `HK_*` 整数做键 + 顺延结果全部持久化 + 程序内部组合查重（同一组合不能绑两个功能）+ 非核心键失败降级为提示（划词翻译键失败才算致命）。

> ⚠️ 踩坑记录（框选与划词钩子冲突，v1.2 修复）：迷你按钮的 `WH_MOUSE_LL` 钩子把**截图框选的拖动**也当成"划词拖选"，松开鼠标就触发一次全局 Ctrl+C 抓词。修复：`_handle_drag_end` 顶部加 `ocr_selector` 守卫——遮罩开着时直接忽略拖动结束事件。已由 `tests/ocr_full_app.py` 真 App 驱动验证（框选全程无迷你按钮弹出）。

### 4.8 截图对照小窗（v1.5 新增）

**解决"长网页要不断回头对照"的场景**：按 `Ctrl+Prtsc`（被占用自动顺延到 `Ctrl+Alt+W` → `Ctrl+Alt+D` → …）或点托盘菜单"截图对照" → 框选屏幕区域 → 该区域以**置顶小窗**固定显示，滚动网页时不用来回切窗口。Esc / 右键 / 右上角 ✕ 关闭，**用完即关、不创建窗口时零内存开销**。

**多窗口并存（v1.5.1）**：可同时打开最多 **5 个** 对照小窗，各自独立关闭。层级策略为「**点谁谁在前**」——点击任意窗口即 `lift + focus` 置前（无边框窗点击不自动抢焦点，需显式处理）。新窗口初始位置相对鼠标**级联偏移**（28px × 序号，向右下错开），避免新窗弹出直接盖住旧窗；工具条标题带编号（`对照 1`、`对照 2`…）。超过 5 个时提示"请先关闭一个"。

**保存 + 剪贴板（v1.5.2）**：与 Win+Shift+S 同一逻辑——**框选完成截图自动进剪贴板**（CF_DIB 位图，任何应用直接 Ctrl+V 粘贴）；文件保存则**主动触发**（工具条「存 PNG」按钮 / 快捷键 Ctrl+S），存 1:1 原始分辨率 PNG 到 `%USERPROFILE%\Pictures\QuickTool\`，命名 `QuickTool_时间戳.png`，保存成功 toast 提示路径。剪贴板"总是放"（零副作用）、文件"按需存"（不堆积）——这就是"怎么确定是否保存"的答案：**剪贴板无需确认，文件需明确意图**。

| 环节 | 实现 | 为什么 |
|---|---|---|
| 框选 | 复用 `RegionSelector`（加 `title` 参数区分文案） | 与截图翻译同一交互，零重复代码 |
| 抓屏 | 复用 `grab_screen_bmp`（GDI `BitBlt + CAPTUREBLT`） | 毫秒级、物理像素坐标 |
| 显示 | `qt/ui.py` 新增 `PinWindow`：置顶无边框 Toplevel + 工具条 + Canvas | 整窗可拖动；滚轮缩放 1x~1/8x（只缩小不放大，对照真实尺寸最实用）；顶部工具条显示当前缩放百分比 |
| 多窗口 | `app.pin_wins` 列表管理；`PinWindow` 带 `index` 序号 + `CASCADE=28` 级联偏移；点击 `lift()+focus_force()` 置前；`close()` 从列表移除自己 | 并存不互相顶掉；「点谁谁在前」层级；上限 `PIN_MAX=5` 防失控 |
| 剪贴板 | `set_clipboard_image`：BMP 去 14B 文件头 → **CF_DIB**（HGLOBAL，走 `_CLIP_LOCK` 串行）→ `SetClipboardData` | 与 Win+Shift+S 同格式，微信/QQ/Word 直接粘贴；CF_DIB 是内存块，快照/还原兼容（不在非 HGLOBAL 黑名单） |
| 保存 | `_save_png`：复用 `_bmp_to_png` → 写 `Pictures\QuickTool\`；工具条按钮 + Ctrl+S 触发 | 存 1:1 原图；主动按键 = 明确保存意图；toast 非阻塞提示（不卡主线程） |
| BMP→图像 | **标准库 zlib 手写 PNG 编码**（8bit RGB，IDAT 压缩）→ base64 → `tk.PhotoImage` | `tk.PhotoImage` 不认 BMP（只认 GIF/PPM/PNG），PIL 又违反零依赖铁律；PNG 压缩后 base64 体积远小于裸 BMP，且 Tk 8.6 原生支持 |

> ⚠️ 关键设计：`PinWindow` 打开期间 `_handle_drag_end` 也会忽略拖选（同 `ocr_selector` 守卫）——不然在对照窗里拖一下就会弹迷你按钮。版本号顺延至 1.5.0（单窗）、1.5.1（多窗）、1.5.2（保存+剪贴板）。

### 4.9 拖选卡鼠标的修复（v1.3）

**症状**：程序运行时按住左键拖一下、没选中任何文字，鼠标会卡一下（约半秒）。

**根因**：`WH_MOUSE_LL` 低层鼠标钩子的回调，靠**安装它的那个线程的消息泵**驱动，而抓词原本就跑在同一个线程（Win32 消息循环线程）里。抓词流程是"模拟 Ctrl+C → 轮询等剪贴板序列号变化"：

- 选中了内容 → 剪贴板很快变化，立即返回，无感；
- **没选中内容 → 等满 `timeout × retries`（0.45s × 2）**，这期间钩子线程取不走钩子通知，系统派发鼠标消息被拖住 → 卡。

修复前的实测（`tests/drag_lag.py`，统计鼠标消息到达间隔）：基线 22ms，**拖选后尖峰 467ms**；修复后同法复测：**拖选后 23ms、>150ms 尖峰 0 个**，`VERDICT=SMOOTH`。

**修复**：把重活全部移出钩子线程，四个线程各司其职——

| 线程 | 职责 | 允许阻塞？ |
|---|---|---|
| 主线程 | tkinter 界面 | 否 |
| `Win32Thread` | 热键 / 托盘 / 鼠标钩子，**只做轻量判断 + 投递消息** | **绝对不行** |
| `CaptureWorker` | 抓词（Ctrl+C + 等剪贴板） | 可以（最坏 ~0.7s） |
| `JobWorker` | 联网翻译 / 截图 OCR | 可以（秒级） |
| `RagWorker` | RAG 检索 + 流式生成（v1.7 起，独立于 JobWorker——一段 10s 的回答不能堵住翻译/OCR 排队） | 可以（秒~十秒级） |

顺带治好了同源的两个隐患：热键翻译的网络往返（1~3s）、截图 OCR 的 PowerShell 子进程（1~3s）原本也堵在钩子线程上。另外抓词首轮改为 0.25s 快速失败（慢程序由第二轮兜底），让"根本没选中内容"的场景更早收手。

验证（`tests/lag_workers.py`：把抓词替换成 `sleep(0.8s)` 制造最坏情况，再用跨线程 `SendMessage` 测钩子线程的消息泵往返延迟，只针对本进程实例、不受其他进程干扰）：

| 模式 | 抓词所在线程 | 抓词期间钩子线程往返延迟 |
|---|---|---|
| 修复前（`--baseline` 对照） | `Win32Thread` | **800 ms** |
| 修复后 | `CaptureWorker` | **0 ms** |

### 4.10 换电脑 / 绿色分发

**结论：直接复制 `dist\QuickTool.exe` 到其他电脑就能用。** 它是 PyInstaller `--onefile --windowed` 单文件产物：不写注册表、不需要安装、不需要目标机装 Python 或运行库（Win10/11 自带 UCRT）。

实测（`tests/portable_check.py`）：把 exe 单独复制到空目录（无 `qt/`、无 `data/`），并伪造一个空白 `APPDATA` 模拟"新电脑首次运行"，跑完整自检流程 —— **退出码 0、8 个关键事件齐全、配置正确落到 `%APPDATA%\QuickTool\config.json`**，`PORTABLE PASS`。

| 依赖 | 是否随 exe 走 | 说明 |
|---|---|---|
| Python 解释器 + 标准库 + tkinter | ✅ 打包在 exe 内 | 目标机无需装 Python |
| 离线词库 `data/mini_dict.json` | ✅ `datas` 打包 | 经 `sys._MEIPASS` 解包路径读取 |
| 托盘图标 | ✅ 取 exe 自带资源 ID=1 | 不依赖外部 png/ico |
| OCR 引擎（Windows.Media.Ocr） | ✅ 系统自带（Win10/11） | 但**识别语言包取决于系统**，见第 3 点 |
| `config.json` | ❌ 运行时生成 | 见第 2 点 |

换机后需要注意的 5 件事：

1. **热键可能被自动顺延**（这是特性不是 bug）。新电脑上如果 `Ctrl+Q` 被微信/输入法/浏览器扩展占用，程序会自动换用候选组合并弹一条非阻塞提示，同时把结果写进配置（下次启动不再重新报）。实测在本机已有实例占用的环境下，四个默认组合全部命中 1409，程序自动落位为 `Ctrl+Alt+F1` / `Ctrl+Alt+,` / `Ctrl+Alt+Shift+Q` / `Ctrl+Alt+P`，功能全部可用；托盘菜单里也能手动改。**不要同时开两个实例**——它们会互相抢热键（本机实测 2 个实例并存时后启动的那个四个键全被顺延）。
2. **设置是否随身带**：程序同目录有 `config.json` 就走绿色便携模式（放 U 盘最合适）；没有则读写 `%APPDATA%\QuickTool\config.json`。想把引擎/API Key/热键一起搬到新电脑，把 `config.json` 和 exe 放同一个文件夹复制过去即可。
3. **截图翻译需要系统 OCR 语言包**：设置 → 时间和语言 → 语言 → 首选语言 → 选项 → 下载"文本识别"。新电脑没装 `en-US` 时，截图翻译会给出安装引导，**划词翻译完全不受影响**。
4. **联网与 Key**：默认 MyMemory 需联网（免 Key）；离线词库断网可用（单词级）；大模型翻译引擎需要在新电脑上能访问对应 API 且配置里有 Key。
5. **首次运行的拦截提示**：exe 未签名，新电脑上可能遇到 SmartScreen（点"更多信息 → 仍要运行"）；个别杀软会误报 PyInstaller 单文件产物，加白名单即可。另外开机自启是写 `HKCU\...\Run` 里的**绝对路径**，换了目录或电脑后需要在设置里重新勾选一次。

### 4.11 托盘静默消失的排查与日志系统（v1.4）

**用户报告的现象**：程序跑一段时间后（电脑一直开着、人离开一会儿再回来）托盘图标没了、热键也失效，只能重新打开。

**排查结论：不是 Windows 安全中心。** 按 `tests/_diag_events_tmp.py` 的取证流程翻 Windows 事件日志（Application Error 1000 / WER 1001）发现：当日 3 次 `0xc0000374`（**堆损坏**）崩溃，出错模块 `ntdll.dll`，Defender Operational 日志零记录（排除杀软误杀），explorer.exe 无崩溃（排除资源管理器重启导致托盘图标消失）。堆损坏发生在 C 层，Python 异常捕获不到，进程直接死亡——所以表现为"安静地没了"。

**为什么定位不了具体哪一行**：堆损坏是"先在某处越界写坏堆元数据，之后任意一次堆操作才爆出来"，崩溃点（ntdll 的 RtlFreeHeap/RtlAllocateHeap）通常离真正的元凶很远。静态审查排除了句柄截断（64 位句柄 restype 全部正确声明）、托盘/菜单/GDI 句柄生命周期、跨线程 Tk 调用（四个线程全部只经队列通信）等问题，剩余最可疑的是剪贴板无界读取。所以 v1.4 做了两层动作：

**1) C 边界加固（`qt/winapi.py`）**：
- `get_clipboard_text`：原来 `string_at(ptr)` / `wstring_at(ptr)` 读到 NUL 为止，对**没有终止符的畸形剪贴板数据会越界读**——现改为按 `GlobalSize` 有界读取、截齐偶数字节、解到第一个 NUL 为止；
- `clipboard_snapshot` / `clipboard_restore`：只快照确认是 HGLOBAL 的格式（`GlobalSize` 返回 0 的非内存块格式如 CF_BITMAP 直接跳过），单格式上限 8MB；
- `set_clipboard_text`：`GlobalAlloc`/`GlobalLock` 失败时不再裸 memmove，失败路径释放句柄；
- `message_loop`：`GetMessageW == -1` 不再静默跳过（旧写法会导致消息循环线程静默死亡），连续出错记录并退出；
- 新增 `TaskbarCreated` 监听：explorer 重启会收走所有托盘图标，现在收到广播会自动重新挂图标。

**2) 运行日志系统（`qt/logging_setup.py`，用户要求的取证基建）**：
- 日志位置跟随配置的便携逻辑：`<exe 同目录>\logs\QuickTool.log`（便携）或 `%APPDATA%\QuickTool\logs\`（安装模式），2MB 轮转保留 3 份；
- **三层崩溃取证**：`faulthandler`（C 层访问违例时 dump 各线程 Python 栈到 `crash.log`）+ 三处异常钩子（`sys.excepthook` / `threading.excepthook` / `Tk.report_callback_exception`——windowed exe 没有 stderr，Tk 回调异常默认是黑洞，现在全部落盘）+ 关键事件埋点（启动/热键注册/托盘/抓词/翻译/OCR/退出，含线程名）；
- **5 分钟心跳**：`HEARTBEAT threads=[...]`，区分"进程活着但空闲"与"假死"；
- 日志系统自身任何异常都被吞掉，绝不影响主程序。
- ⚠️ 白盒测试抓到一个自伤 bug：`sys.excepthook` 曾误写成单参签名，任何未捕获异常都会先触发 "Error in sys.excepthook"（钩子自己先炸）——已修为标准三参 `(type, value, tb)` 并加防回归断言（`tests/taskbar_rebuild_check.py` 触发真实未捕获异常验证）。

**验证**：smoke 40/40（新增日志 8 项断言，含 sys.excepthook 三参签名防回归）、e2e 10/10×2（源码 + 打包版）、harden_check 14/14（v1.4.1 起含并发剪贴板压力场景）、taskbar_rebuild_check 5/5、lag_workers 白盒回归 PASS。日志实测样例见下——崩溃后再看最后一条就知道死前在做什么：

```text
2026-08-31 19:53:15.379 [INFO] [MainThread] BOOT QuickTool v1.4 pid=36060
2026-08-31 19:53:15.424 [INFO] [Win32Thread] HWND-CREATED hwnd=3476256
2026-08-31 19:53:15.434 [INFO] [Win32Thread] TRAY-ADD ok=True
2026-08-31 19:53:15.434 [INFO] [Win32Thread] HOTKEY-OK hid=1 combo=Ctrl+Q
2026-08-31 19:53:17.536 [INFO] [Win32Thread] CAPTURE-START kind=hotkey
2026-08-31 19:53:18.514 [INFO] [JobWorker] TRANSLATE-END engine=MyMemory ok=True
2026-08-31 19:53:22.244 [INFO] [MainThread] QUIT-START
```

**如果以后再出现**：直接把 `logs\QuickTool.log`（和 `logs\crash.log`）发给开发者即可——即使还是堆损坏崩溃，faulthandler 的线程栈 + 最后一条事件能大幅缩小嫌疑范围。

**如果以后再出现**：取 `logs\QuickTool.log` 最后一条事件 + `crash.log` 的 faulthandler 栈即可定位（当前版本已确认崩溃点不会再是剪贴板并发）。

### 4.12 置顶便签（v1.6 新增）

**解决"读长文被术语/长段落打断，回来找不到读到哪"的场景**：选中那段文字按 `Ctrl+Alt+N`（被占用自动顺延到 `Ctrl+Alt+M` → …）、点托盘菜单"置顶便签"，或**鼠标拖选后点迷你按钮的「便」**（v1.6.3）→ 内容被**搬运**到屏幕角落的置顶便签窗（默认 520×420），原文位置不受任何影响。

> 💡 三种入口终点完全一致，只是取词方式不同：热键与托盘走「模拟 Ctrl+C 抓词」，**迷你按钮「便」直接用拖选时已经抓好的文字**，少一次剪贴板往返，也更快。

**大小与位置记忆（v1.6.2）**：无边框窗没有系统缩放边框，右下角放了 **`◢` 拖拽手柄**自由调整大小（最小 320×240 夹紧）；关窗时把大小和位置写进配置（`note_w/h/x/y`），下次打开**原样恢复**——便签钉在哪、多大，都是你上次留下的样子。防「打开即关」越关越小：`winfo` 读数未布局完时是 1，低于最小值就回退到逻辑尺寸。

**和截图对照（4.8）的本质区别**：截图对照解决"**看**别处的东西"（一张图一窗口），便签解决"**读过的文字暂存**"（多段累积到同一窗口）。文本可以合并，所以单窗口累积就是正确形态——滚动顺序本身就是你的跳转历史。

| 能力 | 实现 | 设计理由 |
|---|---|---|
| 单窗口累积 | `app.note_win` 单例；已有便签就 `append` 追加一段，没有才新建 | 多开挡视线；段头 `── 序号 · 时间 · 来源窗口标题 ──` 灰色小字记录出处 |
| 段定位 | 每段正文打 `seg{n}` tag，光标用 `compare()` 判断所在段 | tag 定位不受行号漂移/文字增删影响 |
| 搜索 | 顶部「搜索」按钮弹出输入框，实时在便签**全文**里高亮全部命中并跳到当前匹配（Enter / Shift+Enter 前后移动，Esc 关闭） | 便签是「把散落片段攒到一起长期看」的工具，内部搜索比「跳回原应用搜」更直觉也更常用 |
| 可编辑 | `ScrolledText`（tkinter 自带，零依赖） | 查完术语把解释贴回片段下面，自动形成带上下文的学习笔记 |
| 裁剪 | 超 50000 字从最老段整段连头删除（`_first` 推进） | 长时间使用不爆内存；最老的最可能已经不需要 |
| 保存/复制 | 存 txt 到 `%USERPROFILE%\Documents\QuickTool\`（Ctrl+S 同效）；「复制」全量进剪贴板 | 文本文件不进图片库，与截图分目录 |
| 不做翻译过滤 | 抓词不走 `looks_translatable` | 代码、术语、公式也该能钉——这是与划词翻译的关键差异 |

> ⚠️ 关键实现细节：
> - **拖动只绑工具条 Frame**：Text 控件事件会沿 bindtags（widget→class→toplevel→all）传播到 Toplevel——拖动若绑在 Toplevel 上，点击正文字会触发拖动、选不中字。父 Frame 不在 Text 的 bindtags 链上，天然免疫。
> - **来源窗口在热键触发瞬间记录**：`_on_message` 跑在 Win32 线程，此刻前台还是用户正在读的窗口；抓词的模拟 Ctrl+C 之后前台可能已变，必须**立刻**记 `GetForegroundWindow()` + 标题。
> - **搜索条内联在顶部**：「搜索」按钮切换一个 `pack`/`pack_forget` 的输入框（在工具条之下、正文之上），不占布局空间；实时 `Text.search(nocase=True)` 高亮全部命中，金色高亮当前命中并 `see()` 滚动到位。
> - **便签打开期间 `_handle_drag_end` 忽略拖选**（同 `ocr_selector`/`pin_wins` 守卫）——不然在便签里选字会弹迷你按钮。
> - **Esc 关窗**；`_trim` 连段头一起删（正文起点上一行就是段头）。

**搜索为什么比「回搜」更合适**：旧版「回搜」是把光标段前 60 字塞进来源应用的查找框让它自己搜——搜的是原应用、且依赖触发时记录到的来源窗口句柄，容易「没记录到来源窗口」而失效。便签本就是长期攒读物的地方，直接在便签内全文搜索（高亮 + 跳转）才符合用户直觉，也不受来源窗口是否还在的限制。

### 4.13 RAG 快捷问答（v1.7 新增，5 个新模块）

**解决"读到的概念要追问原理"的场景**：选中术语/问题按 `Ctrl+Alt+R`（被占用自动顺延）→ 置顶问答窗（多窗并存、流式逐字渲染）→ 回车提问 → 本地知识库检索 + 大模型流式回答，答案附来源章节。

| 组件 | 实现 | 说明 |
|---|---|---|
| 检索管线（`rag/engine.py`） | 五阶段：① RAG-Fusion 查询变体（LLM 改写 3 个角度，失败降级仅原问题）→ ② BM25 + bge-m3 向量双路召回 → ③ RRF 融合 → ④ bge-reranker 精排 → ⑤ 上下文组装 + 流式生成 | 单路故障不阻塞：变体/embed/rerank 任一失败自动降级跳过 |
| 本地知识库（`rag/store.py`） | sqlite FTS5 trigram 分词，库文件随 config 目录走（便携） | 中文无需分词即可子串命中，纯标准库 |
| 切分（`rag/splitter.py`） | 目标 500 字符/重叠 50；**代码块原子化**——fence 块 ≤3000 字符永不切、独占 chunk，>3000 整块跳过（实测几乎全是数据 blob） | "讲解算法+参考代码"类文档不再被腰斩 |
| API 封装（`rag/api.py`） | OpenAI 兼容 urllib，chat 走 SSE 流式；`enable_thinking=False` 实测提速 ~4 倍（Qwen3 系）；429/5xx 指数退避重试 | **双源架构**：生成走凭据中心所选厂商，检索（embed/rerank）固定硅基流动 bge-m3 / bge-reranker-v2-m3 |
| 上下文组装 | 预算 8000 字符（v1.7.2，配合精排 TopK=10）；**同节去重**（同文档同标题链只留最高分块）、**代码配额=1**（防小模型注意力稀释） | 实测 TopK 5→10 后枚举类问题（锁升级/GC Roots）答案完整度显著提升 |
| PDF 导入（`rag/pdftext.py`） | pypdf 后台线程逐页提取，拼「第 N 页」标题链入库；导入即质量体检（扫描版 / CID 乱码 / 图片页偏多） | pypdf 是全项目唯一第三方依赖例外 |
| 凭据中心（`qt/config.py`） | 五家厂商（硅基流动/魔搭/智谱/DeepSeek/自定义 OpenAI 兼容）统一管 Key，落盘 DPAPI 加密；生成厂商下拉切换，翻译 LLM 与 RAG 生成共用 | Key 绑定本机本账户，换机需重填（安全设计） |
| 界面（`qt/ragui.py`） | `QaWindow` 置顶问答窗（多窗并存、win_id 队列路由流式渲染）+ `KbManager` 文档库管理（导入 .txt/.md/.pdf、文件夹递归批量导入、向量进度） | 选中内容只作生成侧「背景」，不混入检索关键词 |

**关键防坑**：生成端点在 RagApi 构建时固化——设置页改厂商后必须重建引擎（v1.7.2 起 `_save` 自动调 `invalidate_rag_engine()`，保存即生效免重启）。

### 4.14 打包与发布流程（v1.7.2 起）

```
1. 改代码 → smoke 全绿（python tests/smoke.py）→ e2e 双跑（python tests/e2e.py [--exe]）
2. 改版本号：main.py APP_VERSION + ui.py 页脚 + README 版本日志
3. 打包：py -3.12 -m PyInstaller QuickTool.spec --noconfirm --clean
4. 提交：git add <改动文件> && git commit && git tag vX.Y.Z && git push origin main vX.Y.Z
5. 发布：cp dist/QuickTool.exe dist/QuickTool_vX.Y.Z.exe
        gh release create vX.Y.Z dist/QuickTool_vX.Y.Z.exe --title "…" --notes-file RELEASE_NOTES_vX.Y.Z.md
```

产物只发**单个 exe**（dist/logs、config.json 不入库不发版）；exe 与 Release 说明、git tag 三者版本号保持一致。

---

## 五、翻译接口对比与接入

### 5.1 方案对比（额度截至 2026-08，以官方控制台为准）

| 方案 | 费用 | 免费额度 | 速度 | 稳定性 | 需 Key | 隐私 | 推荐场景 |
|---|---|---|---|---|---|---|---|
| **MyMemory**（内置默认） | 免费 | 匿名约 5000 字/天，留邮箱 5 万字/天 | 中（~1.3s） | 中，偶尔限流 | 否 | 文本上传 | 日常查词/短句，零成本起步 |
| Google 网页接口 (gtx) | 免费 | 无明确限制 | 快 | **国内网络基本不可达**（本机实测超时） | 否 | 上传 | 仅海外网络环境 |
| 腾讯翻译君 TMT | 免费 | **500 万字符/月** | 快 | 高（大厂 SLA） | 是 | 上传 | 主力免费引擎，中文优化好 |
| 百度翻译 | 免费 | 标准版 QPS=1，个人认证后更高 | 中 | 高 | 是 | 上传 | 需要实名认证，起步略麻烦 |
| 阿里云机器翻译 | 免费 | 100 万字符/月 | 中 | 高 | 是 | 上传 | 阿里系生态 |
| **大模型 API**（DeepSeek / 硅基流动 / 智谱 / 通义 / Ollama） | 见下 | 智谱 `glm-4-flash` 全免费；硅基流动部分免费模型 | 中（~1-3s） | 高 | 是 | 上传（敏感内容选 Ollama） | 长句/术语/论文/上下文理解，**质量最优** |
| **本地 Ollama** + 小模型 | 免费 | 无限制 | 慢（CPU） | 高（本地） | 否 | **完全离线** | 隐私优先、断网环境 |
| 离线词库（内置 mini_dict / ECDICT） | 免费 | 无限制 | 瞬时 | 高 | 否 | **完全离线** | 查单词兜底，断网也有反应 |

**大模型成本量级**（以 DeepSeek 为例）：约 ¥1/百万 tokens，翻译一千个英文单词大约 **¥0.003**，几乎可忽略；响应通常 1~3 秒，质量显著优于机器翻译，尤其擅长处理专业术语与上下文。

### 5.2 本机实测（2026-08-30，`tests/smoke.py` 第 6 节）

| 引擎 | 结果 |
|---|---|
| MyMemory | ✅ 可用，`artificial intelligence is reshaping the software industry` → 人工智能正在重塑软件行业，~1.3s |
| Google gtx | ❌ 超时（本机网络环境下不可达） |
| 离线词库 | ✅ 瞬时，`benchmark` → n. 基准；基准测试 |
| DeepSeek / 硅基流动 | 端口可达（HTTP 401 = 缺 Key），配置 Key 后即可用 |

### 5.3 各引擎接入配置（设置页 → 凭据中心，v1.7 起）

```jsonc
// MyMemory / 离线词库：零配置，直接用
// Google：engine 切 "google"（仅海外网络可达）

// 凭据中心（config.json 对应结构）：五家厂商各填各的 Key，生成厂商下拉切换
"providers": {
  "siliconflow": {  "base_url": "https://api.siliconflow.cn/v1",
                    "api_key": "sk-…", "chat_model": "Qwen/Qwen3-8B" },
  "modelscope": {   "base_url": "https://api-inference.modelscope.cn/v1",
                    "api_key": "…",    "chat_model": "Qwen/Qwen3.8-Flash-Next" },
  "zhipu":        { "base_url": "https://open.bigmodel.cn/api/paas/v4", "chat_model": "glm-4.5-flash" },
  "deepseek":     { "base_url": "https://api.deepseek.com/v1", "chat_model": "deepseek-chat" },
  "custom":       { "base_url": "http://localhost:11434/v1" }   // 本地 Ollama 也走这里
},
"llm": {
  "provider": "modelscope",   // 生成厂商 = providers 键名（翻译 LLM + RAG 回答共用）
  "model": ""                 // 模型覆盖；留空 = 厂商默认 chat_model
},

// 引擎调度：
"engine": "mymemory",            // 主引擎
"fallback_chain": ["offline"],   // 回退链
```

> Key 落盘自动 DPAPI 加密（`__dpapi__:` 前缀，仅本机本账户可解）；每厂商的模型历史存在 `providers.<厂商>.models`（设置页下拉直选）。本地 Ollama 完全离线：装好后 `custom` 填 `http://localhost:11434/v1` 即可，敏感数据不出本机。

**推荐的省钱组合**：日常查词用 MyMemory（零配置）；追求质量和上下文用智谱 `glm-4-flash`（完全免费）；断网/隐私场景切 Ollama 或离线词库。

### 5.4 离线方案

- **单词级（已内置）**：`data/mini_dict.json` 306 个高频词，断网也能查。
- **完整离线词典（可选）**：把 ECDICT（77 万词，约 30MB）转成 `word<TAB>释义` 存到 `data/ecdict.txt`，程序启动时自动加载，单机可查所有常见词。
- **离线整句（可选）**：配置本地 Ollama + `qwen2.5:7b` 之类小模型，`base_url` 填 `http://localhost:11434/v1` 即可，全程数据不出本机。

---

## 六、配置说明

配置文件位置（二选一，**程序同目录的 config.json 优先**，绿色便携）：

- 绿色便携模式：`config.json`（exe 同目录）
- 默认：`%APPDATA%\QuickTool\config.json`

```jsonc
{
  "hotkey_translate": "Ctrl+Q",         // 划词翻译（被占用自动顺延到 Ctrl+Alt+T → Ctrl+Alt+F1 → …）
  "hotkey_ocr":       "Ctrl+Alt+A",     // 截图翻译（被占用自动顺延到 Ctrl+Alt+I → …）
  "hotkey_pin":       "Ctrl+Prtsc",     // 截图对照小窗（被占用自动顺延到 Ctrl+Alt+W → …）
  "hotkey_note":      "Ctrl+Alt+N",     // 置顶便签（被占用自动顺延到 Ctrl+Alt+M → …）
  "hotkey_rag":       "Ctrl+Alt+R",     // RAG 快捷问答（被占用自动顺延到 Ctrl+Alt+Y → …）
  "hotkey_settings":  "Ctrl+Alt+S",     // 打开设置（被占用自动顺延并记住，不再每次报错）
  "hotkey_quit":      "Ctrl+Alt+Q",     // 退出（被占用自动顺延）

  "engine": "mymemory",                 // 翻译主引擎
  "fallback_chain": ["offline"],        // 回退链
  "source_lang": "auto",
  "target_lang": "zh-CN",

  // ---- 凭据中心（v1.7）：五家厂商统一管 Key，api_key 落盘 DPAPI 加密 ----
  "providers": {
    "siliconflow": {                    // 检索专用：向量/重排固定用这一家
      "base_url": "https://api.siliconflow.cn/v1",
      "api_key": "",
      "chat_model": "Qwen/Qwen3-8B",
      "embed_model": "BAAI/bge-m3",
      "rerank_model": "BAAI/bge-reranker-v2-m3",
      "thinking": true,
      "models": []                      // 本厂商用过的模型历史（设置页下拉直选）
    },
    "modelscope": { "base_url": "https://api-inference.modelscope.cn/v1",
                    "api_key": "", "chat_model": "Qwen/Qwen3.8-Flash-Next",
                    "thinking": true, "models": [] },
    "zhipu":    { "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "api_key": "", "chat_model": "glm-4.5-flash", "thinking": false, "models": [] },
    "deepseek": { "base_url": "https://api.deepseek.com/v1",
                  "api_key": "", "chat_model": "deepseek-chat", "thinking": false, "models": [] },
    "custom":   { "base_url": "", "api_key": "", "chat_model": "",
                  "thinking": false, "models": [] }   // 自定义 OpenAI 兼容端点（Ollama 等）
  },
  "llm": {
    "provider": "siliconflow",          // 生成厂商 = providers 键名（翻译 LLM + RAG 生成共用）
    "model": "",                        // 模型覆盖；留空 = 所选厂商默认 chat_model
    "temperature": 0.2,
    "timeout": 30
  },

  // ---- RAG 快捷问答（v1.7）----
  "rag": {
    "kb":     { "dir": "", "chunk_chars": 500, "chunk_overlap": 50 },
    "retrieval": {
      "fusion_variants": 3,             // ① 变体数；1 = 关闭 Fusion（省一次调用）
      "bm25_top_k": 30,                 // ② BM25 路召回数（v1.7.2 起设置页可调）
      "vector_top_k": 30,               // ② 向量路召回数
      "rrf_top_k": 20,                  // ③ RRF 融合后 TOP-20
      "rerank_top_k": 5,                // ④ 精排后 TOP-5（实测多概念/枚举类问题建议 10）
      "enable_vector": true,
      "enable_rerank": true
    }
  },

  "popup": { "theme": "dark", "alpha": 0.97, "auto_hide_ms": 0,
             "max_width": 520, "font_size": 13, "follow_cursor": true },
  "mini_button": true,                  // 拖选文字后自动弹出『译』『便』迷你按钮
  "mini_button_maxlen": 200,            // 超过此长度不弹按钮（防误抓整页）
  "ocr_lang": "auto",                   // 截图 OCR 语言；auto = 优先 en-US
  "max_chars": 3000,                    // 超长文本截断阈值
  "autostart": false,                   // 开机自启（写 HKCU Run，无需管理员）
  "enable_tray": true
}
```

> **热键顺延与持久化（v1.2 修复）**：任一热键被占用（错误码 1409）都会自动换用候选列表里的空闲组合并**写回配置**，下次启动直接用记住的组合，不再弹错误。程序自己也不会把同一组合绑给两个功能（如划词翻译用了 Ctrl+Q，设置热键就不能再用它——会自动顺延）。非核心键（设置/退出/截图）顺延全部失败时只弹一次轻提示，划词翻译键失败才弹致命提示。

---

## 七、打包成 exe 与发布

### 7.1 步骤

```bat
:: 1. 安装 Python 3.12（勾选 Add to PATH）
:: 2. 安装打包工具（只需一次）
py -3.12 -m pip install pyinstaller pypdf

:: 3. 打包（等价于双击 build.bat）
cd QuickTool
py -3.12 -m PyInstaller QuickTool.spec --noconfirm --clean

:: 4. 产物：dist\QuickTool.exe（单文件、无控制台窗口、含图标）
```

`QuickTool.spec` 关键配置：

```python
EXE(..., console=False,          # windowed，不弹黑框
        upx=False,               # 有 UPX 可改 True，再压 30%~40%
        icon="assets/icon.ico")  # 程序图标
datas=[("data", "data")]         # 打包内置词库
excludes=[...]                   # 剔除用不到的标准库/大包（pypdf 只瘦身不排净——
                                 # 不能排 xml（pypdf.xmp 强依赖）、不能排 sqlite3）
```

> 打包机需装 pypdf（或设 `PYPDF_PATH` 指向含 pypdf 的 site-packages），否则产物缺 PDF 导入功能（其余功能不受影响）。发布流程（tag + GitHub Release + 附件）见 4.14。

### 7.2 体积与性能优化

| 手段 | 效果 |
|---|---|
| 标准库实现 + excludes 剔除无用模块 | 基础包约 12.5MB；含 pypdf 后 24.2MB |
| `--onefile` | 单文件绿色分发（代价：冷启动多 ~0.9s 解压） |
| `--onedir`（替代） | 冷启动 ~0.4s，但产物是文件夹；常驻内存不变 |
| 不引入 WebView2/Electron | 内存从 150MB+ 降到 9MB 级别 |

### 7.3 实测数据（本机 Windows 11，`tests/perf.py`，v1.7.2）

| 指标 | 数值 |
|---|---|
| exe 体积 | **24.2 MB**（单文件，含 pypdf；v1.6.x 纯标准库版为 11~12.5 MB） |
| 冷启动 → 解释器就绪 | **~1.1 s**（onefile 解压） |
| 常驻内存（静置 12s，无窗口） | 工作集 **8.6 MB** / 私有提交 1.6 MB |
| 单次翻译端到端（MyMemory） | ~1.4 s |
| 截图 OCR 端到端（选区→译文） | 抓屏 ~50ms + OCR 子进程 1~3s |

> 注：`tests/perf.py` 里"全局热键可用"读的是 selftest 的 `HOTKEY=` 事件，而 selftest 由 `after(2000)` 定时器启动，故该数字 ≈ 解释器就绪 + 2s 固定定时器，不代表热键注册耗时（实际注册在后台线程毫秒级完成）。

---

## 八、方案优缺点与适用场景

### 方案 A：本工具（自研极简）

- ✅ 极轻：24MB（含 PDF 支持）/ ~9MB 内存 / ~1.1s 启动
- ✅ 零依赖（仅 pypdf 例外）、纯绿色、可放 U 盘、源码可改
- ✅ 多引擎 + 回退 + 缓存 + 离线兜底
- ✅ 剪贴板全格式还原，不打扰用户
- ✅ 截图翻译（Windows 内置 OCR，离线零依赖）
- ✅ 本地知识库 RAG 问答（sqlite FTS5 + 云端向量/重排，凭据中心多厂商切换）
- ❌ 仅 Windows
- ❌ OCR 依赖系统语言包，手写体/艺术字识别一般（对屏幕清晰文字足够）
- ❌ 界面朴素（自绘浮窗，无毛玻璃等特效）

**适用**：个人常驻工具、追求轻快、喜欢改代码的开发者；作为公司内部"翻译助手"的底座。

### 方案 B：直接用 STranslate / Pot 等成熟软件

- ✅ 功能全（OCR、多引擎 UI、插件）
- ❌ 重（.NET 60MB+ / WebView2 40MB+ 内存）
- ❌ 定制引擎行为要啃大型代码库

**适用**：不想动代码、需要 OCR 和完整功能的人。

### 方案 C：改造 SnapTranslate 这类 Python 项目

- ✅ 技术路线一致，上手快
- ❌ 剪贴板不还原、接口单一、无托盘，改造量 ≈ 重写

**适用**：想在最小改动下快速获得"能跑的版本"。

### 直接排除的方案

- **Electron**：150MB+ 体积、150MB+ 内存，与"极简轻量"直接冲突。
- **Tauri**：虽然安装包小，但常驻 WebView2 内存 40MB+，且 Rust 二次开发门槛高，对一个划词工具来说成本不划算。

---

## 九、自动化测试

| 测试 | 覆盖 | 结果 |
|---|---|---|
| `tests/smoke.py` | 不依赖 GUI 的全量冒烟：热键解析与顺延表、剪贴板快照/还原与并发保护、PDF 断词清洗、抓词链路与竞态守卫、Win32 窗口/托盘/钩子、翻译引擎与回退链、GDI 抓屏 + OCR 桥、截图对照/便签/迷你按钮/主题/多显示器/布局回归（6.10）、凭据中心与迁移幂等（6.24）、PDF 导入与批量入库（6.25/6.26，pypdf 缺失时自动 SKIP）、**模型历史记忆与设置页增强（6.27，v1.7.2）**——各版本新增断言明细见十一节版本记录 | **272/272** ✅ |
| `tests/harden_check.py` | **v1.4/v1.4.1 加固专项**：畸形剪贴板数据（无 NUL 的 CF_TEXT/CF_UNICODETEXT、奇数字节流）不越界不崩溃、正常 UTF-16 逐字读回、快照跳过 CF_BITMAP 等非 HGLOBAL 格式、带位图剪贴板安全还原、20 万字符读写、**日志轮转**（>2MB 自动切文件且主日志不膨胀）、**v1.4.1 并发剪贴板压力**（4 线程×150 次 = 600 次操作 0 异常、加锁后写读一致、测后还原剪贴板） | 14/14 ✅ |
| `tests/taskbar_rebuild_check.py` | **v1.4 TaskbarCreated 托盘重建白盒验证**：注册消息号 ≥0xC000、注入广播消息触发 `_tray_add` 重建、非该消息不误触、关闭托盘时不重建。同时抓到并修复 `sys.excepthook` 单参签名 bug（日志钩子自己先炸） | 5/5 ✅ |
| `tests/e2e.py` | 真起进程，`SendInput` 真实按键触发 → 弹窗 → 托盘消息模拟打开设置（**断言窗口 state=normal 且已映射**，防 withdrawn 不可见回归）→ 迷你按钮翻译链路 → 截图对照小窗 + **剪贴板 CF_DIB 探针（v1.5.2）** → **OCR 桥语言包探针** → 置顶便签链路（含 **v1.6.3 迷你按钮『便』累积探针 `MINI_NOTE_TOTAL=3`**）→ 干净退出 | **14/14** ✅（源码版 + 打包版双跑） |
| `tests/perf.py` | 体积 / 冷启动 / 常驻内存 / 热键退出（退出热键从配置读取，兼容顺延后的值） | 见 7.3 |
| `tests/tray_menu_full_app.py` | 诊断脚本：真 App 全链路——模拟托盘右键 → `TrackPopupMenu` 菜单弹出 → 键盘选中「设置」→ 设置窗口可见（Win32 外部确认，不跨线程调 Tk） | ✅ 手动运行 |
| `tests/drag_lag.py` | **鼠标卡顿量化**：建无文本探针窗口 + 20ms 节奏注入鼠标移动，统计 WM_MOUSEMOVE 到达间隔；先测基线再做一次"拖空选"，比对尖峰。修复前基线 22ms / 拖选后 **467ms**（需干净环境，其他 QuickTool 实例的钩子会干扰） | 手动运行 |
| `tests/lag_workers.py` | **钩子线程阻塞回归**（只针对本进程实例，不受其他实例干扰）：把抓词替换成 `sleep(0.8s)` 制造最坏情况，用跨线程 `SendMessage` 测 Win32 线程往返延迟。修复后 `CaptureWorker` + 0ms；加 `--baseline` 可跑"修复前"对照（800ms） | ✅ |
| `tests/portable_check.py` | **绿色便携性**：把 exe 单独复制到空目录（无 `qt/`、`data/`）+ 伪造空白 APPDATA 模拟新电脑首次运行 → 断言 selftest 8 个关键事件齐全、退出码 0、配置正确落到 `%APPDATA%`。实测 `PORTABLE PASS` | ✅ |
| `tests/ocr_full_app.py` | 诊断脚本：**截图翻译真 App 全链路**——纯 Win32 大字窗口渲染已知文字 → 队列触发 `ocr_select` → `SendInput` 真实拖拽框选 → GDI 抓屏 → PowerShell OCR → 进入翻译管线弹窗；同时断言框选期间迷你按钮不弹出（`ocr_selector` 守卫）。实测 OCR 精确读出 `quicktool ocr full app test` | ✅ 手动运行 |
| `tests/pin_full_app.py` | 诊断脚本：**截图对照真 App 全链路（v1.5）**——队列触发 `pin_select` → 遮罩弹出 → `SendInput` 真实拖拽框选 → GDI 抓屏 → PinWindow 置顶小窗创建（断言 topmost + 缩放倍数合法）；**v1.5.1 多窗口**：第二次框选并存 total=2 + 级联偏移位置不同 → 第 3~5 个继续创建 → **第 6 个被 PIN_MAX 上限拦截** → `pin_close` 后列表清空；**v1.5.2**：框选后断言剪贴板出现 CF_DIB（`clipboard_has_dib`）；同时断言框选期间迷你按钮不弹出（`ocr_selector` 守卫）。**v1.5.3 注意：跑前先杀干净后台旧 QuickTool 实例**（其钩子会干扰 CF_DIB 探针）。实测 `FULL_APP_PIN PASS` | ✅ 手动运行 |

> **自动化验证了托盘修复**：selftest 直接给隐藏窗口发 `WM_APP_TRAY` 消息（lParam 高 16 位 = 图标 ID、低 16 位 = `WM_LBUTTONDBLCLK`），断言设置窗口成功打开——该用例在修复前恒为 False。
>
> **右键菜单 → 设置链路**（`tray_menu_full_app.py`）：曾有一版验证在自动化沙箱里模拟点击菜单失败，疑似代码问题。实测定位为验证脚本自身缺陷（跨线程调 Tk 死锁 + 时序），改用外部 Win32 观察后全链路通过：`tray_added=True → menu_found=True → settings_visible=True → app.settings_obj=True`，`TrackPopupMenu` 正确返回 1001（设置）。

---

## 十、后续扩展方向

早期规划的落底情况：~~截图 OCR~~（v1.2 已内置 Windows.Media.Ocr）、~~多显示器适配~~（v1.6.8）、~~开机自启~~（设置勾选）、完整离线词典**机制**已就绪（源码运行时把 `data/ecdict.txt` 放入即自动加载；打包版读内置资源目录，暂不支持外挂）。

当前候选方向（按价值排序）：

1. **多概念查询分解**：RAG 检索前把"一次问多个概念"的问题拆成子问题分别召回再合并——TopK=10 只能缓解"单概念文档霸占候选池、其余概念颗粒无收"，分解才是根治。
2. **UI Automation 抓词**：把"模拟 Ctrl+C"换成 UIA `TextPattern` 优先、剪贴板方案兜底，彻底不碰剪贴板。
3. **生词本 / 翻译历史**：悬浮窗一键收藏，复习闭环。
4. **本地大模型引导**：凭据中心「自定义」端点已可接 Ollama，可加模型自动探测与一键引导配置。
5. **双模型对比评测基准**：固定一批机制题 × 双模型 × 固定检索配置，自动化量化"换模型/调参数"的真实收益（v1.7.2 期间人工评测的固化）。
6. **自动更新检查**：启动时比对 GitHub Releases 最新版本号，非阻塞提示升级。

---

## 十一、版本演进记录（踩坑大事记）

> 正文（第四节）只保留**现在仍然成立**的机制说明；按版本粒度的完整踩坑、
> 修复过程与实测数据都收在本节，改代码前建议先查对应章节。发布节奏紧凑时
> 条目顺序不完全倒序，以版本号为准。

**v1.4.1 实证：日志系统成功抓到崩溃栈并定位真凶（2026-08-31）**。v1.4 上线约 30 分钟后实例再次静默退出（无 QUIT 日志），`crash.log` 这次抓到 faulthandler 栈：

```text
Windows fatal exception: code 0xc0000374
Current thread → _hglobal_read (winapi.py) ← clipboard_snapshot (434)
              ← get_selected_text (capture.py) ← _capture_worker
```

栈直接指向 **CaptureWorker 线程读剪贴板**时堆损坏，证明 v1.4 的"有界读"只修了一半——真正的问题是**剪贴板访问缺少并发保护**：CaptureWorker（抓词）与主线程（复制按钮）同时 `OpenClipboard`，竞态下 `GetClipboardData` 返回失效句柄，`GlobalSize`/`GlobalLock` 读坏堆元数据。且对 CF_BITMAP 等**非 HGLOBAL 格式**（HBITMAP 等 GDI 句柄）调 `GlobalSize` 本身就是高危操作。v1.4.1 修复：

- 新增进程内剪贴板互斥锁 `_CLIP_LOCK`：`clipboard_snapshot` / `clipboard_restore` / `get_clipboard_text` / `set_clipboard_text` 全部串行化（锁只加在会 `OpenClipboard` 的函数上，`_hglobal_read` 等纯内存辅助不加锁，避免嵌套死锁）；
- 新增 `_CF_NON_HGLOBAL` 黑名单（CF_BITMAP/CF_PALETTE/CF_ENHMETAFILE/CF_OWNERDISPLAY）：快照时整类跳过，**绝不调 GlobalSize**；
- 验证：harden_check 新增场景三"并发剪贴板压力"（4 线程×150 次 = 600 次操作 0 异常、写读一致）14/14 ✅，smoke 40/40 ✅，e2e 10/10 ✅，taskbar_rebuild_check 5/5 ✅。

**v1.5.0 截图对照小窗**（2026-08-31）：`Ctrl+Prtsc` 框选截屏 → `PinWindow` 置顶小窗（滚轮缩放 / 整窗拖动 / Esc 关闭）。显示链路用标准库 zlib 手写 PNG 编码喂 `tk.PhotoImage`（Tk 不认 BMP，PIL 违反零依赖）。验证：smoke 51/51（新增 Prtsc 热键解析、HK_PIN 候选表、PinWindow 创建/缩放/关闭 8 项）、e2e 10/10×2（源码 + 打包版，`PIN_SHOWN=True`）。

**v1.5.1 多窗口并存**（2026-08-31）：`app.pin_win` → `app.pin_wins` 列表，最多 **5 个** 对照窗并存，各自独立关闭；「点谁谁在前」点击置前（无边框窗需显式 `lift()+focus_force()`）；新窗口初始位置级联偏移 28px×序号；超过上限提示"请先关闭一个"。验证：smoke 55/55（新增多窗口并存 / 级联偏移位置不同 / 关闭一个不影响另一个 / 全部关闭列表清空 4 项）、e2e 10/10×2（`PIN_SHOWN` 带 total 计数）、`tests/pin_full_app.py` 真 App 驱动 `FULL_APP_PIN PASS`（两次框选并存 total=2 + 级联偏移 + 清空）。

**v1.5.2 截图进剪贴板 + 保存**（2026-08-31）：与 Win+Shift+S 同逻辑——框选完成自动进剪贴板（`set_clipboard_image`：BMP 去 14B 文件头 → CF_DIB，走 `_CLIP_LOCK` 串行；失败不阻塞，小窗照常）；「存 PNG」按钮 / Ctrl+S 主动保存 1:1 原图到 `%USERPROFILE%\Pictures\QuickTool\`（时间戳命名，toast 提示路径）。验证：smoke 63/63（新增 CF_DIB 放图/快照兼容/还原文本 + 存 PNG 路径/魔数/尺寸/toast 8 项）、e2e 11/11×2（新增 `PIN_CLIP=True` 探针断言）、`pin_full_app.py` 真 App 驱动 `FULL_APP_PIN PASS`（框选后 `CLIP_DIB=True`）。

**v1.5.3 截图框选两个 bug 修复**（2026-09-01）：
- **孤儿 release 自动截屏**：`RegionSelector` 只在 `ButtonPress-1` 才设 `_sx/_sy`，`_release` 不检查是否真按下过——遮罩弹出瞬间鼠标残留按下状态（无 press 配对）松手会带 `_sx/_sy=0`，从屏幕左上角 (0,0) 到鼠标位置生成巨大"随机"选区直接截屏（日志实证：1124x110 / 968x134 两个左上角矩形，用户没动鼠标却完成截图）。修复：加 `_pressed` 标志，孤儿 release 直接忽略。
- **选区完成瞬间钩子误判拖拽为划词**：框选 LEFTUP 后 `close()` 先把 `ocr_selector` 置 None、`pin_wins` 尚未 append，钩子的 `WM_APP_DRAG_END` 到达时守卫双双放行 → `get_selected_text` 快照/还原剪贴板，把刚写入的 CF_DIB 覆盖成旧内容（用户截图后 Ctrl+V 粘到旧文本）。修复：加 `App._selecting` 选区流程标志（`open_pin`/`open_ocr` 置位，完成回调 `finally` 清除，取消路径 `close()` 清除），`_handle_drag_end` 第一道守卫拦住。
- 验证：smoke **68/68**（新增孤儿 release 不触发完成 / 正常框选触发 / <MIN_SIZE 取消 / 选区流程中钩子不抢拖拽 / 结束后正常抓词 5 项）、e2e 11/11×2、`pin_full_app.py` `FULL_APP_PIN PASS`×2（CLIP_DIB 稳定 0.0s）。**注意**：跑真 App 驱动测试前须确认没有旧 QuickTool 实例在后台（其 WH_MOUSE_LL 钩子会把注入的拖拽当划词、快照还原剪贴板，导致 CF_DIB 探针误报）——`tasklist | findstr QuickTool` 先杀干净。

**v1.5.4 抓词还原竞态修复——普通 PrtSc 截屏不再被旧图覆盖**（2026-09-01）：
- **症状**：先 `Ctrl+Prtsc` 截图对照（CF_DIB 进剪贴板），再按普通 PrtSc 系统截屏，Win+V / 直接 Ctrl+V 粘出来的是**上一次 Ctrl+Prtsc 的旧图**，退出软件后正常。
- **根因**：拖选（≥6px）会触发抓词，`get_selected_text` 流程为「快照剪贴板（含上次 Ctrl+Prtsc 的 CF_DIB）→ 模拟 Ctrl+C → 轮询 → **无条件 `clipboard_restore(snapshot)`**」。抓词失败时轮询窗口长达 ~0.76s（0.25s + 0.06s + 0.45s），用户在此期间按普通 PrtSc，Windows 刚把新截屏写进剪贴板，restore 的 `EmptyClipboard()` 就把新截屏冲掉、再写回旧快照（日志实证：13:34~13:45 大量 `len=0` 失败抓词，每次 ~0.75s 窗口）。
- **修复**：还原前校验剪贴板序列号——`seq` 变了但读不到文本（截屏是 CF_DIB 位图）＝外部写入，`seq_last` 保持初始值，还原守卫因序列号不匹配**放弃还原**；只有真正读到文本（我们的 Ctrl+C 复制生效）才更新 `seq_last` 并放行还原。
- 验证：smoke **70/70**（新增「抓词期间外部截屏写入不被旧快照冲掉」+「无外部写入时还原仍正常」2 项）、e2e 11/11×2。

**v1.6.0 项目更名 QuickTrans → QuickTool + 置顶便签**（2026-09-01）：
- **更名**：源码/文档/打包 spec/运行时路径（`%APPDATA%\QuickTool`、`Pictures\QuickTool`、日志文件名）全量替换，GitHub 仓库名 `QuickTool` 对齐。旧 `QuickTrans` 目录保留作回滚备份，历史日志不改写。
- **置顶便签（新功能）**：见 4.12 节。选中文字 → `Ctrl+Alt+N` → 钉到置顶便签窗，单窗口累积、可直接编辑、支持便签内全文搜索。
- 验证：smoke **93/93**（新增便签 22 项）、e2e 13/13×2（新增 `NOTE_SHOWN`/`NOTE_TOTAL` 探针）。

**v1.6.1 设置窗口布局错乱修复**（2026-09-01）：
- **症状**：设置窗口热键区只能看到「划词翻译」「截图翻译」两行且全部错位、右半截断，其余 4 行（截图对照/置顶便签/打开设置/退出程序）不可见。
- **根因**：`_row()` 里行 Frame `r` 只包住了 Label，而 Entry/Combobox 的 master 是 LabelFrame，pack 时直接 `pack(side="left")` 到了 LabelFrame —— 行（top）与控件（left）**交错 pack**，pack 空洞逐行右移：每行被上一行的 Entry 推右一个 Entry 宽度（~253px），整个内容区自然宽被撑到 ~2000px，超宽部分被 Canvas 裁掉。该 bug 从 v1.5.3 前就埋着，v1.6.0 加第 4 行后愈发明显才被注意到。
- **修复**：① `widget.pack(in_=r, ...)` 用 Tk 的 `in_` 机制把控件几何挂到行 Frame（master 仍可为 LabelFrame）；② Canvas 加 `itemconfigure(inner_id, width=e.width)` 内容宽度跟随窗口；③ 默认窗口 560→680（「失败回退」行 5 个复选框自然宽 ~620px）。
- 验证：真实构建 Settings 截屏比对（6 行热键整齐、失败回退行完整）+ smoke **97/97**（新增 6.10 节布局回归：6 个输入框同列 x=150、逐行 y 递增、inner 自然宽 <800）。

**v1.6.2 置顶便签窗口可缩放 + 大小/位置记忆**（2026-09-01）：
- **问题**：便签窗口 470×360 固定、无边框不可调整大小，长文本挤在小窗里。
- **实现**：默认尺寸加大到 520×420；右下角 `◢` 手柄拖拽缩放（无边框窗自补缩放能力，最小 320×240 / 最大 2400×1600 夹紧，`_rs_move` 同步逻辑尺寸）；关窗时 `note_w/h/x/y` 写入配置，下次打开原样恢复大小和位置。
- **连带修复**：`winfo_width()` 在窗口未布局完时返回 1，「打开即关」会把记忆尺寸错误写成最小值（越关越小）——保存时读数低于 MIN 就回退逻辑尺寸。
- 验证：smoke **104/104**（新增记忆保存/拖拽放大/最小值夹紧/恢复 5 项）、e2e 13/13×2。

**v1.6.3 迷你按钮加『便』：拖选文字一键进便签**（2026-09-02）：
- **需求**：拖选后弹出的迷你按钮只有『译』，想把这段文字钉进便签还得再按一次 `Ctrl+Alt+N`（等于重新抓一遍词）。
- **实现**：按钮条从单个 26px 圆扩成『译』+『便』并排（26×2+2=54px）；点『便』走 `mini_note()`，直接把拖选时**已经抓好的文字**交给 `open_note()`——**省掉一次「模拟 Ctrl+C + 等剪贴板」**，终点与按热键完全一致（同一个便签窗累积）。
- **来源窗口**：`_handle_drag_end` 在拖选结束瞬间记下前台窗口标题（与热键路径一致的做法），仅用于便签段头的来源标注。
- **细节**：`<Motion>` 按落点 x 分流高亮（`_index_at` 步长 = `SIZE+GAP`）；鼠标 `<Enter>` 即取消 2.2s 自动隐藏——按钮变宽后移到右侧『便』更花时间，否则常常还没点到就消失了；钩子忽略区 `get_rect()` 同步覆盖整条 54px，避免点『便』被当成新一次拖选的起点。
- 验证：smoke **117/117**（新增 6.11 节 13 项：宽度/两圆不重叠/落点索引映射/忽略区覆盖/悬停只高亮当前圆/点『便』走 mini_note/点『译』走 mini_translate/无事件对象默认翻译/进入取消计时）、e2e 14/14×2（新增 `MINI_NOTE_TOTAL=3` 探针，验证『便』能正确累积到第 3 段）。

**v1.6.4 修复打包版偶发崩溃**（2026-09-02）：
- 根因：`qt/ui.py` 全部事件回调写成 `lambda e:`（无默认值），浮窗/弹窗 widget 销毁竞态瞬间 Tk 可能无参调用回调 → `TypeError` 打断主循环 → 进程约 0.16s 退出。
- 修复：全部 `lambda e:` → `lambda e=None:`（共 20 处）。验证：smoke **121/121**、e2e 14/14×2。

**v1.6.5 便签「回搜」改为便签内全文「搜索」**（2026-09-02）：
- **需求**：用户反馈「回搜」没用——它实际是切回来源应用让原应用自己搜，既不搜便签内、又依赖触发时记录到的来源窗口句柄（来源窗口关了就失效）。
- **改动**：把便签工具条右侧的「回搜」按钮换成「搜索」：点击在顶部弹出输入框，实时在便签**全文**高亮全部命中（蓝底）、当前命中金色突出并滚动到位，Enter / Shift+Enter 在命中间前后跳转，Esc 关闭。删除了 `last_hwnd` 链路与 `_back_search` / `_keyword_at_cursor` / `_clip60`。
- 验证：smoke 6.9 节「回搜关键词」用例改为「内搜索」用例；smoke **126/126**、e2e 14/14×2。

**v1.6.6 修复：终端里拖选/系统截图工具框选会误发 Ctrl+C 中断程序**（2026-09-02）：
- **症状**：① 在 cmd 里拖动选择文字会中断正在运行的程序（Ctrl+C 被控制台当『中断信号』而非『复制』）、或 shell 多换一行；② 按裸 PrtSc 进系统截图工具后拖动框选，截完焦点回到 cmd 时同样多一行；Ctrl+PrtSc（QuickTool 自家截图对照）不受影响。关掉 QuickTool 两者都消失。
- **根因**：`MouseDragWatcher`（WH_MOUSE_LL）拖选判定只看位移、不看窗口类型，抓词又统一靠模拟 Ctrl+C——在控制台里（无选区时）Ctrl+C = 中断程序；在截图遮罩里框选也会被误判成"拖选文字"，松手后盲发 Ctrl+C。
- **修复**：拖选结束先用**按下点所在窗口**判定再决定是否抓词——`is_console_window`（conhost/Windows Terminal/mintty 类名+进程名）与 `is_overlay_window`（topmost+盖满屏的截图遮罩特征）命中即跳过并记 `DRAG-SKIP reason=console|overlay`。正常浏览器/编辑器拖选完全不受影响。热键 Ctrl+Q/Ctrl+Alt+N 路径本次未动。
- 验证：smoke **140/140**（新增 6.13 节 11 项：控制台类名/进程名判定、遮罩几何判定、_handle_drag_end 集成不触发抓词）、e2e 14/14×2。

**v1.7.2 生成模型切换增强：模型历史记忆 + 检索参数 UI + 引擎热重建**（2026-09-06）：
- **短背景豁免（v1.7.2 补）**：检索 query 原来只用问题——用户常问"是什么？还有什么屏障？"这类**不含概念词**的问题，而概念恰恰在选中背景的头部，实测导致检索全灭（召回的全是"屏障/barrier"撞词的无关文档）、模型只能靠先验裸考。改为 `query = 问题 + 背景前 100 字`（`_retr_query`，作用域=变体生成/双路召回/精排），完整背景仍只注入生成侧——100 字上限保证长背景不稀释 BM25/向量命中，排序由 rerank 兜底。
- **模型历史记忆（换过一次就记住）**：`providers.<厂商>.models` 列表（去重、最近在前、上限 8，`remember_model` 维护）——设置页保存时自动把生效模型（覆盖值，留空则厂商默认）记入该厂商历史；旧配置无 models 键零迁移，`models_for` 读取时用 chat_model 兜底播种。魔搭默认 chat_model 顺带更新为 `Qwen/Qwen3.8-Flash-Next`。
- **「模型覆盖」Entry → 「生成模型」可编辑 Combobox**：下拉列出当前厂商的历史模型，也可手输新模型；**切厂商（`<<ComboboxSelected>>`）下拉实时跟随新厂商历史**，跨厂商残留的模型名自动回退为空（=新厂商默认）——此前换厂商要手打模型全名、换过的组合不留记录，两步并一步。
- **RAG 检索参数进设置页**：`_section_rag` 新增「检索变体数」（1~5，1=关 Fusion 省一次调用）、「每路召回TopK」（10~100，同时写 bm25/vector 两路）、「精排 TopK」（1~30）三行，非法输入 `_clamp_int` 夹紧回默认。多概念问题加大召回不再要改配置文件。
- **切模型免重启（v1.7.0 遗留 bug 修）**：RagEngine 首问时把 chat/embed/rerank 端点固化进 RagApi 单例，`Settings._save` 从不重置——表现为"设置里切了厂商，问答仍发旧厂商"（实测：魔搭零消耗、硅基流动账单出现 Qwen3-8B）。修复：`_save` 落盘后调 `app.invalidate_rag_engine()` 丢弃单例，下一条问答按新配置重建；正在流式输出的旧问答窗自然跑完。`RagApi.__init__` 增加 `RAG-API chat=…@… retr=… embed=… rerank=…` 端点留痕日志，账单对不上一眼可查。
- **小修**：凭据中心「自定义（OpenAI 兼容） Key」行标签超列宽被截断 → 自定义行用短标签「自定义 Key」；smoke 第 6 节引擎测试钉死 `source_lang/target_lang`（真实 config 的 `target_lang=en` 会让 MyMemory 收到 `langpair=en|en` 报 403，引擎可用性不该随用户配置漂移）。
- **滚轮防误改下拉框（v1.7.2 补）**：Windows 的 TCombobox 类绑定把悬停滚轮直接变成「改选中项」——滚设置页时鼠标扫过『生成服务商』，智谱就被滚成了 DeepSeek（真实报障）。`_tame_combo_wheel` 在建窗后给**全部** Combobox 加控件级 `<MouseWheel>` 绑定：转发 `delta` 给页面滚动后 `return "break"` 拦掉类绑定——悬停在下拉框上滚轮照样滚页面，但值不再变；下拉列表展开后的滚轮由 popdown 自己的列表处理，不受影响。
- **上下文预算 6000→8000（v1.7.2 补）**：配合精排 TopK=10（10 块 ×~500 字符 ≈5000，旧预算会截掉后几块）。实测 TopK=10 + 预算 8000 后，此前检索失利的 Q3 锁升级 / Q5 GC Roots 两题全面翻盘（Flash-Next 72→92、70→91；Qwen3-8B 90→92、90→94），枚举类来源（锁升级全链、GC Roots 六类）稳定进入上下文。另：6.10 布局回归的 z-order 命中测试被其他应用前台窗口干扰（用户操作机器时偶发 hit=None），改为置顶后再测。
- **Office 拖选出 c 修复（v1.7.2 补）**：`send_ctrl_c` 曾把 Ctrl↓/C↓/C↑/Ctrl↑ 四个事件塞进**同一个 SendInput 批次零间隔注入**——Word 的输入管线异步、主线程繁忙，会在确认修饰键状态之前就把 C 当普通字符提交，表现为「拖选后所选文字被清除、原地多一个 c」（选区被裸字符键入覆盖；浏览器/记事本同步管线无此问题）。改为分步注入 + 键间留间隔（Ctrl↓ → 25ms → C↓↑ → 20ms → Ctrl↑），`release_modifiers` 与 `send_ctrl_c` 之间也补 20ms——多出的 ~45ms 落在抓词等剪贴板的窗口里无感，全部跑在 CaptureWorker 线程不碰钩子线程。真机验证：Word 探针文档真实拖选 → `CAPTURE-DONE len=43` 抓词成功、文字完好无 c。
- 验证：smoke **272/272**（6.24 新增 3 项：保存后引擎重建/模型历史落盘/检索参数直通；6.27 新增 12 项：播种/去重置顶/封顶/非法忽略/持久化/下拉跟随/残留回退/全 Combobox 拦轮+滚动转发）、e2e **14/14**（源码 + 打包）。

**v1.7.1 fence-aware 切分 + 上下文防污染 + 批量导入**（2026-09）：
- **代码块原子化（切分根因修）**：针对"讲解算法+参考代码"类 md 实测（rag-corpus-clean 1552 文件 / 13728 代码块：84% ≤500 已安全、16% 被按标点腰斩且中间片丢失 ``` 标记）——fence 块独立成原子单元，**≤3000 字符（实测 p99=3045 覆盖全部教学代码）永不切**，放不下就独占 chunk，命中即完整代码；**>3000 整块跳过不入库**（实测该尺寸几乎全是 Coze/Dify 数据 blob：单行 3 万字符的 JSON 测试样例，检索命中不了、命中了也没用）。fence 与前后散文强制 flush 分离，代码块不再被拖进散文段落。
- **生成上下文防污染（小模型 8B 级实测痛点）**：预算 4000→6000（3000 字符代码块命中不再挤掉全部来源）；**同节去重**——同文档同标题链的命中只留最高分一块，防单章节连续切片霸榜；**代码配额=1**——含 fence 的块最多进 1 块，防 Qwen3-8B 级模型面对大段代码的注意力稀释/复读倾向；系统提示词加"代码是参考实现，回答以原理讲解为主"。
- **批量导入**：文档库「导入文件夹…」递归扫描 .txt/.md/.pdf（隐藏目录/文件跳过、扩展名大小写不敏感）；「导入文件…」对话框本支持多选。批量 PDF 信号量限流并发提取 2 个，聚合进度「PDF x/y：」，跨批到达总数自动累加。
- **语料清洗方法论**（rag-corpus-clean 实测沉淀）：清洗标准是**相关性**（按目录清单排：demo 数据集 all-in-rag/data 324 个 md 占全语料 21%、exercises 参考答案、案例源码导出）而非"代码占比"（CS-Notes 设计模式 96% 代码恰是理论资料本体，不能一刀切）。
- **导入即预向量化（v1.7.1 补）**：批量导入暴露懒向量化短板——首问 gate 要补算全部缺失向量（1200 文档 ≈ 数万 chunk、十几分钟无进度，像卡死）。改为：①文档库导入完成（txt/md 与 PDF 收尾）即自动后台线程补算，UI 实时"后台向量化 d/t"，未配 Key 静默跳过（ask 时 gate 兜底）；②`embed_chunks_missing` 加 `on_progress` 回调，ask 兜底路径同样显示"向量化文档 d/t"；③`api.embed`/`rerank` 遇 429/5xx/网络错误指数退避重试（0.8s 起 ×2，最多 3 次），批量场景不再一碰限流整批失败。
- 验证：smoke **271/271**（6.20 新增 fence 原子化 5 项：短块完整/超限跳过/独占不切/fence 内 # 不当标题；6.21 新增 5 项：进度回调/错误码解析/429 重试/401 立即抛；6.22 新增防污染 5 项：预算/同节去重/代码配额；6.26 新增预向量化接线 2 项 + 批量导入 7 项）、e2e 14/14。

**v1.7.0 RAG 快捷问答全链 + 凭据中心**（2026-09）：
- **C 弹框问答**：选中文字按 `Ctrl+Alt+Y`（被占自动顺延）→ 置顶问答窗，输入问题回车才发送，选中内容作为「背景」随请求注入（只进生成、不进检索——fusion 变体/rerank 的 query 恒为用户问题）；发送后输入框清空、弹窗自动抢输入焦点。可用 `Ctrl+Alt+N` 钉便签同款交互复用（单窗口累积、缩放、位置记忆一致）。
- **B DeepL 移除**：DeepL 官方接口国内不可达，翻译引擎收敛为 MyMemory / GoogleGTX / 大模型 / 离线词库四家。
- **A 凭据中心（多厂商生成切换）**：设置页「凭据中心 · 大模型」统一管理五家 API Key——硅基流动 / 魔搭 ModelScope / 智谱 GLM / DeepSeek / 自定义（OpenAI 兼容）；生成服务商下拉切换，翻译 LLM 引擎与 RAG 回答生成共用所选厂商（`llm.provider`），可填模型覆盖（`llm.model`）或留空用厂商默认；向量/重排检索固定硅基流动。密钥字段 save/load 全程 DPAPI 加密（仅本机本账户可解，落盘无明文）。test1 阶段旧配置（`rag.api.*` / `llm.{base_url,api_key,model}` 直填）启动一次性迁移到新结构（幂等，可重复载入不抖动）。RAG 检索纯本地（sqlite FTS5 + bge-m3 向量，零第三方依赖），引擎五阶段：RAG-Fusion → BM25+向量双路 → RRF → Rerank → 流式生成。
- 验证：smoke **238/238**（新增 6.24 凭据中心：迁移幂等 / 端点矩阵 / Settings 保存落盘；6.19/6.21 随新结构改造）、e2e 14/14。

- **PDF 文档库导入（v1.7）**：文档库支持 `.pdf`（引入本项目**唯一第三方依赖例外 pypdf**，纯 Python 零原生扩展——中文 PDF 的 CID 字体必须解 ToUnicode 才能还原中文，实测零依赖手写解流对中文 100% 乱码）。PDF 走后台线程逐页提取（进度实时显示），拼成带「第 N 页」标题链的 markdown 入库（检索来源可定位到页），导入完成做**质量体检**：扫描版（无文字层）、CID 乱码（错映射成泰文/老挝文等，靠异常脚本占比检测，U+FFFD 探不出来）、图片页偏多（每页均字数过低）三类问题当场提示。pypdf 可选依赖经三方实测（源码/全量打包/瘦身打包，6 样本提取 SHA1 逐字节一致）不参与 extract_text，spec 已 excludes 瘦身（**不能排 xml**——pypdf.xmp 强依赖；**不能排 sqlite3**——知识库在用），exe 12.5MB → 24MB。打包机需 `py -3.12 -m pip install pypdf` 或设 `PYPDF_PATH` 指向含 pypdf 的 site-packages。
- 验证：smoke **249/249**（新增 6.25：提取/进度/标题链/体检四态/KbManager 后台全链路；pypdf 缺失时经 `PYPDF_PATH` 兜底、再缺失 SKIP）、e2e 14/14。
- **批量导入（v1.7.0）**：文档库新增「导入文件夹…」——选一个文件夹**递归扫描**全部 .txt/.md/.pdf 走同一入库管线（隐藏目录/隐藏文件跳过、扩展名大小写不敏感、结果排序稳定）；「导入文件…」对话框本身支持 Ctrl/Shift 多选。批量 PDF 用**信号量限流并发提取 2 个**（防一次几十个 PDF 时 N 线程同时跑 CPU 密集解压），进度条带聚合前缀「`PDF x/y：`」，跨批到达时总数自动累加。验证：smoke **256/256**（新增 6.26 节 7 项：scan_folder 递归/隐藏跳过、worker 限流异常路径不卡死不泄漏信号量、聚合进度前缀、跨批计数累加 + 全量入库）。

**v1.6.10 三项 P2 技术债清理（P2-1~P2-3）**（2026-09-03）：
- **P2-1 截图 OCR 临时文件竞态修复（唯一有真实 bug 的一项）**：截图翻译每次选区改写唯一临时文件（`tempfile.mkstemp` 前缀 `qt_ocr_`，取代固定名 `QuickTool_ocr.bmp`）——此前快速连发两次截图（第一次的 OCR 任务尚未开跑、第二次已覆盖写同一路径）会让第一次读到第二次的图、重复出结果；OCR 任务结束按各自路径删除临时文件（含 no_lang 早退路径走 finally），不再残留 temp。
- **P2-2 死代码清理**：删除回搜功能（v1.6.5 移除）遗留的 `send_ctrl_f` / `send_ctrl_v` / `set_foreground` / `quit_message_loop` 四个零引用函数（-35 行）；`get_foreground_window` 是活代码（来源窗口记录，main.py 3 处调用）保留。
- **P2-3 winapi.py argtypes 声明去重**：9 组共 13 行重复声明（`SetForegroundWindow`×3、剪贴板族/`VkKeyScanW`/`GetWindowLongW`/`SetWindowLongW`/`DestroyMenu` 各×2——同一 API 在"顶部集中区"与"函数使用处"各声明一次）收敛为**每 API 恰一次**，文件头补声明约定注释；值与原完全一致，零行为变化。
- 验证：smoke **167/167**（新增 6.17 死代码守护 3 项 + 6.18 argtypes 唯一性守护 3 项，均为源码级防回退断言）、e2e 14/14×2。

**v1.6.9 主题硬编码清理（审查清单 ⑥）**（2026-09-03）：
- **症状**：设置里把主题切成 light，只有翻译气泡跟着变——截图对照小窗、置顶便签、设置窗口本身全是写死的颜色（前两者写死 `THEMES["dark"]`，设置窗口整窗写死浅色 `#f5f7fa`），切主题对它们毫无影响。
- **修复**：`ui.py` 新增统一取色入口 `current_theme(app)`（读 `popup.theme`；配置缺失/主题名非法/取色异常一律回退 dark 且绝不抛异常——主题只该影响外观，不能让窗口开不出来）。`PinWindow`/`NoteWindow`/`RegionSelector`/`Settings`/`Popup`/`MiniButton` 全部改经它取色，源码里不再允许真实出现 `THEMES["dark"]`（新增 smoke 守护断言防回退）。主题表补 4 键：`win`（常规窗口底色）、`canvas`（图像查看区）、`on_mask`（遮罩文字）、`on_accent`（accent 底色上的文字）。
- **两个不能一刀切的地方**：① 截屏遮罩恒为半透明黑（要压暗屏幕内容），提示文字只能用浅色 `on_mask`，跟 `fg` 走则 light 主题下深色字直接看不见；② 便签搜索高亮是 accent 底色，dark 的亮蓝配深字、light 的中蓝必须配白字，文字色必须进主题表（`on_accent`）。
- **改主题立刻生效**：保存时用字典对象 `is` 判断主题是否变化，变了就地重刷——ttk 控件重配 `ttk.Style` 即全体跟随（Windows 默认主题不接受自定义背景，故切 `clam`，代价是控件变扁平跨平台风格）；原生 tk widget 无 style 机制，按「旧主题色→新主题色」映射递归重刷。已开着的便签/截图对照窗同步调 `refresh_theme()`，无需关掉重开。
- 验证：smoke **160/160**（新增 6.16 节 11 项：dark/light 键集合一致 / 按配置取到对应主题 / 非法名、无 cfg、cfg 抛异常均回退 dark 不崩 / `on_mask` 两主题亮度均 >128 / 三窗口换肤入口存在 / **源码守护：ui.py 不得再出现真实 `THEMES["dark"]` 引用**）。

**v1.6.8 多显示器适配（审查清单 ⑤）**（2026-09-03）：
- **症状**：① 截图翻译/截图对照的选区遮罩只盖主屏——副屏上按 `Ctrl+Alt+A` / `Ctrl+Prtsc` 根本框不到；② 副屏上拖选文字后，『译便』按钮/翻译气泡/对照小窗会被"夹回"主屏（`get_work_area()` 只返回主屏工作区），离鼠标十万八千里；③ 便签在副屏关掉后重开被拽回主屏。
- **修复**：`winapi.py` 新增 `get_virtual_screen()`（SM_X/Y/CX/CYVIRTUALSCREEN 外接矩形）与 `get_work_area_at(x,y)`（`MonitorFromPoint` + `GetMonitorInfoW` 取点所在显示器工作区，取不到退回主屏）。`RegionSelector` 遮罩改盖**整个虚拟屏**并把回调坐标平移 `(vx,vy)` 成虚拟屏坐标（消费端 `grab_screen_bmp` 的 BitBlt 本来就是虚拟屏语义，零改动）；`MiniButton`/`Popup`/`PinWindow` 落点与初始缩放改按**鼠标所在屏**工作区夹紧；`NoteWindow` 记忆恢复按记忆点所在屏夹紧（记忆落在已拔出显示器时自动夹进最近在用屏）。
- **关键坑（实测）**：Tk `geometry` 的 x/y 段 `-1920`（无 `+` 前缀）是**『距右缘偏移』**语义（实测 `800x600-800+0` → `winfo_x=107`），只有 `+-1920` 符号连写才是绝对坐标（实测 `800x600+-800+0` → `winfo_x=-800`）。副屏居左时虚拟原点为负，遮罩定位必须用后者。
- 验证：smoke **149/149**（新增 6.15 节 3 项：单显示器下虚拟屏==主屏 / 远偏离屏落点夹回主屏工作区 / 光标所在屏工作区正常返回）。⚠️ 本机单显示器，双屏真机（含副屏居左布局）留待实机验证；`.git` 恢复后历史暂以浅根 `086d624` 展示，`git fetch` 通路恢复即补全。

**v1.6.7 跨线程状态审计 + Tk 回调健壮性**（2026-09-03）：
- **跨线程共享状态改造（③ 队列化）**：迷你按钮待译文本、截图 OCR 图片路径、便签来源标题不再经共享属性 + Win32 消息中转，一律随队列 payload 直达后台线程（消除 `_mini_pending`/`_ocr_img` 及拖选路径对 `_last_source` 的跨线程读写）；OCR 语言包缓存加锁防并发重复探测；退出流程加尾部守卫（`_shutting_down`，退出的瞬间排队中的拖选/热键消息不再触发多余抓词）。行为零变化，纯消除数据竞争。
- **Tk 回调健壮性（审查修复）**：NoteWindow 工具条 `lambda e, c=cmd` 补默认参数（P0①，与 v1.6.4 崩溃同构的唯一残留）；Settings 滚轮从 `bind_all` 改为绑定窗口自身（②，修复关设置后滚轮事件泄漏到其他窗口刷 `UNCAUGHT-TK`）；方法型回调统一 `event=None` 防御销毁竞态（P1④(+⑫)）。
- 验证：smoke **146/146**（新增 6.14 节 6 项：跨线程状态走队列接线）、e2e 14/14×2。

---

*QuickTool v1.7.2 · 运行时零第三方依赖 · 底层能力基于 Win32 API（RegisterHotKey / 剪贴板 / Shell_NotifyIcon）*
