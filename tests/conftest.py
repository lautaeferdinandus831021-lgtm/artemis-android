# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Repository-wide pytest fixtures and classification helpers.

The default test paths contain only deterministic tests.  Tests under the
integration and end-to-end trees remain directly runnable, and receive stable
markers here so callers can select them without relying on filename patterns.
"""

import os
from pathlib import Path

import pytest

# The deterministic suite must run without model credentials
# (CONTRIBUTING.md).  Some unit modules build their LLM eagerly at import or
# construction time, which pydantic rejects when every provider key is empty,
# before a test can inject its own mock.  Seed non-placeholder dummy keys so
# model objects construct successfully; every LLM interaction in the suite is
# mocked, so no real provider is ever contacted.
for _key in (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPEN_ROUTER_API_KEY",
    "XAI_API_KEY",
):
    # Environments commonly export these as empty strings; treat that as
    # unset so the suite never trips provider-side key validation.
    if not os.environ.get(_key):
        os.environ[_key] = "test-dummy-key"

from artemis.drivers.mock.mock_driver import MockDeviceDriver  # noqa: E402


@pytest.fixture
def mock_driver():
    """Provide an isolated mock mobile driver."""
    return MockDeviceDriver(device_id="fixture-mock-device", width=1080, height=2400)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach test-layer markers according to the owning test directory."""
    for item in items:
        parts = Path(str(item.path)).parts
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        if "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
