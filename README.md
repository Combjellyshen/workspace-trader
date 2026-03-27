# 砚·交易台 (Yan Trading Terminal)

A 股分析 AI Agent 工作区，基于 [OpenClaw](https://github.com/nicepkg/openclaw) 平台。

## 功能

- **盘前分析** — 自动采集宏观、北向、研报、RSS 等数据，生成盘前研判
- **盘中监控** — 实时跟踪观察池标的技术指标与资金流向
- **收盘复盘** — 多维度收盘总结，含市场情绪、板块轮动、主力动向
- **周报** — 周度深度总结与策略回顾
- **选股筛选** — PEG/板块/多因子筛选，结合投资哲学体系
- **投资哲学** — 持续迭代的四层漏斗投资框架

## 项目结构

```
├── scripts/           # Python 数据管线与分析脚本
│   ├── data/          #   数据采集（Tushare、AkShare、RSS、宏观）
│   ├── analysis/      #   分析（选股、情绪、技术面、市场宽度）
│   ├── reporting/     #   报告生成（Markdown → PDF/HTML）
│   ├── memory/        #   记忆管理与哲学迭代
│   └── utils/         #   公共工具
├── config/            # Agent 配置（模型路由、MCP、调度）
├── skills/            # 技能链接（→ skill-library）
├── docs/              # 项目文档与重构计划
├── PHILOSOPHY.md      # 投资哲学体系
├── PLAYBOOKS.md       # 任务执行手册
├── WORKFLOW.md        # 分析工作流
├── SOUL.md            # Agent 人格与行为
├── IDENTITY.md        # Agent 身份
├── DATA_SOURCES.md    # 数据源优先级
└── MEMORY_SYSTEM.md   # 记忆系统架构
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
npm install
```

### 2. 配置

```bash
# 复制配置模板并填入你的 API Key
cp config.example.json config.json
cp config/claude_dispatch.example.json config/claude_dispatch.json
cp USER.example.md USER.md
```

编辑以下文件：
- `config.json` — 填入 Tushare / Alpha Vantage / FRED API Key
- `config/claude_dispatch.json` — 填入 Telegram Group ID
- `USER.md` — 填入你的个人偏好

### 3. 数据源 API Key 获取

| 数据源 | 用途 | 申请地址 |
|--------|------|---------|
| [Tushare](https://tushare.pro) | A 股行情、财务数据 | tushare.pro 注册 |
| [Alpha Vantage](https://www.alphavantage.co) | 海外市场数据 | alphavantage.co/support |
| [FRED](https://fred.stlouisfed.org) | 美国宏观经济数据 | fred.stlouisfed.org/docs/api |

## 依赖

**Python**: akshare, tushare, pandas, numpy, requests, beautifulsoup4, feedparser, markdown, openpyxl, Pillow

**Node.js**: puppeteer (PDF 生成 / 网页抓取)

## License

MIT
