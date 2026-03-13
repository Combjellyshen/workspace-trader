#!/usr/bin/env python3
"""
交易台记忆管理系统

目录结构:
  memory/
    reports/          — 券商研报存档（按月归档，超6月压缩）
      2026-03/
        2026-03-06_stock.json   个股研报
        2026-03-06_strategy.json 策略研报
    signals/          — 关键信号留存
      2026-03-06.json  当日关键信号
    reviews/          — 复盘日志
      2026-03-06.md    当日复盘
    sentiment/        — 情绪面历史
      2026-03-06.json  当日情绪快照
    stocks/           — 自选股档案
      002475.json      个股详情（投资逻辑+估值轨迹+信号历史）
    state.json        — 状态追踪（连板股、持续异动等）

子命令:
  save_reports     — 保存当日研报到存档
  save_signals     — 保存关键信号
  save_sentiment   — 保存情绪面快照
  compress         — 压缩超过6个月的数据
  query_reports <code> [months] — 查询某只股票的历史评级变化
  query_signals [days]     — 查看近N天的关键信号
  query_reviews [days]     — 查看近N天的复盘
  status           — 查看状态追踪
  update_stock <code> <json_patch> — 更新股票档案
  get_stock <code> — 查看某只股票的完整档案
  stock_summary <code> — 生成某只股的历史判断时间线摘要
  list_stocks      — 列出所有股票档案及其状态
  add_stock <code> <name> [type] — 新增股票档案
"""
import json
import os
import sys
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / 'memory'
REPORTS_DIR = BASE / 'reports'
SIGNALS_DIR = BASE / 'signals'
REVIEWS_DIR = BASE / 'reviews'
SENTIMENT_DIR = BASE / 'sentiment'
STOCKS_DIR = BASE / 'stocks'
STATE_FILE = BASE / 'state.json'

# Ensure directories
for d in [REPORTS_DIR, SIGNALS_DIR, REVIEWS_DIR, SENTIMENT_DIR, STOCKS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def today_str():
    return datetime.now().strftime('%Y-%m-%d')


def month_str():
    return datetime.now().strftime('%Y-%m')


# ─── Save Operations ───

def save_reports():
    """采集并保存当日研报到月度目录"""
    from scripts.data import research_reports as rr

    month_dir = REPORTS_DIR / month_str()
    month_dir.mkdir(exist_ok=True)
    date = today_str()

    latest = rr.latest_reports(days=1, page_size=50)
    with open(month_dir / f'{date}_stock.json', 'w') as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)

    industry = rr.industry_reports(days=1, page_size=30)
    with open(month_dir / f'{date}_industry.json', 'w') as f:
        json.dump(industry, f, ensure_ascii=False, indent=2)

    strategy = rr.strategy_reports(days=1, page_size=30)
    with open(month_dir / f'{date}_strategy.json', 'w') as f:
        json.dump(strategy, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        'action': 'save_reports',
        'date': date,
        'path': str(month_dir),
        'counts': {
            'stock': latest.get('total', 0),
            'industry': industry.get('total', 0),
            'strategy': strategy.get('total', 0),
        }
    }, ensure_ascii=False, indent=2))


def save_signals(signals_data=None):
    """保存关键信号（从 stdin 或参数读取）"""
    date = today_str()
    filepath = SIGNALS_DIR / f'{date}.json'

    existing = []
    if filepath.exists():
        with open(filepath) as f:
            existing = json.load(f)

    if signals_data is None:
        if not sys.stdin.isatty():
            signals_data = json.load(sys.stdin)
        else:
            signals_data = []

    if isinstance(signals_data, dict):
        signals_data = [signals_data]

    existing.extend(signals_data)

    with open(filepath, 'w') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        'action': 'save_signals',
        'date': date,
        'total_signals': len(existing),
        'new_signals': len(signals_data),
    }, ensure_ascii=False, indent=2))


def save_sentiment(sentiment_data=None):
    """保存情绪面快照"""
    date = today_str()
    filepath = SENTIMENT_DIR / f'{date}.json'

    existing = []
    if filepath.exists():
        with open(filepath) as f:
            existing = json.load(f)

    if sentiment_data is None:
        from scripts.analysis import sentiment as sm
        sentiment_data = sm.all_sentiment()

    snapshot = {
        'timestamp': datetime.now().isoformat(),
        'data': sentiment_data,
    }
    existing.append(snapshot)

    with open(filepath, 'w') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        'action': 'save_sentiment',
        'date': date,
        'snapshots_today': len(existing),
    }, ensure_ascii=False, indent=2))


