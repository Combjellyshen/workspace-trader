# Trade Workspace Bug Audit v1

> **Date**: 2026-03-22
> **Scope**: All Python scripts in `scripts/`, all markdown docs, scheduling/orchestration
> **Companion**: `docs/trade-workspace-refactor-plan-v1.md` (refactor plan)

---

## Code Bugs

### B-01 — Timezone-naive `datetime.now()` in intraday_alert.py [High]

**File**: `scripts/analysis/intraday_alert.py` lines 407, 505, 574
**Impact**: Snapshot filenames and check timestamps use system-local time instead of Asia/Shanghai. Off-by-day issues if system TZ differs (e.g., UTC server).
**Fix**: `from zoneinfo import ZoneInfo; TZ = ZoneInfo("Asia/Shanghai")` — replace `datetime.now()` with `datetime.now(TZ)` at all three locations.
**Phase**: 0

### B-02 — Timezone-naive `datetime.now()` in memory_manager.py [High]

**File**: `scripts/memory/memory_manager.py` lines 57-58
**Impact**: `today_str()` and `month_str()` functions produce wrong dates on non-Shanghai TZ systems. Signals, reviews, and reports may be filed under wrong date.
**Fix**: Same as B-01.
**Phase**: 0

### B-03 — Timezone-naive `datetime.now()` in common.py [High]

**File**: `scripts/utils/common.py` line 159
**Impact**: Timestamp metadata in all script outputs uses local time without TZ marker.
**Fix**: Same as B-01.
**Phase**: 0

### B-04 — Timezone-naive `datetime.now()` in 11 other analysis scripts [High]

**Files**: tech_analysis.py, kline_detailed_review.py, watchlist_deep_dive.py, cross_day_compare.py, growth_hunter.py, main_force.py, market_breadth.py, peg_screener.py, sector_screener.py, tick_chip.py, philosophy_updater.py
**Impact**: Same as B-01 — wrong dates on non-Shanghai TZ.
**Fix**: Same as B-01. Apply systematically to all 14 files.
**Phase**: 0

### B-05 — Missing `encoding='utf-8'` on file open [High]

**Files**:
- `scripts/memory/memory_manager.py` lines 106, 138, 215, 253, 279, 302 (6 locations)
- `scripts/reporting/md_to_pdf.py` line 111 (1 location)
- `scripts/analysis/cross_day_compare.py` line 43 (1 location)
**Impact**: Will raise `UnicodeDecodeError` on systems where default locale isn't UTF-8 (e.g., some Docker images). Chinese text in memory files and reports may be corrupted.
**Fix**: Add `encoding='utf-8'` to all `open()` calls.
**Phase**: 0

### B-06 — Hardcoded linuxbrew path in md_to_pdf.py [Low]

**File**: `scripts/reporting/md_to_pdf.py` line 15
**Code**: `_BREW_LIB = "/home/linuxbrew/.linuxbrew/lib"`
**Impact**: Path doesn't exist on non-Homebrew Linux. Not a crash (guarded), but misleading.
**Fix**: Make conditional or derive from environment.
**Phase**: 5

### B-07 — False positives in report_quality_check.py [Medium]

