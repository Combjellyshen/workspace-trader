#!/usr/bin/env python3
"""
技术分析计算器 — 给自选股 & 指数算关键技术指标

输入：股票代码（或 --watchlist）
输出：RSI / KDJ / BOLL / MACD / 量比 / 均线系统 / 形态识别

子命令：
  stock <code>     — 单只股票技术分析
  index            — 主要指数技术分析
  watchlist        — 自选股批量
  all              — 指数 + 自选股
"""
import json
import sys
import math
from datetime import datetime, timedelta


def _load_kline(code, days=120):
    """通过 akshare 加载K线；兼容指数、股票、ETF"""
    import akshare as ak
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')
    
    if code.startswith('sh') or code.startswith('sz'):
        # 指数
        symbol = code[2:]
        df = ak.stock_zh_index_daily_em(symbol=symbol)
        if df is not None and not df.empty:
            df = df.rename(columns={'date': '日期', 'open': '开盘', 'close': '收盘', 
                                     'high': '最高', 'low': '最低', 'volume': '成交量'})
            df = df.tail(days)
    else:
        # ETF优先走ETF接口
        if code.startswith(('5', '15', '16', '56', '58')):
            try:
                df = ak.fund_etf_hist_em(symbol=code, period='daily', start_date=start, end_date=end, adjust='qfq')
            except Exception:
                df = None
        else:
            df = None
        if df is None or df.empty:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                     start_date=start, end_date=end, adjust="qfq")
    
    if df is None or df.empty:
        return None
    return df


def calc_ema(data, period):
    """EMA"""
    result = [0.0] * len(data)
    result[0] = data[0]
    k = 2.0 / (period + 1)
    for i in range(1, len(data)):
        result[i] = data[i] * k + result[i-1] * (1 - k)
    return result


