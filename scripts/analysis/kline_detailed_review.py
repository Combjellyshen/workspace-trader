#!/usr/bin/env python3
"""
K线详细技术复盘器

目标：给周报（五天）与每日收盘复盘提供可直接引用的结构化技术面材料，
不只报指标，还解释“为什么变”。

用法：
  python3 scripts/analysis/kline_detailed_review.py indices
  python3 scripts/analysis/kline_detailed_review.py watchlist
  python3 scripts/analysis/kline_detailed_review.py all
  python3 scripts/analysis/kline_detailed_review.py code 002475
"""
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.analysis import tech_analysis  # noqa: E402
from scripts.utils.common import json_output, load_watchlist, load_config  # noqa: E402

try:
    import akshare as ak  # type: ignore
except Exception:
    ak = None


INDEX_MAP = {
    'sh000001': '上证指数',
    'sz399001': '深证成指',
    'sz399006': '创业板指',
    'sh000300': '沪深300',
    'sh000688': '科创50',
}


def _safe_round(v, n=2):
    try:
        return round(float(v), n)
    except Exception:
        return None


def _ret(closes, days):
    if len(closes) <= days:
        return None
    return round((closes[-1] / closes[-days-1] - 1) * 100, 2)


def _zone(values, up=70, down=30):
    if values is None:
        return None
    if values >= up:
        return 'overbought'
    if values <= down:
        return 'oversold'
    return 'neutral'


def _ma_change_reason(closes, vols, ma_name, prev_price, curr_price, prev_ma, curr_ma):
    if prev_ma is None or curr_ma is None:
        return None
    prev_above = prev_price > prev_ma
    curr_above = curr_price > curr_ma
    avg5 = statistics.mean(vols[-6:-1]) if len(vols) >= 6 else None
    vol_ratio = (vols[-1] / avg5) if avg5 and avg5 > 0 else None
    if prev_above != curr_above:
        direction = '站上' if curr_above else '跌破'
        reason = f'最新收盘{curr_price}相对{ma_name}({curr_ma})出现{direction}'
        if vol_ratio:
            if vol_ratio >= 1.5:
                reason += '，且伴随明显放量，说明资金选择更坚决'
            elif vol_ratio <= 0.7:
                reason += '，但量能不足，可靠性一般'
            else:
                reason += '，量能中性，需要后续确认'
        return reason
    return None


def _explain_macd(prev_macd, curr_macd):
    if not curr_macd:
        return None
    cross = curr_macd.get('cross')
    hist_trend = curr_macd.get('histogram_trend')
    if cross == 'golden':
        return 'MACD出现金叉，说明短线趋势开始由弱转强，但仍需量能配合确认。'
    if cross == 'death':
        return 'MACD出现死叉，说明短线动能走弱，反弹质量下降。'
    if prev_macd and hist_trend == 'expanding' and curr_macd.get('MACD', 0) > 0:
        return 'MACD红柱继续放大，说明上行动能在强化。'
    if prev_macd and hist_trend == 'expanding' and curr_macd.get('MACD', 0) < 0:
        return 'MACD绿柱继续放大，说明下行动能在强化。'
    if prev_macd and hist_trend == 'contracting':
        return 'MACD柱体收敛，说明原有趋势动能开始衰减。'
    return None


def _explain_rsi(prev_rsi, curr_rsi):
    if curr_rsi is None:
        return None
    prev_zone = _zone(prev_rsi)
    curr_zone = _zone(curr_rsi)
    if prev_zone != curr_zone:
        if curr_zone == 'overbought':
            return f'RSI升至{curr_rsi}，进入超买区，短线容易出现震荡或分歧。'
        if curr_zone == 'oversold':
            return f'RSI降至{curr_rsi}，进入超卖区，存在技术性反抽条件。'
        return f'RSI回到中性区({curr_rsi})，说明极端情绪有所缓和。'
    if prev_rsi is not None:
        delta = curr_rsi - prev_rsi
        if abs(delta) >= 8:
            direction = '快速抬升' if delta > 0 else '快速回落'
            return f'RSI较前一日{direction}，反映短线动能变化较大。'
    return None


def _explain_kdj(prev_kdj, curr_kdj):
    if not curr_kdj:
        return None
    j = curr_kdj.get('J')
    if j is None:
        return None
    prev_j = prev_kdj.get('J') if prev_kdj else None
    if prev_j is not None:
        if prev_j <= 0 < j:
            return 'KDJ的J值从极弱区回到0上方，说明短线超跌状态开始缓解。'
        if prev_j >= 100 > j:
            return 'KDJ的J值从过热区回落，说明短线追涨情绪开始降温。'
    if j < 0:
        return f'KDJ的J值为{j}，仍处在超卖区，反抽可能存在但不等于趋势反转。'
    if j > 100:
        return f'KDJ的J值为{j}，处在过热区，继续上冲时需警惕分歧。'
    return None


