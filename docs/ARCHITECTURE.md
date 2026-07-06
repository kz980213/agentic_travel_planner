# 架构

本文档描述智能旅行规划助手是如何组织起来的 —— 逐模块讲解、请求/webhook 生命周期、
关键设计决策以及扩展点。关于*为什么它是生产就绪的*的叙述,见
[`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md)。

---

## 顶层目录结构

```
.
├── web_server.py                 # FastAPI 应用、流式聊天、Stripe webhook
├── travel_agent/
│   ├── config.py                 # 环境变量驱动的 Config + 结构化 JSON 日志
│   ├── setup.py                  # build_llm / build_mcp_server / attach_external_mcp_servers / build_agent
│   ├── cli.py                    # 交互式命令行(无 Web)
│   ├── agent/
│   │   ├── orchestrator.py       # 大模型 ↔ 工具 的循环
│   │   ├── llm.py                # OpenAI / Anthropic / Google provider
│   │   ├── memory.py             # 滑动窗口式会话记忆
│   │   ├── cache.py              # 同步 + 异步 TTL 缓存
│   │   ├── retry.py              # async_retry 辅助函数
│   │   ├── documents.py          # PDF / DOCX / TXT 文本抽取
│   │   └── prompts/system.md     # 外置的系统提示词
│   ├── mcp/
│   │   ├── protocol.py           # JSON-RPC 2.0 + MCP 的 Pydantic 模型
│   │   └── mcp_server.py         # 工具注册表与分发器(进程内 + stdio 子进程)
│   ├── tools/
│   │   ├── flights.py            # Amadeus 搜索 + Aviasales 深链
│   │   ├── hotels.py             # Amadeus Hotel Search v3 + Hotellook 深链
│   │   ├── cars.py               # RentalCars 深链 + 价格预估
│   │   ├── weather.py            # Open-Meteo 预报 + 气候代理估算
│   │   ├── payment.py            # create_payment_session / get_payment_status
│   │   └── datetime_tool.py      # 当前日期/时间
│   └── payments/
│       ├── models.py             # CheckoutRequest / CheckoutResponse / 等
│       ├── stripe_client.py      # StripeClient + StripeMockClient
│       └── service.py            # PaymentService:幂等性、webhook 去重
├── static/                       # 原生 HTML/CSS/JS 聊天界面(无构建步骤)
├── scripts/                      # debug_gemini.py、verify_langfuse.py
├── tests/                        # pytest-asyncio + respx + freezegun
└── docs/                         # PRODUCTION_READINESS.md、ARCHITECTURE.md
```

---

## 组件关系图

```
                ┌──────────────────────┐
   HTTP 聊天 ──▶│ web_server.py        │── NDJSON 流 ──▶ 浏览器/命令行
                │ (FastAPI + lifespan) │
                └─┬──────┬──────┬──────┘
        按会话   │      │      │ POST /webhooks/stripe
                 ▼      │      ▼
        ┌──────────────┐│   ┌──────────────────┐
        │ Orchestrator ││   │ PaymentService   │
        └─┬───────┬────┘│   │ (幂等性 +        │
   工具   │       │ 大模型│   │  webhook 去重)   │
   调用   │       ▼     │   └────────┬─────────┘
          │   ┌──────────┴──┐         │
          │   │ LLMProvider │         │
          │   │ (OAI/Ant/G) │         │ Stripe API ◀──────┐
          │   └─────────────┘         ▼                    │
          ▼                    ┌──────────────┐            │
   ┌──────────────┐            │ StripeClient │ ◀── HTTPS ─┘
   │ MCPServer    │            │  (或 Mock)   │
   │ (注册表 +    │            └──────────────┘
   │  分发器)     │
   └─┬────────┬───┘
     │        │ stdio JSON-RPC(可选,受 key 控制)
     │        ▼
     │   ┌──────────────────────┐
     │   │ npx 子进程           │ ──▶ Google Maps API
     │   │ server-google-maps   │     (地理编码、路线、
     │   │ → maps_* 工具        │      距离矩阵、地点等)
     │   └──────────────────────┘
     │ 进程内分发
     ▼
  ┌──────┬───────┬──────────┬──────────┬─────────────┬──────────┐
  ▼      ▼       ▼          ▼          ▼             ▼          ▼
 航班    酒店    租车       天气    create_payment  get_payment datetime
                                   _session       _status
  ▲      ▲        ▲          ▲
  │      │        │          │
  │ Amadeus       │      Open-Meteo
  │ Self-Service  │   (地理编码 +
  │ (航班 +       │    预报 +
  │  酒店)        │    历史归档)
  │               │
  Aviasales      RentalCars
  联盟            联盟
  深链            深链
```

---

## 模块参考

### `web_server.py`
FastAPI 入口。持有 `SessionManager`(按 `X-Session-Id` 索引的、按会话隔离的
`AgentOrchestrator` 实例),并暴露:

| 接口 | 用途 |
|---|---|
| `GET  /` | 提供静态聊天界面(`static/index.html`) |
| `GET  /healthz` | 存活探针 —— 进程在运行就始终返回 200 |
| `GET  /readyz` | 就绪探针 —— 当降级为 MockAgent 时返回 503 |
| `POST /api/chat` | multipart:`message` + 可选 `file`。以 NDJSON 流式返回事件 |
| `POST /webhooks/stripe` | 原始请求体,验签后分发给 `PaymentService` |
| `GET  /payment/success` | 结账成功后 Stripe 的重定向目标 |
| `GET  /payment/cancel` | 取消后 Stripe 的重定向目标 |

关键防护:CORS 白名单(不用 `*`)、25 MB 上传上限、MIME 魔数嗅探、
单请求 `asyncio.timeout(REQUEST_TIMEOUT_SECONDS)`、按会话的记忆隔离。

一个 FastAPI `lifespan` 上下文管理器在启动时接入可选的外部 MCP 子进程
(调用 `attach_external_mcp_servers`),并在关闭时把它们关掉(`MCPServer.close()`)。

### `travel_agent/config.py`
环境配置的唯一真相来源。

- `Config` —— 类级属性,在导入时(`load_dotenv()` 之后)从 `os.getenv` 读取。
- `Config.validate()` —— 若当前 `STRIPE_MODE` 所需的 key 缺失,则抛出 `ConfigError`。
  live 模式额外要求 `sk_live_…`。
- `setup_logging()` —— 在根 logger 上安装一个幂等的 JSON formatter
  (`JsonFormatter` 在通过 `extra=` 传入时,会在结构化记录里输出 `request_id`/`session_id`)。

测试代码通过 `monkeypatch.setattr(Config, …)` 直接读写属性,而不是重新加载模块 ——
这样可以避免产生别的已导入模块看不到的、彼此分叉的 `Config` 类。

### `travel_agent/setup.py`
`web_server.py` 和 `cli.py` 共用的智能体构建入口:

- `select_provider()` → 从 `Config` 得到 `(provider_name, api_key)`,优先
  `Config.LLM_PROVIDER`,否则回退到任意一个配了 key 的 provider。
- `build_llm()` → 一个 `LLMProvider`,若什么都没配则为 `None`。
- `build_mcp_server()` → 一个注册了所有进程内生产工具的全新 `MCPServer`
  (工具注册的唯一真相来源)。同步 —— 这里不会启动任何子进程。
- `attach_external_mcp_servers(server)` → 异步,由 FastAPI 的 `lifespan` 在启动时调用。
  拉起可选的 MCP 子进程(当前是设置了 `GOOGLE_MAPS_API_KEY` 时的 Google Maps 服务器)。
  失败会记录日志但绝不抛出 —— 即使某个子进程无法启动,进程内工具也必须继续可用。
- `build_agent()` → 供命令行/一次性使用的完整 `AgentOrchestrator`。

### `travel_agent/agent/orchestrator.py`
智能体循环。只有一个方法要紧:`run_generator(user_input, file_data,
mime_type, request_id)`。它会:

1. 创建一个 Langfuse trace(若 Langfuse 关闭则为空操作)。
2. 通过 `DocumentProcessor` 从任何附带的 PDF/DOCX/TXT 中抽取文本,并作为一个清晰
   标注的区块内联进用户消息。
3. 最多循环 `Config.MAX_TURNS` 次:
   - 在开头放一条全新的 `system` 消息(来自 `prompts/system.md` 的静态提示词 +
     一个动态的 `CRITICAL DATE CONTEXT` 区块)。
   - 通过 `async_retry` 调用 `llm.call_tool(messages, tools)` —— 尝试
     `Config.MAX_LLM_RETRIES` 次。
   - 对任何文本内容 yield 出 `{type: "message"}`;若没有工具调用则停止。
   - 对每个工具调用,分发给 `MCPServer.call_tool`(同样用 `async_retry` 包裹),
     yield 出 `tool_call` + `tool_result` 事件,并把结果追加进记忆。
4. 结束/刷新 Langfuse trace。

在任何内容到达可观测性层之前,PII 都会被脱敏(`_redact_pii`:邮箱和 ≥ 8 位的连续数字)。

### `travel_agent/agent/llm.py`
抽象的 `LLMProvider` + 三个实现:

- `OpenAIProvider` —— `AsyncOpenAI` 的 chat.completions。对工具调用参数做防御式
  `json.loads`(格式错误的 JSON 会以 `{"__error__": ...}` 载荷的形式呈现,而不是让循环崩溃)。
- `AnthropicProvider` —— `AsyncAnthropic` 的 messages,并做 content-block 转换
  (`tool_use`、`tool_result`)。
- `GoogleProvider` —— `google.generativeai`。默认模型是 `gemini-2.5-flash`
  (`gemini-2.0-flash` 这个默认值因 Google 免费额度政策被下线;`2.5-flash` 是当前
  广泛可用的替代)。按 `(model_name, system_instruction)` 缓存 `GenerativeModel`
  实例,这样不必每次调用都重建模型对象。工具 schema 的类型在遇到未知值时回退为
  `STRING`(Gemini 对其枚举很严格)。

Langfuse 可观测性放在三个自由函数里:`langfuse_trace`、`langfuse_generation`、
`langfuse_flush`。它们兼容 v2 和 v3 SDK 形态,且绝不抛出 —— 失败会记录日志,trace
直接变成 `None`。

### `travel_agent/agent/memory.py`
`AgentMemory` 抽象基类 + `InMemoryMemory`(滑动窗口,默认
`Config.MAX_MESSAGES = 50`)。`add_message` 会校验每条记录都带 `role`;
`get_messages` 返回一份拷贝,这样调用方无法篡改状态。

### `travel_agent/agent/cache.py`
- `ToolCache` —— 同步、线程安全(使用 `threading.Lock`),缓存键为 JSON+sha256,
  按 TTL 淘汰。
- `AsyncToolCache` —— 异步安全(`asyncio.Lock`),此外还通过一个共享的在途 `Future`
  合并对同一 key 的并发调用。

### `travel_agent/agent/retry.py`
`async_retry(operation, *, attempts, base_delay, label, extra)` —— 编排器对 LLM 调用
和工具调用共用的单一辅助函数。线性退避(`base_delay * (i + 1)`),以 WARNING 级别
带 `exc_info` 记录日志,若所有尝试都失败则重新抛出最后一个异常。

### `travel_agent/agent/documents.py`
`DocumentProcessor` —— 静态方法 `supports(mime_type)` + `extract(data, mime_type)`。
PDF 用 `pypdf`,DOCX 用 `python-docx`,TXT 用 UTF-8 解码。失败时返回 `None`;
编排器会回退为直接转发原始字节。

### `travel_agent/agent/prompts/system.md`
外置的系统提示词。告诉大模型如何规划行程、如何格式化输出,以及 —— 至关重要的 ——
预订是在合作方托管页面上完成的,大模型绝不能声称已经扣款。

### `travel_agent/mcp/protocol.py`
JSON-RPC 2.0 的 Pydantic 模型(`JsonRpcRequest`、`JsonRpcResponse`)以及 MCP 的
`Tool` / `CallToolRequest` / `CallToolResult`。`create_tool_definition` 生成我们交给
大模型的那个字典结构。

### `travel_agent/mcp/mcp_server.py`
工具注册表 + 分发器,并可选支持 stdio MCP 子进程。

- `register_tool(func)` —— 从函数签名推断出 JSON Schema
  (`int`/`float`/`bool`/`list`/`dict` → JSON Schema 类型;其余默认 `"string"`)。
  用 `inspect.getdoc()` 作为工具描述。
- `register_mcp_subprocess(command, args, env, label)` —— 异步。通过官方 `mcp`
  Python SDK 的 `stdio_client` 拉起一个 MCP 服务器子进程(例如 Google Maps 的 Node
  服务器),调用 `initialize` + `tools/list`,并把每个远程工具作为代理注册进同一个注册表。
  工具名冲突时按 last-write-wins 处理(以 WARNING 记录)。子进程由一个 `AsyncExitStack`
  持有;`close()` 会把它们都干净地关闭(接入了 FastAPI 的 `lifespan`)。
- `call_tool(name, arguments)` —— 丢弃未知参数(大模型会幻觉出多余参数)、校验必填参数
  齐全、区分同步/异步分发、把 dict/list 结果做 JSON 编码以免结构被压成 `str(dict)`,
  最后返回一个 `CallToolResult`。工具抛出的 `ValueError` 会被干净地呈现;其他异常用
  `logger.exception` 记录并作为 `isError` 返回。子进程代理会跳过本地参数过滤(由远程
  服务器校验),直接转发给 `session.call_tool`。

### `travel_agent/tools/*`
每个工具都是一个普通函数(同步或异步)。入参都是简单类型 —— 大模型的工具调用 JSON
schema 由签名推导而来。校验在函数内部完成,并以 `ValueError` 抛出(`MCPServer.call_tool`
会把它转成一个干净的 `"Invalid input"` 载荷)。

| 工具 | 作用 | 真实 API 来源 |
|---|---|---|
| `search_flights` | 实时航班报价 | Amadeus Self-Service v2/shopping/flight-offers |
| `book_flight` | 生成真实的 Aviasales 预订链接 + intent ref | Travelpayouts 联盟深链(无需商业协议) |
| `search_hotels` | 真实酒店报价;API 失败时回退为仅深链 | Amadeus Hotel Search v3(`hotels/by-city` + `hotel-offers`) |
| `rent_car` | 计算价格预估 + 真实 RentalCars 链接 | RentalCars + Travelpayouts 深链 |
| `get_forecast` | 14 天内实时预报;超出则用去年同日期归档代理;任意城市地理编码 | Open-Meteo(无需 key) |
| `create_payment_session` | 托管的 Stripe Checkout 链接 | Stripe Checkout(依 `STRIPE_MODE` 真实/mock) |
| `get_payment_status` | 从 Stripe + 本地缓存刷新会话状态 | Stripe Checkout |
| `get_current_datetime` | 供大模型推理用的当前日期/时间 | 本地时钟 |

`flights.py` 和 `hotels.py` 共享一个 `AmadeusTokenCache`(带异步锁的 OAuth token 缓存,
在过期前 60 秒刷新以避免 thundering herd)。

当设置了 `GOOGLE_MAPS_API_KEY` 时,会从
`@modelcontextprotocol/server-google-maps` 子进程新增七个工具:`maps_geocode`、
`maps_reverse_geocode`、`maps_directions`、`maps_distance_matrix`、
`maps_search_places`、`maps_place_details`、`maps_elevation`。它们在应用启动时通过
`tools/list` 被发现,并作为代理分发 —— 大模型对待它们与进程内工具完全一致。

### `travel_agent/payments/*`
一个小巧的支付包,带有清晰的 provider 边界:

- `models.py` —— `CheckoutRequest`(Pydantic;校验金额、货币白名单、邮箱格式)、
  `CheckoutResponse`、`PaymentRecord`(服务端状态)、`PaymentStatus` 枚举。
- `stripe_client.py` —— `StripeClient`(真实)和 `StripeMockClient`
  (内存实现,用于 `STRIPE_MODE=mock` 与测试)。两者实现同一协议:
  `create_checkout_session`、`retrieve_session`、`verify_webhook`。真实客户端会把每个
  `stripe.error.*` 映射为一小组对用户安全的 `PaymentProviderError` 消息,这样原始异常
  绝不会泄露到聊天输出中。`build_stripe_client()` 依据 `Config.STRIPE_MODE` 选择实现。
- `service.py` —— `PaymentService` 持有内存中的 `PaymentRecord` 存储
  (按 booking_id 和 session_id)以及已处理的 webhook 事件 ID 集合。两项保证:
  - **幂等性**:用相同 `booking_id` 重复调用 `create_checkout` 会返回已有会话。
  - **Webhook 去重**:每个 `event.id` 至多处理一次。

  所有会修改状态的路径都由一个 `asyncio.Lock` 保护。

### `travel_agent/cli.py`
一个轻量的交互式 REPL。加载配置、通过 `setup.build_agent` 构建智能体、把流式事件
打印到标准输出。

### `static/`
聊天界面(原生 HTML/CSS/JS,无构建步骤)。`static/js/app.js`:

- 生成一个按对话隔离的 ID(`currentConversationId`),并在每次 `POST /api/chat` 时作为
  `X-Session-Id` 请求头发送。这让服务端的 `SessionManager` 与用户可见的线程保持一致 ——
  新线程获得新记忆;已有线程恢复完整历史。
- 渲染助手消息时,自动把 Markdown 风格的 `[text](url)` 和纯 `http(s)://` URL 变成链接。
- 自动在新标签页打开任何 host 匹配 `aviasales.com`、`hotellook.com`、`rentalcars.com`
  或 `checkout.stripe.com` 的 URL,让用户无需额外点击就落到合作方结账页。受浏览器
  弹窗拦截规则影响 —— 内联链接是始终可点击的兜底。

---

## 请求生命周期:`POST /api/chat`

```
[客户端] ──multipart── webserver.chat ──┬─► 校验 message 非空(为空则 400)
                                        │
                                        ├─► 若有文件:读取至多 MAX_UPLOAD_MB 字节
                                        │      超大则拒绝(413)
                                        │      嗅探魔数(PDF / DOCX / TXT)
                                        │      不匹配则拒绝(415)
                                        │
                                        ├─► SessionManager.get_or_create(session_id)
                                        │      创建按会话隔离的 AgentOrchestrator
                                        │      (共享 LLM + MCPServer,全新记忆)
                                        │
                                        └─► async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                                                async for event in agent.run_generator(...):
                                                    yield NDJSON 行

agent.run_generator 的轮次循环:
  1. langfuse_trace(...)                       # 可观测性(关闭时为空操作)
  2. 若有附件则抽取文档                          # DocumentProcessor
  3. memory.add_message(user_message)
  4. while turn < MAX_TURNS:
     - messages = [system + memory.get_messages()]
     - response = async_retry(llm.call_tool, attempts=MAX_LLM_RETRIES)
     - 若有文本则 yield {type: "message", content}
     - 若无 tool_calls:break
     - 对每个 tool_call:
         yield {type: "tool_call", ...}
         result = async_retry(server.call_tool, attempts=MAX_TOOL_RETRIES)
         yield {type: "tool_result", ...}
         memory.add_message(工具结果)
  5. trace.end(); langfuse_flush()
```

暴露给客户端的错误都是通用字符串(完整堆栈留在日志里)。超时时,会追加一行
`{"type": "error", "content": "Response timed out. Please retry."}` 并关闭流。

---

## Webhook 生命周期:`POST /webhooks/stripe`

```
Stripe ── 原始请求体 + Stripe-Signature 头 ──▶ /webhooks/stripe
                                                       │
                                                       ▼
                                       PaymentService.verify_webhook
                                       (= StripeClient.verify_webhook,
                                        构造并校验 Event)
                                                       │
                                  签名错误?────────────┴────► 400
                                                       │
                                                       ▼
                                       PaymentService.handle_webhook
                                         - 按 event.id 去重
                                         - 按 session_id 查 PaymentRecord
                                         - 把 event.type → PaymentStatus 映射
                                              checkout.session.completed → SUCCEEDED
                                              .async_payment_failed       → FAILED
                                              .expired                    → EXPIRED
                                              payment_intent.payment_failed → FAILED
                                         - record.updated_at = now
                                                       │
                                                       ▼
                                                  {received: true}
```

智能体在一次轮次中绝不直接扣款。流程是:

1. 智能体调用 `create_payment_session(...)` 并把 `url` 返回给用户。
2. 用户在 Stripe 的托管页面付款(SCA/3DS 由 Stripe 处理)。
3. Stripe 向 `/webhooks/stripe` 触发 `checkout.session.completed`。
4. 下次智能体调用 `get_payment_status(session_id)` 时,会看到 `SUCCEEDED`,即可在
   聊天中确认。

---

## 关键设计决策

### 为什么用托管/联盟预订,而不是真实 PNR?
真正在 Hertz/Hilton/Lufthansa 扣款并预留的真实预订,需要与每个供应商(或 Amadeus
生产环境)签**署商业协议**。这是商业门槛,不是技术门槛。托管/联盟模式(Kayak、
Skyscanner、Hopper、Google Flights 的联盟路径都在用)是没有这些合同的开发者唯一
可走的端到端真实路径 —— 而且它是真实预订,只是在合作方站点完成,归因让我们赚取佣金。

### 既然预订不收费,为什么还留着 Stripe?
用于运营方想直接收取的任何服务费、礼宾订阅或高级增值。Checkout 模式对聊天智能体来说
是正确选择:智能体把一个托管 URL 交给用户,用户付款,webhook 在服务端完成收尾。
银行卡数据绝不经过我们的服务器(PCI 范围 = 0)。

### 为什么既有进程内工具,又有 stdio MCP 子进程?
model-context-protocol 规范假设工具位于独立进程,智能体通过 stdio 与之通信。当工具
本就以 MCP 服务器形式存在时(Google Maps、文件系统、Postgres),这就是正确答案 ——
你获得跨语言互操作、隔离,而且上游维护者替你发布修复。

对于我们自有的工具(航班、酒店、租车、天气、支付),为每个工具启动一个子进程只会增加
延迟、进程管理和序列化开销,没有好处 —— 它们是同一仓库里的 Python 函数。进程内注册表
处理这些;`register_mcp_subprocess` 处理外部的。两者都填充同一个 `MCPServer.tools`
字典,因此编排器和大模型看到的是一份统一的工具列表。

因为 `mcp/protocol.py` 的 Pydantic 模型与线上格式一致,反方向的大门也保持敞开:把
**我们的**工具暴露**成**一个 MCP 服务器,让 Claude Desktop / 其他智能体调用它们,是
增量工作,而非重写。

### 为什么按会话隔离编排器?
记忆按会话隔离,因为对话不在用户之间共享。LLM + MCPServer 是无状态且共享的(代价:
一个 LLM 客户端对象、一个工具注册表)。当超过 `MAX_SESSIONS` 时,会话按 TTL + LRU
淘汰 —— 有界的内存占用,而第一天就无需为 Redis/DB 付费。

### 为什么自写 `async_retry` 而不用 tenacity?
我们只在两处需要重试(LLM 调用、工具调用),且需要一致的日志形态
(`extra={"request_id": ...}`、`exc_info=True`)。25 行、零依赖、无学习成本。

### 为什么要滑动窗口式记忆上限?
否则长时间运行的会话会无界增长 —— 无论是内存还是 prompt 的 token 成本。窗口很小
(默认 50 条消息,可配置)。若后续想改成摘要压缩,可以子类化 `AgentMemory` 而不动
编排器。

### 为什么把系统提示词外置?
三个原因:(1)无需改 Python 文件即可编辑;(2)可从磁盘加载,供需要校验提示词内容的
测试使用;(3)为通过环境变量对不同提示词做 A/B 测试扫清障碍。

### 为什么用 `StripeMockClient` 而不是 mock 掉 `stripe.checkout.Session.create`?
在 SDK 边界处 mock 会把测试与 SDK 的接口耦合,SDK 升级时就会失效。
`StripeClient` / `StripeMockClient` 协议是**我们的**边界、由我们掌控,并带一个
`simulate_completion` 辅助函数为测试构造逼真的 webhook 事件。同样的模式适用于未来
任何 provider(PayPal、Adyen)—— 实现该协议即可,无需改测试。

---

## 扩展点

### 新增一个工具
1. 在 `travel_agent/tools/<name>.py` 里写一个入参为简单类型的函数。对非法输入抛出
   `ValueError`。
2. 从 `travel_agent/tools/__init__.py` 重新导出。
3. 在 `travel_agent/setup.py::build_mcp_server` 中注册它。
4. 在 `tests/test_tools_<name>.py` 中加测试。
5. 若不直观,在 `travel_agent/agent/prompts/system.md` 中补充指引。

### 新增一个外部 MCP 服务器
当工具已以 MCP 服务器形式存在(说 stdio JSON-RPC 的 Node/Python 子进程)时,无需任何
工具代码 —— 把子进程接入 `travel_agent/setup.py::attach_external_mcp_servers`:

```python
if Config.MY_KEY:
    await server.register_mcp_subprocess(
        command="npx",
        args=["-y", "@some-org/server-name"],
        env={"MY_KEY": Config.MY_KEY},
        label="my-server",
    )
```

把 `MY_KEY` 加到 `Config`(在 `travel_agent/config.py`)以及 `.env.example`。保持注册
受 key 控制,这样没有该 key 的全新克隆仍能启动 —— `attach_external_mcp_servers` 用
try/except 包住失败,配置错误的子进程绝不会让应用崩溃。工具发现(`tools/list`)与分发
(`tools/call`)自动发生;远程工具会与进程内工具一起出现在 `MCPServer.list_tools()` 中。

### 新增一个 LLM provider
1. 在 `travel_agent/agent/llm.py` 中子类化 `LLMProvider`。实现 `generate_text` 和
   `call_tool` 两者。
2. 在 `get_llm_provider` 中加一个分支。
3. 在 `tests/test_llm_providers.py` 中加"构建冒烟 + 工具转换"测试。

### 新增一个支付 provider
1. 实现 `travel_agent/payments/stripe_client.py` 里的 `StripeClientProtocol` 形态 ——
   `create_checkout_session`、`retrieve_session`、`verify_webhook`。
2. 在一个新的 `STRIPE_MODE` 值下(或改名 —— 例如 `PAYMENT_PROVIDER`)把它接入
   `build_stripe_client()`。
3. `PaymentService` 无需改动。

### 新增持久化(记忆 + 支付)
`AgentMemory` 和 `PaymentService` 都把状态藏在接口之后。要换成 Postgres/Redis:
- 子类化 `AgentMemory`,实现由数据库支撑的 `add_message` / `get_messages` / `clear`。
- 把 `PaymentService` 里的 `_by_booking` / `_by_session` 字典换成一个方法相同
  (`get_by_booking`、`get_by_session`、`upsert`)的小型 `PaymentStore` 接口。

两处改动都是局部的 —— 编排器、Web 服务器和工具都不受影响。

---

## 运维关注点

### 日志
- 仅 JSON,通过 `JsonFormatter`(`travel_agent/config.py`)。
- 每条记录都带 `timestamp`、`level`、`message`、`module`、`function`。
- `request_id` 和 `session_id` 通过 `logger.info/warning/error/exception` 调用上的
  `extra={...}` 传播。
- PII(邮箱、≥ 8 位连续数字)在任何字段到达 Langfuse 或聊天错误消息之前,由
  `orchestrator.py` 中的 `_redact_pii` 脱敏。

### 密钥
- 全部通过 `python-dotenv` 从 `.env` 加载(导入时读取一次)。
- `.gitignore` 阻止仓库根目录的 `/.env` 入库;`.env.example` 是模板。
- 当当前 `STRIPE_MODE` 所需的 key 缺失时,`Config.validate()` 抛出 `ConfigError`;
  `web_server.py` 在 `live` 模式下会主动调用它,缺少任何必需 key 就拒绝启动。

### 会话
- 以 `X-Session-Id` 请求头为键;缺失则生成。
- `SessionManager` 为每个会话持有一个 `(timestamp, orchestrator)` 字典。
- 按 TTL(`SESSION_TTL_SECONDS`)以及超过 `MAX_SESSIONS` 时的 LRU 淘汰。
- 每个会话的记忆相互独立;LLM + MCPServer 共享。

### 可观测性
- Langfuse 可选(无 key → 空操作)。封装层兼容 SDK v2 和 v3。
- 每个智能体轮次一个 trace(`name="agent-turn"`);每次 LLM 调用一个 generation
  (`name="llm-call"`,含模型 + token 数)。
- 可观测性中的失败绝不打断轮次 —— 它们被记录,trace 变成 `None`。

### 健康 / 就绪
- `/healthz` 是存活探针 —— 进程有响应就始终 200。
- `/readyz` 在降级为 `MockSessionManager`(无 LLM key)时返回 503,否则 200。
- Docker `HEALTHCHECK` 每 30 秒打一次 `/healthz`。

### CI
- `.github/workflows/ci.yml` 在 Python 3.11 + 3.12 上运行 `pytest --cov=travel_agent
  --cov-fail-under=70`,外加一个依赖测试通过的 `docker build` 任务。
