import json
import logging
import os
import time
import urllib.error
import urllib.request

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands_tools import current_time, rss

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# 現在処理中のセッションID（ツールからセッション操作するために使用）
_current_session_id: str | None = None


@tool
def clear_memory() -> str:
    """会話の記憶・履歴をクリアします。ユーザーが「記憶を消して」「履歴をリセット」「忘れて」「会話をクリア」など、会話履歴の削除を求めた場合に使います。

    Returns:
        クリア結果のメッセージ
    """
    if _current_session_id and _current_session_id in _agent_sessions:
        _agent_sessions[_current_session_id].messages.clear()
        del _agent_sessions[_current_session_id]
        logger.info(f"Session cleared by tool: {_current_session_id}")
    return "会話の記憶をクリアしました。"


# Tavily APIキーは Secrets Manager にカンマ区切りで複数置き、上限に当たったら次のキーへ切り替える。
# キーの追加・入れ替えは put-secret-value だけでよい（再デプロイ不要。5分以内に読み直す）
TAVILY_SECRET_ARN = os.environ.get("TAVILY_SECRET_ARN", "")
_TAVILY_KEYS_TTL = 300
# 次のキーへ切り替えるステータス（401/403: キー無効、429: レート制限、432/433: クレジット上限）
_TAVILY_ROTATE_STATUSES = {401, 403, 429, 432, 433}
_tavily_keys: list[str] = []
_tavily_keys_loaded_at = 0.0
_tavily_key_index = 0


def _load_tavily_keys() -> list[str]:
    """Secrets Manager からキー一覧を読む（TTL付きキャッシュ）。読めなければ環境変数の単体キーを使う"""
    global _tavily_keys, _tavily_keys_loaded_at, _tavily_key_index
    if _tavily_keys and time.time() - _tavily_keys_loaded_at < _TAVILY_KEYS_TTL:
        return _tavily_keys
    keys: list[str] = []
    if TAVILY_SECRET_ARN:
        try:
            secret = boto3.client("secretsmanager").get_secret_value(SecretId=TAVILY_SECRET_ARN)
            raw = secret.get("SecretString", "").replace("\n", ",")
            keys = [k.strip() for k in raw.split(",") if k.strip().startswith("tvly-")]
        except Exception as e:
            logger.warning(f"Failed to load Tavily keys from Secrets Manager: {e}")
    if not keys and TAVILY_API_KEY:
        keys = [TAVILY_API_KEY]
    if keys != _tavily_keys:
        _tavily_key_index = 0
    _tavily_keys = keys
    _tavily_keys_loaded_at = time.time()
    logger.info(f"Tavily keys loaded: {len(keys)}")
    return keys


