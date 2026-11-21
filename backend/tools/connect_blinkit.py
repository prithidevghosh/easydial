"""Save an operator's manually authenticated Blinkit browser session locally."""

import asyncio
from pathlib import Path

from connect_swiggy import ENV, save_settings
from playwright.async_api import async_playwright


async def main():
    state_dir = Path(__file__).resolve().parents[1] / ".state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    state_dir.chmod(0o700)
    state_path = state_dir / "blinkit.json"
    print("Log in on Blinkit's own page, then select your saved full delivery address.")
    print("Do not add products or place an order during this setup step.")
    async with async_playwright() as pw:
        browser = await pw.firefox.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://blinkit.com", wait_until="domcontentloaded")
        await asyncio.to_thread(input, "After logging in and selecting the address, press Enter here: ")
        location = await page.locator("div[class*='LocationBar__Subtitle']").first.inner_text(timeout=10000)
        print("Selected header (private):", location)
        address = (
            await asyncio.to_thread(
                input, "Enter the full saved address exactly as shown in Blinkit's basket/address selector: "
            )
        ).strip()
        if not address or not location.strip():
            raise ValueError("An explicit full address is required")
        await context.storage_state(path=state_path)
        state_path.chmod(0o600)
        save_settings(
            ENV,
            {
                "BLINKIT_STORAGE_STATE": str(state_path),
                "BLINKIT_VERIFIED_LOCATION": location.strip(),
                "BLINKIT_VERIFIED_ADDRESS": address,
            },
        )
        await browser.close()
    print("Blinkit session and address saved locally. Use the same physical address for Swiggy.")
    print("Set LIVE_ADDRESS_LABEL in .env if you have not connected Swiggy, then restart the backend.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, Exception) as error:
        print(f"Blinkit setup stopped ({type(error).__name__}). Re-run locally; no secrets were displayed.")
        raise SystemExit(1) from None
