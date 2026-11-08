"""Browser lifecycle manager using Playwright's synchronous API.

Provides a context-manager-based wrapper around a Chromium persistent
browser context, configured via the project's central ``Settings`` object.
"""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from config import settings

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manage a single Chromium browser instance with a persistent profile.

    Usage::

        with BrowserManager() as bm:
            page = bm.new_page()
            page.goto("https://unstop.com")
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: BrowserContext | None = None
        # _context is an alias kept for semantic clarity; with
        # launch_persistent_context the context *is* the browser handle.
        self._context: BrowserContext | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def launch(self, headless: bool | None = None) -> None:
        """Launch Chromium with a persistent user-data profile.

        Parameters
        ----------
        headless:
            Override the ``settings.browser.headless`` flag for this
            session.  When *None*, the value from settings is used.
        """
        if self.is_running():
            logger.warning("Browser is already running — skipping launch.")
            return

        resolved_headless: bool = (
            headless if headless is not None else settings.browser.headless
        )
        user_data_dir: Path = Path(settings.browser.user_data_dir)
        user_data_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Launching Chromium (headless=%s, slow_mo=%d, user_data_dir=%s)",
            resolved_headless,
            settings.browser.slow_mo,
            user_data_dir,
        )

        try:
            self._playwright = sync_playwright().start()

            launch_kwargs: dict = {
                "headless": resolved_headless,
                "slow_mo": settings.browser.slow_mo,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }

            # Playwright's user_agent kwarg on persistent context.
            user_agent: str = settings.browser.user_agent
            if user_agent:
                launch_kwargs["user_agent"] = user_agent

            context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                **launch_kwargs,
            )

            # Set the default navigation / action timeout.
            context.set_default_timeout(settings.browser.timeout)
            context.set_default_navigation_timeout(settings.browser.timeout)

            self._browser = context
            self._context = context
            logger.info("Browser launched successfully.")

        except Exception:
            logger.exception("Failed to launch browser.")
            # Ensure partial state is cleaned up.
            self.close()
            raise

    def new_page(self) -> Page:
        """Create and return a new page in the current browser context.

        Raises
        ------
        RuntimeError
            If the browser has not been launched yet.
        """
        if not self.is_running():
            raise RuntimeError(
                "Browser is not running. Call launch() first or use the "
                "context manager."
            )

        assert self._context is not None  # guarded by is_running()
        page: Page = self._context.new_page()
        logger.debug("New page created (total open: %d).", len(self._context.pages))
        return page

    def close(self) -> None:
        """Perform a clean shutdown of the browser and Playwright."""
        if self._context is not None:
            try:
                self._context.close()
                logger.info("Browser context closed.")
            except Exception:
                logger.exception("Error while closing browser context.")
            finally:
                self._context = None
                self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
                logger.debug("Playwright stopped.")
            except Exception:
                logger.exception("Error while stopping Playwright.")
            finally:
                self._playwright = None

    def is_running(self) -> bool:
        """Return *True* if the browser context is active."""
        return self._context is not None

    def check_login_status(self, page: Page | None = None) -> bool:
        """Probe whether the current session is authenticated on Unstop."""
        close_page = False
        if page is None:
            if not self.is_running():
                return False
            page = self.new_page()
            close_page = True

        try:
            current_url = page.url
            if "unstop.com" not in current_url:
                page.goto("https://unstop.com", wait_until="domcontentloaded")

            # Check for logged-in indicators
            logged_in_selectors = [
                "un-user-avatar",
                ".user-profile",
                "a[href*='/user/']",
                "[class*='profile_icon']",
                "[class*='user_logged_in']",
                "img[alt*='profile' i]",
            ]
            for sel in logged_in_selectors:
                if page.query_selector(sel) is not None:
                    return True

            # If login button is visible, definitely not logged in
            login_btn = page.query_selector("button:has-text('Login'), a:has-text('Login'), a[href*='/login']")
            if login_btn is not None:
                return False

            return False
        except Exception as exc:
            logger.debug("Error checking login status: %s", exc)
            return False
        finally:
            if close_page:
                try:
                    page.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> BrowserManager:
        self.launch()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()