def _tavily_search(api_key: str, query: str) -> dict:
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps({
            "query": query,
            "max_results": 5,
            "search_depth": "basic",
            "include_answer": True,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


@tool
def web_search(query: str) -> str:
    """一般的なウェブ検索を行います。ニュース、技術情報、一般知識の検索に使います。
    注意: AWSの最新アップデートやWhat's Newについてはこのツールではなく、必ずrssツールを使ってください。

    Args:
        query: 検索クエリ（日本語または英語）

    Returns:
        検索結果のテキスト
    """
    global _tavily_key_index
    keys = _load_tavily_keys()
    result = None
    # 前回成功したキーから順に試し、上限系のエラーなら次のキーへ回す
    for attempt in range(len(keys)):
        idx = (_tavily_key_index + attempt) % len(keys)
        try:
            result = _tavily_search(keys[idx], query)
            _tavily_key_index = idx
            break
        except urllib.error.HTTPError as e:
            logger.warning(f"Tavily key #{idx + 1}/{len(keys)} failed: HTTP {e.code}")
            if e.code not in _TAVILY_ROTATE_STATUSES:
                break
        except Exception as e:
            logger.warning(f"Tavily key #{idx + 1}/{len(keys)} failed: {e}")
            break
    if result is None:
        logger.error("All Tavily keys failed")
        return "ウェブ検索が一時的に使えません。検索せずに分かる範囲で回答し、最新情報は確認できなかったと伝えてください。"

    parts = []

    # Tavily生成の要約があれば先頭に表示
    if result.get("answer"):
        parts.append(f"【要約】\n{result['answer']}")

    # 個別の検索結果
    for item in result.get("results", []):
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")
        parts.append(f"■ {title}\n{url}\n{content}")

    return "\n\n".join(parts) if parts else "検索結果が見つかりませんでした。"


SYSTEM_PROMPT = """あなたはLINEで動くアシスタント「みのるんAI」です。
ユーザーからの質問や依頼に応じて、ツールを活用しながら柔軟に対応します。
Claude Sonnet 4.5という最先端のLLMで駆動しています。みのるんというエンジニアが開発しています。

## 利用可能なツール
- web_search: ウェブ検索で最新情報を取得（ニュース、技術情報、一般知識など）
- search_documentation: AWSの公式ドキュメントを検索
- read_documentation: AWSドキュメントのページを読み取り
- rss: RSSフィードを取得（AWSの最新アップデート確認に使用。action="fetch", url="https://aws.amazon.com/jp/about-aws/whats-new/recent/feed/" で呼び出す）
- current_time: 現在のUTC時刻を取得（JST = UTC+9 に変換して使用）
- clear_memory: 会話の記憶・履歴をクリア

## 対応方針
- AWSの最新アップデート、What's New、新機能について聞かれたら → rss ツールを使う
- AWSサービスについての質問 → search_documentation + read_documentation で対応
- 最新のニュースや調べ物 → web_search で対応
- 日時や相対日付（"最新"なども）に関する質問 → current_time で現在時刻を確認
- 一般的な質問や雑談 → 自分の知識で対応（必要に応じてweb_searchも活用）
- 複数のツールを組み合わせて回答してもOK
- 「記憶を消して」「忘れて」「リセット」「履歴クリア」など会話履歴の削除を求められたら → clear_memory を使う
- 曖昧な依頼など、不明点があればユーザーに聞き返してください

## 応答ルール
- 元気に明るく応対すること。絵文字は頻用しすぎないこと
- 最終回答はスマホで読みやすいようコンパクトに
- 1メッセージは200文字以内を目安にする。Web検索結果もうまく要約すること
- 長文は避け、重要な情報のみを簡潔に伝える
- Markdownは絶対に使わない（LINEではレンダリングされないため）
  - NG: **太字**、# 見出し、[リンク](URL)、```コードブロック```
  - OK: 「・」で箇条書き、【】で強調、改行で区切り

## 注意
- ウェブ検索結果を使う場合、出典URLは省略し、情報の要点だけ伝える
- このチャットは会話履歴を保持しています。前の会話の文脈を踏まえて自然に応答してください
- current_time はUTCを返すので、必ずJST（+9時間）に変換すること
"""

app = BedrockAgentCoreApp()

# AWS Knowledge MCP Server（認証不要のリモートMCPサーバー）
aws_docs_client = MCPClient(
    lambda: streamablehttp_client(url="https://knowledge-mcp.global.api.aws")
)

# セッション管理: session_id → Agent
# AgentCore Runtimeが同じruntimeSessionIdを同じコンテナにルーティングするため、
# コンテナのアイドルタイムアウト（15分）で自動的にセッションが破棄される
_agent_sessions: dict[str, Agent] = {}


def _get_or_create_agent(session_id: str | None) -> Agent:
    """セッションIDに対応するAgentを取得または作成"""
    if session_id and session_id in _agent_sessions:
        return _agent_sessions[session_id]

    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID),
        system_prompt=SYSTEM_PROMPT,
        tools=[current_time, web_search, rss, clear_memory, aws_docs_client],
    )

    if session_id:
        _agent_sessions[session_id] = agent

    return agent


@app.entrypoint
async def invoke_agent(payload, context):
    global _current_session_id
    prompt = payload.get("prompt", "")
    session_id = payload.get("session_id")
    _current_session_id = session_id

    agent = _get_or_create_agent(session_id)

    async for event in agent.stream_async(prompt):
        yield event


if __name__ == "__main__":
    app.run()
