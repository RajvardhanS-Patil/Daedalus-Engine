# Daedalus Engine (Unstop Autopilot)

**Local-first autonomous AI competition management system for Unstop**

A terminal-based application that helps students discover, prepare for, and manage competitions, hackathons, quizzes, and other opportunities on Unstop — powered by local AI (Ollama + Qwen 2.5 7B).

## Features

- 🔍 **Automated Discovery** — Scrapes Unstop public listings for competitions, hackathons, quizzes
- 🤖 **AI Analysis** — Scores and classifies opportunities using local LLM
- 📋 **Registration Copilot** — Guided registration with approval policies
- 📝 **Quiz Laboratory** — Practice question generation and evaluation
- 📊 **Presentation Studio** — Generate PPTX/PDF for competitions
- 🏆 **Achievement Tracker** — Track certificates, points, and badges
- ⏰ **Scheduler** — Automated daily discovery and reporting

## Requirements

- **OS**: Linux (tested on Arch Linux)
- **Python**: 3.10+
- **GPU**: NVIDIA GPU with 6GB+ VRAM (for Ollama)
- **Ollama**: Installed and running with `qwen2.5:7b` model
- **Package Manager**: `uv` (recommended) or `pip`

## Installation

### 1. Clone and set up

```bash
cd ~/Documents/Unstop\ Automation
```

### 2. Create virtual environment and install dependencies

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 3. Install Playwright browsers

```bash
playwright install chromium
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env if needed (defaults should work)
```

### 5. Verify Ollama

```bash
# Make sure Ollama is running
ollama serve  # or: systemctl --user start ollama

# Verify model is available
ollama list
# Should show qwen2.5:7b
```

### 6. Run the application

```bash
python main.py
```

## Configuration

All settings are configurable via `.env` file or environment variables. See `.env.example` for all options.

Key settings:
- `OLLAMA_MODEL` — Change the LLM model (default: `qwen2.5:7b`)
- `BROWSER_HEADLESS` — Set to `false` to see the browser during discovery
- `DISCOVERY_DELAY_SECONDS` — Rate limiting between page loads (default: 3)
- `SCHEDULER_ENABLED` — Enable automated scheduling (default: false)

## Project Structure

```
unstop-autopilot/
├── main.py                    # Entry point
├── config/                    # Configuration & policies
├── agents/                    # LLM client, orchestration agents
├── browser/                   # Playwright automation
├── database/                  # SQLAlchemy models & connection
├── intelligence/              # AI scoring & classification (Phase 2)
├── quiz/                      # Quiz practice engine (Phase 4)
├── documents/                 # PPTX/PDF generators (Phase 5)
├── submissions/               # Submission validation (Phase 6)
├── achievements/              # Certificate tracking (Phase 6)
├── scheduler/                 # APScheduler jobs (Phase 7)
├── terminal/                  # Rich terminal UI
├── tests/                     # Test suite
└── data/                      # Runtime data (gitignored)
```

## Security & Privacy

- All data stored locally in SQLite
- Browser sessions stored locally, excluded from git
- No passwords stored in code or config
- No paid APIs required
- All consequential actions require explicit user approval
- LLM cannot execute arbitrary code

## License

MIT
