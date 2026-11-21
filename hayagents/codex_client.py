"""Codex as the model behind Agent Framework, on a ChatGPT sign-in (no API key).

Agent Framework sends the chat and the tool list; Codex replies with text or tool calls;
Agent Framework runs the tools (through the gate). Codex's own tools are all switched off.
"""
import json

from agent_framework import (BaseChatClient, ChatMiddlewareLayer, ChatResponse, ChatResponseUpdate, Content,
                             FunctionInvocationLayer, Message)
from openai_codex import ApprovalMode, AsyncCodex, Sandbox

OFF = ["shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "image_generation", "browser_use",
       "computer_use", "view_image", "hooks", "skill_search", "tool_suggest"]
REPLY = {"type": "object", "additionalProperties": False, "required": ["text", "tool_calls"], "properties": {
    "text": {"type": "string"},
    "tool_calls": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                              "required": ["name", "arguments_json"],
                                              "properties": {"name": {"type": "string"},
                                                             "arguments_json": {"type": "string"}}}}}}


class CodexChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self, model, effort):
        super().__init__()
        self.model, self.effort = model, effort
        self.codex = self.thread = None
        self.system = self.sent = ""

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        if not stream:
            return self.ask(messages, options)

        async def one_update():                   # Codex answers in one piece
            response = await self.ask(messages, options)
            yield ChatResponseUpdate(role="assistant", contents=[*response.messages[0].contents,
                                                                 Content.from_usage(response.usage_details)])
        return self._build_response_stream(one_update())

    async def ask(self, messages, options) -> ChatResponse:
        tools = [t.to_json_schema_spec()["function"] for t in options.get("tools") or []
                 if hasattr(t, "to_json_schema_spec")]
        system = options.get("instructions") or ""
        if tools:
            system += ("\nAct only through these tools. To call them, list them in tool_calls (arguments as a "
                       "JSON string) with empty text; when done, answer in text with no tool_calls.\n" + json.dumps(tools))
        chat = transcript(messages)
        if not self.thread or system != self.system or not chat.startswith(self.sent):   # new conversation: new thread
            self.codex = self.codex or AsyncCodex()
            self.thread = await self.codex.thread_start(
                model=self.model, base_instructions=system, ephemeral=True, sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                config={"features": dict.fromkeys(OFF, False), "web_search": "disabled"})
            self.system, self.sent = system, ""
        new, self.sent = chat[len(self.sent):], chat  # same conversation: send only what's new, Codex remembers the rest
        result = await self.thread.run(new, effort=self.effort, output_schema=REPLY if tools else None)

        reply = json.loads(result.final_response) if tools else {"text": result.final_response, "tool_calls": []}
        contents = [Content.from_text(reply["text"])] if reply["text"] else []
        contents += [Content.from_function_call(call_id=f"{result.id}-{i}", name=c["name"], arguments=c["arguments_json"])
                     for i, c in enumerate(reply["tool_calls"])]
        used = result.usage.last
        return ChatResponse(messages=[Message(role="assistant", contents=contents)],
                            usage_details={"total_token_count": used.input_tokens + used.output_tokens})


def transcript(messages) -> str:
    """The chat so far as text."""
    lines = []
    for m in messages:
        for c in m.contents:
            if c.type == "text" and c.text:
                lines.append(f"[{m.author_name or m.role}] {c.text}")
            elif c.type == "function_call":
                lines.append(f"[you called {c.name}({c.arguments})]")
            elif c.type == "function_result":
                lines.append(f"[tool result] {c.result}")
    return "".join(line + "\n\n" for line in lines)
