# AgentCore LINE Chatbot

LINE で動く AI チャットボットを、AWS Bedrock AgentCore + Strands Agents でサーバーレスに構築するサンプルです。

## 概要

LINE にメッセージを送ると、AI エージェントがウェブ検索や AWS ドキュメント検索などのツールを駆使して回答してくれます。
ツール実行中の途中経過もリアルタイムに吹き出し表示されるので、待ち時間のストレスがありません。

<img src="docs/images/sample1.jpg" width="300"> <img src="docs/images/sample2.jpg" width="300">

## システム構成

![Architecture](docs/images/architecture.png)

| レイヤー | 技術 |
|---------|------|
| IaC | AWS CDK (TypeScript) + AgentCore L2 コンストラクト |
| Webhook | API Gateway (REST) + Lambda (Python 3.13 / ARM64) |
| Agent | Strands Agents on Bedrock AgentCore Runtime |
| LLM | Claude Sonnet 4.5 on Amazon Bedrock |

## 機能

- Tavily API を使ったウェブ検索（ニュース、技術情報、一般知識など）
- AWS Knowledge MCP Server によるAWSドキュメント検索・閲覧
- AWS What's New の RSS フィード取得
- SSE ストリーミングによるリアルタイム応答（ツール実行状況を LINE に通知、最終回答を 1 通で送信）
- 1対1チャット / グループチャット（メンション起動）の両対応
- 会話履歴の保持（セッション管理、15分 TTL）
- OpenTelemetry による可観測性

### エージェントのツール一覧

| ツール | 説明 |
|-------|------|
| `web_search` | Tavily API によるウェブ検索 |
| `current_time` | 現在の UTC 時刻を取得 |
| `rss` | RSS フィード取得（AWS What's New など） |
| `clear_memory` | 会話の記憶・履歴をクリア |
| `search_documentation` | AWS 公式ドキュメント検索（MCP Server 経由） |
| `read_documentation` | AWS ドキュメントのページ読み取り（MCP Server 経由） |

## デプロイ手順

### 前提条件

- AWS CLI（SSO 設定済み）、Node.js 18+、Docker
- LINE Developers の Messaging API チャネル
- [Tavily](https://tavily.com) の API キー

### 1. クローン & インストール

```bash
git clone https://github.com/minorun365/agentcore-line-chatbot.git
cd agentcore-line-chatbot
npm install
```

### 2. 環境変数の設定

```bash
cp .env.example .env.local
```

`.env.local` に以下の値を記入します。

| 変数名 | 説明 | 取得元 |
|--------|------|--------|
| `LINE_CHANNEL_SECRET` | LINE チャネルシークレット | LINE Developers コンソール |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE アクセストークン | LINE Developers コンソール |
| `TAVILY_API_KEY` | Tavily API キー | Tavily ダッシュボード |

### 3. AWS へデプロイ

```bash
aws sso login --profile your-profile
set -a && source .env.local && set +a
npx cdk deploy --profile your-profile
```

### 4. LINE Webhook の設定

デプロイ完了時に出力される **WebhookUrl** を LINE Developers コンソールに設定します。

- 「Webhook の利用」→ オン
- 「応答メッセージ」→ オフ
- グループで使う場合は「グループトーク・複数人トークへの参加を許可する」→ オン

### 運用コマンド

```bash
npx cdk deploy --profile your-profile             # フルデプロイ
set -a && source .env.local && set +a
npx cdk deploy --hotswap --profile your-profile    # エージェントのみ高速デプロイ
npx cdk diff --profile your-profile                # 差分確認
```

### Tavily API キーの追加・入れ替え

キーは Secrets Manager の `agentcore-line-chatbot/tavily-api-keys` にカンマ区切りで置きます。上限（HTTP 429 / 432 など）に当たると次のキーへ自動で切り替わり、全部尽きた場合は検索なしで回答を続けます。値を入れ替えれば 5 分以内に反映され、再デプロイは不要です。

```bash
aws secretsmanager put-secret-value --profile your-profile --region us-east-1 \
  --secret-id agentcore-line-chatbot/tavily-api-keys --secret-string 'tvly-xxxxx,tvly-yyyyy'
```

シークレットが読めないときは、デプロイ時の環境変数 `TAVILY_API_KEY` を予備として使います。

### 障害対応中の案内メッセージ

Webhook Lambda の環境変数 `MAINTENANCE_MESSAGE` に文面を入れると、エージェントを呼ばずにその文面だけを返します（応答メッセージで返すため、月間の通数を消費しません）。空にすると通常動作に戻ります。デプロイ時に `.env.local` へ `MAINTENANCE_MESSAGE` を書いておくと、その値が設定されます。

### 依存パッケージの版

`agent/requirements.txt` は `agent/requirements.in` から生成した固定版です。版を固定しないと、コンテナを作り直したときに互換性のない新しい版が入り、エージェントが起動できなくなります。更新するときは、ファイル先頭のコマンドで生成し直してから、手元でコンテナを起動して動作を確認してください。
