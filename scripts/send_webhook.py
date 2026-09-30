"""Pretend to be the payment provider and send a signed webhook.

    python scripts/send_webhook.py <payment_reference> SUCCESS [event_id]

Run it twice with the same event_id to see idempotency in action.
"""
import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.security import sign_webhook_body  # noqa: E402

BASE_URL = "http://localhost:8000"


def send(client: httpx.Client, reference: str, status: str, event_id: str | None = None) -> tuple[str, httpx.Response]:
    """Sign the event exactly like the provider would and POST it."""
    event_id = event_id or f"evt_{uuid.uuid4().hex}"
    body = json.dumps({"event_id": event_id, "payment_reference": reference, "status": status}).encode()
    response = client.post(
        "/payments/webhook/",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": sign_webhook_body(body)},
    )
    return event_id, response


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit("usage: python scripts/send_webhook.py <payment_reference> SUCCESS|FAILED [event_id]")
    reference, status = argv[1], argv[2]
    event_id = argv[3] if len(argv) > 3 else None

    client = httpx.Client(base_url=BASE_URL)
    event_id, response = send(client, reference, status, event_id)
    print(f"event_id={event_id}")
    print(response.status_code, response.json())


if __name__ == "__main__":
    main(sys.argv)
