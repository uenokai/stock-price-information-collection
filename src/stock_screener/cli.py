"""コマンドラインインターフェース。

- `stock-screener screen`   : 週次ファンダメンタル・スクリーニング → ウォッチリスト生成
- `stock-screener signals`  : 日次テクニカル・シグナル判定 → 台帳記録 + 成績更新 + レポート + 通知
- `stock-screener backtest` : シグナルルールの過去検証 → reports/backtest.md
- `stock-screener portfolio`: 毎月上位N銘柄を保有した場合 vs 指数ETF → reports/portfolio-backtest.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from .backtest import run_backtest as _run_backtest
from .config import load_config
from .data.fundamentals import fetch_fundamentals
from .data.prices import fetch_price_history
from .notify import send_slack
from .portfolio import latest_momentum
from .portfolio import run_portfolio_backtest as _run_portfolio_backtest
from .report import (
    build_slack_message,
    now_jst,
    write_backtest_report,
    write_performance_report,
    write_portfolio_report,
    write_signals_report,
    write_watchlist_report,
)
from .screening.fundamental import apply_market_cap_filter, score_fundamentals
from .signals.technical import scan_watchlist
from .tracking import (
    DEFAULT_HORIZONS,
    forward_returns,
    load_signal_log,
    results_to_log_rows,
    save_signal_log,
    summarize_returns,
    update_signal_log,
)
from .universe import load_universe

logger = logging.getLogger(__name__)

# ウォッチリストCSVに保存するカラム
WATCHLIST_COLUMNS = [
    "ticker", "name", "market", "sector", "industry", "currency", "score",
    "metric_coverage", "market_cap", "roe", "operating_margin", "revenue_growth",
    "earnings_growth", "pe", "pb", "dividend_yield", "debt_to_equity", "momentum_12_1",
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
        retry_passes=int(fetch_cfg.get("retry_passes", 2)),
        retry_cooldown=float(fetch_cfg.get("retry_cooldown", 60)),
    )
    # 株価モメンタム (12-1ヶ月) も指標に加える。ポートフォリオ・バックテストで効果を確認済み
    if "momentum_12_1" in screening_cfg.get("weights", {}):
        pf_cfg = config.get("portfolio", {})
        prices = fetch_price_history(universe["ticker"].tolist(),
                                     period=str(config["signals"].get("history_period", "2y")),
                                     cache_dir=paths.get("cache_dir"))
        momentum = latest_momentum(prices, int(pf_cfg.get("momentum_lookback", 252)),
                                   int(pf_cfg.get("momentum_skip", 21)))
        fundamentals = fundamentals.merge(momentum.rename_axis("ticker").reset_index(),
                                          on="ticker", how="left")
    _save_fundamentals_snapshot(fundamentals, paths.get("fundamentals_dir"))

    merged = universe.merge(fundamentals, on="ticker", how="left")
    filtered = apply_market_cap_filter(merged)
    scored = score_fundamentals(filtered, screening_cfg)
    watchlist = scored.head(int(screening_cfg.get("watchlist_size", 60)))

    watchlist_path = Path(paths["watchlist"])
    previous = pd.read_csv(watchlist_path) if watchlist_path.exists() else None
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [c for c in WATCHLIST_COLUMNS if c in watchlist.columns]
    watchlist[columns].to_csv(watchlist_path, index=False)
    logger.info("ウォッチリストを保存しました: %s (%d 銘柄)", watchlist_path, len(watchlist))

    report_path = Path(paths["reports_dir"]) / "watchlist.md"
    write_watchlist_report(watchlist, report_path, universe_size=len(universe), previous=previous)
    logger.info("レポートを生成しました: %s", report_path)
    return watchlist


def run_signals(config: dict[str, Any], notify: bool = True) -> list[dict[str, Any]]:
    """第2段階: ウォッチリスト銘柄の買いシグナルを判定し、台帳と成績表を更新する。"""
    signals_cfg = config["signals"]
    paths = config["paths"]
    horizons = _horizons(config)

    watchlist_path = Path(paths["watchlist"])
    if not watchlist_path.exists():
        logger.warning("ウォッチリストが存在しないため、先にスクリーニングを実行します")
        run_screen(config)
    watchlist = pd.read_csv(watchlist_path)
    if watchlist.empty:
        logger.error("ウォッチリストが空です: %s", watchlist_path)
        return []

    # 過去にシグナルを出した銘柄 (ウォッチリストから外れていても) の株価も取り、成績を追跡する
    log_path = paths.get("signal_log")
    log = load_signal_log(log_path)
    tickers = list(dict.fromkeys(watchlist["ticker"].tolist() + log["ticker"].tolist()))

    prices = fetch_price_history(
        tickers,
        period=str(signals_cfg.get("history_period", "2y")),
        cache_dir=paths.get("cache_dir"),
    )
    results = scan_watchlist(prices, watchlist, signals_cfg)

    reports_dir = Path(paths["reports_dir"])
    run_date = f"{now_jst():%Y-%m-%d}"
    write_signals_report(results, reports_dir / "signals" / f"{run_date}.md",
                         watchlist_size=len(watchlist))
    write_signals_report(results, reports_dir / "latest-signals.md",
                         watchlist_size=len(watchlist))
    logger.info("レポートを生成しました: %s", reports_dir / "latest-signals.md")

    if log_path:
        log = update_signal_log(log_path, results_to_log_rows(results, run_date))
        evaluated = forward_returns(log, prices, horizons)
        save_signal_log(log_path, evaluated)  # 確定した ret_N を台帳に書き戻す
        entries = evaluated[evaluated["is_new"]]
        summary = summarize_returns(entries, horizons, "signal")
        write_performance_report(evaluated, summary, horizons, reports_dir / "performance.md")
        logger.info("成績表を更新しました: %s", reports_dir / "performance.md")

    if results and notify:
        only_new = bool(signals_cfg.get("notify_only_new", True))
        has_new = any(r.get("new_signals") for r in results)
        if only_new and not has_new:
            logger.info("新規シグナルが無いためSlack通知をスキップします (継続中 %d 銘柄)", len(results))
        else:
            send_slack(build_slack_message(results, only_new=only_new))
    return results


def run_backtest(
    config: dict[str, Any],
    period: str | None = None,
    universe: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Phase 3: 本番と同じシグナルルールを過去データに当てて成績を集計する。"""
    signals_cfg = config["signals"]
    paths = config["paths"]
    bt_cfg = config.get("backtest", {})
    horizons = _horizons(config)
    period = period or str(bt_cfg.get("history_period", "5y"))
    gap = int(signals_cfg.get("renotify_after_days", 10))

    if universe:
        meta = load_universe(config)
        scope = "全ユニバース"
    else:
        watchlist_path = Path(paths["watchlist"])
        if not watchlist_path.exists():
            raise FileNotFoundError(
                f"ウォッチリストがありません: {watchlist_path} (先に `stock-screener screen` を実行)"
            )
        meta = pd.read_csv(watchlist_path)
        scope = "ウォッチリスト"
    if limit:
        meta = meta.head(limit)

    prices = fetch_price_history(meta["ticker"].tolist(), period=period,
                                 cache_dir=paths.get("cache_dir"))
    result = _run_backtest(prices, signals_cfg, horizons, gap, meta=meta)
    report_path = Path(paths["reports_dir"]) / "backtest.md"
    write_backtest_report(result, horizons, report_path, period=period, gap=gap,
                          scope=scope, meta=meta)
    logger.info("バックテストレポートを生成しました: %s", report_path)
    return result


