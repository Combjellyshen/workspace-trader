#!/usr/bin/env bash
set -euo pipefail
cd /home/bot/.openclaw/workspace-trader
TODAY="2026-03-11"
OUTDIR="data/weekly_inputs/${TODAY}"
REPORTDIR="reports/weekly"
WEEKLYDATA="${REPORTDIR}/${TODAY}-weekly-data.txt"
mkdir -p "$OUTDIR" "$REPORTDIR"
: > "$WEEKLYDATA"

run() {
  local outfile="$1"; shift
  local title="$1"; shift
  {
    echo "## ${title}"
    echo "COMMAND: $*"
    echo "TIME: $(date -Is)"
    if "$@" > "$outfile" 2>&1; then
      echo "STATUS: success"
    else
      code=$?
      echo "STATUS: failed($code)"
    fi
    echo
    echo "### OUTPUT FILE: ${outfile}"
    cat "$outfile" 2>/dev/null || true
    echo
    echo "---"
    echo
  } >> "$WEEKLYDATA"
}

run "$OUTDIR/01_market_cache_save.txt" "01 market_cache save_market" python3 scripts/data/market_cache.py save_market
run "$OUTDIR/02_deep_snapshot.txt" "02 deep_data snapshot" python3 scripts/data/deep_data.py snapshot
run "$OUTDIR/03_industry_flow.txt" "03 deep_data industry" python3 scripts/data/deep_data.py industry
run "$OUTDIR/04_concept_flow.txt" "04 deep_data concept" python3 scripts/data/deep_data.py concept
run "$OUTDIR/05_northbound.txt" "05 deep_data north" python3 scripts/data/deep_data.py north
run "$OUTDIR/06_hot.txt" "06 deep_data hot" python3 scripts/data/deep_data.py hot
run "$OUTDIR/07_market_breadth.txt" "07 market_breadth all" python3 scripts/analysis/market_breadth.py all
run "$OUTDIR/08_sentiment.txt" "08 sentiment all 30" python3 scripts/analysis/sentiment.py all 30
run "$OUTDIR/09_macro_deep_all.txt" "09 macro_deep all" python3 scripts/data/macro_deep.py all
run "$OUTDIR/10_macro_summary.txt" "10 macro_deep summary" python3 scripts/data/macro_deep.py summary
run "$OUTDIR/11_macro_monitor_all.txt" "11 macro_monitor.py all" python3 scripts/data/macro_monitor.py all
run "$OUTDIR/12_strategy_reports.txt" "12 research_reports strategy" python3 scripts/data/research_reports.py strategy
run "$OUTDIR/13_industry_reports.txt" "13 research_reports industry" python3 scripts/data/research_reports.py industry
run "$OUTDIR/14_rss_all.txt" "14 rss_aggregator all" python3 scripts/data/rss_aggregator.py all
run "$OUTDIR/15_cross_asset.txt" "15 market_intel_pipeline weekly" python3 scripts/data/market_intel_pipeline.py weekly --date "$TODAY"
run "$OUTDIR/16_event_calendar.txt" "16 event_calendar_builder" python3 scripts/data/event_calendar_builder.py
run "$OUTDIR/17_market_regime.txt" "17 market_regime_detector" python3 scripts/analysis/market_regime_detector.py
run "$OUTDIR/18_watchlist_context.txt" "18 watchlist_context_builder" python3 scripts/memory/watchlist_context_builder.py
run "$OUTDIR/19_institution_all.txt" "19 institution_tracker all" python3 scripts/data/institution_tracker.py all
run "$OUTDIR/20_consistency.txt" "20 data_consistency_guard" python3 scripts/utils/data_consistency_guard.py

echo "DONE: $TODAY" >> "$WEEKLYDATA"
