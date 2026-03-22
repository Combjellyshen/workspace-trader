# Trade Workspace Refactor Plan v1

> **Date**: 2026-03-22
> **Author**: Engineering audit (Claude Code)
> **Status**: Approved for implementation
> **Companion doc**: `docs/trade-bug-audit-v1.md` (bug register)

---

## 1. Current-State Inventory

### 1.1 System identity

**砚·交易台** — an A-share-focused analysis system running on the OpenClaw agent framework, serving a single user (Combjelly Shen, Shanghai timezone) via Telegram. The system collects market data, generates daily/weekly analytical reports, and maintains long-term memory for prediction validation.

### 1.2 Orchestration model (today)

```
┌─────────────────────────────────────────────────┐
│  OpenClaw Heartbeat (scheduler)                  │
│  SOUL.md schedule → wakes Claude at fixed times  │
└────────────────────┬────────────────────────────┘
                     │
         ┌───────────▼────────────┐
         │  Claude Agent           │
         │  (monolithic executor)  │
         │                        │
         │  1. Reads 11 .md docs  │
         │  2. Runs 42+ scripts   │
         │  3. Calls MCP servers  │
         │  4. Writes prose       │
         │  5. Self-reviews       │
         │  6. Generates PDF      │
         │  7. Archives memory    │
         │  8. Delivers Telegram  │
         └───────┬────────────────┘
     ┌───────────┼──────────────────────┐
  ┌──▼──┐   ┌───▼──────────────┐   ┌───▼───────┐
  │Skills│   │Python scripts/42 │   │MCP servers│
  │ (12) │   │data/analysis/    │   │yfinance(6)│
  │      │   │memory/reporting/ │   │FMP (~200) │
  └──────┘   └──────────────────┘   └───────────┘
```

**Problem**: Every scheduled task loads the entire agent context (~45K tokens of preamble from 11 markdown docs), re-interprets prose playbooks, and executes a variable sequence of ~15 script calls with no programmatic enforcement, no checkpointing, and no structured review loop. The same Claude invocation collects data, thinks, writes, reviews its own writing, and delivers.

### 1.3 Scheduled tasks

| Time (CST) | Trigger | Task | Playbook | Key scripts | Output |
|------------|---------|------|----------|-------------|--------|
| 07:50 WD | Heartbeat | Premarket data collection | PB-1 §1-2 | `market_intel_pipeline.py premarket` + 12 items | `data/daily_inputs/{date}/` |
| 08:30 WD | Heartbeat | Premarket analysis + report | PB-1 §3-5 | (reads collected data) | `reports/daily/{date}-pre-market.{md,pdf}` |
| 11:30 WD | Heartbeat | Intraday anomaly check | PB-3 | `intraday_alert.py check/snapshot` | Telegram alert |
| 14:30 WD | Heartbeat | Intraday anomaly check | PB-3 | `intraday_alert.py check/snapshot` | Telegram alert |
| 15:05 WD | Heartbeat | Closing data collection | PB-2 §1-2 | `market_intel_pipeline.py postmarket` + 13 items | `data/daily_inputs/{date}/` |
| 15:45 WD | Heartbeat | Closing review + report | PB-2 §3-6 | (reads collected data) | `reports/daily/{date}-closing.{md,pdf}` |
| 17:00 Fri | Heartbeat | Weekly data collection | PB-4 §1-2 | `market_intel_pipeline.py weekly` + deep scripts | `data/weekly_inputs/{date}/` |
| 18:00 Fri | Heartbeat | Weekly market insight | PB-4 §3-6 | (reads collected data) | `reports/weekly/{date}-market-insight.{md,pdf}` |
| 18:30 Sun | Heartbeat | Philosophy data collection | PB-5 §1-2 | `philosophy_updater.py` | `memory/knowledge/` |
| 19:30 Sun | Heartbeat | Philosophy weekly update | PB-5 §3-6 | (reads collected data) | `reports/philosophy/` + `PHILOSOPHY.md` |

### 1.4 Scripts inventory (42 total)

| Directory | Count | Purpose |
|-----------|-------|---------|
| `scripts/data/` | 16 | Collection: market cache, cross-asset, RSS, research, macro, institutional, deep data, stock profiles, valuations, event calendar |
| `scripts/analysis/` | 15 | Analysis: sentiment, breadth, technicals, K-line review, watchlist deep dive, growth vectors, PEG screen, sector rotation, tick/chip, intraday alert, multi-factor, regime detection, main force |
| `scripts/memory/` | 3 | State: memory manager, philosophy updater, watchlist context |
| `scripts/reporting/` | 3 | Output: md→pdf, md→html, quality check |
| `scripts/utils/` | 3 | Shared: common HTTP, data consistency guard, `__init__` |
| `scripts/legacy/` | 2 | Deprecated scripts (kept for reference) |

