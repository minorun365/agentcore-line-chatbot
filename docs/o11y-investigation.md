# AgentCore Observability トレース未出力 調査レポート

## 問題

`agentcore_line_chatbot` の AgentCore Runtime から CloudWatch の GenAI Observability Traces View にトレースが一切表示されない。
同じアカウントの `marp-agent`（https://github.com/minorun365/marp-agent）では正常にトレースが表示される。

## 環境情報

- AWSアカウント: 123456789012
- リージョン: us-east-1
- Runtime名: `agentcore_line_chatbot`
- Runtime ID: `agentcore_line_chatbot-gcJwjw6ZSB`
- ロググループ: `/aws/bedrock-agentcore/runtimes/agentcore_line_chatbot-gcJwjw6ZSB-DEFAULT`

## 否定済みの仮説（11個）

| # | 仮説 | 検証方法 | 結果 |
|---|------|---------|------|
| 1 | X-Ray サンプリングレート | marp-agent と同設定で比較 | ❌ 同設定で正常 |
| 2 | MCP パッケージ競合 | pip freeze 比較 | ❌ 両方に mcp==1.26.0 |
| 3 | OTel パッケージバージョン差異 | pip freeze 比較 | ❌ 完全同一 |
| 4 | CMD `python -m agent` vs `python agent.py` | 変更してデプロイ | ❌ 変化なし |
| 5 | `OTEL_RESOURCE_ATTRIBUTES` 上書き事故 | 削除してデプロイ | ❌ 変化なし |
| 6 | `OTEL_TRACES_EXPORTER` 未設定 | `otlp` 明示してデプロイ | ❌ 変化なし |
| 7 | `OTEL_EXPORTER_OTLP_ENDPOINT` 宛先不一致 | `http://127.0.0.1:4318` 明示 | ❌ 変化なし |
| 8 | IAM ロール権限不足（xray:*） | ロールポリシー確認 | ❌ L2が自動付与済み |
| 9 | 手動 env が AgentCore 自動設定を上書き | 手動env全削除してデプロイ | ❌ 変化なし |
| 10 | VPC / NAT / エンドポイント | CDK コード確認 | ❌ `usingPublicNetwork()` |
| 11 | Dockerfile（uv/非root/ADOT二重install） | marp-agentパターンに変更 | ❌ 変化なし |

### 補足: 否定の根拠

**IAM ロール**: L2 コンストラクトが自動付与したポリシーに X-Ray 権限あり:
```json
{
  "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
  "Resource": "*",
  "Effect": "Allow",
  "Sid": "XRayAccess"
}
```

**CloudWatch Delivery Pipeline**: 一時的に仮説として浮上したが、marp-agent にも Delivery Pipeline が存在しないのにトレースが出ているため否定。

**`OTEL_LOG_LEVEL=debug`**: Python の ADOT では効果なし（OTel Python SDK のドキュメントに明記）。

## 確認済み事項

### otel-rt-logs にはログ・メトリクスが正常出力されている

- `bedrock_agentcore.app`: リクエストごとに出力 ✅
- `opentelemetry.instrumentation.botocore.bedrock-runtime`: gen_ai イベント出力 ✅
- `strands.event_loop.*` メトリクス: Embedded Metrics Format で出力 ✅
- リソース属性（service.name 等）: 正常 ✅

### X-Ray にはスパンが到達していない

```bash
aws xray get-trace-summaries ... → Total traces: 0（全サービスで0件）
```

`/aws/spans/default` ロググループも存在しない。

### CloudWatch / X-Ray 基盤設定は正常

- Transaction Search: 有効 (`Destination: CloudWatchLogs`, `Status: ACTIVE`)
- リソースポリシー: 設定済み
- GenAI Observability ダッシュボード自体は動作中（marp-agent は表示される）

## 現在の状態

### CDK 環境変数（最小構成に戻した）

```typescript
environmentVariables: {
  TAVILY_API_KEY: process.env.TAVILY_API_KEY || "",
  AGENT_OBSERVABILITY_ENABLED: "true",
  OTEL_PYTHON_DISTRO: "aws_distro",
  OTEL_PYTHON_CONFIGURATOR: "aws_configurator",
  OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf",
},
```

marp-agent と完全に同じ OTEL 環境変数構成。

