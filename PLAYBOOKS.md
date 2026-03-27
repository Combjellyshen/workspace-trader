# 任务执行手册（Playbooks）

> **用途**：无论是定时任务触发还是用户手动要求，同一类型的任务必须执行同一套流程。
> 本文件定义每类任务的完整执行阶段。Cron prompt 和手动请求共享同一标准。
>
> - 输出结构规范：`WORKFLOW.md`
> - 数据脚本速查：`DATA_SOURCES.md` §6
> - 记忆系统操作：`MEMORY_SYSTEM.md`

---

## PB-1. 盘前分析（A股开盘前分析）

**定位**：每交易日盘前深度报告，不追求周报级篇幅，但必须把分析做透。

### 阶段 1：记忆回顾
- `python3 scripts/memory/memory_manager.py query_signals 7` — 近一周信号
- `python3 scripts/memory/memory_manager.py query_reviews 3` — 近 3 天复盘
- `python3 scripts/memory/memory_manager.py status` — 当前记忆状态
- `python3 scripts/memory/memory_manager.py query_reports 3` — 近 3 天报告摘要

目标：建立"昨日判断 → 今日验证"的连续性上下文。

### 阶段 2：预采集数据读取
- 读取 `reports/daily/{today}-data.txt`（如缺失则应急执行预采集）
- 读取 `data/daily_inputs/{today}/09_cross_asset.txt`
- `python3 scripts/utils/data_consistency_guard.py`

### 阶段 3：数据补全（12 项）
| # | 命令 | 用途 |
|---|------|------|
| 1 | `python3 scripts/data/deep_data.py snapshot` | 主要指数实时行情 |
| 2 | `python3 scripts/data/rss_aggregator.py categorized` | 财经新闻分类 |
| 3 | `python3 scripts/data/research_reports.py strategy` | 卖方策略纪要 |
| 4 | `python3 scripts/analysis/sentiment.py all` | 多维情绪面 |
| 5 | `python3 scripts/analysis/market_breadth.py all` | 涨跌家数 / 强弱 |
| 6 | `python3 scripts/analysis/cross_day_compare.py all` | 跨日数据比较 |
| 7 | `python3 scripts/analysis/watchlist_deep_dive.py summary` | 自选池深度摘要（验证/证伪/交易计划） |
| 8 | `python3 scripts/analysis/tech_analysis.py watchlist` | 自选股技术面 |
| 9 | `python3 scripts/analysis/kline_detailed_review.py all` | 详细K线技术复盘（指数+观察池，含变化原因） |
| 10 | `python3 scripts/analysis/main_force.py batch` | 主力资金方向 |
| 11 | `python3 scripts/data/institution_tracker.py all` | 机构持仓变化 |
| 12 | `python3 scripts/data/macro_monitor.py all` | 宏观先行指标 |

### 阶段 4：深度分析
- 按 `WORKFLOW.md` §8 分析框架权重（消息→资金→情绪→技术→研报）
- 必须采用 **结论→证据→推理→风险→行动** 结构
- **五角色论证**（强制）：多头主力 / 空头主力 / 量化观察员 / 风控官 / 主编裁决
- 消息面必须进入主论证链条，不得只做新闻罗列
- 必须形成统一判断与今日预案
- **默认执行网络深搜补证**：除非用户明确要求只用本地数据，否则至少补做一轮 `web_search` / `web_fetch`，把公告、行业催化、媒体/研报线索与本地数据交叉验证
- **默认执行真实性审查与深度思考**：对公司/标的至少补问并回答“产品表述是否夸大、进展是否已兑现、财务是否正常、市场为何这样定价、技术前景是否真实、如果我错了最可能错在哪”
- **提出质疑后必须继续举证**：一旦写出“可能夸大/可能不实/可能未兑现”，默认继续搜索反例、第三方来源、同行案例或官方证据，不允许只停在怀疑层面
- **默认研究核心人员背景**：公司深度分析必须尽量补充创始人/董事长/首席科学家/核心技术负责人的背景，并判断其履历是否支撑公司当前叙事
- **默认加入公开尽调**：对公司企业信息类任务，默认搜索并呈现国家企业信用信息公示系统、公司官网、新闻稿/融资稿、招投标/政府项目文件、法院/知识产权公开信息、媒体/投资机构/数据库页面等来源，并按“主体核验→股权/关联线索→合作/融资核验→风险线索→信息置信度”结构输出
- **先做产品分流**：先判断标的是个股/ETF/可转债/其他产品；若为 ETF，必须切换到 `WORKFLOW.md` §10.3 的 ETF 10 项深度清单（发行、标的、走势、资金、情绪、价值锚、流动性、风险与交易计划）

