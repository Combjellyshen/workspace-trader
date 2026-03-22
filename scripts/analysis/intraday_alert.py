#!/usr/bin/env python3
"""
intraday_alert.py — 盘中异动检测引擎
纯数据计算，不调用模型，消耗接近0 token。

用法:
  python3 scripts/analysis/intraday_alert.py check      # 计算当前异动评分，输出JSON
  python3 scripts/analysis/intraday_alert.py snapshot   # 存快照到 data/intraday/YYYY-MM-DD-HHMM.json
"""

import sys
import json
import os
import glob
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

_HERE = Path(__file__).resolve().parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from scripts.utils.common import safe_float, load_watchlist, WORKSPACE_ROOT  # noqa: E402

# ─── 路径配置 ─────────────────────────────────────────────────────────────────
WORKSPACE = WORKSPACE_ROOT
WATCHLIST_PATH = WORKSPACE / "watchlist.json"
INTRADAY_DIR = WORKSPACE / "data" / "intraday"
SH_TZ = ZoneInfo("Asia/Shanghai")

# ─── 工具函数 ────────────────────────────────────────────────────────────────

def now_shanghai() -> datetime:
    return datetime.now(SH_TZ)


def warn(msg: str):
    print(f"[intraday_alert] {msg}", file=sys.stderr)


def infer_market(code: str) -> str:
    code = str(code)
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("8", "4")):
        return "bj"
    return "sz"


def pick_col(columns, exact_names=None, contains=None, exclude=None):
    """按优先级挑列，优先精确匹配，避免把振幅/5分钟涨跌幅误当成涨跌幅。"""
    exact_names = exact_names or []
    contains = contains or []
    exclude = exclude or []

    cols = list(columns)
    for name in exact_names:
        if name in cols:
            return name

    for col in cols:
        if all(token in col for token in contains) and not any(token in col for token in exclude):
            return col

    return None


# ─── 维度1：自选股价格异动 ───────────────────────────────────────────────────

def check_price_alerts(watchlist):
    """
    用 ak.stock_zh_a_spot_em() 获取全市场实时行情，筛出自选股，检测涨跌幅。
    返回 (alerts, stocks_snapshot)
    """
    alerts = []
    stocks_snapshot = []

    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()

        # 建立自选股 code → name 映射
        watchlist_codes = {s["code"]: s["name"] for s in watchlist}

        # 精确适配列名，避免误用“振幅”“5分钟涨跌幅”“60日涨跌幅”等字段
        code_col = pick_col(df.columns, exact_names=["代码", "证券代码"], contains=["代码"])
        name_col = pick_col(df.columns, exact_names=["名称", "证券简称"], contains=["名称"])
        pct_col = pick_col(df.columns, exact_names=["涨跌幅"], contains=["涨跌", "幅"], exclude=["5分钟", "3日", "5日", "10日", "20日", "60日", "年初", "振幅"])
        price_col = pick_col(df.columns, exact_names=["最新价", "当前价", "现价"], contains=["价"], exclude=["最高", "最低", "今开", "昨收", "参考"])
        pre_close_col = pick_col(df.columns, exact_names=["昨收", "昨日收盘", "前收盘"], contains=["昨收"])

        if not code_col:
            return alerts, stocks_snapshot

        # 筛出自选股
        df_watch = df[df[code_col].astype(str).str.zfill(6).isin(watchlist_codes.keys())]

        for _, row in df_watch.iterrows():
            code = str(row[code_col]).zfill(6)
            name = watchlist_codes.get(code, row.get(name_col, "") if name_col else "")
            price = safe_float(row.get(price_col, 0)) if price_col else 0.0
            pct = safe_float(row.get(pct_col, 0)) if pct_col else 0.0
            pre_close = safe_float(row.get(pre_close_col, 0)) if pre_close_col else 0.0

            # 若涨跌幅列异常，但价格/昨收可用，则重新计算
            if pre_close > 0:
                calc_pct = round((price - pre_close) / pre_close * 100, 2)
                if abs(pct - calc_pct) > 3.0:
                    pct = calc_pct

            stock_info = {
                "code": code,
                "name": name,
                "price": price,
                "change_pct": round(pct, 2),
                "main_flow_yi": 0.0  # 后续由维度4填充
            }
            stocks_snapshot.append(stock_info)

            # 判断异动
            level = None
            detail = ""
            if pct > 5:
                level = "red"
                detail = f"涨幅 +{pct:.1f}%，强势拉升"
            elif pct > 3:
                level = "yellow"
                detail = f"涨幅 +{pct:.1f}%，突破前高"
            elif pct < -5:
                level = "red"
                detail = f"跌幅 {pct:.1f}%，大幅下跌"
            elif pct < -3:
                level = "yellow"
                detail = f"跌幅 {pct:.1f}%，跌势明显"

            if level:
                alerts.append({
                    "type": "price",
                    "code": code,
                    "name": name,
                    "level": level,
                    "detail": detail,
                    "action_hint": "关注量能是否配合" if pct > 0 else "关注是否跌破支撑位"
                })

        # 补全未出现在行情中的自选股
        returned_codes = {s["code"] for s in stocks_snapshot}
        for s in watchlist:
            if s["code"] not in returned_codes:
                stocks_snapshot.append({
                    "code": s["code"],
                    "name": s["name"],
                    "price": 0.0,
                    "change_pct": 0.0,
                    "main_flow_yi": 0.0
                })

    except Exception as e:
        warn(f"price alerts fallback to placeholders: {e}")
        # 非交易时间或接口问题
        for s in watchlist:
            stocks_snapshot.append({
                "code": s["code"],
                "name": s["name"],
                "price": 0.0,
                "change_pct": 0.0,
                "main_flow_yi": 0.0
            })

    return alerts, stocks_snapshot


