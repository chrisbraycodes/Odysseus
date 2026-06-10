#!/usr/bin/env python3
"""Find maximum stable TGI context limits for LLM-Local (RTX 3090 / eGPU).

Sweeps --max-total-tokens / --max-input-length on the vllm-server container,
verifies each level with live inference + GPU/container health checks, then
writes the best safe config back to LLM-Local/docker-compose.yml.

Does NOT touch the Odysseus git repo — only LLM-Local compose (backed up first).

Usage:
  venv\\Scripts\\python scripts\\find_max_context.py
  venv\\Scripts\\python scripts\\find_max_context.py --compose "F:\\Github Projects\\LLM-Local\\docker-compose.yml"
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import httpx
except ImportError:
    print("pip install httpx", file=sys.stderr)
    sys.exit(1)

DEFAULT_COMPOSE = Path(r"F:\Github Projects\LLM-Local\docker-compose.yml")
CONTAINER = "vllm-server"
BASE_URL = "http://127.0.0.1:8000"
RESULTS_PATH = Path(__file__).resolve().parents[1] / "data" / "max_context_sweep.json"

# Compose maintainer noted 12288+ OOM'd previously; sweep below that first.
CANDIDATES = [
    (8192, 4096, 4096),
    (9216, 4608, 4608),
    (10240, 5120, 5120),
    (11264, 5632, 5632),
    (12288, 6144, 6144),
    (14336, 7168, 7168),
    (16384, 8192, 8192),
]


@dataclass
class SweepResult:
    max_total: int
    max_input: int
    max_batch_prefill: int
    server_up: bool
    inference_ok: bool
    empirical_max_input: int
    gpu_used_mib: Optional[int]
    gpu_free_mib: Optional[int]
    error: str = ""


def run(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 600) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc)


def gpu_stats() -> Tuple[Optional[int], Optional[int]]:
    code, out = run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
        timeout=15,
    )
    if code != 0:
        return None, None
    line = out.strip().splitlines()[0] if out.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return None, None


def patch_compose(compose_path: Path, total: int, inp: int, prefill: int) -> None:
    text = compose_path.read_text(encoding="utf-8")
    text = re.sub(
        r"(- MAX_INPUT_LENGTH=)\d+",
        rf"\g<1>{inp}",
        text,
        count=1,
    )
    text = re.sub(
        r"(- MAX_TOTAL_TOKENS=)\d+",
        rf"\g<1>{total}",
        text,
        count=1,
    )
    text = re.sub(
        r"(- MAX_BATCH_PREFILL_TOKENS=)\d+",
        rf"\g<1>{prefill}",
        text,
        count=1,
    )
    cmd_pat = (
        r"(command: --model-id Qwen/Qwen2\.5-Coder-7B-Instruct --port 80 --hostname 0\.0\.0\.0 "
        r"--max-input-length )\d+( --max-total-tokens )\d+( --max-batch-prefill-tokens )\d+"
    )
    repl = rf"\g<1>{inp}\g<2>{total}\g<3>{prefill}"
    text, n = re.subn(cmd_pat, repl, text, count=1)
    if n != 1:
        raise RuntimeError("Could not patch vllm-server command line in compose file")
    compose_path.write_text(text, encoding="utf-8")


def restart_server(compose_path: Path, wait_s: int = 300) -> bool:
    run(["docker", "compose", "-f", str(compose_path), "stop", "vllm-server"], timeout=120)
    code, out = run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d", "vllm-server"],
        timeout=180,
    )
    if code != 0:
        print(out[-2000:])
        return False

    deadline = time.time() + wait_s
    with httpx.Client() as client:
        while time.time() < deadline:
            try:
                r = client.get(f"{BASE_URL}/info", timeout=5.0)
                if r.is_success:
                    info = r.json()
                    if info.get("max_total_tokens"):
                        return True
            except Exception:
                pass
            # Container exited?
            code2, ps = run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], timeout=10)
            if code2 == 0 and ps.strip() != "true":
                return False
            time.sleep(5)
    return False


def quick_inference(model: str, max_input: int, timeout: float = 120.0) -> Tuple[bool, int, str]:
    """Send a prompt sized to ~70% of max_input; return ok, empirical tokens, detail."""
    chars = max(500, int(max_input * 0.7 * 4.0))
    filler = ("token " * (chars // 6))[:chars]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": filler}],
        "max_tokens": 64,
        "temperature": 0,
        "stream": False,
    }
    try:
        with httpx.Client() as client:
            r = client.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=timeout)
            if not r.is_success:
                return False, 0, r.text[:300]
            usage = r.json().get("usage") or {}
            return True, int(usage.get("prompt_tokens") or 0), "ok"
    except Exception as exc:
        return False, 0, str(exc)


def empirical_max_input(model: str, max_total: int, max_input: int) -> int:
    """Reuse benchmark binary search logic inline (lightweight)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from scripts.benchmark_context import (
            binary_search_max_input,
            calibrate_chars_per_token,
            _chat_url,
        )
    except ImportError:
        from benchmark_context import (
            binary_search_max_input,
            calibrate_chars_per_token,
            _chat_url,
        )

    chat_url = _chat_url(f"{BASE_URL}/v1")
    with httpx.Client() as client:
        cpt = calibrate_chars_per_token(client, chat_url, model, 90.0)
        best, _ = binary_search_max_input(
            client, chat_url, model, max_total, max_input, 512, cpt, "", 90.0
        )
        return best


