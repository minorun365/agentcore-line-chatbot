# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要
LINE Messaging API + Bedrock AgentCore で動く汎用 AI チャットボット。
Strands Agents でウェブ検索（Brave Search API）やAWSドキュメント検索ツールを備えた対話型アシスタント。

## 技術スタック
- IaC: AWS CDK (TypeScript) + `@aws-cdk/aws-bedrock-agentcore-alpha` L2 コンストラクト
- Webhook: API Gateway (REST) + Lambda (Python 3.13, ARM64)
- Agent: Strands Agents on Bedrock AgentCore Runtime (Docker コンテナ)
- LLM: Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`)
- 検索: Brave Search API（キーは AgentCore Identity の API キー資格情報プロバイダ `brave` から読む。`agent/brave.py`）、AWS Knowledge MCP Server
- Observability: OpenTelemetry (AgentCore 標準)

## 開発コマンド

```bash
# 依存パッケージのインストール
npm install

# TypeScript のビルド（CDK コードの型チェック）
npx tsc

# CDK の差分確認
npx cdk diff --profile sandbox

# デプロイ前に環境変数をシェルに読み込む（CDKが process.env 経由で参照するため必須）
set -a && source .env.local && set +a

# フルデプロイ（CDK + Lambda + AgentCore Runtime すべて）
npx cdk deploy --profile sandbox

# 高速デプロイ（AgentCore Runtime の Docker イメージのみ更新）
npx cdk deploy --hotswap --profile sandbox
```

環境変数は `.env.local` に定義（テンプレート: `.env.example`）。`bin/agentcore-line-chatbot.ts` で `dotenv.config` により CDK 実行時に読み込まれるが、`--hotswap` デプロイ時は `set -a && source .env.local && set +a` でシェルにも展開が必要。

## アーキテクチャ

リクエストフローは3段構成で、Agent は LINE に依存しない設計:

```
LINE User
  → API Gateway (REST, VTL で raw body + signature を抽出)
    → Lambda 非同期呼び出し (X-Amz-Invocation-Type: Event)
      → LINE 署名検証 → AgentCore Runtime SSE 呼び出し
        → ツール実行状況はリアルタイム通知、テキストは最終ブロックのみ Push Message で送信

AgentCore Runtime (Docker コンテナ)
  → Strands Agent (セッション管理: reply_to を session_id に使用、15分 TTL)
    → Tools: current_time, web_search(Brave Search API), rss, clear_memory, AWS Knowledge MCP Server
```

**Lambda (`lambda/webhook.py`)** — LINE Webhook の受付と SSE→LINE 変換のブリッジ。LINE SDK でローディングアニメーション表示、Push Message 送信、グループチャット対応（メンション検出・除去）を担当。

**Agent (`agent/agent.py`)** — `BedrockAgentCoreApp` のエントリーポイント。`Agent.stream_async()` でストリーミング応答を生成。セッション管理は `_agent_sessions` dict で Agent インスタンスをキャッシュ（15分 TTL）。

**CDK (`lib/agentcore-line-chatbot-stack.ts`)** — AgentCore Runtime + Lambda + API Gateway を定義。Lambda は `grantInvokeRuntime` で AgentCore 呼び出し権限を付与。

## 設計上の注意点
- API Gateway → Lambda は非同期呼び出し（`X-Amz-Invocation-Type: Event`）。LINE への応答は Lambda が Push Message で返す
- VTL テンプレートで `$util.escapeJavaScript($input.body)` により raw body を保持（LINE 署名検証に必須）
- Lambda の ARM64 アーキテクチャと bundling の `platform: "linux/arm64"` は必ず一致させること
- AgentCore の SSE には2種類のイベントがある: Bedrock Converse Stream 形式（dict）のみ処理し、Strands 生イベント（str）は無視する
- AWS Knowledge MCP Server (`https://knowledge-mcp.global.api.aws`) は認証不要。`MCPClient` + `streamablehttp_client` で接続し、Agent の tools に直接渡す
- **BedrockAgentCoreApp の import は `from bedrock_agentcore import BedrockAgentCoreApp` を使うこと**。`from bedrock_agentcore.runtime import ...` だと GenAI Observability のトレースが出力されない
- Agent の Docker コンテナは `opentelemetry-instrument python agent.py` で起動（`agent/Dockerfile` の CMD）。OTel の設定は CDK 側の環境変数で注入
- LINE Push Message のテキスト上限は 5000 文字。`webhook.py` で `[:5000]` にトランケートしている
- セッション管理は `reply_to`（user_id or group_id）を `runtimeSessionId` として使い、AgentCore が同じコンテナにルーティング。コンテナのアイドルタイムアウト（15分）で自動破棄

## Agent にツールを追加する手順
新しいツールを追加する場合、以下の2箇所を同時に変更すること:
1. `agent/agent.py` — ツール関数を定義し、`_get_or_create_agent()` 内の `tools=` リストに追加。`SYSTEM_PROMPT` にもツールの説明と使い分けルールを追記
2. `lambda/webhook.py` — `TOOL_STATUS_MAP` にツール名とLINE上で表示するステータスメッセージを追加（例: `"my_tool": "処理中です..."`)

## ディレクトリ構成（主要ファイル）
```
bin/agentcore-line-chatbot.ts  # CDK アプリのエントリーポイント（dotenv 読み込み）
lib/agentcore-line-chatbot-stack.ts  # CDK スタック定義（AgentCore Runtime + Lambda + API Gateway）
agent/
  agent.py          # Strands Agent 本体（ツール定義、セッション管理、SYSTEM_PROMPT）
  Dockerfile        # AgentCore Runtime のコンテナイメージ
  requirements.txt  # Python 依存（strands-agents, bedrock-agentcore, mcp 等）
lambda/
  webhook.py        # LINE Webhook ハンドラ（署名検証、SSE→LINE Push Message 変換）
  requirements.txt  # Python 依存（line-bot-sdk）
```