### 阶段 5：质检与发布
1. 保存：`reports/daily/{today}-pre-market.md`
2. 质检：`python3 scripts/reporting/report_quality_check.py reports/daily/{today}-pre-market.md`
3. 质检失败先修正再生成 PDF
4. 对用户可见交付：若属于复杂/深度分析，默认发送 PDF 成品，不只发送长文本

---

## PB-2. 收盘复盘（A股收盘复盘）

**定位**：每交易日收盘后的验证性复盘——不复盘的分析毫无价值。

### 阶段 1：预采集数据读取
- 读取 `reports/daily/{today}-closing-data.txt`（如缺失则应急执行预采集）
- 读取 `data/daily_inputs/{today}/09_cross_asset.txt`
- `python3 scripts/utils/data_consistency_guard.py`

### 阶段 2：记忆回顾（建立日内连续性）
- 读取盘前报告：`reports/daily/{today}-pre-market.md`
- `python3 scripts/memory/memory_manager.py query_signals 1` — 今日信号
- `python3 scripts/memory/memory_manager.py status`

目标：对照盘前预判，明确哪些被验证、哪些被证伪。

### 阶段 3：数据补全（13 项）
| # | 命令 | 用途 |
|---|------|------|
| 1 | `python3 scripts/data/deep_data.py snapshot` | 收盘行情 |
| 2 | `python3 scripts/data/deep_data.py north` | 北向资金 |
| 3 | `python3 scripts/analysis/sentiment.py all` | 收盘情绪 |
| 4 | `python3 scripts/analysis/market_breadth.py all` | 涨跌结构 |
| 5 | `python3 scripts/analysis/cross_day_compare.py all` | 跨日对比 |
| 6 | `python3 scripts/analysis/tech_analysis.py watchlist` | 技术面收盘 |
| 7 | `python3 scripts/analysis/kline_detailed_review.py all` | 详细K线收盘复盘（指数+观察池，含变化原因） |
| 8 | `python3 scripts/analysis/main_force.py batch` | 主力净流入 |
| 9 | `python3 scripts/data/institution_tracker.py all` | 机构动向 |
| 10 | `python3 scripts/data/rss_aggregator.py categorized` | 盘后新闻 |
| 11 | `python3 scripts/data/macro_monitor.py all` | 宏观数据 |
| 12 | `python3 scripts/data/market_intel_pipeline.py postmarket` | 跨资产快照（含外盘/商品/汇率/利率） |
| 13 | `web_search` + `web_fetch`（关键词：VIX/MOVE/credit spread/redemption/bank liquidity） | 世界风险事件与机构运行状况补证 |

### 阶段 4：九步复盘（WORKFLOW.md §7）
1. 今日市场全景概述
2. 与盘前预判对照（验证/证伪清单）
3. 盘中异动事件回顾与归因
4. 资金流与情绪面分析
5. 详细K线技术复盘（主要指数 + 观察池；MA5/10/20/60、MACD、RSI、KDJ、布林带、量价、关键位，以及变化原因）
6. 世界风险评估（风险指数、机构运行、风险事件、风险温度分、传导链）
7. 主导矛盾识别与裁决
8. 明日预判与关注点
9. 认知更新（今天学到了什么）

若复盘对象包含 ETF，额外强制复核：份额申赎、折溢价、跟踪偏离、同类相对强弱、消息—资金—价格一致性（按 `WORKFLOW.md` §10.3）。

必须采用 **结论→证据→推理→风险→行动** 结构。