# ─── 维度2：大盘异动 ─────────────────────────────────────────────────────────

def check_index_alerts():
    """
    获取上证指数涨跌幅，检测大盘异动。
    返回 (alerts, sh_index_snapshot)
    """
    alerts = []
    sh_snapshot = {"price": 0.0, "change_pct": 0.0, "status": "ok"}

    try:
        import akshare as ak

        # 尝试获取指数行情
        df = ak.stock_zh_index_spot_em(symbol="上证系列指数")
        # 列名适配
        code_col = None
        pct_col = None
        price_col = None
        for col in df.columns:
            if "代码" in col:
                code_col = col
            elif "涨跌幅" in col:
                pct_col = col
            elif "最新价" in col or "当前价" in col:
                price_col = col

        sh_row = None
        if code_col:
            sh_rows = df[df[code_col] == "000001"]
            if not sh_rows.empty:
                sh_row = sh_rows.iloc[0]

        if sh_row is not None and pct_col:
            pct = safe_float(sh_row.get(pct_col, 0))
            price = safe_float(sh_row.get(price_col, 0)) if price_col else 0.0
            sh_snapshot = {"price": round(price, 2), "change_pct": round(pct, 2), "status": "ok"}

            if pct < -2.5:
                alerts.append({
                    "type": "index",
                    "code": "000001",
                    "name": "上证指数",
                    "level": "red",
                    "detail": f"沪指大跌 {pct:.2f}%，市场恐慌",
                    "action_hint": "考虑降低仓位，等待企稳"
                })
            elif pct < -1.5:
                alerts.append({
                    "type": "index",
                    "code": "000001",
                    "name": "上证指数",
                    "level": "yellow",
                    "detail": f"沪指下跌 {pct:.2f}%，注意风险",
                    "action_hint": "关注是否跌破关键支撑"
                })
            elif pct > 2.0:
                alerts.append({
                    "type": "index",
                    "code": "000001",
                    "name": "上证指数",
                    "level": "yellow",
                    "detail": f"沪指大涨 +{pct:.2f}%，关注追高风险",
                    "action_hint": "强势信号，但注意高位风险"
                })
        else:
            sh_snapshot["status"] = "market_closed"

    except Exception as e:
        warn(f"index alerts unavailable: {e}")
        sh_snapshot["status"] = "market_closed"

    return alerts, sh_snapshot


# ─── 维度3：北向资金 ─────────────────────────────────────────────────────────

def check_north_flow():
    """
    获取北向资金净流入，检测北向异动。
    返回 (alerts, north_flow_yi, north_status)
    """
    alerts = []
    north_flow_yi = 0.0
    north_status = "ok"

    try:
        import akshare as ak

        # 尝试获取北向资金
        df = ak.stock_em_hsgt_north_net_flow_in(symbol="沪深港通")
        if df is None or df.empty:
            north_status = "unavailable"
            return alerts, north_flow_yi, north_status

        # 取最新一行
        latest = df.iloc[-1]
        # 列名适配：找数值列
        val = None
        for col in df.columns:
            if col not in ("日期", "date", "Date"):
                try:
                    val = float(latest[col])
                    break
                except (TypeError, ValueError):
                    continue

        if val is not None:
            # 单位可能是亿或元，做量级判断
            if abs(val) > 1e8:
                val = val / 1e8  # 转换为亿
            north_flow_yi = round(val, 2)

            if north_flow_yi < -150:
                alerts.append({
                    "type": "north_flow",
                    "code": "",
                    "name": "北向资金",
                    "level": "red",
                    "detail": f"北向净流出 {abs(north_flow_yi):.1f}亿，外资大幅撤退",
                    "action_hint": "外资大幅出逃，谨慎"
                })
            elif north_flow_yi < -80:
                alerts.append({
                    "type": "north_flow",
                    "code": "",
                    "name": "北向资金",
                    "level": "yellow",
                    "detail": f"北向净流出 {abs(north_flow_yi):.1f}亿，外资持续流出",
                    "action_hint": "关注外资动向"
                })
            elif north_flow_yi > 80:
                alerts.append({
                    "type": "north_flow",
                    "code": "",
                    "name": "北向资金",
                    "level": "yellow",
                    "detail": f"北向净流入 +{north_flow_yi:.1f}亿，外资积极布局",
                    "action_hint": "外资净流入偏强，偏多信号"
                })
        else:
            north_status = "unavailable"

    except Exception as e:
        warn(f"north flow unavailable: {e}")
        north_status = "unavailable"

    return alerts, north_flow_yi, north_status


