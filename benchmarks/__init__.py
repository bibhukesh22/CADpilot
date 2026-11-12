"""CADPilot Benchmark Dataset Package.

Provides structured engineering benchmark prompts (Prompts 1-30) and loader
utilities covering mounting plates, circular flanges, L-brackets, standoffs,
and self-correction edge cases.
"""

from .loader import (
    BENCHMARK_DIR,
    load_prompts,
    load_prompt_by_id,
    load_prompts_batch,
    get_benchmark_summary,
    validate_prompt_schema,
)

__all__ = [
    "BENCHMARK_DIR",
    "load_prompts",
    "load_prompt_by_id",
    "load_prompts_batch",
    "get_benchmark_summary",
    "validate_prompt_schema",
]