### 阶段 5：记忆归档（强制）
- `python3 scripts/memory/memory_manager.py save_reports daily {today}-closing`
- `python3 scripts/memory/memory_manager.py save_sentiment {today}`
- 若有交易信号：`python3 scripts/memory/memory_manager.py save_signals <type> <code> <direction> <reason>`
- `python3 scripts/memory/memory_manager.py update_state`
- 复盘日志追加到 `memory/reviews/{today}-closing.md`

### 阶段 6：质检与发布
1. 撰写/成稿默认委派 Claude
2. 保存：`reports/daily/{today}-closing.md`
3. 质检：`python3 scripts/reporting/report_quality_check.py reports/daily/{today}-closing.md`
4. 收盘报告必须包含固定章节：`世界风险评估`（含风险温度分、机构运行状况、对A股传导链）
5. 质检失败先修正再生成 PDF
6. 对用户可见交付：若属于复杂/深度分析，默认发送 PDF 成品，不只发送长文本

---

## PB-3. 盘中异动检测

**定位**：快速检测+分级响应，不写长报告。

### 阶段 1：异动检测
- `python3 scripts/analysis/intraday_alert.py check`
- `python3 scripts/analysis/intraday_alert.py snapshot`
- `python3 scripts/utils/data_consistency_guard.py`

### 阶段 2：分级响应
- **alert_score == 0**：一句话快报"无显著异动"，附当前指数简况
- **alert_score ≥ 1**：对触发标的执行：
  - `python3 scripts/analysis/tech_analysis.py <code>`
  - `python3 scripts/analysis/main_force.py <code>`
  - 聚焦回答：①什么数据触发 ②有无新闻支撑 ③数据与消息是否一致 ④持续性与风险

盘中保持简洁，不展开成完整报告。

---

## PB-4. 周报（砚·周度市场洞察）

**定位**：工作区最高级别的例行研究成品。输出结构严格遵循 `WORKFLOW.md` §6。

### 阶段 0：文档加载
必须先读取并内化以下文档：
- `WORKFLOW.md`（§6 周报硬规范 + §6.2 的 11 节骨架 + §8 分析框架权重 + §9 报告模板）
- `PHILOSOPHY.md` — 投资哲学与认知框架
- `DATA_SOURCES.md` — 数据源优先级与脚本速查
- `MEMORY_SYSTEM.md` — 记忆系统使用规范

### 阶段 1：记忆回顾（建立跨周叙事弧）
#### 1.1 本周
- `python3 scripts/memory/memory_manager.py query_signals 7`
- `python3 scripts/memory/memory_manager.py query_reviews 5`
- `python3 scripts/memory/memory_manager.py query_reports 5`
- `python3 scripts/memory/memory_manager.py status`

#### 1.2 跨周趋势（回看 4 周）
- `python3 scripts/memory/memory_manager.py query_signals 28`
- `python3 scripts/memory/memory_manager.py query_reports 20`

提炼：
1. 本周 vs 上周判断的验证/证伪清单
2. 过去 4 周主裁决的连续性和漂移方向
3. 哪些信号在重复出现（趋势），哪些是一次性噪音

### 阶段 2：预采集数据摄入
- 读取 `reports/weekly/{today}-weekly-data.txt`（19 个脚本完整输出）
- 如缺失，先运行 `python3 scripts/data/market_intel_pipeline.py weekly` 并补跑预采集
- 读取 `data/weekly_inputs/{today}/15_cross_asset.txt`
- `python3 scripts/utils/data_consistency_guard.py`

