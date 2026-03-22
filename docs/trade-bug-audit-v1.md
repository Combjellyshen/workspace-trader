# Trade Workspace Bug Audit v1

> Date: 2026-03-22
> Scope: `workspace-trader`
> Goal: list production failures, verified code bugs, and architecture fragilities that should be folded into the refactor.

---

## 1. Production failures already visible in scheduler state

### P1. Closing review job times out
- Job: `A股收盘复盘`
- Current scheduler state: `lastRunStatus=error`, `lastErrorReason=timeout`
- Observed duration: ~1800s effective cap
- Why it matters:
  - end-of-day report is one of the highest-value recurring outputs
  - timeout suggests current monolithic run is too long / too serial / too fragile
- Refactor direction:
  - split into `collect -> analyze -> write -> review -> deliver`
  - persist step state so retries resume from checkpoint instead of restarting entire task

### P2. Weekly data precollect job times out
- Job: `周报数据预采集`
- Current scheduler state: `lastRunStatus=error`, `lastErrorReason=timeout`
- Why it matters:
  - weekly report currently depends on a large serial collection chain
  - failure at precollect poisons the entire Friday pipeline
- Refactor direction:
  - move to manifest-driven collection with required/optional inputs and explicit degradation rules

### P3. Weekly market insight hits rate limit
- Job: `砚·周度市场洞察`
- Current scheduler state: `lastRunStatus=error`, `lastErrorReason=rate_limit`
- Why it matters:
  - current weekly generation is too bursty and expensive
  - discussion + longform generation + review are not rate-aware
- Refactor direction:
  - split weekly flow into multiple worker stages
  - serialize Claude-heavy steps
  - make review/revise incremental instead of full-regenerate

---

## 2. Verified code-level bugs

### B1. `intraday_alert.py` uses naive local time in multiple places
- File: `scripts/analysis/intraday_alert.py`
- Verified points:
  - `datetime.now().strftime("%Y-%m-%d")`
  - `datetime.now().strftime("%Y-%m-%d %H:%M")`
  - `datetime.now().strftime("%Y-%m-%d-%H%M")`
- Risk:
  - if host timezone differs from Asia/Shanghai, snapshot naming and date matching can drift
- Fix:
  - standardize on `ZoneInfo("Asia/Shanghai")`

### B2. `memory_manager.py` also uses naive local time
- File: `scripts/memory/memory_manager.py`
- Verified points:
  - `today_str()`
  - `month_str()`
- Risk:
  - report/memory archival dates can drift across timezone boundaries
- Fix:
  - same Asia/Shanghai timezone helper as above

### B3. `md_to_pdf.py` reads markdown without explicit UTF-8 encoding
- File: `scripts/reporting/md_to_pdf.py`
- Verified point:
  - `with open(md_path, 'r') as f:`
- Risk:
  - locale-dependent failure on non-UTF-8 environments
- Fix:
  - read with `encoding='utf-8'`

### B4. `intraday_alert.py` exchange routing is incomplete
- File: `scripts/analysis/intraday_alert.py`
- Verified point:
  - `if code.startswith("6"): market = "sh" else: market = "sz"`
- Risk:
  - Beijing exchange / other future code families are mis-routed
- Fix:
  - centralize market inference in shared helper

### B5. `cross_asset_snapshot.py` imports optional scraping deps at module import time
- File: `scripts/data/cross_asset_snapshot.py`
- Verified point:
  - top-level `import requests`
  - top-level `from bs4 import BeautifulSoup`
- Risk:
  - any importer crashes immediately if dependency is missing, even when those functions are not used
- Fix:
  - lazy-import provider-specific deps inside the provider functions, or hard-fail with explicit dependency message

### B6. `intraday_alert.py` still swallows failures too aggressively
- File: `scripts/analysis/intraday_alert.py`
- Verified point:
  - multiple `except Exception: pass` / `continue`
- Risk:
  - silent data gaps, invisible collection failures, false sense of success
- Fix:
  - replace silent swallow with structured warnings/errors

---

## 3. Important nuance: the old `intraday=0.0` placeholder conflict is partly fixed already

### N1. Placeholder handling exists in current `data_consistency_guard.py`
- File: `scripts/utils/data_consistency_guard.py`
- Verified point:
  - guard now detects `intraday_placeholder`
  - skips hard conflict when intraday returns `(0, 0)` placeholder values
- Implication:
  - this is **not a fresh code bug in the guard anymore**
  - but older generated artifacts still show the historical noisy conflict pattern

### N2. Remaining problem is downstream contract cleanliness
- Risk:
  - even with guard-side mitigation, upstream tasks still emit placeholder-heavy intraday snapshots
  - reports and handoff files can still inherit confusing warnings or stale assumptions
- Refactor direction:
  - formalize placeholder/stale/degraded states in collection manifests instead of leaving interpretation to prose

---

## 4. Architecture fragilities that should be fixed as part of refactor

### F1. No real central pipeline runtime yet
- `PLAYBOOKS.md` describes stages in prose, but execution is still mostly “agent reads doc and manually does steps”
- Result:
  - skipped steps are hard to detect
  - retry/recover is weak
  - state is not durable enough

### F2. `market_intel_pipeline.py` name over-promises
- It sounds like the main orchestrator
- In practice it is mostly a cross-asset / input preparation entrypoint, not the full scheduler-aware pipeline
- Result:
  - system intent and implementation drift apart

### F3. Quality check is mostly text-structure based
- `report_quality_check.py` verifies presence of sections/keywords
- It does **not** verify:
  - data freshness
  - provenance quality
  - internal numerical consistency
  - whether the report truly used the promised inputs

### F4. No durable task manifest / run-state / artifact contract
- There is no unified per-run state machine like:
  - collect
  - normalize
  - discuss
  - write
  - review
  - revise
  - deliver
- Result:
  - long jobs fail whole-chain
  - hard to recover after timeout/rate limit
  - hard to tell which artifact belongs to which run

### F5. Current cron jobs are too heavy at the wrong stage boundary
- Collection and generation are both long-running and often serialized in single logical runs
- Result:
  - timeout risk
  - rate-limit risk
  - poor observability

---

## 5. Recommended fix order

### Immediate safe fixes
1. timezone hardening in `intraday_alert.py`
2. timezone hardening in `memory_manager.py`
3. UTF-8 explicit read in `md_to_pdf.py`
4. remove/replace silent `except Exception: pass`

### Refactor-integrated fixes
5. introduce run manifests + stage state
6. split report generation into Claude worker stages
7. make weekly flow rate-aware and checkpointed
8. separate collection gating from content generation
9. make quality checks validate artifacts, not only markdown text

---

## 6. Best pilot tasks for cutover

1. `A股开盘前分析`
2. `A股收盘复盘`

Why these first:
- highest user value
- frequent enough to validate quickly
- expose both collection and longform generation issues
- easier than weekly report, but representative of the full architecture
