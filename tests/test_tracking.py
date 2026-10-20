"""シグナル台帳・成績追跡・バックテストのテスト (合成データ使用)。"""

import numpy as np
import pandas as pd

from stock_screener.backtest import COMBO_NAME, run_backtest
from stock_screener.signals.technical import (
    SIGNAL_NAMES,
    classify_new_signals,
    compute_signal_frame,
    detect_signals,
    first_of_episode,
)
from stock_screener.tracking import (
    forward_returns,
    load_signal_log,
    results_to_log_rows,
    save_signal_log,
    summarize_returns,
    update_signal_log,
)

PARAMS = {
    "golden_cross_lookback": 5,
    "breakout_volume_mult": 1.5,
    "pullback_rsi_min": 30,
    "pullback_rsi_max": 45,
    "volume_surge_mult": 3.0,
    "volume_surge_min_return": 0.02,
    "renotify_after_days": 10,
}


def make_ohlcv(close, volume=None, start="2023-01-02"):
    close = np.asarray(close, dtype=float)
    if volume is None:
        volume = np.full(len(close), 1_000_000.0)
    index = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.asarray(volume, dtype=float),
    }, index=index)


def breakout_series():
    close = np.concatenate([np.linspace(100, 150, 300), [160.0]])
    volume = np.full(len(close), 1_000_000.0)
    volume[-1] = 5_000_000.0
    return make_ohlcv(close, volume)


class TestSignalFrame:
    def test_last_row_matches_detect_signals(self):
        # ベクトル版の最終行 == 日次判定 (ルールの一本化が保たれていること)
        for df in [breakout_series(), make_ohlcv(np.linspace(200, 100, 300)),
                   make_ohlcv(np.full(300, 100.0))]:
            frame = compute_signal_frame(df, PARAMS)
            signals, _ = detect_signals(df, PARAMS)
            assert [s for s in SIGNAL_NAMES if frame[s].iloc[-1]] == signals

    def test_insufficient_history_gives_empty_frame(self):
        frame = compute_signal_frame(make_ohlcv(np.full(100, 100.0)), PARAMS)
        assert frame.empty

    def test_warmup_days_have_no_signals(self):
        frame = compute_signal_frame(breakout_series(), PARAMS)
        assert not frame.loc[~frame["valid"], SIGNAL_NAMES].any().any()
        assert bool(frame["breakout_52w"].iloc[-1])


class TestFirstOfEpisode:
    def test_consecutive_days_count_once(self):
        flag = pd.Series([False, True, True, True, False, False, True])
        # gap=2: 最終日の直前2日は無成立 → 新規
        assert first_of_episode(flag, gap=2).tolist() == [False, True, False, False, False, False, True]
        # gap=3: 直前3日 (index 3〜5) に成立日がある → 継続扱い
        assert first_of_episode(flag, gap=3).tolist() == [False, True, False, False, False, False, False]

    def test_gap_zero_keeps_every_day(self):
        flag = pd.Series([True, True, False, True])
        assert first_of_episode(flag, gap=0).tolist() == [True, True, False, True]

    def test_reoccurrence_within_gap_is_not_new(self):
        flag = pd.Series([True, False, False, True])
        assert first_of_episode(flag, gap=5).tolist() == [True, False, False, False]
        assert first_of_episode(flag, gap=2).tolist() == [True, False, False, True]

    def test_classify_new_signals_breakout_is_new(self):
        df = breakout_series()
        signals, _ = detect_signals(df, PARAMS)
        is_new = classify_new_signals(df, signals, PARAMS)
        assert is_new["breakout_52w"] is True


class TestSignalLog:
    def test_update_is_idempotent_per_date(self, tmp_path):
        path = tmp_path / "log.csv"
        results = [{
            "ticker": "A.T", "name": "A", "market": "japan",
            "signals": ["pullback", "golden_cross"], "new_signals": ["golden_cross"],
            "metrics": {"close": 100.0, "rsi": 40.0, "volume_ratio": 1.0,
                        "pct_from_52w_high": -0.1},
            "score": 0.7, "bar_date": "2024-01-04",
        }]
        rows = results_to_log_rows(results, "2024-01-05")
        assert len(rows) == 2
        assert rows.set_index("signal").loc["golden_cross", "is_new"]
        assert not rows.set_index("signal").loc["pullback", "is_new"]

        log = update_signal_log(path, rows)
        log = update_signal_log(path, rows)  # 同じ日に再実行しても重複しない
        assert len(log) == 2
        reloaded = load_signal_log(path)
        assert len(reloaded) == 2
        assert reloaded["is_new"].dtype == bool

        other = results_to_log_rows(results, "2024-01-08")
        log = update_signal_log(path, other)
        assert len(log) == 4

    def test_empty_results_still_write_log(self, tmp_path):
        path = tmp_path / "log.csv"
        log = update_signal_log(path, results_to_log_rows([], "2024-01-05"))
        assert log.empty and path.exists()