### Dockerfile（marp-agent パターンに変更済み）

```dockerfile
FROM python:3.13-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/ping || exit 1
CMD ["opentelemetry-instrument", "python", "agent.py"]
```

## 残っている差分: agent.py のコード（最有力）

marp-agent のソースコードを直接比較した結果、CDK の env 変数・Dockerfile 構成は実質同一。
**唯一の未検証差分は agent.py の実装コード**。

### marp-agent（正常）vs agentcore-line-chatbot（問題）

| 項目 | marp-agent | agentcore-line-chatbot |
|------|-----------|----------------------|
| **import** | `from bedrock_agentcore import BedrockAgentCoreApp` | `from bedrock_agentcore.runtime import BedrockAgentCoreApp` |
| **yield パターン** | 構造化: `{"type": "text", "data": chunk}` | **Strands 生イベント passthrough**: `yield event` |
| **MCPClient** | なし | モジュールレベルで初期化（`streamablehttp_client`） |
| **セッション管理** | あり（`get_or_create_agent`） | あり（`_get_or_create_agent`） |
| **追加パッケージ** | `tavily-python` | `botocore`, `mcp`, `strands-agents-tools[rss]` |

### 疑わしい差分（優先順）

#### 1. import パスの違い

```python
# marp-agent（正常）
from bedrock_agentcore import BedrockAgentCoreApp

# agentcore-line-chatbot（問題）
from bedrock_agentcore.runtime import BedrockAgentCoreApp
```

同じクラスの re-export かもしれないが、異なるクラス/初期化パスの可能性もある。

#### 2. yield パターンの違い

```python
# marp-agent（正常）— Strands イベントを変換して yield
async for event in stream:
    if "data" in event:
        yield {"type": "text", "data": event["data"]}
    elif "current_tool_use" in event:
        yield {"type": "tool_use", "data": tool_name}
yield {"type": "done"}

# agentcore-line-chatbot（問題）— Strands イベントをそのまま yield
async for event in agent.stream_async(prompt):
    yield event
```

Strands の生イベントが `BedrockAgentCoreApp` の SSE 変換や OTel スパン管理に影響している可能性。

#### 3. MCPClient のモジュールレベル初期化

```python
# モジュール読み込み時に実行される
aws_docs_client = MCPClient(
    lambda: streamablehttp_client(url="https://knowledge-mcp.global.api.aws")
)
```

`streamablehttp_client` は httpx ベース。`opentelemetry-instrument` による auto-instrumentation の初期化順序と干渉する可能性。

## 解決: import パスの変更でトレースが出力された ✅

### 原因

```python
# NG: このパスだとトレースが出力されない
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# OK: 公式ドキュメントと同じパスならトレースが出力される
from bedrock_agentcore import BedrockAgentCoreApp
```

### 検証結果

| 変更 | 結果 |
|------|------|
| `from bedrock_agentcore import BedrockAgentCoreApp` に変更 | ✅ GenAI Observability Traces に表示された |

### 考察

- 内部的にはどちらも `bedrock_agentcore/runtime/app.py` の同じクラスを使用する
- OTel のログ・メトリクスは両パスで正常出力されていた
- **トレース（X-Ray スパン）のエクスポートのみ**影響を受ける
- SDK のトップレベル `__init__.py` で行われる Observability 初期化フックに、runtime サブモジュール経由だと乗らない（または条件付き）と推測
- 11 個の仮説を否定した後、import パスの 1 行変更で解決した

### 検証しなかった仮説（不要になった）

- MCPClient のモジュールレベル初期化 → 未検証（import パスで解決）
- yield パターンの構造化 → 未検証（import パスで解決）
- `OTEL_TRACES_SAMPLER=always_on` → 未検証（`Sampled=1` でサンプリングは正常だったため不要と判断）

## 参考リンク

- [AgentCore Observability Quickstart](https://aws.github.io/bedrock-agentcore-starter-toolkit/user-guide/observability/quickstart.md)
- [AgentCore Observability 設定ドキュメント](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [AgentCore Observability 公式ドキュメント](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html)
- [AgentCore Streaming Response 公式例（正しい import パス）](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/response-streaming.html)
- [GitHub: Traces not sent when launched with bedrock core runtime](https://github.com/orgs/langfuse/discussions/8694)