#### 数据→章节映射
| 数据源 | 服务于章节 | 核心问题 |
|--------|----------|---------|
| deep_data snapshot/industry/concept/north/hot | §2 全球市场 + §4 A股结构 | 指数/行业/概念/资金的周度变化方向？ |
| market_breadth all | §4 A股结构 | 广度扩散还是收缩？涨停跌停趋势？ |
| sentiment all 30 | §4 A股 + §9 多空对抗 | 情绪分位？拐点信号？ |
| macro_deep all + macro_monitor all | §3 宏观环境 | 宏观顺风还是逆风？PMI/CPI/利率？ |
| market_intel_pipeline weekly | §2 全球市场 + §3 宏观 | 全球风险资产联动？ |
| event_calendar_builder | §3 宏观 + §11 下周展望 | 下周关键事件？ |
| market_regime_detector | §4 A股结构 | 当前市场体制？和上周一致？ |
| watchlist_context_builder / watchlist_deep_dive | §8 自选股跟踪 | 自选股本周怎样？逻辑被验证还是证伪？下一步怎么做？ |
| research_reports strategy/industry | §5 行业深度 + §6 新闻市场关系 | 机构怎么看？评级变化？ |
| rss_aggregator all | §6 新闻市场关系 | 本周最重要 3-5 条新闻？ |
| institution_tracker all | §4 A股 + §5 行业深度 | 机构资金涌入/撤离方向？ |
| market_intel_pipeline weekly + web_search/web_fetch | §10 世界系统性风险评估 | 风险指数/机构运行/风险事件是否升温？ |

### 阶段 3：实时数据补全（MCP 工具）
- `mcporter call yfinance.get_quote symbol=^GSPC` — 标普500
- `mcporter call yfinance.get_quote symbol=^IXIC` — 纳指
- `mcporter call yfinance.get_quote symbol=^HSI` — 恒生指数
- `mcporter call yfinance.get_quote symbol=GC=F` — 黄金
- `mcporter call yfinance.get_quote symbol=CL=F` — 原油
- `mcporter call yfinance.get_quote symbol=DX-Y.NYB` — 美元指数
- `mcporter call fmp.enable_toolset name=quotes` + 按需查询

MCP 调用失败时标注"外盘数据来源为预采集快照，非实时"。

### 阶段 4：多 agent 协作（3 轮，全留痕）

#### 第一轮：分模块独立研究（5 个模块）
每个模块产出：核心事实 → 论证逻辑 → 初步结论 → 置信度 → 主要风险

1. **全球宏观与跨资产** — 数据：market_intel_pipeline、macro_deep/monitor、yfinance → 全球风险资产共振/背离，对 A 股传导路径
2. **A股市场结构与资金** — 数据：deep_data、market_breadth、sentiment、institution_tracker、market_regime_detector → 市场体制、广度、分化、资金主线
3. **行业赛道与消息面** — 数据：research_reports、rss_aggregator、deep_data industry/concept/hot → 至少 3 条赛道"新闻→预期→资金→价格"链条
4. **自选股/候选研究池** — 数据：watchlist_context_builder、watchlist_deep_dive、tech_analysis、main_force、stock_profile、stock_valuation_history → 验证/证伪 + 相对强弱 + 交易计划 + 新候选方向
5. **风险管理与反方视角** — 综合全部数据找反例 → 如果主流判断错了，最可能因为什么

#### 第二轮：交叉质询
每个模块至少提出 1 个挑战，逐条回应：
- 结论有充分数据支撑，还是只靠一条新闻？
- 是否忽略反例或历史不一致？
- 数据冲突处是否做了信任源选择？
- 是一次性扰动还是中期变量？
- 是否存在幸存者偏差或确认偏误？

#### 第三轮：主编裁决
- 列出各模块分歧点
- 写明最终采用/放弃哪条论证链路
- 给出主裁决 + 推翻条件 + "如果我错了"

### 阶段 5：写作
严格按 `WORKFLOW.md` §6.2 的 11 节骨架逐节写作。重点要求：
- **全球市场**：每个市场 2-3 句分析 + 传导链条 + 共振/背离/噪音判断
- **A股结构**：广度扩散 vs 收缩、风格轮动、错位现象
- **K线技术面周度复盘**：至少覆盖主要指数 + 观察池逐只的 MA5/10/20/60、MACD、RSI、KDJ、布林带、量价与关键支撑/压力，并解释这周为什么这样变化；排版上优先用“小表格 + 一句话结论 + 变化原因”，不要把技术指标挤成连续大段
- **行业深度**：至少 3 条赛道（发生什么→资金→催化→定价持续性→历史类比→多空裁决）
- **历史对照**：定量（4-12 周关键指标变化幅度），引用记忆系统历史情景
- **世界风险评估**：必须输出风险指数汇总 + 机构运行状况 + 风险事件跟踪 + 风险温度分（0-100）+ 对A股传导链
- **多空对抗**：五角色论证（🐂多头 / 🐻空头 / 📊量化 / 💰基本面 / ⚖️仲裁），仲裁必须引用记忆
- **ETF 专项覆盖**：若观察池含 ETF，逐只按 `WORKFLOW.md` §10.3 的 10 项框架写明（至少覆盖发行/标的方法学/资金与情绪/价值锚/风险与计划）

