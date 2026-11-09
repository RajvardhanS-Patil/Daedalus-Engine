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

            resolved_slow_mo = settings.browser.slow_mo
            if not resolved_headless and resolved_slow_mo == 0:
                resolved_slow_mo = 250

            launch_kwargs: dict = {
                "headless": resolved_headless,
                "slow_mo": resolved_slow_mo,
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

            # Inject glowing AI visual cursor across all pages in headed mode
            if not resolved_headless:
                cursor_script = """
                (() => {
                    function setupCursor() {
                        if (document.getElementById('agentx-cursor')) return;
                        const style = document.createElement('style');
                        style.textContent = `
                            #agentx-cursor {
                                position: fixed;
                                width: 20px;
                                height: 20px;
                                border-radius: 50%;
                                background: radial-gradient(circle, #ff0055 40%, rgba(255, 0, 85, 0.4) 75%, transparent 100%);
                                box-shadow: 0 0 12px #ff0055, 0 0 24px rgba(255, 0, 85, 0.9);
                                pointer-events: none;
                                z-index: 2147483647;
                                transform: translate(-50%, -50%);
                                transition: left 0.08s ease-out, top 0.08s ease-out;
                            }
                            #agentx-ring {
                                position: fixed;
                                width: 40px;
                                height: 40px;
                                border-radius: 50%;
                                border: 2px solid #00f0ff;
                                box-shadow: 0 0 12px #00f0ff;
                                pointer-events: none;
                                z-index: 2147483646;
                                transform: translate(-50%, -50%);
                                transition: left 0.14s ease-out, top 0.14s ease-out, transform 0.15s ease;
                            }
                            #agentx-label {
                                position: fixed;
                                background: rgba(8, 8, 16, 0.9);
                                color: #00f0ff;
                                font-family: monospace;
                                font-size: 11px;
                                font-weight: bold;
                                padding: 2px 7px;
                                border-radius: 4px;
                                border: 1px solid #00f0ff;
                                box-shadow: 0 0 8px rgba(0, 240, 255, 0.4);
                                pointer-events: none;
                                z-index: 2147483647;
                                transform: translate(16px, -16px);
                                transition: left 0.14s ease-out, top 0.14s ease-out;
                                letter-spacing: 0.5px;
                            }
                        `;
                        const parent = document.head || document.documentElement;
                        parent.appendChild(style);

                        const cursor = document.createElement('div');
                        cursor.id = 'agentx-cursor';
                        const ring = document.createElement('div');
                        ring.id = 'agentx-ring';
                        const label = document.createElement('div');
                        label.id = 'agentx-label';
                        label.textContent = 'AgentX AI';

                        const b = document.body || document.documentElement;
                        b.appendChild(cursor);
                        b.appendChild(ring);
                        b.appendChild(label);
                    }

                    window.addEventListener('mousemove', (e) => {
                        const c = document.getElementById('agentx-cursor');
                        const r = document.getElementById('agentx-ring');
                        const l = document.getElementById('agentx-label');
                        if (c) { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }
                        if (r) { r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px'; }
                        if (l) { l.style.left = e.clientX + 'px'; l.style.top = e.clientY + 'px'; }
                    }, { passive: true });

                    window.addEventListener('click', (e) => {
                        const r = document.getElementById('agentx-ring');
                        if (r) {
                            r.style.transform = 'translate(-50%, -50%) scale(1.8)';
                            r.style.borderColor = '#ff0055';
                            setTimeout(() => {
                                r.style.transform = 'translate(-50%, -50%) scale(1.0)';
                                r.style.borderColor = '#00f0ff';
                            }, 220);
                        }
                    }, { passive: true });

                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', setupCursor);
                    } else {
                        setupCursor();
                    }
                })();
                """
                context.add_init_script(cursor_script)

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
        import time as _time

        close_page = False
        if page is None:
            if not self.is_running():
                return False
            page = self.new_page()
            close_page = True

        try:
            # 1. Primary check: inspect persistent authentication cookies
            if self._context is not None:
                try:
                    cookies = self._context.cookies(["https://unstop.com", "https://api.unstop.com"])
                    auth_cookie_names = {"access_token", "token", "jwt", "session_id", "user_id"}
                    for cookie in cookies:
                        c_name = cookie.get("name", "").lower()
                        c_val = cookie.get("value", "")
                        if (c_name in auth_cookie_names or "token" in c_name) and len(c_val) > 10:
                            expires = cookie.get("expires", -1)
                            if expires == -1 or expires > _time.time():
                                logger.info("Valid authentication token '%s' detected in session cookies.", cookie.get("name"))
                                return True
                except Exception as c_exc:
                    logger.debug("Cookie inspection error: %s", c_exc)

            # 2. Secondary check: DOM logged-in indicators
            current_url = page.url
            if "unstop.com" not in current_url:
                page.goto("https://unstop.com", wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

            logged_in_selectors = [
                "un-user-avatar",
                ".user-profile",
                "a[href*='/user/']",
                "[class*='profile_icon']",
                "[class*='user_logged_in']",
                "img[alt*='profile' i]",
                "a[href*='/dashboard']",
                "[class*='avatar']",
            ]
            for sel in logged_in_selectors:
                el = page.query_selector(sel)
                if el is not None and el.is_visible():
                    return True

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
