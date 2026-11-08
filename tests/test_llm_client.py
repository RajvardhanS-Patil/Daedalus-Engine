"""
Tests for Ollama LLM Client
"""

import pytest
from pydantic import BaseModel
from agents.llm_client import OllamaClient


def test_ollama_client_init():
    """Verify Ollama client initialization with settings."""
    client = OllamaClient()
    assert client.base_url.startswith("http")
    assert client.model == "qwen2.5:7b"
    assert client.timeout > 0


def test_ollama_health_check_live():
    """Test actual live health check against local Ollama instance."""
    client = OllamaClient()
    # Ollama is verified running in user environment
    assert client.health_check() is True


def test_ollama_model_available():
    """Verify Qwen 2.5 7B model presence."""
    client = OllamaClient()
    models = client.get_available_models()
    assert any("qwen2.5" in m for m in models)


class SampleSchema(BaseModel):
    summary: str
    eligible: bool


def test_ollama_recovery_instructions():
    """Verify recovery message contains instructions."""
    client = OllamaClient()
    rec = client.get_recovery_instructions()
    assert "ollama serve" in rec
