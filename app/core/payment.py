"""Payment gateways. Every call names its currency (ISO 4217): the church's account may be abroad, and
an unnamed currency silently falls back to the account's default. Amounts to Paystack are in the
currency's minor unit (kobo, pesewas, cents: app.core.geo.minor_units); Flutterwave takes major units."""
import httpx


async def initialize_paystack(
    email: str,
    amount_minor: int,
    reference: str,
    callback_url: str,
    metadata: dict | None = None,
    secret_key: str = "",
    currency: str = "NGN",
) -> str | None:
    url = "https://api.paystack.co/transaction/initialize"

    payload = {
        "email": email,
        "amount": amount_minor,
        "currency": currency,
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


async def verify_paystack(reference: str, secret_key: str = "") -> dict:
    """Verify a Paystack transaction. Returns a dict with 'success' bool and 'amount_kobo' int."""
    url = f"https://api.paystack.co/transaction/verify/{reference}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {secret_key}"},
        )
    if response.status_code != 200:
        return {"success": False, "state": "error"}
    data = response.json()
    if not data.get("status"):
        return {"success": False, "state": "error"}
    tx = data.get("data", {})
    raw = tx.get("status")
    return {
        "success": raw == "success",
        # "pending" = the customer has not finished paying yet (do not brand it failed)
        "state": "paid" if raw == "success" else ("pending" if raw in ("abandoned", "pending", "ongoing", "processing", "queued") else "failed"),
        "amount_minor": tx.get("amount", 0),
        "currency": tx.get("currency") or "",
        "email": tx.get("customer", {}).get("email", ""),
    }


async def verify_flutterwave(transaction_id: str, tx_ref: str, secret_key: str = "") -> dict:
    """Verify a Flutterwave transaction. Returns a dict with 'success' bool and 'amount' float."""
    if not transaction_id:
        return {"success": False, "state": "pending"}
    url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {secret_key}"},
        )
    if response.status_code != 200:
        return {"success": False, "state": "error"}
    data = response.json()
    if data.get("status") != "success":
        return {"success": False, "state": "error"}
    tx = data.get("data", {})
    raw = tx.get("status")
    ok = raw == "successful" and tx.get("tx_ref") == tx_ref
    return {
        "success": ok,
        "state": "paid" if ok else ("pending" if raw in ("pending", "processing", "new") else "failed"),
        "amount": float(tx.get("amount", 0)),
        "currency": tx.get("currency") or "",
        "email": tx.get("customer", {}).get("email", ""),
    }


async def initialize_flutterwave(
    email: str,
    amount: float,
    reference: str,
    redirect_url: str,
    customer: dict | None = None,
    customizations: dict | None = None,
    secret_key: str = "",
    currency: str = "NGN",
) -> str | None:
    url = "https://api.flutterwave.com/v3/payments"

    payload = {
        "tx_ref": reference,
        "amount": str(amount),
        "currency": currency,
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
