"""Coarse token-budget guard for local-LLM prompts.

Ollama's tokenizer varies by model (qwen vs llama vs etc.) and we don't
ship ``tiktoken`` here. A 4-chars-per-token English heuristic is rough
but sufficient to catch obvious budget blowouts at build time — the
real win is catching a 50-candidate prompt that wandered past 6000
tokens, not shaving the last 5% off context usage.
"""

from __future__ import annotations

from dataclasses import dataclass

# English prose lands near 4 chars/token across GPT/Claude/Qwen
# tokenizers. Underestimating tokens understates budget pressure, so
# bias slightly conservative (= slightly more tokens per char).
_CHARS_PER_TOKEN = 3.6


class PromptTooLargeError(RuntimeError):
    """Raised when a rendered prompt exceeds the allowed share of num_ctx."""


@dataclass
class BudgetReport:
    prompt_tokens: int
    budget_tokens: int
    margin_pct: float
    fits: bool


def estimate_tokens(text: str) -> int:
    """Cheap token count estimate. Returns at least 1 for non-empty text."""
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def check_prompt_fits(
    prompt: str,
    *,
    num_ctx: int,
    margin_pct: float = 0.2,
    raise_on_fail: bool = True,
) -> BudgetReport:
    """Verify the prompt leaves at least ``margin_pct`` of ``num_ctx`` for
    the model's reply. Raises ``PromptTooLargeError`` by default on fail.

    Pass ``raise_on_fail=False`` to just collect the report (useful in
    smoke tests that want to assert structure, not enforce).
    """
    if not 0.0 <= margin_pct < 1.0:
        raise ValueError(f"margin_pct must be in [0, 1), got {margin_pct}")
    budget = int(num_ctx * (1 - margin_pct))
    tokens = estimate_tokens(prompt)
    fits = tokens <= budget
    report = BudgetReport(
        prompt_tokens=tokens,
        budget_tokens=budget,
        margin_pct=margin_pct,
        fits=fits,
    )
    if not fits and raise_on_fail:
        raise PromptTooLargeError(
            f"prompt uses ~{tokens} tokens; budget is {budget} "
            f"({int(margin_pct * 100)}% reply margin on num_ctx={num_ctx})"
        )
    return report


__all__ = [
    "BudgetReport",
    "PromptTooLargeError",
    "check_prompt_fits",
    "estimate_tokens",
]
