#!/usr/bin/env python3
"""Empirical context-window benchmark for local LLM servers (TGI / vLLM / OpenAI-compatible).

Designed for setups where the model card says 128k but the *serving* stack caps
much lower (common with TGI --max-total-tokens, VRAM limits, eGPU paths, etc.).

Usage (from repo root):
  venv\\Scripts\\python scripts\\benchmark_context.py
  venv\\Scripts\\python scripts\\benchmark_context.py --base-url http://127.0.0.1:8000/v1 --agent-sim

The script:
  1. Reads advertised limits from /info (TGI) and /v1/models when available
  2. Binary-searches the largest *input* that still returns HTTP 200
  3. Optionally adds a fixed Odysseus-like agent system prompt overhead
  4. Prints Odysseus settings recommendations
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import httpx
except ImportError:
    print("httpx is required. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)


# Rough agent tool preamble size (chars). Real Odysseus agent prompts are larger;
# this is a conservative lower bound for "can agent mode possibly fit?".
AGENT_SIM_SYSTEM_CHARS = 28_000


@dataclass
class ServerLimits:
    backend: str = "unknown"
    model_id: str = ""
    max_input_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    raw_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeResult:
    target_chars: int
    ok: bool
    status: int
    input_tokens: Optional[int]
    latency_s: float
    detail: str


def _base_host(base_url: str) -> str:
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3]
    return u.rstrip("/")


def _chat_url(base_url: str) -> str:
    u = base_url.rstrip("/")
    return u if u.endswith("/chat/completions") else f"{u}/chat/completions"


def _models_url(base_url: str) -> str:
    u = base_url.rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    if not u.endswith("/v1"):
        u = f"{u}/v1" if "/v1" not in u else u
    return f"{u}/models"


def fetch_server_limits(client: httpx.Client, base_url: str, timeout: float) -> ServerLimits:
    limits = ServerLimits()
    host = _base_host(base_url)

    try:
        r = client.get(f"{host}/info", timeout=timeout)
        if r.is_success:
            info = r.json()
            limits.backend = "tgi"
            limits.raw_info = info
            limits.model_id = str(info.get("model_id") or "")
            limits.max_input_tokens = _as_int(info.get("max_input_tokens"))
            limits.max_total_tokens = _as_int(info.get("max_total_tokens"))
            return limits
    except Exception:
        pass

    try:
        r = client.get(_models_url(base_url), timeout=timeout)
        if r.is_success:
            data = r.json()
            models = data.get("data") or []
            if models:
                m = models[0]
                limits.model_id = str(m.get("id") or "")
                for key in (
                    "max_model_len",
                    "context_length",
                    "context_window",
                    "max_context_length",
                    "max_seq_len",
                ):
                    val = _as_int(m.get(key))
                    if val:
                        limits.max_total_tokens = val
                        break
                limits.backend = "openai-compatible"
    except Exception:
        pass

    return limits


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    return None


def parse_input_tokens(text: str) -> Optional[int]:
    """Parse TGI/vLLM validation errors for actual input token counts."""
    patterns = [
        r"Given:\s*(\d+)\s*`?inputs`?",
        r"Given:\s*(\d+)\s+inputs",
        r"input.*?(\d+)\s+tokens",
        r"prompt.*?(\d+)\s+tokens",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return int(m.group(1))
    return None


def filler_text(char_count: int) -> str:
    """Generate deterministic filler. English prose ≈ 0.25–0.35 tokens/char."""
    word = "benchmark "
    reps = max(1, char_count // len(word))
    text = (word * reps)[:char_count]
    return text.strip() or "benchmark"


def probe_once(
    client: httpx.Client,
    chat_url: str,
    model: str,
    char_count: int,
    max_tokens: int,
    system_extra: str,
    timeout: float,
) -> ProbeResult:
    user = filler_text(char_count)
    messages = []
    if system_extra:
        messages.append({"role": "system", "content": system_extra})
    messages.append({"role": "user", "content": user})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }

    t0 = time.perf_counter()
    try:
        r = client.post(chat_url, json=payload, timeout=timeout)
        latency = time.perf_counter() - t0
        body = r.text
        input_tokens = None
        if r.is_success:
            try:
                data = r.json()
                usage = data.get("usage") or {}
                input_tokens = _as_int(usage.get("prompt_tokens"))
            except Exception:
                pass
        else:
            input_tokens = parse_input_tokens(body)

        detail = body[:240].replace("\n", " ")
        return ProbeResult(char_count, r.is_success, r.status_code, input_tokens, latency, detail)
    except httpx.TimeoutException:
        return ProbeResult(char_count, False, 0, None, timeout, "timeout")
    except Exception as exc:
        return ProbeResult(char_count, False, 0, None, time.perf_counter() - t0, str(exc))


def calibrate_chars_per_token(
    client: httpx.Client,
    chat_url: str,
    model: str,
    timeout: float,
) -> float:
    """Send a medium prompt; use server-reported tokens to calibrate generator."""
    chars = 4000
    for _ in range(4):
        res = probe_once(client, chat_url, model, chars, max_tokens=1, system_extra="", timeout=timeout)
        if res.input_tokens and res.input_tokens > 0:
            return chars / res.input_tokens
        if res.ok:
            return 4.0
        chars = max(500, chars // 2)
    return 4.0


def measure_overhead_tokens(
    client: httpx.Client,
    chat_url: str,
    model: str,
    system_extra: str,
    timeout: float,
) -> int:
    """Tokens consumed by system/agent preamble with a tiny user message."""
    if not system_extra:
        return 0
    res = probe_once(client, chat_url, model, 32, max_tokens=1, system_extra=system_extra, timeout=timeout)
    if res.input_tokens:
        return max(0, res.input_tokens - 8)
    return 0


def binary_search_max_input(
    client: httpx.Client,
    chat_url: str,
    model: str,
    max_total: int,
    max_input: Optional[int],
    max_tokens_out: int,
    chars_per_token: float,
    system_extra: str,
    timeout: float,
) -> Tuple[int, List[ProbeResult]]:
    """Return (max_input_tokens_observed, probe_log)."""
    # Search in token space, convert to chars for payload generation.
    budget = max(256, max_total - max_tokens_out - 32)
    if max_input:
        budget = min(budget, max_input)
    overhead = measure_overhead_tokens(client, chat_url, model, system_extra, timeout)
    budget = max(64, budget - overhead)
    lo_tok, hi_tok = 64, budget
    best_ok = 0
    log: List[ProbeResult] = []

    while lo_tok <= hi_tok:
        mid_tok = (lo_tok + hi_tok) // 2
        chars = max(64, int(mid_tok * chars_per_token))
        res = probe_once(
            client, chat_url, model, chars, max_tokens_out, system_extra, timeout
        )
        log.append(res)
        actual = res.input_tokens or mid_tok

        if res.ok:
            best_ok = max(best_ok, actual)
            lo_tok = mid_tok + 1
        else:
            hi_tok = mid_tok - 1

    return best_ok, log


def agent_sim_prompt() -> str:
    return (
        "You are an autonomous coding agent with file, shell, and web tools. "
        "Follow workspace confinement. Never delete files outside the workspace. "
        + filler_text(AGENT_SIM_SYSTEM_CHARS)
    )


def recommend_odysseus_settings(
    max_input_plain: int,
    max_total: int,
    agent_overhead_tokens: int,
) -> Dict[str, Any]:
    usable_agent = max(0, max_input_plain - agent_overhead_tokens - 256)
    return {
        "agent_input_token_budget": max(1500, min(usable_agent, 6000)),
        "notes": (
            f"Plain chat fits ~{max_input_plain} input tokens (with ~512 reserved for output). "
            f"Agent mode needs ~{agent_overhead_tokens}+ tokens of tool/system overhead; "
            f"budget ~{usable_agent} tokens for your messages+history."
        ),
        "max_total_tokens_server": max_total,
    }


def print_report(
    limits: ServerLimits,
    model: str,
    chars_per_token: float,
    plain_max_in: int,
    agent_max_in: int,
    agent_overhead: int,
    settings: Dict[str, Any],
) -> None:
    print()
    print("=" * 72)
    print("CONTEXT BENCHMARK REPORT")
    print("=" * 72)
    print(f"  Backend           : {limits.backend}")
    print(f"  Model             : {model or limits.model_id or '(unknown)'}")
    print(f"  Server max input  : {limits.max_input_tokens or 'n/a'}")
    print(f"  Server max total  : {limits.max_total_tokens or 'n/a'}")
    print(f"  Calibrated        : {chars_per_token:.2f} chars/token (from live server)")
    print()
    print("  Empirical max INPUT tokens (HTTP 200, 512 tokens reserved for output):")
    print(f"    Plain chat      : {plain_max_in}")
    print(f"    Agent-sim chat  : {agent_max_in}  (includes ~{agent_overhead} token tool preamble)")
    print()
    print("  Suggested Odysseus data/settings.json values:")
    print(f"    agent_input_token_budget: {settings['agent_input_token_budget']}")
    print()
    print(f"  {settings['notes']}")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local LLM context limits")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/v1",
        help="OpenAI-compatible base URL (default: http://127.0.0.1:8000/v1)",
    )
    parser.add_argument("--model", default="", help="Model id (auto-detect from /info or /v1/models)")
    parser.add_argument(
        "--max-tokens-out",
        type=int,
        default=512,
        help="Output tokens to reserve during probes (default: 512)",
    )
    parser.add_argument(
        "--agent-sim",
        action="store_true",
        help="Also benchmark with a large agent-style system prompt",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout seconds")
    parser.add_argument("--json-out", default="", help="Write full results JSON to this path")
    args = parser.parse_args()

    chat_url = _chat_url(args.base_url)
    print(f"Probing {chat_url} ...")

    with httpx.Client() as client:
        limits = fetch_server_limits(client, args.base_url, args.timeout)
        model = args.model or limits.model_id
        if not model:
            try:
                r = client.get(_models_url(args.base_url), timeout=args.timeout)
                if r.is_success:
                    models = r.json().get("data") or []
                    if models:
                        model = models[0].get("id", "")
            except Exception:
                pass
        if not model:
            print("Could not detect model id. Pass --model explicitly.", file=sys.stderr)
            return 1

        max_total = limits.max_total_tokens or 8192
        print(f"Detected: backend={limits.backend}, model={model}, max_total={max_total}")

        print("Calibrating chars/token...")
        cpt = calibrate_chars_per_token(client, chat_url, model, args.timeout)
        print(f"  -> {cpt:.2f} chars/token")

        print("Binary search: plain chat max input...")
        plain_max, plain_log = binary_search_max_input(
            client,
            chat_url,
            model,
            max_total,
            limits.max_input_tokens,
            args.max_tokens_out,
            cpt,
            "",
            args.timeout,
        )

        agent_max = plain_max
        agent_overhead = 0
        if args.agent_sim:
            sim = agent_sim_prompt()
            agent_overhead = measure_overhead_tokens(client, chat_url, model, sim, args.timeout)
            print(f"  Agent preamble overhead: ~{agent_overhead} tokens")
            print("Binary search: agent-sim max user input...")
            agent_max, _ = binary_search_max_input(
                client,
                chat_url,
                model,
                max_total,
                limits.max_input_tokens,
                args.max_tokens_out,
                cpt,
                sim,
                args.timeout,
            )

        settings = recommend_odysseus_settings(plain_max, max_total, agent_overhead or 3500)
        print_report(limits, model, cpt, plain_max, agent_max, agent_overhead or 3500, settings)

        if args.json_out:
            out = {
                "limits": limits.__dict__,
                "model": model,
                "chars_per_token": cpt,
                "plain_max_input_tokens": plain_max,
                "agent_sim_max_input_tokens": agent_max,
                "odysseus_settings": settings,
                "plain_probe_count": len(plain_log),
            }
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            print(f"Wrote {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
