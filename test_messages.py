import traceback
from app.main import app
from starlette.testclient import TestClient

client = TestClient(app)

try:
    r = client.get("/messages")
    print(f"Status: {r.status_code}")
    print(f"Length: {len(r.text)}")
    if r.status_code >= 400:
        print(r.text[:3000])
except Exception as e:
    traceback.print_exc()
