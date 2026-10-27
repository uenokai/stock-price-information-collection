"""株価履歴 (日足OHLCV) の取得。

yfinance でバッチ取得し、Parquet にキャッシュする。
キャッシュが新しければ再取得しない (ローカルでの試行錯誤を高速化するため。
GitHub Actions ではランナーが毎回まっさらなので常に新規取得になる)。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# データ不良の判定: 1日で GLITCH_DROP 以下に急落し、GLITCH_WINDOW 営業日以内に
# 急落前の GLITCH_RECOVER 以内まで戻る → 急落中の値を欠損扱いにして前日値で埋める
# (yfinance の分割・配当調整ミスで 1306.T 等に実際に発生する)
GLITCH_DROP = 0.5
GLITCH_WINDOW = 5
GLITCH_RECOVER = 0.3

_PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


def fetch_price_history(
    tickers: list[str],
    period: str = "2y",
    cache_dir: str | Path | None = None,
    max_age_hours: float = 20.0,
) -> dict[str, pd.DataFrame]:
    """各銘柄コードの日足OHLCVを返す。

    Returns:
        {ticker: DataFrame(index=日付, columns=[open, high, low, close, volume])}
        取得に失敗した銘柄は含まれない。
    """
    tickers = list(dict.fromkeys(tickers))  # 順序を保って重複除去

    cached = _load_cache(tickers, cache_dir, period, max_age_hours)
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
        _save_cache(result, cache_dir, period)
    return result


def _split_by_ticker(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yf.download の結果を銘柄コードごとの DataFrame に分解する。"""
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
        sub = _remove_glitches(sub, ticker)
        if not sub.empty:
            sub.index.name = "date"
            result[ticker] = sub
    return result


def _remove_glitches(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """調整後終値の明らかな不良値 (一時的な急落→即回復、0以下) を前日値で埋める。"""
    close = df["close"].astype(float)
    bad = close <= 0
    values = close.to_numpy()
    n = len(values)
    i = 1
    while i < n:
        prev = values[i - 1]
        if prev > 0 and values[i] > 0 and values[i] / prev < 1.0 - GLITCH_DROP:
            for j in range(i + 1, min(i + 1 + GLITCH_WINDOW, n)):
                if abs(values[j] / prev - 1.0) <= GLITCH_RECOVER:
                    bad.iloc[i:j] = True
                    i = j
                    break
        i += 1
    if bad.any():
        logger.warning("%s: 不良値 %d 日分を前日値で補正しました", ticker, int(bad.sum()))
        df = df.copy()
        df.loc[bad, _PRICE_COLUMNS] = np.nan
        df[_PRICE_COLUMNS] = df[_PRICE_COLUMNS].ffill()
        df = df.dropna(subset=["close"])
    return df


def _cache_path(cache_dir: str | Path, period: str) -> Path:
    # 期間ごとに別ファイル (日次判定の 2y と過去検証の 5y が混ざらないように)
    return Path(cache_dir) / f"prices_{period}.parquet"


def _load_cache(
    tickers: list[str], cache_dir: str | Path | None, period: str, max_age_hours: float
) -> dict[str, pd.DataFrame] | None:
    if cache_dir is None:
        return None
    path = _cache_path(cache_dir, period)
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


def _save_cache(result: dict[str, pd.DataFrame], cache_dir: str | Path, period: str) -> None:
    path = _cache_path(cache_dir, period)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for ticker, df in result.items():
        frame = df.reset_index()
        frame["ticker"] = ticker
        frames.append(frame)
    pd.concat(frames, ignore_index=True).to_parquet(path, index=False)
    logger.info("株価キャッシュを保存しました: %s", path)
