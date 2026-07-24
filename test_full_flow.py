import asyncio
from app.main import app
from httpx import AsyncClient, ASGITransport

async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as c:
        # Login
        r = await c.post('/admin/login', data={'email': 'admin@scentofwater.org', 'password': 'admin123'}, follow_redirects=False)
        print(f'Login: {r.status_code} -> {r.headers.get("location")}')

        # Follow redirect to dashboard
        r = await c.get('/admin/dashboard', follow_redirects=False)
        print(f'Dashboard: {r.status_code} ({len(r.text)} bytes)')
        if r.status_code == 200:
            if 'Super Admin' in r.text:
                print('  -> Super Admin name found!')
            if 'Dashboard' in r.text or 'dashboard' in r.text.lower():
                print('  -> Dashboard content loaded!')
        elif r.status_code == 303:
            print(f'  -> Re-redirect: {r.headers.get("location")}')

        # Test admin sub-routes
        for path in ['/admin/members', '/admin/departments', '/admin/messages', '/admin/events']:
            r = await c.get(path, follow_redirects=False)
            if r.status_code == 200:
                print(f'{path}: 200 ({len(r.text)} bytes)')
            elif r.status_code == 303:
                print(f'{path}: 303 -> {r.headers.get("location")}')
            else:
                print(f'{path}: {r.status_code}')

asyncio.run(test())
