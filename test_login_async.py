import asyncio
from app.main import app
from httpx import AsyncClient, ASGITransport

async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as c:
        r = await c.post('/admin/login', data={'email': 'admin@scentofwater.org', 'password': 'admin123'}, follow_redirects=False)
        print(f'Status: {r.status_code}')
        if r.status_code != 303:
            print(f'Body: {r.text[:2000]}')
        else:
            print(f'Location: {r.headers.get("location")}')

asyncio.run(test())
