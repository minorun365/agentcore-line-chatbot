# 機能追加の実装計画

## 現在のプロジェクト状態

### デプロイ済み（2025-02-07）
- CloudFormation スタック `AgentcoreLineChatbotStack` が us-east-1 にデプロイ済み
- AWS プロファイル: `sandbox`（個人 Org アカウント `715841358122`）
- Webhook URL: `https://jj67ivglg1.execute-api.us-east-1.amazonaws.com/prod/webhook`
- AgentCore Runtime ARN: `arn:aws:bedrock-agentcore:us-east-1:715841358122:runtime/agentcore_line_chatbot-gcJwjw6ZSB`
- LINE Developers コンソールに Webhook URL 設定済み
- 基本機能（Claude Sonnet 4.5 + Tavily ウェブ検索）が動作する状態

### Git 状態
- `git init` 済み、まだ初回コミットなし
- `.gitignore` で `/doc` を除外済み（ユーザーが追加）

### ファイル構成
```
agentcore-line-chatbot/
├── bin/agentcore-line-chatbot.ts       # CDK エントリーポイント
├── lib/agentcore-line-chatbot-stack.ts  # CDK スタック
├── lambda/
│   ├── webhook.py                       # Webhook Handler + SSE→LINE変換
│   └── requirements.txt                 # line-bot-sdk
├── agent/
│   ├── agent.py                         # Strands Agent（web_search + current_time）
│   ├── requirements.txt                 # strands-agents 等
│   └── Dockerfile                       # uv + Python 3.13 + OpenTelemetry
├── doc/
│   ├── PLAN.md                          # 初期実装計画
│   ├── progress.md                      # この文書
│   └── architecture.png                 # アーキテクチャ図
├── .env.example                         # 環境変数テンプレート
├── .env.local                           # 実際の環境変数（Git 除外）
├── .gitignore
├── CLAUDE.md
├── README.md
├── cdk.json / package.json / tsconfig.json
```

### 参照すべきナレッジスキル
- `/kb-kimi` - Kimi K2 Thinking の問題・ワークアラウンド（ツール名破損リトライ、`<think>`タグ、cache 非対応など）
- `/kb-line` - LINE Bot 開発（Webhook、署名検証、Push Message、グループチャット）
- `/kb-strands-agentcore` - Strands Agents + Bedrock AgentCore（エージェント開発、CDK）

### 参考プロジェクト
- `~/git/minorun365/line-schedule-checker` - 本プロジェクトのベース。SSE処理、グループチャット対応などはここから流用済み

### デプロイコマンド
```bash
aws sso login --profile sandbox
npx cdk deploy --profile sandbox           # フルデプロイ
npx cdk deploy --hotswap --profile sandbox  # エージェントのみ高速デプロイ
```

### 設計上の注意点
- API Gateway は Lambda を非同期呼び出し（`X-Amz-Invocation-Type: Event`）→ Reply Message 不可、Push Message のみ
- VTL テンプレートで `$util.escapeJavaScript($input.body)` → raw body 保持（LINE 署名検証に必須）
- Lambda ARM64 + バンドリング `platform: "linux/arm64"` は必ず一致させる
- AgentCore の SSE には 2 種類のイベントがある。dict（Bedrock Converse Stream 形式）のみ処理し、str（Strands 生イベント）は無視する
- セッション管理: `reply_to`（user_id or group_id）を `runtimeSessionId` に使用 → 同じチャット画面なら同じコンテナにルーティング

---

## 機能1: Kimi K2 Thinking モデル対応

環境変数 `MODEL_ID` でモデルを切り替えられるようにする。

### 変更ファイル
- `agent/agent.py` - モデル切り替えロジック追加
- `lib/agentcore-line-chatbot-stack.ts` - 環境変数 `MODEL_ID` を AgentCore Runtime に追加
- `.env.example` - `MODEL_ID` を追記

### 実装内容

**agent/agent.py:**
- 環境変数 `MODEL_ID` を読み取り（デフォルト: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`）
- Kimi K2（`moonshot.kimi-k2-thinking`）の場合は `cache_prompt` / `cache_tools` を指定しない
- Kimi K2 特有の注意点:
  - `<think>` タグがテキストに混入する → フィルタリング不要（SSE側で処理されるため、Agent側では対処不要）
  - ツール名破損のリスクがある → ただしリトライはサーバーサイドで複雑になるため、まずはシンプルに実装

```python
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

def _create_model():
    if "kimi" in MODEL_ID:
        return BedrockModel(model_id=MODEL_ID)
    else:
        return BedrockModel(model_id=MODEL_ID)
```

### CDK 変更
```typescript
environmentVariables: {
    MODEL_ID: process.env.MODEL_ID || "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    // ...
},
```

---

## 機能2: AWS ドキュメント検索（リモート MCP サーバー統合）

AWS Knowledge MCP Server（`https://knowledge-mcp.global.api.aws`）にリモート接続する。
認証不要・Dockerfile変更不要で、Strands の `MCPClient` + `streamablehttp_client` で直接接続できる。

