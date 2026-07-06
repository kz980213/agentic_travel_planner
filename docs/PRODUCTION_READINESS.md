# 智能旅行规划助手 —— 生产就绪计划

> **状态:** 第 1–6 阶段及第 2.5 阶段 ✅ 已完成(98 个测试通过,**覆盖率 75%**,
> 门槛 70%)。本仓库**作为单用户、单主机应用已生产就绪**。日常架构见
> [`ARCHITECTURE.md`](ARCHITECTURE.md)。
>
> **第 7 阶段(见下)是"正经"多用户生产部署的计划** —— 持久化、鉴权、前端加固、
> 可观测性留存。尚未开始;当前代码在本地/单租户使用下无需它也能正常运行。

## 背景

目标:把本仓库从"demo / 教学项目"推进到**生产就绪**:干净、无 bug、安全、测试充分,
并带有真实的支付集成。

已锁定的决策:
- **删除 `annotated/`**(唯一真相来源)。✅
- 用 `pytest-asyncio` 实现**充分的测试覆盖**(目标 ≥70%)。✅ 75%。
- **保留 Stripe**,但支付流程围绕 **Stripe Checkout Session + webhook** 重建(对聊天
  智能体而言这是正确模式 —— 无 PCI 范围、真实 SCA/3DS、真实金钱)。✅
- **处处真实 API**(在最初范围之外新增):航班 + 酒店搜索用 Amadeus,天气用
  Open-Meteo(带地理编码),预订通过 Aviasales / Hotellook / RentalCars + Travelpayouts
  的托管/联盟模式。✅ —— 见第 2.5 阶段。

计划组织为 6 个顺序阶段。每个阶段都有验收标准 —— 只有前一个通过,我们才进入下一个。
第 1–2 阶段是小的 bug/安全修复;第 3 阶段(支付)是单个最大的改动;第 4 阶段(重构)
让第 5 阶段(测试)变得轻松;第 6 阶段是清理与部署。第 2.5 阶段是在预订架构必须改变时
中途插入的。

---

## 第 1 阶段 —— 止血式 bug 修复 ✅

自包含的崩溃/正确性 bug。每个都只有几行。

| # | 文件:行 | 修复 |
|---|-----------|-----|
| 1.1 | `verify_langfuse.py:69` | 把 `os.times().elapsed` 换成 `time.time()`(该脚本目前会崩溃) |
| 1.2 | `travel_agent/tools/weather.py:50` | 别再把 `Config.WEATHER_API_KEY` 当 URL 用。硬编码 `https://api.open-meteo.com/v1/forecast` 并去掉该环境变量(Open-Meteo 无需 key) |
| 1.3 | `travel_agent/tools/cars.py:24` | 用 `datetime.strptime` 从 `start_date`/`end_date` 计算 `days`,而不是硬编码 `3` |
| 1.4 | `travel_agent/tools/flights.py:162` | 删除 `if origin.upper() == "NOW":` 调试分支 |
| 1.5 | `travel_agent/tools/flights.py:165` | 把脆弱的 `f"{date[:-2]}{int(date[-2:]) + 1:02d}"` 换成 `datetime + timedelta(days=1)` |
| 1.6 | `travel_agent/agent/llm.py:319` | 给 `genai.protos.Type[v['type'].upper()]` 加 `KeyError` 防护;默认 `STRING` |
| 1.7 | `travel_agent/agent/llm.py:189` | 用 try/except 包住 `json.loads(tc.function.arguments)`;失败时作为工具错误呈现,不要让循环崩溃 |
| 1.8 | `travel_agent/agent/orchestrator.py:257` | `if not response:` → `if response is None:`(空字典曾被当作失败) |
| 1.9 | `travel_agent/tools/weather.py:60,66-68` | 用 try/except 包住 `httpx.get`;在索引前对 `daily[...][0]` 做边界检查 |

**验收:** `python verify_langfuse.py` 能跑到结束。`2026-06-01`→`2026-06-10` 的租车返回
9 天。`search_flights("XYZ", "ABC", "2026-01-31")` 不会把日期运算搞崩。

---

## 第 2 阶段 —— 安全与并发加固 ✅

| # | 文件:行 | 修复 |
|---|-----------|-----|
| 2.1 | `web_server.py:46` | CORS:把 `allow_origins=["*"]` 换成按逗号切分的 `ALLOWED_ORIGINS` 环境变量(生产无通配符) |
| 2.2 | `web_server.py:148` | 强制 25 MB 上传上限;在把请求体读入内存前就拒绝 |
| 2.3 | `web_server.py:149` | MIME 类型白名单(`application/pdf`、DOCX、`text/plain`);通过 `python-magic` 嗅探魔数,不信任客户端请求头 |
| 2.4 | `web_server.py:157` | 用 `asyncio.wait_for(..., timeout=300)` 包住流式生成器;超时时 yield 一个干净的 SSE 错误 |
| 2.5 | `travel_agent/tools/flights.py:115` | 把 `random.randint` 的预订编号换成 `secrets.token_urlsafe(8).upper()` |
| 2.6 | `travel_agent/agent/orchestrator.py:151` | 对 Langfuse metadata 做 PII 脱敏:去掉邮箱、≥8 位连续数字、文档正文 |
| 2.7 | `travel_agent/agent/orchestrator.py:406` | 面向用户的通用错误消息;完整堆栈只进 logger |
| 2.8 | `web_server.py:53,125` | 在 `uvicorn.run` 前初始化 `agent`(避免启动钩子竞态);为每个会话创建全新的 `InMemoryMemory`(新的 `X-Session-Id` 头或生成的 UUID),这样用户之间不共享历史 |
| 2.9 | `travel_agent/tools/flights.py:8-9` | 用 `asyncio.Lock` 包住 `_amadeus_token_cache` 的修改;重构为一个小巧的 `AmadeusTokenCache` 类 |
| 2.10 | `travel_agent/agent/cache.py` | 新增 `AsyncToolCache`(真正的 `async def wrapper`);`weather.py:7` 切换到它;同步 `ToolCache` 只保留给同步工具 |

