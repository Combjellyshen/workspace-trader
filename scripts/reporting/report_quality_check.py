#!/usr/bin/env python3
"""报告发布前质检器

设计原则：检查报告是否覆盖了关键分析维度，而非纠字眼。
每个检查项都接受多个同义词/变体表达，只要命中一个即通过。
"""
import re
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.common import load_watchlist  # noqa: E402

# ---------------------------------------------------------------------------
# 通用必需维度（所有报告类型）
# ---------------------------------------------------------------------------

REQUIRED_COMMON = {
    '数据覆盖': ['数据覆盖', '数据来源', '数据源', '覆盖说明', '覆盖率'],
    '风险提示': ['风险提示', '风险声明', '风险警示', '风险因素'],
    '数据缺口': ['数据缺口', '数据缺失', '不可用', '暂缺', '无法获取'],
}

# ---------------------------------------------------------------------------
# 分析深度必需维度（所有报告类型）
# ---------------------------------------------------------------------------

DEPTH_REQUIRED = {
    '反思模块': ['如果我错了', '如果判断错误', '错误可能', '最可能错在'],
    '推翻条件': ['推翻条件', '失效条件', '翻转条件', '否定条件', '止损条件'],
    '主导矛盾': ['主导矛盾', '核心矛盾', '主要矛盾', '关键分歧'],
}

# ---------------------------------------------------------------------------
# 周报必需章节
# ---------------------------------------------------------------------------

WEEKLY_REQUIRED = {
    '全球市场': ['全球市场', '全球概览', '海外市场', '外盘'],
    '跨资产': ['跨资产', '债券', '加密', '商品', '汇率'],
    '宏观': ['宏观', '政策', '央行', 'PMI', 'CPI'],
    'A股结构': ['A股', '市场结构', '涨跌', '广度'],
    '行业赛道': ['行业', '赛道', '板块', '轮动'],
    '新闻': ['新闻', '消息面', '催化', '事件驱动'],
    '观察池': ['自选股', '观察池', '持仓', 'watchlist'],
    '风险评估': ['风险评估', '风险温度', 'VIX', '系统性风险', '风险指数'],
    '多角色': ['多空', '论证', '论战', '辩论', 'agent', '角色', '裁决'],
    '下周展望': ['下周', '展望', '预判', '预案'],
}

WEEKLY_CROSS_ASSET = {
    '美股': ['标普', 'S&P', '纳斯达克', '纳指', 'NASDAQ', '道琼斯', '道指', 'Dow', '美股'],
    '商品': ['原油', 'WTI', '黄金', 'Gold', '铜', 'Copper', '大宗'],
    '外汇': ['美元指数', 'DXY', 'USD/CNH', 'USDCNH', '离岸人民币', '汇率'],
    '利率': ['美债', '国债', '利率', '收益率', '2Y', '10Y', '期限利差'],
    '加密': ['BTC', '比特币', 'ETH', '以太坊', '加密货币', 'crypto'],
}

WEEKLY_RISK = {
    '风险指标': ['VIX', 'MOVE', '恐慌指数', '波动率', '信用利差', 'HY', 'IG', '风险温度'],
    '机构状况': ['银行', '券商', '资管', '基金', '赎回', '流动性', '机构'],
    '传导': ['传导', '风险源', '外溢', '冲击', '影响路径'],
}

MIN_WEEKLY_CHARS = 12000
MIN_WEEKLY_H2 = 8

# ---------------------------------------------------------------------------
# 日报/复盘必需维度
# ---------------------------------------------------------------------------

DAILY_REQUIRED = {
    '消息面': ['新闻', '消息面', '催化', '事件', '公告', '政策'],
    '矛盾判断': ['矛盾', '背离', '一致性', '分歧', '统一', '冲突'],
}

CLOSING_REQUIRED = {
    '风险评估': ['风险评估', '风险温度', '世界风险', '系统性风险', 'VIX'],
    '机构状况': ['机构', '银行', '券商', '资管', '赎回', '流动性'],
}

# ---------------------------------------------------------------------------
# K线技术面检查（宽松版）
# 只要覆盖 >= 5/10 个技术指标维度即通过
# ---------------------------------------------------------------------------

KLINE_INDICATORS = [
    ['K线', '技术面', '技术分析'],
    ['MA5', 'MA10', 'MA20', '均线', '移动平均'],
    ['MA60', '60日', '长期均线'],
    ['MACD', '快慢线', 'DIF', 'DEA'],
    ['RSI', '相对强弱'],
    ['KDJ', '随机指标'],
    ['布林', 'Bollinger', 'BOLL'],
    ['量价', '成交量', '换手', '放量', '缩量'],
    ['支撑', '压力', '阻力'],
    ['原因', '为什么', '因为', '导致', '驱动', '逻辑'],
]
KLINE_MIN_COVERAGE = 5  # 至少覆盖 5/10 个维度

# ---------------------------------------------------------------------------
# 观察池与 ETF
# ---------------------------------------------------------------------------

WATCHLIST_KEYWORDS = ['验证', '证伪', '交易计划', '失效条件', '计划', '止损']
WATCHLIST_MIN_KEYWORDS = 2  # 至少命中 2/6 个

ETF_KEYWORD_GROUPS = [
    ['折溢价', '溢价', '折价', 'premium', 'discount'],
    ['份额', '申赎', '申购', '赎回', '规模变化'],
    ['方法学', '跟踪标的', '跟踪指数', '标的指数', '编制'],
    ['成分', '前十大', '持仓', '权重股', '重仓'],
]
ETF_MIN_GROUPS = 2  # 至少覆盖 2/4 个维度

