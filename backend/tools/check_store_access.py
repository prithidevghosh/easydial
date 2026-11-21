"""Read-only, anonymous homepage diagnostic. No login, cart, retries, or spoofing."""
import asyncio
import json

from easydial.providers.browser import BrowserSession


async def main():
    browser = BrowserSession()
    browser.headless = False
    try:
        page, _ = await browser.open()
        response = await page.goto("https://blinkit.com/", wait_until="domcontentloaded", timeout=45000)
        body = (await page.locator("body").inner_text()).casefold()
        headers = await response.all_headers() if response else {}
        denied = "access denied" in body or "sorry, you have been blocked" in body
        print(json.dumps({
            "store": "blinkit", "http_status": response.status if response else None,
            "title": await page.title(), "chrome_version": browser.browser.version,
            "headed": True, "anonymous": True, "access_denied": denied,
            "server": headers.get("server"), "ray_id": headers.get("cf-ray"),
        }, indent=2))
        return 2 if denied else (0 if response and response.ok else 1)
    finally:
        await browser.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
