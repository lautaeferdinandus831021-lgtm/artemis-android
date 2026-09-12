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

"""Shared test environment for the deterministic unit suite.

Rich-based CLI output must render identically on every machine and CI
runner. Some hosted runners export ``COLUMNS=80`` and force color
(typer/rich detect ``GITHUB_ACTIONS``/``FORCE_COLOR``), which wraps panel
borders and injects ANSI codes into ``CliRunner`` output, breaking
plain-text assertions in ``test_cli.py``. Pin the environment here,
before any test module (and therefore the CLI modules) is imported:
a fixed generous width keeps table/panel layout stable and typer's
official ``_TYPER_FORCE_DISABLE_TERMINAL`` switch keeps output free of
ANSI escape sequences regardless of runner detection.
"""

import os

os.environ["COLUMNS"] = "120"
for _color_var in ("FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(_color_var, None)
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
