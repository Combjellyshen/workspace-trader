#!/usr/bin/env python3
"""下周关键事件日历构建器（骨架版）"""
import json
from datetime import datetime, timedelta

start = datetime.now()
end = start + timedelta(days=7)

calendar = {
    'generated_at': datetime.now().isoformat(),
    'window': {
        'start': start.strftime('%Y-%m-%d'),
        'end': end.strftime('%Y-%m-%d')
    },
    'categories': [
        '全球宏观', '中国宏观', '财报日历', '政策会议', '行业催化'
    ],
    'events': [],
    'note': '建议由周报任务通过 MCP fmp calendar / economics + web_search 动态补充具体事件。'
}

print(json.dumps(calendar, ensure_ascii=False, indent=2))
