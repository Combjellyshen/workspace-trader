# DATA_SOURCES.md — 数据源与调用优先级

> **职责边界**：本文件只记录数据源、可用性与调用优先级；不承担分析流程、人格规则或报告格式要求。
>
> 最后更新：2026-03-11

## 一、调用优先级总则
- **A 股数据**：本地 `scripts/*` > AkShare > Tushare
- **美股/海外**：MCP FMP > MCP yfinance > Alpha Vantage > edgartools
- **美国宏观**：FRED > FMP economics > US Fiscal Data
- **系统风险**：Hedge Fund Monitor
- **跨资产映射**：FMP（indexes / forex）> Stooq（commodities / crypto / DAX / DXY 补位）> Yahoo fallback；统一入口 `scripts/data/market_intel_pipeline.py`
- **消息面**：`scripts/data/rss_aggregator.py` + FMP news + yfinance news 交叉验证
- **技术指标**：A 股优先本地脚本；海外优先 FMP，其次 Alpha Vantage
- 主接口失败时，自动切换备用接口；仍失败则明确写“数据暂不可得”

## 二、A 股核心数据
| 数据类型 | 主接口 | 备用接口 | 说明 |
|---|---|---|---|
| 实时行情 | `scripts/data/market_cache.py` + AkShare | Tushare `pro.daily()` | 免费、快 |
| 历史 K 线 | AkShare `stock_zh_a_hist()` | Tushare `pro.daily()` | 支持前/后复权 |
| 板块行情 | `scripts/data/market_cache.py` + AkShare 板块接口 | — | 行业+概念 |
| 个股/行业资金流 | `scripts/data/deep_data.py` | AkShare 资金流接口 | 大单/超大单净流入 |
| 龙虎榜 | `scripts/data/deep_data.py` + AkShare | — | 异动席位追踪 |
| 北向资金 | `scripts/data/institution_tracker.py` + Tushare `pro.moneyflow_hsgt()` | — | 每日+个股维度 |
| 财务报表 | `scripts/data/stock_profile.py` + AkShare | Tushare 三表接口 | 三表+关键指标 |
| 财务指标 | AkShare 财务分析指标 | Tushare `pro.fina_indicator()` | ROE/毛利率/现金流 |
| 估值分位 | `scripts/data/stock_valuation_history.py` | — | PE/PB/PS 分位 |
| 技术分析 | `scripts/analysis/tech_analysis.py` | — | MACD/RSI/均线/布林 |
| 筹码/主力 | `scripts/analysis/tick_chip.py` + `scripts/analysis/main_force.py` | — | 大单与筹码行为 |
| 市场广度 | `scripts/analysis/market_breadth.py` | — | 涨跌比/新高新低 |
| 情绪面 | `scripts/analysis/sentiment.py` | — | 情绪综合评分 |

## 三、海外与跨资产数据
| 数据类型 | 主接口 | 备用接口 | 说明 |
|---|---|---|---|
| 美股实时行情 | MCP `yfinance.get_quote` | MCP FMP quotes | 快速快查 |
| 美股历史数据 | MCP `yfinance.get_historical` | Alpha Vantage / FMP charts | — |
| 美股财务报表 | MCP FMP statements | MCP yfinance / edgartools | FMP 最全 |
| 公司档案 | MCP FMP company | MCP yfinance | 主营/高管/公司信息 |
| 分析师评级 | MCP FMP analyst | — | 目标价/评级共识 |
| DCF 估值 | MCP FMP dcf | — | 自动折现模型 |
| 内部交易 | MCP FMP insider-trades | edgartools Form 4 | — |
| 机构持仓 | MCP FMP institutional | edgartools 13F | — |
| SEC 文件 | MCP FMP sec-filings | edgartools | 深度监管文件 |
| ETF/基金 | MCP FMP etf-funds | Tushare 基金接口 | — |
| 技术指标 | MCP FMP technical-indicators | Alpha Vantage | 海外优先 FMP |
| 财经新闻 | MCP FMP news + yfinance news | Alpha Vantage NEWS_SENTIMENT | 三源交叉 |
| 财经日历 | MCP FMP calendar | — | 财报/分红/IPO |
| 指数（美股/港股/日股） | MCP FMP indexes | Stooq / Yahoo fallback | 跨资产映射主链 |
| 欧股 DAX | Stooq CSV | Yahoo fallback | FMP 当前存在权限缺口时优先 Stooq |
| 商品（原油/黄金/铜） | Stooq historical table | Yahoo fallback | 当前稳定性优于 Yahoo |
| 外汇（USD/CNH / USDJPY / EURUSD） | MCP FMP forex | Yahoo fallback | USD/CNH 允许回退 USDCNY 近似代理 |
| 美元指数 | Stooq historical table（DX.F 连续期货代理） | Yahoo fallback | 用于风险偏好/美元强弱映射 |
| 加密资产（BTC / ETH） | Stooq CSV | Yahoo fallback | 供风险偏好映射与周报跨资产章节使用 |

## 四、宏观与系统风险数据
| 数据类型 | 主接口 | 备用接口 | 说明 |
|---|---|---|---|
| 中国宏观 | `scripts/data/macro_deep.py` + `scripts/data/macro_monitor.py` | Tushare 宏观接口 | PMI/利率/社融 |
| 美国宏观 | FRED | MCP FMP economics | GDP/CPI/失业/利率 |
| 美国财政 | US Fiscal Data | — | 国债/财政收支 |
| 全球人口/经济 | Data Commons | — | 人口/GDP/失业 |
| 对冲基金与系统风险 | Hedge Fund Monitor | — | 杠杆/回购/Form PF |
| 国债收益率 | FRED | US Fiscal Data / Alpha Vantage | 每日更新 |

