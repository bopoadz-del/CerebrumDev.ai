"""Tests for natural-language command parsing in the chat router."""

import pytest
from app.routers.chat import _parse_command, _apply_command
from app.models.session import SessionState


@pytest.mark.parametrize(
    "message, expected_command, expected_args",
    [
        ("set domain to medical", "set_domain", {"domain": "medical"}),
        ("use domain legal", "set_domain", {"domain": "legal"}),
        ("domain is finance", "set_domain", {"domain": "finance"}),
        ("set model to Llama-3.1-8B", "set_model", {"model": "Llama-3.1-8B"}),
        ("use model Mistral-7B", "set_model", {"model": "Mistral-7B"}),
        ("lora rank 64", "set_lora_rank", {"lora_rank": 64}),
        ("set learning rate to 2e-4", "set_learning_rate", {"learning_rate": 2e-4}),
        ("set vector db to chroma", "set_vector_db", {"vector_db": "Chroma"}),
        ("set hnsw to accurate", "set_hnsw_preset", {"hnsw_preset": "accurate"}),
    ],
)
def test_parse_command(message, expected_command, expected_args):
    command, args = _parse_command(message)
    assert command == expected_command
    assert args == expected_args


def test_parse_command_no_match():
    command, args = _parse_command("hello world")
    assert command is None
    assert args is None


def test_apply_command_updates_state():
    state = SessionState(session_id="sess_test", user_id="anonymous")
    _apply_command(state, "set_domain", {"domain": "medical"})
    assert state.config.domain == "medical"

    _apply_command(state, "set_model", {"model": "Mistral-7B"})
    assert state.config.ai_config.base_model == "Mistral-7B"

    _apply_command(state, "set_lora_rank", {"lora_rank": 16})
    assert state.config.ai_config.lora_rank == 16

    _apply_command(state, "set_learning_rate", {"learning_rate": 1e-4})
    assert state.config.ai_config.learning_rate == 1e-4


@pytest.mark.parametrize(
    "message",
    [
        "what blocks are available",
        "list blocks",
        "available blocks",
        "show blocks",
    ],
)
def test_parse_list_blocks_command(message):
    command, args = _parse_command(message)
    assert command == "list_blocks"
    assert args == {}
