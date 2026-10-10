"""The guard in tests/conftest.py: no test can spend a real key by accident."""

from __future__ import annotations

import pytest

from between_jobs.api import llm_client


async def test_the_real_model_client_cannot_be_reached_from_a_test() -> None:
    with pytest.raises(AssertionError, match="real model client"):
        await llm_client.generate(
            api_key="sk-or-v1-not-real",
            model="test/model",
            base_url=None,
            system_prompt="s",
            user_prompt="u",
        )