### 阶段 6：质检与归档
1. 保存：`reports/weekly/{today}-market-insight.md`
2. 质检：`python3 scripts/reporting/report_quality_check.py reports/weekly/{today}-market-insight.md`
3. **自检清单**（全部 ✓ 才可过）：
   - [ ] ≥12000 字符 Markdown 正文
   - [ ] §6.2 的 11 节结构完整无遗漏
   - [ ] 每节有具体数据支撑（非空话）
   - [ ] 跨资产传导链条已写出
   - [ ] 世界系统性风险评估完整（风险指数/机构运行/风险事件/风险温度分/传导链）
   - [ ] 至少 3 条赛道深度分析
   - [ ] 多 agent 讨论纪要完整留痕
   - [ ] 历史对照有定量数据
   - [ ] 有主裁决 + 推翻条件 + "如果我错了"
   - [ ] 观察池处理正确（非空逐只分析 / 为空明确写出）
   - [ ] 观察池逐只写出“验证/证伪/交易计划/失效条件”
   - [ ] 若观察池含 ETF，ETF 10 项深度清单已逐只覆盖
   - [ ] 若观察池含 ETF，已出现“折溢价 / 份额或申赎 / 指数方法学或跟踪标的 / 成分或前十大”这些硬关键词
4. 质检失败必须修正至通过，再生成 PDF
5. 归档：
   - `python3 scripts/memory/memory_manager.py save_reports weekly {today}-market-insight`
   - 若观察池非空，逐只写入 `memory/stocks/<code>.json` 的 `report_refs`

---

## PB-5. 哲学周更新（砚·哲学周更新）

**定位**：每周投资框架的验证与迭代。

### 阶段 1：数据读取
读取 `memory/knowledge/{today}-philosophy-data.txt`（如缺失则应急执行预采集）

### 阶段 2：记忆回顾
- `python3 scripts/memory/memory_manager.py query_reviews 7` — 本周复盘
- `python3 scripts/memory/memory_manager.py query_signals 7` — 本周信号

### 阶段 3：框架更新分析
- 本周市场数据是否支持/挑战现有框架？
- 有无新的认知点值得写入框架？
- 已有的哲学条目哪些被验证，哪些被质疑？
- 踩坑日志有无新内容需要记录？

### 阶段 4：输出
1. 总结本周市场对框架的验证状况
2. 列出值得更新的具体条目
3. 直接修改 `PHILOSOPHY.md`
4. 更新 `PHILOSOPHY.md` 头部的最后更新日期

### 阶段 5：归档
- `python3 scripts/memory/memory_manager.py save_reports philosophy {today}-philosophy`

### 阶段 6：发送摘要
用 message tool 发送到 telegram:-1003521046656：本周框架验证结果 + 是否有更新 + 一句话下周投资情绪

---

## PB-6. 股票遴选（砚·多维选股）

**定位**：全市场扫描 + 五维评分 + 深度验证 + Claude 辩论，输出短/中/长线候选名单。
**建议频率**：每周 1-2 次（周二/周四收盘后），或有重大板块轮动时手动触发。

> **⚠️ 强制规则：用户要求"选股"/"完整选股"/"跑选股"时，必须执行 `python3 scripts/orchestrator/dispatcher.py scout` 并等待其完成。禁止绕过 dispatcher 手工拼凑报告。**
> - dispatcher 会自动编排 collect → normalize → discuss → write → review → revise → deliver 全链路
> - 每个阶段完成后通过 Telegram 发送进度通知（如 "discuss done 3/7"），这是进度提示不是完成信号
> - 必须等 dispatcher 退出（status=completed 或 failed）后才可发送最终报告
> - 如果 dispatcher 某个阶段失败，在 TG 报告中明确标注哪个阶段失败、影响了什么
> - 预计耗时 15-20 分钟，不要试图 3 分钟出结果

