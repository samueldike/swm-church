import urllib.request
import urllib.error

routes = [
    "/",
    "/about",
    "/departments",
    "/messages",
    "/events",
    "/calendar",
    "/casom",
    "/contact",
    "/get-involved",
    "/give",
    "/privacy-policy",
    "/terms-of-service",
    "/sitemap",
    "/coming-soon",
    "/stories",
    "/prayer",
    "/watch",
    "/admin",
    "/admin/dashboard",
]

for path in routes:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:8000{path}")
        resp = urllib.request.urlopen(req, timeout=5)
        size = len(resp.read())
        print(f"  {resp.status} {path:40s} ({size:>7} bytes)")
    except urllib.error.HTTPError as e:
        print(f"  {e.code} {path:40s}")
    except Exception as e:
        print(f"  ERR {path:40s} {str(e)[:80]}")
