"""パイプライン統合テスト。

データ取得層をモックし、screen → watchlist → signals → レポート生成の
一連の流れが正しく配線されていることを確認する (ネットワーク不要)。
"""

import numpy as np
import pandas as pd
import yaml

from stock_screener import cli
from stock_screener.config import load_config

N_TICKERS = 8


def make_config(tmp_path):
    universe_file = tmp_path / "universe.csv"
    pd.DataFrame({
        "ticker": [f"TST{i}.T" for i in range(N_TICKERS)],
        "name": [f"テスト銘柄{i}" for i in range(N_TICKERS)],
    }).to_csv(universe_file, index=False)

    config = {
        "universe": {
            "japan": {"file": str(universe_file), "min_market_cap": 1000},
        },
        "screening": {
            "watchlist_size": 5,
            "min_metrics_required": 2,
            "min_sector_size": 3,
            "weights": {"roe": 0.5, "pe": 0.5},
        },
        "signals": {
            "history_period": "2y",
            "golden_cross_lookback": 5,
            "breakout_volume_mult": 1.5,
            "pullback_rsi_min": 30,
            "pullback_rsi_max": 45,
            "volume_surge_mult": 3.0,
            "volume_surge_min_return": 0.02,
        },
        "paths": {
            "cache_dir": str(tmp_path / "cache"),
            "fundamentals_dir": str(tmp_path / "fundamentals"),
            "watchlist": str(tmp_path / "watchlist.csv"),
            "reports_dir": str(tmp_path / "reports"),
        },
        "fetch": {"fundamentals_pause": 0, "max_retries": 0},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return config_path


def fake_fundamentals(tickers, **kwargs):
    n = len(tickers)
    return pd.DataFrame({
        "ticker": tickers,
        "sector": ["Tech"] * n,
        "industry": ["Software"] * n,
        "currency": ["JPY"] * n,
        # TST0が最も高ROE・低PERになるように並べる
        "market_cap": [1_000_000.0] * n,
        "roe": np.linspace(0.30, 0.02, n),
        "operating_margin": np.linspace(0.25, 0.05, n),
        "revenue_growth": [0.10] * n,
        "earnings_growth": [0.08] * n,
        "pe": np.linspace(8.0, 40.0, n),
        "pb": np.linspace(1.0, 5.0, n),
        "dividend_yield": [0.02] * n,
        "debt_to_equity": [50.0] * n,
    })


def fake_prices(tickers, **kwargs):
    # 全銘柄: 52週高値を大出来高でブレイクする値動き
    close = np.concatenate([np.linspace(100, 150, 300), [160.0]])
    volume = np.full(len(close), 1_000_000.0)
    volume[-1] = 5_000_000.0
    index = pd.bdate_range("2023-01-02", periods=len(close))
    df = pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": volume,
    }, index=index)
    return {t: df.copy() for t in tickers}


def test_screen_then_signals_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "fetch_fundamentals", fake_fundamentals)
    monkeypatch.setattr(cli, "fetch_price_history", fake_prices)

    config = load_config(make_config(tmp_path))

    # 第1段階: スクリーニング
    watchlist = cli.run_screen(config)
    assert len(watchlist) == 5
    assert watchlist.iloc[0]["ticker"] == "TST0.T"  # 最良ファンダの銘柄が1位
    assert (tmp_path / "watchlist.csv").exists()
    watchlist_md = (tmp_path / "reports" / "watchlist.md").read_text(encoding="utf-8")
    assert "TST0.T" in watchlist_md

    # 第2段階: シグナル判定 (通知なし)
    results = cli.run_signals(config, notify=False)
    assert len(results) == 5  # 全銘柄がブレイクアウト
    assert all("breakout_52w" in r["signals"] for r in results)

    latest = (tmp_path / "reports" / "latest-signals.md").read_text(encoding="utf-8")
    assert "52週高値ブレイク" in latest
    assert "TST0.T" in latest
    dated_reports = list((tmp_path / "reports" / "signals").glob("*.md"))
    assert len(dated_reports) == 1


def test_signals_auto_runs_screen_when_watchlist_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "fetch_fundamentals", fake_fundamentals)
    monkeypatch.setattr(cli, "fetch_price_history", fake_prices)

    config = load_config(make_config(tmp_path))
    # screenを実行せずにsignalsを呼んでも、自動でスクリーニングが走る
    results = cli.run_signals(config, notify=False)
    assert (tmp_path / "watchlist.csv").exists()
    assert len(results) == 5
