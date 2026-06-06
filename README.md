# AI 社群情报控制台 (nan-sentinel)

> 基于 LLM 的 QQ 消息实时分类与情报采集系统 — 桌面客户端

你是否曾在 QQ 群里被 99+ 的消息淹没，翻了半天才找到一条重要通知？你是否因为错过群里的一条二手交易信息而懊恼？**AI 社群情报控制台**就是为了解决这个问题而生的——它自动监听你的 QQ 消息，用大语言模型把每条消息智能分类，让你一目了然地看到什么是重要的、什么是有趣的、什么是值得交易的。

![登录界面1](dist_output_exe/AI_Console_EXE/1.png)
![登录界面2](dist_output_exe/AI_Console_EXE/2.png)
![主界面](dist_output_exe/AI_Console_EXE/3.png)

---

## 它能解决什么问题？

| 痛点 | 解决方案 |
|------|----------|
| QQ 群消息太多，重要通知被淹没 | 自动识别 **A 类重要信息**（考试安排、DDL、放假通知），置顶显示 |
| 想知道群里最近在聊什么八卦 | **B 类校园轶事**自动归类，随时浏览 |
| 错过群里的二手交易/代课/跑腿信息 | **C 类二手资讯**自动抓取，含虚拟服务（代课、拼车、合租等） |
| 想回顾本周群里发生了什么 | **LLM 周报生成**，一键总结本周 ABC 三类情报 |
| 需要把采集到的情报分享到飞书群 | **飞书 Webhook** 实时推送，自动转发到指定飞书群 |
| 多人协作采集，数据需要汇总 | **母舰云端同步**，多个节点数据汇聚到 Vercel 云端 |
| 不想安装 Python/Node.js 等开发环境 | **一键安装包**（EXE），双击即用，零配置 |

---

## 核心功能详解

### 🔍 LLM 智能三分类

消息进入后，系统会调用大语言模型（DeepSeek / OpenAI / Claude）进行语义理解，分为四类：

| 分类 | 含义 | 示例 |
|------|------|------|
| **A — 重要信息** | 学校官方正式通知 | "期末考试安排已出，请查看教务系统" |
| **B — 校园轶事** | 日常讨论、吐槽、学术交流 | "今天食堂的红烧肉绝了"、"高数挂科率多少啊" |
| **C — 二手资讯** | 真实交易意图（含虚拟服务） | "出一个二手显示器，200块"、"代课周三上午，50元" |
| **None — 垃圾信息** | 灌水、纯表情、无意义回复 | "哈哈哈哈哈"、"6"、"👍" |

**智能识别中文修辞**：不会把"卖萌"误判为二手交易，不会把"砸锅卖铁"当成卖废铁——LLM 理解语境，不做字面关键词匹配。

**LLM 不可用时自动降级**到规则引擎（基于关键词匹配），保证系统持续运行。

### ⚡ 实时流模式 vs 📦 积攒模式

系统提供两种工作模式，适应不同场景：

#### 实时流模式（默认）

```
消息到达 → 立即调用 LLM 分类 → 写入数据库 → 仪表盘实时更新
```

**适合场景**：
- 你正在开会/上课，需要实时看到重要通知
- 你想第一时间捕捉二手交易信息（拼手速抢便宜货）
- 你想在飞书群里实时收到情报推送

**优点**：延迟最低，消息到达后几秒内即可看到分类结果。

#### 积攒模式（Batch）

```
消息到达 → 先存入缓冲池（不调 LLM） → 你手动点击"批量处理" → LLM 一次性分析所有消息
```

**适合场景**：
- 你不想每条消息都调 LLM（省钱！DeepSeek 按 token 计费）
- 你想攒一天的消息，晚上统一查看
- 你想让 LLM 看到完整对话上下文后再分类（多人讨论同一话题时，上下文很重要）

**优点**：
1. **省钱**：批量处理时 LLM 按话题聚类，而非逐条调用，API 调用次数大幅减少
2. **更准确**：LLM 能看到完整对话上下文。比如 A 问"有没有人出显示器"，B 回复"我有一个"，C 说"多少钱"——逐条分类可能漏掉，但批量处理时 LLM 能识别这是一个完整的交易讨论
3. **按话题聚类**：批量模式不只是分类，还会把相关消息聚合为一个"话题卡片"，每个话题包含完整的原始对话记录

### 📊 仪表盘