### 変更ファイル
- `agent/agent.py` - MCPClient でリモート MCP ツールを追加
- `agent/requirements.txt` - `mcp` パッケージ追加
- `lambda/webhook.py` - `TOOL_STATUS_MAP` に MCP ツール追加

### 提供ツール（AWS Knowledge MCP Server）
- `search_documentation` - AWS ドキュメント検索
- `read_documentation` - ドキュメント取得・マークダウン変換
- `recommend` - 関連コンテンツ推奨
- `list_regions` - AWS リージョン一覧
- `get_regional_availability` - サービスの地域別可用性

### 実装内容

**agent/agent.py:**
```python
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

aws_docs_client = MCPClient(
    lambda: streamablehttp_client(url="https://knowledge-mcp.global.api.aws")
)
```

Agent の tools に `aws_docs_client` を追加。MCPClient を Agent に直接渡すとライフサイクルが自動管理される。

**agent/requirements.txt に追加:**
```
mcp
```

**Dockerfile の変更は不要**（リモート接続のため）

**lambda/webhook.py の TOOL_STATUS_MAP:**
```python
"search_documentation": "AWSドキュメントを検索しています...",
"read_documentation": "AWSドキュメントを読んでいます...",
"recommend": "関連ドキュメントを探しています...",
```

### システムプロンプト追記
```
- search_documentation: AWSの公式ドキュメントを検索
- read_documentation: AWSドキュメントのページを読み取り
- AWSサービスについて聞かれた場合 → search_documentation + read_documentation で対応
```

---

## 機能3: LINE ローディングアニメーション

「考えています...」のテキスト送信を、LINE 公式のローディングアニメーション API に置き換える。

### 変更ファイル
- `lambda/webhook.py` - ローディングアニメーション呼び出し追加

### API 仕様
- エンドポイント: `POST https://api.line.me/v2/bot/chat/loading/start`
- SDK: `MessagingApi.show_loading_animation(ShowLoadingAnimationRequest(...))`
- `loadingSeconds`: 5〜60秒（5秒刻み）、デフォルト20秒
- メッセージ到達時に自動消滅

### 制限事項
- **1対1チャットでのみ有効**（グループチャットでは使えない）
- グループチャットの場合は従来通り「考えています...」テキストを送信

### 実装内容
```python
from linebot.v3.messaging import ShowLoadingAnimationRequest

def show_loading(user_id: str) -> None:
    """1対1チャットでローディングアニメーションを表示"""
    try:
        with ApiClient(line_config) as api_client:
            api = MessagingApi(api_client)
            api.show_loading_animation(
                ShowLoadingAnimationRequest(chat_id=user_id, loading_seconds=60)
            )
    except Exception as e:
        logger.warning(f"Loading animation failed: {e}")

# handler 内
if is_group_chat:
    send_push_message(reply_to, "考えています...")
else:
    show_loading(source.user_id)
```

---

## 機能4: AWS What's New RSS フィード

Strands 組み込みの `rss` ツールを使い、AWS What's New の RSS フィードから最新アップデートを取得する。

### RSS フィード URL
- `https://aws.amazon.com/jp/about-aws/whats-new/recent/feed/`

### 変更ファイル
- `agent/agent.py` - `rss` ツールを import して Agent の tools に追加
- `agent/requirements.txt` - `strands-agents-tools[rss]` に変更（feedparser, html2text, requests 追加）
- `lambda/webhook.py` - `TOOL_STATUS_MAP` に rss ツール追加

### 実装内容

**agent/requirements.txt:**
- `strands-agents-tools` → `strands-agents-tools[rss]` に変更

**agent/agent.py:**
```python
from strands_tools import rss
# tools に rss を追加
```

システムプロンプトに以下を追記:
```
- rss: RSSフィードを取得（AWSの最新アップデート確認に使用）
- AWSの最新アップデートについて聞かれた場合 → rss ツールで https://aws.amazon.com/jp/about-aws/whats-new/recent/feed/ を fetch
```

**lambda/webhook.py の TOOL_STATUS_MAP:**
```python
"rss": "AWSの最新アップデートを取得しています...",
```

---

## 実装順序

1. **機能3: LINE ローディングアニメーション**（Lambda のみの変更で簡単）
2. **機能1: Kimi K2 モデル対応**（Agent + CDK の変更）
3. **機能2: AWS ドキュメント検索**（Agent + Lambda の変更、リモート接続のため Dockerfile 変更不要）
4. **機能4: AWS What's New RSS フィード**（Agent + Lambda の変更）

各機能の実装完了ごとにデプロイして動作確認する。

## 実装ステータス

| 機能 | ステータス |
|------|-----------|
| 機能3: LINE ローディングアニメーション | 完了 |
| 機能1: Kimi K2 モデル対応 | 完了 |
| 機能2: AWS ドキュメント検索（リモート MCP） | 完了 |
| 機能4: AWS What's New RSS フィード | 完了 |