**验收:** `curl -H "Origin: http://evil"` 被拒绝。100 MB 上传返回 413。两个并行聊天会话
获得各自独立的历史。`pytest tests/test_security.py`(第 5 阶段新写)通过。

---

## 第 3 阶段 —— Stripe Checkout 端到端重写 ✅

用真实的生产流程替换 demo 支付流程。仍在 Stripe 内。

### 3.1 架构(新流程)

```
LLM 工具调用: create_payment_session(amount, currency, booking_metadata)
        │
        ▼
PaymentService.create_checkout_session()
   • stripe.checkout.Session.create(
        mode="payment",
        line_items=[{price_data, quantity}],
        success_url=APP_URL + "/payment/success?sid={CHECKOUT_SESSION_ID}",
        cancel_url=APP_URL + "/payment/cancel?sid={CHECKOUT_SESSION_ID}",
        metadata={booking_id, flight_id, ...},
        customer_email=...,
        payment_intent_data={ idempotency_key: booking_id }
     )
        │
        ▼
工具返回托管的 Stripe URL + session_id → 用户在聊天中点击
        │
        ▼
用户在 Stripe 托管页面付款(卡 / Apple Pay / Google Pay,SCA 自动)
        │
        ▼
Stripe POST → POST /webhooks/stripe(带签名)
   • stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
   • 收到 "checkout.session.completed" → BookingService.finalize(metadata.booking_id)
   • 收到 "checkout.session.expired" / "payment_failed" → 标记预订取消
        │
        ▼
智能体轮询 BookingService(或 webhook 推送到会话存储)→ 在聊天中确认
```

### 3.2 新增 / 变更的文件

- **`travel_agent/payments/__init__.py`** —— 包边界。
- **`travel_agent/payments/stripe_client.py`** —— `StripeClient` 封装 `stripe` SDK。集中管理 `api_key`、重试、错误映射。暴露:
  - `create_checkout_session(amount_cents, currency, customer_email, metadata, success_url, cancel_url) -> CheckoutSession`
  - `verify_webhook(payload: bytes, sig_header: str) -> stripe.Event`(签名错误时抛出)
  - `retrieve_session(session_id) -> stripe.checkout.Session`
- **`travel_agent/payments/service.py`** —— `PaymentService` 是业务层(provider 无关的接口,便于未来 provider 接入)。负责幂等性、payment intent → booking_id 映射的持久化(v1 用内存字典,藏在 `PaymentStore` 接口之后)。
- **`travel_agent/payments/models.py`** —— Pydantic 模型:`CheckoutRequest`、`CheckoutResponse`、`WebhookEvent`、`PaymentStatus` 枚举(`pending`、`succeeded`、`failed`、`cancelled`、`expired`)。
- **`travel_agent/tools/payment.py`** —— 已替换。新工具:
  - `create_payment_session(amount, currency, customer_email, booking_id) -> {url, session_id, expires_at}`
  - `get_payment_status(session_id) -> {status, amount_paid, ...}`
  - 旧的 `process_payment` 移除(那是硬编码卡号的 hack)。
- **`web_server.py`** —— 新增 `POST /webhooks/stripe` 路由(需要原始请求体;**不能**先做 JSON 解析)。验签,分发给 `PaymentService.handle_webhook(event)`。
- **`tests/test_payments.py`** —— 新增(第 5 阶段)。
- **`travel_agent/agent/orchestrator.py`** —— 更新系统提示词:向大模型解释新流程(它必须把 Checkout URL 返回给用户,然后在最终确认预订前调用 `get_payment_status` 确认)。
- **`.env.example`** —— 新增 `STRIPE_PUBLISHABLE_KEY`、`STRIPE_WEBHOOK_SECRET`、`APP_URL`(用于 `success_url`/`cancel_url`)。
- **`travel_agent/config.py`** —— 新增这些 key;当 `STRIPE_MODE=live` 时校验已设置 webhook secret。

### 3.3 幂等性、重试、错误

- 对每次 Stripe 写操作(`checkout.Session.create`)传入 `idempotency_key=booking_id`。对同一预订重复运行 `create_payment_session` 会返回已有会话,绝不重复扣款。
- 用一个小重试辅助包住 Stripe API 调用(3 次、指数退避,仅在 `stripe.error.APIConnectionError` / `RateLimitError` 时重试 —— 绝不在 `CardError` 或 `InvalidRequestError` 时重试)。
- 在 `StripeClient._map_error` 中把 Stripe 错误 → 对用户安全的消息。绝不把 `stripe.error.*` 泄露到聊天输出。
- Webhook 处理必须幂等(重试时我们会收到重复的 `checkout.session.completed`)—— 以 `event.id` 为键,已处理事件存在 `PaymentStore` 中。

### 3.4 移除了什么

- `pm_card_visa` 硬编码测试卡 —— 移除。
- `confirm=True` 自动确认 —— 移除(Checkout 处理确认)。
- "没有 Stripe key 就 mock" 的静默回退 —— 移除(缺少 `STRIPE_SECRET_KEY` 时我们在启动时高声失败)。一个单独的 `STRIPE_MODE=mock` 环境变量显式为本地开发选择 mock 路径。

### 3.5 验收

