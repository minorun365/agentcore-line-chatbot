import logging

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands_tools import current_time, rss

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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


# Web検索は AgentCore Web Search（AWS が用意する検索 API。リソースを作らず IAM だけで呼べる）
_web_search_client = boto3.client("bedrock-agentcore")


@tool
def web_search(query: str) -> str:
    """一般的なウェブ検索を行います。ニュース、技術情報、一般知識の検索に使います。
    注意: AWSの最新アップデートやWhat's Newについてはこのツールではなく、必ずrssツールを使ってください。

    Args:
        query: 検索クエリ（日本語または英語）

    Returns:
        検索結果のテキスト
    """
    try:
        results = _web_search_client.invoke_web_search(
            webSearchId="web-search.v1", searchQuery=query, maxResults=5
        ).get("results", [])
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return "ウェブ検索が一時的に使えません。検索せずに分かる範囲で回答し、最新情報は確認できなかったと伝えてください。"
    logger.info(f"Web search: {len(results)} results")

    parts = []
    for item in results:
        published = item.get("publishedAt")
        date = f"（{published:%Y-%m-%d}）" if published else ""
        parts.append(f"■ {item.get('title') or ''}{date}\n{item.get('url', '')}\n{(item.get('text') or '')[:500]}")

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
- ウェブ検索結果を使う場合、情報の要点を伝え、参考にしたページのURLを末尾に1つ添える（リンク記法にせずURLをそのまま書く）
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
