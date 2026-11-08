"""
Unstop Autopilot — Terminal Menu Configurations

Definitions of menu options and navigation paths across all modules.
"""

MAIN_MENU_ITEMS: list[tuple[str, str]] = [
    ("1", "Discover Opportunities (Live Scrape)"),
    ("2", "View Opportunities (Search & Filter)"),
    ("3", "AI Opportunity Analysis (Qwen 2.5)"),
    ("4", "Registration Copilot (Assisted)"),
    ("5", "Quiz Laboratory (Practice & Prep)"),
    ("6", "Presentation Studio (PPTX/PDF)"),
    ("7", "Submission Manager (Validation)"),
    ("8", "Certificate Manager (Archive)"),
    ("9", "Achievement Analytics (Progress)"),
    ("10", "Automation Settings (Scheduler)"),
    ("11", "System Diagnostics (Hardware/LLM)"),
    ("12", "Exit Application"),
]

FILTER_MENU_ITEMS: list[tuple[str, str]] = [
    ("1", "View All Opportunities"),
    ("2", "Filter: Hackathons Only"),
    ("3", "Filter: Competitions Only"),
    ("4", "Filter: Quizzes Only"),
    ("5", "Filter: Free Entry Only"),
    ("6", "Filter: Online Mode Only"),
    ("7", "Search by Keyword in Title"),
    ("8", "Return to Main Menu"),
]