### 1.5 Skills inventory (12 linked)

| Skill | Used by | Replaceable by script? |
|-------|---------|----------------------|
| akshare-stock | Primary A-share data | No — core capability |
| agent-browser | Web scraping fallback | No — unique capability |
| hedgefundmonitor | Systemic risk (PB-4 §10) | No — unique data |
| market-research-reports | Report generation assist | Partially — WORKFLOW.md covers this |
| plotly | Charting | No — visualization capability |
| polars | Large data processing | No — performance capability |
| statistical-analysis | Statistical methods | No — analytical capability |
| exploratory-data-analysis | EDA workflows | Partially overlaps with analysis scripts |
| summarizer | Text summarization | Yes — Claude does this natively |
| markitdown | Document conversion | Partially — md_to_pdf.py covers reports |
| usfiscaldata | US fiscal data | No — unique data |
| xiucheng-self-improving-agent | Agent self-improvement | Unused in practice |

### 1.6 Documentation files (11 markdown docs)

| File | Lines | Role | Overlap/Issue |
|------|-------|------|---------------|
| AGENTS.md | 66 | Hard rules (highest priority) | Clean — no overlap |
| SOUL.md | 45 | Personality + schedule table | Schedule table duplicated in orchestration layer |
| WORKFLOW.md | 480+ | Analysis framework + report specs | Contains playbook-level detail (§6-10 are effectively PB-4 specs) |
| PLAYBOOKS.md | 298 | 5 task execution templates | Heavy prose; data collection lists duplicate DATA_SOURCES.md §6 |
| DATA_SOURCES.md | 198 | API priority + script index | Script index duplicated in PLAYBOOKS.md |
| PHILOSOPHY.md | 150+ | Investment framework | Clean — domain content |
| MEMORY_SYSTEM.md | 104 | Memory architecture | Clean — orthogonal |
| HEARTBEAT.md | 10 | Wake rules | Could be 3 lines in dispatcher config |
| TOOLS.md | 22 | Quick reference | Subset of DATA_SOURCES.md §6 |
| USER.md | 14 | User profile | Clean |
| IDENTITY.md | 12 | Self-definition | Overlaps with SOUL.md |

**Documentation debt**: ~45K tokens of prose loaded every session. WORKFLOW.md §6 is effectively a second copy of PB-4. TOOLS.md is a subset of DATA_SOURCES.md. IDENTITY.md duplicates SOUL.md.

### 1.7 Delivery channel

- **Telegram group**: `-1003521046656` (configured in `config/claude_dispatch.json`)
- **Format**: PDF for complex analysis; short text for intraday alerts
- **PDF pipeline**: Markdown → WeasyPrint (primary) → Puppeteer fallback → HTML intermediate saved

### 1.8 Memory system

```
memory/
├── reports/YYYY-MM/         — Broker research archives (monthly)
├── signals/YYYY-MM-DD.json  — Daily key signals (9 categories)
├── reviews/YYYY-MM-DD.md    — Daily closing review logs
├── sentiment/YYYY-MM-DD.json — Daily emotion snapshots
├── snapshots/               — Market moment captures
├── stocks/<code>.json       — Individual watchlist archives
├── knowledge/               — Long-term learning (philosophy)
└── state.json               — Current tracking state
```

State.json tracks: streak stocks, watch alerts, market patterns (breadth ratio, risk temperature). Memory lifecycle: raw 0-30d → original 30d-6mo → gzip 6mo+.

### 1.9 MCP servers

| Server | Transport | Tools | Status |
|--------|-----------|-------|--------|
| yfinance | STDIO | 6 (get_quote, get_historical, get_company_info, etc.) | OK |
| FMP | HTTP 127.0.0.1:18900 | ~200 (indexes, forex, quotes, DCF, insider, analyst, SEC) | OK |

### 1.10 API credentials

All configured and verified: Tushare, Alpha Vantage, FRED, FMP (via MCP), AkShare (no key), yfinance (no key), Hedge Fund Monitor (no key), Data Commons, EDGAR.

---

## 2. Target Architecture

