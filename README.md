# Nan Sentinel（南哨）

面向高校学生与授权校园组织的本地优先社群情报助手。产品采用“学生情报哨站 + 组织情报母舰”双层结构：学生端减少翻群和漏看，组织端完成跨哨站预警确认、误报复核与事项处置。

母舰不是原始聊天监控后台。学生通过邀请密钥、链接或二维码主动加入，先选择允许共享的群、A/B/C 分类、证据范围和有效期，再查看实际共享预览。哨站默认只同步分类、摘要、标签、置信度、来源类型和哈希化来源指纹；发送者身份、完整聊天原文和私聊不会默认上传。

## 已实现

- QQ / NapCat 实时接入，默认只处理群聊，私聊需主动开启。
- CSV 历史导入与统一导入 API，可承接 Webhook、飞书、钉钉、邮件、RSS 适配器。
- LLM 分类失败自动降级到规则引擎，结果保留置信度、分类方法和来源。
- 学生端支持消息检索、收藏、周报、预警与逐条人工纠错。
- 组织母舰支持协作空间、仅显示一次且可轮换的邀请密钥、链接/二维码、独立成员密钥、节点启停、A 类预警、误报复核、处置状态和审计。
- 学生可按群和分类授权、预览母舰将看到的内容、设置有效期、暂停共享、删除已共享数据或彻底撤销。
- 管理员申请完整原文时必须填写核验理由；学生在本机看到原文后，只能逐条二次批准或拒绝，批准内容 7 天后清除。
- 同步采用本地持久化待发队列；网络失败不丢数据，恢复后可重试。
- 母舰默认仅接收结构化情报；可选证据片段会限制长度并脱敏手机号、邮箱、QQ/微信联系方式。
- 后端与 NapCat WebSocket 仅监听本机回环地址；配置接口不回显 API Key、哨站令牌和管理员令牌。

产品边界与角色设计见 [PRODUCT.md](PRODUCT.md)，复赛架构、演示路径和常见问答见 [docs/MOTHERSHIP_UPGRADE.md](docs/MOTHERSHIP_UPGRADE.md)。

## 本地运行学生哨站

环境要求：Windows、Python 3.10+、Node.js 20+。

```powershell
cd frontend
npm ci
npm run build

cd ..\backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
Copy-Item config.yaml.example config.yaml

cd ..
.\backend\venv\Scripts\python.exe launcher.py
```

首次启动后，在设置中填写兼容 OpenAI Chat Completions 格式的 Base URL、模型名和 API Key。密钥只写入已被 Git 忽略的本机 `backend/config.yaml`。

## 本地运行组织母舰

母舰是独立 FastAPI 服务。开发或复赛演示时可直接启动：

```powershell
.\backend\venv\Scripts\python.exe -m mothership.bootstrap
```

启动器会生成仅显示一次的管理员令牌。操作顺序：

1. 管理员在“设置 → 情报母舰 → 管理员工作台接入”填写母舰地址和管理员密钥。
2. 打开“情报母舰”，创建协作空间，获得仅显示一次的邀请密钥、链接和二维码。
3. 学生打开邀请，在本机选择群、分类、证据范围与期限，并核对共享预览。
4. 学生确认后，本机保存独立成员密钥；点击“立即同步”发送授权范围内的结构化情报。
5. 管理员确认预警、复核误报或更新处置状态；如需原文，发起带理由的一次性申请。

正式部署必须使用 HTTPS、持久化磁盘和由环境变量注入的高强度 `MOTHERSHIP_ADMIN_TOKEN`。详细说明见 [mothership/README.md](mothership/README.md)。

## 多源导入

CSV 必需列为 `content`，可选列包括：

```text
id,channel_id,channel_name,sender_id,sender_name,created_at,source_url
```

外部适配器可向本机接口 `POST http://127.0.0.1:8000/api/sources/import` 提交：

```json
{
  "source_type": "feishu",
  "source_name": "课程群",
  "messages": [
    {
      "id": "external-message-id",
      "channel_id": "chat-id",
      "channel_name": "群名",
      "sender_id": "user-id",
      "sender_name": "发送者",
      "content": "消息正文",
      "created_at": "2026-06-28 10:00:00",
      "source_url": "https://example.com/message"
    }
  ]
}
```

该通道已统一数据契约，但飞书、钉钉等平台仍需按各自开放平台规则创建应用、授权和事件签名适配器；不能把“支持统一导入”表述为“已完成全部原生接入”。

## 数据与隐私

- `backend/config.yaml`、`backend/market.db`：哨站本机配置和数据库，均不提交 Git。
- `mothership/mothership.db`：母舰结构化情报数据库，不提交 Git。
- 私聊采集默认关闭；任何组织接入都应先明确授权范围、用途、留存周期与退出方式。
- 管理员令牌与哨站令牌权限分离；停用哨站后，其令牌立即失效。
- NapCat 属于非官方 QQ 接入方案，存在账号风控与平台规则变化风险；正式发布前需明确提示并评估官方 QQ Bot。

## 验证

```powershell
cd frontend
npm run lint
npm run build
npm audit

cd ..
.\backend\venv\Scripts\python.exe -m unittest discover -s tests -v
.\backend\venv\Scripts\python.exe -m py_compile backend\api.py backend\scraper.py mothership\app.py mothership\bootstrap.py launcher.py build_exe.py
```

回归测试覆盖密钥保护、历史迁移、去重、人工纠错、收藏、多源导入、失败保留、母舰权限分离、令牌停用、结构化同步和审计记录。

## 项目结构

```text
backend/              学生哨站 FastAPI、SQLite、分类与同步队列
frontend/             React + TypeScript 学生端与母舰工作台
mothership/           组织母舰服务、节点权限、复核与审计
tests/                哨站和母舰回归测试
NapCat_Portable/      本机 QQ 接入运行时（不纳入 Git）
docs/                 复赛架构与演示说明
launcher.py           桌面启动器
build_exe.py          Windows EXE 打包脚本
installer.iss         Inno Setup 安装脚本
PRODUCT.md            产品定位与设计边界
```

## 当前边界

- QQ 全量群消息依赖 NapCat；官方 Bot 一般只能获取其授权范围或与 Bot 相关的消息。
- 飞书、钉钉、邮件、RSS 当前具备统一导入通道，尚未完成所有平台的原生授权适配。
- 人工复核准确率仅统计实际复核样本，不代表全量模型准确率。
- 母舰当前是可部署的复赛版本；正式多组织运营仍需补充账号体系、细粒度 RBAC、备份恢复和合规评估。
