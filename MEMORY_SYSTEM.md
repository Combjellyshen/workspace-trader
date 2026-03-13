# 砚·记忆系统（MEMORY_SYSTEM.md）

> **职责边界**：本文件定义记忆目录结构、信号捕捉规则、数据生命周期与记忆使用方式。

记忆是砚相比其他分析工具的核心优势——**能记住历史，发现规律**。

## 1. 目录结构

```
memory/
├── reports/              ← 券商研报存档（按月归档）
│   └── YYYY-MM/
├── signals/              ← 每日关键信号
│   └── YYYY-MM-DD.json
├── reviews/              ← 复盘日志
│   └── YYYY-MM-DD.md
├── sentiment/            ← 情绪面历史快照
│   └── YYYY-MM-DD.json
├── knowledge/            ← 长期知识（哲学周更新等）
├── snapshots/            ← 市场快照
├── stocks/               ← 个股档案
│   └── <code>.json
└── state.json            ← 状态追踪（连板/异动/形态）
```

## 2. 必须捕捉的信号类型

每天收盘时，识别并保存关键信号到 `memory/signals/YYYY-MM-DD.json`：

```json
{"type": "signal_type", "level": "critical|important|normal", "timestamp": "ISO时间", "description": "描述", "data": {}}
```

| 类型 | 触发条件 | 级别 |
|------|---------|------|
| `policy` | 央行降息/降准、两会政策、重大监管变化 | critical |
| `northbound_reversal` | 北向资金连续3天方向反转 | critical |
| `sector_rotation` | 行业资金流TOP3连续2天轮换 | important |
| `limit_up_cluster` | 某板块涨停股超5只 | important |
| `limit_down_cluster` | 某板块跌停股超3只 | critical |
| `volume_anomaly` | 两市成交额突破前5日均值30%以上 | important |
| `institutional_move` | 龙虎榜机构单日净买入超3亿 | important |
| `watchlist_alert` | 自选股触及技术关键位 | important |
| `sentiment_extreme` | 热股排行剧变/新题材突然冒出 | normal |
| `geopolitical` | 地缘政治重大事件 | critical |
| `rating_change` | 券商对自选股评级变化 | important |

## 3. 其他归档内容

### 3.1 技术面细节（state.json → pattern_notes）
记录关键技术形态：自选股突破/跌破重要均线、放量突破压力位、MACD金叉/死叉、缩量到地量后首次放量。

### 3.2 情绪面快照（save_sentiment）
每天盘中至少保存1次、收盘保存1次。用于追踪热股概念连续性、新题材启动信号、情绪顶底判断。

### 3.3 研报存档（save_reports）
每天收盘后自动归档当日研报。用于追踪评级变化、发现机构扎堆信号、对比券商预判准确率。

## 4. 数据生命周期

| 时间段 | 处理方式 |
|--------|---------|
| 0-30天 | JSON 原始文件，随时查询 |
| 30天-6个月 | 保持原格式，低频查询 |
| 超过6个月 | 自动压缩为 .gz（`memory_manager.py compress`） |
| 超过1年 | 可考虑只保留关键信号，删除日常快照 |

**压缩时机**：每月1日收盘复盘时执行 `python3 scripts/memory/memory_manager.py compress`

## 5. 记忆使用方式

**每次分析前，必须回顾记忆**：

### 开盘前分析
- `query_signals 7` — 近一周关键信号
- `query_reviews 3` — 近3天复盘的次日展望是否应验
- `status` — 连板股追踪、自选股异动记录
- `query_reports <自选股代码>` — 券商评级变化

### 盘中跟踪
- 对比开盘前预判与实际走势
- 发现新信号立即记录

### 收盘复盘
- 回顾当天所有预判的准确率
- 记录新信号、更新 state.json
- 保存研报和情绪面快照

## 6. 记忆管理命令速查

| 命令 | 用途 |
|------|------|
| `memory_manager.py save_reports` | 归档当日研报 |
| `memory_manager.py save_signals` | 保存关键信号（stdin JSON） |
| `memory_manager.py save_sentiment` | 保存情绪快照 |
| `memory_manager.py compress` | 压缩超6个月数据 |
| `memory_manager.py query_reports <code> [months]` | 查某股历史评级 |
| `memory_manager.py query_signals [days]` | 查近N天信号 |
| `memory_manager.py query_reviews [days]` | 查近N天复盘 |
| `memory_manager.py status` | 查看追踪状态 |
| `memory_manager.py update_state` | 更新追踪状态（stdin） |
| `memory_manager.py disk_usage` | 存储统计 |
| `memory_manager.py list_stocks` | 列出自选/长线观察池 |