- 端到端测试模式:本地运行 `stripe listen --forward-to localhost:8000/webhooks/stripe`;智能体创建会话,测试卡 `4242 4242 4242 4242` 在托管页面完成,webhook 触发,预订在 `BookingService` 中标记为已确认,智能体在聊天中确认。
- 3DS 测试卡 `4000 0027 6000 3184` 通过 SCA 挑战成功。
- 失败卡 `4000 0000 0000 9995`(余额不足)→ webhook 以 `checkout.session.async_payment_failed` 触发,预订标记失败,智能体告知用户。
- 签名错误的 webhook 返回 400。
- 同一 webhook 事件重放两次只产生一个已完成的预订。
- `pytest tests/test_payments.py` 通过(用 `respx` 风格的 fixture mock `stripe` SDK)。

---

## 第 4 阶段 —— 重构与代码质量 ✅

在支付之后进行,这样重构不会和进行中的重写冲突。

| # | 位置 | 变更 |
|---|-------|--------|
| 4.1 | `travel_agent/agent/orchestrator.py:23-141` | 把 120 行系统提示词移到 `travel_agent/agent/prompts/system.md`,初始化时加载 |
| 4.2 | `travel_agent/agent/orchestrator.py:158-200` | 把 `DocumentProcessor` 类抽到 `travel_agent/agent/documents.py`;编排器只调用它。文档上下文进入 **system** 提示词,而非用户消息 |
| 4.3 | `travel_agent/agent/orchestrator.py:245-255, 309-321` | 抽出 `_async_retry(coro_factory, *, attempts, base_delay)` 辅助;LLM 与工具重试共用 |
| 4.4 | `travel_agent/agent/orchestrator.py:211,243,305` | 所有重试/轮次上限移入 `Config`(`MAX_TURNS`、`MAX_LLM_RETRIES`、`MAX_TOOL_RETRIES`) |
| 4.5 | `travel_agent/agent/llm.py` | 新建 `travel_agent/agent/llm/` 包:`base.py`、`openai.py`、`anthropic.py`、`google.py`、`schema.py`(单一 `SchemaBuilder`)、`observability.py`(Langfuse 装饰器)。消除 `generate_text` 与工具 schema 转换的三处重复 |
| 4.6 | `travel_agent/agent/llm/google.py` | 按 `(model_name, system_instruction)` 缓存 `GenerativeModel` 实例 —— 别再每次调用都重建(`llm.py:385-389`) |
| 4.7 | `travel_agent/agent/llm/*` | 把所有 `print()` 换成 `logging.getLogger(__name__)` |
| 4.8 | `travel_agent/agent/memory.py:26` | 滑动窗口:保留最近 `MAX_MESSAGES`(可配置,默认 50) |
| 4.9 | `travel_agent/cli.py` 与 `web_server.py` | 抽出共享的 `travel_agent/setup.py::build_agent(provider) -> Agent` —— 干掉重复 |
| 4.10 | `travel_agent/tools/*` | 所有工具的参数用 Pydantic 模型:`FlightSearchArgs`、`CarRentalArgs`、`WeatherArgs`,带校验器(IATA 正则、日期格式、`start < end`、货币白名单、amount > 0) |
| 4.11 | `travel_agent/tools/flights.py:84-97,132-142` | 把 `airline_map` 提升为模块级常量 |
| 4.12 | `travel_agent/mcp/mcp_server.py:103` | `str(result)` → `json.dumps(result, default=str)`;调用前依签名校验必填参数 |
| 4.13 | 死代码清扫 | 移除:`travel_agent/agent/cache.py:36` 未用全局、`travel_agent/mcp/protocol.py:27-30` 未用 `Tool`、`travel_agent/mcp/mcp_server.py:12,31-56` 孤立的 `tool_models`、`travel_agent/agent/orchestrator.py:343` 未用 `run()`、`debug_gemini.py` → `scripts/` |
| 4.14 | `travel_agent/config.py` | 改为 `@dataclass(frozen=True)`;`load_dotenv()` 变懒加载(`@functools.cache`);`validate()` 抛出 `ConfigError` |

**验收:** `ruff check .` 干净;`mypy travel_agent/` 干净;`travel_agent/` 中无 `print()`;
行为与重构前一致(由第 5 阶段测试验证)。

---

## 第 5 阶段 —— 测试套件(目标 ≥70% 覆盖率)✅(75%)

