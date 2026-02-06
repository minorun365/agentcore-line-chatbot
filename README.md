# AgentCore LINE Chatbot

LINE で動く AI チャットボットを、AWS Bedrock AgentCore + Strands Agents でサーバーレスに構築するサンプルです。

## 概要

LINE にメッセージを送ると、AI エージェントがウェブ検索などのツールを駆使して回答してくれます。
ツール実行中の途中経過もリアルタイムに吹き出し表示されるので、待ち時間のストレスがありません。

## システム構成

![Architecture](doc/architecture.png)

## 機能

- Tavily API を使ったウェブ検索（ニュース、技術情報、一般知識など）
- SSE ストリーミングによるリアルタイム応答（ツール実行状況を LINE に逐次表示）
- 1対1チャット / グループチャット（メンション起動）の両対応
- 会話履歴の保持（セッション管理、15分 TTL）
- OpenTelemetry による可観測性

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

| 変数名 | 取得元 |
|--------|--------|
| `LINE_CHANNEL_SECRET` | LINE Developers コンソール |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developers コンソール |
| `TAVILY_API_KEY` | Tavily ダッシュボード |

### 3. AWS へデプロイ

```bash
aws sso login --profile your-profile
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
npx cdk deploy --hotswap --profile your-profile    # エージェントのみ高速デプロイ
npx cdk diff --profile your-profile                # 差分確認
```
