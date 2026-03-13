#!/usr/bin/env python3
"""自选池上下文构建器（组合视角）"""
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import load_watchlist  # noqa: E402
from scripts.data import deep_data  # type: ignore


def build_context():
    watchlist = load_watchlist()
    snap = deep_data.full_snapshot()
    realtime = {str(x.get('code', ''))[-6:]: x for x in snap.get('watchlist_realtime', [])}

    items = []
    for s in watchlist:
        code = s['code'] if isinstance(s, dict) else str(s)
        rt = realtime.get(code, {})
        items.append({
            'code': code,
            'name': s.get('name', '') if isinstance(s, dict) else '',
            'pct_chg': rt.get('pct_chg'),
            'price': rt.get('close')
        })

    return {
        'watchlist_count': len(watchlist),
        'items': items,
        'summary_hint': '先总结自选池整体收益/回撤、风格暴露、被强化/被削弱的逻辑，再展开逐只分析。'
    }


if __name__ == '__main__':
    print(json.dumps(build_context(), ensure_ascii=False, indent=2))
