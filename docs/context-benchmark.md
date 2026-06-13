# GPU and context-window benchmarking

How to fit a local LLM server to your GPU and tune Odysseus agent token budgets.
Use this when the model card advertises 128K context but your serving stack (TGI,
vLLM, llama.cpp, VRAM limits, eGPU bandwidth) caps much lower.

**Scripts:** `scripts/benchmark_context.py`, `scripts/find_max_context.py`  
**Results (local, gitignored):** `data/context_benchmark.json`, `data/max_context_sweep.json`

---

## When to run this

| Symptom | Likely cause |
|---------|----------------|
| Agent mode goes silent on long tasks | Serving `max_total_tokens` too low for tool schemas + history |
| Chat works, agent fails | Agent adds ~2–14K tokens of tool/system overhead |
| OOM or container restart after raising context | VRAM limit — step down one level in the sweep |
| Cookbook shows model “fits” but serve fails | Passthrough OK but CUDA/ROCm userspace missing — reinstall via **Cookbook → Dependencies** |

---

## Step 1 — Confirm GPU inside Docker

Cookbook and local serve only see GPUs Docker exposes to the container.

**NVIDIA:**

```bash
scripts/check-docker-gpu.sh
# If passthrough works:
scripts/check-docker-gpu.sh --enable-nvidia-overlay   # writes COMPOSE_FILE to .env
docker compose up -d --build
docker compose exec odysseus nvidia-smi -L
```

**AMD:**

```bash
scripts/check-docker-amd-gpu.sh
# Add COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml and RENDER_GID to .env
```

`nvidia-smi` (or `/dev/kfd` + `/dev/dri` on AMD) inside the container confirms
**device passthrough**, not that vLLM/llama.cpp was built with GPU support.

---

## Step 2 — Pick a model that fits VRAM (Cookbook)

1. Open Odysseus → **Cookbook**
2. Let it scan hardware (VRAM, system RAM, CPU)
3. Choose a model with a high **fit score** for your VRAM
4. Download and **Serve** (or point Settings at an existing host endpoint)

**VRAM scaling rule of thumb** (7B instruct, FP16/BF16):

| VRAM | Typical max context (TGI/vLLM) | Notes |
|------|-------------------------------|-------|
| 8 GB | 4K–8K total | Tight; prefer GGUF Q4 via llama.cpp |
| 12 GB | 8K–12K | 7B at 8K often stable |
| 16 GB | 12K–16K | Good for coding agents |
| 24 GB | 16K–32K+ | RTX 3090/4090 class; sweep empirically |
| 48 GB+ | 32K+ | Scale `CANDIDATES` in `find_max_context.py` |

Larger models or FP8/AWQ change these numbers — always verify with a sweep.

---

## Step 3 — Probe the running server (`benchmark_context.py`)

From repo root (venv or container with `httpx`):

```bash
python scripts/benchmark_context.py --base-url http://127.0.0.1:8000/v1 --agent-sim
```

**Docker Odysseus → host LLM server:**

```bash
python scripts/benchmark_context.py --base-url http://host.docker.internal:8000/v1 --agent-sim
```

The script:

1. Reads limits from `/info` and `/v1/models`
2. Binary-searches the largest input that still returns HTTP 200
3. Simulates agent system-prompt overhead
4. Prints recommended `agent_input_token_budget` for `data/settings.json`

Example output fields:

- `plain_chat_max_input` — usable user tokens in Chat mode
- `agent_user_budget` — safe user budget after tool overhead
- `agent_input_token_budget` — value to paste into Settings

---

## Step 4 — Sweep context limits on your compose file (`find_max_context.py`)

For **TGI / Text Generation Inference** (or any compose service with
`MAX_TOTAL_TOKENS`, `MAX_INPUT_LENGTH`, `MAX_BATCH_PREFILL_TOKENS`), this script
steps through candidate limits, restarts the server, checks GPU memory, and writes
the best stable config back to your compose file (after backing it up).

```bash
python scripts/find_max_context.py --compose /path/to/your/llm-docker-compose.yml
```

**Requirements:**

- `docker`, `nvidia-smi` on PATH (NVIDIA)
- `httpx` (`pip install httpx`)
- A running compose stack with an OpenAI-compatible server on port 8000 (adjust
  `BASE_URL` / `CONTAINER` at the top of the script if yours differ)

**Customize for your GPU** — edit `CANDIDATES` in `scripts/find_max_context.py`:

