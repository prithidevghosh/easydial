# iOS design direction

Build date: 2026-11-21. Native SwiftUI, iOS 17+.

## Tokens and intent

- Paper white `#F8FAFD`: clear, neutral background.
- Deep blue `#133A72`: trusted primary action and text emphasis.
- Sky blue `#E8F0FC`: calm voice-control surround and assistant response.
- Ink `#18263D`: readable text.
- Slate `#52647E`: supporting text with adequate contrast.
- Pale blue sample notice: clear disclosure with restrained visual weight.

Use Apple's rounded system face for the app identity and large voice invitation; system text styles for product details, scalable with Dynamic Type. Product prices align with their product, not a floating dashboard statistic. Keep the main voice control central and supporting content left aligned.

Layout: identity/settings → explicit sample notice → conversation invitation → large microphone → concise assistant message → sample shortcuts / text fallback → provider results. After search the voice control becomes compact; equivalent products group provider prices into one card. A persistent bottom action opens a separate basket/review sheet, avoiding a long scroll through results to reach checkout.

The distinctive element is the large familiar call-like voice control. Avoid a decorative dial pad that implies telephone calls work. Keep equal product comparison cards quiet and readable. Use original copy about groceries, corrections and pack sizes rather than generic AI claims.

Review against brief: large controls and short choices support people unfamiliar with apps; no background animation or fake waveforms; no technical configuration in shopping content. Sample-mode disclosure is necessary for honesty, not developer decoration. Use familiar platform controls for accessibility rather than custom gestures. Test long text and compact devices.

The founder explicitly requested premium, soothing aesthetics and frictionless UX. The second pass reduces the sample notice's visual weight, softens headline weight, adds restrained concentric voice-control depth, groups equivalent products, and separates basket review. The custom microphone icon is generated from vector drawing code in `ios/tools/generate-icon.swift`; it uses no external artwork.
