import urllib.request
import json

base_url = "http://localhost:8161"
headers = {
    "X-Project-Id": "ai-rag-common-framework",
    "X-Actor-Type": "agent",
    "X-Actor-Id": "ai-rag-common-framework"
}

req = urllib.request.Request(f"{base_url}/projects/ai-rag-common-framework/tasks", headers=headers)
with urllib.request.urlopen(req) as resp:
    tasks = json.loads(resp.read().decode())
    v019_tasks = [t for t in tasks if t.get("parent_id") == "EPIC-17" or t.get("id") == "EPIC-17"]
    print(f"Tasks in EPIC-17 ({len(v019_tasks)}):")
    for t in sorted(v019_tasks, key=lambda x: x.get("id", "")):
        print(f"  [{t.get('id')}] {t.get('title')}: status = {t.get('status')}")
