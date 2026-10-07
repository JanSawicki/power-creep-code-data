#!/usr/bin/env python3
"""Resolve the vLLM judge endpoint, auto-detecting a running SLURM job.

The vLLM server runs on whichever HPC node SLURM assigns (see jobs/vllm.job),
so the URL is not fixed. resolve_llm_url() figures out the live endpoint by
probing, in priority order:

  1. An explicit URL (e.g. from --llm-url).
  2. $VLLM_LLM_URL (set by `source vllm.endpoints`).
  3. The VLLM_LLM_URL line in ./vllm.endpoints (written by jobs/vllm.job).
  4. The node of a RUNNING SLURM job named "vllm" (from squeue).

The first candidate that actually answers on /v1/models wins. If none answer,
it raises RuntimeError with instructions to start the job manually.
"""

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

REPO = Path(__file__).parent.parent
DEFAULT_PORT = 18000


def _probe(url: str, timeout: int = 5) -> bool:
    """True if the vLLM server at `url` answers on /models."""
    try:
        req = urllib.request.Request(
            f"{url}/models", headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.loads(resp.read())
        return True
    except Exception:
        return False


def _squeue_vllm_node() -> str | None:
    """Node name of the user's RUNNING SLURM job named 'vllm', or None."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        out = subprocess.run(
            ["squeue", "-u", user, "-h", "-o", "%j|%T|%N"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        name, state, node = (p.strip() for p in parts)
        if name == "vllm" and state == "RUNNING" and node:
            return node
    return None


def resolve_llm_url(explicit: str | None = None, port: int = DEFAULT_PORT,
                    repo: Path = REPO, verbose: bool = True) -> str:
    """Return a reachable vLLM endpoint URL, or raise RuntimeError with guidance.

    An explicit URL is trusted without probing (the user forced it). Otherwise
    each auto-detected candidate is probed and the first live one is returned.
    """
    if explicit:
        return explicit

    candidates: list[tuple[str, str]] = []

    env_url = os.environ.get("VLLM_LLM_URL")
    if env_url:
        candidates.append(("$VLLM_LLM_URL", env_url))

    ep_file = repo / "vllm.endpoints"
    if ep_file.exists():
        m = re.search(r"VLLM_LLM_URL=(\S+)", ep_file.read_text())
        if m:
            candidates.append(("vllm.endpoints", m.group(1)))

    node = _squeue_vllm_node()
    if node:
        candidates.append(("squeue vllm job", f"http://{node}:{port}/v1"))

    seen: set[str] = set()
    for source, url in candidates:
        if url in seen:
            continue
        seen.add(url)
        if _probe(url):
            if verbose:
                print(f"vLLM detected via {source}: {url}")
            return url

    checked = ", ".join(f"{s}={u}" for s, u in candidates) or "none found"
    raise RuntimeError(
        "vLLM server is not reachable (checked: " + checked + ").\n"
        "It does not appear to be running. Start it manually with:\n"
        "    sbatch jobs/vllm.job\n"
        "then, once the job is RUNNING and the model has loaded:\n"
        "    source vllm.endpoints"
    )
