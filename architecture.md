# Architecture of Unstop Autopilot (AgentX Core)

This document specifies the architectural model, perception-action loop, and control mechanisms for the **Autonomous Unstop AI Agent (AgentX)**.

---

## 1. System Vision & Overview

AgentX is a local-first, desktop-autonomous agent designed to operate inside a visible Chromium browser on Linux (Wayland / XWayland via Hyprland) with:
- **Physical Screen & Cursor Control**: Actuates the user's real hardware mouse pointer and keyboard (via PyAutoGUI) mapped directly to browser viewport coordinates.
- **Continuous Perception-Action Loop**: Never halts or blocks on terminal prompts; reacts dynamically to page transitions, dialogs, form stages, and question timers.
- **DOM & Multi-Frame Parser**: Traverses nested iframes, shadow DOM, and dynamic Angular/React material widgets used by Unstop.
- **Local AI Decision Engine**: Solves MCQs, coding challenges, and comprehension problems in real time using local Ollama (`qwen2.5:7b`) with zero cloud telemetry.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   AGENTX RUNTIME                                       │
│                                                                                        │
│   ┌───────────────────────────┐           ┌────────────────────────────────────────┐   │
│   │   Desktop Actuator        │           │     Perception Engine                  │   │
│   │   • PyAutoGUI OS Pointer  │◄──────────┤     • Playwright Frame Crawler         │   │
│   │   • Hyprland Window Geoms │           │     • Shadow DOM & Modal Inspector     │   │
│   │   • Hardware Keystrokes   │           │     • Live Timer & OCR Reader          │   │
│   └─────────────┬─────────────┘           └───────────────────┬────────────────────┘   │
│                 │                                             │                        │
│                 ▼                                             ▼                        │
│   ┌────────────────────────────────────────────────────────────────────────────────┐   │
│   │                      Autonomous State Machine (2 Hz Loop)                      │   │
│   │                                                                                │   │
│   │   [AUTHENTICATED?] ───► [REGISTRATION] ───► [ROUNDS] ───► [ASSESSMENT / MCQS]  │   │
│   │          │                     │                 │                 │           │   │
│   │          ▼                     ▼                 ▼                 ▼           │   │
│   │   Interactive Login      Fill Profile     Select Active     Evaluate Qwen      │   │
│   │     Auto-Detector        & Checkboxes      Round CTA        Click & Next       │   │
│   └──────────────────────────────────────┬─────────────────────────────────────────┘   │
│                                          │                                             │
│                                          ▼                                             │
│                           ┌─────────────────────────────┐                              │
│                           │      SQLite Audit Store     │                              │
│                           │      • Session Scores       │                              │
│                           │      • Solved Questions     │                              │
│                           │      • Activity History     │                              │
│                           └─────────────────────────────┘                              │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Subsystems

### 2.1 Physical Desktop Actuator
Rather than injecting synthetic CSS animations or relying solely on CDP synthetic events, AgentX drives the actual desktop cursor:
1. **Window Coordinate Resolution**:
   - Queries `hyprctl clients -j` to determine the exact desktop geometry `[win_x, win_y]` of the Chromium window.
   - Measures browser chrome offset (`top_bar` height, typically 75–85px for tab strip and address bar).
2. **Bounding Box Translation**:
   - `screen_x = win_x + box.x + box.width / 2`
   - `screen_y = win_y + top_bar + box.y + box.height / 2`
3. **Smooth Physical Actuation**:
   - Moves the OS hardware pointer via `pyautogui.moveTo(screen_x, screen_y, duration=0.20)`.
   - Triggers physical clicks (`pyautogui.click()`) coupled with Playwright DOM event dispatching to guarantee event propagation across complex Angular event listeners.

---

### 2.2 Autonomous State Machine
The agent transitions across 6 discrete states every 500ms without blocking on terminal prompts:

1. **`CheckAuth`**: Checks whether the session is authenticated by testing for live DOM profile indicators (`.user-profile`, `un-user-avatar`, `img[alt*='profile']`).
2. **`InteractiveLogin`**: If logged out, navigates to `https://unstop.com/auth/login`, remains visible, and automatically resumes the instant a valid profile is detected.
3. **`AutoRegister`**: Targets `.register_btn`, `div.on_unstop`, fills candidate profile fields (`name`, `college`, `degree`, `graduation_year`), clicks declaration checkboxes, and submits.
4. **`TraverseRounds`**: Expands round stages, detects active round entry buttons (`a[href*='/assessment/']`, `button:has-text('Take Assessment')`, `button:has-text('Play')`), and clicks to enter.
5. **`AssessmentPrep`**: Handles instruction screens, webcam/proctoring disclaimers, checks terms checkboxes, and triggers "Start Assessment".
6. **`LiveSolving`**: Continuously parses questions, evaluates solutions via Qwen 2.5 7B, moves the real cursor to select the choice, clicks "Save & Next", and confirms submission upon completion.

---

### 2.3 Multi-Frame Assessment Scraper
Unstop assessments frequently render in third-party or isolated iframes (`iframe[src*='assessment']`, `iframe[src*='wecp']`, `iframe[src*='hackerearth']`).
The perception engine searches recursively:
- Recursively inspects `page.frames` to extract:
  - Active question text and syntax-highlighted code blocks
  - Option radio buttons and text labels
  - Remaining time indicators
- Normalizes option prefixes (`A.`, `1.`, `(a)`) to compare clean semantic choices against LLM output.

---

### 2.4 Local LLM Reasoning Pipeline
1. Prompt constructed with strict JSON schema:
   ```json
   {
     "chosen_index": 0,
     "confidence": 0.95,
     "rationale": "Brief explanation"
   }
   ```
2. Dispatched to local Ollama instance (`http://localhost:11434/api/generate`) running `qwen2.5:7b`.
3. If Ollama is slow (>8s) or unreachable, activates the heuristic solver (detecting keywords like "All of the above", lexical overlaps, and syntactic cues) to guarantee progress without timeout penalties.

---

## 3. Data & Persistence Model

- **Session Profile**: Stored in `data/browser_profile/` using persistent Chromium context.
- **Candidate Prefill**: Configured in SQLite `user_preferences` table (name, university, year, degree, phone, location).
- **Assessment History**: Recorded in `quiz_sessions` and `quiz_questions` with question text, options, chosen index, confidence, and correctness telemetry.
