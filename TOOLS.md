# TOOLS.md - 工具速查

## 数据脚本入口
- 统一入口：`python3 scripts/data/market_intel_pipeline.py <premarket|postmarket|weekly>`
- 详细脚本列表见 `DATA_SOURCES.md` §6

## MCP 工具
- `mcporter call yfinance.get_quote symbol=<ticker>` — 实时外盘报价
- `mcporter call fmp.enable_toolset name=quotes` — FMP 数据接口

## PDF 生成
- `python3 scripts/reporting/md_to_pdf.py input.md output.pdf`
- 优先 WeasyPrint → 降级 Puppeteer
- HTML 副本自动保存

## 报告质检
- `python3 scripts/reporting/report_quality_check.py <file.md>`

## 记忆操作
- 详见 `MEMORY_SYSTEM.md` §6

