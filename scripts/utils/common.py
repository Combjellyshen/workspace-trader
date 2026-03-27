"""公共工具函数 — 消除跨模块重复代码

所有脚本应优先从这里导入以下公共能力：
- safe_float / safe_pct  : 安全数值转换
- http_get               : 统一 HTTP GET（urllib 实现，无额外依赖）
- WORKSPACE_ROOT         : 项目根目录（基于 __file__ 解析，可移植）
- load_watchlist         : 加载 watchlist.json / longterm_watchlist.json
- load_config            : 加载 config.json
- json_output            : 标准化 JSON stdout 输出
"""

import json
import os
import socket
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# IPv4 优先 — 部分金融 API（jin10 等）有 IPv6 AAAA 记录，
# 但本机 IPv6 不通，Python 默认先尝试 IPv6 会无限挂起。
# 在模块加载时 monkey-patch getaddrinfo，优先返回 IPv4。
# ============================================================
_original_getaddrinfo = socket.getaddrinfo


def _ipv4_first_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    results = _original_getaddrinfo(host, port, family, type, proto, flags)
    v4 = [r for r in results if r[0] == socket.AF_INET]
    return v4 if v4 else results


socket.getaddrinfo = _ipv4_first_getaddrinfo

# ============================================================
# 路径
# ============================================================
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
"""项目根目录，等价于 workspace-trader/"""


def workspace_path(*parts):
    """拼接项目根目录下的路径，返回 Path 对象"""
    return WORKSPACE_ROOT.joinpath(*parts)


# ============================================================
# 安全数值转换
# ============================================================
def safe_float(val, default=0.0, units=False):
    """安全浮点数转换，失败返回 default

    Args:
        val: 输入值
        default: 转换失败时返回值
        units: 为 True 时解析中文单位后缀（万亿/亿/万）
    """
    if val is None:
        return default
    s = str(val).strip()
    if s in ('', '-', '--', 'N/A', 'None', 'nan', 'False', 'True'):
        return default
    s = s.replace('%', '').replace(',', '')
    multiplier = 1.0
    if units:
        if s.endswith('万亿'):
            s = s[:-2]; multiplier = 1e12
        elif s.endswith('亿'):
            s = s[:-1]; multiplier = 1e8
        elif s.endswith('万'):
            s = s[:-1]; multiplier = 1e4
    try:
        result = float(s) * multiplier
        # nan / inf → default（无需 numpy）
        if result != result or result == float('inf') or result == float('-inf'):
            return default
        return result
    except (TypeError, ValueError):
        return default


def safe_pct(val, default=None):
    """安全百分比转换，去除 % 和逗号"""
    if val is None or str(val).strip() in ('', '-', '--', 'N/A'):
        return default
    try:
        return float(str(val).replace('%', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return default


# ============================================================
# HTTP 请求
# ============================================================
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

SINA_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://finance.sina.com.cn',
}

EASTMONEY_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://quote.eastmoney.com/',
}


# ============================================================
# 东财 push2 HTTP 数据层（绕过 HTTPS 封锁）
# ============================================================
# akshare 默认走 HTTPS push2.eastmoney.com，在部分 VPS 上被 TLS 握手拦截。
# HTTP 协议同域名同接口可正常访问。以下函数提供全市场行情、行业/概念资金流
# 等关键数据的可靠获取通道，供所有分析脚本共享。

def push2_get(fs, fields, sort='f3', top=50, page=1):
    """
    通用 push2 HTTP 列表接口，带重试和多域名 fallback。

    push2.eastmoney.com 对 VPS IP 的封锁是间歇性的：
    - HTTPS 几乎永久封锁
    - HTTP 时通时断
    所以先试 HTTP push2，失败后尝试备用域名。

    Returns:
        (total, items_list)
    """
    import requests as _req
    import time

    params = {
        'fid': sort, 'po': '1', 'pz': top, 'pn': page,
        'np': '1', 'fltt': '2', 'fs': fs, 'fields': fields,
    }

    # 多域名 + 重试
    urls = [
        'http://push2.eastmoney.com/api/qt/clist/get',
        'http://push2.eastmoney.com/api/qt/clist/get',   # retry same
        'http://push2his.eastmoney.com/api/qt/clist/get', # 历史域名有时也支持 clist
    ]

    last_err = None
    for i, url in enumerate(urls):
        try:
            r = _req.get(url, params=params, headers=EASTMONEY_HEADERS, timeout=15)
            d = r.json()
            total = (d.get('data') or {}).get('total', 0)
            diff = (d.get('data') or {}).get('diff', [])
            items = list(diff.values()) if isinstance(diff, dict) else (diff or [])
            if total > 0 or items:
                return total, items
        except Exception as e:
            last_err = e
            if i < len(urls) - 1:
                time.sleep(1)

    # 全部失败，尝试 akshare 作为最终 fallback（HTTPS 偶尔能通）
    return 0, []


