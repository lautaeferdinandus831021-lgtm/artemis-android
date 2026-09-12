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

"""``artemis freebuff`` — verify the machine's Freebuff identity and quota."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

from artemis.services.freebuff import (
    BACKEND_URL,
    CREDENTIALS_PATH_ENV,
    credentials_path,
    load_credentials,
)
from artemis.utils.logger import get_logger

logger = get_logger(__name__)
console = Console()

app = typer.Typer(
    help=(
        "Inspect the local Freebuff (codebuff.com) account connection: "
        "credential file, live authentication, and Freebucks quota."
    ),
    no_args_is_help=True,
)
#: Alias used by the CLI router in ``interfaces/cli/main.py``.
freebuff_app = app


def _status_table(status) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_row(
        "[bold]Connection[/bold]",
        "[green]connected[/green]" if status.connected else "[red]not connected[/red]",
    )
    if status.email:
        table.add_row("Account", status.email)
    if status.user_id:
        table.add_row("User ID", status.user_id)
    if status.access_tier:
        table.add_row("Access tier", status.access_tier)
    if status.session_status:
        table.add_row("Session", status.session_status)
    if status.freebucks_balance is not None:
        table.add_row("Freebucks balance", str(status.freebucks_balance))
    if status.freebucks_daily_remaining is not None:
        table.add_row(
            "Daily remaining",
            f"{status.freebucks_daily_remaining}/{status.freebucks_daily_limit}"
            f" (resets {status.freebucks_daily_reset_at or 'unknown'})",
        )
    if status.country_code:
        table.add_row("Country", status.country_code)
    if status.country_block_reason:
        table.add_row(
            "Country gate",
            f"[red]{status.country_block_reason}[/red] — CLI sessions may be refused",
        )
    return table


@app.command()
def status(
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Seconds to wait for each backend request.",
            min=1.0,
            max=60.0,
        ),
    ] = 15.0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON instead of a table."),
    ] = False,
    path: Annotated[
        str | None,
        typer.Option(
            "--credentials",
            help="Explicit path to the Freebuff credentials JSON.",
        ),
    ] = None,
):
    """Check the local Freebuff credentials against the live backend."""

    import dataclasses

    from artemis.services.freebuff import check_connection

    resolved_path = Path(path).expanduser() if path else None

    status_obj = check_connection(timeout=timeout, path=resolved_path)

    if json_output:
        payload = dataclasses.asdict(status_obj)
        payload.pop("llm_routing_available", None)
        console.print_json(data=payload)
        return

    if not status_obj.connected:
        console.print(
            Panel(
                f"[red]{status_obj.error}[/red]\n\n"
                f"Credentials are read from {credentials_path()}\n"
                f"(override with the {CREDENTIALS_PATH_ENV} environment variable).\n"
                "Log in with the freebuff CLI to create them.",
                title="Freebuff — not connected",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    console.print(
        Panel(
            _status_table(status_obj),
            title=f"Freebuff — connected via {BACKEND_URL}",
            border_style="green",
        )
    )

    if status_obj.country_block_reason:
        console.print(
            "[yellow]Note:[/yellow] the account is country-gated "
            f"({status_obj.country_block_reason}); even the freebuff CLI may "
            "refuse to start sessions. Model access still requires "
            "per-provider API keys (GEMINI_API_KEY etc.) regardless."
        )


def _load_credentials_or_exit(path: str | None):
    try:
        return load_credentials(Path(path).expanduser() if path else None)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def verify(
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for the backend.", min=1.0),
    ] = 15.0,
    path: Annotated[
        str | None,
        typer.Option("--credentials", help="Explicit credentials JSON path."),
    ] = None,
):
    """Verify credentials only (no session/quota fetch)."""

    import httpx

    from artemis.services.freebuff import verify_connection

    credentials = _load_credentials_or_exit(path)
    try:
        status_obj = verify_connection(credentials, timeout=timeout, fetch_session=False)
    except httpx.HTTPError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if status_obj.connected:
        console.print(f"[green]OK[/green] authenticated as {status_obj.email} on {BACKEND_URL}")
        return
    console.print(f"[red]FAILED[/red] {status_obj.error}")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
