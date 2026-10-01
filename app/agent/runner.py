"""The agent loop: send, execute tool calls, return results, repeat until the record tool is accepted.

Guardrails live inside tool functions. A guardrail raises `Blocked`; the message goes back to the
model as a tool error and the model must correct itself. Every step is written to the audit log.
"""
from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass
from typing import Callable

from .. import db
from ..config import settings
from . import llm


class Blocked(Exception):
    """A guardrail rejected the model's tool call. The message is shown to the model."""


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], dict]
    terminal: bool = False  # accepted call ends the run


class AgentFailed(RuntimeError):
    pass


def _short(obj, limit=1500) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


def run_agent(case_id: str, stage: int, system: str, user: str, tools: list[Tool]) -> dict:
    """Runs one stage's agent. Returns the accepted terminal tool's result."""
    by_name = {t.name: t for t in tools}
    specs = [llm.ToolSpec(t.name, t.description, t.schema) for t in tools]
    cfg = settings
    if stage in settings.llm_stage_models:
        cfg = dataclasses.replace(settings, llm_model=settings.llm_stage_models[stage])
    convo = llm.conversation_factory(cfg, system, user, specs)
    db.add_event(case_id, stage, "model", "run_started", {"model": cfg.llm_model, "provider": cfg.llm_provider,
                                                          "tool_mode": cfg.llm_tool_mode})
    nudged = False
    for round_no in range(1, settings.max_agent_rounds + 1):
        t0 = time.time()
        turn = convo.send()
        ms = int((time.time() - t0) * 1000)
        if turn.text.strip():
            db.add_event(case_id, stage, "model", "says", {"text": _short(turn.text, 1200), "ms": ms})
        if not turn.tool_calls:
            if nudged:
                raise AgentFailed("The model stopped without recording a result.")
            nudged = True
            convo.add_user("You have not called the record tool yet. Call it now with your result.")
            continue
        results = []
        done = None
        for call in turn.tool_calls:
            tool = by_name.get(call.name)
            if tool is None:
                out, err = f"Unknown tool '{call.name}'. Available: {', '.join(by_name)}", True
                db.add_event(case_id, stage, "guard", call.name, {"args": call.arguments, "result": out})
            else:
                try:
                    res = tool.fn(call.arguments)
                    out, err = _short(res, 6000), False
                    db.add_event(case_id, stage, "tool", call.name, {"args": _public_args(call.arguments), "result": res})
                    if tool.terminal:
                        done = res
                except Blocked as b:
                    out, err = f"Blocked by guardrail: {b}", True
                    db.add_event(case_id, stage, "guard", call.name, {"args": _public_args(call.arguments), "result": str(b)})
                except Exception as e:  # a bug or bad arguments: tell the model, keep going
                    out, err = f"Tool error: {e}", True
                    db.add_event(case_id, stage, "guard", call.name, {"args": _public_args(call.arguments), "result": out})
            results.append((call, out, err))
        if done is not None:
            db.add_event(case_id, stage, "model", "run_finished", {"rounds": round_no})
            return done
        convo.add_tool_results(results)
    raise AgentFailed(f"No accepted result after {settings.max_agent_rounds} rounds.")


def _public_args(args: dict) -> dict:
    """Trim long text fields so the audit feed stays readable (the full value is stored on the case)."""
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + "…"
        elif isinstance(v, list) and len(v) > 12:
            out[k] = v[:12] + ["…"]
        else:
            out[k] = v
    return out
