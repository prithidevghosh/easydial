"""Only public product-image hosts observed on the store websites."""

from urllib.parse import urlsplit

HOSTS = {"cdn.grofers.com", "instamart-media-assets.swiggy.com", "media-assets.swiggy.com"}


def product_image_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname in HOSTS
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        ):
            return value
    except ValueError:
        pass
    return None
