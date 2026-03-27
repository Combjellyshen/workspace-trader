# Refactor Plan: Workspace-Trader Architecture Restructure

> **Date**: 2026-03-22
> **Status**: Draft — awaiting review
> **Goal**: Agent/skill/MCP handle capability-specific collection, routing, delivery, and hard gating only. Discussion/teams, report analysis, writing, review, and revision become Claude Code-style worker flows.

---

## 1. Current Architecture

### 1.1 What exists today

```
┌─────────────────────────────────────────────────────────────┐
│  OpenClaw Heartbeat (scheduler)                             │
│  SOUL.md schedule → triggers Claude agent at fixed times    │
└──────────────────────────┬──────────────────────────────────┘
                           │
           ┌───────────────▼───────────────┐
           │  Claude Agent (monolith)       │
           │  - Reads 11 markdown docs      │
           │  - Runs Python scripts          │
           │  - Writes reports (MD)          │
           │  - Runs quality check           │
           │  - Generates PDF                │
           │  - Manages memory               │
           │  - Delivers via Telegram        │
           └───────────────────────────────┘
                           │
    ┌──────────────────────┼──────────────────────┐
    │                      │                      │
┌───▼────┐  ┌──────────────▼──────────┐  ┌───────▼───────┐
│ Skills │  │ Python Scripts (42+)     │  │ MCP Servers   │
│  (12)  │  │ data/ analysis/ memory/  │  │ yfinance, fmp │
│        │  │ reporting/ utils/        │  │               │
└────────┘  └─────────────────────────┘  └───────────────┘
```

**Key problem**: The Claude agent is a monolith. It simultaneously:
- Collects data (runs scripts, calls MCP)
- Analyzes markets (reads data, applies frameworks)
- Writes reports (full prose, 12K+ char weekly)
- Reviews own work (quality check)
- Revises and regenerates
- Archives to memory
- Delivers to user

There's no separation between *infrastructure* (what machines should do) and *intellectual work* (what the LLM should do with structured worker flows).

### 1.2 Central orchestrator gap

`market_intel_pipeline.py` is misnamed — it **only handles cross-asset snapshots**. The actual "orchestration" is described in `PLAYBOOKS.md` as prose instructions that the Claude agent interprets each time. There is no programmatic pipeline that enforces the 5-playbook structure.

### 1.3 Scheduling mechanism

OpenClaw heartbeat (not cron). Times defined in `SOUL.md`. The heartbeat wakes Claude, Claude reads `HEARTBEAT.md` rules, decides what to do. No durable job queue — if Claude misses a heartbeat or fails mid-task, the work is lost.

---

## 2. Target Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Scheduler Layer  (OpenClaw heartbeat / cron / both)         │
│  Emits task_type + task_date → job queue                     │
└──────────────────────────┬───────────────────────────────────┘
                           │
           ┌───────────────▼───────────────┐
           │  Dispatcher (thin router)      │
           │  Routes task_type to the       │
           │  correct pipeline              │
           └───────┬───────────────────────┘
                   │
    ┌──────────────┼──────────────────────────────┐
    │              │                              │
┌───▼────────┐ ┌──▼──────────────────┐ ┌─────────▼──────────┐
│ Collection │ │ Worker Flows         │ │ Delivery            │
│ Pipeline   │ │ (Claude Code-style)  │ │ Pipeline            │
│            │ │                      │ │                     │
│ - Scripts  │ │ - analyze(data)      │ │ - quality_check()   │
│ - MCP      │ │ - write_report()     │ │ - md_to_pdf()       │
│ - Skills   │ │ - review(draft)      │ │ - telegram_send()   │
│ - Gating   │ │ - revise(feedback)   │ │ - memory_archive()  │
│            │ │ - discuss(multi-pov) │ │                     │
└────────────┘ └──────────────────────┘ └────────────────────┘
```

### 2.1 Layer responsibilities

| Layer | Responsibility | Implementation |
|-------|---------------|----------------|
| **Scheduler** | Emit tasks at correct times, retry on failure | OpenClaw heartbeat + optional cron fallback |
| **Dispatcher** | Map task_type → pipeline, manage state | New: `scripts/orchestrator/dispatcher.py` |
| **Collection Pipeline** | Run data scripts, call MCP, validate data, gate on coverage | Existing scripts + new orchestration wrapper |
| **Worker Flows** | Analysis, writing, review, revision, multi-agent discussion | Claude Code subagent invocations with structured prompts |
| **Delivery Pipeline** | Quality check, PDF gen, Telegram delivery, memory archival | Existing scripts + new orchestration wrapper |

### 2.2 Worker flow design (Claude Code-style)

Each intellectual task becomes a **worker flow** — a sequence of Claude Code subagent calls with explicit inputs/outputs:

```
worker_flow("closing_review"):
  1. analyze(collected_data) → structured_analysis.json
  2. write_report(analysis, template) → draft.md
  3. review(draft, quality_rules) → review_feedback.json
  4. IF review.issues > 0: revise(draft, feedback) → revised.md
  5. approve(final_draft) → ready.md
