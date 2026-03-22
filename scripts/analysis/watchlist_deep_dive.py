#!/usr/bin/env python3
"""
自选池深度分析器（默认深度模式）

目标：把“行情摘要式自选池”升级为“证据链式跟踪”。

覆盖维度：
- 产品分流（个股 / ETF）
- 行情与相对强弱
- 技术结构
- 资金结构
- 财务质量（个股）
- 估值分位（个股）
- 公告 / 新闻催化
- 行业 / 主题上下文
- 交易计划 / 失效条件 / 下次复核点

用法：
  python3 scripts/analysis/watchlist_deep_dive.py all
  python3 scripts/analysis/watchlist_deep_dive.py summary
  python3 scripts/analysis/watchlist_deep_dive.py code 002475
"""
import json
import math
import re
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import ensure_importable, json_output, load_watchlist, safe_float, safe_pct  # noqa: E402
ensure_importable()

from scripts.analysis import main_force, tech_analysis  # noqa: E402
from scripts.data import deep_data, stock_profile, stock_valuation_history  # noqa: E402

try:
    import akshare as ak  # type: ignore
except Exception:
    ak = None

MEMORY_STOCKS = _HERE / 'memory' / 'stocks'


def _now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y-%m-%d %H:%M:%S')


def _stock_prefix(code: str) -> str:
    return ('SH' if code.startswith('6') or code.startswith('5') else 'SZ') + code


def _load_memory_stock(code: str):
    p = MEMORY_STOCKS / f'{code}.json'
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _pct_change(new, old):
    new = safe_float(new, None)
    old = safe_float(old, None)
    if new in (None, 0) or old in (None, 0):
        return None
    try:
        return round((new / old - 1) * 100, 2)
    except Exception:
        return None


def _safe_round(v, n=2):
    if v is None:
        return None
    try:
        return round(float(v), n)
    except Exception:
        return None


def _kline_metrics(code: str):
    """补充 1/5/20/60 日走势与波动。"""
    try:
        df = tech_analysis._load_kline(code, days=140)  # type: ignore[attr-defined]
        if df is None or len(df) < 10:
            return {'error': 'kline 数据不足'}

        close_col = '收盘' if '收盘' in df.columns else 'close'
        high_col = '最高' if '最高' in df.columns else 'high'
        low_col = '最低' if '最低' in df.columns else 'low'
        amount_col = '成交额' if '成交额' in df.columns else None

        closes = [float(x) for x in df[close_col].tolist()]
        highs = [float(x) for x in df[high_col].tolist()]
        lows = [float(x) for x in df[low_col].tolist()]
        amounts = [float(x) for x in df[amount_col].tolist()] if amount_col else []

        def ret(days):
            if len(closes) <= days:
                return None
            return round((closes[-1] / closes[-days-1] - 1) * 100, 2)

        daily_ret = []
        for i in range(1, min(len(closes), 21)):
            try:
                daily_ret.append((closes[-i] / closes[-i-1] - 1) * 100)
            except Exception:
                pass

        low_20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
        high_20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        current = closes[-1]

        return {
            'close': current,
            'returns': {
                '1d_pct': ret(1),
                '5d_pct': ret(5),
                '20d_pct': ret(20),
                '60d_pct': ret(60),
            },
            'drawdown_from_20d_high_pct': _safe_round((current / high_20 - 1) * 100),
            'distance_to_20d_low_pct': _safe_round((current / low_20 - 1) * 100),
            'volatility_20d_pct': _safe_round(statistics.pstdev(daily_ret), 2) if len(daily_ret) >= 5 else None,
            'avg_amount_5d_yi': _safe_round(sum(amounts[-5:]) / min(len(amounts), 5) / 1e8, 2) if amounts else None,
            'avg_amount_20d_yi': _safe_round(sum(amounts[-20:]) / min(len(amounts), 20) / 1e8, 2) if amounts else None,
        }
    except Exception as e:
        return {'error': str(e)[:160]}


MARKET_CACHE = None


def _load_market_context():
    global MARKET_CACHE
    if MARKET_CACHE is not None:
        return MARKET_CACHE
    try:
        snap = deep_data.full_snapshot()
    except Exception as e:
        snap = {'snapshot_error': str(e)}

    industry_flow = snap.get('industry_flow', []) or []
    concept_flow = snap.get('concept_flow', []) or []
    north_raw = snap.get('northbound', []) or snap.get('north_flow', []) or []
    if isinstance(north_raw, dict):
        north = north_raw.get('data', []) or []
    else:
        north = north_raw or []
    watchlist_rt = {str(x.get('code', ''))[-6:]: x for x in snap.get('watchlist_realtime', []) or []}
    MARKET_CACHE = {
        'snapshot': snap,
        'industry_flow': industry_flow,
        'concept_flow': concept_flow,
        'north': north,
        'watchlist_realtime': watchlist_rt,
        'top_industry_inflow': [x.get('name') for x in industry_flow[:5]],
        'top_concept_inflow': [x.get('name') for x in concept_flow[:5]],
    }
    return MARKET_CACHE


