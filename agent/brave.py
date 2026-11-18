"""Brave Search API（AWS Marketplace の従量課金で契約）。

キーはコードにも環境変数にも置かず、AgentCore Identity の API キー保管庫 `brave` から読む。
保管庫から読むには workload token が要る。Runtime が呼び出しに付けてくれるのはユーザーID付きで
呼ばれたときだけなので、無ければ環境変数 BRAVE_WORKLOAD の名前のワークロード ID で自分で取る。
エントリーポイントの最初で prime() を呼んでキーを取っておく。
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_key = None


def prime():
    """保管庫から Brave のキーを取ってコンテナの中に持っておく（取れていれば何もしない）。"""
    global _key
    if _key is None:
        client = boto3.client("bedrock-agentcore")
        token = BedrockAgentCoreContext.get_workload_access_token()
        if not token and os.environ.get("BRAVE_WORKLOAD"):
            token = client.get_workload_access_token(workloadName=os.environ["BRAVE_WORKLOAD"])["workloadAccessToken"]
        if token:
            _key = client.get_resource_api_key(
                resourceCredentialProviderName="brave", workloadIdentityToken=token)["apiKey"]


def search(query, count=5, recent_days=0, japanese=True):
    """Brave で検索し、title・url・age・text の辞書のリストを返す。"""
    prime()
    if _key is None:
        raise RuntimeError("Brave のキーを取れない（workload token が無く、BRAVE_WORKLOAD も無い）")
    params = {"q": query, "count": count}
    if japanese:
        params.update(country="JP", search_lang="jp", ui_lang="ja-JP")
    if recent_days > 0:
        today = datetime.now(timezone.utc).date()
        params["freshness"] = f"{today - timedelta(days=recent_days)}to{today}"
    req = urllib.request.Request(ENDPOINT + "?" + urllib.parse.urlencode(params),
                                 headers={"Accept": "application/json", "X-Subscription-Token": _key})
    with urllib.request.urlopen(req, timeout=10) as res:
        data = json.load(res)
    return [{"title": r.get("title"), "url": r.get("url"), "age": r.get("age") or r.get("page_age"),
             "text": r.get("description") or ""} for r in data.get("web", {}).get("results", [])]
