"""One Webshare India sticky session per connected Blinkit journey."""

import ipaddress
import json
import os
import secrets
from pathlib import Path

from ..controller import DomainError


class BlinkitProxy:
    def __init__(self, username, password):
        self.username, self.password = username, password
        self.session_id = str(secrets.randbelow(10**12 - 1) + 1)
        self.exit_ip = None
        self.loaded = False

    @property
    def options(self):
        return {
            "server": "http://p.webshare.io:80",
            "username": f"{self.username}-in-{self.session_id}",
            "password": self.password,
        }

    @staticmethod
    def path(browser):
        if browser.profile:
            return Path(browser.profile) / ".easydial-proxy.json"
        if browser.storage_state:
            return Path(browser.storage_state + ".proxy")
        return None

    def load(self, browser):
        if self.loaded:
            return
        path = self.path(browser)
        if path and path.is_file():
            raw = path.read_text()
            data = browser.secret_box.open(raw) if browser.secret_box else json.loads(raw)
            if not str(data["session_id"]).isdigit():
                raise ValueError("Invalid proxy session")
            self.session_id = str(data["session_id"])
            self.exit_ip = str(ipaddress.IPv4Address(data["exit_ip"]))
        self.loaded = True

    def save(self, browser):
        path = self.path(browser)
        if not path or not self.exit_ip:
            return
        data = {"session_id": self.session_id, "exit_ip": self.exit_ip}
        path.parent.mkdir(parents=True, exist_ok=True)
        if browser.secret_box:
            browser.secret_box.write(str(path), data)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(data, file)

    async def verify(self, browser):
        # A real page uses exactly the browser's proxy, including CONNECT authentication.
        probe = await browser.context.new_page()
        try:
            response = await probe.goto("https://ipv4.webshare.io/", timeout=20000)
            if not response or not response.ok:
                raise ValueError("Proxy check failed")
            observed = str(ipaddress.IPv4Address((await response.text()).strip()))
            if self.exit_ip and observed != self.exit_ip:
                raise DomainError(
                    "Blinkit's proxy IP changed or its sticky session expired. "
                    "Reconnect Blinkit to start a new journey; this journey was stopped.", 503
                )
            self.exit_ip = observed
            self.save(browser)
        finally:
            await probe.close()


def configure_blinkit_proxy(browser, settings):
    username = settings.blinkit_proxy_username
    password = settings.blinkit_proxy_password.get_secret_value()
    if not username and not password:
        return
    if not username or not password:
        raise DomainError("Both Blinkit proxy username and password must be configured.", 503)
    # Accept the supplied rotating username, but never rotate within a journey.
    username = username.removesuffix("-in-rotate")
    if not username or "-" in username:
        raise DomainError("Use the base Webshare username or its -in-rotate form.", 503)
    browser.proxy = BlinkitProxy(username, password)