- **实时统计**：总消息数、ABC 各类数量、占比百分比
- **服务状态监控**：NapCat 引擎、WS 消息通道、LLM 配置状态，一目了然
- **最新消息流**：通过 SSE（Server-Sent Events）实时推送，新消息到达时自动刷新，无需手动刷新页面
- **LIVE 指示灯**：绿色 = 实时连接中，橙色 = 离线

### 📬 消息中心

- **分类浏览**：按 A/B/C 分类查看，或查看全部
- **全文搜索**：搜索消息内容、摘要、发送人、群名
- **收藏管理**：一键收藏重要消息，支持自定义收藏夹（如"重要通知"、"好价二手"）
- **时间筛选**：查看最近 1 天 / 7 天 / 30 天的消息

### 📝 周报生成

基于 LLM 自动生成每周情报摘要：
- A 类：本周有哪些重要事项？
- B 类：本周有哪些有趣的校园故事？
- C 类：本周有哪些二手交易信息？

支持按分类单独生成，历史周报自动缓存。

### 🔗 飞书 Webhook

配置飞书群机器人 Webhook 后，新采集到的消息会自动推送到飞书群：

```
[情报同步] 来源: 群聊-XX大学2024级 / 发送人: 张三 / 正文: 出一个二手显示器，200块
```

### ☁️ 母舰云端同步

多个采集节点可以将数据同步到同一个 Vercel 云端（母舰），实现多人协作采集。适合团队使用。

### 🖥️ 桌面客户端

- **pywebview 原生窗口**：不依赖浏览器，独立窗口运行
- **一键启动**：自动拉起 NapCat（QQ 协议引擎）+ API 服务器 + Scraper（消息采集器）
- **进程管理**：窗口关闭时自动清理所有子进程

---

## 快速开始

### 方式一：安装程序（推荐）

1. 从 [Releases](../../releases) 下载 `AIConsole_Setup_x.x.x.exe`
2. 双击安装程序 → Next → 选择目录 → Install
3. 桌面出现「AI 社群情报控制台」图标，双击启动
4. 首次启动会弹出配置窗口：
   - 填写 LLM API Key（必填，用于消息分类）
   - 选择 Base URL 和 Model（默认 OpenAI，可改为 DeepSeek / Claude）
   - 可选填写母舰地址和节点名称（用于云端同步）
5. 点击「保存配置」
6. 右侧会出现 QQ 登录界面，选择登录平台后扫码登录

### 方式二：便携版

1. 从 [Releases](../../releases) 下载 ZIP 包
2. 解压到任意目录
3. 双击 `AI_Console_Launcher.exe`

**系统要求**：Windows 10/11 x64，无需安装 Python 或 Node.js

---

## QQ 登录指南

### 登录平台选择

登录前需要选择登录平台，这决定了你的 QQ 是否会挤掉其他设备：

| 平台 | 说明 | 推荐 |
|------|------|------|
| **iPad** | 不会挤掉电脑端 QQ，可同时在线 | ✅ 推荐 |
| **Android** | 不会挤掉电脑端 QQ，可同时在线 | ✅ 推荐 |
| **Windows** | 会挤掉电脑端 QQ | ❌ 不推荐 |

### 扫码登录

1. 选择登录平台（推荐 iPad 或 Android）
2. 切换到「扫码登录」标签
3. 用手机 QQ 扫描二维码
4. 在手机上确认登录

> ⚠️ **二维码每 10 秒自动刷新**，这是正常行为。如果二维码过期，请等待自动刷新后重新扫描。
>
> ⚠️ 如果提示「二维码过期」，系统会自动获取新码，稍等几秒即可。

### 密码登录

1. 选择登录平台
2. 切换到「密码登录」标签
3. 输入 QQ 号和密码
4. 点击「登录」

> ⚠️ 密码登录可能会触发人机验证，遇到时请改用扫码登录。

### 首次登录的等待时间

首次登录时，系统需要配置 NapCat 的消息通道（WebSocket 服务器）。如果提示「消息通道正在配置中，NapCat 正在自动重启…」，请：

1. **等待约 10 秒**让 NapCat 完成重启
2. 重新扫码登录
3. 登录成功后会自动进入控制台

这是因为 NapCat 需要在登录后才能生成账号专属的配置文件，系统会自动写入 WebSocket 服务器配置并重启 NapCat，整个过程约 10 秒。

### 如果遇到问题

