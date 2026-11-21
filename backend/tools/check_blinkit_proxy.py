"""Read-only local smoke test: sticky IP, Blinkit login UI, and session restoration.

Run from backend: uv run python tools/check_blinkit_proxy.py
Uses an isolated temporary browser state, never the customer's saved profile.
"""

import asyncio
import json
import tempfile
from pathlib import Path

from local_settings import ENV

from easydial.providers.browser import BrowserSession
from easydial.providers.proxy import configure_blinkit_proxy
from easydial.settings import Settings
from easydial.whatsapp.store_login import StoreLogin


async def main():
    settings = Settings(_env_file=ENV)
    with tempfile.TemporaryDirectory(prefix="easydial-proxy-check-") as directory:
        state = str(Path(directory) / "state.json")
        browser = BrowserSession()
        browser.channel = settings.browser_channel
        browser.headless = False
        configure_blinkit_proxy(browser, settings)
        if not browser.proxy:
            raise RuntimeError("Configure the Blinkit proxy first")
        try:
            page, _ = await browser.open()
            first_ip = browser.proxy.exit_ip
            print(json.dumps({"stage": "proxy", "exit_ip": first_ip}), flush=True)
            response = await page.goto("https://blinkit.com/", wait_until="domcontentloaded", timeout=45000)
            print(json.dumps({"stage": "blinkit", "status": response.status if response else None}), flush=True)
            await browser.check_page(page)
            if not response or not response.ok:
                raise RuntimeError("Blinkit did not return success")
            await StoreLogin.open_blinkit_login(page)
            assert await page.get_by_placeholder("Enter mobile number", exact=True).is_visible()
            await browser.open()  # A second IP check through the same browser.
            assert browser.proxy.exit_ip == first_ip
            browser.storage_state = state
            await browser.save()
            print(json.dumps({"stage": "login_ui", "visible": True, "same_ip": True}), flush=True)
        finally:
            await browser.close()
        restored = BrowserSession(storage_state=state)
        restored.channel = settings.browser_channel
        configure_blinkit_proxy(restored, settings)
        try:
            await restored.open()
            assert restored.proxy.exit_ip == first_ip
            print(json.dumps({"stage": "browser_restart", "same_ip": True}), flush=True)
        finally:
            await restored.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        # Browser exceptions can contain connection details; only report the type.
        print(json.dumps({"result": "failed", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
