# scripts/ 目录说明

本目录已按职责拆分为子目录，**顶层兼容包装层已清理**。后续请直接使用子目录下的真实脚本路径。

## 当前结构
- `scripts/data/`：行情、财务、宏观、新闻、事件日历、缓存相关脚本
- `scripts/analysis/`：技术面、资金面、情绪面、多因子、筛选与轮动分析
- `scripts/reporting/`：Markdown/HTML/PDF 渲染与质检
- `scripts/memory/`：记忆管理、观察池上下文、哲学更新
- `scripts/utils/`：校验与通用辅助脚本
- `scripts/legacy/`：已停用但暂保留的旧脚本

## 当前规则
- **不要再使用** 顶层 `scripts/*.py` 旧路径
- 新增脚本必须直接放入对应子目录
- 文档、任务说明、自动化流程统一使用子目录真实路径

## 示例
- `python3 scripts/data/market_cache.py status`
- `python3 scripts/analysis/intraday_alert.py snapshot`
- `python3 scripts/data/market_intel_pipeline.py premarket`
- `python3 scripts/data/market_intel_pipeline.py weekly --date 2026-03-10`
- `python3 scripts/reporting/report_quality_check.py reports/daily/2026-03-10-closing.md`

## 新增统一编排入口
- `scripts/data/market_intel_pipeline.py`
  - `snapshot`：直接输出跨资产 JSON
  - `premarket`：生成 `data/daily_inputs/<date>/09_cross_asset.txt`
  - `postmarket`：生成 `data/daily_inputs/<date>/09_cross_asset.txt`
  - `weekly`：生成 `data/weekly_inputs/<date>/15_cross_asset.txt`
- 当前已把新的跨资产数据链统一接到该入口：
  - FMP（海外指数 / 外汇）
  - Stooq（商品 / 加密 / DAX / 美元指数补位）
  - FRED（美债收益率）
  - Yahoo fallback（兜底）
