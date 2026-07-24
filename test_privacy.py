from app.main import app
from starlette.testclient import TestClient

client = TestClient(app)
for path in ["/privacy-policy", "/terms-of-service"]:
    try:
        r = client.get(path)
        print(f"{r.status_code} {path}")
        if r.status_code != 200:
            print(r.text[:2000])
    except Exception as e:
        print(f"ERR {path}: {e}")
