# Master-agent evaluation and dictation decision

2026-11-21. Scope: WhatsApp Hindi/Hinglish grocery interpretation. No store actions or messages were sent by the live-model evaluator.

## Incident and diagnosis

The user requested “i said yeh sab kuch nhi lena, thumsp kr do ek bass”. The locally retained transcription preserved rejection plus the request for one Thums Up. The original response stopped at an empty-basket message. This is evidence of an intent/orchestration failure; changing speech providers alone cannot fix it. Earlier curd transcription also showed a packet/ticket confusion, so transcription still deserves its own evaluation.

The old instruction told the model to clarify mixed edits/discovery, and the executor treated search and edit as disjoint operations. Existing automated tests largely injected mocked ChatIntent outputs; they verified execution but did not establish live language understanding. Passing those tests was insufficient evidence of a reliable conversational agent.

## Changes

- Search can carry explicit draft edits plus product queries. Every edit target is validated; discovery completes before draft edits apply. Product selection remains a separate exact-variant choice.
- “Reject everything and find X” removes only explicitly rejected existing draft lines and searches X. With an empty draft it only searches. Rejecting suggested options alone does not remove existing items.
- Additive/negated-removal requests preserve existing lines. Specific replacements retain their existing replacement semantics.
- Invalid/inconsistent intents get one bounded reinterpretation, then clarification. Unknown targets, duplicate edit targets and remove/replace conflicts are rejected before mutation.
- Compound draft changes are blocked while an unresolved payment request exists. No new payment/order authority is introduced.
- Empty drafts no longer offer a review-basket button.
- Master model changed from gpt-4.1-mini to gpt-4.1 in settings defaults and the private runtime configuration. Speech remains gpt-4o-transcribe; no Sarvam integration or claim of measured Sarvam superiority.

## Live text eval

Runner: `backend/tools/eval_chat_intents.py`. It calls the real configured OpenAI model against synthetic baskets and displayed choices; it never invokes provider workers. It covers the reported correction with empty/full baskets, Devanagari, new curd requests, explicit removal plus discovery, additions, negation, quantity correction, comparison without ordering, option rejection, ambiguous yes/units, replacements, stop and no automatic payment. It scores structured actions, products, quantities, edit targets/kinds, replacements and unintended selection.

| Configuration | Passing cases | Median interpreter latency |
| --- | --- | --- |
| Previous prompt/schema behavior, gpt-4.1-mini, 16 cases once | 9/16 | 1.08 s |
| Revised behavior, gpt-4.1-mini, 16 cases twice | 30/32 | 1.13 s |
| Revised behavior, gpt-4.1, 16 cases twice | 32/32 | 0.93 s |

Raw synthetic outputs are in `docs/evals/`. These timings are small local samples, not production latency guarantees or a cost comparison. During evaluation, comparison-only quantity scoring was corrected to allow the existing cart quantity; this does not affect the reported initial 9/16 score. The runner currently scores intent, not provider catalogue coverage or delivery of WhatsApp media.

The revised mini still selected a displayed curd variant for a generic curd request instead of presenting a fresh search. The larger model passed that regression in both runs. Separately, both locally retained Thums Up transcript variants were tested against empty and full synthetic baskets with the revised mini; all four preserved the requested drink and count. No original audio was available for replay.

This is a development set used to improve the prompt, not an independent held-out benchmark. 32/32 does not establish general reliability. Extend it with unseen formulations and actual recordings across languages; do not tune only to the reported phrase. Add every future regression before changing the prompt/model.

Reproduce (uses configured OpenAI credentials and incurs API usage):

```sh
uv --directory backend run python tools/eval_chat_intents.py --model gpt-4.1 --repeats 2 --output .state/intent-eval.json
```

The command exits nonzero if any case fails. Deterministic execution regressions in `test_compound_intents.py` cover completing edits plus discovery, no partial draft mutation on raised search failure, and blocking compound changes after payment preparation. Standard suite: 74 passed, 7 DOM tests skipped; no browser DOM changes in this patch.

## Dictation: OpenAI versus Sarvam

Sarvam's official documentation describes Indian-language/code-mixed transcription and native-script, romanized and verbatim output modes. It is a relevant candidate, not a demonstrated improvement on our recordings. Its documented synchronous REST duration limit is 30 seconds, whereas EasyDial currently accepts up to 60 seconds; an integration needs an appropriate longer-audio route rather than silent truncation.

Sources inspected 2026-11-21:
- https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/overview
- https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/rest-api
- https://developers.openai.com/api/docs/guides/speech-to-text

Before switching, create a consented, privately stored audio evaluation set (at least 30–50 real voice notes): Hindi/Hinglish first, then the intended launch languages, accents and background noise. Include brands, package sizes, corrections, negation, keep/remove distinctions and multiple clauses. Send the same recordings through both providers, then the same fixed master interpreter. Measure product/brand accuracy, counts/units, negation retention, intended final basket, unwanted removals, latency and cost. WER alone is insufficient: dropping “nahi” is more important than harmless spelling differences.

A Sarvam API key and the same test recordings are required for a real comparison. Keep both local/private; do not paste credentials into chat or commit recordings. No comparison has yet been run, and speech has not been switched. Source transcripts alone cannot establish which recognizer is better.
