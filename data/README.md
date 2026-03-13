# data/

运行产物与缓存的主目录。

当前承接：
- `data/news/`：消息归档
- `data/market_cache/`：全市场日度缓存
- `data/intraday/`：盘中快照
- `data/daily_inputs/`：盘前 / 收盘复盘的标准输入产物
- `data/weekly_inputs/`：周报的标准输入产物

说明：
- 新的运行产物统一写入 `data/`
- 跨资产标准输入由 `python3 scripts/data/market_intel_pipeline.py <premarket|postmarket|weekly>` 生成
- 长期记忆、知识、个股档案仍留在 `memory/`
- 不再依赖旧目录兼容入口