# 常用字段说明：
# f2=最新价(×100) f3=涨跌幅(×100) f4=涨跌额(×100) f5=成交量 f6=成交额
# f7=振幅(×100) f8=换手率(×100) f9=PE(×100) f10=量比(×100)
# f12=代码 f14=名称 f15=最高(×100) f16=最低(×100) f17=今开(×100) f18=昨收(×100)
# f23=PB(×100) f62=主力净流入(元) f104=上涨家数 f105=下跌家数
# f184=主力净占比(×100) f66=超大单净额 f69=超大单净占比

# 板块代码：
# 全A股: 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048'
# 行业板块: 'm:90+t:2'
# 概念板块: 'm:90+t:3'
# 上证指数系列: 'm:1+s:2'
# 深证指数系列: 'm:0+t:5'

FS_ALL_A = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048'
FS_INDUSTRY = 'm:90+t:2'
FS_CONCEPT = 'm:90+t:3'
FS_SH_INDEX = 'm:1+s:2'


def fetch_a_stock_spot(top=5000):
    """获取全 A 股实时行情。优先 push2 HTTP，失败时 fallback 到 akshare。"""
    total, items = push2_get(
        FS_ALL_A,
        'f12,f14,f2,f3,f6,f8,f9,f10,f15,f16,f17,f18,f23',
        sort='f6', top=top,
    )
    if items:
        result = []
        for it in items:
            result.append({
                '代码': it.get('f12', ''),
                '名称': it.get('f14', ''),
                '最新价': (it.get('f2') or 0) / 100,
                '涨跌幅': (it.get('f3') or 0) / 100,
                '成交额': it.get('f6') or 0,
                '换手率': (it.get('f8') or 0) / 100,
                '市盈率-动态': (it.get('f9') or 0) / 100,
                '量比': (it.get('f10') or 0) / 100,
                '最高': (it.get('f15') or 0) / 100,
                '最低': (it.get('f16') or 0) / 100,
                '今开': (it.get('f17') or 0) / 100,
                '昨收': (it.get('f18') or 0) / 100,
                '市净率': (it.get('f23') or 0) / 100,
            })
        return result

    # Fallback: akshare (HTTPS, 偶尔能通)
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            keep = ['代码', '名称', '最新价', '涨跌幅', '成交额', '换手率', '市盈率-动态', '量比', '最高', '最低', '今开', '昨收', '市净率']
            available = [c for c in keep if c in df.columns]
            import json as _json
            return _json.loads(df[available].to_json(orient='records', force_ascii=False))
    except Exception:
        pass

    return []


def fetch_single_quote(code: str) -> dict:
    """获取单只股票实时报价（腾讯 → 新浪 fallback，不走 push2）。
    用于 push2 完全不可用时的轻量补充。返回 {code, name, price, pct, prev_close, volume, turnover}。
    """
    code = str(code).zfill(6)
    prefix = 'sh' if code.startswith(('6', '5', '9', '11')) else 'sz'

    # 腾讯
    try:
        raw = http_get(f'http://qt.gtimg.cn/q={prefix}{code}', timeout=5)
        parts = raw.split('~')
        if len(parts) > 40:
            return {
                '代码': code,
                '名称': parts[1],
                '最新价': safe_float(parts[3]),
                '昨收': safe_float(parts[4]),
                '涨跌幅': safe_float(parts[32]),
                '成交量': safe_float(parts[36]),
                '成交额': safe_float(parts[37]) * 10000,  # 万→元
            }
    except Exception:
        pass

    # 新浪
    try:
        raw = http_get(f'http://hq.sinajs.cn/list={prefix}{code}',
                       headers=SINA_HEADERS, timeout=5)
        parts = raw.split(',')
        if len(parts) > 30:
            name = raw.split('"')[1].split(',')[0] if '"' in raw else code
            return {
                '代码': code,
                '名称': name,
                '最新价': safe_float(parts[3]),
                '昨收': safe_float(parts[2]),
                '涨跌幅': round((safe_float(parts[3]) - safe_float(parts[2])) / safe_float(parts[2]) * 100, 2) if safe_float(parts[2]) > 0 else 0,
                '成交量': safe_float(parts[8]),
                '成交额': safe_float(parts[9]),
            }
    except Exception:
        pass

    return {}


