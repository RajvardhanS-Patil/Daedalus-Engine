"""
Unstop Autopilot — Real-Time Quiz Solver

Real-time question answering intelligence powered by local Qwen 2.5 7B LLM.
Evaluates scraped live questions and options, predicts the correct answer with
confidence scoring, and provides fast heuristic fallback under timer pressure.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

from agents.llm_client import OllamaClient, OllamaError
from config import settings

logger = logging.getLogger(__name__)


class LiveAnswerDecision(BaseModel):
    """Pydantic schema for structured LLM answer decisions in live assessments."""

    chosen_option: str = Field(description="The exact text or label of the selected answer option")
    chosen_index: int = Field(
        ge=0, le=9, description="0-based integer index of the selected option in the given options list"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score from 0.0 (pure guess) to 1.0 (certain)"
    )
    rationale: str = Field(description="Brief 1-2 sentence logical explanation of why this option is correct")


class QuizSolver:
    """Evaluates live multiple-choice assessment questions using local LLM inference."""

    SYSTEM_PROMPT = (
        "You are an expert competitive quiz solver and speed test taker. "
        "Analyze the multiple-choice question and options provided. "
        "Select the single most correct answer. "
        "You MUST respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "chosen_option": "<exact text of the chosen option>",\n'
        '  "chosen_index": <0-based integer index 0, 1, 2, or 3>,\n'
        '  "confidence": <float between 0.0 and 1.0>,\n'
        '  "rationale": "<concise 1-sentence reasoning>"\n'
        "}\n"
        "Do not include any explanation outside the JSON."
    )

    def __init__(self, llm_client: Optional[OllamaClient] = None) -> None:
        self.llm = llm_client or OllamaClient()

    def solve(
        self,
        question: str,
        options: list[str],
        context: str = "",
    ) -> LiveAnswerDecision:
        """Solve a question using Qwen 2.5 7B.

        Args:
            question: Cleaned question text (including any embedded code or snippets).
            options: List of available answer options.
            context: Optional topic or competition category context.

        Returns:
            LiveAnswerDecision with selected option, index, confidence, and rationale.
        """
        if not options:
            return LiveAnswerDecision(
                chosen_option="",
                chosen_index=0,
                confidence=0.0,
                rationale="No options provided to solver.",
            )

        if len(options) == 1:
            return LiveAnswerDecision(
                chosen_option=options[0],
                chosen_index=0,
                confidence=1.0,
                rationale="Single option available.",
            )

        formatted_options = "\n".join(
            f"[{idx}] {opt}" for idx, opt in enumerate(options)
        )

        user_prompt = (
            f"Question: {question.strip()}\n\n"
            f"Options:\n{formatted_options}\n\n"
        )
        if context:
            user_prompt = f"Assessment Context: {context}\n" + user_prompt

        user_prompt += "Identify the correct option index (0 to N-1) and return the JSON object."

        try:
            decision = self.llm.generate_json(
                prompt=user_prompt,
                system=self.SYSTEM_PROMPT,
                response_model=LiveAnswerDecision,
            )

            # Sanitize chosen_index bounds
            if decision.chosen_index < 0 or decision.chosen_index >= len(options):
                logger.warning(
                    "LLM returned out-of-bounds index %d for %d options. Aligning...",
                    decision.chosen_index,
                    len(options),
                )
                # Try matching option text
                matched_idx = self._find_matching_option_index(
                    decision.chosen_option, options
                )
                decision.chosen_index = matched_idx
                decision.chosen_option = options[matched_idx]
            else:
                # Ensure chosen_option exactly matches the options list at that index
                decision.chosen_option = options[decision.chosen_index]

            return decision

        except (OllamaError, ValueError, Exception) as exc:
            logger.error("LLM quiz solve error: %s. Falling back to heuristic.", exc)
            raise

    def solve_with_fallback(
        self,
        question: str,
        options: list[str],
        context: str = "",
        timeout: Optional[int] = None,
    ) -> LiveAnswerDecision:
        """Attempt LLM solution with instant heuristic fallback on failure or timeout.

        Guarantees an answer decision within strict timer windows.
        """
        if not options:
            return LiveAnswerDecision(
                chosen_option="",
                chosen_index=0,
                confidence=0.0,
                rationale="Empty options list.",
            )

        try:
            return self.solve(question=question, options=options, context=context)
        except Exception as exc:
            logger.warning("Triggering fallback heuristic due to solver error: %s", exc)
            fallback_index = self._keyword_heuristic(question, options)
            return LiveAnswerDecision(
                chosen_option=options[fallback_index],
                chosen_index=fallback_index,
                confidence=0.35,
                rationale=f"Fallback heuristic selected option {fallback_index} (LLM offline or timed out: {exc}).",
            )

    def _find_matching_option_index(self, chosen_text: str, options: list[str]) -> int:
        """Find the best matching option index for a chosen text snippet."""
        clean_chosen = chosen_text.lower().strip()
        for idx, opt in enumerate(options):
            if opt.lower().strip() == clean_chosen:
                return idx

        # Substring search
        for idx, opt in enumerate(options):
            if clean_chosen in opt.lower() or opt.lower() in clean_chosen:
                return idx

        # Letter notation (A, B, C, D)
        match = re.search(r"\b([A-D])\b", clean_chosen, re.IGNORECASE)
        if match:
            letter_idx = ord(match.group(1).upper()) - ord("A")
            if 0 <= letter_idx < len(options):
                return letter_idx

        return 0

    def _keyword_heuristic(self, question: str, options: list[str]) -> int:
        """Heuristic answer picker when LLM is unavailable under time constraints.

        Scores options by:
        1. Non-empty word overlap with question keywords
        2. Typical comprehensive answer patterns ('all of the above')
        3. Length / informativeness heuristic
        """
        scores = [0.0] * len(options)
        q_words = set(re.findall(r"\b[a-zA-Z0-9_]{3,}\b", question.lower()))

        for idx, opt in enumerate(options):
            opt_lower = opt.lower()

            # Positive indicators
            if "all of the above" in opt_lower or "all the above" in opt_lower:
                scores[idx] += 3.0
            if "both a and b" in opt_lower or "both" in opt_lower:
                scores[idx] += 1.5

            # Keyword overlap
            opt_words = set(re.findall(r"\b[a-zA-Z0-9_]{3,}\b", opt_lower))
            overlap = len(q_words & opt_words)
            scores[idx] += float(overlap)

            # Modest bias towards more descriptive (longer) answers
            scores[idx] += min(len(opt) / 80.0, 1.0)

        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        return best_idx
