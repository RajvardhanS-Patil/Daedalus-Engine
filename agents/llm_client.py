"""
Unstop Autopilot — Ollama LLM Client

Synchronous HTTP client for Ollama local LLM inference.
Handles health checks, text generation, and structured JSON output
with comprehensive error handling for connection, timeout, and
parsing failures.
"""

from __future__ import annotations

import json
import logging
from typing import Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OllamaError(Exception):
    """Base exception for Ollama client errors."""
    pass


class OllamaConnectionError(OllamaError):
    """Raised when the Ollama server is unreachable."""
    pass


class OllamaTimeoutError(OllamaError):
    """Raised when a request to Ollama times out."""
    pass


class OllamaModelNotFoundError(OllamaError):
    """Raised when the configured model is not available."""
    pass


class OllamaResponseError(OllamaError):
    """Raised when the Ollama response is invalid or unparseable."""
    pass


class OllamaClient:
    """HTTP client for Ollama LLM inference.

    Uses the Ollama REST API for text generation and structured JSON output.
    All methods are synchronous and use httpx under the hood.

    Attributes:
        base_url: The Ollama server base URL.
        model: The model name to use for generation.
        timeout: Request timeout in seconds.
    """

    def __init__(self) -> None:
        self.base_url: str = settings.ollama.base_url.rstrip("/")
        self.model: str = settings.ollama.model
        self.timeout: int = settings.ollama.timeout
        self._default_temperature: float = settings.ollama.temperature
        self._default_top_p: float = settings.ollama.top_p
        self._context_size: int = settings.ollama.context_size
        self._max_tokens: int = settings.ollama.max_tokens
        logger.info(
            "OllamaClient initialised — server=%s model=%s timeout=%ds",
            self.base_url,
            self.model,
            self.timeout,
        )

    # ------------------------------------------------------------------
    # Health & introspection
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Check if the Ollama server is reachable.

        Sends a GET request to the ``/api/tags`` endpoint and returns
        ``True`` if the server responds with HTTP 200, ``False`` otherwise.
        Connection errors and timeouts are caught and logged rather than
        raised so callers can use this as a simple boolean probe.

        Returns:
            True if the server is reachable, False otherwise.
        """
        try:
            response = httpx.get(
                f"{self.base_url}/api/tags",
                timeout=min(self.timeout, 10),
            )
            reachable = response.status_code == 200
            if reachable:
                logger.debug("Ollama health check passed")
            else:
                logger.warning(
                    "Ollama health check returned HTTP %d", response.status_code
                )
            return reachable
        except httpx.ConnectError:
            logger.warning("Ollama health check failed — connection refused")
            return False
        except httpx.TimeoutException:
            logger.warning("Ollama health check failed — timeout")
            return False
        except httpx.HTTPError as exc:
            logger.warning("Ollama health check failed — %s", exc)
            return False

    def check_model(self) -> bool:
        """Check if the configured model is available on the Ollama server.

        Fetches the list of available models and checks whether the
        configured model name appears (using a prefix match so that
        ``qwen2.5:7b`` matches ``qwen2.5:7b-instruct-...`` variants).

        Returns:
            True if the model is available, False otherwise.
        """
        try:
            available = self.get_available_models()
            # Exact match first, then prefix match
            for name in available:
                if name == self.model or name.startswith(self.model):
                    logger.debug("Model '%s' found on Ollama server", self.model)
                    return True
            logger.warning(
                "Model '%s' not found. Available: %s", self.model, available
            )
            return False
        except OllamaError:
            return False

    def get_available_models(self) -> list[str]:
        """List all models available on the Ollama server.

        Returns:
            A list of model name strings.

        Raises:
            OllamaConnectionError: If the server is unreachable.
            OllamaTimeoutError: If the request times out.
        """
        try:
            response = httpx.get(
                f"{self.base_url}/api/tags",
                timeout=min(self.timeout, 15),
            )
            response.raise_for_status()
            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            logger.debug("Available models: %s", models)
            return models
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self.base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                "Timeout listing Ollama models"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaConnectionError(
                f"HTTP error listing models: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        system: str = "",
        temperature: float | None = None,
    ) -> str:
        """Generate a text completion using the configured model.

        Sends a non-streaming request to the Ollama ``/api/generate``
        endpoint and returns the response text.

        Args:
            prompt: The user prompt to send.
            system: Optional system prompt to set context.
            temperature: Override the default temperature. When ``None``
                the value from settings is used.

        Returns:
            The generated text response.

        Raises:
            OllamaConnectionError: If the server is unreachable.
            OllamaTimeoutError: If the request times out.
            OllamaModelNotFoundError: If the model is not found.
            OllamaResponseError: If the response cannot be parsed.
        """
        effective_temp = (
            temperature if temperature is not None else self._default_temperature
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": effective_temp,
                "top_p": self._default_top_p,
                "num_ctx": self._context_size,
                "num_predict": self._max_tokens,
            },
        }
        if system:
            payload["system"] = system

        logger.debug(
            "Generating text — model=%s temp=%.2f prompt_len=%d",
            self.model,
            effective_temp,
            len(prompt),
        )

        data = self._post_generate(payload)
        response_text = data.get("response", "")
        if not response_text:
            logger.warning("Ollama returned an empty response")
        return response_text

    def generate_json(
        self,
        prompt: str,
        system: str = "",
        response_model: Type[T] | None = None,
    ) -> dict | T:
        """Generate structured JSON output from the LLM.

        Uses Ollama's ``format: 'json'`` parameter to constrain the
        model output to valid JSON.  Optionally validates the result
        against a Pydantic model.

        Args:
            prompt: The user prompt (should instruct the model to respond
                with JSON).
            system: Optional system prompt.
            response_model: Optional Pydantic model class to validate
                and parse the response into.

        Returns:
            A parsed ``dict`` when *response_model* is ``None``, or an
            instance of the Pydantic model otherwise.

        Raises:
            OllamaConnectionError: If the server is unreachable.
            OllamaTimeoutError: If the request times out.
            OllamaModelNotFoundError: If the model is not found.
            OllamaResponseError: If the response is not valid JSON or
                fails Pydantic validation.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self._default_temperature,
                "top_p": self._default_top_p,
                "num_ctx": self._context_size,
                "num_predict": self._max_tokens,
            },
        }
        if system:
            payload["system"] = system

        logger.debug(
            "Generating JSON — model=%s prompt_len=%d response_model=%s",
            self.model,
            len(prompt),
            response_model.__name__ if response_model else "dict",
        )

        data = self._post_generate(payload)
        raw_text = data.get("response", "")

        # Parse the JSON string
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.error("Ollama returned invalid JSON: %s", raw_text[:500])
            raise OllamaResponseError(
                f"LLM response is not valid JSON: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            logger.error(
                "Expected JSON object, got %s: %s", type(parsed).__name__, raw_text[:300]
            )
            raise OllamaResponseError(
                f"Expected a JSON object, got {type(parsed).__name__}"
            )

        # Validate with Pydantic model if provided
        if response_model is not None:
            try:
                return response_model.model_validate(parsed)
            except ValidationError as exc:
                logger.error(
                    "Pydantic validation failed for %s: %s",
                    response_model.__name__,
                    exc,
                )
                raise OllamaResponseError(
                    f"Response validation failed for {response_model.__name__}: {exc}"
                ) from exc

        return parsed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_generate(self, payload: dict) -> dict:
        """Send a POST request to ``/api/generate`` and return parsed JSON.

        This is the shared transport layer used by both :meth:`generate`
        and :meth:`generate_json`.

        Args:
            payload: The request body dictionary.

        Returns:
            The parsed JSON response dictionary.

        Raises:
            OllamaConnectionError: If the server is unreachable.
            OllamaTimeoutError: If the request times out.
            OllamaModelNotFoundError: If the server reports the model is
                not found (HTTP 404).
            OllamaResponseError: If the response body is not valid JSON.
        """
        url = f"{self.base_url}/api/generate"
        try:
            response = httpx.post(url, json=payload, timeout=self.timeout)
        except httpx.ConnectError as exc:
            logger.error("Cannot connect to Ollama at %s", self.base_url)
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. "
                f"{self.get_recovery_instructions()}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error(
                "Ollama request timed out after %ds", self.timeout
            )
            raise OllamaTimeoutError(
                f"Ollama request timed out after {self.timeout}s. "
                "Consider increasing OLLAMA_TIMEOUT or using a smaller model."
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("HTTP error communicating with Ollama: %s", exc)
            raise OllamaConnectionError(
                f"HTTP error communicating with Ollama: {exc}"
            ) from exc

        # Handle HTTP error status codes
        if response.status_code == 404:
            logger.error("Model '%s' not found on Ollama", self.model)
            raise OllamaModelNotFoundError(
                f"Model '{self.model}' not found. "
                f"Run: ollama pull {self.model}"
            )
        if response.status_code != 200:
            error_text = response.text[:500]
            logger.error(
                "Ollama returned HTTP %d: %s", response.status_code, error_text
            )
            raise OllamaResponseError(
                f"Ollama HTTP {response.status_code}: {error_text}"
            )

        # Parse the response body
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            logger.error(
                "Ollama response is not valid JSON: %s", response.text[:500]
            )
            raise OllamaResponseError(
                "Ollama response body is not valid JSON"
            ) from exc

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    @staticmethod
    def get_recovery_instructions() -> str:
        """Return helpful recovery instructions when Ollama is down.

        Returns:
            A multi-line string with troubleshooting steps.
        """
        return (
            "Ollama is not running. To start it:\n"
            "\n"
            "1. Open a terminal and run: ollama serve\n"
            "2. Or start the systemd service: systemctl --user start ollama\n"
            "3. Verify it's running: curl http://localhost:11434/api/tags\n"
            "\n"
            "If the model is not available:\n"
            "  ollama pull qwen2.5:7b\n"
        )
