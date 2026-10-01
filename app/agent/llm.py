"""Model provider layer. The agent only talks to `Conversation`; swapping models is configuration.

Providers:
- anthropic       Claude via the Anthropic Messages API (native tool use)
- openai_compat   Any OpenAI-compatible /chat/completions endpoint: OpenAI, OpenRouter, Together,
                  self-hosted vLLM, Ollama. Works with open-weight models (Qwen, Llama, DeepSeek...).
Tool modes:
- native          The provider's own tool calling
- json            The model replies with {"tool": ..., "arguments": {...}} as plain text.
                  For models or servers without reliable tool calling.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Settings


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Conversation(Protocol):
    def send(self) -> Turn: ...
    def add_tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> None: ...
    def add_user(self, text: str) -> None: ...


class ProviderError(RuntimeError):
    pass


def plain_schema(schema):
    """Collapse ["string", "null"] style unions to one type. Some OpenAI-compatible servers
    (and stricter open-weight tool parsers) reject type arrays."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, list):
                non_null = [t for t in v if t != "null"]
                out[k] = non_null[0] if non_null else "string"
            else:
                out[k] = plain_schema(v)
        return out
    if isinstance(schema, list):
        return [plain_schema(x) for x in schema]
    return schema


# --------------------------------------------------------------------------- Anthropic
class AnthropicConversation:
    def __init__(self, s: Settings, system: str, user: str, tools: list[ToolSpec]):
        import anthropic

        if not s.llm_api_key:
            raise ProviderError("LLM_API_KEY is not set")
        self.client = anthropic.Anthropic(api_key=s.llm_api_key, base_url=s.llm_base_url or None)
        self.model = s.llm_model
        self.max_tokens = s.llm_max_tokens
        self.system = system
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools]
        self.messages: list[dict] = [{"role": "user", "content": user}]

    def send(self) -> Turn:
        try:
            r = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=self.system, messages=self.messages, tools=self.tools
            )
        except Exception as e:  # network, auth, rate limit
            raise ProviderError(f"Anthropic API error: {e}") from e
        content = [b.model_dump(exclude_none=True) for b in r.content]
        self.messages.append({"role": "assistant", "content": content})
        turn = Turn()
        for b in r.content:
            if b.type == "text":
                turn.text += b.text
            elif b.type == "tool_use":
                turn.tool_calls.append(ToolCall(b.id, b.name, dict(b.input or {})))
        return turn

    def add_tool_results(self, results):
        self.messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": err} for c, out, err in results
            ],
        })

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})


# --------------------------------------------------------------------------- rate limiting
class TokenThrottle:
    """Client-side tokens-per-minute budget, per model. Providers like Groq count input + output
    tokens per model per minute, so we wait here instead of burning calls on 429s."""

    def __init__(self):
        self.lock = threading.Lock()
        self.used: dict[str, deque] = defaultdict(deque)  # model -> (timestamp, tokens)

    def _spent(self, model: str, now: float) -> int:
        q = self.used[model]
        while q and now - q[0][0] > 60:
            q.popleft()
        return sum(n for _, n in q)

    def wait(self, model: str, need: int, budget: int) -> None:
        while True:
            with self.lock:
                now = time.time()
                spent = self._spent(model, now)
                if spent == 0 or spent + need <= budget:
                    self.used[model].append((now, need))  # reserve; corrected by record()
                    return
                sleep = max(1.0, 60 - (now - self.used[model][0][0]) + 0.5)
            time.sleep(min(sleep, 20))

    def record(self, model: str, reserved: int, actual: int) -> None:
        with self.lock:
            q = self.used[model]
            for i in range(len(q) - 1, -1, -1):
                if q[i][1] == reserved:
                    q[i] = (q[i][0], actual)
                    break


throttle = TokenThrottle()


def _retry_after(msg: str, attempt: int) -> float:
    m = re.search(r"try again in ([\d.]+)(ms|s|m)", msg)
    if m:
        v = float(m.group(1)) * {"ms": 0.001, "s": 1, "m": 60}[m.group(2)]
        return min(v + 1, 60)
    return min(2 ** attempt * 2, 30)


