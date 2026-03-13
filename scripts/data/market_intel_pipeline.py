#!/usr/bin/env python3
"""市场情报编排入口（当前聚焦跨资产链路）

目的：
- 为盘前 / 收盘复盘 / 周报提供统一的跨资产输入产物
- 把 cross_asset_snapshot 的新数据链（FMP + Stooq + FRED + Yahoo fallback）
  接入标准 data 目录结构，供后续任务与报告直接复用

当前子命令：
- snapshot   直接输出跨资产 JSON 到 stdout
- premarket  写入 data/daily_inputs/<date>/09_cross_asset.txt
- postmarket 写入 data/daily_inputs/<date>/09_cross_asset.txt
- weekly     写入 data/weekly_inputs/<date>/15_cross_asset.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.data.cross_asset_snapshot import build_snapshot  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
SNAPSHOT_RETRIES = 2


def today_str() -> str:
    return datetime.now(TZ).date().isoformat()


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_snapshot(path: Path) -> dict:
    best_snapshot = None
    best_ratio = -1.0

    for attempt in range(1, SNAPSHOT_RETRIES + 1):
        snapshot = build_snapshot()
        ratio = float((snapshot.get("summary") or {}).get("coverage_ratio") or 0.0)
        if ratio > best_ratio:
            best_snapshot = snapshot
            best_ratio = ratio
        if ratio >= 1.0:
            break
        if attempt < SNAPSHOT_RETRIES:
            time.sleep(1.5 * attempt)

    ensure_parent(path)
    path.write_text(dump_json(best_snapshot or {}), encoding="utf-8")
    return best_snapshot or {}


def output_path_for(mode: str, date_str: str) -> Path:
    if mode == "weekly":
        return WORKSPACE_ROOT / "data" / "weekly_inputs" / date_str / "15_cross_asset.txt"
    if mode in {"premarket", "postmarket"}:
        return WORKSPACE_ROOT / "data" / "daily_inputs" / date_str / "09_cross_asset.txt"
    raise ValueError(f"unsupported mode: {mode}")


def print_result(mode: str, path: Path, snapshot: dict) -> None:
    summary = snapshot.get("summary") or {}
    print(
        json.dumps(
            {
                "mode": mode,
                "path": str(path),
                "coverage": summary.get("coverage"),
                "coverage_ratio": summary.get("coverage_ratio"),
                "generated_at": snapshot.get("generated_at"),
                "errors": snapshot.get("errors", []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="市场情报编排入口（跨资产）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("snapshot", help="输出跨资产 JSON 到 stdout")

    for name in ["premarket", "postmarket", "weekly"]:
        p = sub.add_parser(name, help=f"生成 {name} 跨资产输入文件")
        p.add_argument("--date", default=today_str(), help="日期（YYYY-MM-DD），默认 Asia/Shanghai 今天")
        p.add_argument("--output", help="可选：自定义输出路径")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "snapshot":
        print(dump_json(build_snapshot()))
        return

    out_path = Path(args.output) if args.output else output_path_for(args.command, args.date)
    snapshot = write_snapshot(out_path)
    print_result(args.command, out_path, snapshot)


if __name__ == "__main__":
    main()