# ---------------------------------------------------------------------------
# 禁止项
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS = [r'\{\{[^{}]+\}\}']  # 只检查双花括号模板变量

# ---------------------------------------------------------------------------
# 检查逻辑
# ---------------------------------------------------------------------------


def _strip_fenced_code(text: str) -> str:
    return re.sub(r'```[\s\S]*?```', '', text)


def _any_match(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in text (case-insensitive for ASCII)."""
    for kw in keywords:
        if kw in text or kw.lower() in text.lower():
            return True
    return False


def _current_watchlist_meta():
    watchlist = load_watchlist()
    items = []
    has_etf = False
    for raw in watchlist:
        item = raw if isinstance(raw, dict) else {'code': str(raw), 'name': ''}
        code = str(item.get('code', ''))
        name = str(item.get('name', ''))
        ptype = str(item.get('type', '')).lower()
        is_etf = ptype == 'etf' or code.startswith(('5', '15', '16', '56', '58')) or 'etf' in name.lower()
        has_etf = has_etf or is_etf
        items.append({'code': code, 'name': name, 'is_etf': is_etf})
    return {'items': items, 'has_watchlist': bool(items), 'has_etf': has_etf}


def _check_watchlist_depth(text: str, issues: list[str]):
    meta = _current_watchlist_meta()
    if not meta['has_watchlist']:
        return

    # Check stock coverage
    missing_items = []
    for item in meta['items']:
        if item['code'] and item['code'] in text:
            continue
        if item['name'] and item['name'] in text:
            continue
        missing_items.append(item['code'] or item['name'])
    if missing_items:
        issues.append(f'报告未逐只覆盖当前观察池：缺少 {missing_items}')

    # Flexible keyword check
    hits = sum(1 for kw in WATCHLIST_KEYWORDS if kw in text)
    if hits < WATCHLIST_MIN_KEYWORDS:
        issues.append(f'观察池分析深度不足：仅命中 {hits}/{len(WATCHLIST_KEYWORDS)} 个分析维度关键词')

    # ETF check (flexible)
    if meta['has_etf']:
        groups_hit = sum(1 for group in ETF_KEYWORD_GROUPS if _any_match(text, group))
        if groups_hit < ETF_MIN_GROUPS:
            issues.append(f'观察池含 ETF，但 ETF 分析维度不足：仅覆盖 {groups_hit}/{len(ETF_KEYWORD_GROUPS)} 个维度')


def _check_kline_depth(text: str, issues: list[str], label: str):
    hits = sum(1 for group in KLINE_INDICATORS if _any_match(text, group))
    if hits < KLINE_MIN_COVERAGE:
        issues.append(f'{label}K线技术面覆盖不足：仅覆盖 {hits}/{len(KLINE_INDICATORS)} 个指标维度（要求≥{KLINE_MIN_COVERAGE}）')


def check(path: Path):
    text = path.read_text(encoding='utf-8')
    issues = []

    # 通用维度
    for label, keywords in REQUIRED_COMMON.items():
        if not _any_match(text, keywords):
            issues.append(f'缺少必需维度：{label}')

    # 分析深度
    for label, keywords in DEPTH_REQUIRED.items():
        if not _any_match(text, keywords):
            issues.append(f'缺少分析深度维度：{label}')

    lower_path = str(path).lower()
    is_weekly = 'weekly' in lower_path or 'market-insight' in path.name
    is_daily = ('daily' in lower_path) or ('pre-market' in path.name) or ('closing' in path.name)

    if is_weekly:
        # 章节覆盖
        for label, keywords in WEEKLY_REQUIRED.items():
            if not _any_match(text, keywords):
                issues.append(f'周报缺少章节维度：{label}')

        # 跨资产覆盖（按大类检查，不再逐个子项）
        for label, keywords in WEEKLY_CROSS_ASSET.items():
            if not _any_match(text, keywords):
                issues.append(f'周报跨资产缺少：{label}')

        # 风险评估覆盖
        for label, keywords in WEEKLY_RISK.items():
            if not _any_match(text, keywords):
                issues.append(f'周报风险评估缺少：{label}')

        # 篇幅
        if len(text) < MIN_WEEKLY_CHARS:
            issues.append(f'周报正文过短：{len(text)} 字符 < {MIN_WEEKLY_CHARS}')
        h2_count = len(re.findall(r'^##\s+', text, flags=re.M))
        if h2_count < MIN_WEEKLY_H2:
            issues.append(f'周报结构不足：{h2_count} 个 H2 < {MIN_WEEKLY_H2}')

        _check_kline_depth(text, issues, '周报')

    elif is_daily:
        for label, keywords in DAILY_REQUIRED.items():
            if not _any_match(text, keywords):
                issues.append(f'日报缺少维度：{label}')

        is_closing = 'closing' in path.name.lower() or 'closing' in lower_path
        if is_closing:
            for label, keywords in CLOSING_REQUIRED.items():
                if not _any_match(text, keywords):
                    issues.append(f'收盘复盘缺少：{label}')
            _check_kline_depth(text, issues, '收盘复盘')

    if is_weekly or is_daily:
        _check_watchlist_depth(text, issues)

    # 禁止项（只检查双花括号模板）
    template_text = _strip_fenced_code(text)
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, template_text):
            issues.append(f'存在未替换模板变量：{pat}')

    return issues


def main():
    if len(sys.argv) < 2:
        print('Usage: report_quality_check.py <file.md>', file=sys.stderr)
        sys.exit(1)
    path = Path(sys.argv[1])
    issues = check(path)
    if issues:
        print('FAILED')
        for i in issues:
            print('-', i)
        sys.exit(2)
    print('PASSED')


if __name__ == '__main__':
    main()