class TestForwardReturns:
    def test_returns_at_horizons(self):
        close = np.arange(100, 400, dtype=float)  # 毎日+1
        df = make_ohlcv(close)
        bar_date = df.index[10].strftime("%Y-%m-%d")
        log = pd.DataFrame([{
            "date": bar_date, "bar_date": bar_date, "ticker": "X", "name": "X",
            "market": "us", "signal": "pullback", "is_new": True, "close": 110.0,
            "score": 0.5, "rsi": 40, "volume_ratio": 1.0, "pct_from_52w_high": 0.0,
        }])
        out = forward_returns(log, {"X": df}, horizons=[20, 1000])
        assert abs(out.loc[0, "ret_20"] - (130.0 / 110.0 - 1)) < 1e-9
        assert np.isnan(out.loc[0, "ret_1000"])  # 未到達
        assert out.loc[0, "days_elapsed"] == len(df) - 1 - 10
        assert abs(out.loc[0, "ret_now"] - (399.0 / 110.0 - 1)) < 1e-9

    def test_persisted_returns_survive_reload_and_are_not_recomputed(self, tmp_path):
        path = tmp_path / "log.csv"
        close = np.arange(100, 400, dtype=float)
        df = make_ohlcv(close)
        bar_date = df.index[10].strftime("%Y-%m-%d")
        log = pd.DataFrame([{
            "date": bar_date, "bar_date": bar_date, "ticker": "X", "name": "X",
            "market": "us", "signal": "pullback", "is_new": True, "close": 110.0,
            "score": 0.5, "rsi": 40, "volume_ratio": 1.0, "pct_from_52w_high": 0.0,
        }])
        evaluated = forward_returns(log, {"X": df}, horizons=[20])
        save_signal_log(path, evaluated)
        reloaded = load_signal_log(path)
        assert "ret_20" in reloaded.columns and "ret_now" not in reloaded.columns
        # 株価が無くても確定済みの値は保持される
        again = forward_returns(reloaded, {}, horizons=[20])
        assert abs(again.loc[0, "ret_20"] - (130.0 / 110.0 - 1)) < 1e-9

    def test_missing_ticker_gives_nan(self):
        log = pd.DataFrame([{
            "date": "2024-01-05", "bar_date": "2024-01-04", "ticker": "NONE",
            "name": "", "market": "", "signal": "pullback", "is_new": True,
            "close": 1.0, "score": 0.5, "rsi": 1, "volume_ratio": 1, "pct_from_52w_high": 0,
        }])
        out = forward_returns(log, {}, horizons=[20])
        assert np.isnan(out.loc[0, "ret_20"])

    def test_summary_win_rate_and_mean(self):
        entries = pd.DataFrame({
            "signal": ["pullback", "pullback", "golden_cross"],
            "ret_20": [0.10, -0.05, np.nan],
        })
        summary = summarize_returns(entries, [20], "signal").set_index("signal")
        assert summary.loc["pullback", "n_20"] == 2
        assert abs(summary.loc["pullback", "win_20"] - 0.5) < 1e-9
        assert abs(summary.loc["pullback", "mean_20"] - 0.025) < 1e-9
        assert summary.loc["golden_cross", "n_20"] == 0
        assert summary.loc["all", "entries"] == 3


class TestBacktest:
    def test_entries_and_baseline(self):
        prices = {"UP": breakout_series(), "DOWN": make_ohlcv(np.linspace(200, 100, 300))}
        meta = pd.DataFrame({"ticker": ["UP", "DOWN"], "market": ["us", "japan"]})
        result = run_backtest(prices, PARAMS, horizons=[5, 20], gap=10, meta=meta)
        entries = result["entries"]
        assert result["n_tickers"] == 2
        # 最終日のブレイクアウトはエントリーとして記録され、先のリターンは未到達 (NaN)
        bo = entries[(entries["ticker"] == "UP") & (entries["signal"] == "breakout_52w")]
        assert len(bo) == 1 and np.isnan(bo["ret_5"].iloc[0])
        assert (entries["market"].isin(["us", "japan"])).all()
        summary = result["summary"].set_index("signal")
        assert "baseline" in summary.index
        assert summary.loc["baseline", "entries"] == result["n_days"]
        assert "excess_5" in summary.columns
        by_market = result["by_market"]
        assert set(by_market["market"]) <= {"us", "japan"}
        assert COMBO_NAME in summary.index or entries.empty is False

    def test_no_valid_history(self):
        result = run_backtest({"S": make_ohlcv(np.full(50, 1.0))}, PARAMS, [20], 10)
        assert result["n_tickers"] == 0
        assert result["entries"].empty
