import asyncio
from app.main import app
from starlette.testclient import TestClient

client = TestClient(app)
r = client.get("/")
print(f"Status: {r.status_code}")
if r.status_code != 200:
    print(f"Body: {r.text[:2000]}")
else:
    print(f"Content length: {len(r.text)}")
