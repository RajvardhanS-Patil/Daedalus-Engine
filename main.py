"""
Unstop Autopilot — Main Entry Point

Local-First Autonomous AI Competition Management System for Linux.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agents.discovery_agent import DiscoveryAgent
from agents.llm_client import OllamaClient
from config import settings
from database.connection import init_database
from terminal.dashboard import Dashboard


def setup_logging(verbose: bool = False) -> None:
    """Configure structured file and console logging."""
    log_dir = settings.logging.log_file.parent
    log_dir.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if verbose else getattr(logging, settings.logging.level.upper(), logging.INFO)

    file_handler = logging.FileHandler(settings.logging.log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)

    # Suppress verbose 3rd party logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Unstop Autopilot — Local-First Autonomous AI Competition Management System",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Run opportunity discovery headless crawl immediately without interactive UI",
    )
    parser.add_argument(
        "--analyze",
        type=int,
        nargs="?",
        const=5,
        default=None,
        metavar="LIMIT",
        help="Run Qwen 2.5 7B AI analysis on unanalyzed opportunities (default limit: 5)",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print system health & hardware diagnostics and exit",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run background scheduler daemon (APScheduler) continuously",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Launch visible browser window to log in and save your Unstop account session",
    )
    parser.add_argument(
        "--quiz",
        type=str,
        metavar="URL",
        help="Launch autonomous live quiz solver on the specified Unstop assessment URL",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    return parser.parse_args()


def main() -> int:
    """Application main entry point."""
    args = parse_args()
    setup_logging(verbose=args.verbose)

    logger = logging.getLogger("main")
    logger.info("Initializing Unstop Autopilot v%s...", settings.version)

    # Initialize SQLite database schema
    init_database()

    # CLI modes
    if args.diagnostics:
        dashboard = Dashboard()
        dashboard._system_diagnostics(wait_for_input=False)
        return 0

    if args.login:
        from browser.browser_manager import BrowserManager

        print("Launching visible Chromium browser for Unstop authentication...")
        bm = BrowserManager()
        bm.launch(headless=False)
        page = bm.new_page()
        try:
            page.goto("https://unstop.com/login", wait_until="domcontentloaded")
            print("\n" + "=" * 65)
            print("Chromium window opened! Log in to your Unstop account.")
            print("Credentials are never touched by code; cookies save to data/browser_profile/.")
            print("=" * 65 + "\n")
            input("Press [Enter] here once you have finished logging into Unstop... ")
            if bm.check_login_status(page):
                print("\n[SUCCESS] Authentication detected! Your Unstop account is now active.")
            else:
                print("\n[INFO] Session saved to data/browser_profile/. If login was completed, the session is active.")
        except Exception as exc:
            print(f"\n[ERROR] Login session encountered an error: {exc}")
        finally:
            bm.close()
            print("Browser closed cleanly.")
        return 0

    if args.quiz:
        from agents.quiz_agent import LiveQuizAgent

        print(f"Launching AgentX Autonomous Live Quiz Solver on {args.quiz}...")
        agent = LiveQuizAgent()

        def _cli_cb(event):
            ev = event.get("event")
            data = event.get("data")
            if ev == "status":
                print(f"[*] {data}")
            elif ev == "question_scraped":
                print(f"\n[Q{data.get('number')}/{data.get('total')}] {data.get('text')[:80]}...")
            elif ev == "answer_decided":
                print(f" -> Selected [{data.get('chosen_index')}]: {data.get('chosen_option')} (Conf: {int(data.get('confidence', 0)*100)}%)")
            elif ev == "proctor_abort":
                print(f"\n[ABORTED] {data}")
            elif ev == "completed":
                print(f"\n[DONE] Completed {data.get('total_answered')} questions in {data.get('duration')}s.")
            elif ev == "error":
                print(f"\n[ERROR] {data}")

        res = agent.run_quiz(opportunity_url=args.quiz, headless=False, update_callback=_cli_cb)
        print(f"\nFinal Status: {res.status.upper()}")
        print(f"Questions Submitted: {res.answers_submitted}")
        print(f"Average Confidence: {int(res.average_confidence * 100)}%")
        print(f"Duration: {res.duration_seconds}s")
        if res.error_message:
            print(f"Details: {res.error_message}")
        return 0 if res.status == "completed" else 1

    if args.discover:
        print("Starting non-interactive opportunity discovery crawl...")
        agent = DiscoveryAgent()
        summary = agent.run_discovery(callback=lambda msg: print(f"[*] {msg}"))
        print("\nDiscovery Complete:")
        print(f" - Found: {summary.get('total_found')}")
        print(f" - New Saved: {summary.get('new_saved')}")
        print(f" - Updated: {summary.get('updated')}")
        return 0

    if args.analyze is not None:
        from agents.intelligence_agent import IntelligenceAgent

        print(f"Starting Qwen 2.5 7B intelligence evaluation (limit: {args.analyze})...")
        agent = IntelligenceAgent()
        res = agent.analyze_batch(
            limit=args.analyze,
            only_unanalyzed=True,
            callback=lambda msg: print(f"[*] {msg}"),
        )
        print("\nAI Analysis Complete:")
        print(f" - Processed: {res.get('processed', 0)}")
        print(f" - Errors: {res.get('errors', 0)}")
        for item in res.get("results", []):
            print(f"   • #{item['opportunity_id']} {item['title'][:40]}: Score={item['score']} [{item['classification']}]")
        return 0

    if args.daemon:
        import time
        from scheduler.jobs import SchedulerManager

        print("Starting Unstop Autopilot Background Scheduler Daemon...")
        mgr = SchedulerManager()
        try:
            mgr.start()
            print("[*] Scheduler running. Jobs active: daily_discovery (09:00 UTC), deadline_watch (4h), intelligence_batch (6h).")
            print("[*] Press Ctrl+C to terminate daemon.")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nReceived termination signal. Shutting down scheduler gracefully...")
        finally:
            mgr.stop()
            print("Scheduler daemon stopped.")
        return 0

    # Default interactive UI
    try:
        dashboard = Dashboard()
        dashboard.run()
        return 0
    except KeyboardInterrupt:
        print("\nExiting Unstop Autopilot.")
        return 0
    except Exception as exc:
        logger.critical("Fatal error starting Unstop Autopilot: %s", exc, exc_info=True)
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
