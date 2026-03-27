#!/usr/bin/env python3
"""下周关键事件日历构建器

数据源：
1. 停复牌公告 (akshare news_trade_notify_suspend_baidu)
2. 分红除权日历 (akshare news_trade_notify_dividend_baidu)
3. 财报披露日历 — 观察池标的 (akshare stock_report_disclosure)
4. 已知宏观日程 — 固定周期事件（PMI/LPR/美联储等）

输出：JSON 到 stdout，供周报 collect 阶段捕获。
"""

import json
import sys
import concurrent.futures
from datetime import datetime, timedelta
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import scripts.utils.common  # noqa: F401 — IPv4 preference

try:
    import akshare as ak
except ImportError:
    print(json.dumps({"error": "akshare not installed", "events": []}))
    sys.exit(0)


def _ak_call(fn, timeout=15):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn).result(timeout=timeout)


def _load_watchlist():
    wl_path = _HERE / "watchlist.json"
    if not wl_path.exists():
        return []
    try:
        data = json.loads(wl_path.read_text(encoding="utf-8"))
        return [str(s.get("code", s) if isinstance(s, dict) else s)
                for s in data.get("stocks", [])]
    except Exception:
        return []


def get_suspend_events(start, end):
    """停复牌事件"""
    events = []
    try:
        df = _ak_call(lambda: ak.news_trade_notify_suspend_baidu())
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                events.append({
                    "date": str(row.get("停牌时间", ""))[:10],
                    "category": "停复牌",
                    "title": f"{row.get('股票简称', '')}({row.get('股票代码', '')}) 停牌",
                    "detail": str(row.get("停牌事项说明", "")),
                    "importance": 1,
                })
    except Exception:
        pass
    return events


def get_dividend_events(start, end):
    """分红除权事件"""
    events = []
    try:
        df = _ak_call(lambda: ak.news_trade_notify_dividend_baidu())
        if df is not None and not df.empty:
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")
            for _, row in df.iterrows():
                ex_date = str(row.get("除权日", ""))[:10]
                if start_str <= ex_date <= end_str:
                    events.append({
                        "date": ex_date,
                        "category": "分红除权",
                        "title": f"{row.get('股票简称', '')}({row.get('股票代码', '')}) 除权",
                        "detail": f"分红{row.get('分红', '')} 送股{row.get('送股', '')} 转增{row.get('转增', '')}",
                        "importance": 1,
                    })
    except Exception:
        pass
    return events


def get_known_macro_events(start, end):
    """固定周期宏观事件（基于日历规则推算）"""
    events = []
    d = start
    while d <= end:
        wd = d.weekday()  # 0=Mon
        day = d.day
        month = d.month
        ds = d.strftime("%Y-%m-%d")

        # 每月1日：财新制造业PMI
        if day == 1:
            events.append({
                "date": ds, "category": "中国宏观",
                "title": f"{month}月财新制造业PMI公布",
                "detail": "通常上午9:45发布", "importance": 2,
            })
        # 每月3日：财新服务业PMI
        if day == 3:
            events.append({
                "date": ds, "category": "中国宏观",
                "title": f"{month}月财新服务业PMI公布",
                "detail": "通常上午9:45发布", "importance": 2,
            })
        # 每月15日前后：中国CPI/PPI（通常9-12日）
        if day in (9, 10, 11, 12) and wd < 5:
            events.append({
                "date": ds, "category": "中国宏观",
                "title": f"{month}月CPI/PPI可能公布日",
                "detail": "统计局通常在每月9-12日发布", "importance": 2,
            })
        # 每月20日：LPR报价
        if day == 20:
            events.append({
                "date": ds, "category": "中国宏观",
                "title": f"{month}月LPR报价",
                "detail": "上午9:15发布", "importance": 2,
            })
        # FOMC 会议（大致：1/3/5/6/7/9/11/12月，通常月中周三）
        # 简化：标记每月第三个周三为潜在 FOMC 日
        if wd == 2 and 15 <= day <= 21 and month in (1, 3, 5, 6, 7, 9, 11, 12):
            events.append({
                "date": ds, "category": "全球宏观",
                "title": "美联储FOMC议息会议(潜在)",
                "detail": "北京时间次日凌晨2:00公布决议", "importance": 3,
            })
        # 美国非农就业：每月第一个周五
        if wd == 4 and day <= 7:
            events.append({
                "date": ds, "category": "全球宏观",
                "title": f"{month}月美国非农就业数据",
                "detail": "北京时间20:30发布", "importance": 3,
            })

        d += timedelta(days=1)

    return events


def get_fmp_earnings(start, end):
    """FMP 财报日历"""
    import subprocess
    events = []
    mcporter_config = str(_HERE / "config" / "mcporter.json")
    try:
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")
        proc = subprocess.run(
            ["mcporter", "call", "fmp.get_earnings_calendar",
             "--output", "json", "--config", mcporter_config,
             f"from={start_str}", f"to={end_str}"],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            if isinstance(data, list):
                for e in data[:20]:
                    symbol = e.get("symbol", "")
                    date = e.get("date", "")
                    eps_est = e.get("epsEstimated")
                    rev_est = e.get("revenueEstimated")
                    rev_str = f"${rev_est/1e9:.1f}B" if rev_est and rev_est > 1e9 else ""
                    events.append({
                        "date": date,
                        "category": "财报日历",
                        "title": f"{symbol} 财报发布",
                        "detail": f"EPS预期={eps_est} {rev_str}".strip(),
                        "importance": 2,
                    })
    except Exception:
        pass
    return events


def build_calendar():
    now = datetime.now()
    start = now
    end = now + timedelta(days=7)

    all_events = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(get_suspend_events, start, end): "停复牌",
            pool.submit(get_dividend_events, start, end): "分红除权",
            pool.submit(get_known_macro_events, start, end): "宏观日程",
            pool.submit(get_fmp_earnings, start, end): "财报日历",
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                all_events.extend(future.result())
            except Exception:
                pass

    # 按日期排序
    all_events.sort(key=lambda e: e.get("date", ""))

    return {
        "generated_at": now.isoformat(),
        "window": {
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        },
        "categories": ["全球宏观", "中国宏观", "停复牌", "分红除权", "财报日历"],
        "events": all_events,
        "event_count": len(all_events),
    }


if __name__ == "__main__":
    print(json.dumps(build_calendar(), ensure_ascii=False, indent=2))
