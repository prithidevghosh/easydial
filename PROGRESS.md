
## Explicit payment retry after three empty history checks
- Fixed the active-attempt guard that kept routing retry requests back to status even after three clean history reads found no matching order.
- The stopped-check result now offers Retry payment and Check status. Requests to proceed/retry payment open a short confirmation explaining possible late confirmation and duplicate risk.
- Confirming retry archives the old monitor, COD/UPI request and automatic-check records transactionally, then releases the active attempt. Multiuser archives use the existing encryption key. Old EasyDial QR endpoints stop serving the archived request.
- The basket stays intact; the ordinary checkout path revalidates its bill and opens payment methods (or shows a changed bill). A retry does not itself submit an order. COD still requires Place order.
- Confirmation buttons are bound to basket version, draft and an expiring single-use nonce. Pending/confirmed attempts and other customers cannot be released through this operation.
- Natural-language instructions distinguish retry/proceed requests from claims of completed payment. This fixes recovery state handling; it does not claim the original merchant submission failure is diagnosed or that absence proves failure.
- Validation: regression coverage for archived attempts, checkout recovery, replay rejection, pending/customer isolation and actionable three-check results. No real payment or order submitted during testing.
