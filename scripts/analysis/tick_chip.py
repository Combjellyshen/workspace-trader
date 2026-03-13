#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tick_chip.py — A股分时成交爬虫 + 自建筹码分布 + 图像互证
用法:
  python3 scripts/analysis/tick_chip.py tick 002475
  python3 scripts/analysis/tick_chip.py big_order 002475
  python3 scripts/analysis/tick_chip.py chip 002475
  python3 scripts/analysis/tick_chip.py visual 002475
  python3 scripts/analysis/tick_chip.py batch
"""

import sys
import io
import json
import os
import time
import traceback
from datetime import datetime, timedelta
from collections import defaultdict

# UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests

# ─── market判断 ───────────────────────────────────────────────────────────────

def get_market(code: str) -> int:
    """返回 1=沪市, 0=深市"""
    code = code.strip()
    if code[:3] in ('600', '601', '603', '605', '688', '900'):
        return 1
    if code[:2] in ('51', '56', '58'):  # 上交所ETF
        return 1
    if code[:2] in ('15', '16'):        # 深交所ETF
        return 0
    if code[:2] in ('00', '30', '20'):
        return 0
    # 默认深市
    return 0

def get_sina_prefix(code: str) -> str:
    """用于新浪分时图 URL：sh/sz"""
    return 'sh' if get_market(code) == 1 else 'sz'

# ─── 分时成交爬虫 ──────────────────────────────────────────────────────────────

def fetch_ticks(code: str, pos: int = -5000) -> list:
    """
    从东方财富爬取分时成交明细
    返回 list of dict: {time, price, volume_hand, amount_k, direction}
    direction: 1=主买, 2=主卖, 4=集合竞价, 0=中性
    """
    market = get_market(code)
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/details/get"
        f"?fields1=f1,f2,f3,f4"
        f"&fields2=f51,f52,f53,f54,f55"
        f"&fltt=2&pos={pos}"
        f"&secid={market}.{code}"
        f"&ut=bd1d9ddb04089700cf9c27f6f7426281"
    )
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://quote.eastmoney.com/',
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        return []

    details = raw.get('data', {})
    if details is None:
        return []

    # 尝试拿股票名称/最新价
    details_list = details.get('details', [])
    ticks = []
    if not details_list:
        return ticks

    for item in details_list:
        parts = item.split(',')
        if len(parts) < 5:
            continue
        try:
            tick = {
                'time': parts[0],
                'price': float(parts[1]),
                'volume_hand': int(float(parts[2])),   # 手
                'amount_k': float(parts[3]),            # 金额(千元)
                'direction': int(parts[4]),             # 1主买 2主卖 4集竞 0中性
            }
            ticks.append(tick)
        except (ValueError, IndexError):
            continue
    return ticks

# ─── 命令: tick ────────────────────────────────────────────────────────────────

def cmd_tick(code: str):
    ticks = fetch_ticks(code)
    market = get_market(code)
    direction_map = {1: '主买', 2: '主卖', 4: '集竞', 0: '中性'}

    if not ticks:
        result = {
            'code': code,
            'market': '沪市' if market == 1 else '深市',
            'data': [],
            'signal': 'no_data',
            'interpretation': '当前无分时成交数据，可能为非交易时间或接口限流，已返回空数据。',
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 统计摘要
    total_buy = sum(t['volume_hand'] for t in ticks if t['direction'] == 1)
    total_sell = sum(t['volume_hand'] for t in ticks if t['direction'] == 2)
    net = total_buy - total_sell

    # 最近20条
    recent = ticks[-20:]
    for t in recent:
        t['direction_label'] = direction_map.get(t['direction'], '未知')

    signal = 'bullish' if net > 0 else ('bearish' if net < 0 else 'neutral')
    interpretation = (
        f"最近{len(ticks)}笔成交: 主买{total_buy}手, 主卖{total_sell}手, "
        f"净买入{net}手。{'主买占优，短线偏多。' if net>0 else '主卖占优，短线偏空。'}"
    )

    result = {
        'code': code,
        'market': '沪市' if market == 1 else '深市',
        'data': {
            'recent_ticks': recent,
            'summary': {
                'total_ticks': len(ticks),
                'total_buy_hand': total_buy,
                'total_sell_hand': total_sell,
                'net_buy_hand': net,
            }
        },
        'signal': signal,
        'interpretation': interpretation,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

# ─── 命令: big_order ──────────────────────────────────────────────────────────

BIG_ORDER_THRESHOLD = 500     # 手
SUPER_ORDER_THRESHOLD = 2000  # 手

def cmd_big_order(code: str):
    ticks = fetch_ticks(code, pos=-5000)
    market = get_market(code)

    if not ticks:
        result = {
            'code': code,
            'data': {},
            'signal': 'no_data',
            'interpretation': '当前无分时成交数据，可能为非交易时间。',
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 分类
    big_buy = [t for t in ticks if t['direction'] == 1 and t['volume_hand'] >= BIG_ORDER_THRESHOLD]
    big_sell = [t for t in ticks if t['direction'] == 2 and t['volume_hand'] >= BIG_ORDER_THRESHOLD]
    super_buy = [t for t in ticks if t['direction'] == 1 and t['volume_hand'] >= SUPER_ORDER_THRESHOLD]
    super_sell = [t for t in ticks if t['direction'] == 2 and t['volume_hand'] >= SUPER_ORDER_THRESHOLD]

    # 全天汇总
    all_buy = sum(t['volume_hand'] for t in ticks if t['direction'] == 1)
    all_sell = sum(t['volume_hand'] for t in ticks if t['direction'] == 2)
    all_net = all_buy - all_sell

    big_buy_vol = sum(t['volume_hand'] for t in big_buy)
    big_sell_vol = sum(t['volume_hand'] for t in big_sell)
    big_net = big_buy_vol - big_sell_vol

    super_buy_vol = sum(t['volume_hand'] for t in super_buy)
    super_sell_vol = sum(t['volume_hand'] for t in super_sell)

    # 大单价格区间（主力吸筹区）
    big_buy_prices = [t['price'] for t in big_buy]
    absorption_zone = None
    if big_buy_prices:
        absorption_zone = {
            'min': round(min(big_buy_prices), 3),
            'max': round(max(big_buy_prices), 3),
            'avg': round(sum(big_buy_prices) / len(big_buy_prices), 3),
        }

    # 按小时分布
    hourly = defaultdict(lambda: {'buy': 0, 'sell': 0, 'big_buy': 0, 'big_sell': 0})
    for t in ticks:
        try:
            hour = t['time'].split(':')[0]
        except Exception:
            hour = '??'
        if t['direction'] == 1:
            hourly[hour]['buy'] += t['volume_hand']
            if t['volume_hand'] >= BIG_ORDER_THRESHOLD:
                hourly[hour]['big_buy'] += t['volume_hand']
        elif t['direction'] == 2:
            hourly[hour]['sell'] += t['volume_hand']
            if t['volume_hand'] >= BIG_ORDER_THRESHOLD:
                hourly[hour]['big_sell'] += t['volume_hand']

    # 信号判断
    if big_net > 5000:
        signal = 'strong_inflow'
    elif big_net > 1000:
        signal = 'mild_inflow'
    elif big_net < -5000:
        signal = 'strong_outflow'
    elif big_net < -1000:
        signal = 'mild_outflow'
    else:
        signal = 'neutral'

    interpretation_parts = [
        f"全天主买{all_buy}手，主卖{all_sell}手，净买{all_net}手。",
        f"大单(≥{BIG_ORDER_THRESHOLD}手): 净流入{big_net}手 (买{big_buy_vol}手/卖{big_sell_vol}手)。",
        f"超大单(≥{SUPER_ORDER_THRESHOLD}手): 买{super_buy_vol}手/卖{super_sell_vol}手。",
    ]
    if absorption_zone:
        interpretation_parts.append(
            f"主力大单吸筹区间: {absorption_zone['min']}-{absorption_zone['max']}元"
            f"（均价{absorption_zone['avg']}元）。"
        )
    if big_net > 0:
        interpretation_parts.append("大单净流入为正，主力整体买入倾向。")
    else:
        interpretation_parts.append("大单净流出，主力整体卖出倾向或无明显主力介入。")

    result = {
        'code': code,
        'market': '沪市' if market == 1 else '深市',
        'data': {
            'summary': {
                'all_buy_hand': all_buy,
                'all_sell_hand': all_sell,
                'all_net_hand': all_net,
                'big_buy_hand': big_buy_vol,
                'big_sell_hand': big_sell_vol,
                'big_net_hand': big_net,
                'super_buy_hand': super_buy_vol,
                'super_sell_hand': super_sell_vol,
                'big_order_threshold': BIG_ORDER_THRESHOLD,
                'super_order_threshold': SUPER_ORDER_THRESHOLD,
            },
            'absorption_zone': absorption_zone,
            'hourly_distribution': dict(hourly),
            'big_buy_orders': big_buy[:10],   # 最多返回前10条
            'big_sell_orders': big_sell[:10],
        },
        'signal': signal,
        'interpretation': ' '.join(interpretation_parts),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

# ─── 命令: chip (筹码分布) ────────────────────────────────────────────────────

def _fetch_kline(code: str, days: int):
    """
    获取日K线，自动判断股票/ETF，返回标准化DataFrame
    列: date, open, high, low, close, volume, turnover (小数形式，如0.015=1.5%)
    """
    import akshare as ak

    market = get_market(code)
    prefix = 'sh' if market == 1 else 'sz'

    errors = []

    # 方案1: stock_zh_a_daily (股票，包含换手率)
    try:
        df = ak.stock_zh_a_daily(symbol=f'{prefix}{code}', adjust='qfq')
        if df is not None and len(df) > 0 and 'turnover' in df.columns:
            df = df.tail(days).reset_index(drop=True)
            # turnover已经是小数形式
            return df
    except Exception as e:
        errors.append(f'stock_zh_a_daily: {e}')

    # 方案2: fund_etf_hist_sina (ETF，无换手率，用成交量估算)
    try:
        df = ak.fund_etf_hist_sina(symbol=f'{prefix}{code}')
        if df is not None and len(df) > 0:
            df = df.tail(days).reset_index(drop=True)
            # 没有换手率，用成交量相对均值估算（粗略）
            vol_mean = df['volume'].mean()
            if vol_mean > 0:
                df['turnover'] = (df['volume'] / vol_mean * 0.02).clip(0.001, 0.5)
            else:
                df['turnover'] = 0.02
            return df
    except Exception as e:
        errors.append(f'fund_etf_hist_sina: {e}')

    # 方案3: stock_zh_a_hist (可能被封，作为备用)
    try:
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days + 10)).strftime('%Y%m%d')
        df = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start_date, end_date=end_date, adjust='qfq')
        if df is not None and len(df) > 0:
            # 标准化列名
            col_map = {}
            for col in df.columns:
                if '最高' in col: col_map[col] = 'high'
                elif '最低' in col: col_map[col] = 'low'
                elif '收盘' in col: col_map[col] = 'close'
                elif '开盘' in col: col_map[col] = 'open'
                elif '换手' in col: col_map[col] = 'turnover'
                elif '成交量' in col: col_map[col] = 'volume'
                elif '日期' in col: col_map[col] = 'date'
            df = df.rename(columns=col_map).tail(days).reset_index(drop=True)
            if 'turnover' in df.columns:
                # 如果是百分比形式转小数
                if df['turnover'].mean() > 1:
                    df['turnover'] = df['turnover'] / 100.0
            return df
    except Exception as e:
        errors.append(f'stock_zh_a_hist: {e}')

    raise RuntimeError(' | '.join(errors))


def cmd_chip(code: str, days: int = 60):
    """
    用akshare获取近N日日K线，自建筹码分布：
    - 每天筹码均匀分布在[low, high]
    - 历史筹码每天按(1-换手率)衰减
    """
    try:
        import akshare as ak
    except ImportError:
        print(json.dumps({'error': 'akshare未安装', 'signal': 'error', 'interpretation': '请pip install akshare'}, ensure_ascii=False))
        return

    try:
        df = _fetch_kline(code, days)
    except Exception as e:
        result = {
            'code': code,
            'data': {},
            'signal': 'error',
            'interpretation': f'获取K线失败: {e}',
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if df is None or len(df) == 0:
        result = {
            'code': code,
            'data': {},
            'signal': 'no_data',
            'interpretation': '未获取到K线数据，请检查股票代码。',
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 确保关键列存在
    required = ['high', 'low', 'close', 'turnover']
    missing = [c for c in required if c not in df.columns]
    if missing:
        result = {
            'code': code,
            'data': {'columns': list(df.columns)},
            'signal': 'error',
            'interpretation': f'K线数据缺少列: {missing}，原列: {list(df.columns)}',
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # ── 构建筹码分布 ──
    # 价格区间: 用所有low/high的范围
    import numpy as np

    price_min = float(df['low'].min())
    price_max = float(df['high'].max())
    bins = 100  # 价格分成100个桶
    price_levels = np.linspace(price_min, price_max, bins + 1)
    chip_dist = np.zeros(bins)  # 每个价格桶的筹码量（归一化）

    for _, row in df.iterrows():
        try:
            low = float(row['low'])
            high = float(row['high'])
            turnover = float(row['turnover'])  # 已是小数形式 (0.015 = 1.5%)
            if turnover <= 0 or turnover > 1:
                turnover = min(max(turnover, 0.001), 0.999)
        except (ValueError, TypeError):
            continue

        # 旧筹码衰减
        chip_dist *= (1 - turnover)

        # 今天新增筹码均匀分布在[low, high]
        # 找覆盖的桶
        for i in range(bins):
            bucket_low = price_levels[i]
            bucket_high = price_levels[i + 1]
            # 重叠长度
            overlap = max(0, min(high, bucket_high) - max(low, bucket_low))
            if overlap > 0 and (high - low) > 0:
                fraction = overlap / (high - low)
                chip_dist[i] += turnover * fraction

    # 归一化
    total_chip = chip_dist.sum()
    if total_chip > 0:
        chip_dist_norm = chip_dist / total_chip
    else:
        chip_dist_norm = chip_dist

    # 当前收盘价
    current_price = float(df['close'].iloc[-1])

    # 获利盘比例: 价格 <= 当前价的筹码占比
    profitable_ratio = 0.0
    for i in range(bins):
        mid = (price_levels[i] + price_levels[i + 1]) / 2
        if mid <= current_price:
            profitable_ratio += chip_dist_norm[i]
    profitable_ratio = round(float(profitable_ratio) * 100, 2)

    # 主要套牢区: 当前价以上的筹码密集区 (top3 buckets)
    trapped_buckets = []
    for i in range(bins):
        mid = (price_levels[i] + price_levels[i + 1]) / 2
        if mid > current_price:
            trapped_buckets.append((float(chip_dist_norm[i]), round(float(mid), 3)))
    trapped_buckets.sort(reverse=True)
    trapped_zones = [{'price': p, 'chip_pct': round(c * 100, 3)} for c, p in trapped_buckets[:5]]

    # 主要支撑区: 当前价以下的筹码密集区 (top5 buckets)
    support_buckets = []
    for i in range(bins):
        mid = (price_levels[i] + price_levels[i + 1]) / 2
        if mid < current_price:
            support_buckets.append((float(chip_dist_norm[i]), round(float(mid), 3)))
    support_buckets.sort(reverse=True)
    support_zones = [{'price': p, 'chip_pct': round(c * 100, 3)} for c, p in support_buckets[:5]]

    # 筹码峰 (top10)
    all_buckets = []
    for i in range(bins):
        mid = (price_levels[i] + price_levels[i + 1]) / 2
        all_buckets.append({'price': round(float(mid), 3), 'chip_pct': round(float(chip_dist_norm[i]) * 100, 3)})
    all_buckets.sort(key=lambda x: x['chip_pct'], reverse=True)
    top_chip_zones = all_buckets[:10]

    # 信号
    if profitable_ratio >= 80:
        signal = 'high_profit_ratio'
        interpretation_signal = f"获利盘{profitable_ratio}%较高，有获利了结压力。"
    elif profitable_ratio <= 30:
        signal = 'high_trapped_ratio'
        interpretation_signal = f"获利盘仅{profitable_ratio}%，大量套牢盘，上方阻力大。"
    else:
        signal = 'neutral'
        interpretation_signal = f"获利盘{profitable_ratio}%，筹码分布较均衡。"

    if support_zones:
        support_str = '、'.join([f"{z['price']}元({z['chip_pct']}%)" for z in support_zones[:3]])
        interpretation_signal += f" 主要支撑区: {support_str}。"
    if trapped_zones:
        trapped_str = '、'.join([f"{z['price']}元({z['chip_pct']}%)" for z in trapped_zones[:3]])
        interpretation_signal += f" 主要套牢区: {trapped_str}。"

    result = {
        'code': code,
        'data': {
            'current_price': current_price,
            'price_range': {'min': round(price_min, 3), 'max': round(price_max, 3)},
            'profitable_ratio_pct': profitable_ratio,
            'support_zones': support_zones,
            'trapped_zones': trapped_zones,
            'top_chip_zones': top_chip_zones,
            'days_used': len(df),
        },
        'signal': signal,
        'interpretation': interpretation_signal,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

# ─── 命令: visual (图像互证) ──────────────────────────────────────────────────

def cmd_visual(code: str):
    """
    下载新浪分时图，用PIL分析颜色分布提取量能形态，与tick数据交叉验证
    """
    try:
        from PIL import Image
        import io as _io
    except ImportError:
        print(json.dumps({'error': 'Pillow未安装', 'signal': 'error', 'interpretation': '请pip install Pillow'}, ensure_ascii=False))
        return

    prefix = get_sina_prefix(code)
    url = f"https://image.sinajs.cn/newchart/min/n/{prefix}{code}.gif"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://finance.sina.com.cn/',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        img = Image.open(_io.BytesIO(resp.content)).convert('RGB')
    except Exception as e:
        result = {
            'code': code,
            'image_url': url,
            'data': {},
            'signal': 'error',
            'interpretation': f'图片下载或解析失败: {e}',
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    width, height = img.size
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter('ignore', DeprecationWarning)
        pixels = list(img.getdata())
    total_pixels = len(pixels)

    # 颜色统计: 红色(涨/买), 绿色(跌/卖), 白/灰(背景), 其他
    red_count = 0
    green_count = 0
    bg_count = 0
    other_count = 0

    for r, g, b in pixels:
        # 红色: R >> G, R >> B
        if r > 150 and r > g * 1.5 and r > b * 1.5:
            red_count += 1
        # 绿色: G >> R, G >> B (中国股市绿色=下跌)
        elif g > 150 and g > r * 1.5 and g > b * 1.2:
            green_count += 1
        # 背景色: 接近白/灰/黑
        elif abs(r - g) < 30 and abs(g - b) < 30 and abs(r - b) < 30:
            bg_count += 1
        else:
            other_count += 1

    red_pct = round(red_count / total_pixels * 100, 2)
    green_pct = round(green_count / total_pixels * 100, 2)
    other_pct = round(other_count / total_pixels * 100, 2)

    # 量能形态判断
    if red_pct > green_pct * 1.5:
        visual_signal = 'bullish_visual'
        visual_desc = f"图像红色主导({red_pct}% vs 绿色{green_pct}%)，视觉上买盘偏多。"
    elif green_pct > red_pct * 1.5:
        visual_signal = 'bearish_visual'
        visual_desc = f"图像绿色主导({green_pct}% vs 红色{red_pct}%)，视觉上卖盘偏多。"
    else:
        visual_signal = 'mixed_visual'
        visual_desc = f"图像红绿相当(红{red_pct}%/绿{green_pct}%)，多空均衡。"

    # 与tick数据交叉验证
    ticks = fetch_ticks(code)
    cross_validation = {}
    cross_signal = 'no_cross_data'
    cross_interpretation = '无tick数据，无法交叉验证。'

    if ticks:
        tick_buy = sum(t['volume_hand'] for t in ticks if t['direction'] == 1)
        tick_sell = sum(t['volume_hand'] for t in ticks if t['direction'] == 2)
        total_tick = tick_buy + tick_sell
        tick_buy_pct = round(tick_buy / total_tick * 100, 2) if total_tick > 0 else 0
        tick_sell_pct = round(tick_sell / total_tick * 100, 2) if total_tick > 0 else 0

        cross_validation = {
            'tick_buy_pct': tick_buy_pct,
            'tick_sell_pct': tick_sell_pct,
            'visual_red_pct': red_pct,
            'visual_green_pct': green_pct,
        }

        # 对倒识别: 图像红柱多(视觉买盘)但tick主卖占多 → 可能对倒
        if red_pct > green_pct and tick_sell_pct > 55:
            cross_signal = 'possible_wash_trade'
            cross_interpretation = (
                f"⚠️ 图像显示红柱偏多({red_pct}%)，但tick主卖占比{tick_sell_pct}%偏高，"
                f"可能存在对倒行为（视觉上涨/实际卖出）。"
            )
        elif green_pct > red_pct and tick_buy_pct > 55:
            cross_signal = 'possible_suppression'
            cross_interpretation = (
                f"⚠️ 图像绿色偏多({green_pct}%)，但tick主买占比{tick_buy_pct}%较高，"
                f"可能存在打压吸筹行为。"
            )
        elif (red_pct > green_pct and tick_buy_pct > 50) or (green_pct > red_pct and tick_sell_pct > 50):
            cross_signal = 'consistent'
            cross_interpretation = f"图像与tick数据方向一致，无明显异常。"
        else:
            cross_signal = 'neutral'
            cross_interpretation = "图像与tick数据多空信号基本中性，无明显异常。"

    result = {
        'code': code,
        'image_url': url,
        'image_size': {'width': width, 'height': height},
        'data': {
            'color_distribution': {
                'red_pct': red_pct,
                'green_pct': green_pct,
                'background_pct': round(bg_count / total_pixels * 100, 2),
                'other_pct': other_pct,
            },
            'visual_signal': visual_signal,
            'cross_validation': cross_validation,
        },
        'signal': cross_signal,
        'interpretation': f"{visual_desc} 交叉验证: {cross_interpretation}",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

# ─── 命令: batch ──────────────────────────────────────────────────────────────

def cmd_batch():
    """读取watchlist.json，对所有股票执行big_order+chip"""
    workspace = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    watchlist_path = os.path.join(workspace, 'watchlist.json')

    try:
        with open(watchlist_path, 'r', encoding='utf-8') as f:
            watchlist = json.load(f)
    except Exception as e:
        print(json.dumps({
            'error': f'读取watchlist.json失败: {e}',
            'signal': 'error',
            'interpretation': f'请检查 {watchlist_path} 是否存在。',
        }, ensure_ascii=False, indent=2))
        return

    stocks = watchlist.get('stocks', [])
    if not stocks:
        print(json.dumps({'error': '自选股列表为空', 'signal': 'no_data', 'interpretation': '请先在watchlist.json中添加股票。'}, ensure_ascii=False))
        return

    results = []
    for stock in stocks:
        code = stock.get('code', '')
        name = stock.get('name', code)
        if not code:
            continue

        # big_order
        ticks = fetch_ticks(code, pos=-5000)
        market = get_market(code)

        big_order_data = {}
        big_order_signal = 'no_data'
        if ticks:
            big_buy = [t for t in ticks if t['direction'] == 1 and t['volume_hand'] >= BIG_ORDER_THRESHOLD]
            big_sell = [t for t in ticks if t['direction'] == 2 and t['volume_hand'] >= BIG_ORDER_THRESHOLD]
            big_buy_vol = sum(t['volume_hand'] for t in big_buy)
            big_sell_vol = sum(t['volume_hand'] for t in big_sell)
            big_net = big_buy_vol - big_sell_vol
            all_buy = sum(t['volume_hand'] for t in ticks if t['direction'] == 1)
            all_sell = sum(t['volume_hand'] for t in ticks if t['direction'] == 2)
            big_order_data = {
                'all_net_hand': all_buy - all_sell,
                'big_net_hand': big_net,
                'big_buy_hand': big_buy_vol,
                'big_sell_hand': big_sell_vol,
            }
            if big_net > 1000:
                big_order_signal = 'inflow'
            elif big_net < -1000:
                big_order_signal = 'outflow'
            else:
                big_order_signal = 'neutral'

        # chip (简化版，避免重复输出)
        chip_data = {}
        chip_signal = 'no_data'
        try:
            import numpy as np
            days_chip = 60
            df = _fetch_kline(code, days_chip)

            if df is not None and len(df) > 0:
                if all(c in df.columns for c in ['high', 'low', 'close', 'turnover']):
                    price_min = float(df['low'].min())
                    price_max = float(df['high'].max())
                    bins = 100
                    price_levels = np.linspace(price_min, price_max, bins + 1)
                    chip_dist = np.zeros(bins)

                    for _, row in df.iterrows():
                        try:
                            low = float(row['low'])
                            high = float(row['high'])
                            turnover = float(row['turnover'])  # 已是小数形式
                            if turnover <= 0 or turnover > 1:
                                turnover = min(max(turnover, 0.001), 0.999)
                        except Exception:
                            continue
                        chip_dist *= (1 - turnover)
                        for i in range(bins):
                            bl = price_levels[i]
                            bh = price_levels[i + 1]
                            overlap = max(0, min(high, bh) - max(low, bl))
                            if overlap > 0 and (high - low) > 0:
                                chip_dist[i] += turnover * overlap / (high - low)

                    total = chip_dist.sum()
                    if total > 0:
                        chip_dist /= total

                    current_price = float(df['close'].iloc[-1])
                    profitable_ratio = sum(chip_dist[i] for i in range(bins)
                                          if (price_levels[i] + price_levels[i+1]) / 2 <= current_price)
                    profitable_ratio = round(float(profitable_ratio) * 100, 2)

                    chip_data = {
                        'current_price': current_price,
                        'profitable_ratio_pct': profitable_ratio,
                    }
                    if profitable_ratio >= 80:
                        chip_signal = 'high_profit_ratio'
                    elif profitable_ratio <= 30:
                        chip_signal = 'high_trapped_ratio'
                    else:
                        chip_signal = 'neutral'
        except Exception as e:
            chip_signal = f'error: {e}'

        results.append({
            'code': code,
            'name': name,
            'market': '沪市' if market == 1 else '深市',
            'big_order': {
                'data': big_order_data,
                'signal': big_order_signal,
            },
            'chip': {
                'data': chip_data,
                'signal': chip_signal,
            },
        })

    output = {
        'batch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(results),
        'data': results,
        'signal': 'batch_complete',
        'interpretation': f'批量分析完成，共{len(results)}只股票。',
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

# ─── 主入口 ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python3 tick_chip.py <command> [code]")
        print("命令: tick, big_order, chip, visual, batch")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == 'batch':
        cmd_batch()
    elif cmd in ('tick', 'big_order', 'chip', 'visual'):
        if len(sys.argv) < 3:
            print(json.dumps({
                'error': f'命令 {cmd} 需要股票代码参数',
                'signal': 'error',
                'interpretation': f'用法: python3 tick_chip.py {cmd} <code>',
            }, ensure_ascii=False))
            sys.exit(1)
        code = sys.argv[2].strip()
        if cmd == 'tick':
            cmd_tick(code)
        elif cmd == 'big_order':
            cmd_big_order(code)
        elif cmd == 'chip':
            cmd_chip(code)
        elif cmd == 'visual':
            cmd_visual(code)
    else:
        print(json.dumps({
            'error': f'未知命令: {cmd}',
            'signal': 'error',
            'interpretation': '支持的命令: tick, big_order, chip, visual, batch',
        }, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