# ─── 维度4：主力资金 ─────────────────────────────────────────────────────────

def check_main_flow(watchlist, stocks_snapshot):
    """
    获取自选股主力资金（大单净流入），检测主力异动。
    stocks_snapshot 会被原地更新 main_flow_yi 字段。
    返回 alerts
    """
    alerts = []

    try:
        import akshare as ak

        # 建立 code → snapshot 索引
        snap_map = {s["code"]: s for s in stocks_snapshot}

        for stock in watchlist:
            code = stock["code"]
            name = stock["name"]

            # 判断市场
            market = infer_market(code)

            try:
                df = ak.stock_individual_fund_flow(stock=code, market=market)
                if df is None or df.empty:
                    continue

                # 取最新一行（今日）
                latest = df.iloc[-1]

                # 列名适配：找主力净流入列
                main_col = None
                for col in df.columns:
                    if "主力" in col and "净" in col:
                        main_col = col
                        break
                    elif "主力净流入" in col:
                        main_col = col
                        break

                if main_col is None:
                    # 尝试找"净额"相关列
                    for col in df.columns:
                        if "净额" in col or "净流入" in col:
                            main_col = col
                            break

                if main_col:
                    val = safe_float(latest.get(main_col, 0))
                    # akshare stock_individual_fund_flow 净额列单位为元，统一转换为亿
                    # 若值绝对值 > 1000（明显不像亿），则按元转换；否则已是亿
                    if abs(val) > 1000:
                        val = val / 1e8
                    main_flow_yi = round(val, 4)

                    # 更新 snapshot
                    if code in snap_map:
                        snap_map[code]["main_flow_yi"] = main_flow_yi

                    # 异动判断（5000万 = 0.5亿）
                    if main_flow_yi > 0.5:
                        alerts.append({
                            "type": "main_flow",
                            "code": code,
                            "name": name,
                            "level": "yellow",
                            "detail": f"主力净流入 +{main_flow_yi:.2f}亿",
                            "action_hint": "主力吸筹信号",
                            "signal": "吸筹"
                        })
                    elif main_flow_yi < -0.5:
                        alerts.append({
                            "type": "main_flow",
                            "code": code,
                            "name": name,
                            "level": "yellow",
                            "detail": f"主力净流出 {main_flow_yi:.2f}亿",
                            "action_hint": "主力出货信号，注意风险",
                            "signal": "出货"
                        })

            except Exception as e:
                warn(f"main flow unavailable for {code}: {e}")
                # 单只股票失败，跳过继续
                continue

    except Exception as e:
        warn(f"main flow checks unavailable: {e}")

    return alerts


# ─── 维度5：快照对比 ─────────────────────────────────────────────────────────

def check_snapshot_diff(stocks_snapshot):
    """
    读取 data/intraday/ 下今天最近一次快照，与当前对比。
    返回 alerts
    """
    alerts = []

    today_str = now_shanghai().strftime("%Y-%m-%d")
    pattern = str(INTRADAY_DIR / f"{today_str}-*.json")
    files = sorted(glob.glob(pattern))

    if not files:
        return alerts

    latest_file = files[-1]
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            prev = json.load(f)

        prev_stocks = {s["code"]: s for s in prev.get("snapshot", {}).get("stocks", [])}
        curr_stocks = {s["code"]: s for s in stocks_snapshot}

        for code, curr in curr_stocks.items():
            if code not in prev_stocks:
                continue
            prev_s = prev_stocks[code]

            curr_pct = curr.get("change_pct", 0)
            prev_pct = prev_s.get("change_pct", 0)
            delta = curr_pct - prev_pct

            if abs(delta) >= 2.0:
                direction = "上涨" if delta > 0 else "下跌"
                alerts.append({
                    "type": "snapshot_diff",
                    "code": code,
                    "name": curr.get("name", code),
                    "level": "yellow",
                    "detail": f"盘中{direction}变化 {delta:+.1f}%（前次快照 {prev_pct:+.1f}% → 当前 {curr_pct:+.1f}%）",
                    "action_hint": f"短期{direction}幅度较大，注意节奏"
                })

            # 主力资金方向反转
            curr_flow = curr.get("main_flow_yi", 0)
            prev_flow = prev_s.get("main_flow_yi", 0)
            if prev_flow != 0 and curr_flow != 0:
                if (prev_flow > 0.1 and curr_flow < -0.1) or (prev_flow < -0.1 and curr_flow > 0.1):
                    alerts.append({
                        "type": "snapshot_diff",
                        "code": code,
                        "name": curr.get("name", code),
                        "level": "yellow",
                        "detail": f"主力资金方向反转（前次 {prev_flow:+.2f}亿 → 当前 {curr_flow:+.2f}亿）",
                        "action_hint": "资金方向反转，需重新评估"
                    })

    except Exception as e:
        warn(f"snapshot diff unavailable: {e}")

    return alerts