```python
# (max_total, max_input, max_batch_prefill) — stop when OOM or inference fails
CANDIDATES = [
    (8192, 4096, 4096),
    (10240, 5120, 5120),
    (12288, 6144, 6144),
    (16384, 8192, 8192),   # common stable point on 24 GB cards
    (20480, 10240, 10240), # try on 24 GB+ only
    (32768, 16384, 16384), # 48 GB+ class
]
```

**Stability criteria** (same as the reference RTX 3090 sweep):

- Container starts and `/info` reports the new limits
- Quick inference returns HTTP 200
- `nvidia-smi` shows **≥ 2 GB VRAM free** at the chosen level
- Empirical max-input probe succeeds without OOM

Results are saved to `data/max_context_sweep.json`.

> **Note:** `find_max_context.py` patches TGI-style env vars and command lines.
> If your server uses different names, adapt `patch_compose()` or set limits
> manually and use only `benchmark_context.py`.

---

## Step 5 — Apply settings in Odysseus

1. **Settings → Models** — add endpoint:
   - Host Ollama in Docker: `http://host.docker.internal:11434/v1`
   - Host TGI/vLLM: `http://host.docker.internal:8000/v1`
2. Set **Supports tools** per backend:
   - Ollama / vLLM with native tool calling: `true` for Qwen2.5+
   - TGI / older stacks: often `false` (fenced-block tools are more reliable)
3. **Settings** or `data/settings.json`:

```json
"agent_input_token_budget": 4577
```

Use the value from `benchmark_context.py --agent-sim`. Odysseus reads TGI `/info`
for `max_total_tokens` via `src/model_context.py` when available.

4. Restart Odysseus after editing settings:

```bash
docker compose restart odysseus
```

---

## Step 6 — Verify agent + workspace

1. Open `http://127.0.0.1:7000/workspace`
2. **+ → Workspace** → pick a folder under `/workspace` (your mounted host path)
3. Switch to **Agent** mode, enable **Shell**
4. New chat → short test: *List files in the workspace*
5. Confirm tool runs complete and replies arrive within expected latency

**Workspace mount:** set `WORKSPACE_HOST_PATH` in `.env` before `docker compose up`.
Inside the container, paths are always `/workspace/...` — never paste host paths
like `C:\Users\...` into agent prompts.

**Node projects:** scaffold with `npm install` / `npx` inside `/workspace` so
`node_modules` stays on the mounted volume. Do not copy `node_modules` across
host/container boundaries on Windows Docker.

---

## Reference: RTX 3090 24 GB (eGPU) example

Benchmarked June 2026 with `Qwen/Qwen2.5-Coder-7B-Instruct` on TGI (`vllm-server`,
port 8000) and Odysseus in Docker on port 7000.

| Setting | Before | After sweep |
|---------|--------|-------------|
| `MAX_TOTAL_TOKENS` | 8,192 | **16,384** |
| `MAX_INPUT_LENGTH` | 4,096 | **8,192** |
| Plain chat input | ~3.8K | **~7,667** tokens |
| Agent user budget | ~750 | **~4,577** tokens |
| GPU used at 16K | — | ~21 GB, **~3.3 GB free** |

On a **better card** (RTX 4090 24GB, 32GB+ workstation GPU), extend `CANDIDATES`
and re-run the sweep — do not assume 16K is the ceiling.

On a **smaller card** (8–12 GB), stop at the first failed level and use the
previous candidate. Prefer smaller quantizations (GGUF Q4/Q5) via Cookbook.

---

## Restore previous limits

If a higher context level causes instability:

```bash
cp your-compose.yml.bak-maxcontext your-compose.yml
docker compose -f your-compose.yml restart vllm-server
```

Lower `agent_input_token_budget` in `data/settings.json` and restart Odysseus.

---

## Agent context overhead (why 8K total breaks agent mode)

| Component | Approx. tokens |
|-----------|----------------|
| Full fenced-block system prompt (all tools) | ~9,700 |
| Compact system prompt (native tool calling) | ~4,350 |
| All native tool schemas (65 tools) | ~13,800 |

At **8K total**, the system prompt alone can exceed the window. At **16K+**,
Odysseus agent mode works when:

- **Tool-RAG** retrieves only relevant tools (not all 65)
- **Soft-trim** caps history to `agent_input_token_budget`
- Endpoint `supports_tools` matches the backend

---

## Fork / PR checklist

Before pushing to upstream or sharing a fork:

- [ ] `WORKSPACE_HOST_PATH` uses a generic path, not a personal Desktop
- [ ] No `data/`, `logs/`, `.env`, or benchmark JSON committed
- [ ] No test project folders (e.g. scaffold apps) in `workspace/`
- [ ] `docker-compose.yml` has no machine-specific bind mounts
- [ ] GPU overlay enabled only via `.env` / documented scripts, not hardcoded
