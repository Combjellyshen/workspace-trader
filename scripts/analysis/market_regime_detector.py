#!/usr/bin/env python3
"""市场状态识别器（轻量规则版）"""
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.data import deep_data  # type: ignore


def detect():
    snap = deep_data.full_snapshot()
    indices = {x['code']: x for x in snap.get('indices', [])}
    sh = float(indices.get('sh000001', {}).get('pct_chg', 0) or 0)
    sz = float(indices.get('sz399001', {}).get('pct_chg', 0) or 0)
    cyb = float(indices.get('sz399006', {}).get('pct_chg', 0) or 0)

    if cyb > sh + 1.0 and sz > sh:
        regime = '成长进攻 / 风险偏好提升'
    elif sh > 1.0 and abs(cyb - sh) < 0.8:
        regime = '指数共振上行'
    elif sh < -1.0 and cyb < -1.0:
        regime = '系统性风险释放'
    else:
        regime = '震荡轮动 / 结构性行情'

    return {
        'regime': regime,
        'inputs': {'sh000001_pct': sh, 'sz399001_pct': sz, 'sz399006_pct': cyb},
        'note': '当前为轻量规则版，可继续接入 market_breadth / sentiment / northbound 做更稳的 regime 识别。'
    }

if __name__ == '__main__':
    print(json.dumps(detect(), ensure_ascii=False, indent=2))
