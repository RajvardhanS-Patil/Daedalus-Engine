"""
Unstop Autopilot — Terminal Components

Reusable rich visual components: header, status panel, stats panel,
opportunity tables, menus, notifications, and interactive prompts.
"""

from __future__ import annotations

import shutil
from typing import Any, Sequence

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


def create_header() -> Panel:
    """Create the application header with styled typography."""
    ascii_art = (
        "[bold cyan] _   _ _   _ ____ _____ ___  ____     _   _   _ _____ ___  ____ ___ _     ___ _____ [/bold cyan]\n"
        "[bold cyan]| | | | \\ | / ___|_   _/ _ \\|  _ \\   / \\ | | | |_   _/ _ \\|  _ \\_ _| |   / _ \\_   _|[/bold cyan]\n"
        "[bold blue]| | | |  \\| \\___ \\ | || | | | |_) | / _ \\| | | | | || | | | |_) | || |  | | | || |  [/bold blue]\n"
        "[bold blue]| |_| | |\\  |___) || || |_| |  __/ / ___ \\ |_| | | || |_| |  __/| || |__| |_| || |  [/bold blue]\n"
        "[bold magenta] \\___/|_| \\_|____/ |_| \\___/|_|   /_/   \\_\\___/  |_| \\___/|_|  |___|_____\\___/ |_|  [/bold magenta]"
    )
    subtitle = Text("Local-First Autonomous AI Competition Management System • Linux / RTX 3050", style="italic dim white")
    
    content = Text.from_markup(ascii_art)
    content.append("\n\n")
    content.append(subtitle)

    return Panel(
        content,
        border_style="bright_blue",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def create_status_panel(
    ollama_ok: bool,
    model_ok: bool,
    db_ok: bool,
    browser_ok: bool,
    scheduler_on: bool,
    last_discovery: str | None = None,
) -> Panel:
    """System health status indicators panel."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="left", ratio=1)

    ollama_indicator = "[bold green]● Connected[/bold green]" if ollama_ok else "[bold red]○ Disconnected[/bold red]"
    model_indicator = "[bold green]● Loaded (Qwen 2.5 7B)[/bold green]" if model_ok else "[bold yellow]○ Not loaded[/bold yellow]"
    db_indicator = "[bold green]● Connected (SQLite)[/bold green]" if db_ok else "[bold red]○ Offline[/bold red]"
    browser_indicator = "[bold green]● Ready (Playwright)[/bold green]" if browser_ok else "[bold yellow]○ Idle[/bold yellow]"
    sched_indicator = "[bold green]● Active[/bold green]" if scheduler_on else "[bold dim yellow]○ Stopped[/bold dim yellow]"
    disc_time = last_discovery if last_discovery else "Never"

    col1 = (
        f"[bold white]Ollama Runtime:[/bold white] {ollama_indicator}\n"
        f"[bold white]AI Model:[/bold white] {model_indicator}"
    )
    col2 = (
        f"[bold white]Database:[/bold white] {db_indicator}\n"
        f"[bold white]Browser Layer:[/bold white] {browser_indicator}"
    )
    col3 = (
        f"[bold white]Scheduler:[/bold white] {sched_indicator}\n"
        f"[bold white]Last Discovery:[/bold white] [cyan]{disc_time}[/cyan]"
    )

    grid.add_row(col1, col2, col3)

    return Panel(
        grid,
        title="[bold yellow] SYSTEM STATUS [/bold yellow]",
        border_style="yellow",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def create_stats_panel(
    total_opps: int = 0,
    eligible: int = 0,
    registered: int = 0,
    upcoming_deadlines: int = 0,
    certificates: int = 0,
    verified_points: int = 0,
) -> Panel:
    """Metric cards showing key performance stats."""
    grid = Table.grid(expand=True)
    for _ in range(6):
        grid.add_column(justify="center", ratio=1)

    cards = [
        f"[bold cyan]{total_opps}[/bold cyan]\n[dim]Total Opps[/dim]",
        f"[bold green]{eligible}[/bold green]\n[dim]Eligible[/dim]",
        f"[bold magenta]{registered}[/bold magenta]\n[dim]Registered[/dim]",
        f"[bold yellow]{upcoming_deadlines}[/bold yellow]\n[dim]Upcoming Deadlines[/dim]",
        f"[bold bright_blue]{certificates}[/bold bright_blue]\n[dim]Certificates[/dim]",
        f"[bold bright_green]{verified_points}[/bold bright_green]\n[dim]Verified Points[/dim]",
    ]

    grid.add_row(*cards)

    return Panel(
        grid,
        title="[bold green] TELEMETRY & STATS [/bold green]",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def create_opportunity_table(
    opportunities: Sequence[Any],
    show_score: bool = True,
    title: str = "Discovered Opportunities",
) -> Table:
    """Create a formatted Rich table of opportunities."""
    table = Table(
        title=f"[bold cyan]{title}[/bold cyan]",
        box=box.ROUNDED,
        expand=True,
        header_style="bold bright_white on dark_blue",
        show_lines=True,
    )

    table.add_column("ID", justify="center", style="dim", width=4)
    table.add_column("Title", justify="left", ratio=3)
    table.add_column("Category", justify="center", ratio=1)
    table.add_column("Mode", justify="center", ratio=1)
    table.add_column("Fee", justify="center", ratio=1)
    table.add_column("Deadline", justify="center", ratio=2)

    if show_score:
        table.add_column("Score", justify="center", width=7)
        table.add_column("Priority", justify="center", ratio=1)

    table.add_column("Status", justify="center", width=10)

    if not opportunities:
        table.add_row(
            "-",
            "[dim italic]No opportunities found. Run discovery to populate listing.[/dim italic]",
            "-", "-", "-", "-", *("-" for _ in ([1, 2] if show_score else [])), "-"
        )
        return table

    for opp in opportunities:
        # Format ID
        opp_id = str(opp.id) if hasattr(opp, "id") else "-"
        
        # Format Title (clean, truncate if needed)
        title_str = getattr(opp, "title", "Untitled") or "Untitled"
        if len(title_str) > 50:
            title_str = title_str[:47] + "..."

        # Category style
        cat = getattr(opp, "category", "unknown") or "unknown"
        cat_style = "cyan" if "hackathon" in cat.lower() else "magenta" if "quiz" in cat.lower() else "blue"
        cat_disp = f"[{cat_style}]{cat.capitalize()}[/{cat_style}]"

        # Mode
        mode = getattr(opp, "mode", "unknown") or "unknown"
        mode_style = "green" if "online" in mode.lower() else "yellow" if "hybrid" in mode.lower() else "red"
        mode_disp = f"[{mode_style}]{mode.capitalize()}[/{mode_style}]"

        # Fee
        fee = getattr(opp, "fee", "unknown") or "unknown"
        fee_style = "green" if any(w in fee.lower() for w in ["free", "0", "nil"]) else "yellow"
        fee_disp = f"[{fee_style}]{fee}[/{fee_style}]"

        # Deadline
        dl = getattr(opp, "deadline", "unknown") or "unknown"

        # Score & Priority
        score_val = getattr(opp, "suitability_score", None)
        score_disp = f"[bold]{score_val}[/bold]" if score_val is not None else "[dim]--[/dim]"

        priority = getattr(opp, "classification", None) or "UNSCORED"
        pri_style = (
            "bold green" if "HIGH" in priority
            else "green" if "GOOD" in priority
            else "yellow" if "LOW" in priority
            else "red" if "INELIGIBLE" in priority
            else "dim"
        )
        pri_disp = f"[{pri_style}]{priority}[/{pri_style}]"

        # Status
        status = getattr(opp, "status", "open") or "open"
        stat_style = "green" if status == "open" else "dim red" if status == "expired" else "yellow"
        stat_disp = f"[{stat_style}]{status.upper()}[/{stat_style}]"

        row = [opp_id, title_str, cat_disp, mode_disp, fee_disp, dl]
        if show_score:
            row.extend([score_disp, pri_disp])
        row.append(stat_disp)

        table.add_row(*row)

    return table


def create_menu(items: list[tuple[str, str]], title: str = "Command Menu") -> Panel:
    """Render a clean two-column grid menu of options."""
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="left", ratio=1)

    half = (len(items) + 1) // 2
    for i in range(half):
        item1 = items[i]
        c1 = f"[bold cyan][{item1[0]}][/bold cyan] {item1[1]}"
        
        c2 = ""
        if i + half < len(items):
            item2 = items[i + half]
            c2 = f"[bold cyan][{item2[0]}][/bold cyan] {item2[1]}"
            
        grid.add_row(c1, c2)

    return Panel(
        grid,
        title=f"[bold bright_white] {title} [/bold bright_white]",
        border_style="bright_blue",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def print_success(message: str) -> None:
    """Output formatted success alert."""
    console.print(f"[bold green]✔ SUCCESS:[/bold green] {message}")


def print_error(message: str) -> None:
    """Output formatted error alert."""
    console.print(f"[bold red]✖ ERROR:[/bold red] {message}")


def print_warning(message: str) -> None:
    """Output formatted warning alert."""
    console.print(f"[bold yellow]⚠ WARNING:[/bold yellow] {message}")


def print_info(message: str) -> None:
    """Output formatted informative alert."""
    console.print(f"[bold cyan]ℹ INFO:[/bold cyan] {message}")


def confirm_action(message: str, default: bool = False) -> bool:
    """Interactive y/N confirmation prompt."""
    prompt_hint = "[Y/n]" if default else "[y/N]"
    raw = console.input(f"[bold yellow]?[/bold yellow] {message} {prompt_hint} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def get_menu_choice(prompt: str = "Enter choice > ") -> str:
    """Prompt the user for a single line of input."""
    try:
        return console.input(f"[bold bright_cyan]{prompt}[/bold bright_cyan]").strip()
    except (KeyboardInterrupt, EOFError):
        return "12"  # default exit code
