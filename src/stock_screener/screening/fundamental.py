"""ファンダメンタル・スクリーニング (第1段階)。

各指標を業種内パーセンタイル (0〜1) に変換してから重み付き平均で合成スコアを算出する。
業種内で相対評価する理由: 業種によって適正なPERや利益率の水準が大きく違うため、
生の値で比較すると特定業種 (例: 銀行=低PBR) ばかりが上位に来てしまう。

パーセンタイルは比率ベースの指標 (ROE, PER等) にのみ適用するため、
日本株と米国株のように通貨が違う市場を混ぜても問題ない。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 各指標の方向: +1 = 高いほど良い / -1 = 低いほど良い
METRIC_DIRECTIONS = {
    "roe": 1,
    "operating_margin": 1,
    "revenue_growth": 1,
    "earnings_growth": 1,
    "pe": -1,
    "pb": -1,
    "dividend_yield": 1,
    "debt_to_equity": -1,
}

# 0以下の値が「割安」ではなく「異常値」を意味する指標 (赤字企業のPER等) → 欠損扱い
_POSITIVE_ONLY = ("pe", "pb")


def score_fundamentals(df: pd.DataFrame, screening_cfg: dict[str, Any]) -> pd.DataFrame:
    """合成スコアを算出し、スコア降順に並べた DataFrame を返す。

    Args:
        df: ticker, sector と METRIC_DIRECTIONS の指標カラムを含む DataFrame
        screening_cfg: config.yaml の screening セクション

    Returns:
        入力に score, metric_coverage カラムを加え、スコア算出不能行を除いたもの
    """
    weights: dict[str, float] = screening_cfg["weights"]
    min_required: int = screening_cfg.get("min_metrics_required", 4)
    min_sector_size: int = screening_cfg.get("min_sector_size", 5)

    unknown = set(weights) - set(METRIC_DIRECTIONS)
    if unknown:
        raise ValueError(f"未定義の指標が weights にあります: {sorted(unknown)}")

    work = df.copy()
    work["sector"] = work.get("sector", pd.Series(index=work.index, dtype=object)).fillna("Unknown")
    for col in _POSITIVE_ONLY:
        if col in work.columns:
            work.loc[work[col] <= 0, col] = np.nan

    percentiles = pd.DataFrame(index=work.index)
    for metric in weights:
        if metric not in work.columns:
            percentiles[metric] = np.nan
            continue
        percentiles[metric] = _sector_percentile(
            work[metric], work["sector"], min_sector_size
        )
        if METRIC_DIRECTIONS[metric] == -1:
            percentiles[metric] = 1.0 - percentiles[metric]

    weight_series = pd.Series(weights, dtype=float)
    available = percentiles.notna()
    # 欠損している指標の重みを、存在する指標に再配分する (= 有効重みで正規化)
    effective_weights = available.mul(weight_series, axis=1)
    denominator = effective_weights.sum(axis=1)
    numerator = (percentiles * weight_series).sum(axis=1)  # NaNは自動でスキップされる

    work["metric_coverage"] = available.sum(axis=1)
    work["score"] = np.where(denominator > 0, numerator / denominator, np.nan)
    work.loc[work["metric_coverage"] < min_required, "score"] = np.nan

    dropped = work["score"].isna().sum()
    if dropped:
        logger.info("指標不足でスコア算出できない銘柄を %d 件除外", dropped)

    result = work.dropna(subset=["score"]).sort_values("score", ascending=False)
    return result.reset_index(drop=True)


def apply_market_cap_filter(df: pd.DataFrame) -> pd.DataFrame:
    """時価総額が市場ごとの下限未満 (または取得不能) の銘柄を除外する。"""
    if "market_cap" not in df.columns or "min_market_cap" not in df.columns:
        return df
    mask = df["market_cap"] >= df["min_market_cap"]  # NaN は False → 除外
    removed = (~mask).sum()
    if removed:
        logger.info("時価総額フィルタで %d 銘柄を除外", removed)
    return df[mask].copy()


def _sector_percentile(
    values: pd.Series, sectors: pd.Series, min_sector_size: int
) -> pd.Series:
    """業種内パーセンタイル。業種のサンプル数が少なければ全体パーセンタイルで代用。"""
    sector_pct = values.groupby(sectors).rank(pct=True)
    global_pct = values.rank(pct=True)
    sector_counts = values.notna().groupby(sectors).transform("sum")
    return sector_pct.where(sector_counts >= min_sector_size, global_pct)
