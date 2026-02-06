# AgentCore LINE Chatbot - 実装で得た学び

このプロジェクトの実装を通じて得た技術的な知見やハマりポイントをまとめる。

---

## LINE Messaging API

### 非同期 Lambda では Reply Message が使えない

LINE Webhook は 3 秒以内のレスポンスを要求する。AI エージェントの処理は数十秒かかるため、API Gateway で即座に 200 を返し、Lambda を非同期起動する構成を採用。

この場合 `replyToken` は 30 秒で失効するため **Push Message のみ** 使用する。Push Message は月の無料枠（200 通）があるため、大量利用時は有料プランが必要。

### VTL テンプレートでの raw body 保持

LINE 署名検証には Webhook のリクエストボディをそのまま（raw body）使う必要がある。API Gateway の VTL テンプレートでは `$input.json('$')` ではなく `$util.escapeJavaScript($input.body)` を使うこと。前者はパース済み JSON を返すため署名検証に失敗する。

### ローディングアニメーションはグループチャット非対応

`show_loading_animation` API は `chatId` に user_id のみ指定可能。group_id / room_id は不可。

公式ドキュメント:
> "You can't specify group chats or multi-person chats."

グループチャットでは従来通りテキストメッセージ（「考えています...」）で代替する。

### グループチャットのメンション除去

`@Bot名` をメッセージから除去する際、**index が大きい方から除去** しないと位置ずれが起きる。`message.mention.mentionees` を index 降順でソートしてから処理すること。

### テキスト上限は 5000 文字

LINE Push Message のテキスト上限は 5000 文字。AI の回答が長くなる可能性があるため、`text.strip()[:5000]` で切り詰める。

---

## Bedrock AgentCore

### SSE には 2 種類のイベントがある

AgentCore Runtime の SSE ストリームには 2 種類のイベントが含まれる:

- **パターン A**: Bedrock Converse Stream 形式（JSON 辞書） → これを処理する
- **パターン B**: Strands Agent 生イベントの Python repr（JSON 文字列） → 無視する

パターン B は `json.loads` すると文字列型になるため、`isinstance(event, dict)` で判別できる。パターン A のみ処理すればテキストやツール使用イベントを正しく取得できる。

### CDK デプロイしてもコンテナはすぐに入れ替わらない

`npx cdk deploy` でコード・環境変数を更新しても、既存の実行中コンテナは古いコード＆環境変数のまま動き続ける。新しい設定が反映されるのは新規に起動されるコンテナのみ。

対処法:
- 15 分のアイドルタイムアウトを待つ（自然に消える）
- `aws bedrock-agentcore stop-runtime-session` で明示的に停止

### セッション ID でコンテナがルーティングされる

`runtimeSessionId` に同じ値を渡すと同じコンテナに到達する。LINE の `reply_to`（user_id or group_id）をセッション ID に使うことで、同じチャット画面なら同じコンテナ（= 同じ会話履歴）で対話できる。

### DEFAULT エンドポイントが自動作成される

Runtime を作成すると DEFAULT エンドポイントが自動作成される。`addEndpoint()` を呼ぶと不要なエンドポイントが増えるため、特別な理由がなければ呼ばない。

### クロスリージョン推論には inference-profile の権限が必要

`us.anthropic.claude-*` 形式のモデル ID を使う場合、IAM ポリシーに `foundation-model/*` だけでなく `inference-profile/*` も必要。後者がないと `AccessDeniedException` が発生する。

---

## Strands Agents

### MCPClient を Agent の tools に直接渡すとライフサイクル自動管理

`MCPClient` インスタンスを `Agent(tools=[...])` に直接渡すと、Agent が MCPClient のライフサイクル（接続・切断）を自動管理してくれる。`with` ステートメントでの手動管理は不要。

### AWS Knowledge MCP Server は認証不要

`https://knowledge-mcp.global.api.aws` は認証なしで接続可能なリモート MCP サーバー。`streamablehttp_client` で直接接続でき、Dockerfile の変更も不要。search_documentation、read_documentation 等の AWS ドキュメント検索ツールが提供される。

### RSS ツールは extras インストールが必要

`strands-agents-tools` の RSS ツールを使うには `strands-agents-tools[rss]` でインストールする。これにより `feedparser`、`html2text`、`requests` が追加される。`action="fetch"` で URL を指定すれば、購読管理なしでフィードを直接取得できる。

### カスタムツールは urllib.request で十分

外部 REST API を呼ぶカスタムツール（Tavily 等）は Python 標準ライブラリの `urllib.request` で実装すれば追加パッケージ不要。Docker イメージのビルド時間短縮にも有効。

### セッション管理は Agent インスタンスの再利用で実現

Strands Agent は内部に会話履歴を保持する。セッション ID ごとに Agent インスタンスを辞書で管理し、同じセッション ID なら同じ Agent を返すことで会話の継続性を実現。TTL を設けてメモリリークを防止する。

---

## Kimi K2 Thinking

### cache_prompt / cache_tools を指定してはいけない

Kimi K2 で `BedrockModel` を作成する際、`cache_prompt` や `cache_tools` を指定すると `AccessDeniedException` が発生する。モデル ID に `"kimi"` が含まれる場合はこれらのオプションを省略すること。

### クロスリージョン推論のプレフィックスがない

Claude は `us.` や `jp.` のプレフィックスが付くが、Kimi K2 はプレフィックスなしの `moonshot.kimi-k2-thinking` をそのまま使用する。

---

## CDK / インフラ

### Lambda ARM64 とバンドリングの platform は一致させる

Lambda の `architecture: ARM_64` と Code バンドリングの `platform: "linux/arm64"` は必ず一致させること。不一致だとネイティブ依存パッケージ（C 拡張等）が正しくビルドされない。

### dotenv で .env.local を自動読み込み

CDK エントリーポイント（`bin/agentcore-line-chatbot.ts`）で `dotenv.config({ path: ".env.local" })` を呼ぶことで、`process.env` 経由で環境変数を CDK スタックに渡せる。手動での `export` が不要になる。

### AgentCore の OTEL トレースには 3 点セットが必要

1. `requirements.txt`: `strands-agents[otel]` + `aws-opentelemetry-distro`
2. `Dockerfile`: `CMD ["opentelemetry-instrument", "python", "agent.py"]`
3. CDK 環境変数: `AGENT_OBSERVABILITY_ENABLED=true` 他

3 つすべてが揃わないとトレースが出力されない。

---

## 設計判断

### Lambda と Agent を分離した理由

Lambda（webhook.py）は LINE 固有の処理（署名検証、Push Message 送信、ローディング表示）を担当し、Agent（agent.py）は LINE に依存しない汎用的な AI エージェントとして設計。

メリット:
- Agent を LINE 以外のインターフェース（Web UI 等）でも再利用可能
- LINE SDK の依存を Lambda 側に閉じ込められる
- Agent のテスト・開発が LINE 環境なしで可能

### SSE のテキストバッファリング

AI の回答は小さなチャンク（1〜数文字）で届く。これをそのまま Push Message にすると大量のメッセージが送られてしまう。`contentBlockDelta` でバッファに蓄積し、`contentBlockStop` でまとめて送信することで、自然な粒度のメッセージになる。

### ツール使用時のステータスメッセージ

ツール実行中は数秒〜数十秒の無応答時間が生じる。`contentBlockStart(toolUse)` を検出して日本語のステータスメッセージ（「ウェブ検索しています...」等）を送ることで、ユーザーに処理状況を伝える。