### 2.1 Design principles

1. **Agent/skill/MCP = capability layer only**: Collection, routing, delivery, and hard-gating (data quality, risk thresholds)
2. **Claude Code = worker layer**: Discussion, analysis, report writing, review, and revision as structured multi-step flows
3. **Separation of data and intellect**: Scripts produce structured data; Claude produces structured prose; neither does the other's job
4. **Checkpoint everything**: Every step writes intermediate state; any step can be retried independently
5. **Fail loud**: No silent `except: pass`; every data gap is tracked and surfaced in the final report

### 2.2 Target layout

```
┌─────────────────────────────────────────────────────┐
│  Scheduler Layer                                     │
│  OpenClaw heartbeat → emits task_type + task_date    │
└──────────────────────┬──────────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │  Dispatcher                 │
         │  scripts/orchestrator/      │
         │  dispatcher.py              │
         │                            │
         │  - Routes task_type        │
         │  - Manages .state/tasks/   │
         │  - Resumes from checkpoint │
         └───────┬────────────────────┘
                 │
   ┌─────────────┼───────────────────────────┐
   │             │                           │
┌──▼──────────┐ │ ┌──────────────────────┐ ┌─▼────────────────┐
│ Collection  │ │ │ Worker Flows         │ │ Delivery          │
│ Pipeline    │ │ │ (Claude Code stages) │ │ Pipeline          │
│             │ │ │                      │ │                   │
│ collect.py  │ │ │ analyze(data)        │ │ quality_check()   │
│ - scripts   │ │ │ discuss(multi-pov)   │ │ md_to_pdf()       │
│ - MCP calls │ │ │ write(sections)      │ │ telegram_send()   │
│ - skills    │ │ │ review(draft)        │ │ memory_archive()  │
│ - gating    │ │ │ revise(feedback)     │ │ state_update()    │
│             │ │ │                      │ │                   │
│ → manifest  │ │ │ → checkpointed .md   │ │ → completed task  │
└─────────────┘ │ └──────────────────────┘ └──────────────────┘
                │
         ┌──────▼──────────────────────────────────┐
         │ Capability Layer (unchanged)             │
         │ Skills (12) │ MCP (yfinance, FMP) │ Py  │
         └─────────────────────────────────────────┘
```

### 2.3 Layer responsibilities

| Layer | Responsibility | What it does NOT do |
|-------|---------------|-------------------|
| **Scheduler** | Emit `task_type` + `task_date` at correct times; retry failed tasks | Analysis, writing, delivery |
| **Dispatcher** | Route to pipeline stages; persist task state; resume from checkpoint | Data collection, prose generation |
| **Collection Pipeline** | Run data scripts per manifest; validate coverage; write `manifest.json` | Interpret data, write reports |
| **Worker Flows** | Analysis, multi-POV discussion, report writing, self-review, revision | Data collection, PDF generation, delivery |
| **Delivery Pipeline** | Quality check, PDF gen, Telegram send, memory archive, state update | Data collection, analysis |
| **Capability Layer** | Skills, MCP, Python scripts — execute specific data/tool operations | Orchestration, prose, decisions |

### 2.4 Worker flow design

Each intellectual task becomes a **worker flow** — a sequence of Claude Code subagent calls with explicit input/output contracts:

```python
# Example: closing review flow
CLOSING_REVIEW = WorkerFlow(
    name="closing_review",
    steps=[
        Step("load_context", inputs=["manifest", "memory_query"], output="context"),
        Step("analyze",      inputs=["context", "premarket_report"], output="analysis"),
        Step("discuss",      inputs=["analysis"], output="discussion"),  # multi-POV
        Step("write",        inputs=["analysis", "discussion", "template"], output="draft"),
        Step("review",       inputs=["draft", "quality_rules"], output="review_feedback"),
        Step("revise",       inputs=["draft", "review_feedback"], output="final"),
    ]
)
```

**Key change vs. today**: Each step is a *separate* Claude invocation with clear I/O. The orchestrator manages state between steps. If `review` finds issues, the flow loops back to `revise` without re-running collection or analysis.

### 2.5 Multi-agent discussion (weekly report)

Currently described in PB-4 §4 as prose instructions. In the new system:

