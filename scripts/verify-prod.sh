#!/usr/bin/env bash
# デプロイ後の本番検査。READY 表示やデプロイ成功だけでは「起動できない」「呼び出しが拒否される」を検出できないため、
# 設定値を読んだうえで、実際にエージェントを呼んで回答が返るところまで確かめる。
# 使い方: AWS_PROFILE=your-profile scripts/verify-prod.sh
set -euo pipefail
export AWS_REGION=${AWS_REGION:-us-east-1}
STACK=AgentcoreLineChatbotStack
fail() { echo "NG: $*"; exit 1; }

FN=$(aws cloudformation describe-stack-resources --stack-name "$STACK" \
  --query "StackResources[?ResourceType=='AWS::Lambda::Function' && starts_with(LogicalResourceId, 'WebhookFunction')].PhysicalResourceId" --output text)
ARN=$(aws lambda get-function-configuration --function-name "$FN" --query 'Environment.Variables.AGENTCORE_RUNTIME_ARN' --output text)
RID=${ARN##*/}

# 1. 構成の検査
read -r STATUS MMDS SECRET <<<"$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RID" \
  --query '[status, metadataConfiguration.requireMMDSV2, environmentVariables.TAVILY_SECRET_ARN]' --output text)"
[ "$STATUS" = READY ] || fail "Runtime status=$STATUS"
[ "$MMDS" = True ] || fail "requireMMDSV2 が有効ではない（無効だと呼び出しが拒否される）"
[ "$SECRET" != None ] || fail "TAVILY_SECRET_ARN が未設定"
MAINT=$(aws lambda get-function-configuration --function-name "$FN" --query 'Environment.Variables.MAINTENANCE_MESSAGE' --output text)
case "$MAINT" in ""|None) ;; *) echo "注意: 障害中メッセージが有効（エージェントは呼ばれない）: $MAINT" ;; esac
echo "OK: 構成（READY / MMDSv2 / Tavilyシークレット）"

# 2. 経路の検査（エージェントを実際に呼び、ウェブ検索つきの回答が返るか）
ARN="$ARN" uv run -q --with boto3 python - <<'PY'
import boto3, json, os, sys, time, uuid
c = boto3.client("bedrock-agentcore")
sid = "verify-prod-" + uuid.uuid4().hex
t = time.time()
r = c.invoke_agent_runtime(
    agentRuntimeArn=os.environ["ARN"], runtimeSessionId=sid, qualifier="DEFAULT",
    payload=json.dumps({"prompt": "今日のニュースをウェブ検索して1つだけ短く教えて", "session_id": sid}).encode(),
)
tools, text = [], ""
for line in r["response"].iter_lines(chunk_size=64):
    s = line.decode("utf-8")
    if not s.startswith("data: "):
        continue
    try:
        e = json.loads(s[6:])
    except json.JSONDecodeError:
        continue
    ev = e.get("event") if isinstance(e, dict) else None
    if not isinstance(ev, dict):
        continue
    tu = ev.get("contentBlockStart", {}).get("start", {}).get("toolUse")
    if tu:
        tools.append(tu["name"])
    text += ev.get("contentBlockDelta", {}).get("delta", {}).get("text", "")
print(f"{time.time() - t:.1f}s tools={tools}")
if "web_search" not in tools or len(text.strip()) < 20:
    sys.exit("NG: ウェブ検索つきの回答が返らなかった")
if "一時的に使えません" in text:
    sys.exit("NG: Tavily キーがすべて使えない")
print("OK: 経路（エージェント起動・ウェブ検索・回答）")
PY