# --------------------------------------------------------------------------- OpenAI-compatible
class OpenAICompatConversation:
    def __init__(self, s: Settings, system: str, user: str, tools: list[ToolSpec]):
        from openai import OpenAI

        self.client = OpenAI(api_key=s.llm_api_key or "not-needed", base_url=s.llm_base_url or None, max_retries=0)
        self.model = s.llm_model
        self.max_tokens = s.llm_max_tokens
        self.fallbacks = [m.strip() for m in s.llm_fallback_model.split(",") if m.strip()]
        self.tpm_budget = s.llm_tpm_budget
        self.effort = s.llm_reasoning_effort
        self.tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": plain_schema(t.schema)}}
            for t in tools
        ]
        self.messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _create(self):
        kw = {}
        if self.effort in ("low", "medium", "high") and "gpt-oss" in self.model:
            kw["extra_body"] = {"reasoning_effort": self.effort}  # fewer hidden reasoning tokens
        est = len(json.dumps(self.messages)) // 3 + len(json.dumps(self.tools)) // 3 + self.max_tokens
        throttle.wait(self.model, est, self.tpm_budget)
        r = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=self.tools, tool_choice="auto", max_tokens=self.max_tokens, **kw
        )
        used = getattr(getattr(r, "usage", None), "total_tokens", None)
        if used:
            throttle.record(self.model, est, used)
        return r

    def send(self) -> Turn:
        fallbacks = [m for m in self.fallbacks if m != self.model]
        attempt = 0
        while True:
            try:
                r = self._create()
                break
            except Exception as e:
                msg = str(e)
                if "429" not in msg and "rate_limit" not in msg and "503" not in msg and "overloaded" not in msg.lower():
                    raise ProviderError(f"Model API error: {e}") from e
                attempt += 1
                too_large = "too large" in msg.lower()
                if not too_large and attempt <= 3:
                    time.sleep(_retry_after(msg, attempt))  # a short wait usually clears a per-minute limit
                elif fallbacks:
                    self.model = fallbacks.pop(0)  # each model has its own limits
                    attempt = 0
                else:
                    raise ProviderError(f"Model API error (rate limited on every model): {e}") from e
        m = r.choices[0].message
        calls = []
        for tc in m.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        self.messages.append({
            "role": "assistant",
            "content": m.content or "",
            **({"tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in calls
            ]} if calls else {}),
        })
        return Turn(text=m.content or "", tool_calls=calls)

    def add_tool_results(self, results):
        for c, out, err in results:
            self.messages.append({"role": "tool", "tool_call_id": c.id, "content": ("ERROR: " if err else "") + out})

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})


# --------------------------------------------------------------------------- JSON mode (any provider)
JSON_INSTRUCTIONS = """
You act by calling tools. You cannot call them natively, so reply with ONLY one JSON object per message:
  {"tool": "<tool name>", "arguments": { ... }}
or, when you are completely finished:
  {"final": "<one sentence>"}
No other text. After each call you will receive the tool's result and may call another tool.

Available tools:
"""


class JSONModeConversation:
    """Wraps plain text completion so models without tool calling can still drive the agent."""

    def __init__(self, s: Settings, system: str, user: str, tools: list[ToolSpec]):
        catalog = "\n".join(f"- {t.name}: {t.description}\n  arguments schema: {json.dumps(plain_schema(t.schema))}" for t in tools)
        self.s = s
        self.system = system + "\n\n" + JSON_INSTRUCTIONS + catalog
        self.messages: list[dict] = [{"role": "user", "content": user}]
        self.n = 0

    def _complete(self) -> str:
        s = self.s
        try:
            if s.llm_provider == "anthropic":
                import anthropic

                r = anthropic.Anthropic(api_key=s.llm_api_key, base_url=s.llm_base_url or None).messages.create(
                    model=s.llm_model, max_tokens=s.llm_max_tokens, system=self.system, messages=self.messages
                )
                return "".join(b.text for b in r.content if b.type == "text")
            from openai import OpenAI

            r = OpenAI(api_key=s.llm_api_key or "not-needed", base_url=s.llm_base_url or None).chat.completions.create(
                model=s.llm_model, messages=[{"role": "system", "content": self.system}, *self.messages], max_tokens=s.llm_max_tokens
            )
            return r.choices[0].message.content or ""
        except Exception as e:
            raise ProviderError(f"Model API error: {e}") from e

    def send(self) -> Turn:
        text = self._complete()
        self.messages.append({"role": "assistant", "content": text})
        obj = _first_json(text)
        if isinstance(obj, dict) and "tool" in obj:
            self.n += 1
            return Turn(text="", tool_calls=[ToolCall(f"json-{self.n}", str(obj["tool"]), dict(obj.get("arguments") or {}))])
        if isinstance(obj, dict) and "final" in obj:
            return Turn(text=str(obj["final"]))
        return Turn(text=text)

    def add_tool_results(self, results):
        parts = [f"Result of {c.name}{' (ERROR)' if err else ''}: {out}" for c, out, err in results]
        self.messages.append({"role": "user", "content": "\n\n".join(parts)})

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})


def _first_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


# --------------------------------------------------------------------------- factory
def open_conversation(s: Settings, system: str, user: str, tools: list[ToolSpec]) -> Conversation:
    if s.llm_tool_mode == "json":
        return JSONModeConversation(s, system, user, tools)
    if s.llm_provider == "anthropic":
        return AnthropicConversation(s, system, user, tools)
    if s.llm_provider in ("openai_compat", "openai", "openrouter", "vllm", "ollama"):
        return OpenAICompatConversation(s, system, user, tools)
    raise ProviderError(f"Unknown LLM_PROVIDER '{s.llm_provider}'")


# Tests replace this with a scripted fake.
conversation_factory = open_conversation