## 五、MCP Server 状态（当前记录）
| Server | 状态 | 传输 | 已注册工具数 |
|---|---|---|---|
| yfinance | ✅ OK | STDIO | 6 |
| fmp | ✅ OK | HTTP 127.0.0.1:18900 | ~200 |

## 六、脚本命令速查

所有脚本路径：`/home/bot/.openclaw/workspace-trader/scripts/`

### 核心数据

| 命令 | 数据内容 |
|------|----------|
| `deep_data.py snapshot` | 指数行情 + 行业/概念资金流 + 个股主力TOP + 龙虎榜50 + 北向10天 + 自选实时 |
| `deep_data.py industry` | 行业资金流排名 |
| `deep_data.py concept` | 概念资金流排名 |
| `deep_data.py stock_flow` | 个股主力净流入 TOP30 |
| `deep_data.py dragon` | 龙虎榜明细（机构买卖、原因） |
| `deep_data.py north` | 沪深港通近10天 |
| `deep_data.py hot` | 同花顺热度（**每天限2次**） |
| `deep_data.py quote <codes>` | 任意股票/指数实时行情 |

### 券商研报

| 命令 | 数据内容 |
|------|----------|
| `research_reports.py stock <code>` | 个股评级+目标价+摘要（近3月） |
| `research_reports.py industry` | 行业评级+策略（近7天） |
| `research_reports.py strategy` | 宏观策略研报（近7天） |
| `research_reports.py latest` | 全市场最新个股研报（近3天） |

### 消息面 & 情绪面

| 命令 | 数据内容 |
|------|----------|
| `rss_aggregator.py categorized` | 10源RSS自动分类（央行/A股/全球/大宗/地缘/科技/加密） |
| `rss_aggregator.py domestic` | 国内（财联社+华尔街见闻） |
| `rss_aggregator.py international` | 国际（CNBC/FT/MarketWatch/ZeroHedge/Yahoo/CoinDesk/OilPrice） |
| `sentiment.py all` | 情绪全景：同花顺热股+东财人气+百度热搜 |
| `sentiment.py hot_stocks [N]` | 同花顺热股 TOP N |
| `sentiment.py popularity` | 东财人气 TOP20 |
| `sentiment.py baidu` | 百度股市通热搜 |

### K线 & 技术面

| 命令 | 数据内容 |
|------|----------|
| `tushare_data.py kline <code>` | 60日K线+MA5/10/20+趋势 |
| `tushare_data.py north` | Tushare北向数据 |
| `stock_analyzer.py --watchlist` | 自选股批量分析（新浪行情） |

### 记忆系统脚本

| 命令 | 用途 |
|------|------|
| `memory_manager.py save_reports` | 归档当日全市场研报 |
| `memory_manager.py save_signals` | 保存关键信号（stdin JSON） |
| `memory_manager.py save_sentiment` | 保存情绪面快照 |
| `memory_manager.py compress` | 压缩 >6个月历史为 .gz |
| `memory_manager.py query_reports <code> [months]` | 查历史评级变化 |
| `memory_manager.py query_signals [days]` | 查近N天信号 |
| `memory_manager.py query_reviews [days]` | 查近N天复盘 |
| `memory_manager.py status` | 连板/异动/形态追踪 |
| `memory_manager.py update_state` | 更新追踪状态（stdin） |
| `memory_manager.py disk_usage` | 存储统计 |

### 其他

| 命令 | 数据内容 |
|------|----------|
| `lottery.py` | 双色球/大乐透历史数据 |

## 七、数据源频率限制

| 数据 | 来源 | 限制 |
|------|------|------|
| 指数/个股行情 | 新浪 | 无 |
| 行业/概念资金流 | 新浪 | 无 |
| 个股主力流入TOP | 新浪 | 无 |
| 龙虎榜(机构席位) | 东财 | 无 |
| 券商研报 | 东财 | 无 |
| 同花顺热股 | 同花顺 | 无 |
| 东财人气榜 | 东财 | 无 |
| 百度热搜 | 百度 | 无 |
| RSS消息面 | 多源 | 无 |
| 北向资金 | Tushare | 无 |
| K线 | Tushare | 1次/分 |
| 同花顺热度(Tushare) | Tushare | **2次/天** |
| ❌ PE/PB/ROE | Tushare | 积分不够 |
| ❌ 指数日线 | Tushare | 积分不够 |

## 八、API Key / 凭据依赖
| 接口 | Key 来源 | 状态 |
|---|---|---|
| Tushare | `config.json` | ✅ 已配置 |
| Alpha Vantage | 环境变量 `ALPHAVANTAGE_API_KEY` | ✅ 已配置 |
| FRED | 环境变量 `FRED_API_KEY` | ✅ 已配置 |
| FMP | 通过 MCP Server 管理 | ✅ 已就绪 |
| yfinance | 无需 Key | ✅ |
| US Fiscal Data | 无需 Key | ✅ |
| Hedge Fund Monitor | 无需 Key | ✅ |
| Data Commons | 环境变量 `DC_API_KEY` | ✅ 已配置 |
| AkShare | 无需 Key | ✅ |
| edgartools | 环境变量 `EDGAR_IDENTITY` | ✅ 已配置 |