```python
WEEKLY_DISCUSSION = WorkerFlow(
    name="weekly_multi_agent",
    steps=[
        Step("module_macro",     inputs=["data_macro"],     output="macro_thesis"),
        Step("module_structure", inputs=["data_structure"],  output="structure_thesis"),
        Step("module_sectors",   inputs=["data_sectors"],    output="sectors_thesis"),
        Step("module_watchlist", inputs=["data_watchlist"],  output="watchlist_thesis"),
        Step("module_risk",      inputs=["data_all"],        output="risk_thesis"),
        Step("cross_challenge",  inputs=["all_theses"],      output="challenged"),
        Step("editor_ruling",    inputs=["challenged"],      output="final_ruling"),
    ]
)
```

Each module is a separate Claude invocation with role-specific system prompts. Cross-challenge and editor ruling are explicit steps, not embedded in a single monolithic prompt.

### 2.6 Task state persistence

```
.state/
└── tasks/
    └── {task_type}-{date}.json
    # {
    #   "task_type": "closing_review",
    #   "date": "2026-03-22",
    #   "status": "in_progress",
    #   "current_step": "write",
    #   "checkpoints": {
    #     "collect": {"completed_at": "...", "manifest_path": "..."},
    #     "analyze": {"completed_at": "...", "output_path": "..."}
    #   },
    #   "errors": [],
    #   "started_at": "...",
    #   "updated_at": "..."
    # }
```

Status lifecycle: `pending → collecting → analyzing → writing → reviewing → delivering → completed`

Any step can fail → status = `failed`, user is alerted, task can be retried from that step.

### 2.7 Collection manifests

Each task type gets a YAML manifest defining required and optional data scripts:

```yaml
# scripts/orchestrator/manifests/closing.yaml
task_type: closing_review
scripts:
  - name: deep_data_snapshot
    command: "python3 scripts/data/deep_data.py snapshot"
    required: true
    timeout_s: 30
  - name: deep_data_north
    command: "python3 scripts/data/deep_data.py north"
    required: true
    timeout_s: 30
  # ... (13 items from PB-2 §3)
min_coverage: 0.7
output_dir: "data/daily_inputs/{date}/"
```

### 2.8 New file structure

```
scripts/
├── orchestrator/           # NEW
│   ├── __init__.py
│   ├── dispatcher.py       # Task routing + state management
│   ├── collect.py          # Data collection pipeline
│   ├── worker.py           # Worker flow framework
│   ├── deliver.py          # Delivery pipeline
│   └── manifests/          # Task type definitions
│       ├── premarket.yaml
│       ├── closing.yaml
│       ├── intraday.yaml
│       ├── weekly.yaml
│       └── philosophy.yaml
├── prompts/                # NEW — prompt templates for worker flows
│   ├── premarket/
│   │   ├── analyze.md
│   │   ├── write.md
│   │   └── review.md
│   ├── closing/
│   │   ├── analyze.md
│   │   ├── discuss.md
│   │   ├── write.md
│   │   └── review.md
│   ├── weekly/
│   │   ├── module_macro.md
│   │   ├── module_structure.md
│   │   ├── module_sectors.md
│   │   ├── module_watchlist.md
│   │   ├── module_risk.md
│   │   ├── cross_challenge.md
│   │   ├── editor_ruling.md
│   │   ├── write.md
│   │   └── review.md
│   ├── philosophy/
│   │   ├── evaluate.md
│   │   └── update.md
│   └── common/
│       ├── quality_review.md
│       └── revision.md
├── data/                   # UNCHANGED
├── analysis/               # UNCHANGED
├── memory/                 # UNCHANGED
├── reporting/              # UNCHANGED
└── utils/                  # UNCHANGED
```

---

## 3. Migration Phases

### Phase 0: Bug fixes + silent-failure remediation (1 session)

**Goal**: Fix clearly-safe bugs before restructuring. Full register: `docs/trade-bug-audit-v1.md`.

Priority fixes:
- [ ] **B-01 to B-04**: Add `ZoneInfo("Asia/Shanghai")` to all `datetime.now()` calls (14 files)
- [ ] **B-05**: Add `encoding='utf-8'` to all `open()` calls (memory_manager.py ×6, md_to_pdf.py ×1, cross_day_compare.py ×1)
- [ ] **B-08**: Fix `market_cache.py:432` key reference `'market_spot'` → correct key
- [ ] **B-09 to B-11**: Replace bare `except:` / `except Exception: pass` with logged exceptions
- [ ] **B-07**: Exclude fenced code blocks from forbidden pattern check in `report_quality_check.py`

**Risk**: Very low — isolated bugfixes, no structural change.

### Phase 1: Collection pipeline hardening (2-3 sessions)