def fetch_macro_indicators() -> dict:
    """获取 VIX/DXY/US10Y/黄金/原油（Yahoo Finance）。盘后也可用。"""
    result = {}
    symbols = {'^VIX': 'vix', 'DX-Y.NYB': 'dxy', '^TNX': 'us10y', 'GC=F': 'gold', 'CL=F': 'wti'}
    for symbol, key in symbols.items():
        try:
            import json as _json
            raw = http_get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d', timeout=8)
            d = _json.loads(raw)
            closes = d['chart']['result'][0]['indicators']['quote'][0]['close']
            latest = [c for c in closes if c is not None][-1]
            result[key] = round(latest, 2)
        except Exception:
            pass
    return result


def fetch_north_flow() -> dict:
    """获取北向资金汇总。akshare → tushare fallback。"""
    # 1) akshare — 当日汇总（东财接口）
    try:
        import akshare as ak
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and not df.empty:
            north = df[df['资金方向'] == '北向']
            if not north.empty:
                sh_net = safe_float(north[north['板块'] == '沪股通']['成交净买额'].sum())
                sz_net = safe_float(north[north['板块'] == '深股通']['成交净买额'].sum())
                # akshare 已知返回 0.0 假数据的问题：两项同时为 0 视为不可信
                if sh_net != 0.0 or sz_net != 0.0:
                    date = str(north.iloc[0].get('交易日', ''))
                    return {'date': date, 'sh_net_yi': round(sh_net, 2),
                            'sz_net_yi': round(sz_net, 2),
                            'total_net_yi': round(sh_net + sz_net, 2),
                            'source': 'akshare'}
    except Exception:
        pass

    # 2) tushare — moneyflow_hsgt（T-1 数据，稳定可靠，优先于 akshare_hist）
    try:
        cfg = load_config()
        token = cfg.get('tushare_token', '')
        if token:
            import tushare as ts
            ts.set_token(token)
            pro = ts.pro_api()
            from datetime import timedelta
            end = datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y%m%d')
            start = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=10)).strftime('%Y%m%d')
            df = pro.moneyflow_hsgt(start_date=start, end_date=end)
            if df is not None and not df.empty:
                latest = df.iloc[0]  # tushare 按日期倒序
                hgt = safe_float(latest.get('hgt', 0))  # 沪股通净买（百万）
                sgt = safe_float(latest.get('sgt', 0))  # 深股通净买（百万）
                north = safe_float(latest.get('north_money', 0))
                date = str(latest.get('trade_date', ''))
                # tushare 单位是百万，转亿
                return {'date': date,
                        'sh_net_yi': round(hgt / 100, 2),
                        'sz_net_yi': round(sgt / 100, 2),
                        'total_net_yi': round(north / 100, 2),
                        'note': 'T-1数据（tushare）',
                        'source': 'tushare'}
    except Exception:
        pass

    # 3) akshare — 历史日线（数据可能很旧，最后手段）
    try:
        import akshare as ak
        df = ak.stock_hsgt_hist_em(symbol='北向资金')
        if df is not None and not df.empty:
            col = '当日成交净买额'
            if col in df.columns:
                valid = df.dropna(subset=[col])
                if not valid.empty:
                    latest = valid.iloc[-1]
                    total_net = safe_float(latest.get(col, 0))
                    date = str(latest.get('日期', ''))
                    return {'date': date, 'sh_net_yi': 0.0, 'sz_net_yi': 0.0,
                            'total_net_yi': round(total_net, 2),
                            'note': '仅有合计，沪深拆分不可用，数据可能延迟',
                            'source': 'akshare_hist'}
    except Exception:
        pass

    return {}


