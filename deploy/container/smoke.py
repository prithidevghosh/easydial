"""Run inside the Linux container; no merchant login or network request is made."""
import asyncio
from easydial.providers.browser import BrowserSession


async def main():
    for headless in (False, True):
        browser = BrowserSession()
        browser.headless = headless
        try:
            page, _ = await browser.open()
            await page.set_content('<h1>Browser ready</h1>')
            assert await page.locator('h1').inner_text() == 'Browser ready'
            print('Chrome verified:', 'headless' if headless else 'headed on Xvfb', flush=True)
        finally:
            await browser.close()


asyncio.run(asyncio.wait_for(main(), timeout=90))