# ─── 计算 alert_score ────────────────────────────────────────────────────────

def calc_score(alerts):
    score = 0
    for a in alerts:
        if a.get("level") == "red":
            score += 2
        elif a.get("level") == "yellow":
            score += 1
    return score


# ─── 生成 summary ────────────────────────────────────────────────────────────

def gen_summary(alerts, alert_score, sh_snapshot, stocks_snapshot, north_flow_yi, north_status):
    if alert_score == 0:
        sh_pct = sh_snapshot.get("change_pct", 0)
        sh_str = f"+{sh_pct:.2f}%" if sh_pct >= 0 else f"{sh_pct:.2f}%"
        return f"无异动。上证指数 {sh_str}，自选股整体平稳。"

    parts = []
    for a in alerts:
        if a["type"] in ("price", "main_flow"):
            name = a.get("name", a.get("code", ""))
            parts.append(f"{name}{a['detail']}")
        elif a["type"] == "index":
            parts.append(a["detail"])
        elif a["type"] == "north_flow":
            parts.append(a["detail"])
        elif a["type"] == "snapshot_diff":
            name = a.get("name", a.get("code", ""))
            parts.append(f"{name}{a['detail']}")

    summary = "；".join(parts[:3])  # 最多取前3条
    if len(alerts) > 3:
        summary += f"；另有 {len(alerts)-3} 项异动。"
    summary += f" alert_score={alert_score}。"
    return summary


# ─── check 主逻辑 ────────────────────────────────────────────────────────────

def run_check():
    now_str = now_shanghai().strftime("%Y-%m-%d %H:%M")
    watchlist = load_watchlist()

    if not watchlist:
        return {
            "time": now_str,
            "alert_score": 0,
            "has_alert": False,
            "alerts": [],
            "snapshot": {
                "sh_index": {},
                "stocks": [],
                "north_flow_yi": None,
                "north_status": "no_watchlist"
            },
            "summary": "当前 watchlist 为空，跳过盘中异动检测。"
        }

    all_alerts = []

    # 维度1：价格
    price_alerts, stocks_snapshot = check_price_alerts(watchlist)
    all_alerts.extend(price_alerts)

    # 维度2：大盘
    index_alerts, sh_snapshot = check_index_alerts()
    all_alerts.extend(index_alerts)

    # 维度3：北向
    north_alerts, north_flow_yi, north_status = check_north_flow()
    all_alerts.extend(north_alerts)

    # 维度4：主力资金（原地更新 stocks_snapshot 里的 main_flow_yi）
    flow_alerts = check_main_flow(watchlist, stocks_snapshot)
    all_alerts.extend(flow_alerts)

    # 维度5：快照对比
    diff_alerts = check_snapshot_diff(stocks_snapshot)
    all_alerts.extend(diff_alerts)

    # 计算评分
    alert_score = calc_score(all_alerts)

    # 生成 summary
    summary = gen_summary(all_alerts, alert_score, sh_snapshot, stocks_snapshot, north_flow_yi, north_status)

    result = {
        "time": now_str,
        "alert_score": alert_score,
        "has_alert": alert_score >= 1,
        "alerts": all_alerts,
        "snapshot": {
            "sh_index": sh_snapshot,
            "stocks": stocks_snapshot,
            "north_flow_yi": north_flow_yi,
            "north_status": north_status
        },
        "summary": summary
    }

    return result


# ─── snapshot 命令 ───────────────────────────────────────────────────────────

def run_snapshot():
    result = run_check()

    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    ts = now_shanghai().strftime("%Y-%m-%d-%H%M")
    out_path = INTRADAY_DIR / f"{ts}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    result["_saved_to"] = str(out_path)
    return result


# ─── 入口 ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 intraday_alert.py check|snapshot", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "check":
        result = run_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "snapshot":
        result = run_snapshot()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print(f"Unknown command: {cmd}. Use 'check' or 'snapshot'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
