#!/usr/bin/env python3
"""跨源数据一致性校验器

用途：
- 校验 price / pre_close / pct_chg 三者是否自洽
- 校验 intraday_alert 与 deep_data 的自选股快照是否冲突
- 输出 ok / warning / error 与推荐信任源

用法：
  python3 scripts/utils/data_consistency_guard.py
  python3 scripts/utils/data_consistency_guard.py --json
"""
import json
import math
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from scripts.utils.common import safe_float, WORKSPACE_ROOT  # noqa: E402
from scripts.data import deep_data  # type: ignore
from scripts.analysis import intraday_alert  # type: ignore


def compare_watchlist():
    result = {
        'status': 'ok',
        'issues': [],
        'source_priority': ['deep_data.watchlist_realtime', 'intraday_alert.snapshot'],
        'recommended_source': 'deep_data.watchlist_realtime'
    }

    watchlist_path = WORKSPACE / 'watchlist.json'
    try:
        watchlist = json.loads(watchlist_path.read_text(encoding='utf-8')).get('stocks', [])
    except Exception:
        watchlist = []

    if not watchlist:
        result['status'] = 'no_data'
        result['note'] = 'watchlist 为空，跳过跨源一致性校验。'
        return result

    intraday = intraday_alert.run_check()
    deep = deep_data.full_snapshot()

    intraday_snapshot = intraday.get('snapshot', {})
    market_closed = intraday_snapshot.get('sh_index', {}).get('status') == 'market_closed'
    intra_map = {s.get('code'): s for s in intraday_snapshot.get('stocks', [])}
    deep_map = {}
    for s in deep.get('watchlist_realtime', []):
        code = str(s.get('code', ''))[-6:]
        deep_map[code] = s

    for code, ds in deep_map.items():
        isnap = intra_map.get(code)
        if not isnap:
            result['issues'].append({
                'level': 'warning',
                'code': code,
                'type': 'missing_in_intraday',
                'detail': 'deep_data 有该股票，但 intraday_alert 未返回'
            })
            continue

        deep_price = safe_float(ds.get('close'))
        deep_pre = safe_float(ds.get('pre_close'))
        deep_pct = safe_float(ds.get('pct_chg'))
        intra_price = safe_float(isnap.get('price'))
        intra_pct = safe_float(isnap.get('change_pct'))

        intraday_placeholder = (
            market_closed and intra_price == 0 and intra_pct == 0
        ) or (
            intra_price == 0 and abs(intra_pct) < 0.01 and deep_price > 0
        )

        if deep_pre > 0:
            calc_pct = round((deep_price - deep_pre) / deep_pre * 100, 2)
            if abs(calc_pct - deep_pct) > 0.5:
                result['issues'].append({
                    'level': 'error',
                    'code': code,
                    'type': 'deep_data_internal_mismatch',
                    'detail': f'deep_data 内部不自洽: 价格推导 {calc_pct:+.2f}% vs 提供值 {deep_pct:+.2f}%'
                })

        if intraday_placeholder:
            result['issues'].append({
                'level': 'warning',
                'code': code,
                'type': 'intraday_placeholder',
                'detail': 'intraday 返回的是非交易时段/占位值(0,0)，已跳过与 deep_data 的硬冲突判定'
            })
            continue

        if abs(deep_price - intra_price) > max(0.03, deep_price * 0.003):
            result['issues'].append({
                'level': 'error',
                'code': code,
                'type': 'price_conflict',
                'detail': f'价格冲突: intraday={intra_price}, deep_data={deep_price}'
            })

        if (deep_pct > 0 and intra_pct < 0) or (deep_pct < 0 and intra_pct > 0):
            result['issues'].append({
                'level': 'error',
                'code': code,
                'type': 'direction_conflict',
                'detail': f'涨跌方向冲突: intraday={intra_pct:+.2f}% vs deep_data={deep_pct:+.2f}%'
            })
        elif abs(deep_pct - intra_pct) > 1.5:
            result['issues'].append({
                'level': 'warning',
                'code': code,
                'type': 'pct_gap',
                'detail': f'涨跌幅偏差较大: intraday={intra_pct:+.2f}% vs deep_data={deep_pct:+.2f}%'
            })

    levels = [i['level'] for i in result['issues']]
    if 'error' in levels:
        result['status'] = 'error'
    elif 'warning' in levels:
        result['status'] = 'warning'

    return result


def main():
    out = compare_watchlist()
    if '--json' in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"status={out['status']}")
        print(f"recommended_source={out['recommended_source']}")
        if not out['issues']:
            print('一致性检查通过')
        for issue in out['issues']:
            print(f"[{issue['level']}] {issue['code']} {issue['type']}: {issue['detail']}")


if __name__ == '__main__':
    main()
