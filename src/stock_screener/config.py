"""設定ファイル (config.yaml) の読み込み。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = "config.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """YAMLの設定を読み込んで辞書として返す。"""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"設定ファイルが見つかりません: {config_path} "
            "(リポジトリのルートで実行するか --config でパスを指定してください)"
        )
    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"設定ファイルの形式が不正です: {config_path}")
    return config
