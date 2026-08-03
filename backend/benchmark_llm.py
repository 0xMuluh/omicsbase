"""Simple local benchmark for prompt loading and client reuse.

Usage:
    python backend/benchmark_llm.py

Measures:
 - Time to call load_system_prompt repeatedly (cold then cached)
 - Time to get OpenAI client for same key/base_url repeatedly (creation then reuse)
"""

import time
from app.services import llm
from app.config import settings


def time_load_prompt(iterations: int = 5):
    print("Timing load_system_prompt")
    t0 = time.time()
    first = llm.load_system_prompt()
    t1 = time.time()
    print(f"First load: {t1 - t0:.4f}s")

    t = time.time()
    for _ in range(iterations):
        llm.load_system_prompt()
    t2 = time.time()
    print(f"{iterations} cached loads: {(t2 - t)/iterations:.6f}s avg")


def time_openai_client(api_key: str = "bench-key", base_url: str | None = None, iterations: int = 5):
    print("Timing _get_openai_client")
    t0 = time.time()
    c1 = llm._get_openai_client(api_key, base_url)
    t1 = time.time()
    print(f"First client create: {t1 - t0:.6f}s")

    t = time.time()
    for _ in range(iterations):
        llm._get_openai_client(api_key, base_url)
    t2 = time.time()
    print(f"{iterations} cached calls: {(t2 - t)/iterations:.6f}s avg")


if __name__ == "__main__":
    time_load_prompt()
    time_openai_client()
