# 项目结构说明（Phase 7：Agent 文档统一 + 代码去重）

## 本阶段完成的事
- Agent 级 `AGENTS.md`（545→~100行）：只保留 Iron Rules + 身份 + MCP + 输出规范 + 文档索引
- 新建 `MEMORY_SYSTEM.md`：从 Agent AGENTS.md 提取记忆系统完整规范
- `WORKFLOW.md` v4：新增 §7 收盘复盘制度 / §8 分析框架权重 / §9 报告模板+建议分级+免责
- `DATA_SOURCES.md`：新增 §6 脚本命令速查 / §7 频率限制
- `scripts/utils/common.py` 增强：`safe_float` 支持 `units=True`（亿/万/万亿）
- 17 个脚本去重：所有本地 `_get` / `safe_float` / `safe_pct` 改用 `common.py` 导入
- 17 个脚本去硬编码：全部 `/home/bot/.openclaw/workspace-trader` → `WORKSPACE_ROOT` / `load_config()` / `load_watchlist()`
- `watchlist_context_builder.py` 修复：顶层代码包装进 `if __name__ == '__main__':` + `build_context()` 函数
- 所有 `sys.path.insert` 统一为条件守卫模式

## Phase 6 完成的事（保留）
- `WEEKLY_REPORT_SPEC.md` → `WORKFLOW.md` §6
- `SELF_INTRO.md` → `SOUL.md`
- 删除孤立文件：TOOLS.md / IDENTITY.md / HEARTBEAT.md
- P0 bug 修复：institution_tracker / sector_screener
- 建立 `scripts/utils/common.py`
- `requirements.txt` 补 beautifulsoup4

## 顶层文档职责（8 份核心文档，无冗余）
- `AGENTS.md`：最高优先级硬规则
- `SOUL.md`：人格、表达风格、对外自我介绍
- `USER.md`：用户画像与偏好
- `PHILOSOPHY.md`：投资哲学与判断框架
- `WORKFLOW.md`：分析流程（§1-5）、周报规范（§6）、复盘制度（§7）、分析框架（§8）、报告模板（§9）
- `PLAYBOOKS.md`：任务执行手册（PB-1~5：盘前/复盘/异动/周报/哲学更新的完整执行阶段）
- `DATA_SOURCES.md`：数据源 + 脚本命令速查 + 频率限制
- `MEMORY_SYSTEM.md`：记忆系统（目录、信号类型、生命周期、使用方式）

## scripts/ 模块化结构
- `scripts/data/`
- `scripts/analysis/`
- `scripts/reporting/`
- `scripts/memory/`
- `scripts/utils/`
- `scripts/legacy/`

## 当前源码 / 生成物分层
### 源码与规则
- `scripts/`：脚本源码
- `config/`：配置
- `docs/`：结构与说明文档
- 顶层核心文档：规则、人格、流程、数据源、用户画像

### 运行中产生的内容
- `reports/`：日报 / 周报 / 盘中报告
- `data/`：新闻归档、市场缓存、盘中快照等运行产物
- `memory/`：长期记忆、个股档案、知识与状态

## 当前约束
1. 所有新命令统一使用子目录真实路径
2. 不再新增顶层 `scripts/*.py` 平铺入口
3. 新产出统一写入 `reports/` 与 `data/`
4. 历史报告、历史记忆中的旧路径描述保留原样，视为归档记录，不做回写篡改

## 下一阶段建议
- 对定时任务做一轮全链路回归测试（开盘前 / 收盘复盘 / 周报）
- 审视 `memory/` 内部是否需要继续拆出更多"运行态数据"到 `data/`
- 考虑为 common.py 补充单元测试
- 清理 `scripts/legacy/` 中已弃用的旧脚本
