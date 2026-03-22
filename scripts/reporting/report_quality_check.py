#!/usr/bin/env python3
"""报告发布前质检器"""
import re
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.common import load_watchlist  # noqa: E402

REQUIRED_COMMON = [
    '数据覆盖说明', '风险提示', '数据缺口'
]

WEEKLY_REQUIRED = {
    '全球市场概览': ['全球市场概览', '全球市场'],
    '跨资产联动': ['跨资产', '债券', '加密'],
    '宏观环境与政策': ['宏观环境与政策', '宏观'],
    'A股市场结构': ['A股市场结构'],
    '主导矛盾': ['主导矛盾'],
    '历史对照': ['历史对照'],
    '行业赛道深度': ['行业赛道深度', '行业 / 赛道', '行业/赛道'],
    '新闻与市场关系': ['新闻与市场关系', '新闻与市场'],
    '自选股周度跟踪': ['自选股', '观察池'],
    '世界风险评估': ['世界系统性风险评估', '世界风险评估', '风险温度分'],
    '多空对抗': ['多空对抗'],
    '多agent讨论': ['多agent', '多 agent', '论战'],
    '下周展望': ['下周展望']
}

WEEKLY_CROSS_ASSET_DETAIL_CHECKS = {
    '美股指数': [['标普500', '标普 500', 'S&P 500'], ['纳斯达克', '纳指', 'NASDAQ'], ['道琼斯', '道指', 'Dow']],
    '商品': [['原油', 'WTI'], ['黄金', 'Gold'], ['铜', 'Copper']],
    '外汇': [['美元指数', 'DXY', 'DX.F'], ['USD/CNH', 'USDCNH', 'USDCNY', '离岸人民币']],
    '利率': [['美国2Y', '美债2Y', '2年美债', '2Y'], ['美国10Y', '美债10Y', '10年美债', '10Y']],
    '加密': [['BTC', '比特币', 'Bitcoin'], ['ETH', '以太坊', 'Ethereum']],
}

WEEKLY_WORLD_RISK_DETAIL_CHECKS = {
    '风险指数': [['VIX', '恐慌指数'], ['MOVE', '美债波动'], ['信用利差', 'HY', 'IG'], ['风险温度分', '风险温度']],
    '机构运行状况': [['银行', '大行'], ['券商', '投行', 'broker'], ['资管', '基金', '赎回', '流动性']],
    '传导链': [['传导链', '风险传导', '海外风险源'], ['A股', '仓位', '风格']],
}

DAILY_REQUIRED_GROUPS = {
    '消息与数据关联': ['新闻', '消息面', '催化', '事件'],
    '矛盾或统一性': ['主导矛盾', '矛盾', '背离', '一致性', '统一性'],
}

CLOSING_REQUIRED_GROUPS = {
    '世界风险评估': ['世界风险评估', '世界风险雷达', '世界系统性风险评估'],
    '风险温度分': ['风险温度分', '风险温度'],
    '机构运行状况': ['机构运行状况', '银行', '券商', '资管', '赎回'],
}

FORBIDDEN_PATTERNS = [r'\{[^{}]+\}', r'\{\{[^{}]+\}\}']
MIN_WEEKLY_CHARS = 12000
MIN_WEEKLY_H2 = 8
WATCHLIST_REQUIRED_KEYWORDS = ['验证', '证伪', '交易计划', '失效条件']
ETF_REQUIRED_KEYWORD_GROUPS = [
    ['折溢价'],
    ['份额', '申赎'],
    ['指数方法学', '标的方法学', '跟踪标的'],
    ['成分', '前十大'],
]
KLINE_REQUIRED_GROUPS = [
    ['K线', '技术面'],
    ['MA5', 'MA10', 'MA20'],
    ['MA60', '均线'],
    ['MACD'],
    ['RSI'],
    ['KDJ'],
    ['布林', '布林带'],
    ['量价', '成交量'],
    ['支撑', '压力'],
    ['变化原因', '为什么变', '原因解释'],
]


