"""コマンドラインインターフェース。

- `stock-screener screen`  : 週次ファンダメンタル・スクリーニング → ウォッチリスト生成
- `stock-screener signals` : 日次テクニカル・シグナル判定 → レポート生成 + 通知
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from .config import load_config
from .data.fundamentals import fetch_fundamentals
from .data.prices import fetch_price_history
from .notify import send_slack
from .report import (
    build_slack_message,
    now_jst,
    write_signals_report,
    write_watchlist_report,
)
from .screening.fundamental import apply_market_cap_filter, score_fundamentals
from .signals.technical import scan_watchlist
from .universe import load_universe

logger = logging.getLogger(__name__)

# ウォッチリストCSVに保存するカラム
WATCHLIST_COLUMNS = [
    "ticker", "name", "market", "sector", "industry", "currency", "score",
    "metric_coverage", "market_cap", "roe", "operating_margin", "revenue_growth",
    "earnings_growth", "pe", "pb", "dividend_yield", "debt_to_equity",
]


def run_screen(config: dict[str, Any], limit: int | None = None) -> pd.DataFrame:
    """第1段階: ユニバース全体をスクリーニングしてウォッチリストを生成する。"""
    screening_cfg = config["screening"]
    paths = config["paths"]
    fetch_cfg = config.get("fetch", {})

    universe = load_universe(config)
    if limit:
        universe = universe.head(limit)
        logger.info("--limit 指定により %d 銘柄に制限して実行します", limit)

    fundamentals = fetch_fundamentals(
        universe["ticker"].tolist(),
        pause=float(fetch_cfg.get("fundamentals_pause", 0.2)),
        max_retries=int(fetch_cfg.get("max_retries", 2)),
    )
    _save_fundamentals_snapshot(fundamentals, paths.get("fundamentals_dir"))

    merged = universe.merge(fundamentals, on="ticker", how="left")
    filtered = apply_market_cap_filter(merged)
    scored = score_fundamentals(filtered, screening_cfg)
    watchlist = scored.head(int(screening_cfg.get("watchlist_size", 60)))

    watchlist_path = Path(paths["watchlist"])
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [c for c in WATCHLIST_COLUMNS if c in watchlist.columns]
    watchlist[columns].to_csv(watchlist_path, index=False)
    logger.info("ウォッチリストを保存しました: %s (%d 銘柄)", watchlist_path, len(watchlist))

    report_path = Path(paths["reports_dir"]) / "watchlist.md"
    write_watchlist_report(watchlist, report_path, universe_size=len(universe))
    logger.info("レポートを生成しました: %s", report_path)
    return watchlist


def run_signals(config: dict[str, Any], notify: bool = True) -> list[dict[str, Any]]:
    """第2段階: ウォッチリスト銘柄の買いシグナルを判定する。"""
    signals_cfg = config["signals"]
    paths = config["paths"]

    watchlist_path = Path(paths["watchlist"])
    if not watchlist_path.exists():
        logger.warning("ウォッチリストが存在しないため、先にスクリーニングを実行します")
        run_screen(config)
    watchlist = pd.read_csv(watchlist_path)
    if watchlist.empty:
        logger.error("ウォッチリストが空です: %s", watchlist_path)
        return []

    prices = fetch_price_history(
        watchlist["ticker"].tolist(),
        period=str(signals_cfg.get("history_period", "2y")),
        cache_dir=paths.get("cache_dir"),
    )
    results = scan_watchlist(prices, watchlist, signals_cfg)

    reports_dir = Path(paths["reports_dir"])
    dated_path = reports_dir / "signals" / f"{now_jst():%Y-%m-%d}.md"
    write_signals_report(results, dated_path, watchlist_size=len(watchlist))
    write_signals_report(results, reports_dir / "latest-signals.md",
                         watchlist_size=len(watchlist))
    logger.info("レポートを生成しました: %s", dated_path)

    if results and notify:
        send_slack(build_slack_message(results))
    return results


def _save_fundamentals_snapshot(df: pd.DataFrame, snapshot_dir: str | None) -> None:
    """財務データのスナップショットを日付付きで保存する (後の分析・検証用)。"""
    if not snapshot_dir:
        return
    path = Path(snapshot_dir) / f"{now_jst():%Y-%m-%d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.info("財務データのスナップショットを保存: %s", path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stock-screener",
        description="中長期投資向けの銘柄発掘システム",
    )
    parser.add_argument("--config", default="config.yaml", help="設定ファイルのパス")
    parser.add_argument("-v", "--verbose", action="store_true", help="デバッグログを出力")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_screen = subparsers.add_parser(
        "screen", help="ファンダメンタル・スクリーニングを実行してウォッチリストを生成 (週次)"
    )
    p_screen.add_argument("--limit", type=int, default=None,
                          help="対象銘柄数を制限 (動作確認用)")

    p_signals = subparsers.add_parser(
        "signals", help="ウォッチリストの買いシグナルを判定してレポート生成 (日次)"
    )
    p_signals.add_argument("--no-notify", action="store_true", help="Slack通知を送らない")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    if args.command == "screen":
        watchlist = run_screen(config, limit=args.limit)
        if watchlist.empty:
            logger.error("ウォッチリストが空になりました。データ取得状況を確認してください")
            return 1
    elif args.command == "signals":
        run_signals(config, notify=not args.no_notify)
    return 0


if __name__ == "__main__":
    sys.exit(main())
