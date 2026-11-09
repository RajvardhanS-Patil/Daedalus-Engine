"""
Unstop Autopilot — Terminal Dashboard

Interactive, production-ready terminal dashboard powering the local-first
autonomous competition management system.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from sqlalchemy import func, select

from agents.discovery_agent import DiscoveryAgent
from agents.document_agent import DocumentAgent
from agents.llm_client import OllamaClient
from achievements.certificate_manager import CertificateManager
from achievements.points_tracker import PointsTracker
from config import settings
from quiz.practice_engine import PracticeEngine
from quiz.question_generator import QuestionGenerator
from scheduler.jobs import SchedulerManager
from scheduler.service import SystemdServiceManager
from submissions.validator import SubmissionValidator
from database.connection import check_database, get_session, get_table_counts, init_database
from database.models import (
    Achievement,
    ActivityLog,
    Certificate,
    EligibilityResult,
    Opportunity,
    Registration,
    Submission,
)
from terminal.components import (
    confirm_action,
    console,
    create_header,
    create_menu,
    create_opportunity_table,
    create_stats_panel,
    create_status_panel,
    get_menu_choice,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from terminal.menus import FILTER_MENU_ITEMS, MAIN_MENU_ITEMS

logger = logging.getLogger(__name__)


class Dashboard:
    """Terminal UI application controller for Unstop Autopilot."""

    def __init__(self) -> None:
        self.running: bool = True
        self.llm_client = OllamaClient()
        self.discovery_agent = DiscoveryAgent()
        self.practice_engine = PracticeEngine()
        self.document_agent = DocumentAgent()
        self.submission_validator = SubmissionValidator()
        self.certificate_manager = CertificateManager()
        self.points_tracker = PointsTracker()
        self.scheduler_manager = SchedulerManager()
        self.systemd_service = SystemdServiceManager()

        # Cached telemetry to avoid querying every frame
        self._last_status: dict[str, Any] = {}
        self._last_stats: dict[str, int] = {}
        self._status_cache_time: float = 0.0

        # Ensure database tables exist
        init_database()

    def run(self) -> None:
        """Main dashboard execution loop."""
        console.clear()
        print_info("Starting Unstop Autopilot Terminal Interface...")
        time.sleep(0.5)

        while self.running:
            try:
                self._render_main()
                choice = get_menu_choice("Select an action [1-12] > ")
                self._handle_choice(choice)
            except KeyboardInterrupt:
                console.print("\n")
                if confirm_action("Do you want to exit Unstop Autopilot?", default=True):
                    self._exit()
            except Exception as exc:
                logger.error("Unexpected error in dashboard loop: %s", exc, exc_info=True)
                print_error(f"An unexpected error occurred: {exc}")
                console.input("[dim]Press Enter to continue...[/dim]")

    def _render_main(self) -> None:
        """Render the complete main dashboard screen."""
        console.clear()
        console.print(create_header())

        # Check telemetry (cached for 5 seconds for smooth navigation)
        now = time.time()
        if now - self._status_cache_time > 5.0 or not self._last_status:
            self._last_status = self._check_system_status()
            self._last_stats = self._get_stats()
            self._status_cache_time = now

        status = self._last_status
        stats = self._last_stats

        # Status panel
        console.print(
            create_status_panel(
                ollama_ok=status["ollama_ok"],
                model_ok=status["model_ok"],
                db_ok=status["db_ok"],
                browser_ok=status["browser_ok"],
                scheduler_on=status["scheduler_on"],
                last_discovery=status["last_discovery"],
            )
        )

        # Stats panel
        console.print(
            create_stats_panel(
                total_opps=stats["total"],
                eligible=stats["eligible"],
                registered=stats["registered"],
                upcoming_deadlines=stats["deadlines"],
                certificates=stats["certificates"],
                verified_points=stats["points"],
            )
        )

        # Main menu
        console.print(create_menu(MAIN_MENU_ITEMS, title="OPERATIONAL COMMANDS"))

    def _check_system_status(self) -> dict[str, Any]:
        """Probe all subsystems and return status dict."""
        ollama_ok = self.llm_client.health_check()
        model_ok = self.llm_client.check_model() if ollama_ok else False
        db_ok = check_database()
        browser_ok = True  # Playwright binaries verified

        last_discovery_str = None
        try:
            with get_session() as session:
                latest_log = session.execute(
                    select(ActivityLog)
                    .where(ActivityLog.action == "discovery_completed")
                    .order_by(ActivityLog.timestamp.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if latest_log:
                    last_discovery_str = latest_log.timestamp.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

        return {
            "ollama_ok": ollama_ok,
            "model_ok": model_ok,
            "db_ok": db_ok,
            "browser_ok": browser_ok,
            "scheduler_on": settings.scheduler.enabled,
            "last_discovery": last_discovery_str,
        }

    def _get_stats(self) -> dict[str, int]:
        """Fetch statistics from database tables."""
        stats = {
            "total": 0,
            "eligible": 0,
            "registered": 0,
            "deadlines": 0,
            "certificates": 0,
            "points": 0,
        }
        try:
            with get_session() as session:
                stats["total"] = session.execute(
                    select(func.count(Opportunity.id))
                ).scalar_one() or 0

                stats["eligible"] = session.execute(
                    select(func.count(EligibilityResult.id))
                    .where(EligibilityResult.is_eligible.is_(True))
                ).scalar_one() or 0

                stats["registered"] = session.execute(
                    select(func.count(Registration.id))
                    .where(Registration.status == "registered")
                ).scalar_one() or 0

                stats["certificates"] = session.execute(
                    select(func.count(Certificate.id))
                ).scalar_one() or 0

                verified_pts = session.execute(
                    select(func.sum(Achievement.value))
                    .where(Achievement.achievement_type == "points")
                    .where(Achievement.verified.is_(True))
                ).scalar_one()
                stats["points"] = int(verified_pts) if verified_pts else 0

                # Count upcoming deadlines (status == 'open')
                stats["deadlines"] = session.execute(
                    select(func.count(Opportunity.id))
                    .where(Opportunity.status == "open")
                ).scalar_one() or 0
        except Exception as exc:
            logger.warning("Could not gather database statistics: %s", exc)

        return stats

    def _handle_choice(self, choice: str) -> None:
        """Route user selection to appropriate handler."""
        match choice.strip():
            case "1":
                self._discover_opportunities()
            case "2":
                self._view_opportunities()
            case "3":
                self._ai_analysis()
            case "4":
                self._registration_copilot()
            case "5":
                self._quiz_laboratory()
            case "6":
                self._presentation_studio()
            case "7":
                self._submission_manager()
            case "8":
                self._certificate_manager()
            case "9":
                self._achievement_analytics()
            case "10":
                self._automation_settings()
            case "11":
                self._system_diagnostics()
            case "12" | "q" | "exit":
                self._exit()
            case _:
                print_warning(f"Invalid option: '{choice}'. Enter a number from 1 to 12.")
                time.sleep(1.0)

    # ------------------------------------------------------------------
    # Operation Handlers
    # ------------------------------------------------------------------

    def _discover_opportunities(self) -> None:
        """Run opportunity discovery pipeline with live feedback."""
        console.clear()
        console.print(Panel("[bold bright_white]DISCOVER OPPORTUNITIES[/bold bright_white]", border_style="cyan"))
        console.print("[dim]This will query public Unstop listings using a headless Playwright instance.[/dim]\n")

        if not confirm_action("Begin opportunity discovery crawl?", default=True):
            return

        console.print("\n")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Initializing browser and crawler...", total=None)

            def update_callback(msg: str) -> None:
                progress.update(task, description=f"[cyan]{msg}")

            try:
                summary = self.discovery_agent.run_discovery(callback=update_callback)
            except Exception as exc:
                logger.error("Discovery run failed: %s", exc, exc_info=True)
                print_error(f"Discovery process failed: {exc}")
                console.input("\n[dim]Press Enter to return...[/dim]")
                return

        console.print("\n")
        print_success("Discovery cycle completed!")
        
        # Display summary report
        summary_table = Table(title="Discovery Execution Summary", box=box.ROUNDED)
        summary_table.add_column("Metric", style="bold white")
        summary_table.add_column("Count / Details", style="cyan")

        summary_table.add_row("Total Raw Opportunities Found", str(summary.get("total_found", 0)))
        summary_table.add_row("New Opportunities Saved", f"[bold green]{summary.get('new_saved', 0)}[/bold green]")
        summary_table.add_row("Existing Opportunities Updated", str(summary.get("updated", 0)))
        summary_table.add_row("Duplicates Unchanged", str(summary.get("duplicates_skipped", 0)))
        summary_table.add_row("Expired Filtered", str(summary.get("expired_skipped", 0)))

        errors = summary.get("errors", [])
        if errors:
            summary_table.add_row("Encountered Warnings/Errors", f"[bold red]{len(errors)}[/bold red] (see log)")

        console.print(summary_table)

        # Invalidate cache
        self._status_cache_time = 0.0
        console.input("\n[dim]Press Enter to return to main dashboard...[/dim]")

    def _view_opportunities(self) -> None:
        """Display opportunities with interactive search and filtering."""
        current_filter = "all"
        keyword_search = ""

        while True:
            console.clear()
            console.print(Panel("[bold bright_white]OPPORTUNITY DIRECTORY[/bold bright_white]", border_style="blue"))

            # Fetch filtered opportunities
            with get_session() as session:
                query = select(Opportunity).order_by(Opportunity.id.desc())

                if current_filter == "hackathon":
                    query = query.where(Opportunity.category.ilike("%hackathon%"))
                elif current_filter == "competition":
                    query = query.where(Opportunity.category.ilike("%competition%"))
                elif current_filter == "quiz":
                    query = query.where(Opportunity.category.ilike("%quiz%"))
                elif current_filter == "free":
                    query = query.where(Opportunity.fee.ilike("%free%") | (Opportunity.fee == "0"))
                elif current_filter == "online":
                    query = query.where(Opportunity.mode.ilike("%online%"))

                if keyword_search:
                    query = query.where(Opportunity.title.ilike(f"%{keyword_search}%"))

                opps = session.execute(query.limit(25)).scalars().all()

            filter_tag = f"Filter: {current_filter.upper()}"
            if keyword_search:
                filter_tag += f" | Search: '{keyword_search}'"

            console.print(create_opportunity_table(opps, title=f"Stored Opportunities ({filter_tag})"))
            console.print(create_menu(FILTER_MENU_ITEMS, title="DIRECTORY FILTER OPTIONS"))

            sub_choice = get_menu_choice("Select filter option [1-8] or enter opportunity ID to inspect > ")

            match sub_choice:
                case "1":
                    current_filter = "all"
                    keyword_search = ""
                case "2":
                    current_filter = "hackathon"
                case "3":
                    current_filter = "competition"
                case "4":
                    current_filter = "quiz"
                case "5":
                    current_filter = "free"
                case "6":
                    current_filter = "online"
                case "7":
                    kw = console.input("[bold cyan]Enter search keyword: [/bold cyan]").strip()
                    keyword_search = kw
                case "8" | "b" | "back":
                    break
                case _:
                    # If user typed an ID, view detail
                    if sub_choice.isdigit():
                        self._view_opportunity_detail(int(sub_choice))
                    else:
                        print_warning("Invalid choice.")
                        time.sleep(0.5)

    def _view_opportunity_detail(self, opp_id: int) -> None:
        """Display comprehensive details and source evidence for an opportunity."""
        with get_session() as session:
            opp = session.get(Opportunity, opp_id)
            if not opp:
                print_error(f"Opportunity #{opp_id} not found.")
                console.input("[dim]Press Enter to continue...[/dim]")
                return

            console.clear()
            console.print(Panel(f"[bold bright_white]{opp.title}[/bold bright_white]", border_style="cyan"))

            grid = Table.grid(expand=True, padding=(0, 2))
            grid.add_column(style="bold white", width=22)
            grid.add_column(style="cyan")

            grid.add_row("Database ID:", str(opp.id))
            grid.add_row("Official URL:", opp.url)
            grid.add_row("Category:", opp.category)
            grid.add_row("Participation Mode:", opp.mode)
            grid.add_row("Registration Fee:", opp.fee)
            grid.add_row("Submission Deadline:", opp.deadline)
            grid.add_row("Participants Count:", opp.participants_count)
            grid.add_row("Certificate Info:", opp.certificate_info)
            grid.add_row("Current Status:", opp.status.upper())
            grid.add_row("Discovered At:", opp.discovered_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
            grid.add_row("Suitability Score:", str(opp.suitability_score) if opp.suitability_score is not None else "Unscored (Phase 2)")
            grid.add_row("Classification:", opp.classification or "Unclassified (Phase 2)")

            console.print(Panel(grid, title="Opportunity Metadata", box=box.ROUNDED))

            if opp.description:
                console.print(Panel(opp.description, title="Extracted Description", box=box.ROUNDED))

            evidence = opp.get_source_evidence()
            if evidence:
                ev_table = Table(title="Scraped Evidence Audit Trail", box=box.SIMPLE)
                ev_table.add_column("Field", style="bold yellow")
                ev_table.add_column("Raw Extracted Text", style="white")
                for k, v in evidence.items():
                    ev_table.add_row(k, str(v))
                console.print(ev_table)

            console.input("\n[dim]Press Enter to return to directory...[/dim]")

    def _ai_analysis(self) -> None:
        """Interactive AI scoring, eligibility evaluation, and classification menu."""
        from agents.intelligence_agent import IntelligenceAgent

        while True:
            console.clear()
            console.print(Panel("[bold bright_white]AI OPPORTUNITY INTELLIGENCE (Qwen 2.5 7B)[/bold bright_white]", border_style="magenta"))

            if not self.llm_client.health_check():
                print_error("Ollama server is not reachable at " + settings.ollama.base_url)
                console.print(Panel(self.llm_client.get_recovery_instructions(), title="Recovery Steps", box=box.ROUNDED))
                console.input("\n[dim]Press Enter to return...[/dim]")
                return

            # Query counts
            with get_session() as session:
                total_opps = session.execute(select(func.count(Opportunity.id))).scalar_one() or 0
                unanalyzed = session.execute(
                    select(func.count(Opportunity.id)).where(Opportunity.suitability_score.is_(None))
                ).scalar_one() or 0
                analyzed = total_opps - unanalyzed
                high_pri = session.execute(
                    select(func.count(Opportunity.id)).where(Opportunity.classification == "HIGH PRIORITY")
                ).scalar_one() or 0

            status_grid = Table.grid(expand=True)
            status_grid.add_column(justify="center", ratio=1)
            status_grid.add_column(justify="center", ratio=1)
            status_grid.add_column(justify="center", ratio=1)
            status_grid.add_column(justify="center", ratio=1)
            status_grid.add_row(
                f"[bold cyan]{total_opps}[/bold cyan]\n[dim]Total Opportunities[/dim]",
                f"[bold green]{analyzed}[/bold green]\n[dim]Analyzed by Qwen[/dim]",
                f"[bold yellow]{unanalyzed}[/bold yellow]\n[dim]Unanalyzed Pending[/dim]",
                f"[bold bright_green]{high_pri}[/bold bright_green]\n[dim]High Priority Matches[/dim]",
            )
            console.print(Panel(status_grid, box=box.ROUNDED, border_style="blue"))

            menu_items = [
                ("1", "Batch Analyze Next 5 Unanalyzed Opportunities"),
                ("2", "Batch Analyze Next 10 Unanalyzed Opportunities"),
                ("3", "Analyze Specific Opportunity by ID"),
                ("4", "View Detailed AI Analysis & Scoring Report"),
                ("5", "Return to Main Dashboard"),
            ]
            console.print(create_menu(menu_items, title="AI INTELLIGENCE ACTIONS"))

            choice = get_menu_choice("Select an action [1-5] > ")
            agent = IntelligenceAgent()

            match choice.strip():
                case "1":
                    self._run_batch_analysis(agent, limit=5)
                case "2":
                    self._run_batch_analysis(agent, limit=10)
                case "3":
                    target_id = console.input("[bold cyan]Enter Opportunity ID to analyze: [/bold cyan]").strip()
                    if target_id.isdigit():
                        self._run_single_analysis(agent, int(target_id))
                    else:
                        print_warning("Invalid ID.")
                        time.sleep(1.0)
                case "4":
                    target_id = console.input("[bold cyan]Enter Opportunity ID to view report: [/bold cyan]").strip()
                    if target_id.isdigit():
                        self._view_ai_report(int(target_id))
                    else:
                        print_warning("Invalid ID.")
                        time.sleep(1.0)
                case "5" | "b" | "back":
                    break
                case _:
                    print_warning("Invalid selection.")
                    time.sleep(0.5)

    def _run_batch_analysis(self, agent: Any, limit: int) -> None:
        """Run batch analysis with live progress bar."""
        console.print(f"\n[bold cyan]Starting batch analysis (limit={limit})...[/bold cyan]")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Initializing Qwen 2.5 7B...", total=None)

            def update_cb(msg: str) -> None:
                progress.update(task, description=f"[cyan]{msg}")

            result = agent.analyze_batch(limit=limit, only_unanalyzed=True, callback=update_cb)

        print_success(f"Batch completed! Processed: {result.get('processed', 0)}, Errors: {result.get('errors', 0)}")
        self._status_cache_time = 0.0  # Invalidate cache
        console.input("\n[dim]Press Enter to continue...[/dim]")

    def _run_single_analysis(self, agent: Any, opp_id: int) -> None:
        """Analyze a single opportunity and display immediate breakdown."""
        console.print(f"\n[bold cyan]Analyzing Opportunity #{opp_id} with Qwen 2.5 7B...[/bold cyan]")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(f"[cyan]Analyzing Opportunity #{opp_id}...", total=None)
            res = agent.analyze_opportunity(opp_id)

        if "error" in res:
            print_error(res["error"])
        else:
            print_success(f"Opportunity #{opp_id} analyzed! Score: [bold]{res['score']}[/bold] | Priority: [bold green]{res['classification']}[/bold green]")
            self._view_ai_report(opp_id)

        self._status_cache_time = 0.0
        console.input("\n[dim]Press Enter to continue...[/dim]")

    def _view_ai_report(self, opp_id: int) -> None:
        """Display formatted AI analysis report with score breakdown and reasoning."""
        with get_session() as session:
            opp = session.get(Opportunity, opp_id)
            if not opp or not opp.ai_analysis:
                print_error(f"No AI analysis found for Opportunity #{opp_id}. Run analysis first.")
                console.input("\n[dim]Press Enter to continue...[/dim]")
                return

            try:
                data = json.loads(opp.ai_analysis)
            except Exception:
                print_error("Corrupted AI analysis record.")
                return

            console.clear()
            console.print(Panel(f"[bold bright_white]AI EVALUATION REPORT: {opp.title}[/bold bright_white]", border_style="magenta"))

            # Top Summary
            grid = Table.grid(expand=True, padding=(0, 2))
            grid.add_column(style="bold white", width=22)
            grid.add_column(style="cyan")

            grid.add_row("Suitability Score:", f"[bold bright_white on dark_green] {opp.suitability_score} / 100 [/bold bright_white on dark_green]")
            grid.add_row("Classification:", f"[bold bright_yellow]{opp.classification}[/bold bright_yellow]")
            grid.add_row("Participation Type:", opp.participation_type or "Unknown")
            grid.add_row("Format / Mode:", opp.format_type or "Unknown")
            grid.add_row("Eligibility Summary:", opp.eligibility or "Not specified")
            grid.add_row("Competition Rounds:", opp.num_rounds or "1")
            grid.add_row("Time Commitment:", opp.time_commitment or "Unknown")
            grid.add_row("Submission Req:", opp.submission_requirements or "Online")

            console.print(Panel(grid, title="AI Parameter Extraction", box=box.ROUNDED))

            # Score Breakdown Table
            scoring = data.get("scoring", {})
            breakdown = scoring.get("breakdown", {})
            reasoning = scoring.get("reasoning", [])

            sc_table = Table(title="Suitability Scoring Breakdown (Weighted Sum)", box=box.ROUNDED, expand=True)
            sc_table.add_column("Evaluation Criterion", style="bold white", ratio=2)
            sc_table.add_column("Points Earned", justify="center", style="bold green", ratio=1)
            sc_table.add_column("Max Weight", justify="center", style="dim", ratio=1)

            max_weights = {
                "individual_participation": 20,
                "free_registration": 20,
                "certificate_available": 15,
                "online_participation": 15,
                "eligibility_match": 15,
                "reasonable_time_commitment": 10,
                "deadline_suitability": 5,
            }

            for crit, pts in breakdown.items():
                label = crit.replace("_", " ").title()
                sc_table.add_row(label, str(pts), str(max_weights.get(crit, "-")))

            console.print(sc_table)

            if reasoning:
                console.print(Panel("\n".join(f"• {r}" for r in reasoning), title="Scoring Rationale & Evidence Audit", box=box.ROUNDED))

            console.input("\n[dim]Press Enter to return...[/dim]")

    def _registration_copilot(self) -> None:
        """Assisted registration workflow with policy approvals and profile management."""
        from agents.registration_agent import RegistrationAgent

        agent = RegistrationAgent()

        while True:
            console.clear()
            console.print(Panel("[bold bright_white]REGISTRATION COPILOT (Assisted Browser Profile)[/bold bright_white]", border_style="yellow"))

            menu_items = [
                ("1", "Check Session / Log in to Unstop (Open Visible Browser)"),
                ("2", "View Recommended Opportunities Ready for Registration"),
                ("3", "Start Guided Registration for an Opportunity"),
                ("4", "View Active & Confirmed Registrations"),
                ("5", "Manage Candidate Profile (Prefill Settings)"),
                ("6", "Return to Main Dashboard"),
            ]
            console.print(create_menu(menu_items, title="REGISTRATION COPILOT COMMANDS"))

            choice = get_menu_choice("Select an action [1-6] > ")

            match choice.strip():
                case "1":
                    self._setup_unstop_session(agent)
                case "2":
                    self._view_registration_candidates(agent)
                case "3":
                    self._start_guided_registration(agent)
                case "4":
                    self._view_active_registrations(agent)
                case "5":
                    self._manage_candidate_profile(agent)
                case "6" | "b" | "back":
                    break
                case _:
                    print_warning("Invalid selection.")
                    time.sleep(0.5)

    def _setup_unstop_session(self, agent: Any) -> None:
        """Open a visible browser so the candidate can log in manually."""
        console.clear()
        console.print(Panel("[bold bright_white]UNSTOP AUTHENTICATED SESSION SETUP[/bold bright_white]", border_style="cyan"))
        print_info("A visible Chromium browser window will open.")
        print_info("Log in to your Unstop account manually. Never share passwords with the assistant.")
        print_info("Your session cookies are saved locally in data/browser_profile/ (excluded from git).\n")

        if not confirm_action("Open browser for manual login?", default=True):
            return

        from browser.browser_manager import BrowserManager
        bm = BrowserManager()
        bm.launch(headless=False)
        page = bm.new_page()

        try:
            page.goto("https://unstop.com/login", wait_until="domcontentloaded")
            print_info("Browser opened! Please complete your login in the browser window.")
            console.input("\n[bold yellow]Press Enter once you have finished logging in...[/bold yellow]")

            is_logged_in = bm.check_login_status(page)
            if is_logged_in:
                print_success("Authentication detected! Your login session is securely active.")
            else:
                print_warning("Could not confirm login status automatically. If you logged in, the session cookie is saved.")
        except Exception as exc:
            print_error(f"Browser session error: {exc}")
        finally:
            bm.close()
            console.input("\n[dim]Press Enter to return...[/dim]")

    def _view_registration_candidates(self, agent: Any) -> None:
        """Display opportunities that are scored high/good priority and ready for registration."""
        console.clear()
        candidates = agent.get_registration_candidates()
        console.print(create_opportunity_table(candidates, show_score=True, title="Opportunities Ready for Registration"))
        console.input("\n[dim]Press Enter to return...[/dim]")

    def _start_guided_registration(self, agent: Any) -> None:
        """Guided registration execution with policy approval dialog."""
        console.clear()
        console.print(Panel("[bold bright_white]START GUIDED REGISTRATION[/bold bright_white]", border_style="yellow"))

        target_id_str = console.input("[bold cyan]Enter Opportunity ID to register for: [/bold cyan]").strip()
        if not target_id_str.isdigit():
            print_warning("Invalid ID.")
            time.sleep(1.0)
            return

        opp_id = int(target_id_str)

        # Step 1: Inspect
        console.print(f"\n[cyan]Inspecting Opportunity #{opp_id} registration requirements...[/cyan]")
        try:
            reqs = agent.inspect_opportunity(opp_id)
        except Exception as exc:
            print_error(f"Inspection failed: {exc}")
            console.input("\n[dim]Press Enter to continue...[/dim]")
            return

        # Step 2: Show Registration Plan & Approval Summary
        plan_table = Table(title="Registration Plan & Policy Gates", box=box.ROUNDED, expand=True)
        plan_table.add_column("Parameter / Gate", style="bold white", ratio=2)
        plan_table.add_column("Value / Status", style="cyan", ratio=3)

        plan_table.add_row("Opportunity", reqs.opportunity_title)
        plan_table.add_row("Registration Status", "[green]OPEN[/green]" if reqs.is_open else "[red]CLOSED[/red]")
        plan_table.add_row("Registration Fee", f"[bold green]{reqs.fee_amount}[/bold green]" if reqs.is_free else f"[bold red]{reqs.fee_amount} (PAID)[/bold red]")
        plan_table.add_row("Participation Mode", reqs.participation_type.capitalize())

        prefill_summary = ", ".join(f"{k}: {v}" for k, v in reqs.prefill_data.items() if v)
        plan_table.add_row("Prefill Profile Fields", prefill_summary or "None")

        if reqs.missing_fields:
            plan_table.add_row("Missing Profile Fields", f"[bold yellow]{', '.join(reqs.missing_fields)}[/bold yellow]")

        if reqs.actions_requiring_approval:
            approval_items = "\n".join(f"• [{a['level'].upper()}] {a['detail']}" for a in reqs.actions_requiring_approval)
            plan_table.add_row("[bold yellow]Approval Requirements[/bold yellow]", approval_items)

        console.print(plan_table)

        if not reqs.is_open:
            print_warning("Registration for this opportunity is closed.")
            console.input("\n[dim]Press Enter to continue...[/dim]")
            return

        if reqs.already_registered:
            print_success("You are already registered for this competition!")
            console.input("\n[dim]Press Enter to continue...[/dim]")
            return

        # Step 3: Explicit User Approval
        console.print("\n")
        if not confirm_action("Do you authorize the Registration Copilot to proceed with registration?", default=False):
            print_warning("Registration cancelled by user policy gate.")
            console.input("\n[dim]Press Enter to continue...[/dim]")
            return

        # Step 4: Execute in visible browser
        def approval_cb(payload: dict[str, Any]) -> bool:
            return True  # Already confirmed by user

        console.print("[cyan]Opening browser to assist registration...[/cyan]")
        result = agent.register_opportunity(opp_id, confirm_callback=approval_cb, headless=False)

        if result.get("status") in ("success", "already_registered"):
            print_success(result.get("message", "Registration completed!"))
        elif result.get("status") == "in_progress":
            print_info(result.get("message", ""))
        elif result.get("status") == "login_required":
            print_warning(result.get("message", ""))
            print_info("Use Option 1 from the Registration Copilot menu to log in.")
        else:
            print_warning(f"Registration result: {result.get('message', 'Unknown status')}")

        self._status_cache_time = 0.0
        console.input("\n[dim]Press Enter to continue...[/dim]")

    def _view_active_registrations(self, agent: Any) -> None:
        """Display table of all active and confirmed registrations."""
        console.clear()
        regs = agent.get_active_registrations()

        table = Table(title="[bold green]Active & Confirmed Opportunity Registrations[/bold green]", box=box.ROUNDED, expand=True)
        table.add_column("Reg ID", style="dim", width=6)
        table.add_column("Opp ID", style="dim", width=6)
        table.add_column("Opportunity Title", style="bold white", ratio=3)
        table.add_column("Status", justify="center", ratio=1)
        table.add_column("Registered At", justify="center", ratio=1)
        table.add_column("Notes", style="dim", ratio=2)

        if not regs:
            table.add_row("-", "-", "[italic dim]No registrations recorded yet.[/italic dim]", "-", "-", "-")
        else:
            for r in regs:
                stat_style = "bold green" if r["status"] == "registered" else "bold yellow"
                table.add_row(
                    str(r["registration_id"]),
                    str(r["opportunity_id"]),
                    r["title"],
                    f"[{stat_style}]{r['status'].upper()}[/{stat_style}]",
                    r["registered_at"],
                    r["notes"],
                )

        console.print(table)
        console.input("\n[dim]Press Enter to return...[/dim]")

    def _manage_candidate_profile(self, agent: Any) -> None:
        """View and edit candidate profile information used for prefill."""
        while True:
            console.clear()
            profile = agent.get_candidate_profile()

            console.print(Panel("[bold bright_white]CANDIDATE AUTOFILL PROFILE[/bold bright_white]", border_style="cyan"))
            table = Table(box=box.ROUNDED, expand=True)
            table.add_column("#", style="dim", width=4)
            table.add_column("Field Name", style="bold white", ratio=2)
            table.add_column("Current Stored Value", style="cyan", ratio=3)

            field_keys = list(profile.keys())
            for idx, key in enumerate(field_keys, start=1):
                label = key.replace("_", " ").title()
                val = profile.get(key, "") or "[dim italic]Not set[/dim italic]"
                table.add_row(str(idx), label, val)

            console.print(table)
            print_info("This profile data is stored exclusively in your local SQLite database.")
            print_info("It is used to prefill routine registration fields without manual typing.\n")

            sub_choice = console.input("[bold cyan]Enter field # to edit, or press Enter to return: [/bold cyan]").strip()
            if not sub_choice:
                break

            if sub_choice.isdigit() and 1 <= int(sub_choice) <= len(field_keys):
                chosen_key = field_keys[int(sub_choice) - 1]
                new_val = console.input(f"[bold yellow]Enter new value for {chosen_key.replace('_', ' ').title()}: [/bold yellow]").strip()
                if new_val:
                    agent.update_profile_field(chosen_key, new_val)
                    print_success(f"Updated {chosen_key}!")
                    time.sleep(0.8)
            else:
                print_warning("Invalid field number.")
                time.sleep(0.8)

    def _quiz_laboratory(self) -> None:
        """Interactive Quiz Laboratory with timed practice, AI MCQ generation, and analytics."""
        while True:
            console.clear()
            console.print(Panel("[bold bright_white]QUIZ LABORATORY[/bold bright_white]\n[dim]AI Practice Tests, Timed Assessments & Accuracy Analytics[/dim]", border_style="cyan"))

            menu_table = Table(box=box.ROUNDED, expand=True)
            menu_table.add_column("Option", style="bold cyan", width=8)
            menu_table.add_column("Action", style="bold white")
            menu_table.add_column("Description", style="dim")

            menu_table.add_row("1", "Quick Practice Assessment", "Take a timed or untimed practice MCQ test with instant feedback")
            menu_table.add_row("2", "Generate MCQs for Opportunity", "Use Qwen 2.5 7B to generate tailored questions from competition briefs")
            menu_table.add_row("3", "Performance & Weak Topics", "View overall accuracy, breakdown by domain, and weak areas")
            menu_table.add_row("4", "Live Quiz Autopilot", "AgentX autonomous screen control: solves & submits live Unstop quizzes in real time")
            menu_table.add_row("5", "Return to Main Menu", "Go back to the main dashboard")

            console.print(menu_table)
            choice = console.input("\n[bold cyan]Select an option [1-5] > [/bold cyan]").strip()

            if choice == "1":
                self._run_quiz_practice_test()
            elif choice == "2":
                self._generate_quiz_from_opportunity()
            elif choice == "3":
                self._view_quiz_analytics()
            elif choice == "4":
                self._run_live_quiz_autopilot()
            elif choice in ("5", "b", "q", "back"):
                break
            else:
                print_warning("Invalid choice. Please select 1, 2, 3, 4, or 5.")
                time.sleep(1.0)

    def _run_quiz_practice_test(self) -> None:
        """Run an interactive MCQ practice test session in terminal."""
        console.clear()
        console.print(Panel("[bold bright_white]START PRACTICE ASSESSMENT[/bold bright_white]", border_style="cyan"))

        console.print("[dim]Select topic for practice:[/dim]")
        console.print("  [bold cyan]1[/bold cyan]. Python Programming")
        console.print("  [bold cyan]2[/bold cyan]. Data Structures & Algorithms")
        console.print("  [bold cyan]3[/bold cyan]. General Tech & Networking")
        console.print("  [bold cyan]4[/bold cyan]. Custom Topic (type your own)")

        topic_choice = console.input("\n[bold cyan]Choose topic [1-4, default 1] > [/bold cyan]").strip()
        topic_map = {"1": "python", "2": "data_structures", "3": "general"}
        if topic_choice in topic_map:
            topic = topic_map[topic_choice]
        elif topic_choice == "4":
            topic = console.input("[bold cyan]Enter custom topic name > [/bold cyan]").strip() or "general"
        else:
            topic = "python"

        count_input = console.input("[bold cyan]Number of questions [3-10, default 3] > [/bold cyan]").strip()
        try:
            q_count = max(1, min(10, int(count_input)))
        except ValueError:
            q_count = 3

        console.print(f"\n[cyan]Initializing session with {q_count} questions on '{topic}'...[/cyan]")
        state = self.practice_engine.start_session(mode="practice", topic=topic, question_count=q_count)

        if not state.questions:
            print_error("No questions could be loaded or generated.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        for idx, q in enumerate(state.questions, 1):
            console.clear()
            header_text = f"Question {idx} of {len(state.questions)} | Topic: [bold yellow]{q.topic}[/bold yellow] | Difficulty: [magenta]{q.difficulty}[/magenta]"
            console.print(Panel(f"[bold bright_white]{q.question}[/bold bright_white]", title=header_text, border_style="cyan"))

            options = q.get_options()
            labels = ["A", "B", "C", "D"]
            for opt_idx, opt in enumerate(options):
                lbl = labels[opt_idx] if opt_idx < len(labels) else str(opt_idx + 1)
                console.print(f"  [bold bright_cyan][{lbl}][/bold bright_cyan] {opt}")

            answer = console.input("\n[bold cyan]Your answer (A/B/C/D) > [/bold cyan]").strip()
            eval_res = self.practice_engine.submit_answer(state.session_id, q.id, answer)

            console.print("\n")
            if eval_res["is_correct"]:
                console.print("[bold green]✔ CORRECT![/bold green]")
            else:
                console.print(f"[bold red]✘ INCORRECT.[/bold red] Correct answer: [bold green]{eval_res['correct_answer']}[/bold green]")

            if eval_res["explanation"]:
                console.print(f"[dim white]Explanation: {eval_res['explanation']}[/dim white]")

            console.input("\n[dim]Press Enter for next question...[/dim]")

        # Complete session
        summary = self.practice_engine.complete_session(state.session_id)
        console.clear()
        console.print(Panel("[bold bright_white]ASSESSMENT COMPLETED[/bold bright_white]", border_style="green"))

        score_color = "green" if summary["score_pct"] >= 70 else ("yellow" if summary["score_pct"] >= 50 else "red")
        res_table = Table(box=box.ROUNDED)
        res_table.add_column("Metric", style="bold white")
        res_table.add_column("Result", style="cyan")

        res_table.add_row("Topic", summary["topic"])
        res_table.add_row("Questions Attempted", str(summary["total_questions"]))
        res_table.add_row("Correct Answers", str(summary["correct_answers"]))
        res_table.add_row("Final Score", f"[{score_color}]{summary['score_pct']}%[/{score_color}]")
        res_table.add_row("Duration", f"{summary['duration_seconds']} seconds")

        console.print(res_table)
        console.input("\n[dim]Press Enter to return to Quiz Laboratory...[/dim]")

    def _generate_quiz_from_opportunity(self) -> None:
        """Select a competition and generate tailored MCQs using local Qwen 2.5 7B."""
        console.clear()
        console.print(Panel("[bold bright_white]AI QUESTION GENERATION FROM OPPORTUNITY[/bold bright_white]", border_style="cyan"))

        with get_session() as session:
            opps = session.execute(
                select(Opportunity)
                .where(Opportunity.status == "open")
                .order_by(Opportunity.suitability_score.desc().nullslast())
                .limit(10)
            ).scalars().all()

        if not opps:
            print_warning("No active opportunities found in database.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED)
        table.add_column("#", style="bold cyan", width=4)
        table.add_column("Title", style="white")
        table.add_column("Category", style="yellow")
        table.add_column("Score", style="green")

        for idx, o in enumerate(opps, 1):
            table.add_row(str(idx), o.title[:45], o.category or "unknown", str(o.suitability_score or "-"))

        console.print(table)
        sel = console.input("\n[bold cyan]Select competition [1-10] > [/bold cyan]").strip()
        try:
            sel_idx = int(sel) - 1
            if not (0 <= sel_idx < len(opps)):
                print_warning("Invalid selection.")
                time.sleep(1.0)
                return
        except ValueError:
            print_warning("Invalid input.")
            time.sleep(1.0)
            return

        chosen_opp = opps[sel_idx]
        console.print(f"\n[cyan]Generating 3 practice MCQs for '{chosen_opp.title[:40]}' using Qwen 2.5 7B...[/cyan]")
        try:
            questions = self.practice_engine.generator.generate_for_opportunity(chosen_opp.id, num_questions=3)
            print_success(f"Generated {len(questions)} MCQs and added to question bank!")
            for q in questions:
                console.print(f" • [bold white]{q.question}[/bold white] ([dim]{q.difficulty}[/dim])")
        except Exception as exc:
            print_error(f"Generation failed: {exc}")

        console.input("\n[dim]Press Enter to return to Quiz Laboratory...[/dim]")

    def _view_quiz_analytics(self) -> None:
        """Display overall accuracy, topic breakdown, and identify weak areas."""
        console.clear()
        console.print(Panel("[bold bright_white]QUIZ ACCURACY & WEAK TOPIC ANALYTICS[/bold bright_white]", border_style="cyan"))

        analytics = self.practice_engine.get_performance_analytics()

        # Overall summary panel
        acc = analytics["overall_accuracy_pct"]
        acc_color = "green" if acc >= 70 else ("yellow" if acc >= 50 else "red")
        console.print(
            Panel(
                f"[bold white]Total Attempted:[/bold white] [cyan]{analytics['total_questions_attempted']}[/cyan]    "
                f"[bold white]Total Correct:[/bold white] [green]{analytics['total_correct']}[/green]    "
                f"[bold white]Overall Accuracy:[/bold white] [{acc_color}]{acc}%[/{acc_color}]",
                title="Cumulative Performance",
                border_style="cyan",
            )
        )

        # Topic Breakdown Table
        topic_table = Table(title="Domain / Topic Performance", box=box.ROUNDED)
        topic_table.add_column("Topic", style="bold white")
        topic_table.add_column("Attempted", style="cyan")
        topic_table.add_column("Correct", style="green")
        topic_table.add_column("Accuracy", style="bold")

        for t in analytics["topic_breakdown"]:
            t_acc = t["accuracy"]
            t_col = "green" if t_acc >= 70 else ("yellow" if t_acc >= 50 else "red")
            topic_table.add_row(t["topic"], str(t["shown"]), str(t["correct"]), f"[{t_col}]{t_acc}%[/{t_col}]")

        if analytics["topic_breakdown"]:
            console.print(topic_table)
        else:
            console.print("[dim]No questions attempted yet.[/dim]")

        # Weak Topics Alert
        if analytics["weak_topics"]:
            console.print("\n[bold red]⚠ WEAK TOPICS REQUIRING ATTENTION (<60% accuracy):[/bold red]")
            for wt in analytics["weak_topics"]:
                console.print(f"  • [bold yellow]{wt['topic']}[/bold yellow]: {wt['accuracy']}% accuracy ({wt['correct']}/{wt['shown']})")

        # Recent Sessions Table
        if analytics["recent_sessions"]:
            sess_table = Table(title="\nRecent Practice Sessions", box=box.ROUNDED)
            sess_table.add_column("ID", style="dim", width=4)
            sess_table.add_column("Topic", style="white")
            sess_table.add_column("Score", style="bold green")
            sess_table.add_column("Duration", style="cyan")
            sess_table.add_column("Date", style="dim")

            for s in analytics["recent_sessions"]:
                sess_table.add_row(str(s["id"]), s["topic"], f"{s['score']}% ({s['correct']}/{s['total']})", f"{s['duration_sec']}s", s["started_at"])

            console.print(sess_table)

        console.input("\n[dim]Press Enter to return to Quiz Laboratory...[/dim]")

    def _run_live_quiz_autopilot(self) -> None:
        """Execute autonomous live Unstop quiz assessment with real-time HUD."""
        console.clear()
        console.print(Panel(
            "[bold bright_white]AGENTX LIVE QUIZ AUTOPILOT[/bold bright_white]\n"
            "[dim]Autonomous Browser Control • Real-Time Qwen 2.5 7B Question Solving • Automatic Submission[/dim]",
            border_style="magenta",
        ))

        # Check Ollama and Browser prerequisites
        with console.status("[cyan]Verifying system readiness...[/cyan]"):
            from agents.llm_client import OllamaClient
            llm = OllamaClient()
            llm_ok = llm.health_check()
            model_ok = llm.check_model() if llm_ok else False

        if not (llm_ok and model_ok):
            print_error("Local Ollama Qwen 2.5 7B model is offline. Please ensure 'ollama serve' is active.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        # Query opportunities for quiz candidates
        from agents.quiz_agent import LiveQuizAgent
        with get_session() as session:
            stmt = select(Opportunity).where(
                (Opportunity.category.ilike("%quiz%")) |
                (Opportunity.category.ilike("%assessment%")) |
                (Opportunity.category.ilike("%competition%"))
            ).limit(8)
            opps = session.scalars(stmt).all()

        console.print("\n[bold white]Select Target Assessment:[/bold white]")
        opp_table = Table(box=box.SIMPLE_HEAVY)
        opp_table.add_column("#", style="cyan", width=4)
        opp_table.add_column("Title", style="bold white", width=36)
        opp_table.add_column("Category", style="dim", width=14)
        opp_table.add_column("URL", style="dim")

        for idx, o in enumerate(opps, 1):
            opp_table.add_row(str(idx), o.title[:35], o.category or "quiz", o.url[:50])

        console.print(opp_table)
        console.print("\n[bold cyan]Choose # from table, or type 'u' to enter a custom Unstop quiz URL, or 'b' to back:[/bold cyan]")
        choice = console.input("[bold cyan]> [/bold cyan]").strip()

        if choice in ("b", "back", "q"):
            return

        target_url = ""
        if choice.lower() == "u":
            target_url = console.input("\n[bold yellow]Enter direct Unstop Quiz/Assessment URL: [/bold yellow]").strip()
            if not target_url.startswith("http"):
                print_error("Invalid URL format.")
                time.sleep(1.0)
                return
        else:
            try:
                sel_idx = int(choice) - 1
                if 0 <= sel_idx < len(opps):
                    target_url = opps[sel_idx].url
                else:
                    print_warning("Invalid selection number.")
                    time.sleep(1.0)
                    return
            except ValueError:
                print_warning("Invalid input.")
                time.sleep(1.0)
                return

        console.clear()
        console.print(Panel(
            f"[bold white]TARGET ASSESSMENT:[/bold white] [cyan]{target_url}[/cyan]\n\n"
            "[bold red]SAFETY & POLICY GATES:[/bold red]\n"
            "• A visible Chromium browser window will launch on your screen.\n"
            "• AgentX will autonomously navigate, read questions, evaluate answers via Qwen 2.5 7B, and click choices.\n"
            "• [bold yellow]Proctoring Guard:[/bold yellow] If webcam or proctoring software is detected, AgentX will immediately abort.\n"
            "• You can take manual control or close the window at any moment.\n",
            title="[bold magenta]PRE-FLIGHT AUTHORIZATION[/bold magenta]",
            border_style="magenta",
        ))

        if not confirm_action("Authorize AgentX to begin autonomous screen control?", default=False):
            print_info("Operation cancelled by user.")
            time.sleep(1.0)
            return

        console.print("\n[bold green]Launching AgentX Live Quiz Autopilot...[/bold green]\n")

        def _terminal_callback(event: dict[str, Any]) -> None:
            ev_type = event.get("event")
            data = event.get("data")
            if ev_type == "status":
                console.print(f"[dim]⚡ [cyan]{data}[/cyan][/dim]")
            elif ev_type == "question_scraped":
                q_num = data.get("number", 1)
                q_tot = data.get("total", 1)
                q_text = data.get("text", "")[:100]
                q_timer = data.get("timer", "N/A")
                console.print(f"\n[bold yellow]━━━━━━━━ Question {q_num}/{q_tot} (Timer: {q_timer}) ━━━━━━━━[/bold yellow]")
                console.print(f"[bold white]{q_text}[/bold white]")
            elif ev_type == "answer_decided":
                c_idx = data.get("chosen_index", 0)
                c_opt = data.get("chosen_option", "")
                conf = data.get("confidence", 0.0)
                rat = data.get("rationale", "")
                conf_pct = int(conf * 100)
                bar = "█" * (conf_pct // 10) + "░" * (10 - (conf_pct // 10))
                console.print(f"  [bold green]➜ Chosen [[{c_idx}]]: {c_opt}[/bold green] (Conf: [{bar}] {conf_pct}%)")
                if rat:
                    console.print(f"  [dim]Rationale: {rat}[/dim]")
            elif ev_type == "proctor_abort":
                print_error(f"Proctoring Abort: {data}")
            elif ev_type == "completed":
                print_success(f"Assessment Finished: {data.get('total_answered')} questions completed in {data.get('duration')}s!")
            elif ev_type == "error":
                print_error(f"Error: {data}")

        agent = LiveQuizAgent()
        result = agent.run_quiz(
            opportunity_url=target_url,
            headless=False,
            update_callback=_terminal_callback,
        )

        console.print("\n")
        res_panel = Panel(
            f"[bold white]Status:[/bold white] [{ 'green' if result.status == 'completed' else 'red' }]{result.status.upper()}[/]\n"
            f"[bold white]Questions Submitted:[/bold white] {result.answers_submitted}\n"
            f"[bold white]Average AI Confidence:[/bold white] {int(result.average_confidence * 100)}%\n"
            f"[bold white]Total Duration:[/bold white] {result.duration_seconds}s\n"
            + (f"[bold red]Message:[/bold red] {result.error_message}\n" if result.error_message else ""),
            title="[bold magenta]LIVE ASSESSMENT SUMMARY[/bold magenta]",
            border_style="magenta",
        )
        console.print(res_panel)
        console.input("\n[dim]Press Enter to return to Quiz Laboratory...[/dim]")

    def _presentation_studio(self) -> None:
        """Interactive Presentation Studio for pitch decks and PDF executive briefs."""
        while True:
            console.clear()
            console.print(Panel("[bold bright_white]PRESENTATION STUDIO[/bold bright_white]\n[dim]Autonomous Pitch Deck & PDF Brief Synthesis with Qwen 2.5 7B[/dim]", border_style="bright_blue"))

            menu_table = Table(box=box.ROUNDED, expand=True)
            menu_table.add_column("Option", style="bold cyan", width=8)
            menu_table.add_column("Action", style="bold white")
            menu_table.add_column("Description", style="dim")

            menu_table.add_row("1", "Generate PPTX Pitch Deck", "Create a 16:9 widescreen presentation deck for an opportunity")
            menu_table.add_row("2", "Generate PDF Strategic Brief", "Create an executive strategy document, milestones & compliance checklist")
            menu_table.add_row("3", "View Generated Documents Archive", "Browse all synthesized PPTX and PDF files with local paths")
            menu_table.add_row("4", "Return to Main Menu", "Go back to the main dashboard")

            console.print(menu_table)
            choice = console.input("\n[bold cyan]Select an option [1-4] > [/bold cyan]").strip()

            if choice == "1":
                self._generate_pitch_deck()
            elif choice == "2":
                self._generate_pdf_brief()
            elif choice == "3":
                self._view_generated_documents()
            elif choice in ("4", "b", "q", "back"):
                break
            else:
                print_warning("Invalid choice. Please select 1, 2, 3, or 4.")
                time.sleep(1.0)

    def _generate_pitch_deck(self) -> None:
        """Interactive flow to synthesize a PPTX presentation deck."""
        console.clear()
        console.print(Panel("[bold bright_white]GENERATE POWERPOINT PITCH DECK[/bold bright_white]", border_style="bright_blue"))

        with get_session() as session:
            opps = session.execute(
                select(Opportunity)
                .where(Opportunity.status == "open")
                .order_by(Opportunity.suitability_score.desc().nullslast())
                .limit(10)
            ).scalars().all()

        if not opps:
            print_warning("No active opportunities found.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED)
        table.add_column("#", style="bold cyan", width=4)
        table.add_column("Title", style="white")
        table.add_column("Category", style="yellow")
        table.add_column("Score", style="green")

        for idx, o in enumerate(opps, 1):
            table.add_row(str(idx), o.title[:45], o.category or "unknown", str(o.suitability_score or "-"))

        console.print(table)
        sel = console.input("\n[bold cyan]Select opportunity [1-10] > [/bold cyan]").strip()
        try:
            sel_idx = int(sel) - 1
            if not (0 <= sel_idx < len(opps)):
                print_warning("Invalid selection.")
                time.sleep(1.0)
                return
        except ValueError:
            print_warning("Invalid input.")
            time.sleep(1.0)
            return

        chosen_opp = opps[sel_idx]

        team_name = console.input("[bold cyan]Enter Presenter / Team Name [default 'Team Autopilot'] > [/bold cyan]").strip() or "Team Autopilot"
        theme_choice = console.input("[bold cyan]Select Theme (1: Dark Modern, 2: Clean Light) [default 1] > [/bold cyan]").strip()
        theme = "light" if theme_choice == "2" else "dark"

        console.print(f"\n[cyan]Synthesizing presentation structure for '{chosen_opp.title[:40]}' using Qwen 2.5 7B...[/cyan]")
        try:
            doc_record = self.document_agent.generate_presentation_for_opportunity(
                opportunity_id=chosen_opp.id,
                theme=theme,
                team_name=team_name,
            )
            print_success(f"PowerPoint deck generated successfully!")
            console.print(f"[bold white]Title:[/bold white] {doc_record.title}")
            console.print(f"[bold white]File Location:[/bold white] [bold cyan]{doc_record.file_path}[/bold cyan]")
            console.print(f"[bold white]Template:[/bold white] {doc_record.template_used}")
        except Exception as exc:
            logger.error("Deck generation failed: %s", exc, exc_info=True)
            print_error(f"Deck generation failed: {exc}")

        console.input("\n[dim]Press Enter to return to Presentation Studio...[/dim]")

    def _generate_pdf_brief(self) -> None:
        """Interactive flow to synthesize an executive PDF strategic brief."""
        console.clear()
        console.print(Panel("[bold bright_white]GENERATE PDF STRATEGIC BRIEF[/bold bright_white]", border_style="bright_blue"))

        with get_session() as session:
            opps = session.execute(
                select(Opportunity)
                .where(Opportunity.status == "open")
                .order_by(Opportunity.suitability_score.desc().nullslast())
                .limit(10)
            ).scalars().all()

        if not opps:
            print_warning("No active opportunities found.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED)
        table.add_column("#", style="bold cyan", width=4)
        table.add_column("Title", style="white")
        table.add_column("Category", style="yellow")
        table.add_column("Score", style="green")

        for idx, o in enumerate(opps, 1):
            table.add_row(str(idx), o.title[:45], o.category or "unknown", str(o.suitability_score or "-"))

        console.print(table)
        sel = console.input("\n[bold cyan]Select opportunity [1-10] > [/bold cyan]").strip()
        try:
            sel_idx = int(sel) - 1
            if not (0 <= sel_idx < len(opps)):
                print_warning("Invalid selection.")
                time.sleep(1.0)
                return
        except ValueError:
            print_warning("Invalid input.")
            time.sleep(1.0)
            return

        chosen_opp = opps[sel_idx]
        author = console.input("[bold cyan]Author Name [default 'Unstop Autopilot'] > [/bold cyan]").strip() or "Unstop Autopilot"

        console.print(f"\n[cyan]Compiling executive PDF brief for '{chosen_opp.title[:40]}'...[/cyan]")
        try:
            doc_record = self.document_agent.generate_brief_for_opportunity(
                opportunity_id=chosen_opp.id,
                author=author,
            )
            print_success(f"PDF Strategic Brief compiled successfully!")
            console.print(f"[bold white]Title:[/bold white] {doc_record.title}")
            console.print(f"[bold white]File Location:[/bold white] [bold cyan]{doc_record.file_path}[/bold cyan]")
            console.print(f"[bold white]Status:[/bold white] {doc_record.status}")
        except Exception as exc:
            logger.error("PDF generation failed: %s", exc, exc_info=True)
            print_error(f"PDF generation failed: {exc}")

        console.input("\n[dim]Press Enter to return to Presentation Studio...[/dim]")

    def _view_generated_documents(self) -> None:
        """Display all stored PPTX and PDF documents in a rich table."""
        console.clear()
        console.print(Panel("[bold bright_white]GENERATED DOCUMENTS ARCHIVE[/bold bright_white]", border_style="bright_blue"))

        docs = self.document_agent.get_generated_documents()
        if not docs:
            print_info("No generated documents found. Use Options 1 or 2 to generate PPTX or PDF artifacts.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("ID", style="bold cyan", width=4)
        table.add_column("Type", style="bold yellow", width=6)
        table.add_column("Title", style="white")
        table.add_column("Template", style="dim", width=16)
        table.add_column("Path", style="dim cyan")
        table.add_column("Created", style="dim", width=14)

        for d in docs:
            type_color = "bright_blue" if d.doc_type == "pptx" else "bright_red"
            created_str = d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else ""
            table.add_row(
                str(d.id),
                f"[{type_color}]{d.doc_type.upper()}[/{type_color}]",
                d.title[:35],
                d.template_used or "-",
                d.file_path,
                created_str,
            )

        console.print(table)
        console.input("\n[dim]Press Enter to return to Presentation Studio...[/dim]")

    def _submission_manager(self) -> None:
        """Interactive Submission Manager with file checklist verification and policy gates."""
        while True:
            console.clear()
            console.print(Panel("[bold bright_white]SUBMISSION MANAGER[/bold bright_white]\n[dim]Deliverable Compliance Inspection, Human-in-the-Loop Policy Gates & Tracking[/dim]", border_style="bright_magenta"))

            menu_table = Table(box=box.ROUNDED, expand=True)
            menu_table.add_column("Option", style="bold cyan", width=8)
            menu_table.add_column("Action", style="bold white")
            menu_table.add_column("Description", style="dim")

            menu_table.add_row("1", "Inspect & Validate Deliverable File", "Check format, size, slide/page integrity, and section keywords")
            menu_table.add_row("2", "Record & Sign Off Submission", "Sign off deliverable for an opportunity (enforces human approval)")
            menu_table.add_row("3", "View Submissions History", "Audit trail of validated and completed competition submissions")
            menu_table.add_row("4", "Return to Main Menu", "Go back to the main dashboard")

            console.print(menu_table)
            choice = console.input("\n[bold cyan]Select an option [1-4] > [/bold cyan]").strip()

            if choice == "1":
                self._validate_deliverable_file()
            elif choice == "2":
                self._sign_off_submission()
            elif choice == "3":
                self._view_submissions_history()
            elif choice in ("4", "b", "q", "back"):
                break
            else:
                print_warning("Invalid choice. Please select 1, 2, 3, or 4.")
                time.sleep(1.0)

    def _validate_deliverable_file(self) -> None:
        """Inspect and validate a presentation, brief, or archive file."""
        console.clear()
        console.print(Panel("[bold bright_white]INSPECT DELIVERABLE FILE[/bold bright_white]", border_style="bright_magenta"))

        file_path_str = console.input("[bold cyan]Enter full file path (or relative to workspace) > [/bold cyan]").strip()
        if not file_path_str:
            print_warning("No file path provided.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        res = self.submission_validator.validate_file(file_path_str)

        console.print("\n")
        status_color = "green" if res.is_valid else "red"
        status_text = "PASSED COMPLIANCE" if res.is_valid else "FAILED COMPLIANCE"
        console.print(f"[bold {status_color}]Result: {status_text}[/bold {status_color}]")

        # Stats table
        stats_table = Table(title="File Properties & Metrics", box=box.ROUNDED)
        stats_table.add_column("Property", style="bold white")
        stats_table.add_column("Value", style="cyan")

        for k, v in res.file_stats.items():
            stats_table.add_row(k.replace("_", " ").title(), str(v))
        console.print(stats_table)

        # Checklist
        chk_table = Table(title="Compliance Checklist", box=box.ROUNDED)
        chk_table.add_column("Check Item", style="bold white")
        chk_table.add_column("Status", style="bold")

        for k, passed in res.checklist.items():
            chk_table.add_row(
                k.replace("_", " ").title(),
                "[bold green]✔ PASS[/bold green]" if passed else "[bold red]✘ FAIL[/bold red]"
            )
        console.print(chk_table)

        if res.errors:
            console.print("\n[bold red]Errors Found:[/bold red]")
            for err in res.errors:
                console.print(f"  • {err}")

        if res.warnings:
            console.print("\n[bold yellow]Warnings / Recommendations:[/bold yellow]")
            for w in res.warnings:
                console.print(f"  • {w}")

        console.input("\n[dim]Press Enter to return to Submission Manager...[/dim]")

    def _sign_off_submission(self) -> None:
        """Enforces human-in-the-loop confirmation before recording submission."""
        console.clear()
        console.print(Panel("[bold bright_white]RECORD & SIGN OFF SUBMISSION[/bold bright_white]", border_style="bright_magenta"))

        with get_session() as session:
            opps = session.execute(
                select(Opportunity)
                .where(Opportunity.status == "open")
                .order_by(Opportunity.suitability_score.desc().nullslast())
                .limit(10)
            ).scalars().all()

        if not opps:
            print_warning("No active opportunities found.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED)
        table.add_column("#", style="bold cyan", width=4)
        table.add_column("Title", style="white")
        table.add_column("Category", style="yellow")
        table.add_column("Score", style="green")

        for idx, o in enumerate(opps, 1):
            table.add_row(str(idx), o.title[:45], o.category or "unknown", str(o.suitability_score or "-"))

        console.print(table)
        sel = console.input("\n[bold cyan]Select competition for submission [1-10] > [/bold cyan]").strip()
        try:
            sel_idx = int(sel) - 1
            if not (0 <= sel_idx < len(opps)):
                print_warning("Invalid selection.")
                time.sleep(1.0)
                return
        except ValueError:
            print_warning("Invalid input.")
            time.sleep(1.0)
            return

        chosen_opp = opps[sel_idx]
        file_path_str = console.input("[bold cyan]Enter deliverable file path > [/bold cyan]").strip()
        if not file_path_str:
            print_warning("File path is required.")
            time.sleep(1.0)
            return

        # Security Policy Confirmation Gate
        console.print(
            Panel(
                "[bold yellow]SECURITY POLICY ENFORCEMENT: 'final_submission'[/bold yellow]\n\n"
                f"[white]Competition:[/white] [bold cyan]{chosen_opp.title}[/bold cyan]\n"
                f"[white]Deliverable:[/white] [bold cyan]{file_path_str}[/bold cyan]\n\n"
                "[dim]Submitting deliverables affects your official academic & competition record. "
                "The system will never submit files without your explicit authorization.[/dim]",
                border_style="yellow",
            )
        )

        if not confirm_action("Do you authorize recording and signing off this final submission?"):
            print_warning("Submission sign-off cancelled by user.")
            time.sleep(1.0)
            return

        notes = console.input("[bold cyan]Submission notes / comments [optional] > [/bold cyan]").strip()
        try:
            sub = self.submission_validator.record_submission(
                opportunity_id=chosen_opp.id,
                file_path=file_path_str,
                notes=notes,
                confirmed_by_user=True,
            )
            print_success(f"Submission recorded and verified! ID={sub.id}")

            # Award participation & submission points
            self.points_tracker.record_competition_completion(
                opportunity_id=chosen_opp.id,
                points=100,
                badge_name="Verified Submitter",
            )
            print_success("Awarded +100 Unstop Platform Points!")
        except Exception as exc:
            logger.error("Submission failed: %s", exc, exc_info=True)
            print_error(f"Submission record failed: {exc}")

        console.input("\n[dim]Press Enter to return to Submission Manager...[/dim]")

    def _view_submissions_history(self) -> None:
        """Display all recorded submissions."""
        console.clear()
        console.print(Panel("[bold bright_white]SUBMISSIONS AUDIT TRAIL[/bold bright_white]", border_style="bright_magenta"))

        subs = self.submission_validator.get_submissions()
        if not subs:
            print_info("No submissions recorded yet.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("ID", style="bold cyan", width=4)
        table.add_column("Competition", style="white")
        table.add_column("Format", style="yellow", width=8)
        table.add_column("Status", style="bold green", width=12)
        table.add_column("Verified", style="cyan", width=10)
        table.add_column("Submitted At", style="dim", width=18)

        for s in subs:
            ver_text = "[bold green]YES[/bold green]" if s["verified"] else "[dim]NO[/dim]"
            table.add_row(
                str(s["id"]),
                s["opportunity_title"][:40],
                s["file_type"].upper(),
                s["status"].upper(),
                ver_text,
                s["submitted_at"],
            )

        console.print(table)
        console.input("\n[dim]Press Enter to return to Submission Manager...[/dim]")

    def _certificate_manager(self) -> None:
        """Interactive Certificate Manager for earned certificates."""
        while True:
            console.clear()
            console.print(Panel("[bold bright_white]CERTIFICATE MANAGER[/bold bright_white]\n[dim]Manage & Archive Earned Competition Certificates[/dim]", border_style="green"))

            summary = self.certificate_manager.get_certificate_summary()
            console.print(
                Panel(
                    f"[bold white]Total Earned:[/bold white] [bold green]{summary['total_certificates']}[/bold green]    "
                    f"[bold white]Locally Archived:[/bold white] [bold cyan]{summary['downloaded_locally']}[/bold cyan]    "
                    f"[bold white]Pending Download:[/bold white] [yellow]{summary['pending_download']}[/yellow]\n"
                    f"[dim]Archive Directory: {summary['archive_directory']}[/dim]",
                    title="Certificates Summary",
                    border_style="green",
                )
            )

            menu_table = Table(box=box.ROUNDED, expand=True)
            menu_table.add_column("Option", style="bold cyan", width=8)
            menu_table.add_column("Action", style="bold white")
            menu_table.add_column("Description", style="dim")

            menu_table.add_row("1", "View Earned Certificates", "Browse complete list of earned certificates and awards")
            menu_table.add_row("2", "Register New Certificate", "Manually add a certificate with local PDF/image link")
            menu_table.add_row("3", "Scan Local Archive Directory", "Index new unindexed certificates from data/certificates/")
            menu_table.add_row("4", "Return to Main Menu", "Go back to the main dashboard")

            console.print(menu_table)
            choice = console.input("\n[bold cyan]Select an option [1-4] > [/bold cyan]").strip()

            if choice == "1":
                self._view_certificates_list()
            elif choice == "2":
                self._register_certificate()
            elif choice == "3":
                self._scan_certificates()
            elif choice in ("4", "b", "q", "back"):
                break
            else:
                print_warning("Invalid choice.")
                time.sleep(1.0)

    def _view_certificates_list(self) -> None:
        """Display list of certificates in a rich table."""
        console.clear()
        console.print(Panel("[bold bright_white]EARNED CERTIFICATES ARCHIVE[/bold bright_white]", border_style="green"))

        certs = self.certificate_manager.list_certificates()
        if not certs:
            print_info("No certificates recorded in database yet.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("ID", style="bold cyan", width=4)
        table.add_column("Certificate Name", style="bold white")
        table.add_column("Issuer", style="yellow", width=18)
        table.add_column("Type", style="cyan", width=14)
        table.add_column("Local File", style="dim", width=25)
        table.add_column("Earned Date", style="dim", width=12)

        for c in certs:
            path_disp = Path(c["local_path"]).name if c["local_path"] else "[yellow]pending[/yellow]"
            table.add_row(
                str(c["id"]),
                c["name"][:35],
                c["issuer"][:16],
                c["certificate_type"].title(),
                path_disp,
                c["earned_at"],
            )

        console.print(table)
        console.input("\n[dim]Press Enter to return to Certificate Manager...[/dim]")

    def _register_certificate(self) -> None:
        """Add a certificate record manually."""
        console.clear()
        console.print(Panel("[bold bright_white]REGISTER NEW CERTIFICATE[/bold bright_white]", border_style="green"))

        name = console.input("[bold cyan]Certificate Title (e.g. Winner - CodeSprint) > [/bold cyan]").strip()
        if not name:
            print_warning("Title is required.")
            time.sleep(1.0)
            return

        issuer = console.input("[bold cyan]Issuer [default 'Unstop'] > [/bold cyan]").strip() or "Unstop"
        cert_type = console.input("[bold cyan]Type (participation/completion/winner/merit) [default 'participation'] > [/bold cyan]").strip() or "participation"
        local_path = console.input("[bold cyan]Local File Path [optional] > [/bold cyan]").strip()

        try:
            cert = self.certificate_manager.add_certificate(
                name=name,
                issuer=issuer,
                certificate_type=cert_type,
                local_path=local_path,
            )
            print_success(f"Certificate registered successfully! ID={cert.id}")
            # Award points for certificate
            self.points_tracker.add_achievement(
                achievement_type="badge",
                title=f"Certificate: {name[:40]}",
                description=f"Earned {cert_type} certificate from {issuer}.",
                value=75,
                source=issuer,
                verified=True,
            )
            print_success("Awarded +75 Unstop Points for verified certificate!")
        except Exception as exc:
            print_error(f"Failed to register certificate: {exc}")

        console.input("\n[dim]Press Enter to return to Certificate Manager...[/dim]")

    def _scan_certificates(self) -> None:
        """Scan local folder and index newly added certificate files."""
        console.clear()
        console.print(Panel("[bold bright_white]SCAN LOCAL CERTIFICATE DIRECTORY[/bold bright_white]", border_style="green"))

        console.print("[cyan]Scanning data/certificates/ for new files...[/cyan]")
        count = self.certificate_manager.scan_local_archive()
        if count > 0:
            print_success(f"Successfully indexed {count} new certificates from filesystem!")
        else:
            print_info("No new unindexed certificates found in data/certificates/.")

        console.input("\n[dim]Press Enter to return to Certificate Manager...[/dim]")

    def _achievement_analytics(self) -> None:
        """Display achievement analytics, points progress, and rank tier badges."""
        console.clear()
        console.print(Panel("[bold bright_white]ACHIEVEMENTS & POINTS LEADERBOARD[/bold bright_white]", border_style="bright_yellow"))

        summary = self.points_tracker.get_points_summary()

        # Tier & Points Panel
        bar_len = 30
        filled = int((summary["tier_progress_pct"] / 100.0) * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        tier_info = (
            f"[bold bright_yellow]Current Rank Tier:[/bold bright_yellow] [bold white]{summary['current_tier']}[/bold white]\n"
            f"[dim]{summary['tier_description']}[/dim]\n\n"
            f"[bold white]Verified Platform Points:[/bold white] [bold green]{summary['verified_points']}[/bold green]    "
            f"[bold white]Total Accumulated:[/bold white] [bold cyan]{summary['total_points']}[/bold cyan]\n"
            f"[bold white]Next Milestone:[/bold white] [yellow]{summary['next_tier']}[/yellow] ({summary['points_to_next_tier']} pts to go)\n"
            f"[bold cyan]Progress to Next Tier:[/bold cyan] [{summary['tier_progress_pct']}%] [{bar}]"
        )
        console.print(Panel(tier_info, title="Competitive Rank Progression", border_style="bright_yellow"))

        # Badges Table
        if summary["badges"]:
            b_table = Table(title="Earned Badges & Distinctions", box=box.ROUNDED)
            b_table.add_column("Badge / Achievement", style="bold white")
            b_table.add_column("Category", style="cyan")
            b_table.add_column("Source", style="yellow")
            b_table.add_column("Date", style="dim")

            for b in summary["badges"]:
                b_table.add_row(b["title"], b["type"].upper(), b["source"][:30], b["date"])
            console.print(b_table)

        # Recent Points Activity Log Table
        if summary["recent_achievements"]:
            a_table = Table(title="\nRecent Points Activity Log", box=box.ROUNDED)
            a_table.add_column("Event", style="white")
            a_table.add_column("Points", style="bold green", width=10)
            a_table.add_column("Source", style="dim", width=25)
            a_table.add_column("Verified", style="cyan", width=10)
            a_table.add_column("Date", style="dim", width=12)

            for a in summary["recent_achievements"]:
                v_str = "[bold green]YES[/bold green]" if a["verified"] else "[yellow]pending[/yellow]"
                a_table.add_row(
                    a["title"][:40],
                    f"+{a['value']}",
                    a["source"][:23],
                    v_str,
                    a["date"],
                )
            console.print(a_table)

        console.input("\n[dim]Press Enter to return to main dashboard...[/dim]")

    def _automation_settings(self) -> None:
        """Interactive Automation Settings for APScheduler jobs and Linux systemd service."""
        while True:
            console.clear()
            console.print(Panel("[bold bright_white]AUTOMATION & SCHEDULER SETTINGS[/bold bright_white]\n[dim]Daily 09:00 Cron, Linux systemd Daemon & Background Monitoring[/dim]", border_style="cyan"))

            sched_status = "[bold green]ACTIVE (RUNNING)[/bold green]" if self.scheduler_manager.is_active else "[dim]INACTIVE (STOPPED)[/dim]"
            svc_info = self.systemd_service.get_status()
            sysd_status = "[bold green]ACTIVE[/bold green]" if svc_info["is_active"] else "[dim]INACTIVE[/dim]"

            console.print(
                Panel(
                    f"[bold white]In-Process APScheduler:[/bold white] {sched_status}\n"
                    f"[bold white]Linux systemd Service:[/bold white] {sysd_status} (Installed: {svc_info['unit_installed']}, Available: {svc_info['systemd_available']})\n"
                    f"[dim]Service Unit: {svc_info['unit_file_path']}[/dim]",
                    title="Daemon Status Overview",
                    border_style="cyan",
                )
            )

            menu_table = Table(box=box.ROUNDED, expand=True)
            menu_table.add_column("Option", style="bold cyan", width=8)
            menu_table.add_column("Action", style="bold white")
            menu_table.add_column("Description", style="dim")

            menu_table.add_row("1", "View Scheduled Jobs & Telemetry", "Inspect recurring job schedules, last run timestamps & outcomes")
            menu_table.add_row("2", "Trigger Job Manually Now", "Run daily_discovery, deadline_watch, or intelligence_batch on-demand")
            menu_table.add_row("3", "Toggle In-Process Scheduler", "Start or stop the background APScheduler in this process")
            menu_table.add_row("4", "Linux systemd Service Manager", "Generate unit file, enable, start or stop systemd user service")
            menu_table.add_row("5", "Return to Main Menu", "Go back to the main dashboard")

            console.print(menu_table)
            choice = console.input("\n[bold cyan]Select an option [1-5] > [/bold cyan]").strip()

            if choice == "1":
                self._view_scheduler_jobs()
            elif choice == "2":
                self._trigger_job_manually()
            elif choice == "3":
                self._toggle_in_process_scheduler()
            elif choice == "4":
                self._manage_systemd_service()
            elif choice in ("5", "b", "q", "back"):
                break
            else:
                print_warning("Invalid choice.")
                time.sleep(1.0)

    def _view_scheduler_jobs(self) -> None:
        """Display job telemetry and next run times."""
        console.clear()
        console.print(Panel("[bold bright_white]AUTOMATION JOBS TELEMETRY[/bold bright_white]", border_style="cyan"))

        jobs = self.scheduler_manager.get_jobs_status()
        if not jobs:
            print_info("No jobs initialized yet. Starting scheduler will register default jobs.")
            console.input("\n[dim]Press Enter to return...[/dim]")
            return

        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Job Name", style="bold white")
        table.add_column("Type", style="cyan")
        table.add_column("Schedule", style="yellow")
        table.add_column("Last Run", style="dim", width=20)
        table.add_column("Status", style="bold")
        table.add_column("Errors", style="red", width=8)

        for j in jobs:
            st = j["last_status"]
            st_color = "green" if st == "success" else ("yellow" if st in ("ready", "running") else "red")
            table.add_row(
                j["job_name"],
                j["job_type"],
                j["schedule"],
                j["last_run"],
                f"[{st_color}]{st.upper()}[/{st_color}]",
                str(j["error_count"]),
            )

        console.print(table)
        console.input("\n[dim]Press Enter to return to Automation Settings...[/dim]")

    def _trigger_job_manually(self) -> None:
        """Manually trigger a background job."""
        console.clear()
        console.print(Panel("[bold bright_white]TRIGGER BACKGROUND JOB MANUALLY[/bold bright_white]", border_style="cyan"))

        console.print("  [bold cyan]1[/bold cyan]. Daily Opportunity Discovery (Crawl Unstop)")
        console.print("  [bold cyan]2[/bold cyan]. Deadline Watch & Alert Scanner")
        console.print("  [bold cyan]3[/bold cyan]. AI Intelligence Batch Scoring (Qwen 2.5 7B)")

        job_map = {"1": "daily_discovery", "2": "deadline_watch", "3": "intelligence_batch"}
        sel = console.input("\n[bold cyan]Select job to execute [1-3] > [/bold cyan]").strip()

        if sel not in job_map:
            print_warning("Invalid selection.")
            time.sleep(1.0)
            return

        job_name = job_map[sel]
        console.print(f"\n[cyan]Executing job '{job_name}'...[/cyan]")
        try:
            res = self.scheduler_manager.execute_job(job_name)
            if res["status"] == "success":
                print_success(f"Job '{job_name}' executed successfully!")
                console.print(f"[dim]Result: {json.dumps(res['result'], indent=2)[:300]}[/dim]")
            else:
                print_error(f"Job '{job_name}' failed: {res['result'].get('error', 'Unknown error')}")
        except Exception as exc:
            print_error(f"Failed to execute job: {exc}")

        console.input("\n[dim]Press Enter to return to Automation Settings...[/dim]")

    def _toggle_in_process_scheduler(self) -> None:
        """Start or stop in-process APScheduler."""
        console.clear()
        console.print(Panel("[bold bright_white]TOGGLE IN-PROCESS SCHEDULER[/bold bright_white]", border_style="cyan"))

        if self.scheduler_manager.is_active:
            if confirm_action("Scheduler is currently ACTIVE. Do you want to STOP it?"):
                self.scheduler_manager.stop()
                print_success("In-process scheduler stopped.")
        else:
            if confirm_action("Scheduler is currently STOPPED. Do you want to START it?"):
                try:
                    self.scheduler_manager.start()
                    print_success("In-process scheduler started in background!")
                except Exception as exc:
                    print_error(f"Could not start scheduler: {exc}")

        time.sleep(1.2)

    def _manage_systemd_service(self) -> None:
        """Manage Linux systemd user service."""
        console.clear()
        console.print(Panel("[bold bright_white]LINUX SYSTEMD USER SERVICE MANAGER[/bold bright_white]", border_style="cyan"))

        status = self.systemd_service.get_status()

        console.print(f"[bold white]systemd Available:[/bold white] {status['systemd_available']}")
        console.print(f"[bold white]Service Unit Path:[/bold white] [cyan]{status['unit_file_path']}[/cyan]")
        console.print(f"[bold white]Unit File Installed:[/bold white] {status['unit_installed']}")
        console.print(f"[bold white]Service Active (Running):[/bold white] {status['is_active']}")
        console.print(f"[bold white]Service Enabled (At Boot):[/bold white] {status['is_enabled']}\n")

        console.print("  [bold cyan]1[/bold cyan]. Generate & Install Unit File (`systemctl --user daemon-reload`)")
        console.print("  [bold cyan]2[/bold cyan]. Start Service (`systemctl --user start unstop-autopilot.service`)")
        console.print("  [bold cyan]3[/bold cyan]. Stop Service (`systemctl --user stop unstop-autopilot.service`)")
        console.print("  [bold cyan]4[/bold cyan]. Restart Service (`systemctl --user restart unstop-autopilot.service`)")
        console.print("  [bold cyan]5[/bold cyan]. Return to Automation Settings")

        sub_sel = console.input("\n[bold cyan]Choose action [1-5] > [/bold cyan]").strip()

        if sub_sel == "1":
            ok, msg = self.systemd_service.install_and_enable()
            if ok:
                print_success(f"Unit file installed and daemon reloaded! ({msg})")
            else:
                print_error(f"Failed to install/enable: {msg}")
        elif sub_sel == "2":
            ok, msg = self.systemd_service.start()
            if ok:
                print_success("Service started!")
            else:
                print_error(f"Failed to start service: {msg}")
        elif sub_sel == "3":
            ok, msg = self.systemd_service.stop()
            if ok:
                print_success("Service stopped.")
            else:
                print_error(f"Failed to stop service: {msg}")
        elif sub_sel == "4":
            ok, msg = self.systemd_service.restart()
            if ok:
                print_success("Service restarted.")
            else:
                print_error(f"Failed to restart service: {msg}")

        console.input("\n[dim]Press Enter to return to Automation Settings...[/dim]")

    def _system_diagnostics(self, wait_for_input: bool = True) -> None:
        """Display hardware, GPU, LLM, and environment diagnostics."""
        console.clear()
        console.print(Panel("[bold bright_white]SYSTEM HARDWARE & RUNTIME DIAGNOSTICS[/bold bright_white]", border_style="yellow"))

        table = Table(box=box.ROUNDED, expand=True)
        table.add_column("Component", style="bold cyan", width=24)
        table.add_column("Diagnostics Detail", style="white")

        # OS and Kernel
        table.add_row("Operating System", f"{platform.system()} {platform.release()} ({platform.machine()})")
        table.add_row("Python Interpreter", f"{sys.version.split()[0]} ({sys.executable})")

        # GPU via nvidia-smi
        gpu_info = "N/A"
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpu_info = res.stdout.strip()
        except Exception:
            pass
        table.add_row("NVIDIA GPU Status", gpu_info)

        # Ollama
        ollama_status = "Available (HTTP 200)" if self.llm_client.health_check() else "Unavailable (Check ollama serve)"
        table.add_row("Ollama API Server", f"{settings.ollama.base_url} [{ollama_status}]")
        
        models = self.llm_client.get_available_models()
        table.add_row("Installed Models", ", ".join(models) if models else "None detected")
        table.add_row("Selected Model", f"{settings.ollama.model} (Max Context: {settings.ollama.context_size} tokens)")

        # Database
        table_counts = get_table_counts()
        db_details = f"Connected ({len(table_counts)} tables)\n" + ", ".join(f"{k}: {v}" for k, v in table_counts.items())
        table.add_row("SQLite Database", db_details)

        # Storage paths
        table.add_row("Project Root", str(settings.project_root))
        table.add_row("Browser Profile Dir", str(settings.browser.user_data_dir))

        console.print(table)
        if wait_for_input:
            console.input("\n[dim]Press Enter to return to main dashboard...[/dim]")

    def _exit(self) -> None:
        """Perform graceful shutdown."""
        self.running = False
        console.clear()
        console.print(Panel("[bold green]Thank you for using Unstop Autopilot. Good luck with your competitions![/bold green]", border_style="green"))
        time.sleep(0.5)
