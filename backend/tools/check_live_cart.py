"""Explicit opt-in live API smoke test. Mutates a test cart, never places an order."""

import argparse

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["blinkit", "instamart"])
    parser.add_argument("--product", default="Heritage Daily Health Toned Milk")
    parser.add_argument("--pack", default="500 ml")
    parser.add_argument("--confirm-cart-edits", action="store_true", required=True)
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as client:
        created = client.post("/sessions")
        created.raise_for_status()
        payload = created.json()
        state = payload["session"]
        path = f"/sessions/{state['id']}"
        headers = {"Authorization": f"Bearer {payload['token']}"}

        def post(route, body):
            response = client.post(path + route, headers=headers, json=body)
            response.raise_for_status()
            return response.json()

        state = post("/search", {"query": args.product, "expected_version": state["version"]})
        matches = [
            offer
            for result in state["results"]
            for offer in result["offers"]
            if offer["provider"] == args.provider
            and offer["name"] == args.product
            and offer["pack_size"] == args.pack
            and offer["available"] is True
        ]
        if len(matches) != 1:
            raise SystemExit("Exact product/pack unavailable or ambiguous. No cart was modified.")
        state = post(
            "/basket",
            {
                "kind": "add",
                "offer_id": matches[0]["id"],
                "quantity": 1,
                "expected_version": state["version"],
            },
        )
        reviewed = False
        try:
            state = post("/review", {"expected_version": state["version"]})
            reviewed = True
            total = state["review"]["total_paise"]
            state = post(
                "/confirm",
                {"expected_version": state["version"], "review_id": state["review"]["id"], "confirmed": True},
            )
            assert state["phase"] == "checkout_pending"
            print(
                f"{args.provider}: live API search/add/review → checkout_pending; payable ₹{total / 100:.2f}; no order placed"
            )
        finally:
            if reviewed:
                state = post(
                    "/basket",
                    {
                        "kind": "remove",
                        "line_id": state["lines"][0]["id"],
                        "expected_version": state["version"],
                    },
                )
                post("/review", {"expected_version": state["version"]})
                print(f"{args.provider}: test cart cleared and verified")
            else:
                print(
                    "Review did not complete. Inspect the provider cart before another run; no automatic overwrite attempted."
                )


if __name__ == "__main__":
    main()
