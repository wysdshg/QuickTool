# QuickTool · Windows 效率工具箱

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Release](https://img.shields.io/github/v/release/wysdshg/QuickTool)
![Tests](https://img.shields.io/badge/tests-285%20smoke%20%2B%2014%20e2e-brightgreen)

划词翻译 / 截图 OCR / 截图对照 / 置顶便签 / **本地 RAG 快捷问答**——常驻托盘，绿色免安装。

| 关键指标 | 数值 |
|---|---|
| 体积 / 内存 / 冷启动 | 24MB 单文件 exe / ~9MB 常驻（工作集 8.6MB 实测）/ ~1.1s（onefile 解压） |
| 运行时依赖 | **0**（全标准库；唯一例外 pypdf，只服务 PDF 文档库导入） |
| 自动化测试 | **285 项冒烟 + 14 项端到端**（真起进程、真实按键、真 App 全链路驱动） |
| 发布 | 已迭代发布 14 个版本（GitHub Releases 同步 exe 产物） |

---

## 目录

1. [项目亮点](#项目亮点)
2. [RAG 快捷问答（v1.7）：五阶段检索流水线](#rag-快捷问答v17五阶段检索流水线)
3. [功能与快捷键](#功能与快捷键)
4. [快速开始](#快速开始)
5. [架构与技术选型](#架构与技术选型)
6. [核心实现精选](#核心实现精选)
7. [项目结构](#项目结构)
8. [开发者文档（docs/）](#开发者文档docs)

---

## 项目亮点

- **完整 RAG 五阶段流水线，运行时零第三方依赖**：查询变体（RAG-Fusion）→ BM25（SQLite FTS5 trigram）+ 向量（bge-m3）双路召回 → RRF 融合 → Rerank（bge-reranker-v2-m3）精排 → SSE 流式生成。除云端 embedding/rerank 走 HTTP 接口外全部基于 Python 标准库（sqlite3 / urllib）——clone 下来就能跑，不装任何包。
- **面向 8B 级小模型的实战调优闭环**：语料 fence 原子化切分、上下文防污染、检索 query 拼接背景、上下文预算与精排 TopK 联动——实测把两道检索失利的评测题从 **72 → 92**、**70 → 91** 分拉回（同一模型、同一语料，只改检索侧）。
- **纯 ctypes 手写 Win32**：全局热键（RegisterHotKey + 冲突自动顺延持久化）、剪贴板全格式快照/还原与进程内互斥、Shell_NotifyIcon 托盘、WH_MOUSE_LL 低级鼠标钩子、DPAPI 凭据加密——不引任何原生扩展。
- **自动化测试矩阵**：285 项冒烟（含源码级防回退守护断言）+ 14 项端到端 + 并发剪贴板压力（4 线程 × 150 次 = 600 次操作 0 异常）+ 真 App 驱动诊断脚本（SendInput 真实拖选 → GDI 抓屏 → OCR/截图对照全链路）。
- **工程克制**：单文件 24MB、~1.1s 冷启动、绿色便携（config.json 随程序走可放 U 盘）、断网也能查词（内置离线词库）。

---

## RAG 快捷问答（v1.7）：五阶段检索流水线

> 本项目技术含量最高的部分：在**没有任何第三方库**的前提下，把一套多路召回 + 精排的 RAG 检索引擎做进了常驻托盘的小工具，并针对 8B 级小模型完成了一轮实测调优。

**用法**：选中术语/问题 → `Ctrl+Alt+Q` 弹文字动作菜单按 `3`（或托盘「快捷提问」）→ 置顶问答窗（多窗并存、流式逐字渲染）→ 回车提问，回答附来源章节。

### 流水线总览

| 阶段 | 实现 | 解决什么问题 |
|---|---|---|
| ① 查询变体（RAG-Fusion） | LLM 生成 1~5 路改写（设置页可调），失败降级仅用原问题 | 「一次问多个概念」拆成子问题分别召回，避免单概念文档霸占候选池 |
| ② 双路召回 | BM25（SQLite FTS5 trigram + 中文 4 字滑窗）＋ 向量（bge-m3，1024 维） | 关键词与语义互补；未配云端 Key 时自动降级纯 BM25，问答链路不断 |
| ③ RRF 融合 | 1/(k+rank) 合并两路排序 | 无需调权重的鲁棒融合 |
| ④ Rerank 精排 | bge-reranker-v2-m3，候选 = top_k × 3 | 解决「余弦分数过密无区分」导致的排序失真 |
| ⑤ 流式生成 | SSE 逐字输出，`enable_thinking=false` | 关闭思考链，响应提速约 4 倍 |

### 针对 8B 级小模型的调优（全部真机实测）

1. **语料切分：代码块原子化**——fence 代码块 ≤3000 字符永不切（实测 1552 文件 / 13728 代码块，p99 = 3045 覆盖全部教学代码），超限整块跳过不入库；按标点腰斩会让中间片段丢失 ``` 标记，检索命中也答不出来。
2. **上下文防污染**——同文档同标题链的命中只留最高分一块（防单章节连续切片霸榜）；含代码的块最多进 1 块（防小模型注意力稀释与复读）；系统提示词明确「代码是参考实现，回答以原理讲解为主」。
3. **检索 query 拼背景**——`query = 问题 + 选中背景前 100 字`。用户常问「还有什么屏障？」这类不含概念词的问题，纯问题检索会全灭；100 字上限防长背景稀释 BM25/向量命中，排序交给 rerank 兜底。
4. **预算与召回联动**——上下文预算 4000 → 8000，配合精排 TopK = 10（10 块 × ~500 字符）；旧预算会截掉后几块，实测两道检索失利题（锁升级 / GC Roots）因此全面翻盘。
5. **导入即预向量化**——批量导入完成立即后台补算向量（否则首问触发懒补算，1200 文档要十几分钟、像卡死）；API 429/5xx 指数退避重试，批量场景不再一碰限流整批失败。
6. **检索参数进设置页**——变体数 / 每路 TopK / 精排 TopK 均可在界面调整，多概念问题加大召回不需要改配置文件。

### 凭据中心与模型热切换

- 五厂商统一管理（硅基流动 / 魔搭 ModelScope / 智谱 GLM / DeepSeek / 自定义 OpenAI 兼容），密钥全程 **DPAPI 加密**落盘（仅本机本账户可解）；
- **切换厂商 / 模型免重启**（RagApi 单例热重建），换过的模型组合自动记忆、下拉直选；
- 文档库批量导入 `.txt` / `.md` / `.pdf`：PDF 走 pypdf 后台线程逐页提取 + 质量体检（扫描版 / CID 乱码 / 图片页占比，问题当场提示）。

> 完整调优过程与验证数据：[docs/09-版本演进与踩坑大事记.md](docs/09-版本演进与踩坑大事记.md)（v1.7.0 ~ v1.7.3 条目）

---

## 功能与快捷键

| 功能 | 入口 | 说明 |
|---|---|---|
| 划词翻译 | `Ctrl+Q` 或拖选后点「译」 | 被占用时自动顺延到空闲组合并持久化；结果悬浮窗显示 |
| 文字动作菜单 | 拖选/双击选词 → 「译 / 便 / 问」三圆按钮，或 `Ctrl+Alt+Q` → `1/2/3` | 数字键直达、免二次抓词；按钮色键透明、缝隙点击穿透 |
| 截图动作菜单 | `Ctrl+Alt+W` → `1 对照 / 2 OCR` | 先框选看到截图，再选用途 |
| 截图 OCR 翻译 | 截图动作菜单 → `2`，或托盘菜单 | Windows 内置 OCR，完全离线无 Key；识别 → 清洗 → 统一翻译管线 |
| 截图对照小窗 | 截图动作菜单 → `1` | 置顶小窗钉在屏幕角落，滚轮缩放、最多 5 窗并存、级联偏移；截图自动进剪贴板（CF_DIB），「存 PNG」保存 1:1 原图 |
| 置顶便签 | 拖选后点「便」，或 `Ctrl+Alt+Q` → `2` | 单窗口累积、可编辑、全文搜索、大小/位置记忆、超 5 万字自动裁剪 |
| RAG 快捷问答 | 拖选后点「问」，或 `Ctrl+Alt+Q` → `3` | 选中内容作背景，本地知识库检索 + 大模型流式回答 |
| 托盘 | 双击 = 设置；右键 = 全部功能入口 | explorer 重启自动重建图标 |

---

## 快速开始

### 下载即用

到 [Releases](https://github.com/wysdshg/QuickTool/releases) 下载 `QuickTool.exe` 直接运行——不写注册表、无需安装、目标机不用装 Python（Win10/11 自带 UCRT）。已实测绿色便携：单独复制 exe 到空目录 + 伪造空白 APPDATA 模拟新电脑首次运行，8 个关键自检事件齐全、退出码 0。

### 源码运行

```bat
git clone https://github.com/wysdshg/QuickTool.git
cd QuickTool
run.bat        :: 需要 Python 3.12（系统级，带 tkinter）
```

### 截图 OCR 语言包

OCR 用系统自带的 Windows.Media.Ocr，语言包取决于系统安装情况：设置 → 时间和语言 → 语言 → 选项 → 下载「文本识别」语言功能。未安装时划词翻译不受影响，截图翻译会给出引导。

### 配置翻译与模型凭据

翻译引擎：MyMemory（默认，零配置）/ 离线词库（断网兜底）/ 大模型（质量最优）。大模型走「凭据中心」：

```jsonc
"providers": {
  "siliconflow": { "base_url": "https://api.siliconflow.cn/v1", "api_key": "sk-…", "chat_model": "Qwen/Qwen3-8B" },
  "modelscope":  { "base_url": "https://api-inference.modelscope.cn/v1", "api_key": "…", "chat_model": "Qwen/Qwen3.8-Flash-Next" },
  "zhipu":       { "base_url": "https://open.bigmodel.cn/api/paas/v4", "chat_model": "glm-4.5-flash" },
  "deepseek":    { "base_url": "https://api.deepseek.com/v1", "chat_model": "deepseek-chat" },
  "custom":      { "base_url": "http://localhost:11434/v1" }   // 本地 Ollama 也走这里
}
```

推荐组合：日常查词 MyMemory（零成本）→ 质量场景智谱 `glm-4-flash`（完全免费）→ 断网/隐私切本地 Ollama 或离线词库。完整引擎对比与接入说明见 [docs/03-翻译接口与凭据.md](docs/03-翻译接口与凭据.md)。

---

## 架构与技术选型

### 为什么是 Python + tkinter + ctypes

1. **零第三方依赖**：GUI 用内置 tkinter；热键/剪贴板/托盘全部 `ctypes` 直调 Win32 API；网络用内置 urllib；知识库用内置 sqlite3。
2. **易改**：Python 读起来像伪代码，符合「轻量工具持续演化」的定位（起步约 800 行，现核心约 6000 行）。
3. **不碰坑**：一台有 Python 的 Windows 就能开发打包，没有 Rust/WebView2/.NET 工具链负担。

| 方案 | exe 体积 | 常驻内存 | 冷启动 | 依赖 |
|---|---|---|---|---|
| **Python + tkinter + ctypes（本方案）** | **24.2 MB** | **~9 MB** | **~1.1 s** | 标准库 + pypdf |
| C# WPF / .NET | 60MB+ | 30~60MB | ~0.5s | .NET 运行时 |
| Tauri v2 (Rust) | 3~5MB 安装包 | 40MB+（WebView2） | ~1s | WebView2 |
| Electron | 150MB+ | 150MB+ | 2~4s | 自带 Chromium |

> 起步前调研过 Pot / STranslate / SnapTranslate / FlashTrans 等 7 个同类项目：成熟软件太重（WebView2 40MB+ / .NET 60MB+），同技术路线的 Python 项目有三处致命伤（不还原剪贴板 / 接口国内不可达 / 无回退链），改造量 ≈ 重写——完整调研表见 [docs/01-开源方案调研.md](docs/01-开源方案调研.md)。

### 刻意砍掉的依赖

| 常见做法 | 为什么不用 |
|---|---|
| `keyboard` / `pynput` 监听热键 | 装的是**全局键盘钩子**（键盘记录器行为），极易被杀软拦截，部分场景要管理员权限。`RegisterHotKey` 是系统级机制，无钩子、无权限要求 |
| `pywin32` 操作 Win32 | 打包体积 +30MB 起；ctypes 声明几个结构体就够 |
| `requests` 发请求 | 只是 GET/POST + JSON，urllib 够用，少一个依赖 |
| `pyperclip` | 做不到「全格式快照/还原」（不只文本），直接用 Win32 剪贴板 API |
| `pystray` 托盘库 | 自己用 `Shell_NotifyIconW` 实现约 60 行，省掉 Pillow 依赖 |
| PIL 处理截图 | Tk 原生认 PNG——用标准库 zlib 手写 PNG 编码（见核心实现精选） |

### 五线程模型

```
┌─ 主线程（Tk 事件循环）──────────────┐   ┌─ Win32 线程（独立消息循环）────┐
│ 悬浮窗 / 设置 / 便签 / RAG 界面      │   │ 全局热键 / 托盘消息 / 鼠标钩子  │
│ queue.Queue.poll(50ms)  ←────────── │   │ 只做轻量判断 + 投递消息         │
└─────────────────────────────────────┘   └───────────────────────────────┘
┌─ CaptureWorker ─────────┐  ┌─ JobWorker ──────────┐  ┌─ RagWorker ────────┐
│ 抓词（Ctrl+C + 等剪贴板）│  │ 联网翻译 / 截图 OCR   │  │ RAG 检索 + 流式生成 │
└─────────────────────────┘  └──────────────────────┘  └────────────────────┘
```

Tk 不是线程安全的，所有线程**只通过 `queue.Queue` 单向通信**，绝不跨线程操作控件；抓词/翻译/OCR/RAG 各用独立 worker，一段 10s 的 RAG 回答不会堵住翻译/OCR 排队。

---

## 核心实现精选

### 抓词链路：快照 → 序列号 → 还原（三重保护）

Windows 没有「读取任意窗口选区」的通用 API，本方案选「模拟 Ctrl+C + 读剪贴板」（浏览器/PDF/Word/IDE/远程桌面通吃），配三重保护：① 抓词前**全格式快照**剪贴板（含图片/文件）；② 用**剪贴板序列号**判断复制是否完成，不盲等；③ 无论成败都**还原**，用户剪贴板无感。组合键注入按步分批（Ctrl↓ → 25ms → C↓↑ → 20ms → Ctrl↑）——零间隔批量注入会被 Word 的异步输入管线当成裸字符提交（表现为「选区被清除、原地多一个 c」）。

### 拖选卡鼠标：467ms → 23ms

程序运行时拖一下鼠标会卡半秒。根因：`WH_MOUSE_LL` 钩子回调靠安装线程的消息泵驱动，而抓词（等剪贴板超时 0.45s × 2）原本就跑在同一线程——没选中内容时钩子线程被占住，系统派发的鼠标消息全部被拖住。实测（`tests/drag_lag.py` 统计鼠标消息到达间隔）：基线 22ms，拖选后尖峰 **467ms**；把抓词移出钩子线程后复测 **23ms、>150ms 尖峰 0 个**（跨线程白盒对照：钩子线程往返 800ms → 0ms）。五线程模型就是这次重构确立的。

### 剪贴板堆损坏排查实录

程序偶发静默消失。取证（Windows 事件日志 + faulthandler）定位为 `0xc0000374` **堆损坏**——C 层崩溃 Python 捕获不到。根因：CaptureWorker 与主线程同时 `OpenClipboard` 的竞态 + 对 CF_BITMAP 等**非 HGLOBAL 格式**调 `GlobalSize` 读坏堆元数据。修复三件套：① 剪贴板族函数全部走进程内互斥锁串行化；② 非 HGLOBAL 格式黑名单（绝不调 GlobalSize）；③ 有界读取（按 GlobalSize 截断，不信 NUL 终止符）。加固的同时补了三层崩溃取证基建（faulthandler + 三处异常钩子 + 关键事件埋点 + 5 分钟心跳）——「程序为什么没了」从此可追溯。

### 截图 OCR：内置 WinRT 引擎桥

选 Windows.Media.Ocr 而不是 PaddleOCR/Tesseract：完全离线、无 Key、无第三方依赖（后两者要 pip 依赖或外部二进制，打包体积与杀软风险都不可接受）。实现是内嵌 PowerShell 反射调用 WinRT 异步（PS 5.1 需手动拿 `GetAwaiter`）；抓屏用 GDI `BitBlt + CAPTUREBLT` 自拼 BMP——进程做 DPI 感知，避开「DPI 不感知进程抓 125% 缩放屏坐标错位」的坑。

### 截图对照：标准库手写 PNG 编码

对照小窗要显示抓屏位图，但 `tk.PhotoImage` 不认 BMP、PIL 违反零依赖铁律——用标准库 zlib 手写 PNG 编码（8bit RGB + IDAT 压缩）再喂给 Tk。多窗并存（上限 5）用「点谁谁在前」+ 级联偏移 28px 管理，截图自动进剪贴板与 Win+Shift+S 同格式（CF_DIB，走剪贴板互斥锁）。

### 全局热键顺延与便携分发

热键被占用（错误码 1409）自动换用候选链空闲组合并**写回配置**，下次启动直接用记住的组合；程序内部保证同一组合不绑两个功能。分发层面：exe 单文件零安装，config.json 同目录即便携模式（可放 U 盘）、否则落 `%APPDATA%`；换机后热键自动重新顺延落位，OCR 语言包缺失有引导，主功能不受影响。

> 全部 14 个实现主题（含悬浮窗、迷你按钮钩子细节、便签段定位、日志系统、打包发布流程等）的完整版本：[docs/02-核心实现详解.md](docs/02-核心实现详解.md)

---

## 项目结构

```
QuickTool/
├── main.py                # 入口：五线程模型、热键路由、事件分发、RAG worker
├── QuickTool.spec         # PyInstaller 打包配置（pypdf 瘦身 excludes）
├── qt/
│   ├── winapi.py          # 纯 ctypes：热键 / 剪贴板 / 光标 / 托盘 / 抓屏 / 鼠标钩子
│   ├── capture.py         # 选中文本提取 + PDF 断词清洗
│   ├── engines.py         # 翻译引擎（MyMemory / Google / LLM / 离线）
│   ├── translator.py      # 引擎调度：LRU 缓存 + 失败回退链
│   ├── config.py          # 配置读写 + 凭据中心（五厂商 / DPAPI 加密）+ 开机自启
│   ├── logging_setup.py   # 三层崩溃取证 + 心跳 + 轮转日志
│   ├── ocr.py             # Windows.Media.Ocr 桥（内嵌 PowerShell）
│   ├── ui.py              # 悬浮窗 / 设置 / 便签 / 对照窗 / 选区遮罩 / 迷你按钮
│   └── ragui.py           # RAG 问答窗 + 文档库管理窗
├── rag/                   # RAG 引擎（纯标准库 + 云端 embedding/rerank）
│   ├── engine.py          # 五阶段总装：Fusion → 双路召回 → RRF → Rerank → 生成
│   ├── store.py           # 本地知识库（sqlite FTS5 trigram）
│   ├── splitter.py        # fence-aware 切分（代码块原子化）
│   ├── bm25.py / vectors.py / fusion.py / api.py / pdftext.py
├── data/mini_dict.json    # 内置离线词库（306 词，断网兜底）
└── tests/
    ├── smoke.py           # 冒烟测试（285 项断言）
    ├── e2e.py             # 端到端：真实按键触发全链路（源码 + 打包双跑）
    └── perf.py            # 体测：体积 / 启动 / 内存
```

---

## 开发者文档（docs/）

> 面向开发者与 AI 辅助开发——改代码前建议先查对应文档，尤其 09 号文档的踩坑大事记。

| 文档 | 内容 |
|---|---|
| [01-开源方案调研](docs/01-开源方案调研.md) | 7 个同类项目对比、为什么不改造现成项目、借鉴了什么 |
| [02-核心实现详解](docs/02-核心实现详解.md) | 全部 14 个实现主题的完整细节（热键/抓词/线程/OCR/对照/便签/RAG/日志…） |
| [03-翻译接口与凭据](docs/03-翻译接口与凭据.md) | 8 种翻译方案对比、本机实测、各引擎接入配置、离线方案 |
| [04-配置说明](docs/04-配置说明.md) | 配置文件结构、便携模式、换机迁移注意事项 |
| [05-打包与发布](docs/05-打包与发布.md) | PyInstaller 步骤、spec 关键配置、体积/性能优化与实测数据 |
| [06-方案优缺点与适用场景](docs/06-方案优缺点与适用场景.md) | 自研 vs 成熟软件 vs 改造现成项目的取舍分析 |
| [07-自动化测试](docs/07-自动化测试.md) | smoke / e2e / 加固 / 真 App 驱动全套测试矩阵与实测结果 |
| [08-后续扩展方向](docs/08-后续扩展方向.md) | 已落底项与当前候选方向（按价值排序） |
| [09-版本演进与踩坑大事记](docs/09-版本演进与踩坑大事记.md) | v1.4 → v1.7.3 按版本的完整踩坑、修复过程与实测数据 |

---

*QuickTool v1.7.3 · 运行时零第三方依赖 · 底层能力基于 Win32 API（RegisterHotKey / 剪贴板 / Shell_NotifyIcon）*
