import httpx


async def initialize_paystack(
    email: str,
    amount_kobo: int,
    reference: str,
    callback_url: str,
    metadata: dict | None = None,
    secret_key: str = "",
) -> str | None:
    url = "https://api.paystack.co/transaction/initialize"

    payload = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "callback_url": callback_url,
    }
    if metadata:
        payload["metadata"] = metadata

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json",
            },
        )

    if response.status_code != 200:
        return None

    data = response.json()
    if not data.get("status"):
        return None

    return data.get("data", {}).get("authorization_url")


async def initialize_flutterwave(
    email: str,
    amount_naira: float,
    reference: str,
    redirect_url: str,
    customer: dict | None = None,
    customizations: dict | None = None,
    secret_key: str = "",
) -> str | None:
    url = "https://api.flutterwave.com/v3/payments"

    payload = {
        "tx_ref": reference,
        "amount": str(amount_naira),
        "currency": "NGN",
        "redirect_url": redirect_url,
        "customer": customer or {"email": email},
        "customizations": customizations or {"title": "Payment"},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json",
            },
        )

    if response.status_code != 200:
        return None

    data = response.json()
    if data.get("status") != "success":
        return None

    return data.get("data", {}).get("link")