- **二维码不显示**：检查 NapCat 是否启动（查看服务状态中 NapCat 是否为绿色）
- **登录后没有消息**：检查 WS 消息通道状态，如果显示橙色，点击「重新获取」或重启程序
- **已登录但提示未登录**：点击「重新获取」清除登录缓存，重新扫码

---

## 截图说明

| 截图 | 说明 |
|------|------|
| 图 1、图 2 | 登录界面 — 左侧配置 LLM，右侧扫码/密码登录 QQ |
| 图 3 | 主界面 — 仪表盘实时监控，ABC 三类消息统计，最新消息流 |

---

## 架构

```
┌─────────────────────────────────────────────────┐
│                 用户本地 (一键启动)                │
│                                                  │
│  ┌──────────────┐    ┌────────────────────────┐  │
│  │  pywebview   │    │    FastAPI Server      │  │
│  │  原生窗口     │◄──►│    (localhost:8000)    │  │
│  │  React SPA   │    │  静态文件 + REST API    │  │
│  └──────────────┘    └────────────────────────┘  │
│                                                  │
│  ┌──────────────┐    ┌────────────────────────┐  │
│  │    NapCat     │    │     Scraper Agent      │  │
│  │  QQ 协议引擎  │◄──►│  WS 监听 → LLM 分类   │  │
│  │  (port 6099)  │    │  → SQLite / 飞书 / 母舰 │  │
│  └──────────────┘    └────────────────────────┘  │
│                                                  │
│  用户数据: %APPDATA%/AIConsole/                   │
│  ├── market.db        (消息数据库)                │
│  └── config.yaml      (用户配置)                  │
└─────────────────────────────────────────────────┘
```

## 项目结构

```
nan-sentinel/
├── backend/                    # 后端服务
│   ├── api.py                 # FastAPI 服务器（REST API + SSE + 静态文件托管）
│   ├── scraper.py             # WebSocket 消息监听 + LLM 分类引擎
│   ├── config.yaml.example    # 配置模板
│   └── requirements.txt
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── pages/
│   │   │   └── LoginPage.tsx       # LLM 配置 + QQ 登录
│   │   └── components/
│   │       ├── DashboardPage.tsx   # 仪表盘（统计 + 服务状态 + 最新消息）
│   │       ├── MessagesPage.tsx    # 消息中心（分类浏览 + 搜索 + 收藏）
│   │       ├── BookmarksPage.tsx   # 收藏夹管理
│   │       ├── ReportsPage.tsx     # 周报生成
│   │       ├── SettingsPage.tsx    # 系统设置
│   │       ├── Layout.tsx          # 主布局（侧边栏 + 内容区）
│   │       └── ProtectedRoute.tsx  # 登录路由守卫
│   └── dist/                  # 构建输出（已包含在仓库中）
├── api/                        # Vercel 云端 Serverless API（母舰）
│   ├── index.py
│   └── requirements.txt
├── launcher.py                 # 桌面客户端启动器（PyInstaller 入口）
├── build_exe.py                # EXE 打包脚本
├── installer.iss               # Inno Setup 安装程序脚本
├── fetch_napcat.py             # NapCat 下载脚本
├── vercel.json                 # Vercel 部署配置
└── .gitignore
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS |
| 桌面客户端 | pywebview（Edge Chromium 内核） |
| 后端 | Python FastAPI + SQLite + aiohttp |
| QQ 协议 | NapCat（OneBot11 WebSocket） |
| LLM | OpenAI API 兼容（DeepSeek / Claude / GPT） |
| 消息推送 | 飞书 Webhook |
| 打包分发 | PyInstaller + Inno Setup |

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/services` | 各服务运行状态 |
| GET/POST | `/api/config` | 配置管理 |
| GET | `/api/qrcode` | 获取 QQ 登录二维码（PNG） |
| GET | `/api/login/status` | QQ 登录状态（轮询） |
| POST | `/api/login/password` | 密码登录 |
| POST | `/api/login/reset` | 重置登录缓存 |
| GET | `/api/stats` | 消息统计 |
| GET | `/api/messages` | 查询已分类消息（支持筛选/搜索/分页） |
| DELETE | `/api/messages/{id}` | 删除消息 |
| POST | `/api/bookmarks` | 添加收藏 |
| GET | `/api/bookmarks` | 查询收藏 |
| GET | `/api/weekly_summary` | 生成/获取周报 |
| GET | `/api/stream` | SSE 实时消息推送 |
| POST | `/api/batch_process` | 批量处理缓冲池 |
| POST | `/api/restart-napcat` | 重启 NapCat |