**Goal**: Make data collection programmatic, auditable, and gated on coverage.

#### 1a. Create `scripts/orchestrator/collect.py`

```python
def collect(task_type: str, date: str) -> CollectionResult:
    """Run all data scripts for a given task type per its manifest.
    Returns CollectionResult with items, coverage_ratio, errors, manifest_path.
    """
```

- Load manifest YAML for `task_type`
- Run each script with subprocess timeout, capture stdout/stderr
- Write `manifest.json` alongside output files with per-script status
- Gate: if `coverage_ratio < manifest.min_coverage`, mark task as degraded

#### 1b. Create manifests for all 5 task types

Translate PB-1 through PB-5 data collection lists into structured YAML with required/optional flags and timeout values.

#### 1c. Add data freshness metadata

Every script output gets a `_meta` header:
```json
{"_meta": {"source": "deep_data.py snapshot", "timestamp": "2026-03-22T15:10:00+08:00", "freshness": "live"}}
```

#### 1d. Add circuit breaker for flaky sources

- Track per-source failure count in `.state/source_health.json`
- After 3 consecutive failures: skip source for 6 hours, use cached data, log degradation

**Risk**: Low — additive. Old ad-hoc collection still works as fallback.

### Phase 2: Worker flow framework (2-3 sessions, can parallel with Phase 1)

**Goal**: Replace monolithic agent report-writing with structured, checkpointed worker flows.

#### 2a. Create `scripts/orchestrator/worker.py`

Define `WorkerStep` and `WorkerFlow` dataclasses. Each step specifies prompt template path, input keys, output key, max retries.

#### 2b. Create prompt templates

Convert prose from PLAYBOOKS.md and WORKFLOW.md into concrete templates with `{{placeholders}}`. Each includes: role instruction, input data format, expected output structure, quality constraints.

#### 2c. Implement discussion flow for weekly reports

5-module → cross-challenge → editor ruling from PB-4 §4 as explicit worker steps with separate Claude Code subagent invocations.

#### 2d. Implement review-revise loop

After writing, a review step checks against quality rules (WORKFLOW.md §6.6 checklist). If issues found, revise step runs with feedback. Loop up to 2 times.

**Risk**: Medium — prompt templates need careful testing against real market data.

### Phase 3: Dispatcher + state management (1-2 sessions, after Phase 1)

**Goal**: Central dispatcher that routes tasks, persists state, and enables resume-from-checkpoint.

#### 3a. Create `scripts/orchestrator/dispatcher.py`

```python
def dispatch(task_type: str, date: str = None):
    """Main entry point. Check state, resume or start, checkpoint each step."""
```

#### 3b. Integrate with OpenClaw heartbeat

- Heartbeat handler calls `dispatch(inferred_task_type, today)`
- Simplify HEARTBEAT.md to: "Call dispatcher."

#### 3c. Cutover strategy

Run new dispatcher in parallel with old system for 2 weeks. Compare outputs. Switch over when quality is validated.

**Risk**: Medium — this is the cutover point.

### Phase 4: Delivery pipeline consolidation (1 session, after Phase 3)

**Goal**: Unify post-report steps.

#### 4a. Create `scripts/orchestrator/deliver.py`

```python
def deliver(report_path: str, task_type: str, task_state: dict):
    """quality_check → md_to_pdf → telegram_send → memory_archive → state_update"""
```

#### 4b. Improve quality check

- Exclude fenced code blocks before forbidden pattern check (fix B-07)
- Add data freshness validation (verify `_meta.freshness`)
- Add structural validation (section count matches expectations)
- Add length validation (character count per task type minimum)

**Risk**: Low.

### Phase 5: Documentation consolidation + cleanup (1-2 sessions, after Phase 4)

| File | Action |
|------|--------|
| SOUL.md | Keep personality. **Remove** schedule table → moves to manifests. |
| AGENTS.md | Keep as-is. |
| WORKFLOW.md | Keep §0-5 (framework). **Remove** §6-10 → move to prompt templates. |
| PLAYBOOKS.md | **Deprecate** → replaced by manifests + templates. Add deprecation banner. |
| DATA_SOURCES.md | Keep §1-5 (priority). **Remove** §6 script index → auto-generated from manifests. |
| HEARTBEAT.md | **Simplify** to 3 lines. |
| TOOLS.md | **Delete** — subset of DATA_SOURCES.md. |
| IDENTITY.md | **Merge** into SOUL.md, delete. |
| MEMORY_SYSTEM.md | Keep as-is. |
| PHILOSOPHY.md | Keep as-is. |
| USER.md | Keep as-is. |

