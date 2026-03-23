"""Tests for the Cloud engine backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from openjarvis.core.registry import EngineRegistry
from openjarvis.core.types import Message, Role
from openjarvis.engine.cloud import CloudEngine, estimate_cost


class TestEstimateCost:
    def test_known_model(self) -> None:
        cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        assert cost == pytest.approx(12.50)  # 2.50 + 10.00

    def test_unknown_model(self) -> None:
        assert estimate_cost("unknown-model", 100, 100) == 0.0

    def test_prefix_match(self) -> None:
        cost = estimate_cost("gpt-4o-2024-01-01", 1_000_000, 0)
        assert cost == pytest.approx(2.50)


class TestCloudEngineHealth:
    def test_health_no_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        EngineRegistry.register_value("cloud", CloudEngine)
        engine = CloudEngine()
        assert engine.health() is False

    def test_health_with_openai_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        # Mock the openai import
        fake_openai = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"openai": fake_openai}):
            EngineRegistry.register_value("cloud", CloudEngine)
            engine = CloudEngine()
        assert engine.health() is True


class TestCloudEngineListModels:
    def test_list_models_no_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        EngineRegistry.register_value("cloud", CloudEngine)
        engine = CloudEngine()
        assert engine.list_models() == []


class TestCloudEngineGenerate:
    def test_generate_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        fake_usage = SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
        fake_choice = SimpleNamespace(
            message=SimpleNamespace(content="Hello!"),
            finish_reason="stop",
        )
        fake_resp = SimpleNamespace(
            choices=[fake_choice], usage=fake_usage, model="gpt-4o"
        )

        fake_client = mock.MagicMock()
        fake_client.chat.completions.create.return_value = fake_resp

        EngineRegistry.register_value("cloud", CloudEngine)
        engine = CloudEngine()
        engine._openai_client = fake_client

        result = engine.generate(
            [Message(role=Role.USER, content="Hi")], model="gpt-4o"
        )
        assert result["content"] == "Hello!"
        assert result["usage"]["prompt_tokens"] == 10

    def test_generate_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        fake_usage = SimpleNamespace(input_tokens=12, output_tokens=8)
        fake_content = SimpleNamespace(text="Greetings!")
        fake_resp = SimpleNamespace(
            content=[fake_content],
            usage=fake_usage,
            model="claude-sonnet-4-20250514",
            stop_reason="end_turn",
        )

        fake_client = mock.MagicMock()
        fake_client.messages.create.return_value = fake_resp

        EngineRegistry.register_value("cloud", CloudEngine)
        engine = CloudEngine()
        engine._anthropic_client = fake_client

        result = engine.generate(
            [Message(role=Role.USER, content="Hi")],
            model="claude-sonnet-4-20250514",
        )
        assert result["content"] == "Greetings!"
        assert result["usage"]["prompt_tokens"] == 12
        assert result["usage"]["completion_tokens"] == 8
