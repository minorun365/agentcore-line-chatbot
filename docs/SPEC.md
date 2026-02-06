# AgentCore LINE Chatbot - 設計・仕様書

## 概要

LINE Messaging API + Amazon Bedrock AgentCore で動く汎用 AI チャットボット。
Strands Agents フレームワークでツール（ウェブ検索、AWS ドキュメント検索、RSS フィード取得）を備えた対話型アシスタント。

## アーキテクチャ

```
LINE User
  │
  ▼
API Gateway (REST API)
  │  POST /webhook
  │  VTL: raw body + x-line-signature を抽出
  │  X-Amz-Invocation-Type: Event（非同期）
  ▼
Lambda (Python 3.13, ARM64)
  │  1. LINE 署名検証
  │  2. ローディング表示（1対1: アニメーション / グループ: テキスト）
  │  3. AgentCore Runtime をストリーミング呼び出し
  │  4. SSE → LINE Push Message 変換
  ▼
AgentCore Runtime (Docker コンテナ)
  │  Strands Agent + BedrockModel
  │  ツール: current_time, web_search, rss, AWS Knowledge MCP
  ▼
Bedrock LLM (Claude Sonnet 4.5 or Kimi K2 Thinking)
```

## コンポーネント詳細

### 1. API Gateway（REST API）

LINE Webhook のエントリーポイント。LINE は 3 秒以内のレスポンスを要求するため、Lambda を非同期で起動して即座に 200 を返す。

- エンドポイント: `POST /webhook`
- 統合: AWS Lambda 非同期呼び出し（`X-Amz-Invocation-Type: Event`）
- VTL テンプレート: `$util.escapeJavaScript($input.body)` で raw body を保持（署名検証に必須）
- x-line-signature ヘッダーも VTL で抽出して Lambda に渡す

### 2. Lambda（Webhook Handler + SSE Bridge）

LINE と AgentCore の橋渡し役。LINE 固有の処理を担当し、Agent はLINE に依存しない設計。

ファイル: `lambda/webhook.py`

主な責務:
- LINE 署名検証（WebhookParser）
- グループチャットのメンション検出・除去
- ローディング表示の出し分け
- AgentCore Runtime のストリーミング呼び出し
- SSE イベントの解析と LINE Push Message への変換
- ツール使用時のステータスメッセージ送信

環境変数:

| 変数 | 用途 |
|------|------|
| LINE_CHANNEL_SECRET | Webhook 署名検証 |
| LINE_CHANNEL_ACCESS_TOKEN | Push Message / Loading Animation 送信 |
| AGENTCORE_RUNTIME_ARN | AgentCore Runtime の ARN |

### 3. AgentCore Runtime（Strands Agent）

LINE に依存しない汎用 AI エージェント。Docker コンテナとして AgentCore 上で動作。

ファイル: `agent/agent.py`

主な責務:
- LLM（Bedrock）を使った対話
- ツールの実行（ウェブ検索、AWS ドキュメント検索、RSS、時刻取得）
- セッション管理（会話履歴の保持）
- SSE ストリーミングでのレスポンス返却

環境変数:

| 変数 | 用途 |
|------|------|
| MODEL_ID | 使用する LLM モデル ID |
| TAVILY_API_KEY | Tavily Search API キー |
| AGENT_OBSERVABILITY_ENABLED | OTEL トレース有効化 |

### 4. IaC（AWS CDK）

ファイル: `lib/agentcore-line-chatbot-stack.ts`

リソース:
- `agentcore.Runtime` - AgentCore Runtime（Docker イメージ自動ビルド）
- `lambda.Function` - Webhook Handler（Python バンドリング）
- `apigateway.RestApi` - REST API（VTL + 非同期統合）
- IAM ロール・ポリシー（Bedrock モデル呼び出し、Lambda → AgentCore 呼び出し）

## 利用可能なツール

| ツール | 種類 | 用途 |
|--------|------|------|
| current_time | Strands 組み込み | 現在の UTC 時刻取得 |
| web_search | カスタム（urllib） | Tavily API でウェブ検索 |
| rss | Strands 組み込み | RSS フィード取得（AWS What's New 等） |
| search_documentation | リモート MCP | AWS 公式ドキュメント検索 |
| read_documentation | リモート MCP | AWS ドキュメントページ読み取り |
| recommend | リモート MCP | 関連 AWS ドキュメント推奨 |

## LLM モデル

環境変数 `MODEL_ID` で切り替え可能。

| モデル | MODEL_ID | 備考 |
|--------|----------|------|
| Claude Sonnet 4.5（デフォルト）| `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | キャッシュ対応 |
| Kimi K2 Thinking | `moonshot.kimi-k2-thinking` | キャッシュ非対応、思考プロセスあり |

## セッション管理

- `reply_to`（user_id or group_id）を `runtimeSessionId` に使用
- 同じチャット画面なら同じ AgentCore コンテナにルーティング
- Agent インスタンスはメモリ内で管理（TTL: 15 分）
- コンテナ再起動で会話履歴はリセット

## LINE 対応仕様

### 1対1チャット
- ローディング: LINE 公式アニメーション（60 秒、メッセージ到達で自動消滅）
- メッセージ: そのまま処理

### グループチャット
- ローディング: 「考えています...」テキスト送信（アニメーション API はグループ非対応）
- メッセージ: Bot 宛メンション時のみ処理、`@Bot名` を除去してから Agent に渡す
- 送信先: group_id / room_id 宛に Push Message

### SSE → Push Message 変換

AgentCore Runtime の SSE ストリームを解析し、LINE Push Message に変換する。

| SSE イベント | LINE での表現 |
|-------------|--------------|
| contentBlockDelta (text) | テキストバッファに蓄積 |
| contentBlockStop | バッファを flush → Push Message 送信 |
| contentBlockStart (toolUse) | ツール名に応じたステータスメッセージ送信 |
| [DONE] | 処理完了 |

AgentCore の SSE には 2 種類のイベントがある:
- パターン A: Bedrock Converse Stream 形式（dict）→ これを使う
- パターン B: Strands 生イベントの Python repr（str）→ 無視する

## デプロイ

```bash
# SSO ログイン
aws sso login --profile sandbox

# フルデプロイ
npx cdk deploy --profile sandbox

# エージェントのみ高速デプロイ
npx cdk deploy --hotswap --profile sandbox
```

CDK エントリーポイント（`bin/agentcore-line-chatbot.ts`）が `.env.local` を自動読み込みするため、環境変数の手動 export は不要。

## ディレクトリ構成

```
agentcore-line-chatbot/
├── bin/agentcore-line-chatbot.ts       # CDK エントリーポイント（dotenv で .env.local 読み込み）
├── lib/agentcore-line-chatbot-stack.ts # CDK スタック定義
├── lambda/
│   ├── webhook.py                      # Webhook Handler + SSE Bridge
│   └── requirements.txt               # line-bot-sdk
├── agent/
│   ├── agent.py                        # Strands Agent（AgentCore Runtime 上で動作）
│   ├── requirements.txt               # strands-agents, mcp 等
│   └── Dockerfile                     # uv + Python 3.13 + OpenTelemetry
├── docs/
│   ├── PLAN.md                        # 初期実装計画
│   ├── progress.md                    # 実装進捗
│   ├── SPEC.md                        # 設計・仕様書（この文書）
│   └── KNOWLEDGE.md                   # 実装で得た学び
├── .env.example                       # 環境変数テンプレート
├── .env.local                         # 実際の環境変数（Git 除外）
├── CLAUDE.md                          # Claude Code 向けプロジェクト説明
└── cdk.json / package.json / tsconfig.json
```