**Net**: 11 docs → 8 docs, ~45K tokens → ~20K tokens preamble per session.

---

## 4. Bug List (summary)

Full register: **`docs/trade-bug-audit-v1.md`**

| Severity | Count | Key items |
|----------|-------|-----------|
| **High** | 5 | Silent exception swallowing (F-01), no task state persistence (F-02), timezone bugs in 14 files (B-01–B-04), missing encoding (B-05), misleading pipeline name (F-03) |
| **Medium** | 7 | market_cache.py wrong key (B-08), race condition in memory_manager.py (B-12), AkShare instability (F-04), Stooq/FRED timeouts (F-05), no data freshness tracking (F-06), quality check text-only (F-07), report_quality_check false positives (B-07) |
| **Low** | 6 | Hardcoded paths (B-06), dead code (B-09–B-11), BSE exchange missing (B-13), memory compression untested (F-08), Puppeteer fallback fragile (F-09) |

---

## 5. Recommended First Pilot Tasks

### Pilot 1: Phase 0 bug fixes (immediate)

**Why first**: Safe, isolated fixes that improve reliability without structural risk.

**Scope**:
1. Fix all `datetime.now()` → `datetime.now(ZoneInfo("Asia/Shanghai"))` (14 files)
2. Fix all `open()` without `encoding='utf-8'` (8 locations)
3. Replace bare `except:` / `except Exception: pass` with logged exceptions
4. Fix `market_cache.py:432` wrong key

**Validation**: Run each modified script, verify output.

### Pilot 2: Intraday alert pipeline (simplest task type)

**Why**: PB-3 is the simplest — 2 scripts, no report, no discussion. Perfect for validating collection manifest + dispatcher.

**Scope**:
1. Write `manifests/intraday.yaml`
2. Write `collect.py` with manifest loading
3. Write minimal `dispatcher.py` for `task_type="intraday"`
4. Side-by-side test for 3 trading days

### Pilot 3: Premarket collection manifest

**Why**: PB-1 has a well-defined 12-item list. Validates full collection pipeline.

**Scope**:
1. Write `manifests/premarket.yaml` (12 scripts from PB-1 §3)
2. Run `collect("premarket", date)`, verify `manifest.json`
3. Test coverage gating: kill one required script, confirm degraded flag

### Pilot 4: Closing review worker flow

**Why**: Mid-complexity — analysis + writing + review, no multi-agent discussion. Validates worker flow before weekly report.

**Scope**:
1. Write prompt templates: `closing/analyze.md`, `closing/write.md`, `closing/review.md`
2. Implement `WorkerFlow` + `WorkerStep` in `worker.py`
3. End-to-end: collect → analyze → write → review → revise → deliver
4. User compares with current monolithic output

---

## 6. Open Questions

1. **Prompt template format**: Plain markdown with `{{placeholders}}` vs. YAML with metadata? → Start with plain markdown.
2. **Worker step execution**: Subagents vs. sequential in same conversation? → Subagents for weekly modules (isolation), sequential for daily (cheaper).
3. **Task state location**: `.state/` (gitignored) vs. `memory/state.json`? → Separate `.state/` — clean separation.
4. **Manifest format**: YAML vs. JSON? → YAML — supports comments.
5. **Heartbeat integration**: Direct call vs. task queue? → Direct call, add queue later if needed.
6. **Parallel collection**: Run independent scripts concurrently? → Yes, most data scripts are independent.

---

## 7. Non-Goals

- Rewriting data collection scripts (they work; orchestration is the problem)
- Changing memory system architecture
- Migrating away from OpenClaw
- Building a web UI
- Changing investment philosophy or analysis frameworks
- Rewriting PDF generation
- Adding new data sources or skills

---

## 8. Success Criteria

| Criterion | Measurement |
|-----------|-------------|
| No silent data failures | Every collection run produces `manifest.json` with per-script status |
| Resumable tasks | Interrupted task resumes from checkpoint, same result as uninterrupted |
| Report quality maintained | User rates refactored reports ≥ old system quality |
| Reduced context waste | Preamble drops from ~45K to ~20K tokens per session |
| Faster failure recovery | Failed tasks resume from checkpoint in <30s |
| Auditable pipeline | Every report traceable to collection manifest + analysis checkpoint + review feedback |