def _explain_boll(prev_boll, curr_boll):
    if not curr_boll:
        return None
    pos = curr_boll.get('position')
    width = curr_boll.get('width_pct')
    prev_width = prev_boll.get('width_pct') if prev_boll else None
    if pos is not None:
        if pos >= 80:
            return f'价格已接近布林上轨（位置{pos}%），短线偏强但也更接近分歧区。'
        if pos <= 20:
            return f'价格已接近布林下轨（位置{pos}%），短线偏弱但也接近技术性修复区。'
    if prev_width is not None and width is not None:
        if width - prev_width >= 3:
            return '布林带宽明显放大，说明波动率在扩张，趋势选择可能临近。'
        if prev_width - width >= 3:
            return '布林带宽明显收窄，说明波动率收缩，后续更容易酝酿方向选择。'
    return None


def _explain_volume(vols, closes):
    if len(vols) < 6:
        return None
    avg5 = statistics.mean(vols[-6:-1])
    if avg5 <= 0:
        return None
    vr = vols[-1] / avg5
    price_delta = closes[-1] - closes[-2]
    if vr >= 1.8 and price_delta > 0:
        return f'最新成交量较5日均量放大到{vr:.2f}倍，且价格上涨，属于放量上攻。'
    if vr >= 1.8 and price_delta < 0:
        return f'最新成交量较5日均量放大到{vr:.2f}倍，但价格下跌，说明抛压集中释放。'
    if vr <= 0.7 and price_delta > 0:
        return f'价格上涨但量能仅为5日均量的{vr:.2f}倍，偏向缩量反弹，持续性要打问号。'
    if vr <= 0.7 and price_delta < 0:
        return f'价格下跌且量能仅为5日均量的{vr:.2f}倍，更像弱势阴跌而非集中踩踏。'
    return f'量能约为5日均量的{vr:.2f}倍，当前量价配合中性。'


def _load_series(code):
    df = None
    if code.startswith(('sh', 'sz')) and ak is not None:
        try:
            df = ak.stock_zh_index_daily(symbol=code).tail(160).reset_index(drop=True)
            if df is not None and not df.empty:
                df = df.rename(columns={'date': '日期', 'open': '开盘', 'close': '收盘', 'high': '最高', 'low': '最低', 'volume': '成交量'})
                # If latest bar is before today, try to append today's realtime data
                today_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y-%m-%d')
                latest_date = str(df['日期'].iloc[-1])[:10]
                if latest_date < today_str:
                    try:
                        spot = ak.stock_zh_index_spot_em()
                        prefix = code[:2]
                        spot_code = prefix + code[2:]
                        row = spot[spot['代码'] == spot_code]
                        if row.empty:
                            row = spot[spot['代码'] == code]
                        if not row.empty:
                            r = row.iloc[0]
                            import pandas as pd
                            new_row = pd.DataFrame([{
                                '日期': today_str,
                                '开盘': float(r.get('今开', 0)),
                                '收盘': float(r.get('最新价', 0)),
                                '最高': float(r.get('最高', 0)),
                                '最低': float(r.get('最低', 0)),
                                '成交量': float(r.get('成交量', 0)),
                            }])
                            df = pd.concat([df, new_row], ignore_index=True)
                    except Exception:
                        pass  # realtime supplement failed, continue with daily data
        except Exception:
            df = None

    if (df is None or len(df) < 10) and ak is not None and not code.startswith(('sh', 'sz')):
        end = datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y%m%d')
        start = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=220)).strftime('%Y%m%d')
        try:
            if code.startswith(('5', '15', '16', '56', '58')):
                df = ak.fund_etf_hist_em(symbol=code, period='daily', start_date=start, end_date=end, adjust='qfq')
            else:
                df = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start, end_date=end, adjust='qfq')
        except Exception:
            df = None

    # ETF fallback: fund_etf_hist_sina
    if (df is None or len(df) < 10) and ak is not None and code.startswith(('5', '15', '16', '56', '58')):
        try:
            prefix = 'sh' if code.startswith(('5', '11')) else 'sz'
            df = ak.fund_etf_hist_sina(symbol=f'{prefix}{code}')
            if df is not None and not df.empty:
                df = df.rename(columns={'date': '日期', 'open': '开盘', 'close': '收盘',
                                         'high': '最高', 'low': '最低', 'volume': '成交量', 'amount': '成交额'})
        except Exception:
            df = None

    if (df is None or len(df) < 10) and not code.startswith(('sh', 'sz', '5', '15', '16', '56', '58')):
        try:
            import tushare as ts
            cfg = load_config()
            token = cfg.get('tushare_token', '')
            if token:
                ts.set_token(token)
                pro = ts.pro_api()
                ts_code = code + ('.SH' if code.startswith('6') else '.SZ')
                end = datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y%m%d')
                start = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=220)).strftime('%Y%m%d')
                raw = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
                if raw is not None and not raw.empty:
                    raw = raw.sort_values('trade_date').reset_index(drop=True)
                    df = raw.rename(columns={'trade_date': '日期', 'open': '开盘', 'close': '收盘', 'high': '最高', 'low': '最低', 'vol': '成交量', 'amount': '成交额'})
        except Exception:
            df = None

    if df is None or len(df) < 10:
        raise ValueError('K线数据不足')
    close_col = '收盘' if '收盘' in df.columns else 'close'
    open_col = '开盘' if '开盘' in df.columns else 'open'
    high_col = '最高' if '最高' in df.columns else 'high'
    low_col = '最低' if '最低' in df.columns else 'low'
    vol_col = '成交量' if '成交量' in df.columns else 'volume'
    amt_col = '成交额' if '成交额' in df.columns else None
    dates = [str(x)[:10] for x in df['日期'].tolist()] if '日期' in df.columns else [str(x)[:10] for x in df.index.tolist()]
    opens = [float(x) for x in df[open_col].tolist()]
    highs = [float(x) for x in df[high_col].tolist()]
    lows = [float(x) for x in df[low_col].tolist()]
    closes = [float(x) for x in df[close_col].tolist()]
    vols = [float(x) for x in df[vol_col].tolist()]
    amts = [float(x) for x in df[amt_col].tolist()] if amt_col else None
    return dates, opens, highs, lows, closes, vols, amts


