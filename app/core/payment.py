"""Payment gateways. Every call names its currency (ISO 4217): the church's account may be abroad, and
an unnamed currency silently falls back to the account's default. Amounts to Paystack are in the
currency's minor unit (kobo, pesewas, cents: app.core.geo.minor_units); Flutterwave takes major units."""
import httpx

# Online gateways the church can switch between (Settings → Giving gateway; Branch settings for a branch's own account).
GATEWAYS = {"paystack": "Paystack", "flutterwave": "Flutterwave", "bachs": "Bachs"}


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
    """Verify a Flutterwave transaction. Returns a dict with 'success' bool and 'amount' float.

    Without the gateway's numeric transaction id (webhooks, scheduled re-checks) it verifies by our own reference.
    """
    transaction_id = str(transaction_id or "").strip()
    if transaction_id and not transaction_id.isdigit():
        return {"success": False, "state": "error"}       # it goes into the URL path: never pass anything else
    if transaction_id:
        url, params = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify", None
    else:
        url, params = "https://api.flutterwave.com/v3/transactions/verify_by_reference", {"tx_ref": tx_ref}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            url,
            params=params,
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


# ── Bachs (https://docs.bachs.io) ─────────────────────────────────────────
# Hosted checkout sessions. Amounts are decimal strings in MAJOR units; the key prefix picks the host
# (sk_sandbox_… → sandbox, sk_live_… → live). Verification needs Bachs's checkout id, which the checkout
# stores on the gift (Giving.gateway_txn_id); the success redirect and webhooks also carry it.
BACHS_LIVE_URL = "https://api.bachs.io"
BACHS_SANDBOX_URL = "https://sandbox-api.bachs.io"
BACHS_MINIMUMS = {"NGN": 1000, "USD": 1}          # per-charge minimums Bachs documents; others are checked by Bachs


def bachs_base_url(secret_key: str) -> str:
    return BACHS_SANDBOX_URL if (secret_key or "").startswith(("sk_sandbox_", "pk_sandbox_")) else BACHS_LIVE_URL


def _bachs_body(response: httpx.Response) -> dict:
    data = response.json()
    return data.get("data") if isinstance(data.get("data"), dict) else data


async def initialize_bachs(
    email: str,
    name: str,
    amount: float,
    reference: str,
    success_url: str,
    cancel_url: str,
    metadata: dict | None = None,
    secret_key: str = "",
    currency: str = "NGN",
    decimals: int = 2,
) -> tuple[str | None, str | None]:
    """Create a hosted checkout. Returns (checkout_url, checkout_id), or (None, None)."""
    payload = {
        "pricing": {"currency": currency, "amount": f"{amount:.{decimals}f}"},
        "customer": {"email": email, "name": name or email.split("@")[0]},
        "success_url": success_url,
        "cancel_url": cancel_url,
        "reference": reference,
    }
    if metadata:
        payload["metadata"] = {k: str(v)[:500] for k, v in list(metadata.items())[:20]}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{bachs_base_url(secret_key)}/v1/checkout-sessions",
            json=payload,
            headers={"Authorization": f"Bearer {secret_key}", "Content-Type": "application/json",
                     "Idempotency-Key": reference},
        )
    if response.status_code not in (200, 201):
        return None, None
    body = _bachs_body(response)
    return body.get("checkout_url"), body.get("checkout_id")


async def verify_bachs(checkout_id: str, reference: str, secret_key: str = "") -> dict:
    """Ask Bachs about one checkout. Same shape as verify_paystack/verify_flutterwave (amount in major units)."""
    checkout_id = str(checkout_id or "").strip()
    if not checkout_id:
        return {"success": False, "state": "pending"}      # checkout never created, or id not known yet
    if not checkout_id.replace("_", "").isalnum():
        return {"success": False, "state": "error"}        # it goes into the URL path: never pass anything else
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{bachs_base_url(secret_key)}/v1/checkout-sessions/{checkout_id}",
            headers={"Authorization": f"Bearer {secret_key}"},
        )
    if response.status_code != 200:
        return {"success": False, "state": "error"}
    s = _bachs_body(response)
    if s.get("reference") and s.get("reference") != reference:
        return {"success": False, "state": "error"}        # someone else's checkout: never let it settle this gift
    status = str(s.get("status") or "").upper()
    pay = str(s.get("payment_status") or "").lower()
    charge = s.get("charge") or {}
    cstatus = str(charge.get("status") or "").lower()
    currency = charge.get("currency") or s.get("currency") or ""
    if cstatus == "underpaid":                              # paid, but less than asked: reconcile marks it a mismatch
        return {"success": False, "state": "paid", "amount": float(charge.get("amount_paid") or 0), "currency": currency}
    if status == "COMPLETED" or pay == "succeeded" or cstatus in ("succeeded", "accepted", "overpaid"):
        taken = charge.get("amount_paid") or charge.get("amount") or s.get("amount") or 0
        return {"success": True, "state": "paid", "amount": float(taken), "currency": currency}
    if status == "OPEN":
        return {"success": False, "state": "pending"}
    if status in ("EXPIRED", "CANCELLED"):
        return {"success": False, "state": "abandoned"}
    return {"success": False, "state": "failed"}