| 文件 | 覆盖内容 |
|------|----------------|
| `tests/conftest.py` | 共享 fixture:`mock_llm`、`in_memory_memory`、`mcp_server_with_tools`、`httpx_mock`(经 `respx`)、`stripe_mock`、经 `freezegun` 冻结的时间 |
| `tests/test_llm_openai.py` | 初始化、`generate_text`、工具调用往返、格式错误 JSON 参数处理(第 1 阶段 #7)、错误映射 |
| `tests/test_llm_anthropic.py` | 同上 |
| `tests/test_llm_google.py` | 同上 + schema 类型回退(第 1 阶段 #6)+ 模型缓存(第 4 阶段 #6) |
| `tests/test_schema_builder.py` | 跨 provider schema 转换正确性 |
| `tests/test_mcp_server.py` | `register_tool` schema 推断、异步 vs 同步工具、缺参校验、JSON 结果序列化 |
| `tests/test_tools_flights.py` | 用 `respx` mock Amadeus;token 缓存;book_flight 幂等性;日期运算边界(跨月) |
| `tests/test_tools_cars.py` | 天数计算、校验错误 |
| `tests/test_tools_weather.py` | 真实 API 成功 + httpx 出错时回退 mock、边界检查后的 daily 数组 |
| `tests/test_tools_datetime.py` | 时区参数、经 `freezegun` 确定性 |
| `tests/test_payments.py` | `create_checkout_session`(mock Stripe)、webhook 验签、幂等 webhook 重放、3DS 成功路径(模拟)、失败支付路径、mode=mock |
| `tests/test_orchestrator.py` | 替换原有的弱测试。新增:带 mock LLM + 工具的完整智能体循环、文档抽取(PDF/DOCX/TXT fixture)、重试/退避断言、最大轮次上限、语言一致性 |
| `tests/test_documents.py` | PDF/DOCX/TXT 抽取、超大文件拒绝、格式错误文件处理 |
| `tests/test_memory.py` | 滑动窗口淘汰、add/get 往返 |
| `tests/test_config.py` | 缺少必需 key 时抛出;完整环境下校验通过 |
| `tests/test_web_server.py` | 流式响应、文件上传(大小上限、MIME 嗅探)、CORS 拒绝、会话隔离、webhook 路由 |
| `tests/test_security.py` | 预订编号熵、PII 脱敏、CORS、上传限制 |

新 `requirements-dev.txt` 中新增的开发依赖:`pytest`、`pytest-asyncio`、`pytest-cov`、
`respx`、`freezegun`、`ruff`、`mypy`。`pytest.ini` 设置 `asyncio_mode = auto`,覆盖率
门槛 70%。

**验收:** `pytest --cov=travel_agent --cov-fail-under=70` 绿。CI 在每个 PR 上运行(第 6 阶段)。

---

## 第 6 阶段 —— 清理、配置、部署 ✅

| # | 变更 |
|---|--------|
| 6.1 | **删除 `annotated/`**。更新 README,去掉 230-239 节 |
| 6.2 | 删除根目录的 `test_api_integration.py`(损坏的陈旧路径);仅在重写后保留 `tests/test_api_integration.py` |
| 6.3 | 把 `debug_gemini.py` 和 `verify_langfuse.py` 移到 `scripts/`;从仓库根目录移除 |
| 6.4 | 重写 `.env.example`:去掉 `WEATHER_API_KEY`(Open-Meteo 无 key);新增 `LLM_PROVIDER`、`STRIPE_MODE`、`STRIPE_WEBHOOK_SECRET`、`APP_URL`、`ALLOWED_ORIGINS`、`MAX_TURNS`、`MAX_MESSAGES`。按分组加注释 |
| 6.5 | `.gitignore`:收紧 `.env` → `/.env`;新增 `.coverage`、`htmlcov/`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/` |
| 6.6 | `requirements.txt`:给 `stripe`、`openai`、`anthropic`、`google-generativeai`、`fastapi` 加版本上界。拆出 `requirements-dev.txt` |
| 6.7 | `Dockerfile`:通过 `ARG PYTHON_VERSION=3.11` 固定;新增打 `GET /healthz` 的 `HEALTHCHECK`;`CMD` → `ENTRYPOINT`;最终阶段去掉 `.env.example`;加非 root 用户检查 |
| 6.8 | `web_server.py`:新增 `GET /healthz`(返回 `{status: "ok", version, langfuse_enabled, stripe_mode}`)和 `GET /readyz`(校验 LLM provider + Stripe 可达) |
| 6.9 | 新增 `.github/workflows/ci.yml`:`ruff check`、`mypy`、`pytest --cov-fail-under=70`、`docker build`。在每个 PR + push 到 main 时运行 |
| 6.10 | 重写 `README.md`:去掉 "framework-free" 说法,修正 Amadeus/mock 默认说明,新增 **Testing**、**Architecture**、**Deployment**、**Stripe webhook setup** 章节;移除 annotated/ 节 |
| 6.11 | 新增 `CONTRIBUTING.md`:开发环境、测试运行、lint、提交风格 |
| 6.12 | 生产启动检查:若缺少任何**当前模式所需**的 key(LLM key、`STRIPE_MODE=live` 时的 Stripe key、`STRIPE_WEBHOOK_SECRET`、`APP_URL`),`Config.validate()` 在启动时抛出 |

**验收:** 全新克隆 → `cp .env.example .env` → 填 key → `docker build && docker run` →
`/healthz` 返回 200、`/readyz` 返回 200、针对 Stripe 测试模式的端到端聊天 + 支付流程可用。
干净分支上 CI 绿。

---

## 关键文件

核心智能体:`travel_agent/agent/orchestrator.py`、`travel_agent/agent/llm.py` → `llm/` 包、`travel_agent/agent/memory.py`、`travel_agent/agent/cache.py`、`travel_agent/agent/documents.py`(新)
工具:`travel_agent/tools/{flights,cars,weather,payment,datetime_tool}.py`
支付:`travel_agent/payments/{stripe_client,service,models}.py`(新)
MCP:`travel_agent/mcp/mcp_server.py`、`travel_agent/mcp/protocol.py`
Web:`web_server.py`、`travel_agent/cli.py`、`travel_agent/setup.py`(新)
配置与基础设施:`travel_agent/config.py`、`.env.example`、`Dockerfile`、`requirements.txt`、`requirements-dev.txt`(新)、`.github/workflows/ci.yml`(新)
测试:`tests/` 中每个文件 + 新的 `tests/conftest.py`

## 应复用的现有工具(不要重复造轮子)

- `travel_agent/agent/cache.py::ToolCache` —— 用 `AsyncToolCache` 扩展
- `travel_agent/agent/memory.py::AgentMemory` 抽象基类 —— 为任何未来持久化做子类
- `travel_agent/mcp/mcp_server.py::MCPServer.register_tool` —— 已经从签名推断 schema;扩展而非复制
- `travel_agent/config.py::Config` —— 环境的唯一真相来源;新代码从这里读,绝不直接读 `os.environ`

## 端到端验证(第 6 阶段后运行)

1. `rm -rf venv && python -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt -r requirements-dev.txt`
3. `cp .env.example .env` → 填 `OPENAI_API_KEY`(或其他 LLM)、`STRIPE_SECRET_KEY`(test)、`STRIPE_WEBHOOK_SECRET`、`APP_URL=http://localhost:8000`、`ALLOWED_ORIGINS=http://localhost:3000`
4. `ruff check .` → 干净。`mypy travel_agent/` → 干净。
5. `pytest --cov=travel_agent --cov-fail-under=70` → 绿。
6. `stripe listen --forward-to localhost:8000/webhooks/stripe`(一个终端)
7. `uvicorn web_server:app --reload`(另一个终端)
8. 打开聊天界面,规划行程,预订航班,用 `4242 4242 4242 4242` 完成支付,确认 webhook 到达后智能体确认了预订。
9. 用 3DS 卡 `4000 0027 6000 3184` 和拒付卡 `4000 0000 0000 9995` 重复。
10. `docker build -t travel-agent . && docker run --rm -p 8000:8000 --env-file .env travel-agent` → `curl localhost:8000/healthz` 返回 200。

## 范围之外(对第 1–6 阶段而言)

以下项被有意推迟 —— 它们现已计划在**第 7 阶段**。

- 前端重写(现有 `static/` 聊天界面保持原样;只是加一个 "Pay" 链接点击跳转)。
- 持久化数据库(记忆 + 支付存储保持在接口之后的内存实现 —— 加 Postgres 是后续)。
- 鉴权 / 多租户(单用户 demo → 单用户生产;用户账户是另一个项目)。
- 生产级 Langfuse 主机与留存策略(我们只是保持集成的正确性)。

---

## 第 2.5 阶段 —— 处处真实 API(通过托管/联盟模式预订)✅

在用户澄清后新增:"预订酒店、租车、航班以及获取天气的每一次调用都必须是真实 API。"

不可回避的约束:真实的**搜索** API 对开发者是可访问的;真实的**扣用户款的预订**需要与
每个供应商签署商业协议(Amadeus 生产环境、Hertz、Hilton 等)。诚实的生产架构与 Kayak、
Skyscanner、Hopper 所用的相同:

- **真实 API** 用于搜索 + 数据检索。
- **托管/联盟页面**(Aviasales / Hotellook / RentalCars)用于最终预订 + 支付。
- Travelpayouts 联盟 marker 用于佣金归因。

### 实现(已完成)

| 工具 | Provider | 真实之处 |
|------|----------|-------------|
| `get_forecast` | Open-Meteo(无 key) | 14 天内实时预报;超出则用历史归档代理;任意城市地理编码 |
| `search_flights` | Amadeus Self-Service | 真实航班报价(测试端点,读取无需商业协议) |
| `book_flight` | Aviasales 深链 + Travelpayouts 联盟 | 返回真实预订 URL;用户在合作方站点完成支付 + 出票 |
| `search_hotels`(新) | Amadeus Hotel Search v3 | 真实酒店报价;API 失败时回退到 Hotellook 深链 |
| `rent_car` | RentalCars 深链 + Travelpayouts 联盟 | 计算价格预估 + 真实预订 URL |

系统提示词已更新,让大模型明确向用户解释深链流程,并绝不声称已扣款。Stripe 保留给
明确的"服务费"场景。

### 新环境变量(加到 `Config`)
- `TRAVELPAYOUTS_MARKER`(可选)—— 联盟 marker;没有它 URL 也能用,但你放弃归因。
- `CARS_AFFILIATE_HOST`(默认 `https://tp.media/r`)—— Travelpayouts 重定向主机。

---

## 第 2.6 阶段 —— 外部 MCP 服务器(Google Maps)✅

新增此项,是为了证明"平移到 stdio MCP"的说法并非空想,并为大模型提供更丰富的旅行规划
工具箱(驾车路线、地点搜索、距离矩阵)。

### 已完成

- `travel_agent/mcp/mcp_server.py::register_mcp_subprocess` —— 通过官方 `mcp` Python
  SDK 的 `stdio_client` 拉起一个 MCP 服务器子进程,经 `tools/list` 发现其工具,把每个
  远程工具作为代理注册进同一个进程内注册表。子进程由一个 `AsyncExitStack` 持有;
  `close()` 会把它们都干净地关闭。
- `travel_agent/setup.py::attach_external_mcp_servers` —— 异步、受 key 控制。目前当
  设置了 `GOOGLE_MAPS_API_KEY` 时拉起 `@modelcontextprotocol/server-google-maps`;
  失败会记录但绝不抛出。
- `web_server.py` —— 改用 FastAPI 的 `lifespan` 上下文管理器,子进程在应用启动时启动、
  关闭时关闭。
- `requirements.txt` —— 固定 `mcp>=1.0.0,<2.0.0`。

### 它新增了什么

| 工具 | Provider | 说明 |
|------|----------|-------|
| `maps_geocode` / `maps_reverse_geocode` | Google Maps Geocoding API | 地址 ↔ 经纬度 |
| `maps_directions` | Google Maps Directions API | 驾车/步行/公交路线 |
| `maps_distance_matrix` | Google Maps Distance Matrix API | 多对多出行时间 |
| `maps_search_places` / `maps_place_details` | Google Maps Places API | 某地点附近的 POI |
| `maps_elevation` | Google Maps Elevation API | 地形海拔 |

编排器和大模型看待 Maps 工具与进程内的航班/酒店/租车工具完全一致 —— 同样的
`MCPServer.list_tools()` / `call_tool()`。

### 注意事项(在第 7.6 阶段跟踪)

- 上游 `@modelcontextprotocol/server-google-maps` 包在 npm 上被标记为**已弃用**。集成
  仍然可用;上生产前我们需要一个可持续的替代。
- 当前 `Dockerfile` 仅含 Python —— 生产容器不会有 `npx`,所以子进程今天在 Docker 里
  不会启动。
- 缺少子进程崩溃恢复 —— 若 Node 在会话中途挂掉,工具就开始失败。

---

## 支付 provider 理由(为什么选 Stripe 而非其他)

| Provider | 契合度 | 为什么 / 为什么不 |
|---|---|---|
| **Stripe** ✅ | 最佳 | 最好的 Python SDK 与文档;Payment Intents/Checkout 自动处理 SCA/3DS;135+ 货币;世界级测试模式;稳健的 webhook;已经接好 |
| PayPal/Braintree | 弱 | DX 更差、更多重定向粘合、SDK 笨重、测试模式更弱 |
| Adyen | 过度 | 企业级但入驻沉重、文档更弱、集成慢 |
| Square | 不契合 | 线下强;国际化更弱,对旅行/可变金额更弱 |
| Checkout.com | 小众 | 在 EU/MEA 强但生态更小、上手更难 |
| Paddle/LemonSqueezy(Merchant of Record) | 不契合 | 为 SaaS 订阅设计;对一次性可变收费不便 |

对于**基于聊天的智能体**,额外的架构决策是使用 **Stripe Checkout Session**(托管),而不是
在对话中收集卡信息。智能体把托管 URL 返回给用户,Stripe 处理所有卡 UI / SCA / 钱包,
webhook 在服务端确认预订。

---

## 第 7 阶段 —— 正经生产部署(已计划,未开始)

本仓库今天作为**单用户、单主机应用**已生产就绪。要把它作为真正的多用户服务部署到公网,
以下四条工作线需要落地。每条按独立完成来估算;实际中 7.1 → 7.2 → 7.4 → 7.3 是自然顺序
(持久化解锁鉴权;鉴权解锁按用户的 UI 工作;可观测性是并行的基础设施任务)。

### 7.1 持久化存储(替换内存存储)

**为什么:** 今天一次进程重启会丢失 (a) 每个聊天会话的历史,以及 (b) `PaymentStore` 中的
`booking_id → checkout_session_id` 映射。第二个是危险的 —— 重启后到达的 webhook 无法完成
预订,而重新 `create_payment_session` 调用会丢失幂等性,可能重复扣款。

**思路:** 两个存储都已位于抽象基类之后(`AgentMemory`、`PaymentStore`)。工作纯粹是新增
Postgres 支撑的实现 —— 无需改编排器或 Web 层。

**要新增的文件:**
- `travel_agent/persistence/__init__.py`
- `travel_agent/persistence/db.py` —— `asyncpg` 连接池、`init_db()` 迁移执行器。
- `travel_agent/persistence/postgres_memory.py` —— `PostgresMemory(AgentMemory)`。
- `travel_agent/persistence/postgres_payment_store.py` —— `PostgresPaymentStore(PaymentStore)`。
- `migrations/001_init.sql` —— 下方 schema。
- `tests/test_persistence.py` —— 用 `pytest-postgresql` fixture;与现有内存测试一起运行。

**Schema(`migrations/001_init.sql`):**
```sql
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sessions_last_used_idx ON sessions(last_used_at);

CREATE TABLE messages (
    id         BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role       TEXT NOT NULL,            -- system | user | assistant | tool
    content    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX messages_session_idx ON messages(session_id, id);

CREATE TABLE payment_sessions (
    booking_id           TEXT PRIMARY KEY,
    stripe_session_id    TEXT NOT NULL UNIQUE,
    status               TEXT NOT NULL,    -- pending|succeeded|failed|cancelled|expired
    amount_cents         INTEGER NOT NULL,
    currency             CHAR(3) NOT NULL,
    customer_email       TEXT,
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payment_sessions_stripe_idx ON payment_sessions(stripe_session_id);

CREATE TABLE processed_webhook_events (
    event_id    TEXT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**接线:**
- `Config`:新增 `DATABASE_URL`、`MEMORY_BACKEND=memory|postgres`、`PAYMENT_STORE_BACKEND=memory|postgres`。
- `travel_agent/setup.py::build_agent`:依 `MEMORY_BACKEND` 分支选择实现。
- `web_server.py` 启动:若任一后端 = postgres,运行 `init_db()` 并在连接失败时高声失败。
- `SessionManager` 的 TTL/淘汰逻辑改为一个 `DELETE FROM sessions WHERE last_used_at < now() - interval '24 hours'` 后台任务(lifespan 中 `asyncio.create_task`)。

**多副本安全:** `AgentMemory.get_messages` 中的滑动窗口读取需按 `(session_id, id ASC) LIMIT MAX_MESSAGES` 排序 —— 已完成。`PostgresPaymentStore.record_event` 用 `INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING event_id` 处理 webhook 去重竞态。

**迁移策略:** 全新项目 —— 无既有生产数据。直接把 schema 作为 `001_init.sql` 发布,若 `MEMORY_BACKEND=postgres` 则启动时运行。

**估算工作量:** 约 4 小时代码 + 2 小时测试。生产的关键路径。

---

### 7.2 鉴权与多租户

**为什么:** 今天的"会话"就是客户端发送的任意 `X-Session-Id` 头。任何人猜中(或嗅探到)
一个会话 ID,就能拿到那个会话的历史*以及*其待支付链接。对 `localhost` 可接受;对公网
不可接受。

**思路(v1,最小):** API-key 鉴权 + 用户范围会话。无密码登录 UI、无 OAuth —— 那些是 v2。
目标是在不构建身份服务的前提下阻止未鉴权访问。

**要新增/变更的文件:**
- `travel_agent/auth/__init__.py`
- `travel_agent/auth/api_keys.py` —— `verify_api_key(key) -> user_id`(查表,key 用 bcrypt 哈希)。
- `travel_agent/auth/dependencies.py` —— FastAPI `Depends(current_user)`,返回一个 `User` dataclass。
- `migrations/002_users.sql`:
  ```sql
  CREATE TABLE users (
      user_id      TEXT PRIMARY KEY,
      email        TEXT NOT NULL UNIQUE,
      api_key_hash TEXT NOT NULL,
      created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  ALTER TABLE sessions ADD COLUMN user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE;
  ALTER TABLE payment_sessions ADD COLUMN user_id TEXT REFERENCES users(user_id);
  CREATE INDEX sessions_user_idx ON sessions(user_id);
  ```
- `web_server.py`:
  - 所有 `/chat`、`/upload`、`/session*` 路由加 `Depends(current_user)`。
  - `/webhooks/stripe` 保持不鉴权(已验签)—— Stripe 无法发送 API key。
  - 会话查找变为 `WHERE session_id = $1 AND user_id = $2`(纵深防御 —— 阻止一个用户认领另一个用户的会话 ID)。
- `scripts/create_user.py` —— 铸造用户 + API key 的 CLI(一次性打印明文 key,只存 bcrypt 哈希)。
- `static/app.js` —— 从 `localStorage` 读 API key,作为 `Authorization: Bearer <key>` 头发送。

**限流:** 加 `slowapi` 中间件,以 `user_id` 为键,默认每用户 60 req/min。webhook 路由豁免。

**这明确不做:**
- 无密码登录/注册流程(管理员通过 CLI 分发 key)。
- 无 OAuth / SSO / Google 登录。
- 无按用户的 Stripe customer 记录(仍是单个 Stripe 账户;`customer_email` 贯穿)。
- 无角色/权限系统 —— 每个已鉴权用户平等。

**升级到 v2 的路径:** 把 `api_keys.py` 换成 `oauth.py`(Authlib + Google),给 `sessions` 加 `refresh_token` 列,其余不变。`User` 接口不变。

**估算工作量:** 约 3 小时代码 + 2 小时测试 + 前端接线(约 1h)。阻塞任何公网部署。

---

### 7.3 前端打磨与加固

**为什么:** 当前 `static/` 聊天界面在后端重写期间被有意保持原样。它能用,但有些粗糙边角,
真实用户一接触就会暴露。

**具体清单(按优先级):**

1. **鉴权集成**(依赖 7.2):首次访问弹出 API-key 输入框;存 `localStorage`;把 401 呈现为重新鉴权提示,而非通用错误。
2. **支付状态轮询**:当大模型返回 Stripe Checkout URL 时,UI 应每 3 秒轮询 `GET /payment/status/{session_id}`(新端点,`PaymentService.get_status` 的薄封装)直到终态,然后内联呈现确认。今天用户得再问一遍智能体。
3. **流式健壮性**:NDJSON 解析器在慢网络下无法干净处理跨行缓冲切分。用 `TextDecoder` + 行边界状态机替换临时 split。
4. **错误 UX**:区分 (a) 瞬时网络错误(重试按钮)、(b) 限流 429(倒计时)、(c) 超时(自动重试一次)、(d) 5xx(致歉 + 上报链接)。今天一切都是"出错了"。
5. **无障碍**:纯键盘流程审计(Tab 顺序、Escape 关弹窗、图标按钮的 ARIA 标签)、聊天更新时的焦点管理、thinking 指示动画尊重 prefers-reduced-motion。
6. **移动端**:iOS Safari 上文件上传弹窗溢出;键盘出现时的 viewport-height 修复;历史侧边栏中 <44px 的点击目标。
7. **客户端历史持久化**:今天刷新会丢失可见 UI 状态,即便服务端历史存活(有了 7.1)。加载时调用 `GET /sessions/{id}/messages` 并注水。
8. **安全响应头**(服务端但与 UI 相邻):`Content-Security-Policy`、`X-Frame-Options: DENY`、`Referrer-Policy: strict-origin-when-cross-origin`。在 `web_server.py` 中通过 FastAPI 中间件添加。
9. **构建流水线**:原生 JS 现在够用,但要 minify + 给 `app.js`/`style.css` 加指纹以破缓存;为带指纹的资源加 `Cache-Control: immutable`。

**估算工作量:** 开放式 —— 从上面挑 3–4 项发布;其余可迭代。(1)+(2)+(8) 是已鉴权公网部署的最低门槛。

---

### 7.4 可观测性 —— Langfuse 留存与自托管

**为什么:** 集成是正确的(PII 脱敏、trace 已 flush、绝不抛出),但今天每条 trace 都用个人
key 发往 **Langfuse Cloud**。真实部署你需要决定:留在云上、用付费方案 + 留存策略,还是
自托管。无论哪种,告警和看板目前都为零。

**先要做的决策(它们决定后续工作):**
- **托管 vs 自托管 Langfuse?** 自托管 = 完全数据掌控 + Docker Compose + Postgres + ClickHouse;托管 = 零运维 + 月费。现在选定会改变基础设施计划。
- **PII 脱敏级别**:当前 `_redact_pii` 去掉邮箱 + ≥8 位连续数字。够 GDPR 吗?对信用卡相邻流程恐怕不够,尽管我们从不看到卡。
- **留存窗口**:30 / 90 / 365 天?决定存储成本与法务审查。

**要新增的文件/配置(与主机选择无关):**
- `travel_agent/agent/orchestrator.py`:给每条 trace 打上 `user_id` + `session_id` + `release_version`(构建时从 `git rev-parse HEAD` 烘焙为 `RELEASE_SHA` 环境变量)。
- `Dockerfile`:`ARG GIT_SHA` → `ENV RELEASE_SHA=$GIT_SHA`;CI 传 `--build-arg GIT_SHA=$GITHUB_SHA`。
- `travel_agent/agent/observability.py`(新):tag schema + 脱敏配置的集中处;其余代码从这里读,而非直接从 `Config`。
- **告警**:Langfuse → webhook → 任意目标(PagerDuty / Slack)。至少:10 分钟内 `error_rate > 5%`、10 分钟内 `p95_turn_latency > 30s`、1 小时内 `max_turns_hit_rate > 10%`。
- **看板**:预建 Langfuse 看板:每日轮次数、p50/p95/p99 延迟、工具调用分布、按工具的错误率、按 provider 的 LLM token 花费。

**若自托管:**
- 在主 compose 旁放 `docker-compose.observability.yml`,运行 Langfuse + Postgres + ClickHouse + MinIO。
- 每晚把 ClickHouse 数据卷备份到 S3(`ALTER TABLE traces FREEZE PARTITION ...`)。
- 通过 Caddy 反向代理或 Traefik 做 TLS。

**估算工作量:** 决策工作 > 代码工作。代码约 2h;基础设施搭建自托管约 4h、托管带留存配置约 1h。

---

### 7.5 应与第 7 阶段一起落地的较小项

| # | 变更 | 工作量 |
|---|--------|--------|
| 7.5.1 | 跑一遍端到端 Stripe 验证(`stripe listen` + 4242 + 3DS + 拒付卡)—— `docs/PRODUCTION_READINESS.md` §端到端验证 步骤 6–9。上次验证于 commit `3a7b575`;任何生产部署前重跑。 | 30 分钟 |
| 7.5.2 | 确认 `.github/workflows/ci.yml` 在 GitHub Actions 上是绿的(不只是本地)。 | 5 分钟 |
| 7.5.3 | `/healthz` 与 `/readyz` 已存在;新增 `/metrics`(Prometheus 格式)暴露轮次数、按名工具调用数、LLM 延迟直方图。任何带监控的云部署都需要。 | 1h |
| 7.5.4 | `Dockerfile`:最终阶段去掉 `.env.example`(当前通过 `COPY . .` 拷入);加显式 `COPY` 列表以免泄露开发文件。 | 15 分钟 |
| 7.5.5 | 密钥管理:生产停止读 `.env`。把 `Config` 切为直接从环境读(已经如此),并记录 AWS Secrets Manager / GCP Secret Manager / Doppler 集成模式。 | 30 分钟文档 |
| 7.5.6 | 加一个 `SECURITY.md`,含披露邮箱 + 支持版本表 —— GitHub 安全策略徽章及任何懂负责任披露的研究者都需要。 | 15 分钟 |
| 7.5.7 | 许可证审计:`LICENSES.md` 存在但早于 Stripe + Amadeus + Langfuse 的加入。刷新。 | 30 分钟 |
| 7.5.8 | 为 `MCPServer.register_mcp_subprocess` / `close` / 子进程代理分发写测试。Mock `mcp.client.stdio.stdio_client` 使 CI 无需 `npx`。 | 1h |
| 7.5.9 | CLI 对齐:`cli.py::build_agent` 跳过了 `attach_external_mcp_servers`,所以 `python -m travel_agent.cli` 永远拿不到 Maps 工具。接上或记录该差距。 | 30 分钟 |
| 7.5.10 | 为 `maps_directions` / `maps_distance_matrix` / `maps_search_places` 加系统提示词指引,让大模型在规划多站行程时使用它们。 | 20 分钟 |

---

### 7.6 外部 MCP —— 生产加固

**为什么:** 第 2.6 阶段接上了 Google Maps 并证明抽象成立,但该集成带有三个真实的生产阻塞点。

1. **把 Node 加进生产镜像。** 当前 `Dockerfile` 是 `python:3.12-slim` —— 没有 `npx`,所以
   Maps 子进程会静默地永不启动。装入 `node` + MCP 服务器的多阶段构建是最简单的修复。
2. **替换已弃用的上游包。** `@modelcontextprotocol/server-google-maps@0.6.2` 被 npm 标记
   `Package no longer supported`。选项:固定并接受停滞、fork,或在 `googlemaps` SDK 之上
   自建一个约 200 行的自研 Python MCP 服务器(多数团队最终落到这里)。
3. **子进程崩溃恢复。** 若 Node 在会话中途挂掉,后续每次 `maps_*` 调用都会失败,直到应用
   重启。加一个监督器,检测 `ClientSession` 错误并以指数退避重新拉起;暴露一个熔断器,让
   大模型被告知"地图不可用",而不是每轮都看到传输错误。

**运维后续(较小):**
- **成本控制。** Google Maps 按调用计费(每千次 $5–17)。加按会话计数器 + 硬上限,以及对一次
  会话内重复的 `(tool, args)` 查找做 `AsyncToolCache` 记忆化。
- **工具名冲突策略。** `register_mcp_subprocess` 当前是警告并覆盖。对多服务器场景,做命名空间
  前缀(`maps:geocode`)或直接硬失败。
- **就绪面。** 扩展 `/readyz`,在任何已注册子进程宕机时返回 `503`,让编排器把 pod 轮换下线。

**估算工作量:** 项 1+2+3 是生产最低要求(含自研 Maps 服务器约 1 天)。运维项:各约半天。

---

### 第 7 阶段验收标准

- 无 API key 的用户在 `/chat` 上得到 401。
- 杀掉并重启服务器后保留:聊天历史(Postgres)、待支付会话(Postgres webhook 去重)。
- 同一主机上两个并发用户只看到各自的数据 —— 跨用户会话 ID 猜测返回 403,而非 200。
- `stripe listen` 测试(4242 / 3DS / 拒付)针对新的 Postgres 支撑的 `PaymentStore` 通过。
- Langfuse 看板在每条 trace 上显示 `release_version` 标签。
- `pytest --cov-fail-under=70` 仍绿;新测试覆盖 Postgres 存储与鉴权边界。
- `docker compose up` 拉起 应用 + Postgres +(可选)Langfuse 栈;一条命令得到生产拓扑的可用本地替身。
