# QuickTool v1.7.0

Windows 轻量级桌面效率工具集：划词翻译 · 截图翻译 · 截图对照 · 置顶便签 · **RAG 快捷问答（新）**

## 🆕 v1.7.0 核心更新

### RAG 快捷问答（文档问答全链路）
- **五阶段检索管线**：RAG-Fusion 变体扩展 → BM25(FTS5 trigram)+bge-m3 向量双路召回 → RRF 融合 → bge-reranker-v2-m3 重排 → 流式生成（SSE，`enable_thinking=false` 提速 ~4 倍）
- **置顶问答窗**：`Ctrl+Alt+R`（被占用自动顺延）唤起，多开并存、win_id 队列路由流式渲染；选中文字作为「背景」随问题注入
- **文档库（KbManager）**：导入 `.txt` / `.md` / **`.pdf`**（新），删除/清空/向量进度一目了然；知识库纯本地 sqlite FTS5 + 云端向量

### PDF 文档库导入（新，唯一第三方依赖例外 pypdf）
- 中文 PDF 的 CID 字体必须解 ToUnicode 才能还原——实测零依赖手写解流对中文 100% 乱码，故引入纯 Python 的 pypdf（零原生扩展）
- PDF 走**后台线程逐页提取**（进度实时显示，不卡界面），拼成带「第 N 页」标题链的 markdown 入库，**检索来源可定位到页**
- 导入完成做**质量体检**，当场提示三类问题：扫描版（无文字层）/ CID 乱码（错映射成泰文/老挝文等，异常脚本占比检测，U+FFFD 探不出来）/ 图片页偏多（每页均字数过低）
- 可选依赖经三方实测（源码/全量打包/瘦身打包，6 样本提取 SHA1 逐字节一致）确认不参与 extract_text，spec 已 excludes 瘦身（exe 12.5MB → 24MB）

### 凭据中心（多厂商生成切换）
- 设置页统一管理五家 API Key：**硅基流动 / 魔搭 ModelScope / 智谱 GLM / DeepSeek / 自定义（OpenAI 兼容）**
- 生成服务商下拉切换，翻译 LLM 引擎与 RAG 回答生成共用所选厂商，可填模型覆盖或留空用厂商默认
- 密钥字段 save/load 全程 **DPAPI 加密**（仅本机本账户可解，落盘无明文）；旧配置启动一次性幂等迁移

### 其他
- DeepL 官方接口国内不可达，翻译引擎收敛为 MyMemory / GoogleGTX / 大模型 / 离线词库四家
- RAG 检索纯本地（sqlite FTS5 + 向量，零第三方依赖）

## ✅ 验证
- smoke **249/249**（新增 6.24 凭据中心 11 项、6.25 PDF 导入 11 项）
- e2e 真按键全链路 **14/14**（--exe 打包版）
- 打包版 PYZ 核验：pypdf 53 模块在包、numpy/PIL/setuptools 零泄漏

## 📦 使用
下载 `QuickTool_v1.7.0.exe` 直接运行（单文件、免安装）。PDF 功能无需额外安装；RAG 问答需在设置页填入硅基流动等厂商 API Key（本机 DPAPI 加密存储）。

---
*运行时零第三方依赖 · 底层能力基于 Win32 API（RegisterHotKey / 剪贴板 / Shell_NotifyIcon）*
