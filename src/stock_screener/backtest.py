"""バックテスト (Phase 3)。

ウォッチリスト (または全ユニバース) の過去数年分の日足に、本番と同じルール
(`compute_signal_frame`) を適用し、シグナル成立日からの N 営業日後リターンを集計する。
「全営業日に買った場合」(ベースライン) と比べて、シグナルに上乗せ効果があるかを見る。

注意: 現在のウォッチリストは「今の」財務指標で選ばれた銘柄なので、過去の値動きに
適用すると生存バイアス・先読みバイアスが入る。ここで検証できるのは主に
第2段階 (タイミング判定) のルールであり、第1段階の選定効果ではない。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .signals.technical import SIGNAL_NAMES, compute_signal_frame, first_of_episode
from .tracking import summarize_returns

logger = logging.getLogger(__name__)

COMBO_NAME = "combo_2plus"  # 2つ以上のシグナルが同日に成立
UPTREND_NAME = "uptrend_days"  # 上昇トレンド中の全営業日 (トレンドフィルタの効果を見る)


def run_backtest(
    prices: dict[str, pd.DataFrame],
    params: dict[str, Any],
    horizons: list[int],
    gap: int,
    meta: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """全銘柄にシグナルルールを適用し、エントリー一覧と成績表を返す。

    Args:
        prices: {ticker: 日足OHLCV}
        params: config.yaml の signals セクション
        horizons: 評価する営業日数のリスト
        gap: 新規エントリーとみなす間隔 (営業日) = renotify_after_days
        meta: ticker, name, market 等を含む DataFrame (レポート表示用、任意)

    Returns:
        {"entries": DataFrame, "baseline": DataFrame, "summary": DataFrame,
         "by_market": DataFrame, "n_tickers": int, "n_days": int}
    """
    entry_frames = []
    baseline_frames = []
    n_tickers = 0
    n_days = 0
    for ticker, df in prices.items():
        frame = compute_signal_frame(df, params)
        if frame.empty:
            continue
        valid = frame[frame["valid"]]
        if valid.empty:
            continue
        n_tickers += 1
        n_days += len(valid)
        close = frame["close"]
        fwd = {h: close.shift(-h) / close - 1.0 for h in horizons}

        base = pd.DataFrame({f"ret_{h}": fwd[h] for h in horizons}, index=frame.index)
        base["uptrend"] = frame["uptrend"].fillna(False).astype(bool)
        base = base[frame["valid"]]
        base["ticker"] = ticker
        baseline_frames.append(base)

        n_signals = frame[SIGNAL_NAMES].sum(axis=1)
        combos = {name: first_of_episode(frame[name], gap) for name in SIGNAL_NAMES}
        combos[COMBO_NAME] = first_of_episode(n_signals >= 2, gap)
        for name, flag in combos.items():
            dates = frame.index[flag.values]
            if len(dates) == 0:
                continue
            e = pd.DataFrame({
                "ticker": ticker,
                "date": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates],
                "signal": name,
                "close": close.loc[dates].values,
                "n_signals": n_signals.loc[dates].values,
            })
            for h in horizons:
                e[f"ret_{h}"] = fwd[h].loc[dates].values
            entry_frames.append(e)

    entries = (pd.concat(entry_frames, ignore_index=True) if entry_frames
               else pd.DataFrame(columns=["ticker", "date", "signal", "close", "n_signals"]
                                 + [f"ret_{h}" for h in horizons]))
    baseline = (pd.concat(baseline_frames, ignore_index=True) if baseline_frames
                else pd.DataFrame(columns=[f"ret_{h}" for h in horizons] + ["ticker"]))

    if meta is not None and "market" in meta.columns:
        market_map = meta.set_index("ticker")["market"].to_dict()
        entries["market"] = entries["ticker"].map(market_map).fillna("")
        baseline["market"] = baseline["ticker"].map(market_map).fillna("")

    summary = summarize_returns(entries, horizons, "signal", baseline=baseline)
    base_row = summarize_returns(baseline, horizons, "ticker").tail(1).copy()
    base_row["ticker"] = "baseline"
    base_row = base_row.rename(columns={"ticker": "signal"})
    extra = [base_row]
    if "uptrend" in baseline.columns and not baseline.empty:
        up = baseline[baseline["uptrend"]]
        up_row = summarize_returns(up, horizons, "ticker", baseline=baseline).tail(1).copy()
        up_row["ticker"] = UPTREND_NAME
        extra.insert(0, up_row.rename(columns={"ticker": "signal"}))
    summary = pd.concat([summary] + extra, ignore_index=True)

    by_market = pd.DataFrame()
    if "market" in entries.columns and not entries.empty:
        core = entries[entries["signal"] != COMBO_NAME]
        by_market = summarize_returns(core, horizons, "market")
        by_market = by_market[by_market["market"] != "all"]

    logger.info("バックテスト完了: %d 銘柄, %d 営業日分, エントリー %d 件",
                n_tickers, n_days, len(entries[entries["signal"] != COMBO_NAME]))
    return {
        "entries": entries,
        "baseline": baseline,
        "summary": summary,
        "by_market": by_market,
        "n_tickers": n_tickers,
        "n_days": n_days,
    }