# ─── Compress Operations ───

def compress_old(months=6):
    """压缩超过N个月的数据为 .gz"""
    cutoff = datetime.now() - timedelta(days=months * 30)
    cutoff_month = cutoff.strftime('%Y-%m')
    compressed = 0

    for month_dir in sorted(REPORTS_DIR.iterdir()):
        if month_dir.is_dir() and month_dir.name < cutoff_month:
            for f in month_dir.iterdir():
                if f.suffix == '.json':
                    with open(f, 'rb') as f_in:
                        with gzip.open(str(f) + '.gz', 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    f.unlink()
                    compressed += 1

    for f in sorted(SIGNALS_DIR.iterdir()):
        if f.suffix == '.json' and f.stem < cutoff.strftime('%Y-%m-%d'):
            with open(f, 'rb') as f_in:
                with gzip.open(str(f) + '.gz', 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            f.unlink()
            compressed += 1

    for f in sorted(SENTIMENT_DIR.iterdir()):
        if f.suffix == '.json' and f.stem < cutoff.strftime('%Y-%m-%d'):
            with open(f, 'rb') as f_in:
                with gzip.open(str(f) + '.gz', 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            f.unlink()
            compressed += 1

    print(json.dumps({
        'action': 'compress',
        'cutoff': cutoff.strftime('%Y-%m-%d'),
        'files_compressed': compressed,
    }, ensure_ascii=False, indent=2))


# ─── Query Operations ───

def query_reports(code, months=6):
    """查某只股票的历史评级变化"""
    results = []

    for month_dir in sorted(REPORTS_DIR.iterdir()):
        if not month_dir.is_dir():
            continue
        for f in sorted(month_dir.iterdir()):
            if not f.name.endswith('_stock.json'):
                continue
            try:
                with open(f) as fp:
                    data = json.load(fp)
                for r in data.get('reports', []):
                    if r.get('stock_code') == code:
                        results.append(r)
            except (json.JSONDecodeError, IOError):
                pass

    for month_dir in sorted(REPORTS_DIR.iterdir()):
        if not month_dir.is_dir():
            continue
        for f in sorted(month_dir.iterdir()):
            if not f.name.endswith('_stock.json.gz'):
                continue
            try:
                with gzip.open(f, 'rt') as fp:
                    data = json.load(fp)
                for r in data.get('reports', []):
                    if r.get('stock_code') == code:
                        results.append(r)
            except (json.JSONDecodeError, IOError):
                pass

    print(json.dumps({
        'code': code,
        'months': months,
        'rating_history': results,
    }, ensure_ascii=False, indent=2))


def query_signals(days=7):
    """查近N天关键信号"""
    cutoff = datetime.now() - timedelta(days=days)
    results = {}

    for f in sorted(SIGNALS_DIR.iterdir()):
        if f.suffix == '.json' and f.stem >= cutoff.strftime('%Y-%m-%d'):
            try:
                with open(f) as fp:
                    results[f.stem] = json.load(fp)
            except (json.JSONDecodeError, IOError):
                pass

    print(json.dumps(results, ensure_ascii=False, indent=2))


def query_reviews(days=7):
    """查近N天复盘"""
    cutoff = datetime.now() - timedelta(days=days)
    results = {}

    for f in sorted(REVIEWS_DIR.iterdir()):
        if f.suffix == '.md' and f.stem >= cutoff.strftime('%Y-%m-%d'):
            try:
                results[f.stem] = f.read_text()
            except IOError:
                pass

    print(json.dumps(results, ensure_ascii=False, indent=2))


def status():
    """查看状态追踪"""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            data = json.load(f)
    else:
        data = {
            'streak_stocks': {},
            'watch_alerts': {},
            'pattern_notes': {},
            'last_updated': None,
        }
    print(json.dumps(data, ensure_ascii=False, indent=2))


def update_state(new_state=None):
    """更新状态追踪（从 stdin 读取）"""
    if new_state is None:
        if not sys.stdin.isatty():
            new_state = json.load(sys.stdin)
        else:
            print('需要从 stdin 传入 JSON', file=sys.stderr)
            sys.exit(1)

    existing = {}
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            existing = json.load(f)

    existing.update(new_state)
    existing['last_updated'] = datetime.now().isoformat()

    with open(STATE_FILE, 'w') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(json.dumps({'action': 'update_state', 'state': existing}, ensure_ascii=False, indent=2))


def disk_usage():
    """统计存储占用"""
    total = 0
    counts = {}
    for d_name, d_path in [('reports', REPORTS_DIR), ('signals', SIGNALS_DIR),
                            ('reviews', REVIEWS_DIR), ('sentiment', SENTIMENT_DIR),
                            ('stocks', STOCKS_DIR)]:
        size = 0
        count = 0
        for root, dirs, files in os.walk(d_path):
            for f in files:
                fp = os.path.join(root, f)
                size += os.path.getsize(fp)
                count += 1
        counts[d_name] = {'files': count, 'size_kb': round(size / 1024, 1)}
        total += size

    print(json.dumps({
        'total_kb': round(total / 1024, 1),
        'breakdown': counts,
    }, ensure_ascii=False, indent=2))


# ─── Stock Archive Operations ───

LIST_FIELDS = {'valuation_track', 'signal_history', 'report_refs'}


def _load_stock(code):
    """加载股票档案，不存在则报错退出"""
    filepath = STOCKS_DIR / f'{code}.json'
    if not filepath.exists():
        print(f"错误：找不到股票档案 {code}（路径：{filepath}）", file=sys.stderr)
        sys.exit(1)
    with open(filepath, encoding='utf-8') as f:
        return json.load(f)


def _save_stock(code, data):
    """保存股票档案"""
    filepath = STOCKS_DIR / f'{code}.json'
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _deep_merge(base, patch):
    """深度合并两个 dict，只更新 patch 中有的子字段"""
    result = dict(base)
    for k, v in patch.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def update_stock(code, patch_str):
    """
    更新股票档案。
    - list 字段（valuation_track/signal_history/report_refs）：追加一条
    - dict 字段（thesis 等）：深度合并
    - 标量字段（status/notes 等）：直接覆盖
    """
    try:
        patch = json.loads(patch_str)
    except json.JSONDecodeError as e:
        print(f"错误：JSON 解析失败 — {e}", file=sys.stderr)
        sys.exit(1)

    data = _load_stock(code)

    for key, value in patch.items():
        if key in LIST_FIELDS:
            # 追加到列表
            if key not in data:
                data[key] = []
            if isinstance(value, list):
                data[key].extend(value)
            else:
                data[key].append(value)
        elif key in data and isinstance(data[key], dict) and isinstance(value, dict):
            # 深度合并 dict
            data[key] = _deep_merge(data[key], value)
        else:
            # 直接覆盖标量
            data[key] = value

    _save_stock(code, data)
    print(json.dumps({'action': 'update_stock', 'code': code, 'updated_keys': list(patch.keys())},
                     ensure_ascii=False, indent=2))


def get_stock(code):
    """查看某只股票的完整档案"""
    data = _load_stock(code)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def stock_summary(code):
    """生成某只股的历史判断时间线摘要（Markdown 格式）"""
    data = _load_stock(code)
    name = data.get('name', code)
    status_val = data.get('status', '-')
    thesis = data.get('thesis', {})
    conviction = thesis.get('conviction', '-')
    long_reason = thesis.get('long_reason', '-')
    key_risk = thesis.get('key_risk', '-')
    invalidation = thesis.get('invalidation', '-')

    lines = [
        f"## {name}({code}) 历史追踪摘要",
        "",
        f"**当前状态**：{status_val} | **信心**：{conviction}%",
        f"**投资逻辑**：{long_reason}",
        f"**关键风险**：{key_risk}",
        f"**逻辑失效条件**：{invalidation}",
        "",
        "### 估值轨迹",
        "| 日期 | PE | PE分位 | PB分位 | PEG | 判断 |",
        "|------|-----|--------|--------|-----|------|",
    ]

    valuation_track = data.get('valuation_track', [])
    if valuation_track:
        for v in valuation_track:
            date = v.get('date', '-')
            pe = v.get('pe', '-')
            pe_pct = v.get('pe_pct5yr', '-')
            pb_pct = v.get('pb_pct5yr', '-')
            peg = v.get('peg', '-')
            verdict = v.get('verdict', '-')
            lines.append(f"| {date} | {pe} | {pe_pct}% | {pb_pct}% | {peg} | {verdict} |")
    else:
        lines.append("（无记录）")

    lines += ["", "### 关键信号历史"]
    signal_history = data.get('signal_history', [])
    if signal_history:
        for s in signal_history:
            date = s.get('date', '-')
            sig_type = s.get('signal_type', '-')
            content = s.get('content', '-')
            implication = s.get('implication', '-')
            lines.append(f"- **{date}** [{sig_type}] {content} → {implication}")
    else:
        lines.append("（无记录）")

    lines += ["", "### 报告引用"]
    report_refs = data.get('report_refs', [])
    if report_refs:
        for r in report_refs:
            date = r.get('date', '-')
            file_ref = r.get('file', '-')
            verdict = r.get('verdict', '-')
            action = r.get('action', '-')
            lines.append(f"- **{date}** [{verdict}] {file_ref} — 操作建议：{action}")
    else:
        lines.append("（无记录）")

    print('\n'.join(lines))


def list_stocks():
    """列出所有股票档案及其状态"""
    files = sorted(STOCKS_DIR.glob('*.json'))
    if not files:
        print("（暂无股票档案）")
        return

    results = []
    for f in files:
        try:
            with open(f, encoding='utf-8') as fp:
                data = json.load(fp)
            results.append({
                'code': data.get('code', f.stem),
                'name': data.get('name', '-'),
                'type': data.get('type', '-'),
                'sector': data.get('sector', '-'),
                'status': data.get('status', '-'),
                'conviction': data.get('thesis', {}).get('conviction', '-'),
                'valuation_records': len(data.get('valuation_track', [])),
                'signal_records': len(data.get('signal_history', [])),
                'report_records': len(data.get('report_refs', [])),
            })
        except (json.JSONDecodeError, IOError) as e:
            results.append({'code': f.stem, 'error': str(e)})

    print(json.dumps(results, ensure_ascii=False, indent=2))


def add_stock(code, name, stock_type='stock'):
    """新增股票档案（自动创建初始 JSON 模板）"""
    filepath = STOCKS_DIR / f'{code}.json'
    if filepath.exists():
        print(f"警告：档案已存在 {code}，未覆盖。如需更新请使用 update_stock。", file=sys.stderr)
        sys.exit(1)

    template = {
        'code': code,
        'name': name,
        'type': stock_type,
        'sector': '待填写',
        'added': today_str(),
        'status': 'watching',
        'thesis': {
            'long_reason': '待研究',
            'key_risk': '待研究',
            'invalidation': '待研究',
            'conviction': 30,
        },
        'valuation_track': [],
        'signal_history': [],
        'report_refs': [],
        'notes': '',
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(template, f, ensure_ascii=False, indent=2)

    print(json.dumps({'action': 'add_stock', 'code': code, 'name': name,
                      'path': str(filepath)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'

    if cmd == 'save_reports':
        save_reports()
    elif cmd == 'save_signals':
        save_signals()
    elif cmd == 'save_sentiment':
        save_sentiment()
    elif cmd == 'compress':
        months = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        compress_old(months)
    elif cmd == 'query_reports':
        code = sys.argv[2] if len(sys.argv) > 2 else ''
        months = int(sys.argv[3]) if len(sys.argv) > 3 else 6
        query_reports(code, months)
    elif cmd == 'query_signals':
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        query_signals(days)
    elif cmd == 'query_reviews':
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        query_reviews(days)
    elif cmd == 'status':
        status()
    elif cmd == 'update_state':
        update_state()
    elif cmd == 'disk_usage':
        disk_usage()
    elif cmd == 'update_stock':
        if len(sys.argv) < 4:
            print("用法：update_stock <code> <json_patch>", file=sys.stderr)
            sys.exit(1)
        update_stock(sys.argv[2], sys.argv[3])
    elif cmd == 'get_stock':
        if len(sys.argv) < 3:
            print("用法：get_stock <code>", file=sys.stderr)
            sys.exit(1)
        get_stock(sys.argv[2])
    elif cmd == 'stock_summary':
        if len(sys.argv) < 3:
            print("用法：stock_summary <code>", file=sys.stderr)
            sys.exit(1)
        stock_summary(sys.argv[2])
    elif cmd == 'list_stocks':
        list_stocks()
    elif cmd == 'add_stock':
        if len(sys.argv) < 4:
            print("用法：add_stock <code> <name> [type]", file=sys.stderr)
            sys.exit(1)
        stock_type = sys.argv[4] if len(sys.argv) > 4 else 'stock'
        add_stock(sys.argv[2], sys.argv[3], stock_type)
    else:
        print(f"用法：{sys.argv[0]} [save_reports|save_signals|save_sentiment|compress|"
              f"query_reports|query_signals|query_reviews|status|update_state|disk_usage|"
              f"update_stock|get_stock|stock_summary|list_stocks|add_stock]", file=sys.stderr)
        sys.exit(1)
