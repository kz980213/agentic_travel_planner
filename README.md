# 智能旅行规划助手(Agentic Travel Planner)

一个生产就绪的对话式旅行智能体。大模型负责规划行程,并通过一个小型的进程内 MCP
服务器完成真实库存的预订 —— 该服务器把航班、酒店、租车、天气和支付都暴露成工具。

- **真实 API** 搜索:Amadeus Self-Service(航班 + 酒店)、带地理编码的 Open-Meteo
  (天气,任意城市)。
- **真实预订**,采用托管/联盟(affiliate)模式:智能体在 Aviasales(航班)、
  Hotellook(酒店)、RentalCars(租车)上生成真实的预订链接。用户在合作方的安全页面
  完成支付与出票。与 Kayak / Skyscanner / Hopper 同样的模式。通过 Travelpayouts 做联盟归因。
- **Stripe Checkout** 用于**你自己**收取的任何费用(礼宾/服务费)。会话是幂等的、
  与 webhook 绑定;银行卡数据绝不经过你的服务器。本地开发可用 mock 模式。
- **多 provider 大模型**:OpenAI、Anthropic、Google。全链路异步。
- **外部 MCP 服务器**(可选):设置 `GOOGLE_MAPS_API_KEY` 后,
  `@modelcontextprotocol/server-google-maps` 这个 Node 子进程会在应用启动时拉起,
  为大模型的工具箱新增 `maps_geocode`、`maps_directions`、`maps_distance_matrix`、
  `maps_places_search` 等 —— 与进程内的航班/酒店/租车工具并列。
- **按会话隔离的记忆**,带滑动窗口上限;会话通过 `X-Session-Id` 相互隔离。
- **生产级 Web 层**:流式 NDJSON 聊天、文件上传大小与 MIME 魔数校验、CORS 白名单、
  单请求超时。
- **聊天界面**:自动把纯 URL 变成链接,并在新标签页自动打开合作方预订页
  (Aviasales / Hotellook / RentalCars / Stripe Checkout),让用户直达结账。每个对话
  独立的 `X-Session-Id` 让服务端记忆与可见的聊天线程保持一致。
- **可观测性**(可选):Langfuse 记录每一次智能体轮次和大模型调用,并在写日志前对
  PII 做脱敏。
- **测试套件**,覆盖率 ≥70%(pytest-asyncio + respx + freezegun)。

## 快速开始

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 至少填入一个大模型的 key。

uvicorn web_server:app --reload --port 5000
# 打开 http://localhost:5000
```

在 `.env` 中设置 `STRIPE_MODE=mock`,即可在没有 Stripe 凭证的情况下端到端运行。

命令行方式:

```bash
python -m travel_agent.cli
```

## 架构

```
                ┌──────────────┐
   用户聊天 ───▶│ FastAPI 应用 │── 流式 NDJSON ──▶ 浏览器/命令行
                │ web_server   │
                └──────┬───────┘
                       │ 按会话
                       ▼
                ┌──────────────┐    ┌──────────────────────┐
                │  编排器       │◀──▶│ 大模型 (OpenAI/等)    │
                │ Orchestrator │    └──────────────────────┘
                └──────┬───────┘
                       │ 工具调用
                       ▼
                ┌──────────────┐
                │  MCPServer   │   注册并调用工具(同步/异步)
                └──────┬───────┘
                       │
   ┌────────────┬──────┴──────┬──────────────┬──────────────┐
   ▼            ▼             ▼              ▼              ▼
  航班         酒店          租车           天气           支付
(Amadeus)   (Amadeus)   (RentalCars     (Open-Meteo)   (Stripe
            Search v3)   深链)                          Checkout)
```

完整架构 —— 模块参考、请求与 webhook 生命周期、设计决策、扩展点 —— 见
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 预订流程(重要)

| 工具 | 返回 | 由谁完成预订 |
|------|------|--------------|
| `book_flight` | Aviasales 链接 + intent_reference | 用户,在 Aviasales |
| `search_hotels` | 酒店房源 + Hotellook 链接 | 用户,在 Hotellook |
| `rent_car` | 价格预估 + RentalCars 链接 | 用户,在 RentalCars |
| `create_payment_session` | Stripe Checkout 链接 | 用户,在 stripe.com(仅用于**你自己**的服务费) |

位于 `travel_agent/agent/prompts/system.md` 的系统提示词指示大模型醒目地呈现预订链接,
且绝不声称已经扣款。

## 测试

```bash
pip install -r requirements-dev.txt
pytest --cov=travel_agent --cov-report=term-missing
```

CI 在每次 push 与 PR 时运行,覆盖率低于 70% 即失败。

## 部署

```bash
docker build -t travel-agent .
docker run --rm -p 5000:5000 --env-file .env travel-agent
curl http://localhost:5000/healthz   # 存活探针
curl http://localhost:5000/readyz    # 就绪探针
```

生产环境使用 Stripe:
1. 设置 `STRIPE_MODE=live`、真实的 `STRIPE_SECRET_KEY`(sk_live_…)和
   `STRIPE_WEBHOOK_SECRET`。
2. 把 Stripe webhook 指向 `https://<你的域名>/webhooks/stripe`。
3. 把 `ALLOWED_ORIGINS` 锁定为你的真实域名(绝不要用 `*`)。

若所选模式缺少任何必需的 key,`Config.validate()` 会在启动时抛出异常。

## 文档附件

聊天接口接受 PDF / DOCX / TXT 附件(最大 `MAX_UPLOAD_MB`,默认 25)。文本在服务端
通过 pypdf / python-docx 抽取,并作为一个清晰标注的区块插入到用户消息中(这样大模型
会把它当作数据,而不是指令)。

## 延伸阅读

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —— 模块参考、请求与 webhook 生命周期、
  设计决策、扩展点。
- [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) —— 生产就绪计划:
  把本仓库从 demo 推进到生产就绪所做的每一处改动,按阶段组织。
- [`CONTRIBUTING.md`](CONTRIBUTING.md) —— 开发环境搭建、测试运行、约定规范。

## 许可证

MIT。详见 `LICENSE`。