def fetch_margin_balance() -> dict:
    """获取最新融资余额。优先用 stock_margin_account_info（含沪深合计）。"""
    try:
        import akshare as ak
        df = ak.stock_margin_account_info()
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                'date': str(latest.get('日期', '')),
                'balance_yi': round(safe_float(latest.get('融资余额', 0)), 2),
                'margin_buy_yi': round(safe_float(latest.get('融资买入额', 0)), 2),
            }
    except Exception:
        pass
    return {}


def fetch_industry_flow(top=50):
    """获取行业板块资金流排名，返回 [{name, pct, main_flow, up, down}]"""
    _, items = push2_get(FS_INDUSTRY, 'f12,f14,f3,f62,f104,f105', sort='f62', top=top)
    result = []
    for it in items:
        result.append({
            '板块代码': it.get('f12', ''),
            '行业名称': it.get('f14', ''),
            '涨跌幅': (it.get('f3') or 0) / 100,
            '主力净流入': (it.get('f62') or 0),  # 元
            '上涨家数': it.get('f104', 0),
            '下跌家数': it.get('f105', 0),
        })
    return result


def fetch_concept_flow(top=30):
    """获取概念板块资金流排名"""
    _, items = push2_get(FS_CONCEPT, 'f12,f14,f3,f62,f104,f105', sort='f62', top=top)
    result = []
    for it in items:
        result.append({
            '板块代码': it.get('f12', ''),
            '概念名称': it.get('f14', ''),
            '涨跌幅': (it.get('f3') or 0) / 100,
            '主力净流入': (it.get('f62') or 0),
            '上涨家数': it.get('f104', 0),
            '下跌家数': it.get('f105', 0),
        })
    return result


def fetch_index_spot():
    """获取主要指数实时行情"""
    _, items = push2_get(FS_SH_INDEX, 'f12,f14,f2,f3,f4,f6', sort='f6', top=20)
    result = []
    for it in items:
        result.append({
            '代码': it.get('f12', ''),
            '名称': it.get('f14', ''),
            '最新价': (it.get('f2') or 0) / 100,
            '涨跌幅': (it.get('f3') or 0) / 100,
            '涨跌额': (it.get('f4') or 0) / 100,
            '成交额': it.get('f6') or 0,
        })
    return result


def http_get(url, headers=None, timeout=12, encoding='utf-8'):
    """统一 HTTP GET — 使用 urllib，无额外依赖

    Args:
        url: 请求地址
        headers: 自定义 headers，默认使用 DEFAULT_HEADERS
        timeout: 超时秒数
        encoding: 响应编码

    Returns:
        解码后的响应文本
    """
    req = urllib.request.Request(url, headers=headers or DEFAULT_HEADERS)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.read().decode(encoding, errors='replace')


# ============================================================
# 配置与数据加载
# ============================================================
def load_config():
    """加载 config.json（含 tushare_token 等）"""
    cfg_path = workspace_path('config.json')
    if not cfg_path.exists():
        return {}
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_watchlist(name='watchlist.json'):
    """加载观察池文件

    Args:
        name: 文件名，默认 'watchlist.json'，也可用 'longterm_watchlist.json'

    Returns:
        list: 股票列表，文件缺失或解析失败时返回空列表
    """
    wl_path = workspace_path(name)
    if not wl_path.exists():
        return []
    try:
        with open(wl_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and 'stocks' in data:
            return data['stocks']
        return []
    except Exception:
        return []


# ============================================================
# 标准化输出
# ============================================================
def json_output(data, indent=2):
    """打印标准化 JSON 到 stdout，确保中文不转义"""
    print(json.dumps(data, ensure_ascii=False, indent=indent, default=str))


def error_output(module, error, **extra):
    """标准化错误 JSON 输出

    格式: {"status": "error", "module": "xxx", "error": "...", "timestamp": "..."}
    """
    payload = {
        "status": "error",
        "module": module,
        "error": str(error)[:200],
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
    }
    payload.update(extra)
    json_output(payload)


# ============================================================
# 模块路径辅助（替代 sys.path.insert hack）
# ============================================================
def ensure_importable():
    """将 WORKSPACE_ROOT 加入 sys.path（若尚未在里面），
    使 `from scripts.xxx import yyy` 在任何 cwd 下都可用。
    """
    root_str = str(WORKSPACE_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
