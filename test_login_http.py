"""Test admin login flow via HTTP against running server."""
import requests
import re

BASE = "http://127.0.0.1:8000"
s = requests.Session()

# 1. GET /admin - login page
r = s.get(f"{BASE}/admin")
print(f"1. GET /admin -> {r.status_code} ({len(r.text)} bytes)")

# Extract form action
match = re.search(r'action="([^"]+)"', r.text)
if match:
    form_action = match.group(1)
    print(f"   Form action: {form_action}")
else:
    print("   No form action found!")
    form_action = "/admin/login"

# Extract CSRF hidden field if any
csrf_match = re.search(r'name="_token"[^>]*value="([^"]*)"', r.text)
csrf_token = csrf_match.group(1) if csrf_match else ""
print(f"   CSRF token: {csrf_token or '(none)'}")

# 2. POST login with correct creds
data = {"email": "admin@scentofwater.org", "password": "admin123"}
if csrf_token:
    data["_token"] = csrf_token
r = s.post(f"{BASE}{form_action}", data=data, allow_redirects=False)
print(f"2. POST {form_action} -> {r.status_code}")
print(f"   Location: {r.headers.get('location', 'N/A')}")
print(f"   Cookies: {dict(s.cookies)}")

# 3. Follow redirect to dashboard
r = s.get(f"{BASE}/admin/dashboard", allow_redirects=False)
print(f"3. GET /admin/dashboard -> {r.status_code} ({len(r.text)} bytes)")
if r.status_code == 303:
    print(f"   Re-redirect: {r.headers.get('location')}")
elif r.status_code == 200:
    # Check for admin name in dashboard
    if "Super Admin" in r.text:
        print("   'Super Admin' found in dashboard!")
    if "dashboard" in r.text.lower():
        print("   Dashboard content loaded.")

# 4. Test a few admin sub-routes
for path in ["/admin/members", "/admin/departments", "/admin/messages", "/admin/events", "/admin/givings", "/admin/sermons"]:
    r = s.get(f"{BASE}/admin{path}")
    status = f"{r.status_code} ({len(r.text)} bytes)" if r.status_code == 200 else r.status_code
    print(f"4. GET /admin{path} -> {status}")

print("\nDone!")
