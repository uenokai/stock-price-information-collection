"""投資対象ユニバース (監視対象の全銘柄リスト) の読み込み。

ユニバースは市場ごとのCSVファイル (ticker,name) で管理する。
ティッカーは yfinance 形式 (例: 7203.T, AAPL, 0700.HK) で記載し、
市場ごとの差異はサフィックスで吸収する。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def load_universe(config: dict[str, Any]) -> pd.DataFrame:
    """全市場のユニバースCSVを結合して返す。

    Returns:
        columns: ticker, name, market, min_market_cap
    """
    frames = []
    for market, market_cfg in config["universe"].items():
        path = Path(market_cfg["file"])
        if not path.exists():
            logger.warning("ユニバースファイルが見つかりません (スキップ): %s", path)
            continue
        df = pd.read_csv(path)
        if "ticker" not in df.columns:
            raise ValueError(f"{path} に ticker 列がありません")
        df["ticker"] = df["ticker"].str.strip()
        df["market"] = market
        df["min_market_cap"] = float(market_cfg.get("min_market_cap", 0))
        frames.append(df)

    if not frames:
        raise ValueError("有効なユニバースファイルが1つもありません")

    universe = pd.concat(frames, ignore_index=True)
    before = len(universe)
    universe = universe.drop_duplicates(subset="ticker", keep="first")
    if len(universe) < before:
        logger.warning("重複ティッカーを %d 件除外しました", before - len(universe))
    logger.info("ユニバース読み込み完了: %d 銘柄 (%s)", len(universe),
                ", ".join(f"{m}: {n}" for m, n in universe["market"].value_counts().items()))
    return universe