```

**Key principle**: Each step is a separate Claude invocation with clear input/output contracts. The orchestrator manages state between steps. If a step fails, it can be retried without re-running the entire pipeline.

---

## 3. Inventory: Current Scheduled Tasks & Report Types

### 3.1 Scheduled tasks

| Time (CST) | Task | Playbook | Data Scripts | Report Output |
|-------------|------|----------|-------------|---------------|
| 07:50 WD | Pre-market data collection | PB-1 §1-2 | `market_intel_pipeline.py premarket` + 12 data items | `data/daily_inputs/<date>/` |
| 08:30 WD | Pre-market analysis & report | PB-1 §3-5 | (reads collected data) | `reports/daily/<date>-pre-market.{md,pdf}` |
| 11:30 WD | Intraday anomaly check | PB-3 | `intraday_alert.py check` | Alert message (Telegram) |
| 14:30 WD | Intraday anomaly check | PB-3 | `intraday_alert.py check` | Alert message (Telegram) |
| 15:05 WD | Closing data collection | PB-2 §1-2 | `market_intel_pipeline.py postmarket` + 13 data items | `data/daily_inputs/<date>/` |
| 15:45 WD | Closing review & report | PB-2 §3-6 | (reads collected data) | `reports/daily/<date>-closing.{md,pdf}` |
| 17:00 Fri | Weekly data collection | PB-4 §1-2 | `market_intel_pipeline.py weekly` + deep data | `data/weekly_inputs/<date>/` |
| 18:00 Fri | Weekly market insight | PB-4 §3-6 | (reads collected data) | `reports/weekly/<date>-market-insight.{md,pdf}` |
| 18:30 Sun | Philosophy data collection | PB-5 §1-2 | `philosophy_updater.py` | `memory/knowledge/` |
| 19:30 Sun | Philosophy weekly update | PB-5 §3-6 | (reads collected data) | `reports/philosophy/` + `PHILOSOPHY.md` update |

### 3.2 Report types

| Report | Frequency | Min Size | Quality Gate | Delivery |
|--------|-----------|----------|-------------|----------|
| Pre-market analysis | Daily WD | ~8K chars | Common + daily checks | PDF + Telegram |
| Closing review | Daily WD | ~8K chars | Common + daily + closing + K-line | PDF + Telegram |
| Intraday alert | 2x daily WD | N/A (score-based) | alert_score threshold | Telegram text |
| Weekly insight | Weekly Fri | 12K chars | Full weekly checks (12+ sections) | PDF + Telegram |
| Philosophy update | Weekly Sun | Variable | Framework validation | Internal + optional PDF |

### 3.3 Data collection entry points (all under `scripts/`)

| Script | Subcommand | Used By | Output |
|--------|-----------|---------|--------|
| `data/market_intel_pipeline.py` | `premarket/postmarket/weekly/snapshot` | PB-1,2,4 | Cross-asset JSON |
| `data/deep_data.py` | `snapshot` | PB-1,2 | Full market depth |
| `data/rss_aggregator.py` | `categorized` | PB-1 | News RSS |
| `data/research_reports.py` | `strategy/stock/industry/latest` | PB-1,2,4,5 | Broker reports |
| `data/macro_monitor.py` | `all` | PB-1,4 | Macro indicators |
| `data/macro_deep.py` | `all` | PB-4,5 | 5-dim macro score |
| `data/institution_tracker.py` | `all` | PB-2,4 | Institutional flows |
| `data/cross_asset_snapshot.py` | (library) | Pipeline | Global snapshot |
| `data/market_cache.py` | `save/load/status/sectors` | PB-1,2 | Daily cache |
| `data/stock_profile.py` | `full/batch` | On-demand | Company dossier |
| `data/stock_valuation_history.py` | `batch/compare` | On-demand | PE/PB history |
| `data/tushare_data.py` | (library) | deep_data | Tushare wrapper |
| `data/event_calendar_builder.py` | (placeholder) | PB-4 | Event skeleton |
| `analysis/sentiment.py` | `all` | PB-1,2 | Hot stock sentiment |
| `analysis/market_breadth.py` | `all` | PB-1,2 | Breadth metrics |
| `analysis/cross_day_compare.py` | `all` | PB-2 | Day-over-day |
| `analysis/intraday_alert.py` | `check/snapshot` | PB-3 | Anomaly detection |
| `analysis/watchlist_deep_dive.py` | `all/summary` | PB-1,2,4 | Watchlist analysis |
| `analysis/tech_analysis.py` | `watchlist/index` | PB-1,2,4 | Technical indicators |
| `analysis/kline_detailed_review.py` | `all` | PB-2,4 | K-line narratives |
| `analysis/main_force.py` | `batch` | PB-1,2 | Fund flows |
| `analysis/growth_hunter.py` | `analyze/batch` | On-demand | Growth vectors |
| `analysis/peg_screener.py` | `screen/watchlist` | On-demand | PEG screening |
| `analysis/sector_screener.py` | (analysis) | On-demand | Sector rotation |
| `analysis/market_regime_detector.py` | (analysis) | On-demand | Regime detection |
| `analysis/multi_factor.py` | (analysis) | On-demand | Multi-factor |
| `analysis/tick_chip.py` | (analysis) | On-demand | Tick analysis |
| `memory/memory_manager.py` | `save_*/query_*/status/compress` | All PBs | Memory CRUD |
| `memory/watchlist_context_builder.py` | (library) | PB-4 | Watchlist summary |
| `memory/philosophy_updater.py` | (library) | PB-5 | Philosophy data |
| `reporting/report_quality_check.py` | (check) | All report PBs | Pass/fail |
| `reporting/md_to_pdf.py` | (convert) | All report PBs | PDF output |
| `reporting/md_to_html.py` | (convert) | (library) | HTML output |
| `utils/common.py` | (library) | All scripts | Shared utilities |
| `utils/data_consistency_guard.py` | (check) | Ad-hoc | Cross-source validation |

---

## 4. Bug List & Fragile Spots

### 4.1 Bugs (should fix during refactor)

| # | File | Line | Severity | Description |
|---|------|------|----------|-------------|
| B1 | `analysis/intraday_alert.py` | 407, 505, 574 | **Medium** | `datetime.now()` without timezone — snapshot filenames and check timestamps use local time instead of Asia/Shanghai. Can cause off-by-day issues if system TZ differs. |
| B2 | `reporting/md_to_pdf.py` | 111 | **Low** | `open(md_path, 'r')` without `encoding='utf-8'` — will fail on systems where default locale isn't UTF-8 (e.g., some Docker images). |
| B3 | `reporting/report_quality_check.py` | 58 | **Low** | `FORBIDDEN_PATTERNS` regex `\{[^{}]+\}` false-positives on legitimate JSON/code blocks in markdown. Should exclude fenced code blocks before checking. |
| B4 | `analysis/intraday_alert.py` | 323-326 | **Low** | Market detection (`if code.startswith("6"): market = "sh"`) misses Beijing Stock Exchange (8xxxxx → "bj") and potential future exchanges. |
| B5 | `data/cross_asset_snapshot.py` | 30-31 | **Medium** | `import requests` and `from bs4 import BeautifulSoup` at module level — any script that imports this module will crash if these packages aren't installed, even if the importing code doesn't use these functions. |
| B6 | `memory/memory_manager.py` | 57-58 | **Low** | `today_str()` and `month_str()` use `datetime.now()` without timezone — same TZ issue as B1. |

### 4.2 Fragile spots (should harden during refactor)

| # | Area | Severity | Description |
|---|------|----------|-------------|
| F1 | `market_intel_pipeline.py` | **High** | Misleading name + minimal scope. Called "central orchestrator" in docs but only handles cross-asset snapshots. The actual 12-item data collection per playbook is done ad-hoc by the Claude agent with no programmatic enforcement. If the agent skips a step, there's no guardrail. |
| F2 | Silent exception swallowing | **High** | Multiple scripts use bare `except Exception: pass` (intraday_alert.py:139,222,296,387,456; deep_data.py; cross_asset_snapshot.py). Failed data collection is invisible — reports may be written with stale/missing data without any warning. |
| F3 | No task state persistence | **High** | If Claude agent crashes mid-playbook (e.g., during report writing), there's no checkpoint. The entire task must be re-run from scratch. No job queue or task state file. |
| F4 | AkShare API instability | **Medium** | AkShare frequently changes column names and API signatures. Current column-name matching (`pick_col`) is defensive but still fragile. Multiple recent failures noted in memory logs (institute_survey, fund_flow). |
| F5 | Stooq/FRED timeouts | **Medium** | `cross_asset_snapshot.py` has 6s/15s timeouts for Stooq/FRED. These regularly fail, degrading cross-asset coverage. No circuit breaker or cached fallback. |
| F6 | No data freshness tracking | **Medium** | Scripts don't consistently tag their output with timestamps or source reliability. The quality check only validates report *text* (keyword presence), not whether the underlying data is fresh. |
| F7 | Report quality check is text-only | **Medium** | `report_quality_check.py` does keyword matching on markdown text. It cannot verify that cited numbers match actual data, that coverage claims are true, or that analysis is logically consistent. |
| F8 | Puppeteer PDF fallback fragile | **Low** | Inline JavaScript string in `md_to_pdf.py` — no error details if Puppeteer fails. WeasyPrint is primary but can fail on complex layouts. |
| F9 | `data/daily_inputs/` naming convention | **Low** | Files are numbered `00_*` through `20_*` but there's no manifest or schema validation. A missing numbered file is not detected by any automated check. |
| F10 | Memory compression never tested | **Low** | `memory_manager.py compress` is documented to run monthly, but the system is only ~2 weeks old. The gzip compression path is untested in production. |

---

## 5. Implementation Plan

### Phase 0: Pre-refactor bug fixes (1 session)

Fix the clearly safe bugs before restructuring:

- [ ] **B1**: Add `from zoneinfo import ZoneInfo; TZ = ZoneInfo("Asia/Shanghai")` to `intraday_alert.py`, use `datetime.now(TZ)` at lines 407, 505, 574
- [ ] **B2**: Add `encoding='utf-8'` to `md_to_pdf.py:111`
- [ ] **B6**: Add timezone to `memory_manager.py` date functions
- [ ] **F2 (partial)**: In all `except Exception: pass` blocks, add `sys.stderr.write()` logging so failures are at least visible

### Phase 1: Collection pipeline hardening (2-3 sessions)

**Goal**: Make data collection programmatic, reliable, and auditable.

#### 1a. Rename and expand `market_intel_pipeline.py` → `scripts/orchestrator/collect.py`

```python
# New: scripts/orchestrator/collect.py
# Replaces ad-hoc data collection with deterministic pipeline

