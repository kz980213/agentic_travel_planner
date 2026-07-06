"""
本地演示:用一个"脚本化 LLM"驱动【真实的】AgentOrchestrator + 【真实的】工具。

目的:不需要任何付费 API key,就能看到 agent loop 的完整事件流:
    LLM 决定调工具 → 真实工具执行(search_flights 返回 mock 航班)→
    结果写回记忆 → LLM 再决定 → 给出最终答复。

把 ScriptedLLM 换成真实的 OpenAIProvider/AnthropicProvider/GoogleProvider,
行为完全一致 —— 这正是本项目"多模型适配"抽象的价值。

运行:  .venv/Scripts/python.exe scripts/demo_local_loop.py
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from travel_agent.agent.llm import LLMProvider
from travel_agent.agent.orchestrator import AgentOrchestrator
from travel_agent.agent.memory import InMemoryMemory
from travel_agent.setup import build_mcp_server


class ScriptedLLM(LLMProvider):
    """假装是大模型:第1轮决定调 search_flights,第2轮看到结果后给最终答复。

    真实 Provider 由模型自己决定;这里我们写死,好让你在没有 key 时也能
    观察【真实的 orchestrator 循环】和【真实的工具执行】。
    """
    model = "scripted-demo"

    async def generate_text(self, prompt, system_prompt=None):
        return "not used in this demo"

    async def call_tool(self, messages, tools):
        # 如果 memory 里已经出现过 tool 结果,就代表工具跑完了 -> 出最终答复
        already_ran_tool = any(m.get("role") == "tool" for m in messages)

        if not already_ran_tool:
            print(f"    (脚本LLM: 看到 {len(tools)} 个可用工具, 决定调用 search_flights)")
            return {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "name": "search_flights",
                    "arguments": {"origin": "PEK", "destination": "SHA", "date": "2026-07-20"},
                }],
            }

        # 第二轮:读取上一条 tool 结果,组织自然语言回复
        tool_msg = next(m for m in reversed(messages) if m.get("role") == "tool")
        flights = json.loads(tool_msg["content"])
        lines = ["为你找到以下 PEK→SHA (2026-07-20) 的航班:"]
        for i, f in enumerate(flights, 1):
            lines.append(f"{i}. {f['airline']}  {f.get('price')} {f.get('currency')}")
        lines.append("需要我帮你生成某一班的预订链接吗?")
        return {"content": "\n".join(lines), "tool_calls": None}


async def main():
    # 真实的工具服务器(8 个工具全部注册),真实的记忆
    server = build_mcp_server()
    orchestrator = AgentOrchestrator(ScriptedLLM(), server, InMemoryMemory())

    print("已注册的真实工具:", [t["name"] for t in server.list_tools()])
    print("=" * 68)

    user_input = "帮我查 2026-07-20 从北京(PEK)到上海(SHA)的航班"
    print(">>> 用户:", user_input)
    print("=" * 68)

    async for ev in orchestrator.run_generator(user_input, request_id="local-demo"):
        t = ev["type"]
        if t == "tool_call":
            print(f"[TOOL_CALL  ] {ev['name']}({json.dumps(ev['arguments'], ensure_ascii=False)})")
        elif t == "tool_result":
            print(f"[TOOL_RESULT] {ev['name']} (来自真实工具, err={ev.get('is_error')}):")
            print("             ", ev["content"][:400])
        elif t == "message":
            print(f"[MESSAGE    ]\n{ev['content']}")
        elif t == "error":
            print(f"[ERROR      ] {ev['content']}")


if __name__ == "__main__":
    asyncio.run(main())
