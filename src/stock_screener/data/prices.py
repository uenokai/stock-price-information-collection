"""株価履歴 (日足OHLCV) の取得。

yfinance でバッチ取得し、Parquet にキャッシュする。
キャッシュが新しければ再取得しない (ローカルでの試行錯誤を高速化するため。
GitHub Actions ではランナーが毎回まっさらなので常に新規取得になる)。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_FILENAME = "prices.parquet"
_PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


def fetch_price_history(
    tickers: list[str],
    period: str = "2y",
    cache_dir: str | Path | None = None,
    max_age_hours: float = 20.0,
) -> dict[str, pd.DataFrame]:
    """各ティッカーの日足OHLCVを返す。

    Returns:
        {ticker: DataFrame(index=日付, columns=[open, high, low, close, volume])}
        取得に失敗した銘柄は含まれない。
    """
    tickers = list(dict.fromkeys(tickers))  # 順序を保って重複除去

    cached = _load_cache(tickers, cache_dir, max_age_hours)
    if cached is not None:
        return cached

    logger.info("株価履歴を取得中: %d 銘柄 (期間: %s)", len(tickers), period)
    raw = yf.download(
        tickers,
        period=period,
        interval="1d",
        auto_adjust=True,
        group_by="ticker",
        progress=False,
        threads=True,
    )
    result = _split_by_ticker(raw, tickers)

    failed = len(tickers) - len(result)
    if failed:
        logger.warning("%d 銘柄の株価取得に失敗しました", failed)
    if result and cache_dir is not None:
        _save_cache(result, cache_dir)
    return result


def _split_by_ticker(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yf.download の結果をティッカーごとの DataFrame に分解する。"""
    result: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return result

    multi = isinstance(raw.columns, pd.MultiIndex)
    for ticker in tickers:
        if multi:
            if ticker not in raw.columns.get_level_values(0):
                continue
            sub = raw[ticker].copy()
        else:
            # 1銘柄のみの場合は単層カラムで返ることがある
            sub = raw.copy()
        sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
        sub = sub[[c for c in _PRICE_COLUMNS if c in sub.columns]]
        sub = sub.dropna(subset=["close"])
        if not sub.empty:
            sub.index.name = "date"
            result[ticker] = sub
    return result


def _cache_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / CACHE_FILENAME


def _load_cache(
    tickers: list[str], cache_dir: str | Path | None, max_age_hours: float
) -> dict[str, pd.DataFrame] | None:
    if cache_dir is None:
        return None
    path = _cache_path(cache_dir)
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > max_age_hours:
        logger.info("株価キャッシュが古いため再取得します (%.1f 時間経過)", age_hours)
        return None
    stacked = pd.read_parquet(path)
    cached_tickers = set(stacked["ticker"].unique())
    if not set(tickers) <= cached_tickers:
        logger.info("キャッシュにない銘柄があるため再取得します")
        return None
    logger.info("株価キャッシュを使用します: %s (%.1f 時間前)", path, age_hours)
    result = {}
    for ticker, group in stacked.groupby("ticker"):
        if ticker in tickers:
            result[ticker] = group.set_index("date")[_PRICE_COLUMNS]
    return result


def _save_cache(result: dict[str, pd.DataFrame], cache_dir: str | Path) -> None:
    path = _cache_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for ticker, df in result.items():
        frame = df.reset_index()
        frame["ticker"] = ticker
        frames.append(frame)
    pd.concat(frames, ignore_index=True).to_parquet(path, index=False)
    logger.info("株価キャッシュを保存しました: %s", path)
