# agentcore-line-chatbot 実装計画

## 概要

参考プロジェクト `line-schedule-checker` をベースに、GitHub で一般公開する汎用 LINE チャットボットを構築する。
機密情報を含むスケジュール確認機能は丸ごと削除し、Tavily API によるウェブ検索機能のみを持たせる。

## アーキテクチャ

```
LINE User → API Gateway (REST, 非同期Lambda呼び出し)
               → Lambda (Python 3.13, Webhook Handler + SSE Bridge)
                    ├── LINE 署名検証
                    ├── AgentCore Runtime 同期+SSE 呼び出し
                    ├── SSE イベント → LINE Push Message 変換
                    └── Push Message 送信
                         ↓
               AgentCore Runtime (Strands Agent)
                    ├── current_time（UTC→JST変換用）
                    └── web_search（Tavily API）
```

## 参考プロジェクトからの変更点

### 削除するもの
- `get_schedule` ツール（GAS API 呼び出し）
- `SCHEDULE_API_URL` 環境変数
- `ALLOWED_USER_IDS` 環境変数とアクセス制御ロジック全体
- ツール単位のアクセス制御の仕組み（`_current_user_id` グローバル変数など）
- システムプロンプト内のスケジュール関連の記述
- ツールステータスマップの `get_schedule` エントリ

### そのまま活用するもの
- CDK 構成（AgentCore Runtime + Lambda + API Gateway）
- LINE Webhook 処理（署名検証、Push Message 送信）
- SSE ストリーミング → LINE Push Message 変換
- グループチャット対応（メンション起動）
- セッション管理（会話履歴保持、15分 TTL）
- `web_search` ツール（Tavily API）
- `current_time` ツール
- Dockerfile + OpenTelemetry 計装

### 変更するもの
- プロジェクト名・CDK スタック名を `agentcore-line-chatbot` に変更
- システムプロンプトを汎用化（スケジュール固有の記述を削除、ペルソナを汎用的に）
- README.md を GitHub 公開用に新規作成（セットアップ手順、アーキテクチャ図、前提条件など）
- `.env.example` を用意（実際の値は入れず、テンプレートとして）
- `.gitignore` を整備（.env.local, cdk.out, node_modules 等）
- CLAUDE.md をこのプロジェクト用に新規作成

## 実装ステップ

### Step 1: プロジェクト初期化
- `git init` でリポジトリ初期化
- `.gitignore` を作成
- `.env.example` をテンプレートとして作成

### Step 2: CDK プロジェクトのセットアップ
- `package.json` を作成（プロジェクト名を `agentcore-line-chatbot` に）
- `tsconfig.json` を作成
- `cdk.json` を作成
- `bin/agentcore-line-chatbot.ts`（CDK エントリーポイント）を作成
- `lib/agentcore-line-chatbot-stack.ts`（CDK スタック）を作成
  - AgentCore Runtime（環境変数は `TAVILY_API_KEY` のみ）
  - Lambda 関数（環境変数は `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, `AGENTCORE_RUNTIME_ARN`）
  - API Gateway（REST, 非同期 Lambda 呼び出し、VTL テンプレートで raw body 保持）
- `npm install` で依存パッケージをインストール

### Step 3: Lambda 関数の実装
- `lambda/webhook.py` を作成
  - LINE 署名検証
  - メッセージ振り分け（1対1 / グループチャット）
  - AgentCore Runtime の同期+SSE 呼び出し
  - SSE → LINE Push Message 変換（テキストバッファリング）
  - `TOOL_STATUS_MAP` は `current_time` と `web_search` のみ
- `lambda/requirements.txt` を作成（`line-bot-sdk` のみ）

### Step 4: Agent の実装
- `agent/agent.py` を作成
  - `current_time` ツール
  - `web_search` ツール
  - システムプロンプトを汎用化（ウェブ検索 + 雑談に特化）
  - セッション管理（会話履歴保持、15分 TTL）
  - アクセス制御ロジックは不要
- `agent/requirements.txt` を作成
- `agent/Dockerfile` を作成

### Step 5: ドキュメント整備
- `README.md` を作成（GitHub 公開用）
  - プロジェクト概要
  - アーキテクチャ図
  - 前提条件（AWS アカウント、LINE Developers、Tavily API キー）
  - セットアップ手順
  - デプロイ手順
  - カスタマイズ方法（ツール追加の例など）
- `CLAUDE.md` を作成（Claude Code 用のプロジェクトガイド）

### Step 6: デプロイ・動作確認
- `.env.local` に実際の環境変数を設定
- `npx cdk deploy --profile sandbox` でデプロイ
- LINE でメッセージを送信して動作確認

## ファイル構成（完成時）

```
agentcore-line-chatbot/
├── bin/
│   └── agentcore-line-chatbot.ts       # CDK エントリーポイント
├── lib/
│   └── agentcore-line-chatbot-stack.ts  # CDK スタック定義
├── lambda/
│   ├── webhook.py                       # Webhook Handler + SSE Bridge
│   └── requirements.txt                 # line-bot-sdk
├── agent/
│   ├── agent.py                         # Strands Agent（web_search + current_time）
│   ├── requirements.txt                 # strands-agents 等
│   └── Dockerfile                       # uv + Python 3.13 + OpenTelemetry
├── doc/
│   └── PLAN.md                          # この実装計画
├── .env.example                         # 環境変数テンプレート
├── .env.local                           # 実際の環境変数（Git 除外）
├── .gitignore
├── CLAUDE.md                            # Claude Code プロジェクトガイド
├── README.md                            # GitHub 公開用ドキュメント
├── cdk.json
├── package.json
└── tsconfig.json
```

## 環境変数一覧

| 変数名 | 配布先 | 説明 |
|--------|--------|------|
| `LINE_CHANNEL_SECRET` | Lambda | LINE チャネルシークレット |
| `LINE_CHANNEL_ACCESS_TOKEN` | Lambda | LINE チャネルアクセストークン |
| `TAVILY_API_KEY` | AgentCore Runtime | Tavily API キー |

※ 参考プロジェクトにあった `SCHEDULE_API_URL`, `ALLOWED_USER_IDS` は不要

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| フロントエンド | LINE Messaging API |
| Webhook | API Gateway (REST) + Lambda (Python 3.13) |
| AI エージェント | Strands Agents on Bedrock AgentCore Runtime |
| LLM | Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) |
| ウェブ検索 | Tavily Search API |
| IaC | AWS CDK (TypeScript) + AgentCore L2 コンストラクト |
| Observability | AgentCore Observability (OpenTelemetry) |
