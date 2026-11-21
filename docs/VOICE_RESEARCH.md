# Voice input research

Updated: 2026-11-21. No recordings transcribed and no accuracy, latency, or cost benchmark executed. Provider capabilities below are documented claims; recommendations remain provisional.

## Recommendation

Capture audio directly in EasyDial and use an API-accessible speech model. Keep the OpenAI master agent independent of the speech provider. Compare OpenAI transcription against Sarvam on the same recordings before choosing. Start with tap-to-start/tap-to-stop recording; add automatic turn detection after the core journey works.

“Any Indian language” is a long-term goal. A language listed by a provider is not proof of reliable ordering for every accent, dialect, noise condition, or code-switch. Publish a tested language matrix instead.

## Can we use Wispr Flow?

Yes, as a user-installed dictation utility that enters text into a field, potentially useful for an early manual demo. **An embeddable live transcription API/SDK was not verified in the official pages reviewed.** The “Flow for Developers” page describes dictation for programmers, not a public speech SDK. Do not design around undocumented endpoints or assume a consumer subscription grants embedding rights. [Developer page](https://wisprflow.ai/developers).

Its help documentation says language detection operates per dictation. Hinglish must be explicitly selected, and languages such as Tamil and Bengali need explicit selection rather than dependable automatic identification. These limitations conflict with a frictionless “speak any language” experience. [Language behaviour](https://docs.wisprflow.ai/articles/3191899797).

Wispr's documented remote MCP reads Notetaker meeting content; it does not provide live audio ingestion or dictation-history access. It is not the speech-input component EasyDial needs. [Wispr MCP scope](https://docs.wisprflow.ai/articles/9551372685-connect-an-mcp-client-to-wispr-flow-remote-mcp-server).

## Options to evaluate

| Option | Evidence | Decision implication |
| --- | --- | --- |
| OpenAI transcription | Current `gpt-transcribe` documentation supports file audio, committed realtime turns, context, keyword and language hints | Candidate for the simplest OpenAI-based stack; benchmark grocery entities |
| Sarvam Saaras | Current `saaras:v4` documentation lists 22 Indian languages plus English, automatic detection and code-mixed output | Strong candidate for broad Indic input; performance still needs measurement |
| OpenAI realtime voice | Semantic VAD can wait based on whether speech appears complete | Useful later for pauses and natural turn-taking; not proof of accent accuracy |
| Wispr Flow | Consumer dictation; embedded speech API unverified | Optional manual input method, not the core dependency |

OpenAI documents contextual and keyword hints, useful for brands and regional product vocabulary. Account access must be checked before pinning a model. Keep baseline and enhanced-system model versions fixed during evaluation. [OpenAI model](https://developers.openai.com/api/docs/models/gpt-transcribe), [file transcription guide](https://developers.openai.com/api/docs/guides/speech-to-text).

Sarvam documents `transcribe`, `translate`, `verbatim`, `translit`, and `codemix` modes, plus keyterm prompting in v4. REST clips are limited to 30 seconds; longer/live input needs an appropriate streaming or batch path. Language-detection confidence is not confidence that a quantity or brand was understood. [Saaras model documentation](https://docs.sarvam.ai/api/api-guides-tutorials/models/saaras).

For OpenAI voice sessions, semantic VAD may reduce interruptions during hesitant speech. It can trade latency for waiting. Support depends on the selected session/model; do not assume every transcription endpoint supports it. Begin with explicit recording controls and compare turn-ending approaches separately. [VAD documentation](https://developers.openai.com/api/docs/guides/realtime-vad).

## Speech output matters too

A voice interface must read choices and confirmations back clearly. Evaluate OpenAI speech output and Sarvam Bulbul with target users; no winner is established. The reviewed Bulbul model page lists ten Indian languages plus English, which is narrower than Saaras input coverage. Its model page and high-level model index differ on v4 language counts; validate the chosen language/model directly before promising support. [Bulbul model page](https://docs.sarvam.ai/api/api-guides-tutorials/models/bulbul), [model index](https://docs.sarvam.ai/api/getting-started/models).

Recommended speech UX: short sentences, one clarification at a time, repeat and slower-playback controls, explicit listening/processing states, and a large stop button. Avoid reading ten similar products aloud. Offer two or three well-matched choices and ask before substituting.

## Proposed processing pipeline

Audio → faithful transcript → structured intent → clarification or basket revision → provider lookup → grounded spoken response.

Preserve the original transcript alongside normalized fields. Translation to English can help catalogue search, but must not erase brands, negation, quantities, or corrections. Use regional aliases and known catalogue terms to propose candidates, not to invent what was said.

Examples for evaluation:

- “Do packet doodh… nahi, teen” → three packs, not five.
- “Aadha kilo” → 500 g; “dedh kilo” → 1.5 kg.
- “Amul nahi, Mother Dairy” → replacement of a brand constraint.
- “Cheaper one, but same size” → compare equivalent variants.
- “Bread mat add karna” → no bread mutation.
- “Doodh hatao” → clarification if several milk variants are present.

Code-switching and regional units should be checked by native speakers. Brand/variant uncertainties deserve clarification even if the rest of the transcript looks fluent.

## Cost and latency research boundary

No total project price is estimated: recording lengths, chosen models, concurrency and account limits are unknown. Measure audio minutes, input/output tokens, tool latency and retry counts per completed journey. Model cost alone does not capture repeated browser calls or failed-turn recovery. Voice evaluation should report median and p95 latency, not just the fastest demo.

Telephone integration is deferred. Later testing must include actual narrowband/8 kHz call audio, interruptions, reconnects, identity, onboarding and payment handoff. A microphone demo does not validate telephone performance. UPI QR on the same phone is also a usability obstacle; prefer an available app-intent path when supported.