def test_level(compose_path: Path, total: int, inp: int, prefill: int) -> SweepResult:
    print(f"\n--- Testing max_total={total} max_input={inp} prefill={prefill} ---")
    patch_compose(compose_path, total, inp, prefill)
    up = restart_server(compose_path, wait_s=360)
    used, free = gpu_stats()
    if not up:
        return SweepResult(total, inp, prefill, False, False, 0, used, free, "server failed to start")

    with httpx.Client() as client:
        try:
            info = client.get(f"{BASE_URL}/info", timeout=10.0).json()
            model = info.get("model_id", "Qwen/Qwen2.5-Coder-7B-Instruct")
        except Exception as exc:
            return SweepResult(total, inp, prefill, True, False, 0, used, free, str(exc))

    ok, tok, detail = quick_inference(model, inp)
    if not ok:
        return SweepResult(total, inp, prefill, True, False, 0, used, free, detail)

    print("  Quick inference OK; measuring empirical max input...")
    try:
        emp = empirical_max_input(model, total, inp)
    except Exception as exc:
        emp = tok
        detail = f"empirical failed: {exc}"

    used2, free2 = gpu_stats()
    return SweepResult(total, inp, prefill, True, True, emp, used2 or used, free2 or free, detail)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    compose = args.compose
    if not compose.is_file():
        print(f"Compose not found: {compose}", file=sys.stderr)
        return 1

    backup = compose.with_suffix(".yml.bak-maxcontext")
    shutil.copy2(compose, backup)
    print(f"Backed up compose to {backup}")

    results: List[SweepResult] = []
    best: Optional[SweepResult] = None

    try:
        for total, inp, prefill in CANDIDATES:
            if args.dry_run:
                print(f"would test {total}/{inp}/{prefill}")
                continue
            res = test_level(compose, total, inp, prefill)
            results.append(res)
            print(
                f"  -> up={res.server_up} infer={res.inference_ok} "
                f"empirical_in={res.empirical_max_input} gpu_used={res.gpu_used_mib}MiB free={res.gpu_free_mib}MiB"
            )
            if res.server_up and res.inference_ok:
                best = res
            else:
                print("  Stopping sweep — this level failed.")
                break
    finally:
        if best and not args.dry_run:
            print(f"\nApplying best stable config: total={best.max_total} input={best.max_input}")
            patch_compose(compose, best.max_total, best.max_input, best.max_batch_prefill)
            restart_server(compose, wait_s=360)
        else:
            print("\nRestoring compose backup (no stable higher level found).")
            shutil.copy2(backup, compose)
            if not args.dry_run:
                restart_server(compose, wait_s=360)

    out = {
        "best": asdict(best) if best else None,
        "results": [asdict(r) for r in results],
        "compose_backup": str(backup),
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nResults: {RESULTS_PATH}")

    if best:
        print("\n" + "=" * 60)
        print("MAX STABLE CONTEXT (live-tested on your RTX 3090)")
        print("=" * 60)
        print(f"  max_total_tokens : {best.max_total}")
        print(f"  max_input_tokens : {best.max_input}")
        print(f"  plain chat input : ~{best.empirical_max_input} tokens (512 reserved for reply)")
        print(f"  GPU used         : {best.gpu_used_mib} MiB")
        print("=" * 60)
    return 0 if best else 1


if __name__ == "__main__":
    raise SystemExit(main())
