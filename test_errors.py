import urllib.request
import urllib.error

for path in ["/events", "/privacy-policy", "/terms-of-service"]:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:8000{path}")
        resp = urllib.request.urlopen(req, timeout=5)
        print(f"  {resp.status} {path}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:2000]
        print(f"  {e.code} {path}")
        print(f"  BODY: {body}")
    except Exception as e:
        print(f"  ERR {path} {str(e)[:200]}")
