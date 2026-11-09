"""
Unstop Autopilot — Master Agent

Central orchestrator that routes tasks to specialised agents via
predefined tool functions.  Never executes arbitrary code — all
available operations are explicitly defined in ``AVAILABLE_TOOLS``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from config import settings
from database.connection import get_session
from database.models import ActivityLog, Opportunity

logger = logging.getLogger(__name__)


class MasterAgent:
    """Central orchestrator for all agent operations.

    Routes tasks to specialised agents via predefined tool functions.
    Never executes arbitrary code — every callable operation is an
    explicit method on this class.

    Attributes:
        AVAILABLE_TOOLS: Mapping of tool name → human-readable
            description of what the tool does.
    """

    AVAILABLE_TOOLS: dict[str, str] = {
        "discover_opportunities": "Run opportunity discovery pipeline",
        "analyze_opportunity": "AI analysis of a specific opportunity",
        "list_opportunities": "List stored opportunities with optional filters",
        "check_system": "Check system health (database, Ollama, browser)",
        "solve_live_quiz": "Autonomous execution and solving of an Unstop live quiz assessment",
    }

    def __init__(self) -> None:
        self._discovery_agent: Any = None
        self._llm_client: Any = None
        logger.info("MasterAgent initialised with tools: %s", list(self.AVAILABLE_TOOLS))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_tool(self, tool_name: str, **kwargs: Any) -> dict:
        """Execute a predefined tool by name.

        Args:
            tool_name: One of the keys in :attr:`AVAILABLE_TOOLS`.
            **kwargs: Tool-specific keyword arguments.

        Returns:
            A result dictionary.  On success the shape depends on the
            tool.  On error, the dict contains an ``error`` key.
        """
        if tool_name not in self.AVAILABLE_TOOLS:
            logger.warning("Unknown tool requested: %s", tool_name)
            return {
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(self.AVAILABLE_TOOLS),
            }

        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            logger.error("Tool handler not implemented: %s", tool_name)
            return {"error": f"Tool not implemented: {tool_name}"}

        logger.info("Executing tool: %s with kwargs=%s", tool_name, list(kwargs))
        try:
            return handler(**kwargs)
        except Exception as exc:
            logger.error("Tool '%s' failed: %s", tool_name, exc, exc_info=True)
            return {"error": f"Tool '{tool_name}' failed: {exc}"}

    def get_available_tools(self) -> dict[str, str]:
        """Return the mapping of available tool names to descriptions.

        Returns:
            A copy of the ``AVAILABLE_TOOLS`` dictionary.
        """
        return dict(self.AVAILABLE_TOOLS)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _tool_discover_opportunities(self, **kwargs: Any) -> dict:
        """Run the opportunity discovery pipeline.

        Accepts an optional ``callback`` kwarg for progress messages.

        Returns:
            The summary dict from :meth:`DiscoveryAgent.run_discovery`.
        """
        if self._discovery_agent is None:
            from agents.discovery_agent import DiscoveryAgent

            self._discovery_agent = DiscoveryAgent()

        callback = kwargs.get("callback")
        result = self._discovery_agent.run_discovery(callback=callback)
        return {"tool": "discover_opportunities", "result": result}

    def _tool_analyze_opportunity(self, **kwargs: Any) -> dict:
        """Run AI analysis on a specific opportunity.

        Kwargs:
            opportunity_id (int): The database ID of the opportunity to
                analyse.

        Returns:
            Analysis result dictionary with suitability score and
            classification, or an error dict.
        """
        opportunity_id = kwargs.get("opportunity_id")
        if opportunity_id is None:
            return {"error": "Missing required parameter: opportunity_id"}

        try:
            opportunity_id = int(opportunity_id)
        except (ValueError, TypeError):
            return {"error": f"Invalid opportunity_id: {opportunity_id}"}

        try:
            from agents.intelligence_agent import IntelligenceAgent

            agent = IntelligenceAgent()
            result = agent.analyze_opportunity(opportunity_id)
            return {
                "tool": "analyze_opportunity",
                "opportunity_id": opportunity_id,
                "result": result,
            }
        except Exception as exc:
            logger.error("Analysis failed for opportunity %s: %s", opportunity_id, exc)
            return {"error": f"Analysis failed: {exc}"}

    def _tool_list_opportunities(self, **kwargs: Any) -> dict:
        """List stored opportunities with optional filters.

        Kwargs:
            status (str): Filter by status (``open``, ``closed``,
                ``expired``).  Defaults to ``open``.
            limit (int): Maximum number of results.  Defaults to 50.
            category (str): Filter by category.
            min_score (int): Minimum suitability score.

        Returns:
            Dict with ``opportunities`` list and ``total`` count.
        """
        status_filter = kwargs.get("status", "open")
        limit = min(int(kwargs.get("limit", 50)), 200)
        category = kwargs.get("category")
        min_score = kwargs.get("min_score")

        try:
            with get_session() as session:
                query = select(Opportunity)

                if status_filter:
                    query = query.where(Opportunity.status == status_filter)
                if category:
                    query = query.where(Opportunity.category == category)
                if min_score is not None:
                    try:
                        min_score = int(min_score)
                        query = query.where(
                            Opportunity.suitability_score >= min_score
                        )
                    except (ValueError, TypeError):
                        pass

                query = query.order_by(Opportunity.discovered_at.desc()).limit(limit)
                results = session.execute(query).scalars().all()

                # Get total count for the filter
                count_query = select(func.count(Opportunity.id))
                if status_filter:
                    count_query = count_query.where(
                        Opportunity.status == status_filter
                    )
                total = session.execute(count_query).scalar() or 0

                opportunities = []
                for opp in results:
                    opportunities.append({
                        "id": opp.id,
                        "title": opp.title,
                        "url": opp.url,
                        "deadline": opp.deadline,
                        "fee": opp.fee,
                        "mode": opp.mode,
                        "category": opp.category,
                        "status": opp.status,
                        "suitability_score": opp.suitability_score,
                        "classification": opp.classification,
                        "discovered_at": (
                            opp.discovered_at.isoformat()
                            if opp.discovered_at
                            else None
                        ),
                    })

                return {
                    "tool": "list_opportunities",
                    "total": total,
                    "returned": len(opportunities),
                    "filters": {
                        "status": status_filter,
                        "category": category,
                        "min_score": min_score,
                        "limit": limit,
                    },
                    "opportunities": opportunities,
                }

        except Exception as exc:
            logger.error("Failed to list opportunities: %s", exc)
            return {"error": f"Database query failed: {exc}"}

    def _tool_check_system(self, **kwargs: Any) -> dict:
        """Check system health across all components.

        Returns:
            Dict with component status booleans and diagnostic details.
        """
        health: dict[str, Any] = {
            "tool": "check_system",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
            "overall": "healthy",
        }

        # 1. Database
        try:
            from database.connection import check_database, get_table_counts

            db_ok = check_database()
            counts = get_table_counts() if db_ok else {}
            health["components"]["database"] = {
                "status": "ok" if db_ok else "error",
                "table_counts": counts,
            }
        except Exception as exc:
            health["components"]["database"] = {
                "status": "error",
                "error": str(exc),
            }

        # 2. Ollama LLM
        try:
            if self._llm_client is None:
                from agents.llm_client import OllamaClient

                self._llm_client = OllamaClient()

            server_ok = self._llm_client.health_check()
            model_ok = self._llm_client.check_model() if server_ok else False
            available_models = (
                self._llm_client.get_available_models() if server_ok else []
            )

            health["components"]["ollama"] = {
                "status": "ok" if (server_ok and model_ok) else "degraded" if server_ok else "error",
                "server_reachable": server_ok,
                "model_available": model_ok,
                "configured_model": self._llm_client.model,
                "available_models": available_models,
            }

            if not server_ok:
                health["components"]["ollama"]["recovery"] = (
                    self._llm_client.get_recovery_instructions()
                )
        except Exception as exc:
            health["components"]["ollama"] = {
                "status": "error",
                "error": str(exc),
            }

        # 3. Configuration summary
        health["components"]["config"] = {
            "status": "ok",
            "version": settings.version,
            "ollama_model": settings.ollama.model,
            "ollama_base_url": settings.ollama.base_url,
            "discovery_max_pages": settings.discovery.max_pages,
            "target_urls_count": len(settings.discovery.target_urls),
        }

        # 4. Recent activity
        try:
            with get_session() as session:
                recent_logs = (
                    session.execute(
                        select(ActivityLog)
                        .order_by(ActivityLog.timestamp.desc())
                        .limit(5)
                    )
                    .scalars()
                    .all()
                )
                health["recent_activity"] = [
                    {
                        "action": log.action,
                        "status": log.status,
                        "details": log.details[:200],
                        "timestamp": (
                            log.timestamp.isoformat()
                            if log.timestamp
                            else None
                        ),
                    }
                    for log in recent_logs
                ]
        except Exception as exc:
            health["recent_activity"] = []
            logger.warning("Failed to fetch recent activity: %s", exc)

        # Determine overall health
        component_statuses = [
            c.get("status", "unknown")
            for c in health["components"].values()
        ]
        if "error" in component_statuses:
            health["overall"] = "degraded"
        if all(s == "error" for s in component_statuses):
            health["overall"] = "unhealthy"

        return health

    def _tool_solve_live_quiz(self, **kwargs: Any) -> dict:
        """Run the autonomous live quiz solver."""
        from agents.quiz_agent import LiveQuizAgent

        opportunity_url = kwargs.get("opportunity_url")
        opportunity_id = kwargs.get("opportunity_id")
        headless = kwargs.get("headless", False)
        callback = kwargs.get("callback")

        agent = LiveQuizAgent()
        if opportunity_id:
            result = agent.run_quiz_by_id(
                opportunity_id=opportunity_id, headless=headless, update_callback=callback
            )
        elif opportunity_url:
            result = agent.run_quiz(
                opportunity_url=opportunity_url, headless=headless, update_callback=callback
            )
        else:
            return {"error": "Either 'opportunity_id' or 'opportunity_url' must be provided."}

        return {
            "tool": "solve_live_quiz",
            "status": result.status,
            "questions_submitted": result.answers_submitted,
            "duration_seconds": result.duration_seconds,
            "average_confidence": result.average_confidence,
            "error_message": result.error_message,
        }