**File**: `scripts/reporting/report_quality_check.py` line 58
**Code**: `FORBIDDEN_PATTERNS` regex `\{[^{}]+\}` matches legitimate JSON/code blocks in markdown.
**Impact**: Quality check may reject valid reports containing inline JSON examples or code snippets.
**Fix**: Strip fenced code blocks (` ``` `) before applying forbidden pattern regexes.
**Phase**: 4

### B-08 — Wrong key reference in market_cache.py [Medium]

**File**: `scripts/data/market_cache.py` line 432
**Code**: `len(data.get('market_spot', []))` — key `'market_spot'` doesn't exist in the data structure.
**Impact**: Always prints 0 for stock count when running `python3 market_cache.py load`. Functional data is fine, only display output is wrong.
**Fix**: Use the correct key (likely `'market_snapshot'` or the actual key used by the save function).
**Phase**: 0

### B-09 — Bare `except:` in tushare_data.py [High → Medium]

**File**: `scripts/data/tushare_data.py` line 23
**Code**: `except:` without exception type — catches `KeyboardInterrupt`, `SystemExit`, etc.
**Impact**: Cannot interrupt the script cleanly; masks real errors like permission failures.
**Fix**: Change to `except Exception as e:` with logging.
**Phase**: 0

### B-10 — Bare `except:` in peg_screener.py [Medium]

**File**: `scripts/analysis/peg_screener.py` lines 55, 146
**Impact**: PEG calculation failures silently return None; column parsing failures silently continue. No way to diagnose why a stock got no PEG score.
**Fix**: `except Exception as e: sys.stderr.write(f"PEG calc failed for {code}: {e}\n")`
**Phase**: 0

### B-11 — Bare `except:` in sector_screener.py [Medium]

**File**: `scripts/analysis/sector_screener.py` line 213
**Impact**: Data extraction failures in sector loop silently continue. Sector analysis may be incomplete with no warning.
**Fix**: Same pattern as B-10.
**Phase**: 0

### B-12 — Race condition in memory_manager.py [Medium]

**File**: `scripts/memory/memory_manager.py` lines 104-121 (save_signals), 131-152 (save_sentiment)
**Pattern**: Read-modify-write without file locking.
**Impact**: If two processes call save_signals simultaneously (unlikely but possible during manual + scheduled overlap), one write is lost.
**Fix**: Use `fcntl.flock()` or atomic write (write to temp file, then `os.rename`).
**Phase**: 1

### B-13 — Missing BSE exchange detection in intraday_alert.py [Low]

**File**: `scripts/analysis/intraday_alert.py` lines 323-326
**Code**: `if code.startswith("6"): market = "sh"` — misses Beijing Stock Exchange codes (8xxxxx).
**Impact**: BSE-listed stocks get wrong market prefix, leading to failed quote lookups.
**Fix**: Add `elif code.startswith("8") or code.startswith("4"): market = "bj"`.
**Phase**: 0

### B-14 — Module-level imports that crash on missing packages [Medium]

**File**: `scripts/data/cross_asset_snapshot.py` lines 30-31
**Code**: `import requests` and `from bs4 import BeautifulSoup` at top level.
**Impact**: Any script importing this module will crash if bs4 isn't installed, even if it doesn't use BeautifulSoup functions.
**Fix**: Move to lazy imports inside functions that use them.
**Phase**: 1

### B-15 — Dead code / redundant imports in deep_data.py [Low]

**File**: `scripts/data/deep_data.py` lines 181, 195
**Code**: Re-imports `akshare as _ak2`, `akshare as _ak3` (already imported at top).
**Impact**: No functional impact. Code clutter.
**Fix**: Remove redundant imports; use single alias.
**Phase**: 5

---

## Architecture Fragilities

### F-01 — Silent exception swallowing across data scripts [High]

**Files**: intraday_alert.py (lines 139, 222, 296, 387, 456), deep_data.py (multiple), cross_asset_snapshot.py (multiple)
**Pattern**: `except Exception: pass` or `except Exception: continue` — failed data collection is invisible.
**Impact**: Reports may be written with stale/missing data and no indication. The quality check only validates report *text*, not whether underlying data was successfully collected.
**Fix (Phase 0)**: Add `sys.stderr.write()` logging to all silent catch blocks.
**Fix (Phase 1)**: Collection pipeline manifest tracks per-script success/failure explicitly.

### F-02 — No task state persistence [High]

**Description**: If the Claude agent crashes or times out mid-playbook (e.g., during report writing after 20 minutes of data collection), there's no checkpoint. The entire task must re-run from scratch.
**Impact**: Wasted compute, delayed reports, potential missed delivery windows.
**Fix (Phase 3)**: Dispatcher writes `.state/tasks/{type}-{date}.json` at each pipeline stage.

### F-03 — Misleading `market_intel_pipeline.py` name and scope [High]

**File**: `scripts/data/market_intel_pipeline.py`
**Description**: Named "pipeline" and documented as "central orchestrator" but only handles cross-asset snapshots (FMP/Stooq/Yahoo). The actual 12-13 item data collection per playbook is done ad-hoc by the agent reading PLAYBOOKS.md prose.
**Impact**: No programmatic enforcement of data collection completeness. If the agent skips a step, there's no guardrail.
**Fix (Phase 1)**: Rename to `cross_asset_snapshot_pipeline.py` or fold into the new `collect.py` orchestrator.

### F-04 — AkShare API instability [Medium]

**Description**: AkShare frequently changes column names and API signatures. Current `pick_col()` defensive matching is good but still fragile. Multiple recent failures noted in memory logs (institute_survey, fund_flow).
**Impact**: Scripts produce empty/wrong results silently when column names change.
**Fix**: Add column-name mismatch detection to `pick_col()` — log a warning when fallback matching is used. Consider pinning akshare version.

### F-05 — Stooq/FRED timeouts in cross_asset_snapshot.py [Medium]

**File**: `scripts/data/cross_asset_snapshot.py`
**Description**: 6s timeout for Stooq, 15s for FRED. Both regularly fail, degrading cross-asset coverage.
**Impact**: Weekly/daily reports may lack commodity, forex, or yield data with no clear indication.
**Fix (Phase 1)**: Circuit breaker pattern. Cache last successful result. Track failure rate. Use cached fallback after 3 consecutive failures.

### F-06 — No data freshness tracking [Medium]

**Description**: Scripts don't tag output with timestamps or source reliability. Quality check only validates report text (keyword presence), not whether underlying data is fresh.
**Impact**: A report could cite "today's data" that's actually from 3 days ago after a cache hit, and nothing flags it.
**Fix (Phase 1)**: Add `_meta` freshness tags to all script output.

### F-07 — Quality check is text-only [Medium]

**File**: `scripts/reporting/report_quality_check.py`
**Description**: Does keyword matching on markdown text. Cannot verify that cited numbers match data, that coverage claims are true, or that analysis is logically consistent.
**Impact**: Reports can pass quality check while having factual errors or stale data.
**Fix (Phase 4)**: Add freshness validation, section count validation, and data coverage checks to quality pipeline.

### F-08 — Memory compression never tested [Low]

**File**: `scripts/memory/memory_manager.py` (compress command)
**Description**: Documented to run monthly, but the system is only ~2 weeks old. The gzip compression path is untested in production.
**Impact**: Unknown failure mode when compression first runs.
**Fix**: Test manually before it's needed. Add dry-run flag.

### F-09 — Puppeteer PDF fallback fragile [Low]

**File**: `scripts/reporting/md_to_pdf.py`
**Description**: Inline JavaScript string for Puppeteer — no error details if Puppeteer fails. WeasyPrint is primary and mostly works.
**Impact**: If WeasyPrint fails on a complex layout, Puppeteer fallback may also fail silently.
**Fix**: Add error capture and logging to Puppeteer code path.

### F-10 — Daily input files have no schema validation [Low]

**Directory**: `data/daily_inputs/`
**Description**: Files are numbered `00_*` through `20_*` but there's no manifest or schema. A missing numbered file is not detected by any automated check.
**Impact**: Reports may silently lack coverage of a data dimension.
**Fix (Phase 1)**: Collection manifest replaces numbered file convention with explicit per-script tracking.

### F-11 — Documentation overlap and bloat [Medium]

**Files**: 11 markdown docs, ~45K tokens total
**Description**: WORKFLOW.md §6 duplicates PB-4. TOOLS.md is a subset of DATA_SOURCES.md §6. IDENTITY.md overlaps SOUL.md. PLAYBOOKS.md data lists duplicate DATA_SOURCES.md.
**Impact**: Token waste (~25K unnecessary tokens loaded per session). Conflicting instructions when docs diverge.
**Fix (Phase 5)**: Consolidate to 8 docs, ~20K tokens. See refactor plan §3 Phase 5.

---

## Summary by Severity

| Severity | Bugs | Fragilities | Total |
|----------|------|-------------|-------|
| High | B-01, B-02, B-03, B-04, B-05, B-09 | F-01, F-02, F-03 | 9 |
| Medium | B-07, B-08, B-10, B-11, B-12, B-14 | F-04, F-05, F-06, F-07, F-11 | 11 |
| Low | B-06, B-13, B-15 | F-08, F-09, F-10 | 6 |
| **Total** | **15** | **11** | **26** |

## Fix Priority (recommended order)

1. **Immediate (Phase 0)**: B-01–B-05, B-08, B-09–B-11, B-13, F-01 (logging only)
2. **Phase 1**: B-12, B-14, F-01 (full), F-03, F-05, F-06, F-10
3. **Phase 4**: B-07, F-07
4. **Phase 5**: B-06, B-15, F-11
5. **Monitor**: F-04 (AkShare), F-08 (compression), F-09 (Puppeteer)