def _match_industry_flow(industry_name: str, market_ctx: dict):
    if not industry_name:
        return None
    candidates = market_ctx.get('industry_flow', []) or []
    for row in candidates:
        name = str(row.get('name', ''))
        if industry_name == name or industry_name in name or name in industry_name:
            avg_pct = safe_float(row.get('avg_pct'), None)
            return {
                'industry': name,
                'net_inflow_yi': _safe_round(safe_float(row.get('net_inflow'), None)),
                'avg_pct': _safe_round(avg_pct * 100 if avg_pct is not None and abs(avg_pct) < 1 else avg_pct, 2),
                'rank_hint': 'top_inflow' if name in (market_ctx.get('top_industry_inflow') or []) else 'non_top'
            }
    return None


def _stock_profile_bundle(code: str):
    return {
        'company': stock_profile.company_profile(code),
        'finance': stock_profile.financial_summary(code),
        'announcements': stock_profile.latest_announcements(code, limit=8),
        'news': stock_profile.company_news(code, limit=6),
        'valuation': stock_valuation_history.get_pe_history(_stock_prefix(code), years=5),
    }


def _load_local_etf_deep_data(code: str):
    p = _HERE / 'data' / f'{code}_deep_data.json'
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        data['_source_file'] = str(p)
        return data
    except Exception:
        return {}


def _extract_latest_nav_fields(row):
    cols = list(getattr(row, 'index', []))
    unit_cols = sorted([c for c in cols if str(c).endswith('-单位净值')], reverse=True)
    acc_cols = sorted([c for c in cols if str(c).endswith('-累计净值')], reverse=True)
    latest_unit_col = unit_cols[0] if unit_cols else None
    prev_unit_col = unit_cols[1] if len(unit_cols) > 1 else None
    latest_acc_col = acc_cols[0] if acc_cols else None
    return {
        'latest_nav_date': latest_unit_col.split('-单位净值')[0] if latest_unit_col else None,
        'prev_nav_date': prev_unit_col.split('-单位净值')[0] if prev_unit_col else None,
        'unit_nav': safe_float(row.get(latest_unit_col), None) if latest_unit_col else None,
        'acc_nav': safe_float(row.get(latest_acc_col), None) if latest_acc_col else None,
        'prev_unit_nav': safe_float(row.get(prev_unit_col), None) if prev_unit_col else None,
    }


def _run_tick_chip_cli(code: str, mode: str):
    cmd = [sys.executable, str(_HERE / 'scripts' / 'analysis' / 'tick_chip.py'), mode, code]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return {'error': proc.stderr.strip()[:200] or f'{mode} cli failed'}
        text = (proc.stdout or '').strip()
        start = text.find('{')
        if start < 0:
            return {'error': f'{mode} cli 无 JSON 输出'}
        return json.loads(text[start:])
    except Exception as e:
        return {'error': str(e)[:200]}


_GENERIC_ETF_WORDS = {'ETF', '联接', '交易型', '开放式', '指数', '增强', '证券投资基金', '基金'}


def _theme_keywords(name: str):
    pieces = re.findall(r'[\u4e00-\u9fff]{2,}', name or '')
    cleaned = []
    for p in pieces:
        token = p
        for bad in _GENERIC_ETF_WORDS:
            token = token.replace(bad, '')
        token = token.strip()
        if len(token) >= 2 and token not in cleaned:
            cleaned.append(token)
    return cleaned[:3]


def _find_same_theme_candidates(fund_name: str, code: str):
    if ak is None or not fund_name:
        return []
    try:
        names = ak.fund_name_em()
        keywords = _theme_keywords(fund_name)
        if not keywords:
            return []
        mask = None
        for kw in keywords:
            current = names['基金简称'].astype(str).str.contains(kw, na=False)
            mask = current if mask is None else (mask | current)
        rows = names[mask] if mask is not None else names.iloc[0:0]
        rows = rows[rows['基金代码'].astype(str) != code].head(5)
        return rows[['基金代码', '基金简称', '基金类型']].to_dict('records')
    except Exception:
        return []


