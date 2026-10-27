"""保有銘柄単位の過去検証。

「毎月、ある基準で上位N銘柄を選んで等金額で保有する」戦略の成績を、
指数ETF (買って持ち続ける) と比べる。大目的である「リターンの最大化」に
直結する物差しは、個別合図の勝率ではなく「指数に勝てるか」なので、
この比較を最上位の評価指標として扱う。

価格だけで決まる基準 (株価上昇率・傾向) は過去10年で検証できる。
財務指標による基準は過去時点の値が無いため、週次記録が溜まるまで検証できない。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


def build_close_panel(prices: dict[str, pd.DataFrame], tickers: list[str] | None = None) -> pd.DataFrame:
    """{ticker: OHLCV} → 終値のパネル (index=日付, columns=ticker)。上場前・欠損は NaN。"""
    tickers = tickers or list(prices)
    series = {}
    for t in tickers:
        df = prices.get(t)
        if df is None or df.empty:
            continue
        s = df["close"].astype(float)
        idx = pd.DatetimeIndex(s.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        s.index = idx.normalize()
        series[t] = s[~s.index.duplicated(keep="last")]
    if not series:
        return pd.DataFrame()
    panel = pd.DataFrame(series).sort_index()
    return panel


def momentum_score(panel: pd.DataFrame, lookback: int = 252, skip: int = 21) -> pd.DataFrame:
    """株価上昇率: (skip 営業日前の終値) / (lookback 営業日前の終値) - 1。

    直近1ヶ月を除くのは短期の反転 (直近1ヶ月は逆に動きやすい) を避ける標準的な定義。
    """
    p = panel.ffill()  # 祝日等の欠損で NaN にならないように直前値で埋める
    return p.shift(skip) / p.shift(lookback) - 1.0


def latest_momentum(prices: dict[str, pd.DataFrame], lookback: int = 252, skip: int = 21) -> pd.Series:
    """各銘柄の最新の 株価上昇率 (週次絞り込みの指標として使う)。履歴不足は NaN。

    市場ごとに営業日が違うので、銘柄ごとに自分の営業日で数える (パネルの行位置は使わない)。
    """
    out = {}
    for ticker, df in prices.items():
        if df is None or df.empty:
            continue
        close = df["close"].dropna().astype(float)
        if len(close) <= lookback:
            out[ticker] = np.nan
            continue
        base = float(close.iloc[-1 - lookback])
        out[ticker] = float(close.iloc[-1 - skip]) / base - 1.0 if base > 0 else np.nan
    return pd.Series(out, dtype=float, name="momentum_12_1")


def trend_mask(panel: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    """株価が window 日移動平均より上なら True。"""
    return panel > panel.rolling(window, min_periods=window).mean()


def rebalance_dates(index: pd.DatetimeIndex, freq: str = "M") -> list[pd.Timestamp]:
    """各期間 (月) の最初の営業日。"""
    s = pd.Series(index, index=index)
    key = index.to_period("M") if freq == "M" else index.to_period("Q")
    return [pd.Timestamp(v) for v in s.groupby(key).first()]


def simulate(
    panel: pd.DataFrame,
    score: pd.DataFrame | None,
    top_n: int | None,
    eligible: pd.DataFrame | None = None,
    freq: str = "M",
    cost_rate: float = 0.002,
    start: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """毎入れ替え日に上位 top_n (score 降順) を等金額で買い、次の入れ替え日まで保有する。

    Args:
        panel: 終値パネル
        score: 選定点数 (panel と同じ形)。None なら eligible な全銘柄を等金額
        top_n: 保有銘柄数。None なら eligible な全銘柄
        eligible: 保有可能フラグ (例: 上昇傾向の絞り込み)。None なら価格がある銘柄すべて
        freq: 入れ替え頻度 ("M" 月次 / "Q" 四半期)
        cost_rate: 入れ替え手数料 (往復)。入れ替えた銘柄の割合 × cost_rate を控除
        start: この日以降の入れ替えから開始

    Returns:
        {"value": 資産推移 (初期1.0), "holdings": {日付: [ticker,...]}, "turnover": 平均入れ替え率}
    """
    dates = rebalance_dates(panel.index, freq)
    if start is not None:
        dates = [d for d in dates if d >= start]
    value = 1.0
    path_parts: list[pd.Series] = []
    holdings: dict[pd.Timestamp, list[str]] = {}
    turnovers: list[float] = []
    prev: set[str] = set()

    for i, t0 in enumerate(dates):
        t1 = dates[i + 1] if i + 1 < len(dates) else panel.index[-1]
        if t0 >= t1:
            continue
        avail = panel.loc[t0].notna()
        if eligible is not None:
            avail &= eligible.loc[t0].fillna(False).astype(bool)
        candidates = avail[avail].index.tolist()
        if score is not None:
            sc = score.loc[t0, candidates].dropna().sort_values(ascending=False)
            candidates = sc.index.tolist()
            if top_n:
                candidates = candidates[:top_n]
        selected = candidates
        holdings[t0] = selected

        # 入れ替え手数料: 入れ替えた割合 × cost_rate (初回の買い付けは片道分のみ、入替率の平均には含めない)
        if prev or selected:
            n_ref = max(len(prev), len(selected), 1)
            changed = len(set(selected) ^ prev) / (2 * n_ref)
            if prev:
                turnovers.append(changed)
            value *= 1.0 - changed * cost_rate
        prev = set(selected)

        seg = panel.loc[t0:t1]
        if not selected:
            part = pd.Series(value, index=seg.index)  # 現金保有 (リターン0)
        else:
            rel = seg[selected].ffill() / seg[selected].iloc[0]
            part = rel.mean(axis=1) * value  # t0 で等金額、その後は保有したまま (比率は自然に変動)
        end_value = float(part.iloc[-1])
        if i + 1 < len(dates):
            part = part.iloc[:-1]  # t1 は次の区間の先頭と重複するので除く
        path_parts.append(part)
        value = end_value

    path = pd.concat(path_parts) if path_parts else pd.Series(dtype=float)
    return {
        "value": path,
        "holdings": holdings,
        "turnover": float(np.mean(turnovers)) if turnovers else 0.0,
    }


def buy_and_hold(series: pd.Series, start: pd.Timestamp | None = None) -> pd.Series:
    """1本の価格系列を買って持ち続けた場合の資産推移 (初期1.0)。"""
    s = series.dropna()
    if start is not None:
        s = s[s.index >= start]
    if s.empty:
        return s
    return s / float(s.iloc[0])


def performance_metrics(value: pd.Series) -> dict[str, float]:
    """資産推移から 年平均リターン・値動きの大きさ・最大下落率・リターン効率 (無リスク0) を計算する。"""
    v = value.dropna()
    if len(v) < 2:
        return {"total": np.nan, "cagr": np.nan, "vol": np.nan, "max_dd": np.nan,
                "sharpe": np.nan, "years": np.nan}
    years = (v.index[-1] - v.index[0]).days / 365.25
    total = float(v.iloc[-1] / v.iloc[0] - 1.0)
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else np.nan
    daily = v.pct_change().dropna()
    vol = float(daily.std() * math.sqrt(TRADING_DAYS)) if len(daily) > 1 else np.nan
    max_dd = float((v / v.cummax() - 1.0).min())
    sharpe = float(daily.mean() / daily.std() * math.sqrt(TRADING_DAYS)) if len(daily) > 1 and daily.std() > 0 else np.nan
    return {"total": total, "cagr": cagr, "vol": vol, "max_dd": max_dd, "sharpe": sharpe, "years": years}


def yearly_returns(value: pd.Series) -> pd.Series:
    """暦年ごとのリターン。"""
    v = value.dropna()
    if v.empty:
        return pd.Series(dtype=float)
    year_end = v.groupby(v.index.year).last()
    year_start = v.groupby(v.index.year).first()
    first_year = year_end.index[0]
    out = year_end / year_end.shift(1) - 1.0
    out.loc[first_year] = year_end.loc[first_year] / year_start.loc[first_year] - 1.0
    return out


def run_portfolio_backtest(
    prices: dict[str, pd.DataFrame],
    meta: pd.DataFrame,
    benchmarks: dict[str, str],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """市場ごとに戦略を走らせ、指数ETFと比較する。

    Args:
        prices: 全銘柄 + 比較対象の指数の日足
        meta: ticker, market を含む DataFrame
        benchmarks: {market: 比較対象の指数の銘柄コード}
        cfg: config.yaml の portfolio セクション

    Returns:
        {market: {"strategies": {name: {"value": Series, "metrics": dict, "turnover": float}},
                  "yearly": DataFrame, "n_tickers": int, "start": Timestamp, "end": Timestamp}}
    """
    top_n = int(cfg.get("top_n_per_market", 15))
    freq = str(cfg.get("rebalance", "M"))
    cost_rate = float(cfg.get("cost_rate", 0.002))
    lookback = int(cfg.get("momentum_lookback", 252))
    skip = int(cfg.get("momentum_skip", 21))
    trend_window = int(cfg.get("trend_window", 200))
    warmup = max(lookback, trend_window) + 5

    results: dict[str, Any] = {}
    for market, bench_ticker in benchmarks.items():
        tickers = meta.loc[meta["market"] == market, "ticker"].tolist()
        panel = build_close_panel(prices, tickers)
        bench_panel = build_close_panel(prices, [bench_ticker])
        if panel.empty or bench_panel.empty:
            logger.warning("%s: 価格データ不足のためスキップ", market)
            continue
        bench = bench_panel.iloc[:, 0]
        # 指数と銘柄の両方に価格がある日だけ使う
        common = panel.index.intersection(bench.index)
        panel = panel.loc[common]
        bench = bench.loc[common]
        if len(panel) <= warmup + 40:
            logger.warning("%s: 履歴が短すぎます (%d 日)", market, len(panel))
            continue
        start = panel.index[warmup]

        mom = momentum_score(panel, lookback, skip)
        trend = trend_mask(panel, trend_window)

        strategies = {
            "index": {"value": buy_and_hold(bench, start), "turnover": 0.0},
            "universe_ew": simulate(panel, None, None, None, freq, cost_rate, start),
            "momentum_top": simulate(panel, mom, top_n, None, freq, cost_rate, start),
            "momentum_trend": simulate(panel, mom, top_n, trend, freq, cost_rate, start),
            "trend_only": simulate(panel, None, None, trend, freq, cost_rate, start),
        }
        for name, res in strategies.items():
            res["metrics"] = performance_metrics(res["value"])
        yearly = pd.DataFrame({name: yearly_returns(res["value"]) for name, res in strategies.items()})
        results[market] = {
            "strategies": strategies,
            "yearly": yearly,
            "n_tickers": int(panel.notna().any().sum()),
            "benchmark": bench_ticker,
            "start": start,
            "end": panel.index[-1],
            "top_n": top_n,
        }
        logger.info("%s: %d 銘柄, %s〜%s, 戦略 %d 本", market, results[market]["n_tickers"],
                    f"{start:%Y-%m-%d}", f"{panel.index[-1]:%Y-%m-%d}", len(strategies))
    return results
