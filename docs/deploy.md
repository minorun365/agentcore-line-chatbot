# デプロイガイド

AgentCore LINE Chatbot のビルド・デプロイ・更新手順をまとめたドキュメント。

## 前提条件

- Node.js / npm がインストール済み
- AWS CLI v2 がインストール済み
- Docker Desktop が起動済み（AgentCore Runtime のコンテナビルドに必要）
- `.env.local` に環境変数が設定済み

## 1. AWS SSO ログイン

```bash
aws sso login --profile sandbox
```

ログイン済みか確認:

```bash
aws sts get-caller-identity --profile sandbox
```

## 2. 環境変数の読み込み

CDK デプロイ時に `.env.local` の値を環境変数としてスタックに渡す必要がある。

```bash
set -a && source .env.local && set +a
```

## 3. デプロイ

### フルデプロイ（全リソース）

Lambda、API Gateway、AgentCore Runtime すべてを更新する。初回デプロイや CDK スタック定義を変更した場合はこちら。

```bash
npx cdk deploy --profile sandbox
```

### Hotswap デプロイ（高速）

AgentCore Runtime のコンテナ（`agent/` 配下）や Lambda コード（`lambda/` 配下）のみを変更した場合に使える高速デプロイ。CloudFormation スタック更新をスキップする。

```bash
npx cdk deploy --hotswap --profile sandbox
```

## 4. AgentCore コンテナの強制更新

### なぜ必要か

CDK デプロイで AgentCore Runtime を更新しても、**既存の実行中コンテナは古いコード・環境変数のまま動き続ける**。新しい設定が反映されるのは、新規に起動されるコンテナのみ。

AgentCore Runtime はセッション ID ごとにコンテナをルーティングするため、同じセッション ID で呼び出し続けると古いコンテナが使われ続ける。

- デフォルトのアイドルタイムアウト: 900 秒（15 分）
- デフォルトの最大ライフタイム: 28800 秒（8 時間）

### 対処法: セッションを停止する

`stop-runtime-session` コマンドで既存セッションを停止すると、次回呼び出し時に新しいコンテナが起動し、最新のコード・環境変数が反映される。

```bash
aws bedrock-agentcore stop-runtime-session \
  --runtime-session-id "セッションID" \
  --agent-runtime-arn "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/agentcore_line_chatbot" \
  --qualifier DEFAULT \
  --region us-east-1 \
  --profile sandbox
```

#### セッション ID の確認方法

このアプリでは LINE の `user_id`（1 対 1 チャット）または `group_id`（グループチャット）をセッション ID として使用している（`lambda/webhook.py` の `session_id = reply_to` 部分）。

CloudWatch Logs の Lambda ログから確認できる:

```bash
# Lambda のログから直近のセッションIDを確認
aws logs filter-log-events \
  --log-group-name "/aws/lambda/AgentcoreLineChatbotStack-WebhookFunction*" \
  --filter-pattern "reply_to=" \
  --start-time $(date -v-1H +%s000) \
  --region us-east-1 \
  --profile sandbox \
  --query 'events[].message' \
  --output text
```

#### 注意事項

- セッション停止後は**会話履歴（エージェント内のメモリ）もリセットされる**
- 最大 15 分アイドル状態が続けばコンテナは自動終了するので、急がなければ待つだけでも OK

## 5. デプロイの確認

### Webhook URL の確認

デプロイ完了時にコンソールに出力される。LINE Developers コンソールの Webhook URL と一致しているか確認する。

```
AgentcoreLineChatbotStack.WebhookUrl = https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/prod/webhook
```

### 動作確認

LINE アプリからボットにメッセージを送り、応答が返ることを確認する。

## よくあるデプロイ手順（まとめ）

エージェントのコード（`agent/` 配下）を更新して反映させる一連の手順:

```bash
# 1. SSO ログイン（未ログインの場合）
aws sso login --profile sandbox

# 2. 環境変数読み込み
set -a && source .env.local && set +a

# 3. デプロイ
npx cdk deploy --hotswap --profile sandbox

# 4. 既存コンテナセッションを停止（即時反映したい場合）
aws bedrock-agentcore stop-runtime-session \
  --runtime-session-id "セッションID" \
  --agent-runtime-arn "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/agentcore_line_chatbot" \
  --qualifier DEFAULT \
  --region us-east-1 \
  --profile sandbox

# 5. LINE でメッセージを送って動作確認
```