def _etf_component_summary(local_data: dict):
    if not local_data:
        return {}
    holdings = local_data.get('holdings') or []
    components = local_data.get('component_snapshot') or []
    return {
        'top10_weight_sum_pct': _safe_round(local_data.get('top10_weight_sum_pct'), 2),
        'top10_weighted_pb': _safe_round(local_data.get('top10_weighted_pb'), 2),
        'top10_weighted_pe_ttm': _safe_round(local_data.get('top10_weighted_pe_ttm'), 2),
        'top_holdings': holdings[:10],
        'component_snapshot': components[:10],
    }


def _etf_bundle(code: str):
    result = {'code': code, 'timestamp': _now()}
    local_data = _load_local_etf_deep_data(code)
    if local_data:
        result['local_deep_data'] = {
            'source_file': local_data.get('_source_file'),
            'basic': local_data.get('basic') or {},
            'quote': local_data.get('quote') or {},
            'discount': local_data.get('discount') or {},
            'hist': local_data.get('hist') or {},
            'chip': local_data.get('chip') or {},
            'big_order': local_data.get('big_order') or {},
            'main_net_5d': local_data.get('main_net_5d') or [],
            'main_net_5d_sum_yi': local_data.get('main_net_5d_sum_yi'),
        }
        result['components'] = _etf_component_summary(local_data)

    if ak is None:
        result['error'] = 'akshare 不可用'
        return result
    try:
        names = ak.fund_name_em()
        row = names[names['基金代码'].astype(str) == code]
        if not row.empty:
            r = row.iloc[0]
            fund_name = r.get('基金简称')
            result['basic'] = {
                'fund_code': code,
                'fund_name': fund_name,
                'fund_type': r.get('基金类型'),
            }
            result['same_theme_candidates'] = _find_same_theme_candidates(str(fund_name or ''), code)
    except Exception as e:
        result['basic_error'] = str(e)[:160]

    try:
        daily = ak.fund_etf_fund_daily_em()
        row = daily[daily['基金代码'].astype(str) == code]
        if not row.empty:
            r = row.iloc[0]
            nav_fields = _extract_latest_nav_fields(r)
            result['pricing'] = {
                'fund_name': r.get('基金简称'),
                'type': r.get('类型'),
                **nav_fields,
                'growth_rate_pct': safe_pct(r.get('增长率')),
                'market_price': safe_float(r.get('市价'), None),
                'discount_rate_pct': safe_pct(r.get('折价率')),
            }
    except Exception as e:
        result['pricing_error'] = str(e)[:160]

    try:
        hist = ak.fund_etf_hist_em(symbol=code, period='daily', start_date='20240101', end_date=datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y%m%d'), adjust='qfq')
        if hist is not None and len(hist) > 0:
            hist = hist.tail(120).reset_index(drop=True)
            closes = [float(x) for x in hist['收盘'].tolist()]
            amounts = [float(x) for x in hist['成交额'].tolist()] if '成交额' in hist.columns else []
            def ret(days):
                if len(closes) <= days:
                    return None
                return _safe_round((closes[-1] / closes[-days-1] - 1) * 100, 2)
            high_20 = max(closes[-20:]) if len(closes) >= 20 else max(closes)
            result['market_stats'] = {
                'sample_days': len(hist),
                'latest_close': closes[-1],
                'ret_1d_pct': ret(1),
                'ret_5d_pct': ret(5),
                'ret_20d_pct': ret(20),
                'ret_60d_pct': ret(60),
                'drawdown_from_20d_high_pct': _safe_round((closes[-1] / high_20 - 1) * 100, 2),
                'avg_amount_20d_yi': _safe_round(sum(amounts[-20:]) / min(20, len(amounts)) / 1e8, 2) if amounts else None,
            }
    except Exception as e:
        result['market_stats_error'] = str(e)[:160]

    if 'local_deep_data' not in result:
        chip = _run_tick_chip_cli(code, 'chip')
        big_order = _run_tick_chip_cli(code, 'big_order')
        if chip and not chip.get('error'):
            result['chip'] = chip
        if big_order and not big_order.get('error'):
            result['big_order'] = big_order

    return result


def _recent_headlines(bundle: dict):
    anns = (bundle.get('announcements') or {}).get('announcements') or []
    news = (bundle.get('news') or {}).get('news') or []
    important_anns = []
    for a in anns[:5]:
        important_anns.append({
            'date': a.get('date'),
            'title': a.get('title'),
            'tag': a.get('importance') or a.get('type') or '',
        })
    recent_news = []
    for n in news[:5]:
        recent_news.append({
            'date': n.get('date') or n.get('published') or '',
            'title': n.get('title') or '',
            'source': n.get('source') or '',
        })
    return important_anns, recent_news


def _derive_stock_verdict(profile_bundle: dict, tech: dict, force: dict, market_match: dict, memory_data: dict):
    strengths = []
    risks = []
    missing = []

    finance = profile_bundle.get('finance', {}) or {}
    latest_fin = (finance.get('financials') or [{}])[0] if finance.get('financials') else {}
    valuation = profile_bundle.get('valuation', {}) or {}
    company = (profile_bundle.get('company', {}) or {}).get('profile', {}) or {}

    profit_yoy = latest_fin.get('profit_yoy')
    roe = latest_fin.get('roe')
    ocf = latest_fin.get('ocf_yi')
    pe_pct = ((valuation.get('pe') or {}).get('5yr_percentile'))
    pb_pct = ((valuation.get('pb') or {}).get('5yr_percentile'))

    ma_system = tech.get('ma_system') or {}
    arrangement = ma_system.get('arrangement')
    above = ma_system.get('price_above') or []
    macd = tech.get('macd') or {}
    consec = ((force.get('money_flow') or {}).get('consecutive') or {})

    if company.get('industry'):
        strengths.append(f"所属行业：{company.get('industry')}")
    else:
        missing.append('行业归属暂不可得')

    if profit_yoy is not None:
        if profit_yoy > 20:
            strengths.append(f"净利同比 {profit_yoy}% ，盈利增速较强")
        elif profit_yoy < 0:
            risks.append(f"净利同比 {profit_yoy}% ，盈利承压")
    else:
        missing.append('最新利润同比暂不可得')

    if roe is not None:
        if roe >= 15:
            strengths.append(f"ROE {roe}% ，质量优秀")
        elif roe <= 8:
            risks.append(f"ROE {roe}% ，资本回报偏弱")

    if ocf is not None and ocf < 0:
        risks.append(f"经营现金流 {ocf} 亿，为负值")

    avg_pct = None
    if pe_pct is not None and pb_pct is not None:
        avg_pct = (pe_pct + pb_pct) / 2
    elif pe_pct is not None:
        avg_pct = pe_pct
    elif pb_pct is not None:
        avg_pct = pb_pct

    if avg_pct is not None:
        if avg_pct < 35:
            strengths.append(f"估值历史分位约 {round(avg_pct,1)}%，处于低位区")
        elif avg_pct > 75:
            risks.append(f"估值历史分位约 {round(avg_pct,1)}%，已偏高")
    else:
        missing.append('估值分位暂不可得')

    if arrangement == '多头排列':
        strengths.append('均线多头排列')
    elif arrangement == '空头排列':
        risks.append('均线空头排列')

    if 'MA20' in above:
        strengths.append('价格站上 MA20')
    elif ma_system.get('values', {}).get('MA20') is not None:
        risks.append('价格仍在 MA20 下方')

    if macd.get('cross') == 'golden':
        strengths.append('MACD 金叉')
    elif macd.get('cross') == 'death':
        risks.append('MACD 死叉')

    if consec.get('days', 0) >= 2:
        if '流入' in str(consec.get('direction', '')):
            strengths.append(f"主力资金连续 {consec.get('days')} 天净流入")
        else:
            risks.append(f"主力资金连续 {consec.get('days')} 天净流出")

    if market_match:
        net_inflow = market_match.get('net_inflow_yi')
        avg = market_match.get('avg_pct')
        if net_inflow is not None and net_inflow > 0:
            strengths.append(f"行业资金净流入 {net_inflow} 亿")
        if avg is not None and avg < 0:
            risks.append(f"行业平均涨跌幅 {avg}% ，行业走势偏弱")

    memory_thesis = (memory_data.get('thesis') or {})
    if memory_thesis.get('long_reason') and memory_thesis.get('long_reason') != '待研究':
        strengths.append('已有长期跟踪逻辑')

    score = 0
    score += min(len(strengths), 6)
    score -= min(len(risks), 6)

    if score >= 4:
        action = '分批跟踪 / 可试仓'
    elif score >= 1:
        action = '观察为主，等确认'
    elif score <= -3:
        action = '偏谨慎，降级观察'
    else:
        action = '中性观察'

    return {
        'strengthened_logic': strengths[:8],
        'weakened_logic': risks[:8],
        'missing_checks': missing[:8],
        'action_bias': action,
        'score': score,
    }


def _derive_trade_plan_stock(memory_data: dict, tech: dict, verdict: dict):
    ma = tech.get('ma_system') or {}
    vals = ma.get('values') or {}
    latest = (tech.get('latest') or {}).get('close')
    support = ma.get('support')
    resistance = ma.get('resistance')
    lower = ((tech.get('bollinger') or {}).get('lower'))
    upper = ((tech.get('bollinger') or {}).get('upper'))

    buy_low = support or lower
    buy_high = latest if latest and buy_low and latest >= buy_low else resistance or upper
    stop_loss = None
    if buy_low:
        stop_loss = _safe_round(buy_low * 0.97, 3)
    elif latest:
        stop_loss = _safe_round(latest * 0.95, 3)

    invalidation = ((memory_data.get('thesis') or {}).get('invalidation') or '').strip()
    if not invalidation or invalidation == '待研究':
        if vals.get('MA20'):
            invalidation = f"跌破 MA20（{vals.get('MA20')}）且资金继续走弱"
        elif stop_loss:
            invalidation = f"跌破 {stop_loss} 且量价失真"
        else:
            invalidation = '关键支撑失守且资金流转弱'

    return {
        'current_bias': verdict.get('action_bias'),
        'build_zone': {
            'low': _safe_round(buy_low, 3),
            'high': _safe_round(buy_high, 3),
        },
        'position_cap': '单票 10%~20%（确认后再提）' if verdict.get('score', 0) >= 3 else '单票 0%~10%（观察/试错）',
        'stop_loss': stop_loss,
        'take_profit_note': '若出现放量冲高但行业/资金不同步，优先分批兑现',
        'invalidation': invalidation,
        'next_review_points': [
            '下一次公告 / 订单 / 财报边际变化',
            '行业资金流与个股相对强弱是否继续背离',
            '是否重新站稳 MA20 / MA60 或跌破关键支撑',
        ]
    }


def _derive_trade_plan_etf(bundle: dict, tech: dict, memory_data: dict):
    pricing = bundle.get('pricing') or {}
    local = bundle.get('local_deep_data') or {}
    nav = pricing.get('unit_nav') or ((local.get('discount') or {}).get('unit_nav_prev'))
    mkt = pricing.get('market_price') or ((local.get('quote') or {}).get('close'))
    discount = pricing.get('discount_rate_pct')
    if discount is None:
        discount = safe_pct((local.get('discount') or {}).get('discount_rate'))
    ma = tech.get('ma_system') or {}
    support = ma.get('support') or (((local.get('chip') or {}).get('data') or {}).get('support_zones') or [{}])[0].get('price')
    resistance = ma.get('resistance') or (((local.get('chip') or {}).get('data') or {}).get('trapped_zones') or [{}])[0].get('price')
    invalidation = ((memory_data.get('thesis') or {}).get('invalidation') or '').strip()
    if not invalidation or invalidation == '待研究':
        if support:
            invalidation = f'跌破 ETF 技术支撑 {support} 且折价/申赎未改善'
        else:
            invalidation = '跟踪指数走弱且折溢价/成交结构恶化'

    optimistic_trigger = f'放量站稳 {resistance}' if resistance else '资金回流且连续站稳短期均线'
    neutral_trigger = f'在 {support} 附近止跌横盘' if support else '折溢价维持稳定且成交未恶化'
    pessimistic_trigger = f'跌破 {support} 且折价扩大' if support else '主题走弱且成交坍缩'

    build_low = _safe_round(support or nav or mkt, 3)
    build_candidates = [safe_float(x, None) for x in [resistance, mkt, nav] if safe_float(x, None) is not None]
    build_high_raw = max(build_candidates) if build_candidates else None

    return {
        'current_bias': '主题观察 / 等消息与资金一致',
        'build_zone': {
            'low': build_low,
            'high': _safe_round(build_high_raw, 3),
        },
        'position_cap': '主题 ETF 单品种 10%~15%',
        'stop_loss': _safe_round((support or mkt or nav or 0) * 0.96, 3) if (support or mkt or nav) else None,
        'take_profit_note': '主题情绪加速但折价不收敛时，优先减仓而非追高',
        'invalidation': invalidation,
        'scenario_triggers': {
            'optimistic': optimistic_trigger,
            'neutral': neutral_trigger,
            'pessimistic': pessimistic_trigger,
        },
        'next_review_points': [
            '份额申赎 / 折溢价是否恶化',
            '主题驱动是否从消息变为业绩/订单验证',
            '成分方向与主题纯度是否一致',
            '同类替代 ETF 是否出现更优流动性/费率选择',
        ],
        'value_anchor': {
            'unit_nav': nav,
            'market_price': mkt,
            'discount_rate_pct': discount,
        }
    }


def _etf_verdict(bundle: dict, tech: dict, market_ctx: dict, memory_data: dict):
    strengths, risks, missing = [], [], []
    basic = bundle.get('basic') or {}
    pricing = bundle.get('pricing') or {}
    local = bundle.get('local_deep_data') or {}
    components = bundle.get('components') or {}
    market_stats = bundle.get('market_stats') or {}
    chip = (local.get('chip') or bundle.get('chip') or {})
    big_order = (local.get('big_order') or bundle.get('big_order') or {})

    fund_name = basic.get('fund_name')
    if fund_name:
        strengths.append(f"ETF名称：{fund_name}")

    dr = pricing.get('discount_rate_pct')
    if dr is None:
        dr = safe_pct((local.get('discount') or {}).get('discount_rate'))
    if dr is not None:
        if abs(dr) <= 0.3:
            strengths.append(f"折溢价 {dr}% ，定价效率正常")
        elif abs(dr) >= 1:
            risks.append(f"折溢价 {dr}% ，交易情绪偏强/偏弱")
    else:
        missing.append('折溢价暂不可得')

    arrangement = ((tech.get('ma_system') or {}).get('arrangement'))
    if arrangement == '多头排列':
        strengths.append('均线多头排列')
    elif arrangement == '空头排列':
        risks.append('均线空头排列')
    elif (tech.get('ma_system') or {}).get('price_below'):
        risks.append('价格位于主要均线下方')

    name = fund_name or ''
    top_concepts = market_ctx.get('top_concept_inflow') or []
    if any(k for k in top_concepts if k and (k in name or name[:2] in k)):
        strengths.append('主题方向位于当下资金主线附近')
    elif name:
        risks.append('主题方向暂未出现在主线资金前排')

    growth = pricing.get('growth_rate_pct')
    if growth is None:
        growth = (local.get('quote') or {}).get('chg_pct')
    if growth is not None and growth < -2:
        risks.append(f"当日净值/价格涨跌 {growth}% ，波动偏大")

    top10_weight = components.get('top10_weight_sum_pct')
    if top10_weight is not None:
        if top10_weight >= 35:
            risks.append(f"前十大权重合计 {top10_weight}% ，集中度较高")
        else:
            strengths.append(f"前十大权重合计 {top10_weight}% ，集中度暂可控")
    else:
        missing.append('成分集中度暂不可得')

    weighted_pe = components.get('top10_weighted_pe_ttm')
    if weighted_pe is not None:
        if weighted_pe > 45:
            risks.append(f"样本加权 PE 约 {weighted_pe} 倍，景气溢价偏高")
        elif weighted_pe < 20:
            strengths.append(f"样本加权 PE 约 {weighted_pe} 倍，估值压力较小")
    else:
        missing.append('成分估值样本暂不可得')

    hist_5d = market_stats.get('ret_5d_pct')
    if hist_5d is None:
        hist_5d = (local.get('hist') or {}).get('ret_5d_pct')
    if hist_5d is not None and hist_5d <= -5:
        risks.append(f"近5日收益 {hist_5d}% ，短线回撤较大")

    main_net_sum = local.get('main_net_5d_sum_yi')
    if main_net_sum is not None:
        if main_net_sum > 0:
            strengths.append(f"近5日主力净流入 {main_net_sum} 亿")
        elif main_net_sum < 0:
            risks.append(f"近5日主力净流出 {main_net_sum} 亿")
    else:
        missing.append('份额/资金净流入样本暂不完整')

    big_net = (((big_order.get('data') or {}).get('summary') or {}).get('big_net_hand'))
    if big_net is not None:
        if big_net > 0:
            strengths.append(f"分时大单净流入 {big_net} 手，低位有承接")
        elif big_net < 0:
            risks.append(f"分时大单净流出 {big_net} 手")

    profitable_ratio = (((chip.get('data') or {}).get('profitable_ratio_pct')))
    if profitable_ratio is not None:
        if profitable_ratio < 35:
            risks.append(f"获利盘仅 {profitable_ratio}% ，上方套牢压力大")
        elif profitable_ratio > 70:
            strengths.append(f"获利盘 {profitable_ratio}% ，筹码较健康")
    else:
        missing.append('筹码结构暂不可得')

    if not bundle.get('same_theme_candidates'):
        risks.append('同主题替代 ETF 稀缺，流动性与费率缺少可替换比较')

    if ((local.get('basic') or {}).get('tracking_index') or basic.get('fund_name')):
        strengths.append('已识别跟踪标的，可继续追踪指数方法学/调仓机制')
    else:
        missing.append('指数方法学暂不可得')

    score = min(len(strengths), 6) - min(len(risks), 6)
    if score >= 3:
        bias = '可跟踪，但不追高'
    elif score <= -2:
        bias = '偏谨慎，等待主题修复'
    else:
        bias = '中性观察'

    return {
        'strengthened_logic': strengths[:10],
        'weakened_logic': risks[:10],
        'missing_checks': missing[:10],
        'action_bias': bias,
        'score': score,
        'theme_purity_note': '优先检查指数方法学、前十大成分、主题纯度、折溢价与份额申赎是否同向验证。'
    }


def _analyze_stock(item: dict, market_ctx: dict):
    code = item['code']
    name = item.get('name', '')
    memory_data = _load_memory_stock(code)
    realtime = market_ctx.get('watchlist_realtime', {}).get(code, {})

    profile_bundle = _stock_profile_bundle(code)
    tech = tech_analysis.analyze_single(code)
    force = main_force.full_analysis(code)
    kline = _kline_metrics(code)
    company = (profile_bundle.get('company', {}) or {}).get('profile', {}) or {}
    market_match = _match_industry_flow(company.get('industry', ''), market_ctx)
    important_anns, recent_news = _recent_headlines(profile_bundle)
    verdict = _derive_stock_verdict(profile_bundle, tech, force, market_match, memory_data)
    trade_plan = _derive_trade_plan_stock(memory_data, tech, verdict)

    current_pct = realtime.get('pct_chg')
    industry_avg = market_match.get('avg_pct') if market_match else None

    return {
        'code': code,
        'name': name,
        'type': 'stock',
        'generated_at': _now(),
        'memory_status': {
            'status': memory_data.get('status', 'watching'),
            'conviction': ((memory_data.get('thesis') or {}).get('conviction')),
            'thesis': memory_data.get('thesis') or {},
            'report_records': len(memory_data.get('report_refs', []) or []),
        },
        'market_snapshot': {
            'price': realtime.get('close') or realtime.get('price'),
            'pct_chg': current_pct,
            'volume': realtime.get('volume'),
            'amount': realtime.get('amount'),
            'industry_relative_pct': _safe_round(current_pct - industry_avg, 2) if current_pct is not None and industry_avg is not None else None,
        },
        'company_profile': (profile_bundle.get('company') or {}).get('profile') or {},
        'valuation_snapshot': {
            'realtime_valuation': (profile_bundle.get('company') or {}).get('valuation') or {},
            'history_percentile': {
                'pe': (profile_bundle.get('valuation') or {}).get('pe') or {},
                'pb': (profile_bundle.get('valuation') or {}).get('pb') or {},
                'overall_assessment': (profile_bundle.get('valuation') or {}).get('overall_assessment'),
            }
        },
        'financial_quality': {
            'latest_four_periods': (profile_bundle.get('finance') or {}).get('financials') or [],
            'signals': (profile_bundle.get('finance') or {}).get('financial_signals') or [],
        },
        'technical_structure': {
            'kline_metrics': kline,
            'analysis': tech,
        },
        'money_structure': force,
        'industry_context': {
            'industry': company.get('industry'),
            'industry_flow_match': market_match,
            'top_industry_inflow': market_ctx.get('top_industry_inflow') or [],
            'top_concept_inflow': market_ctx.get('top_concept_inflow') or [],
        },
        'event_catalysts': {
            'important_announcements': important_anns,
            'recent_news': recent_news,
        },
        'verification': verdict,
        'trade_plan': trade_plan,
    }


def _analyze_etf(item: dict, market_ctx: dict):
    code = item['code']
    name = item.get('name', '')
    memory_data = _load_memory_stock(code)
    realtime = market_ctx.get('watchlist_realtime', {}).get(code, {})
    bundle = _etf_bundle(code)
    tech = tech_analysis.analyze_single(code)
    kline = _kline_metrics(code)
    verdict = _etf_verdict(bundle, tech, market_ctx, memory_data)
    trade_plan = _derive_trade_plan_etf(bundle, tech, memory_data)
    local = bundle.get('local_deep_data') or {}

    return {
        'code': code,
        'name': name,
        'type': 'etf',
        'generated_at': _now(),
        'memory_status': {
            'status': memory_data.get('status', 'watching'),
            'conviction': ((memory_data.get('thesis') or {}).get('conviction')),
            'thesis': memory_data.get('thesis') or {},
            'report_records': len(memory_data.get('report_refs', []) or []),
        },
        'market_snapshot': {
            'price': realtime.get('close') or realtime.get('price') or (bundle.get('pricing') or {}).get('market_price') or (local.get('quote') or {}).get('close'),
            'pct_chg': realtime.get('pct_chg') if realtime.get('pct_chg') is not None else (bundle.get('pricing') or {}).get('growth_rate_pct') or (local.get('quote') or {}).get('chg_pct'),
            'volume': realtime.get('volume') or (local.get('quote') or {}).get('volume_hand'),
            'amount': realtime.get('amount') or (local.get('quote') or {}).get('turnover_yi'),
        },
        'etf_profile': bundle,
        'index_methodology': {
            'tracking_index': ((local.get('basic') or {}).get('tracking_index')),
            'benchmark': ((local.get('basic') or {}).get('benchmark')),
            'tracking_control': {
                'daily_tracking_dev_target': ((local.get('basic') or {}).get('daily_tracking_dev_target')),
                'annual_tracking_error_target': ((local.get('basic') or {}).get('annual_tracking_error_target')),
            },
        },
        'component_quality': bundle.get('components') or {},
        'pricing_efficiency': {
            'pricing': bundle.get('pricing') or {},
            'market_stats': bundle.get('market_stats') or {},
            'discount': (local.get('discount') or {}),
        },
        'flow_and_crowding': {
            'main_net_5d': local.get('main_net_5d') or [],
            'main_net_5d_sum_yi': local.get('main_net_5d_sum_yi'),
            'chip': local.get('chip') or bundle.get('chip') or {},
            'big_order': local.get('big_order') or bundle.get('big_order') or {},
        },
        'technical_structure': {
            'kline_metrics': kline,
            'analysis': tech,
        },
        'theme_context': {
            'top_industry_inflow': market_ctx.get('top_industry_inflow') or [],
            'top_concept_inflow': market_ctx.get('top_concept_inflow') or [],
            'northbound_recent': (market_ctx.get('north') or [])[:3],
            'same_theme_candidates': bundle.get('same_theme_candidates') or [],
        },
        'verification': verdict,
        'trade_plan': trade_plan,
    }


def build_watchlist_deep_dive(target_code: str | None = None, condensed: bool = False):
    watchlist = load_watchlist()
    if target_code:
        watchlist = [x for x in watchlist if (x.get('code', '') if isinstance(x, dict) else str(x)) == target_code]

    market_ctx = _load_market_context()

    items = []
    for raw in watchlist:
        item = raw if isinstance(raw, dict) else {'code': str(raw), 'name': '', 'type': 'stock'}
        code = item.get('code', '')
        if not code:
            continue
        ptype = (item.get('type') or ('etf' if code.startswith(('5', '15', '16', '56', '58')) else 'stock')).lower()
        try:
            if ptype == 'etf':
                items.append(_analyze_etf(item, market_ctx))
            else:
                items.append(_analyze_stock(item, market_ctx))
        except Exception as e:
            items.append({
                'code': code,
                'name': item.get('name', ''),
                'type': ptype,
                'error': str(e)[:200],
                'generated_at': _now(),
            })

    result = {
        'generated_at': _now(),
        'watchlist_count': len(items),
        'market_context': {
            'top_industry_inflow': market_ctx.get('top_industry_inflow') or [],
            'top_concept_inflow': market_ctx.get('top_concept_inflow') or [],
            'northbound_recent': (market_ctx.get('north') or [])[:5],
            'snapshot_error': (market_ctx.get('snapshot') or {}).get('snapshot_error'),
        },
        'items': items,
    }

    if condensed:
        summary_items = []
        for x in items:
            verification = x.get('verification') or {}
            trade_plan = x.get('trade_plan') or {}
            summary_items.append({
                'code': x.get('code'),
                'name': x.get('name'),
                'type': x.get('type'),
                'price': (x.get('market_snapshot') or {}).get('price'),
                'pct_chg': (x.get('market_snapshot') or {}).get('pct_chg'),
                'action_bias': verification.get('action_bias') or trade_plan.get('current_bias'),
                'top_strengths': (verification.get('strengthened_logic') or [])[:3],
                'top_risks': (verification.get('weakened_logic') or [])[:3],
                'next_review_points': (trade_plan.get('next_review_points') or [])[:3],
            })
        return {
            'generated_at': result['generated_at'],
            'watchlist_count': result['watchlist_count'],
            'pool_hint': '先总结组合风格暴露，再逐只回答：市场在交易什么、逻辑被验证还是证伪、下一步怎么做。',
            'top_industry_inflow': result['market_context']['top_industry_inflow'],
            'top_concept_inflow': result['market_context']['top_concept_inflow'],
            'items': summary_items,
        }

    return result


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if cmd == 'all':
        json_output(build_watchlist_deep_dive())
    elif cmd == 'summary':
        json_output(build_watchlist_deep_dive(condensed=True))
    elif cmd == 'code':
        if len(sys.argv) < 3:
            print('Usage: watchlist_deep_dive.py code <code>', file=sys.stderr)
            sys.exit(1)
        json_output(build_watchlist_deep_dive(target_code=sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(1)
