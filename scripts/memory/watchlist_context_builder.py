#!/usr/bin/env python3
"""自选池上下文构建器（深度模式摘要）"""
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.analysis.watchlist_deep_dive import build_watchlist_deep_dive  # noqa: E402


def build_context():
    """输出适合周报/日报引用的自选池摘要上下文。"""
    return build_watchlist_deep_dive(condensed=True)


if __name__ == '__main__':
    print(json.dumps(build_context(), ensure_ascii=False, indent=2))