def collect(task_type: str, date: str) -> CollectionResult:
    """Run all data scripts for a given task type.

    Returns CollectionResult with:
    - items: dict of {script_name: {status, path, timestamp, coverage}}
    - coverage_ratio: float (0.0-1.0)
    - errors: list of {script, error, severity}
    - manifest_path: path to manifest JSON
    """
```

- Define a **manifest** per task type (which scripts to run, required vs optional)
- Run scripts with timeout + retry logic
- Write a `manifest.json` alongside collected data files
- Gate: if coverage < threshold, flag the task as degraded (don't silently proceed)

#### 1b. Add circuit breaker for flaky sources

- Track per-source failure rate in `memory/state.json`
- If a source fails 3x in a row, skip it for 6 hours and use cached fallback
- Log degradation clearly

#### 1c. Data freshness tags

- Every script output includes `{"_meta": {"source": "...", "timestamp": "...", "freshness": "live|cached|stale"}}`
- Quality check can validate freshness, not just keyword presence

### Phase 2: Worker flow framework (2-3 sessions)

**Goal**: Replace monolithic Claude agent report-writing with structured worker flows.

#### 2a. Create `scripts/orchestrator/worker.py`

```python
# Worker flow definition
@dataclass
class WorkerStep:
    name: str
    prompt_template: str  # Path to prompt template
    input_keys: list[str]
    output_key: str
    max_retries: int = 1