### 阶段 1：数据采集

执行 `python3 scripts/orchestrator/dispatcher.py scout`

自动运行 `manifests/scout.json` 定义的 10 项数据脚本：

| # | 脚本 | 用途 | 必需 |
|---|------|------|------|
| 1 | `market_cache.py save` | 全市场快照（行情+行业资金流+北向） | 是 |
| 2 | `deep_data.py industry` | 行业板块实时数据 | 否 |
| 3 | `deep_data.py concept` | 概念板块实时数据 | 否 |
| 4 | `deep_data.py hot` | 热门个股 | 否 |
| 5 | `rss_aggregator.py categorized` | 新闻催化剂分类 | 否 |
| 6 | `sentiment.py all` | 情绪面多维分析 | 否 |
| 7 | `research_reports.py strategy` | 券商策略观点 | 否 |
| 8 | `market_breadth.py all` | 涨跌广度+涨停梯队 | 否 |
| 9 | `peg_screener.py screen` | PEG 价值预筛 | 否 |
| 10 | `stock_scout.py scan` | 多维遴选引擎（核心） | 是 |

### 阶段 2：stock_scout.py 三阶段漏斗

**Stage 1 — 质量门槛**（~5000 → ~2500）
- 排除：ST、北交所、停牌、成交额 < 3000 万、PE 异常

**Stage 2 — 快速五维评分**（~2500 → TOP 100）
- 基于缓存数据的快速评分（技术/资金/基本面/催化/动量）
- 三种时间维度权重加权，取最高分排名

**Stage 3 — 深度验证**（TOP 100 → TOP 30）
- 调用 `tech_analysis.py` 获取完整技术指标
- 调用 `main_force.py` 获取资金面数据（主力连续性、大单）
- 调用 `growth_hunter.py` 获取财务向量（FFScore、Beneish 红旗、成长阶段）
- 五维深度评分 + 时间维度分类

### 阶段 3：五角色辩论

使用 `prompts/discuss_scout.md` 定义的角色框架：

| 角色 | 关注维度 | 核心问题 |
|------|---------|---------|
| 动量猎手 | 技术+动量 | 有没有 1-5 天的确定性买点？ |
| 资金追踪者 | 资金面 | 聪明钱在进还是在出？ |
| 价值研究员 | 基本面 | 3-12 个月值不值？安全边际？ |
| 消息分析师 | 消息+情绪 | 有没有硬催化？时效？ |
| 风控官 | 全维度 | 最大回撤？仓位建议？ |

辩论三轮：独立分析 → 交叉质疑 → 裁决

### 阶段 4：撰写遴选报告

输出 `reports/scout/{date}-scout.md`，包含：
1. 市场环境判断（广度、情绪、适合哪种时间维度操作）
2. **短线候选**（1-5 天）— 入场价/止损/目标/仓位/催化/确信度
3. **中线候选**（1-4 周）— 同上
4. **长线候选**（1-12 月）— 同上
5. 被拒绝候选 + 拒绝原因
6. 核心分歧记录
7. 下次复核日期

### 阶段 5：质检 + 交付

- `review` 阶段检查：每只入选候选是否有 ≥ 3 个维度评分 ≥ 60
- `deliver` 阶段：Markdown → PDF + Telegram 推送 + 记忆归档

### 入选硬门槛

- 至少 3 个维度评分 ≥ 60
- 大盘广度 < 40% 时，短线候选不超过 2 只
- 每只必须有止损位和目标位
- conviction = 低的不入选
- FFScore < 4 且 Beneish 红旗 ≥ 2 的一票否决

### 五维评分权重

| 维度 | 短线 | 中线 | 长线 |
|------|------|------|------|
| 技术面 | 30% | 20% | 10% |
| 资金面 | 30% | 25% | 10% |
| 基本面 | 5% | 20% | 40% |
| 消息催化 | 15% | 15% | 15% |
| 情绪动量 | 20% | 20% | 25% |
