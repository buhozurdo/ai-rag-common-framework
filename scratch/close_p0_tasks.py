import urllib.request
import json

base_url = "http://localhost:8161"
headers = {
    "X-Project-Id": "ai-rag-common-framework",
    "X-Actor-Type": "agent",
    "X-Actor-Id": "ai-rag-common-framework",
    "Content-Type": "application/json"
}

def update_status(task_id: str, status: str, note: str):
    url = f"{base_url}/projects/ai-rag-common-framework/tasks/{task_id}/status"
    payload = json.dumps({"status": status, "note": note}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            print(f"Updated {task_id} -> {status}: OK")
    except Exception as ex:
        print(f"Failed to update {task_id}: {ex}")

tasks_to_close = [
    ("BUG-05", "Fixed in v0.19.0: Version dynamically imported from __version__ across app, server, client"),
    ("BUG-06", "Fixed in v0.19.0: Added include_metrics parameter to create_app"),
    ("BUG-07", "Fixed in v0.19.0: Aliased create_sse_app to get_sse_app on FrameworkMCPServer"),
    ("BUG-08", "Fixed in v0.19.0: Mounted Prometheus GET /metrics in create_app"),
    ("BUG-09", "Fixed in v0.19.0: Added Gemini pricing, cached_tokens discount, and auto-cost tracking"),
    ("BUG-10", "Fixed in v0.19.0: Partitioned cache namespace by model/temp/sys and enhanced mock vector similarity"),
    ("BUG-11", "Fixed in v0.19.0: Added HMAC secret key support and international E.164 phone formats"),
    ("BUG-12", "Fixed in v0.19.0: Added Spanish stop words and custom stopword sets to hallucination and triad evaluation"),
    ("FEAT-13", "Delivered in v0.19.0: Public API contract freeze with @deprecated and warn_deprecated_parameter"),
    ("TASK-22", "Delivered in v0.19.0: Reconciled test suite inventory (331 total: 312 unit + 19 integration)"),
    ("EPIC-17", "Delivered in v0.19.0: All P0 fixes resolved, API freeze established, and 331 tests passing"),
]

for tid, note in tasks_to_close:
    update_status(tid, "done", note)

print("Project Manager status updates complete!")
