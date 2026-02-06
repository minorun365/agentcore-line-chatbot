# AgentCore LINE Chatbot

## プロジェクト概要
LINE Messaging API + Bedrock AgentCore で動く汎用 AI チャットボット。
Strands Agents でウェブ検索（Tavily API）ツールを備えた対話型アシスタント。

## 技術スタック
- IaC: AWS CDK (TypeScript) + AgentCore L2 コンストラクト
- Webhook: API Gateway (REST) + Lambda (Python 3.13)
- Agent: Strands Agents on Bedrock AgentCore Runtime
- LLM: Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`)
- 検索: Tavily Search API

## ディレクトリ構成
- `bin/` - CDK エントリーポイント
- `lib/` - CDK スタック定義
- `lambda/` - Webhook Handler + SSE Bridge（LINE非依存のAgentとLINEを橋渡し）
- `agent/` - Strands Agent（AgentCore Runtime上で動作、LINE非依存）
- `doc/` - 設計ドキュメント

## デプロイ
```bash
aws sso login --profile sandbox
npx cdk deploy --profile sandbox           # フルデプロイ
npx cdk deploy --hotswap --profile sandbox  # エージェントのみ高速デプロイ
```

## 環境変数（.env.local）
- `LINE_CHANNEL_SECRET` - LINE チャネルシークレット → Lambda
- `LINE_CHANNEL_ACCESS_TOKEN` - LINE アクセストークン → Lambda
- `TAVILY_API_KEY` - Tavily API キー → AgentCore Runtime

## 設計上の注意点
- API Gateway は Lambda を非同期呼び出し（`X-Amz-Invocation-Type: Event`）
- VTL テンプレートで `$util.escapeJavaScript($input.body)` により raw body を保持（LINE 署名検証に必須）
- Lambda の ARM64 アーキテクチャとバンドリングの `platform: "linux/arm64"` は必ず一致させること
- AgentCore の SSE には2種類のイベントがある。Bedrock Converse Stream 形式（dict）のみ処理し、Strands 生イベント（str）は無視する
