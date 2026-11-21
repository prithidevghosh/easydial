# Goals and scope

Updated: 2026-11-21.

## User problem

Older adults and people unfamiliar with apps may prefer calling a local shop over navigating quick-commerce interfaces. EasyDial should let someone describe groceries naturally, understand the request, hear useful choices, revise the basket, and explicitly confirm an order.

This is a product hypothesis supplied by the founder, not a finding from user interviews. Validate with actual intended users; preference for local shops may also reflect trust, credit, delivery relationships, or fees rather than interface friction alone.

## Requirements supplied by the user

- A dialer-style app with voice input initially; telephone calling later.
- An OpenAI master agent understands Indian-language requests.
- Separate Blinkit and Swiggy Instamart agents check products, availability, and prices.
- The user chooses a provider and controls checkout.
- Support additions, removals, replacements, quantity changes, and comparison questions before checkout.
- Research feasibility and hackathon themes before building.
- Keep separate Markdown records for goals, findings, decisions, and progress so another agent can continue.

## Final product target

Native iOS dictation → understood and editable basket → real Blinkit/Instamart checkout → explicitly approved payment → provider-confirmed paid order. Both provider workers now use Playwright. Payment and order confirmation require a later authorized test; this session must not place any order.

## Proposed first demonstrable journey

An onboarded user speaks a short grocery request, hears a clarification if needed, receives matching products and costs, changes one item, hears the final payable amount and address, and confirms. The system distinguishes pending payment from confirmed order placement.

Recommended initial scope: one serviceable delivery address, one user session, Hindi/Hinglish plus one regional language chosen with a real participant, a small basket, and Instamart first. These are recommendations, not accepted scope reductions. Keep both provider roles in the design, but never present a simulated provider as live.

## Deferred

Telephone numbers/PSTN integration; universal language guarantees; unattended purchasing; multi-household deployment; automatic substitutions; cross-platform split purchasing; post-order edits/cancellation; production rollout.

## Success criteria

1. A target user can complete the journey with little or no typing after onboarding.
2. The correct product, brand constraint, variant, pack size, and quantity survive spoken corrections.
3. Cost and availability claims are traceable to current provider responses.
4. Checkout requires approval of the current basket, address, provider, total, and payment method.
5. The same recordings demonstrate measurable improvement over a defined baseline.
6. Failures are surfaced honestly; unknown stock is not called out-of-stock, and pending payment is not called an order.
