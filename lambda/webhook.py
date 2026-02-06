import json
import logging
import os

import boto3
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
AGENTCORE_RUNTIME_ARN = os.environ["AGENTCORE_RUNTIME_ARN"]

parser = WebhookParser(LINE_CHANNEL_SECRET)
line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

agentcore_client = boto3.client("bedrock-agentcore", region_name="us-east-1")

TOOL_STATUS_MAP = {
    "current_time": "現在時刻を確認しています...",
    "web_search": "ウェブ検索しています...",
    "search_documentation": "AWSドキュメントを検索しています...",
    "read_documentation": "AWSドキュメントを読んでいます...",
    "recommend": "関連ドキュメントを探しています...",
    "rss": "AWS What's New RSSを取得しています...",
}


def show_loading(user_id: str) -> None:
    """1対1チャットでLINE公式ローディングアニメーションを表示"""
    try:
        with ApiClient(line_config) as api_client:
            api = MessagingApi(api_client)
            api.show_loading_animation(
                ShowLoadingAnimationRequest(chat_id=user_id, loading_seconds=60)
            )
    except Exception as e:
        logger.warning(f"Loading animation failed: {e}")


def send_push_message(reply_to: str, text: str) -> None:
    """LINE Push Messageを送信する（user_id または group_id を指定）"""
    if not text.strip():
        return
    with ApiClient(line_config) as api_client:
        api = MessagingApi(api_client)
        api.push_message(
            PushMessageRequest(
                to=reply_to,
                messages=[TextMessage(text=text.strip())],
            )
        )


def process_sse_stream(reply_to: str, response) -> None:
    """AgentCore RuntimeのSSEストリームを読み取り、LINE Push Messageに変換して送信する

    AgentCore Runtimeは2種類のSSEイベントを返す:
    - パターンA: Bedrock Converse Stream形式 (JSON辞書) → これを使う
      例: {"event": {"contentBlockDelta": {"delta": {"text": "こ"}}}}
    - パターンB: Strands Agent生イベントのPython repr (JSON文字列) → 無視する
      例: "{'data': 'こ', 'agent': <Agent object>...}"
    """
    text_buffer = ""

    def flush_text_buffer():
        nonlocal text_buffer
        if text_buffer.strip():
            # LINE Push Messageのテキスト上限は5000文字
            send_push_message(reply_to, text_buffer.strip()[:5000])
            text_buffer = ""

    try:
        for line in response["response"].iter_lines(chunk_size=64):
            if not line:
                continue
            line_str = line.decode("utf-8")
            logger.info(f"SSE line: {line_str[:200]}")

            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:]

            if data_str.strip() == "[DONE]":
                break

            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse SSE data: {data_str[:200]}")
                continue

            # パターンB（文字列）は無視: パターンAに同じ情報が含まれている
            if not isinstance(event, dict):
                continue

            # Bedrock Converse Stream形式: {"event": {...}}
            inner_event = event.get("event")
            if not isinstance(inner_event, dict):
                # ライフサイクルイベント (init_event_loop, start等) やmessageは無視
                continue

            # テキストチャンク: {"event": {"contentBlockDelta": {"delta": {"text": "..."}}}}
            content_block_delta = inner_event.get("contentBlockDelta")
            if content_block_delta:
                delta = content_block_delta.get("delta", {})
                text = delta.get("text", "")
                if text:
                    text_buffer += text
                continue

            # ツール使用開始: {"event": {"contentBlockStart": {"start": {"toolUse": {"name": "..."}}}}}
            content_block_start = inner_event.get("contentBlockStart")
            if content_block_start:
                start = content_block_start.get("start", {})
                tool_use = start.get("toolUse", {})
                if tool_use:
                    flush_text_buffer()
                    tool_name = tool_use.get("name", "unknown")
                    status_text = TOOL_STATUS_MAP.get(tool_name, f"{tool_name} を実行しています...")
                    send_push_message(reply_to, status_text)
                continue

            # コンテンツブロック終了: テキストが溜まっていればflush
            if "contentBlockStop" in inner_event:
                flush_text_buffer()
                continue

    except Exception as e:
        logger.error(f"Error processing SSE stream: {e}")
        send_push_message(reply_to, "エラーが発生しました。もう一度お試しください。")
        return
    finally:
        response["response"].close()

    # 残りのバッファをflush
    flush_text_buffer()


def _is_bot_mentioned(message: TextMessageContent) -> bool:
    """メッセージにBot自身へのメンションが含まれているかチェックする"""
    if not message.mention:
        return False
    return any(
        getattr(m, "is_self", False) for m in message.mention.mentionees
    )


def _strip_bot_mention(message: TextMessageContent) -> str:
    """メッセージテキストからBot宛メンション文字列（@Bot名）を除去する"""
    text = message.text
    if not message.mention:
        return text.strip()
    # index が大きい方から除去（位置ずれ防止）
    mentionees = sorted(
        (m for m in message.mention.mentionees if getattr(m, "is_self", False)),
        key=lambda m: m.index,
        reverse=True,
    )
    for m in mentionees:
        text = text[:m.index] + text[m.index + m.length :]
    return text.strip()


def handler(event, context):
    """Lambda handler - API Gatewayから非同期で呼び出される"""
    logger.info(f"Received event: {json.dumps(event)}")

    body_str = event.get("body", "")
    signature = event.get("signature", "")

    # LINE署名検証
    try:
        events = parser.parse(body_str, signature)
    except InvalidSignatureError:
        logger.error("Invalid LINE signature")
        return {"statusCode": 400, "body": "Invalid signature"}

    # テキストメッセージのみ処理
    for line_event in events:
        if not isinstance(line_event, MessageEvent):
            continue
        if not isinstance(line_event.message, TextMessageContent):
            continue

        source = line_event.source
        message = line_event.message
        is_group_chat = source.type in ("group", "room")

        # グループチャット: Bot宛メンションがある場合のみ処理
        if is_group_chat:
            if not _is_bot_mentioned(message):
                logger.info("Skipping group message without bot mention")
                continue

        # 送信先: グループならgroup_id/room_id、1対1ならuser_id
        reply_to = (
            getattr(source, "group_id", None)
            or getattr(source, "room_id", None)
            or source.user_id
        )

        # メッセージテキストからBot宛メンション文字列を除去
        user_message = _strip_bot_mention(message) if is_group_chat else message.text
        logger.info(f"User {source.user_id} (reply_to={reply_to}): {user_message}")

        if not user_message:
            continue

        # ローディング表示: 1対1チャットはアニメーション、グループチャットはテキスト
        if is_group_chat:
            send_push_message(reply_to, "考えています...")
        else:
            show_loading(source.user_id)

        # AgentCore Runtime をストリーミングで呼び出し
        # reply_toをセッションIDに使用: 同じチャット画面なら同じコンテナにルーティング
        session_id = reply_to
        payload = json.dumps({"prompt": user_message, "session_id": session_id})

        try:
            response = agentcore_client.invoke_agent_runtime(
                agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
                runtimeSessionId=session_id,
                payload=payload.encode("utf-8"),
                qualifier="DEFAULT",
            )
            process_sse_stream(reply_to, response)
        except Exception as e:
            logger.error(f"AgentCore invocation failed: {e}")
            send_push_message(reply_to, "エラーが発生しました。もう一度お試しください。")

    return {"statusCode": 200, "body": "OK"}