def calc_rsi(closes, period=14):
    """RSI"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_kdj(highs, lows, closes, n=9, m1=3, m2=3):
    """KDJ"""
    if len(closes) < n:
        return None
    
    k_vals = [50.0]
    d_vals = [50.0]
    
    for i in range(n - 1, len(closes)):
        low_n = min(lows[i - n + 1:i + 1])
        high_n = max(highs[i - n + 1:i + 1])
        
        if high_n == low_n:
            rsv = 50.0
        else:
            rsv = (closes[i] - low_n) / (high_n - low_n) * 100
        
        k = (2/3) * k_vals[-1] + (1/3) * rsv
        d = (2/3) * d_vals[-1] + (1/3) * k
        k_vals.append(k)
        d_vals.append(d)
    
    k = round(k_vals[-1], 2)
    d = round(d_vals[-1], 2)
    j = round(3 * k - 2 * d, 2)
    
    return {'K': k, 'D': d, 'J': j}


def calc_bollinger(closes, period=20, std_mult=2):
    """布林带"""
    if len(closes) < period:
        return None
    
    recent = closes[-period:]
    mid = sum(recent) / period
    variance = sum((x - mid) ** 2 for x in recent) / period
    std = math.sqrt(variance)
    
    upper = round(mid + std_mult * std, 3)
    lower = round(mid - std_mult * std, 3)
    mid = round(mid, 3)
    width = round((upper - lower) / mid * 100, 2)  # 带宽百分比
    
    current = closes[-1]
    position = round((current - lower) / (upper - lower) * 100, 1) if upper != lower else 50.0
    
    return {
        'upper': upper,
        'mid': mid,
        'lower': lower,
        'width_pct': width,
        'position': position,  # 0=下轨, 50=中轨, 100=上轨
        'signal': '超买' if position > 80 else ('超卖' if position < 20 else '中性'),
    }


def calc_macd(closes, fast=12, slow=26, signal=9):
    """MACD"""
    if len(closes) < slow + signal:
        return None
    
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = calc_ema(dif, signal)
    macd = [2 * (d - e) for d, e in zip(dif, dea)]
    
    # 金叉/死叉判断
    cross = 'none'
    if len(macd) >= 2:
        if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
            cross = 'golden'  # 金叉
        elif dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
            cross = 'death'  # 死叉
    
    return {
        'DIF': round(dif[-1], 3),
        'DEA': round(dea[-1], 3),
        'MACD': round(macd[-1], 3),
        'cross': cross,
        'histogram_trend': 'expanding' if len(macd) >= 2 and abs(macd[-1]) > abs(macd[-2]) else 'contracting',
    }


def calc_volume_ratio(volumes, period=5):
    """量比"""
    if len(volumes) < period + 1:
        return None
    avg = sum(volumes[-period-1:-1]) / period
    if avg == 0:
        return None
    return round(volumes[-1] / avg, 2)


def ma_system(closes):
    """均线系统分析"""
    mas = {}
    for p in [5, 10, 20, 60, 120]:
        if len(closes) >= p:
            mas[f'MA{p}'] = round(sum(closes[-p:]) / p, 3)
    
    if not mas:
        return None
    
    # 多头/空头排列
    ma_values = [(k, v) for k, v in sorted(mas.items(), key=lambda x: int(x[0][2:]))]
    
    bullish_order = all(ma_values[i][1] >= ma_values[i+1][1] 
                        for i in range(len(ma_values)-1))
    bearish_order = all(ma_values[i][1] <= ma_values[i+1][1] 
                        for i in range(len(ma_values)-1))
    
    arrangement = '多头排列' if bullish_order else ('空头排列' if bearish_order else '交叉/震荡')
    
    # 当前价相对均线
    current = closes[-1]
    above = [k for k, v in mas.items() if current > v]
    below = [k for k, v in mas.items() if current < v]
    
    return {
        'values': mas,
        'arrangement': arrangement,
        'price_above': above,
        'price_below': below,
        'support': min((v for k, v in mas.items() if v < current), default=None),
        'resistance': min((v for k, v in mas.items() if v > current), default=None),
    }


def detect_patterns(closes, highs, lows, volumes):
    """简易形态识别"""
    patterns = []
    n = len(closes)
    if n < 10:
        return patterns
    
    # 1. 放量突破
    if n >= 6:
        avg_vol = sum(volumes[-6:-1]) / 5
        if volumes[-1] > avg_vol * 1.8 and closes[-1] > max(closes[-6:-1]):
            patterns.append({'pattern': '放量突破', 'bias': 'bullish', 'strength': 'strong'})
    
    # 2. 缩量回调
    if n >= 6:
        avg_vol = sum(volumes[-6:-1]) / 5
        if volumes[-1] < avg_vol * 0.6 and closes[-1] < closes[-2]:
            patterns.append({'pattern': '缩量回调', 'bias': 'neutral_bullish', 'strength': 'moderate'})
    
    # 3. 天量见顶
    if n >= 20:
        max_vol_20 = max(volumes[-20:])
        if volumes[-1] == max_vol_20 and closes[-1] < closes[-2]:
            patterns.append({'pattern': '天量阴线', 'bias': 'bearish', 'strength': 'strong'})
    
    # 4. 十字星
    body = abs(closes[-1] - closes[-2]) if n >= 2 else 0
    shadow = highs[-1] - lows[-1]
    if shadow > 0 and body / shadow < 0.1:
        patterns.append({'pattern': '十字星', 'bias': 'reversal_signal', 'strength': 'moderate'})
    
    # 5. 连续阳线/阴线
    consecutive_up = 0
    for i in range(n-1, max(n-8, 0), -1):
        if closes[i] > closes[i-1]:
            consecutive_up += 1
        else:
            break
    if consecutive_up >= 4:
        patterns.append({'pattern': f'{consecutive_up}连阳', 'bias': 'bullish', 'strength': 'strong'})
    
    consecutive_down = 0
    for i in range(n-1, max(n-8, 0), -1):
        if closes[i] < closes[i-1]:
            consecutive_down += 1
        else:
            break
    if consecutive_down >= 4:
        patterns.append({'pattern': f'{consecutive_down}连阴', 'bias': 'bearish', 'strength': 'strong'})
    
    # 6. 底部放量
    if n >= 20:
        low_20 = min(lows[-20:])
        avg_vol = sum(volumes[-20:]) / 20
        if lows[-1] <= low_20 * 1.02 and volumes[-1] > avg_vol * 1.5:
            patterns.append({'pattern': '底部放量', 'bias': 'potential_reversal', 'strength': 'moderate'})
    
    return patterns


def analyze_single(code, is_index=False):
    """单只股票/指数技术分析"""
    result = {'code': code, 'analysis_time': datetime.now().isoformat()}
    
    try:
        df = _load_kline(code)
        min_required = 10 if code.startswith(('5', '15', '16', '56', '58')) else 20
        if df is None or len(df) < min_required:
            result['error'] = '数据不足'
            return result
        
        closes = df['收盘'].astype(float).tolist()
        highs = df['最高'].astype(float).tolist()
        lows = df['最低'].astype(float).tolist()
        volumes = df['成交量'].astype(float).tolist()
        
        result['latest'] = {
            'close': closes[-1],
            'high': highs[-1],
            'low': lows[-1],
            'volume': volumes[-1],
        }
        
        # 技术指标
        result['rsi_6'] = calc_rsi(closes, 6)
        result['rsi_14'] = calc_rsi(closes, 14)
        result['kdj'] = calc_kdj(highs, lows, closes)
        result['bollinger'] = calc_bollinger(closes)
        result['macd'] = calc_macd(closes)
        result['volume_ratio'] = calc_volume_ratio(volumes)
        result['ma_system'] = ma_system(closes)
        result['patterns'] = detect_patterns(closes, highs, lows, volumes)
        
        # 综合信号
        signals = []
        
        rsi14 = result.get('rsi_14')
        if rsi14 and rsi14 > 70:
            signals.append('⚠️ RSI超买')
        elif rsi14 and rsi14 < 30:
            signals.append('💡 RSI超卖')
        
        kdj = result.get('kdj')
        if kdj:
            if kdj['J'] > 100:
                signals.append('⚠️ KDJ超买')
            elif kdj['J'] < 0:
                signals.append('💡 KDJ超卖')
        
        boll = result.get('bollinger')
        if boll:
            if boll['position'] > 80:
                signals.append('⚠️ 触及布林上轨')
            elif boll['position'] < 20:
                signals.append('💡 触及布林下轨')
        
        macd = result.get('macd')
        if macd:
            if macd['cross'] == 'golden':
                signals.append('✅ MACD金叉')
            elif macd['cross'] == 'death':
                signals.append('❌ MACD死叉')
        
        vr = result.get('volume_ratio')
        if vr and vr > 2.0:
            signals.append(f'📢 量比{vr}（显著放量）')
        elif vr and vr < 0.5:
            signals.append(f'📉 量比{vr}（显著缩量）')
        
        result['signals'] = signals
        
    except Exception as e:
        result['error'] = str(e)
    
    return result


def analyze_indices():
    """主要指数技术分析"""
    indices = {
        'sh000001': '上证指数',
        'sz399001': '深证成指',
        'sz399006': '创业板指',
        'sh000688': '科创50',
        'sh000300': '沪深300',
    }
    results = {}
    for code, name in indices.items():
        print(f"分析 {name}...", file=sys.stderr)
        r = analyze_single(code, is_index=True)
        r['name'] = name
        results[code] = r
    return results


def analyze_watchlist():
    """自选股批量技术分析"""
    from scripts.utils.common import load_watchlist as _load_wl
    stocks = _load_wl()
    
    results = []
    for s in stocks:
        code = s if isinstance(s, str) else s.get('code', '')
        name = s.get('name', '') if isinstance(s, dict) else ''
        if code:
            print(f"分析 {code} {name}...", file=sys.stderr)
            r = analyze_single(code)
            r['name'] = name
            results.append(r)
    
    return results


def full_analysis():
    """指数 + 自选股"""
    return {
        'indices': analyze_indices(),
        'watchlist': analyze_watchlist(),
    }


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    
    if cmd == 'stock':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        if not code:
            print("Usage: tech_analysis.py stock <code>", file=sys.stderr)
            sys.exit(1)
        data = analyze_single(code)
    elif cmd == 'index':
        data = analyze_indices()
    elif cmd == 'watchlist':
        data = analyze_watchlist()
    elif cmd == 'all':
        data = full_analysis()
    else:
        print(f"Usage: {sys.argv[0]} [stock <code>|index|watchlist|all]", file=sys.stderr)
        sys.exit(1)
    
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
