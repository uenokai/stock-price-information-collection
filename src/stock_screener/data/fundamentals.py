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
    retry_passes: int = 2,
    retry_cooldown: float = 60.0,
) -> pd.DataFrame:
    """各ティッカーの財務指標を取得して DataFrame で返す。

    1周目で取れなかった銘柄は、retry_cooldown 秒待ってから最大 retry_passes 回まとめて取り直す
    (Yahoo のレート制限 "Invalid Crumb" 401 は数十秒〜数分で解除されることが多いため)。
    最後まで失敗した銘柄は指標が全て NaN の行になる (後段の時価総額フィルタで落ちる)。
    """
    total = len(tickers)
    infos: dict[str, dict] = {}
    pending = list(tickers)
    for pass_no in range(1 + max(0, retry_passes)):
        if not pending:
            break
        if pass_no > 0:
            logger.warning("財務データ未取得 %d 銘柄を %.0f 秒後に再取得します (%d 回目)",
                           len(pending), retry_cooldown, pass_no)
            time.sleep(retry_cooldown)
        failed = []
        for i, ticker in enumerate(pending, start=1):
            info = _get_info(ticker, max_retries)
            if info and info.get("marketCap") is not None:
                infos[ticker] = info
            else:
                failed.append(ticker)
            if pass_no == 0 and (i % 25 == 0 or i == total):
                logger.info("財務データ取得中: %d/%d", i, total)
            if pause > 0 and i < len(pending):
                time.sleep(pause)
        pending = failed

    rows = []
    for ticker in tickers:
        info = infos.get(ticker, {})
        row: dict = {"ticker": ticker}
        for src, dst in INFO_FIELDS.items():
            row[dst] = info.get(src)
        rows.append(row)
    df = pd.DataFrame(rows)
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    ok = int(df["market_cap"].notna().sum())
    logger.info("財務データ取得完了: %d/%d 銘柄で有効なデータを取得", ok, total)
    if pending:
        logger.warning("最終的に取得できなかった銘柄: %s", ", ".join(pending))
    return df


def _get_info(ticker: str, max_retries: int) -> dict:
    for attempt in range(max_retries + 1):
        try:
            info = yf.Ticker(ticker).info
            if info and isinstance(info, dict):
                return info
        except Exception as e:  # noqa: BLE001 - yfinanceは多様な例外を投げる
            if attempt < max_retries:
                wait = 3 * 2 ** attempt  # 3, 6, 12 ... 秒
                logger.debug("%s の取得失敗 (リトライ %d 秒後): %s", ticker, wait, e)
                time.sleep(wait)
            else:
                logger.warning("%s の財務データ取得に失敗: %s", ticker, e)
    return {}
