import traceback
from app.main import app
from starlette.testclient import TestClient

client = TestClient(app)

try:
    r = client.get("/messages")
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(r.text[:3000])
    else:
        print(f"OK - {len(r.text)} bytes")
except Exception as e:
    traceback.print_exc()