def _strip_fenced_code(text: str) -> str:
    return re.sub(r'```[\s\S]*?```', '', text)


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
    missing_items = []
    for item in meta['items']:
        if item['code'] and item['code'] in text:
            continue
        if item['name'] and item['name'] in text:
            continue
        missing_items.append(item['code'] or item['name'])
    if missing_items:
        issues.append(f'报告未逐只覆盖当前观察池：缺少 {missing_items}')
    for kw in WATCHLIST_REQUIRED_KEYWORDS:
        if kw not in text:
            issues.append(f'观察池分析过浅：缺少关键词“{kw}”')
    if meta['has_etf']:
        for group in ETF_REQUIRED_KEYWORD_GROUPS:
            if not any(k in text for k in group):
                issues.append(f'观察池含 ETF，但 ETF 专项覆盖不足：缺少任一关键词 {group}')


def _check_kline_depth(text: str, issues: list[str], label: str):
    for group in KLINE_REQUIRED_GROUPS:
        if not any(k in text for k in group):
            issues.append(f'{label}缺少详细K线技术复盘：缺少任一关键词 {group}')


def check(path: Path):
    text = path.read_text(encoding='utf-8')
    issues = []
    for sec in REQUIRED_COMMON:
        if sec not in text:
            issues.append(f'缺少必需章节/关键词：{sec}')

    lower_path = str(path).lower()
    is_weekly = 'weekly' in lower_path or 'market-insight' in path.name
    is_daily = ('daily' in lower_path) or ('pre-market' in path.name) or ('closing' in path.name)
    if is_weekly:
        for label, keywords in WEEKLY_REQUIRED.items():
            if not any(k in text for k in keywords):
                issues.append(f'周报缺少章节/关键词：{label} -> {keywords}')
        for label, groups in WEEKLY_CROSS_ASSET_DETAIL_CHECKS.items():
            for group in groups:
                if not any(keyword in text for keyword in group):
                    issues.append(f'周报跨资产覆盖不足：{label} 缺少任一关键词 {group}')
        for label, groups in WEEKLY_WORLD_RISK_DETAIL_CHECKS.items():
            for group in groups:
                if not any(keyword in text for keyword in group):
                    issues.append(f'周报世界风险评估覆盖不足：{label} 缺少任一关键词 {group}')
        if len(text) < MIN_WEEKLY_CHARS:
            issues.append(f'周报正文过短：当前 {len(text)} 字符，低于最低要求 {MIN_WEEKLY_CHARS}')
        h2_count = len(re.findall(r'^##\s+', text, flags=re.M))
        if h2_count < MIN_WEEKLY_H2:
            issues.append(f'周报二级标题过少：当前 {h2_count}，低于最低要求 {MIN_WEEKLY_H2}')
        _check_kline_depth(text, issues, '周报')
    elif is_daily:
        for label, keywords in DAILY_REQUIRED_GROUPS.items():
            if not any(k in text for k in keywords):
                issues.append(f'日报/复盘缺少关键分析维度：{label} -> {keywords}')
        is_closing = 'closing' in path.name.lower() or 'closing' in lower_path
        if is_closing:
            for label, keywords in CLOSING_REQUIRED_GROUPS.items():
                if not any(k in text for k in keywords):
                    issues.append(f'收盘复盘缺少世界风险固定板块：{label} -> {keywords}')
            _check_kline_depth(text, issues, '收盘复盘')

    if is_weekly or is_daily:
        _check_watchlist_depth(text, issues)

    template_text = _strip_fenced_code(text)
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, template_text):
            issues.append(f'存在未替换模板变量：{pat}')
    if '如果我错了' not in text:
        issues.append('缺少“如果我错了”模块')
    if '推翻条件' not in text:
        issues.append('缺少“推翻条件”')
    if '主导矛盾' not in text:
        issues.append('缺少“主导矛盾”')
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
