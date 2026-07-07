"""財務指標 (ファンダメンタルデータ) の取得。

yfinance の Ticker.info から必要な指標だけを抽出する。
1銘柄ずつの取得になるため、レート制限対策のポーズとリトライを入れている。

注意: 指標の単位は yfinance のバージョンによって揺れることがある
(例: dividendYield が割合か%か)。スコアリングはパーセンタイル (順位) ベース
なので、単位の揺れはスコアに影響しない。
"""

from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# yfinance の info キー → 内部カラム名
INFO_FIELDS = {
    "sector": "sector",
    "industry": "industry",
    "currency": "currency",
    "marketCap": "market_cap",
    "returnOnEquity": "roe",
    "operatingMargins": "operating_margin",
    "revenueGrowth": "revenue_growth",
    "earningsGrowth": "earnings_growth",
    "trailingPE": "pe",
    "priceToBook": "pb",
    "dividendYield": "dividend_yield",
    "debtToEquity": "debt_to_equity",
}

NUMERIC_COLUMNS = [
    "market_cap", "roe", "operating_margin", "revenue_growth", "earnings_growth",
    "pe", "pb", "dividend_yield", "debt_to_equity",
]


def fetch_fundamentals(
    tickers: list[str],
    pause: float = 0.2,
    max_retries: int = 2,
) -> pd.DataFrame:
    """各ティッカーの財務指標を取得して DataFrame で返す。

    取得に失敗した銘柄は指標が全て NaN の行になる (後段の欠損フィルタで落ちる)。
    """
    rows = []
    total = len(tickers)
    for i, ticker in enumerate(tickers, start=1):
        info = _get_info(ticker, max_retries)
        row: dict = {"ticker": ticker}
        for src, dst in INFO_FIELDS.items():
            row[dst] = info.get(src)
        rows.append(row)
        if i % 25 == 0 or i == total:
            logger.info("財務データ取得中: %d/%d", i, total)
        if pause > 0 and i < total:
            time.sleep(pause)

    df = pd.DataFrame(rows)
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    ok = df["market_cap"].notna().sum()
    logger.info("財務データ取得完了: %d/%d 銘柄で有効なデータを取得", ok, total)
    return df


def _get_info(ticker: str, max_retries: int) -> dict:
    for attempt in range(max_retries + 1):
        try:
            info = yf.Ticker(ticker).info
            if info and isinstance(info, dict):
                return info
        except Exception as e:  # noqa: BLE001 - yfinanceは多様な例外を投げる
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                logger.debug("%s の取得失敗 (リトライ %d 秒後): %s", ticker, wait, e)
                time.sleep(wait)
            else:
                logger.warning("%s の財務データ取得に失敗: %s", ticker, e)
    return {}
