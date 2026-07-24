"""Test admin login flow."""
from app.main import app
from starlette.testclient import TestClient

client = TestClient(app)

# 1. GET /admin should show login page
r = client.get("/admin")
print(f"1. GET /admin -> {r.status_code} ({len(r.text)} bytes)")

# 2. POST /admin/login with wrong creds (follows redirect)
r = client.post("/admin/login", data={"email": "wrong@test.com", "password": "wrong"})
print(f"2. POST /admin/login (wrong creds) -> {r.status_code}")

# 3. POST /admin/login with correct creds
r = client.post("/admin/login", data={"email": "admin@scentofwater.org", "password": "admin123"})
print(f"3. POST /admin/login (correct) -> {r.status_code} url={r.url}")

# 4. Access dashboard after login
r = client.get("/admin/dashboard")
print(f"4. GET /admin/dashboard -> {r.status_code} ({len(r.text)} bytes)")

# 5. Check if login template resolves url_for correctly
r2 = client.get("/admin")
# Extract form action
import re
match = re.search(r'action="([^"]+)"', r2.text)
if match:
    print(f"5. Login form action: {match.group(1)}")
else:
    print("5. No form action found")

print("\nDone!")
