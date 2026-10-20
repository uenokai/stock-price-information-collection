"""モデルポートフォリオ (ペーパー運用)。

バックテストで良かった戦略を「今なら何を持つか」の具体的な銘柄リストに落とし、
その後の評価額を指数ETFと並べて毎日記録する。注文は出さない。

- 戦略ごとに市場別 上位N銘柄を等金額で保有し、月初 (その月の最初の実行日) に入れ替える
  (ポートフォリオ・バックテストと同じルール。入れ替えコストも同じ式で控除)
- 状態は data/model_portfolio/ の CSV (holdings / trades / nav) に持ち、リポジトリにコミットする
- 過去検証 (backtest) では測れない「その後 (アウトオブサンプル) の成績」がここに溜まる
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

STRATEGIES = {
    "momentum": {
        "label": "モメンタム戦略",
        "description": "各市場で 12-1ヶ月モメンタム上位N銘柄を等金額保有。バックテストで指数超過を確認済み",
        "rank_col": "momentum_12_1",
    },
    "composite": {
        "label": "合成スコア戦略",
        "description": "各市場でウォッチリスト (財務指標+モメンタムの合成スコア) 上位N銘柄を等金額保有。財務指標の効果は未検証",
        "rank_col": "score",
    },
}

HOLDINGS_COLUMNS = ["portfolio", "ticker", "name", "market", "units", "entry_date", "entry_price"]
TRADES_COLUMNS = ["date", "portfolio", "ticker", "name", "market", "side", "price", "reason"]
NAV_COLUMNS = ["date", "portfolio", "nav", "bench_nav", "n_holdings", "last_rebalance"]
_FILES = {"holdings": HOLDINGS_COLUMNS, "trades": TRADES_COLUMNS, "nav": NAV_COLUMNS}


# ---------------------------------------------------------------------------
# 状態の読み書き
# ---------------------------------------------------------------------------

def load_state(state_dir: str | Path) -> dict[str, pd.DataFrame]:
    state = {}
    for name, columns in _FILES.items():
        path = Path(state_dir) / f"{name}.csv"
        if path.exists():
            df = pd.read_csv(path, dtype={"ticker": str, "portfolio": str})
            for col in columns:
                if col not in df.columns:
                    df[col] = np.nan
            state[name] = df[columns]
        else:
            state[name] = pd.DataFrame(columns=columns)
    return state


def save_state(state_dir: str | Path, state: dict[str, pd.DataFrame]) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    for name, columns in _FILES.items():
        df = state[name][columns]
        if name == "nav":
            df = df.sort_values(["date", "portfolio"])
        df.to_csv(Path(state_dir) / f"{name}.csv", index=False)


# ---------------------------------------------------------------------------
# 選定・評価・リバランス
# ---------------------------------------------------------------------------

def select_targets(candidates: pd.DataFrame, rank_col: str, top_n: int) -> pd.DataFrame:
    """市場ごとに rank_col 降順で上位 top_n を選ぶ。columns: ticker, name, market, rank_col。"""
    if candidates.empty or rank_col not in candidates.columns:
        return pd.DataFrame(columns=["ticker", "name", "market", rank_col])
    df = candidates.dropna(subset=[rank_col]).drop_duplicates(subset="ticker")
    parts = [sub.sort_values(rank_col, ascending=False).head(top_n)
             for _, sub in df.groupby("market", sort=True)]
    out = pd.concat(parts, ignore_index=True) if parts else df.head(0)
    return out[["ticker", "name", "market", rank_col]].reset_index(drop=True)


def last_close(prices: dict[str, pd.DataFrame], ticker: str, date: str | None = None) -> float | None:
    """date 以前の最後の終値。無ければ None。"""
    df = prices.get(ticker)
    if df is None or df.empty:
        return None
    close = df["close"].dropna()
    if date is not None:
        idx = pd.DatetimeIndex(close.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        close = close[idx.normalize() <= pd.Timestamp(date).normalize()]
    if close.empty:
        return None
    return float(close.iloc[-1])


def value_of(holdings: pd.DataFrame, prices: dict[str, pd.DataFrame], date: str | None = None) -> float:
    """保有の評価額 (価格が取れない銘柄は取得価格で据え置き)。"""
    total = 0.0
    for row in holdings.itertuples(index=False):
        price = last_close(prices, row.ticker, date)
        if price is None:
            price = float(row.entry_price)
        total += float(row.units) * price
    return total


def rebalance(
    holdings: pd.DataFrame,
    nav_value: float,
    targets: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    date: str,
    portfolio: str,
    cost_rate: float,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """等金額に組み直す。戻り値: (新しい保有, 売買記録, コスト控除後の評価額)。

    価格が取れない対象銘柄は見送る (その分は他の銘柄に等分される)。
    継続保有の銘柄は取得日・取得価格を引き継ぐ (ウェイトだけ等金額に戻す)。
    """
    priced = []
    for row in targets.itertuples(index=False):
        price = last_close(prices, row.ticker, date)
        if price is not None and price > 0:
            priced.append((row.ticker, getattr(row, "name", row.ticker), getattr(row, "market", ""), price))
        else:
            logger.warning("%s: %s の価格が無いため見送り", portfolio, row.ticker)

    prev = set(holdings["ticker"]) if not holdings.empty else set()
    new = {t for t, _, _, _ in priced}
    n_ref = max(len(prev), len(new), 1)
    turnover = len(prev ^ new) / (2 * n_ref) if (prev or new) else 0.0
    nav_after = nav_value * (1.0 - turnover * cost_rate)

    trades = []
    for row in holdings.itertuples(index=False):
        if row.ticker not in new:
            price = last_close(prices, row.ticker, date) or float(row.entry_price)
            trades.append([date, portfolio, row.ticker, row.name, row.market, "sell", price, "rebalance"])
    old = holdings.set_index("ticker") if not holdings.empty else None
    rows = []
    per_name = nav_after / len(priced) if priced else 0.0
    for ticker, name, market, price in priced:
        units = per_name / price
        if old is not None and ticker in old.index:
            entry_date, entry_price = old.loc[ticker, "entry_date"], float(old.loc[ticker, "entry_price"])
        else:
            entry_date, entry_price = date, price
            trades.append([date, portfolio, ticker, name, market, "buy", price, "rebalance"])
        rows.append([portfolio, ticker, name, market, units, entry_date, entry_price])
    new_holdings = pd.DataFrame(rows, columns=HOLDINGS_COLUMNS)
    trades_df = pd.DataFrame(trades, columns=TRADES_COLUMNS)
    logger.info("%s: リバランス %s (保有 %d → %d 銘柄, 入替率 %.0f%%, 売買 %d 件)",
                portfolio, date, len(prev), len(new), turnover * 100, len(trades_df))
    return new_holdings, trades_df, nav_after


def benchmark_nav(prices: dict[str, pd.DataFrame], bench_tickers: list[str],
                  inception: str, date: str) -> float:
    """指数ETFを等金額で持った場合の、開始日=1.0 に対する現在値。"""
    ratios = []
    for t in bench_tickers:
        p0 = last_close(prices, t, inception)
        p1 = last_close(prices, t, date)
        if p0 and p1:
            ratios.append(p1 / p0)
    return float(np.mean(ratios)) if ratios else float("nan")


def should_rebalance(last_rebalance: str | None, date: str) -> bool:
    """月が変わっていればリバランス (その月の最初の実行日に行う)。"""
    if not last_rebalance or (isinstance(last_rebalance, float) and np.isnan(last_rebalance)):
        return True
    return str(last_rebalance)[:7] != date[:7]


def run_model_portfolio(
    state: dict[str, pd.DataFrame],
    targets_by_strategy: dict[str, pd.DataFrame],
    prices: dict[str, pd.DataFrame],
    bench_tickers: list[str],
    date: str,
    cost_rate: float,
    force_rebalance: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """全戦略について評価 (+必要ならリバランス) し、状態と要約を返す。同じ日の再実行は上書き。"""
    holdings_all, trades_all, nav_all = state["holdings"], state["trades"], state["nav"]
    summary: dict[str, Any] = {}

    for name, targets in targets_by_strategy.items():
        h = holdings_all[holdings_all["portfolio"] == name].reset_index(drop=True)
        nav_hist = nav_all[(nav_all["portfolio"] == name) & (nav_all["date"] < date)].sort_values("date")
        inception = str(nav_hist["date"].iloc[0]) if not nav_hist.empty else date
        last_rb = str(nav_hist["last_rebalance"].iloc[-1]) if not nav_hist.empty else None

        if h.empty:
            nav_value, do_rb = 1.0, True
        else:
            nav_value = value_of(h, prices, date)
            do_rb = force_rebalance or should_rebalance(last_rb, date)

        trades_today = pd.DataFrame(columns=TRADES_COLUMNS)
        if do_rb and not targets.empty:
            h, trades_today, nav_value = rebalance(h, nav_value, targets, prices, date, name, cost_rate)
            last_rb = date
        elif do_rb:
            logger.warning("%s: 選定対象が空のためリバランスを見送り", name)

        bench = benchmark_nav(prices, bench_tickers, inception, date)
        holdings_all = pd.concat([holdings_all[holdings_all["portfolio"] != name], h], ignore_index=True)
        if not trades_today.empty:
            # 同じ日の再実行で売買が出た場合だけ置き換える (売買ゼロの再実行で記録を消さない)
            trades_all = pd.concat(
                [trades_all[~((trades_all["portfolio"] == name) & (trades_all["date"] == date))], trades_today],
                ignore_index=True)
        trades_last = trades_all[(trades_all["portfolio"] == name) & (trades_all["date"] == str(last_rb))]
        nav_row = pd.DataFrame([[date, name, nav_value, bench, len(h), last_rb]], columns=NAV_COLUMNS)
        nav_all = pd.concat([nav_all[~((nav_all["portfolio"] == name) & (nav_all["date"] == date))],
                             nav_row], ignore_index=True)

        detail = []
        for row in h.itertuples(index=False):
            price = last_close(prices, row.ticker, date)
            detail.append({
                "ticker": row.ticker, "name": row.name, "market": row.market,
                "entry_date": row.entry_date, "entry_price": float(row.entry_price),
                "price": price, "weight": (float(row.units) * price / nav_value) if price and nav_value else np.nan,
                "ret": (price / float(row.entry_price) - 1.0) if price else np.nan,
            })
        summary[name] = {
            "nav": nav_value, "bench_nav": bench, "inception": inception, "last_rebalance": last_rb,
            "rebalanced_today": bool(do_rb and not targets.empty),
            "holdings": pd.DataFrame(detail), "trades_today": trades_today,
            "trades_last_rebalance": trades_last.reset_index(drop=True),
            "nav_history": nav_all[nav_all["portfolio"] == name].sort_values("date"),
        }
        logger.info("%s: 評価額 %.4f (指数 %.4f), 保有 %d 銘柄, 開始 %s", name, nav_value, bench, len(h), inception)

    state = {"holdings": holdings_all, "trades": trades_all, "nav": nav_all}
    return state, summary