## 开发

```bash
# 后端
cd backend
pip install -r requirements.txt
python api.py                # 启动 API 服务器 (localhost:8000)
python scraper.py            # 启动消息采集器

# 前端
cd frontend
npm install
npm run dev                  # 开发模式 (localhost:5173)
npm run build                # 构建生产版本

# 打包 EXE
python build_exe.py          # 生成 dist_output_exe/

# 打包安装程序
# 用 Inno Setup 打开 installer.iss → Build → Compile
```

## 数据持久化

用户数据存储在 `%APPDATA%/AIConsole/`：

| 文件 | 说明 |
|------|------|
| `market.db` | 消息数据库（SQLite） |
| `config.yaml` | 用户配置（API Key、NapCat 设置等） |

- 更新安装时**不会覆盖**已有数据
- 卸载时会提示是否保留数据
- 开发模式下数据存储在项目根目录
- 每个用户通过昵称隔离数据，只能看到自己的消息

## 飞书 Webhook 配置

飞书 Webhook 可以让你在飞书群里实时收到采集到的消息推送。

### 步骤

1. **创建飞书机器人**
   - 打开飞书，进入你想接收消息的群
   - 点击群设置 → 群机器人 → 添加机器人
   - 选择「自定义机器人」
   - 填写机器人名称（如「情报助手」）
   - 点击「添加」

2. **复制 Webhook 地址**
   - 创建完成后会显示一个 Webhook 地址，格式如：
     ```
     https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
     ```
   - 复制这个地址

3. **在控制台中配置**
   - 打开 AI 社群情报控制台
   - 进入「设置」页面
   - 找到「飞书同步」选项，开启它
   - 粘贴刚才复制的 Webhook 地址
   - 点击保存

4. **测试**
   - 配置完成后，新采集到的消息会自动推送到飞书群
   - 推送格式：`[情报同步] 来源: 群聊-xxx / 发送人: xxx / 正文: xxx`

### 注意事项

- Webhook 地址包含敏感信息，请勿泄露
- 每个群可以创建多个机器人，但建议只用一个
- 飞书 Webhook 有频率限制（每分钟最多 5 条），高频消息可能会被合并
- 如果不需要飞书推送，保持关闭即可，不影响其他功能

## 常见问题

### Q: 为什么二维码一直刷新？
二维码每 10 秒自动刷新一次，这是为了防止过期。如果扫码时提示过期，等几秒让新码出来即可。

### Q: 登录后提示「消息通道正在配置中」怎么办？
首次登录需要配置 WebSocket 通道。请等待约 10 秒让 NapCat 自动重启，然后重新扫码登录。

### Q: 选择哪个登录平台？
推荐选择 **iPad** 或 **Android**，这样不会挤掉你电脑上正在使用的 QQ。选择 Windows 会挤掉电脑端。

### Q: LLM API Key 怎么获取？
- **DeepSeek**：访问 [platform.deepseek.com](https://platform.deepseek.com) 注册，创建 API Key
- **OpenAI**：访问 [platform.openai.com](https://platform.openai.com) 注册
- **Claude**：访问 [console.anthropic.com](https://console.anthropic.com) 注册

系统兼容所有 OpenAI 格式的 API，只需修改 Base URL 即可切换服务商。

### Q: 没有配置 API Key 能用吗？
可以。系统会自动降级到规则引擎（基于关键词匹配），但分类准确率会降低。建议配置 LLM 以获得最佳体验。

### Q: 数据存在哪里？
用户数据存储在 `%APPDATA%/AIConsole/`（Windows），包括数据库 `market.db` 和配置文件 `config.yaml`。更新安装不会覆盖已有数据。

## 合规声明

- 本项目使用的 NapCat 是基于 QQNT 协议的开源实现，仅供学习和研究用途
- 请遵守 QQ 的服务条款和相关法律法规
- 不得将本项目用于任何商业用途或侵犯他人隐私的行为
- 使用者需自行承担因使用本项目而产生的一切风险和责任

## License

[MIT](LICENSE)

## 致谢

- [NapCat](https://github.com/NapNeko/NapCatQQ) — QQ 协议框架
- [FastAPI](https://fastapi.tiangolo.com/) — Python Web 框架
- [React](https://react.dev/) — 前端框架
- [pywebview](https://pywebview.flowrl.com/) — 桌面窗口框架
- [Inno Setup](https://jrsoftware.org/isinfo.php) — Windows 安装程序
