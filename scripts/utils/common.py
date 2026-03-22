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
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
