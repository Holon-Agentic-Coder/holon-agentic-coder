"""Shared fixtures for the stage tests."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture
def no_origin_reconcile() -> Iterator[None]:
    """Stub the post-fetch origin reconcile so decomposed-plan flows never touch a real remote."""
    with patch("sandbox_executor.flow.stages.children.reconcile_with_origin", return_value=[]):
        yield