@dataclass
class WorkerFlow:
    name: str
    steps: list[WorkerStep]

# Example: closing review flow
CLOSING_REVIEW = WorkerFlow(
    name="closing_review",
    steps=[
        WorkerStep("load_data", "prompts/load_data.md", ["manifest_path"], "structured_data"),
        WorkerStep("analyze", "prompts/closing_analyze.md", ["structured_data", "memory_context"], "analysis"),
        WorkerStep("write", "prompts/closing_write.md", ["analysis", "template"], "draft"),
        WorkerStep("review", "prompts/closing_review.md", ["draft", "quality_rules"], "review_result"),
        WorkerStep("revise", "prompts/closing_revise.md", ["draft", "review_result"], "final", max_retries=2),
    ]
)
```

#### 2b. Create prompt templates directory

```
prompts/
├── premarket/
│   ├── analyze.md
│   ├── write.md
│   └── review.md
├── closing/
│   ├── analyze.md
│   ├── write.md
│   ├── kline_review.md
│   ├── world_risk.md
│   └── review.md
├── weekly/
│   ├── analyze.md
│   ├── write_section_{n}.md   # One per major section
│   ├── multi_agent_discuss.md
│   └── review.md
├── philosophy/
│   ├── collect_evidence.md
│   ├── update_framework.md
│   └── review.md
└── common/
    ├── quality_review.md
    └── revision.md