def analyze_kline_detail(code, name=None):
    dates, opens, highs, lows, closes, vols, amts = _load_series(code)
    curr = tech_analysis.analyze_single(code, is_index=code.startswith(('sh', 'sz')))  # type: ignore[arg-type]
    if curr.get('error'):
        curr = {
            'code': code,
            'analysis_time': datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            'latest': {'close': closes[-1], 'high': highs[-1], 'low': lows[-1], 'volume': vols[-1]},
            'rsi_14': tech_analysis.calc_rsi(closes, 14),
            'kdj': tech_analysis.calc_kdj(highs, lows, closes),
            'bollinger': tech_analysis.calc_bollinger(closes),
            'macd': tech_analysis.calc_macd(closes),
            'volume_ratio': tech_analysis.calc_volume_ratio(vols),
            'ma_system': tech_analysis.ma_system(closes),
            'patterns': tech_analysis.detect_patterns(closes, highs, lows, vols),
        }
    prev_df_closes = closes[:-1]
    prev_highs = highs[:-1]
    prev_lows = lows[:-1]
    prev_vols = vols[:-1]

    prev = {
        'rsi_14': tech_analysis.calc_rsi(prev_df_closes, 14),
        'kdj': tech_analysis.calc_kdj(prev_highs, prev_lows, prev_df_closes),
        'bollinger': tech_analysis.calc_bollinger(prev_df_closes),
        'macd': tech_analysis.calc_macd(prev_df_closes),
        'ma_system': tech_analysis.ma_system(prev_df_closes),
    }

    latest = {
        'date': dates[-1],
        'open': opens[-1],
        'high': highs[-1],
        'low': lows[-1],
        'close': closes[-1],
        'pct_chg_1d': _ret(closes, 1),
        'pct_chg_5d': _ret(closes, 5),
        'pct_chg_20d': _ret(closes, 20),
        'pct_chg_60d': _ret(closes, 60),
        'volume': vols[-1],
        'amount': _safe_round(amts[-1], 2) if amts else None,
    }

    ma_curr = (curr.get('ma_system') or {}).get('values') or {}
    ma_prev = (prev.get('ma_system') or {}).get('values') or {}
    prev_price = closes[-2]
    curr_price = closes[-1]

    change_signals = []
    explanations = []
    for ma_name in ['MA5', 'MA10', 'MA20', 'MA60']:
        reason = _ma_change_reason(closes, vols, ma_name, prev_price, curr_price, ma_prev.get(ma_name), ma_curr.get(ma_name))
        if reason:
            change_signals.append(f'{ma_name}位置切换')
            explanations.append(reason)

    macd_exp = _explain_macd(prev.get('macd'), curr.get('macd'))
    if macd_exp:
        change_signals.append('MACD变化')
        explanations.append(macd_exp)
    rsi_exp = _explain_rsi(prev.get('rsi_14'), curr.get('rsi_14'))
    if rsi_exp:
        change_signals.append('RSI变化')
        explanations.append(rsi_exp)
    kdj_exp = _explain_kdj(prev.get('kdj'), curr.get('kdj'))
    if kdj_exp:
        change_signals.append('KDJ变化')
        explanations.append(kdj_exp)
    boll_exp = _explain_boll(prev.get('bollinger'), curr.get('bollinger'))
    if boll_exp:
        change_signals.append('布林带变化')
        explanations.append(boll_exp)

    volume_exp = _explain_volume(vols, closes)
    if volume_exp:
        change_signals.append('量价关系')
        explanations.append(volume_exp)

    patterns = curr.get('patterns') or []
    if patterns:
        pattern_names = '、'.join([p.get('pattern', '') for p in patterns[:4] if p.get('pattern')])
        if pattern_names:
            change_signals.append('形态识别')
            explanations.append(f'当前识别到的K线/量价形态包括：{pattern_names}。')

    high_20 = max(highs[-20:])
    low_20 = min(lows[-20:])
    support = (curr.get('ma_system') or {}).get('support') or min([v for v in ma_curr.values() if v < curr_price], default=None)
    resistance = (curr.get('ma_system') or {}).get('resistance') or min([v for v in ma_curr.values() if v > curr_price], default=None)

    core_read = []
    arrangement = (curr.get('ma_system') or {}).get('arrangement')
    if arrangement:
        core_read.append(f'均线结构为{arrangement}')
    if curr.get('macd') and curr['macd'].get('cross') in ('golden', 'death'):
        core_read.append(f"MACD出现{'金叉' if curr['macd']['cross']=='golden' else '死叉'}")
    if curr.get('rsi_14') is not None:
        core_read.append(f"RSI14={curr['rsi_14']}")
    if curr.get('kdj'):
        core_read.append(f"KDJ-J={curr['kdj'].get('J')}")

    return {
        'code': code,
        'name': name or INDEX_MAP.get(code, ''),
        'generated_at': datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y-%m-%d %H:%M:%S'),
        'latest_bar': latest,
        'technical_snapshot': {
            'ma_system': curr.get('ma_system'),
            'macd': curr.get('macd'),
            'rsi_14': curr.get('rsi_14'),
            'kdj': curr.get('kdj'),
            'bollinger': curr.get('bollinger'),
            'volume_ratio': curr.get('volume_ratio'),
            'patterns': curr.get('patterns'),
        },
        'kline_key_levels': {
            'support': _safe_round(support, 3),
            'resistance': _safe_round(resistance, 3),
            'high_20d': _safe_round(high_20, 3),
            'low_20d': _safe_round(low_20, 3),
        },
        'change_signals': list(dict.fromkeys(change_signals)),
        'why_it_changed': explanations[:8],
        'summary_conclusion': '；'.join(core_read[:5]),
        'reporting_hint': '写入正式报告时，至少回答：价格相对MA5/10/20/60怎么变、MACD/RSI/KDJ是强化还是钝化、量价是否匹配、变化原因是什么、关键支撑/压力在哪里。'
    }


def analyze_watchlist():
    results = []
    for item in load_watchlist():
        s = item if isinstance(item, dict) else {'code': str(item), 'name': ''}
        code = s.get('code', '')
        if not code:
            continue
        try:
            results.append(analyze_kline_detail(code, s.get('name', '')))
        except Exception as e:
            results.append({'code': code, 'name': s.get('name', ''), 'error': str(e)[:200]})
    return results


def analyze_indices():
    results = []
    for code, name in INDEX_MAP.items():
        try:
            results.append(analyze_kline_detail(code, name))
        except Exception as e:
            results.append({'code': code, 'name': name, 'error': str(e)[:200]})
    return results


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if cmd == 'indices':
        json_output(analyze_indices())
    elif cmd == 'watchlist':
        json_output(analyze_watchlist())
    elif cmd == 'all':
        json_output({'indices': analyze_indices(), 'watchlist': analyze_watchlist()})
    elif cmd == 'code':
        code = sys.argv[2]
        json_output(analyze_kline_detail(code))
    else:
        print(__doc__)
        sys.exit(1)