def run_portfolio(config: dict[str, Any], period: str | None = None) -> dict[str, Any]:
    """ポートフォリオ・バックテスト: 月次で上位N銘柄を保有する戦略を指数ETFと比較する。"""
    paths = config["paths"]
    pf_cfg = config.get("portfolio", {})
    period = period or str(pf_cfg.get("history_period", "10y"))
    benchmarks: dict[str, str] = dict(pf_cfg.get("benchmarks", {}))
    if not benchmarks:
        raise ValueError("config.yaml の portfolio.benchmarks が空です")

    meta = load_universe(config)
    tickers = meta["ticker"].tolist() + list(benchmarks.values())
    prices = fetch_price_history(tickers, period=period, cache_dir=paths.get("cache_dir"))
    results = _run_portfolio_backtest(prices, meta, benchmarks, pf_cfg)
    report_path = Path(paths["reports_dir"]) / "portfolio-backtest.md"
    write_portfolio_report(results, report_path, period=period, cfg=pf_cfg)
    logger.info("ポートフォリオ・バックテストのレポートを生成しました: %s", report_path)
    return results


def _horizons(config: dict[str, Any]) -> list[int]:
    raw = config.get("tracking", {}).get("horizons", DEFAULT_HORIZONS)
    return [int(h) for h in raw]


def _save_fundamentals_snapshot(df: pd.DataFrame, snapshot_dir: str | None) -> None:
    """財務データのスナップショットを日付付き CSV で保存する。

    リポジトリにコミットして蓄積する (第1段階の選定ルールを後から過去時点の値で検証するため)。
    """
    if not snapshot_dir:
        return
    path = Path(snapshot_dir) / f"{now_jst():%Y-%m-%d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.round(6).to_csv(path, index=False)
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
        "signals", help="ウォッチリストの買いシグナルを判定し、台帳・成績表・レポートを更新 (日次)"
    )
    p_signals.add_argument("--no-notify", action="store_true", help="Slack通知を送らない")

    p_bt = subparsers.add_parser(
        "backtest", help="シグナルルールを過去データで検証して reports/backtest.md を生成"
    )
    p_bt.add_argument("--period", default=None,
                      help="株価履歴の期間 (yfinance 形式。既定は config の backtest.history_period)")
    p_bt.add_argument("--universe", action="store_true",
                      help="ウォッチリストではなく全ユニバースを対象にする")
    p_bt.add_argument("--limit", type=int, default=None, help="対象銘柄数を制限 (動作確認用)")

    p_pf = subparsers.add_parser(
        "portfolio", help="毎月上位N銘柄を保有した場合の成績を指数ETFと比較 → reports/portfolio-backtest.md"
    )
    p_pf.add_argument("--period", default=None,
                      help="株価履歴の期間 (既定は config の portfolio.history_period)")

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
    elif args.command == "backtest":
        run_backtest(config, period=args.period, universe=args.universe, limit=args.limit)
    elif args.command == "portfolio":
        run_portfolio(config, period=args.period)
    return 0


if __name__ == "__main__":
    sys.exit(main())