```

**Key change**: Currently, playbook instructions are embedded in PLAYBOOKS.md and the agent interprets them each time. In the new system, each step has a **concrete prompt template** with `{{placeholders}}` for dynamic data. The orchestrator fills placeholders and invokes Claude Code subagents.

#### 2c. Discussion/multi-agent flows

For weekly reports (PB-4), the current system mentions "多agent讨论" (multi-agent discussion). Implement this as a worker flow:

```python
WEEKLY_DISCUSSION = WorkerFlow(
    name="weekly_multi_agent",
    steps=[
        WorkerStep("bull_case", "prompts/weekly/bull_thesis.md", ["analysis"], "bull"),
        WorkerStep("bear_case", "prompts/weekly/bear_thesis.md", ["analysis"], "bear"),
        WorkerStep("synthesize", "prompts/weekly/synthesize.md", ["bull", "bear", "analysis"], "synthesis"),
    ]
)
```

Each perspective is a separate Claude invocation with explicit role instructions. The synthesis step receives all perspectives and produces a balanced view.

### Phase 3: Dispatcher & state management (1-2 sessions)

**Goal**: Central dispatcher that routes tasks and persists state.

#### 3a. Create `scripts/orchestrator/dispatcher.py`

```python
def dispatch(task_type: str, date: str = None):
    """Main entry point for all scheduled tasks.

    1. Load task state (check for incomplete runs)
    2. If collection needed: run collect pipeline
    3. If analysis/writing needed: run worker flow
    4. If delivery needed: run delivery pipeline
    5. Persist state at each checkpoint
    """
```

#### 3b. Task state persistence

```
.state/
└── tasks/
    └── <task_type>-<date>.json
    # Contains: {status, current_step, checkpoints, errors, started_at, updated_at}
```

If a task is interrupted, the dispatcher can resume from the last checkpoint instead of re-running everything.

#### 3c. Integrate with OpenClaw heartbeat

- Heartbeat handler checks `.state/tasks/` for pending/failed tasks
- If a task failed, it retries from checkpoint
- If a task is stuck (>30min), it alerts the user

### Phase 4: Delivery pipeline consolidation (1 session)

**Goal**: Unify the post-report steps.

#### 4a. Create `scripts/orchestrator/deliver.py`

```python
def deliver(report_path: str, task_type: str):
    """
    1. Run report_quality_check.py → if FAILED, return to revision step
    2. Run md_to_pdf.py → generate PDF + HTML
    3. Archive to memory (signals, reviews, sentiment as appropriate)
    4. Deliver via Telegram
    5. Update task state to 'completed'
    """
