"""Conservative catalogue matching: recommendation cards are not search matches."""

import re


def matches_query(query, name, pack_size=None):
    aliases = {"dahi": "curd", "cokes": "coke", "popcorns": "popcorn",
               "litre": "l", "litres": "l", "liter": "l", "liters": "l",
               "ltr": "l",
               "cookies": "cookie", "biscuits": "biscuit", "chips": "chip",
               "eggs": "egg", "apples": "apple", "bananas": "banana",
               "oranges": "orange", "tomatoes": "tomato", "potatoes": "potato",
               "onions": "onion", "chocolates": "chocolate", "bottles": "bottle",
               "cans": "can", "nuts": "nut", "peanuts": "peanut"}
    stop = {"a", "an", "the", "of", "pack", "packs", "packet", "packets"}

    def tokens(value):
        return {aliases.get(t, t) for t in re.findall(r"[a-z]+|\d+(?:\.\d+)?", value.casefold())}

    wanted = tokens(query) - stop
    actual = tokens(name + " " + (pack_size or ""))
    if wanted == {"milk"} and actual & {"biscuit", "biscuits", "cookie", "cookies", "soap", "chocolate"}:
        return False
    return bool(wanted) and wanted <= actual
