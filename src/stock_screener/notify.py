"""通知 (Slack Incoming Webhook)。

環境変数 SLACK_WEBHOOK_URL が設定されていれば送信し、なければ何もしない。
GitHub Actions ではリポジトリの Secrets に SLACK_WEBHOOK_URL を登録すると有効になる。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

ENV_VAR = "SLACK_WEBHOOK_URL"


def send_slack(text: str) -> bool:
    """Slackにテキストを送信する。Webhook未設定なら送信せず False を返す。"""
    url = os.environ.get(ENV_VAR, "").strip()
    if not url:
        logger.info("%s が未設定のためSlack通知をスキップします", ENV_VAR)
        return False
    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            ok = 200 <= response.status < 300
    except Exception as e:  # noqa: BLE001 - 通知失敗で本処理を止めない
        logger.error("Slack通知に失敗しました: %s", e)
        return False
    if ok:
        logger.info("Slack通知を送信しました")
    return ok
