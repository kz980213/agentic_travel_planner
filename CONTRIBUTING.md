# 贡献指南

## 环境搭建

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env  # 填入你需要的 key
```

只需 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` 中的一个即可让智能体跑起来。
本地开发若没有 Stripe 凭证,在 `.env` 中设置 `STRIPE_MODE=mock`。

## 运行应用

```bash
# Web(聊天界面在 http://localhost:5000)
uvicorn web_server:app --reload --port 5000

# 命令行
python -m travel_agent.cli
```

## 测试

```bash
pytest                                              # 全部
pytest tests/test_orchestrator.py                   # 单个文件
pytest --cov=travel_agent --cov-report=term-missing # 带覆盖率
```

覆盖率目标为 **≥70%**,由 CI 强制。

测试使用:
- `pytest-asyncio`(auto 模式 —— `async def test_*` 无需 marker 即可运行)
- `respx` 用于 mock httpx 调用(Amadeus、Open-Meteo)
- `freezegun` 用于确定性的日期/时间

## 编写新工具

1. 把函数加到 `travel_agent/tools/<name>.py`。工具参数应为简单类型
   (str / int / float / bool / list / dict)—— schema 由签名推断。
2. 在函数开头校验输入,对非法输入抛出 `ValueError` —— `MCPServer` 会把它们干净地
   呈现给大模型。
3. 在 `travel_agent/tools/__init__.py` 中重新导出。
4. 在 `travel_agent/setup.py::build_mcp_server` 中注册。
5. 在 `tests/test_tools_<name>.py` 中加测试。
6. 若大模型需要"何时调用它"的指引,更新 `travel_agent/agent/prompts/system.md` 里的
   系统提示词。

## 新增外部 MCP 服务器

对于已以 MCP 服务器形式存在的工具(说 stdio JSON-RPC 的 Node/Python 子进程),你无需
编写任何工具代码 —— 只要把子进程接入 `travel_agent/setup.py` 里的
`attach_external_mcp_servers`:

```python
if Config.MY_NEW_SERVER_KEY:
    await server.register_mcp_subprocess(
        command="npx",
        args=["-y", "@some-org/server-name"],
        env={"MY_NEW_SERVER_KEY": Config.MY_NEW_SERVER_KEY},
        label="my-new-server",
    )
```

然后把 `MY_NEW_SERVER_KEY` 加到 `Config`(在 `travel_agent/config.py`)以及
`.env.example`。保持注册**受 key 控制**,这样没有该 key 的全新克隆仍能启动 ——
`attach_external_mcp_servers` 已经用 try/except 包住失败,配置错误的子进程绝不会让应用崩溃。

工具发现(`tools/list`)与分发(`tools/call`)自动进行 —— 远程工具会与进程内工具一起
出现在 `MCPServer.list_tools()` 中,对编排器和大模型来说无从区分。

## 新增 LLM provider

在 `travel_agent/agent/llm.py` 中子类化 `LLMProvider`,然后在 `get_llm_provider` 中加一个
分支。实现 `generate_text` 和 `call_tool` 两者。

## 预订架构

本应用**不会**为旅行库存本身扣客户的银行卡。预订通过联盟深链在合作方托管页面
(Aviasales、Hotellook、RentalCars)完成。Stripe Checkout(`create_payment_session`)
仅保留给**你自己**的收费 —— 服务费、礼宾订阅等。

完整的架构理由见 `docs/PRODUCTION_READINESS.md`。

## 代码风格

- 优先使用一行 docstring。把长解释留给不直观的代码。
- 使用 `logging.getLogger(__name__)`,绝不用 `print`(CI 日志会吞掉 stdout)。
- 不要在 `requirements.txt` 中不加版本上界就引入依赖。
