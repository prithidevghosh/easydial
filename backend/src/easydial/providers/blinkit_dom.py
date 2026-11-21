"""Visible DOM readers; no private API interception or hidden application state."""

QUANTITY = r"""(card) => {
    if ([...card.querySelectorAll('*')].some(e => e.children.length === 0 && e.textContent.trim() === 'ADD')) return 0;
    // Cart drawer uses styled components and a direct text node for quantity.
    const stepper = card.querySelector("div[class*='AddToCart__UpdatedButtonContainer']");
    if (stepper) {
        const direct = [...stepper.childNodes].filter(n => n.nodeType === Node.TEXT_NODE)
            .map(n => n.textContent.trim()).filter(Boolean);
        if (direct.length === 1 && /^\d{1,3}$/.test(direct[0])) return Number(direct[0]);
    }
    const plus = card.querySelector('.icon-plus');
    if (!plus) return null;
    let node = plus.parentElement;
    for (let i = 0; node && i < 5; i++, node = node.parentElement) {
        if (!node.querySelector('.icon-minus')) continue;
        const text = node.innerText.replace(/[+−–-]/g, '').trim();
        if (/^\d{1,3}$/.test(text)) return Number(text);
    }
    return null;
}"""

CARD = r"""(card) => {
    const prices = [...card.querySelectorAll('*')].filter(e => {
        if (e.children.length || !/^₹\s*[\d,.]+$/.test(e.innerText?.trim() || '')) return false;
        let n = e;
        while (n && n !== card) {
            if (getComputedStyle(n).textDecorationLine.includes('line-through') || n.tagName === 'DEL' || n.tagName === 'S') return false;
            n = n.parentElement;
        }
        return true;
    }).map(e => e.innerText.trim());
    return {image: card.querySelector('img')?.currentSrc || card.querySelector('img')?.src, id: card.id, name: card.querySelector("div[class*='line-clamp-2']")?.innerText,
        pack: card.querySelector("div[class*='line-clamp-1']")?.innerText,
        text: card.innerText, current_prices: [...new Set(prices)]};
}"""

CART = r"""(drawer) => {
    const quantity = QUANTITY_FUNCTION;
    const rows = [...drawer.querySelectorAll("div[class*='DefaultProductCard__Container']")];
    return {
        text: drawer.innerText,
        items: rows.map(row => ({
            name: row.querySelector("div[class*='ProductTitle']")?.innerText,
            pack: row.querySelector("div[class*='ProductVariant']")?.innerText,
            quantity: quantity(row)
        })),
        bill: [...drawer.querySelectorAll("div[class*='BillCard__BillItemContainer']")].map(row => {
            const parts = row.innerText.split('\n').map(x => x.trim()).filter(Boolean);
            return parts.length >= 2 ? {label: parts[0], value: parts[parts.length - 1]} : null;
        }).filter(Boolean),
        address: drawer.querySelector("div[class*='ListStrip__SubHeading']")?.innerText,
        total: drawer.querySelector("div[class*='CheckoutStrip__NetPriceText']")?.innerText
    };
}""".replace("QUANTITY_FUNCTION", QUANTITY)