```

#### 4b. Improve quality check

- Exclude fenced code blocks before forbidden pattern check (fix B3)
- Add data freshness validation (check that `_meta.freshness` tags in collected data are not all "stale")
- Add structural validation (check that report section count matches manifest expectations)

### Phase 5: Migration & cleanup (1-2 sessions)

#### 5a. Consolidate documentation

Current state: 11 markdown files with overlapping concerns.

Target:
| File | Keeps | Removes |
|------|-------|---------|
| `SOUL.md` | Personality, tone, self-intro | Schedule table (moves to orchestrator config) |
| `AGENTS.md` | Hard rules 1-8 | Unchanged |
| `WORKFLOW.md` | Analysis framework weights, report standards | Playbook-specific sections (move to prompt templates) |
| `PLAYBOOKS.md` | **Deprecated** — replaced by orchestrator config + prompt templates |
| `DATA_SOURCES.md` | API priority hierarchy, frequency limits | Script command index (auto-generated from manifests) |
| `HEARTBEAT.md` | Simplified — just "check dispatcher for pending tasks" |
| `TOOLS.md` | Quick reference | Auto-generated from script docstrings |
| `MEMORY_SYSTEM.md` | Unchanged (memory is orthogonal to this refactor) |

#### 5b. New file structure

```
scripts/
├── orchestrator/          # NEW: central coordination
│   ├── __init__.py
│   ├── dispatcher.py      # Task routing + state management
│   ├── collect.py         # Data collection pipeline
│   ├── worker.py          # Worker flow framework
│   ├── deliver.py         # Post-report delivery
│   └── manifests/         # Task type definitions (YAML/JSON)
│       ├── premarket.yaml
│       ├── closing.yaml
│       ├── weekly.yaml
│       ├── intraday.yaml
│       └── philosophy.yaml
├── prompts/               # NEW: prompt templates for worker flows
│   ├── premarket/
│   ├── closing/
│   ├── weekly/
│   ├── philosophy/
│   └── common/
├── data/                  # UNCHANGED: data collection scripts
├── analysis/              # UNCHANGED: analysis scripts
├── memory/                # UNCHANGED: memory management
├── reporting/             # UNCHANGED: report generation
└── utils/                 # UNCHANGED: shared utilities
```

#### 5c. Deprecation path

1. Keep existing scripts working throughout migration
2. New orchestrator calls existing scripts — no rewrite of data/analysis layer
3. Playbook docs stay readable but are marked `DEPRECATED — see orchestrator manifests`
4. Old ad-hoc invocation pattern (agent reads PLAYBOOKS.md, runs scripts manually) still works as fallback

---

## 6. Migration Phases Summary

| Phase | Scope | Risk | Blocking? |
|-------|-------|------|-----------|
| **Phase 0** | Bug fixes (B1, B2, B6, F2 partial) | Very low | No — can do immediately |
| **Phase 1** | Collection pipeline + manifests | Low | No — additive, old path still works |
| **Phase 2** | Worker flow framework + prompt templates | Medium | No — can run in parallel with old system |
| **Phase 3** | Dispatcher + state persistence | Medium | Yes — this is the cutover point |
| **Phase 4** | Delivery pipeline | Low | No — builds on Phase 3 |
| **Phase 5** | Doc consolidation + cleanup | Low | No — housekeeping |

**Recommended order**: 0 → 1 → 2 (can parallel) → 3 → 4 → 5

---

## 7. Non-goals (out of scope)

- Rewriting data collection scripts (they work; just need better orchestration)
- Changing the memory system architecture
- Migrating away from OpenClaw/heartbeat scheduling
- Building a web UI
- Changing the investment philosophy or analysis frameworks
- Rewriting the PDF generation pipeline (it works)

---

## 8. Open questions for review

1. **Prompt template format**: Plain markdown with `{{placeholders}}` vs. structured YAML with embedded markdown? Markdown is simpler; YAML allows metadata (expected output format, max tokens, etc.).

2. **Worker flow execution**: Should worker steps be Claude Code subagents (Agent tool) or sequential prompts in the same conversation? Subagents provide isolation but cost more tokens. Same-conversation is cheaper but risks context pollution.

3. **Task state location**: `.state/` directory (gitignored) vs. extending `memory/state.json`? Separate is cleaner; extending state.json keeps everything in one place.

4. **Manifest format**: YAML (human-readable, supports comments) vs. JSON (already used everywhere)?

5. **Heartbeat integration**: Should the dispatcher be called directly by heartbeat, or should heartbeat write to a task queue that the dispatcher polls? Direct is simpler; queue is more robust.
