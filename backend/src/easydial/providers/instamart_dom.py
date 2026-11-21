"""Selectors observed on instamart.in on 2026-11-21, not the legacy Swiggy route."""

CARD = r"""(link) => {
    const card = link.parentElement;
    const pack = card.querySelector('._3wq_F')?.innerText?.trim();
    const price = card.querySelector('._2jn41')?.innerText?.trim();
    return {path: link.getAttribute('href'), name: link.querySelector('img')?.alt,
        pack, price, text: card.innerText,
        add: !!link.querySelector('[data-testid="buttonpair-add"]')};
}"""

CART = r"""(body) => {
    const text = (root, test) => root.querySelector(`[data-testid="${test}"]`)?.innerText?.trim();
    const rows = [...body.querySelectorAll('[data-testid="item-list-available-items-container"] [role="listitem"]')];
    const currentMoney = row => [...row.querySelectorAll('[data-testid="billline-segment-text"]')]
        .filter(e => /^(₹|FREE$|Free$)/.test(e.innerText.trim()) && !getComputedStyle(e).textDecorationLine.includes('line-through'))
        .map(e => e.innerText.trim());
    return {text: body.innerText,
        items: rows.map(row => ({name: text(row, 'cart-item-name'), pack: text(row, 'cart-item-quantity'),
            quantity: /^\d+$/.test(text(row, 'add_buttons_center') || '') ? Number(text(row, 'add_buttons_center')) : null})),
        bill: [...body.querySelectorAll('._2vw3P')].map(row => {
            const prices = currentMoney(row);
            return {label: text(row, 'billline-segment-text'), value: prices.length === 1 ? prices[0] : null};
        }).filter(row => row.label && row.value),
        address: text(body, 'address-line'), total: text(body, 'CART_FOOTER_BILL_TOTAL')};
}"""
