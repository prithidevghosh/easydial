# Theme alignment and evaluation plan

Updated: 2026-11-21. Theme wording is from the user's brief; no organizer URL or complete eligibility rules were supplied. This is an assessment of fit, not organizer approval.

## Fit

| Theme | Assessment | Evidence EasyDial should show |
| --- | --- | --- |
| Build Creative: personal context into everyday AI | Strong fit | A real user's difficulty, preferences/address, spoken request, correction, useful shopping outcome |
| Build Challenging: Voice AI for India | Strongest technical fit, conditional on measurement | Identical recordings through a base system and EasyDial; fewer entity/quantity/correction errors |
| Build Ambitious: SaaS in a day | Weak fit as currently framed | The current idea is a consumer assistant; no reference SaaS or SaaS innovation has been specified |

Recommendation: choose **Build Challenging** if the team can produce a credible paired evaluation. Keep the real-user journey as the product narrative. If measurement is infeasible in the available time, Creative is the more defensible entry. Do not reframe the project as SaaS just to fit a third theme.

Suggested pitch: “EasyDial helps people order groceries in the language they speak, and understands when they change their mind.” The contribution should be correct transactions from imperfect speech, not simply connecting speech recognition to commerce tools.

## Evaluation design — proposed, not executed

Use a small consented dataset from intended users or representative native speakers. Minimum pilot: 60 clips across Hindi/Hinglish and one selected regional language, with at least three speakers per language group where practical. Collect a separate development set; hold out speakers/recordings for final reporting. This is a hackathon pilot, not evidence of universal reliability.

Label transcript, requested items, brand constraints, pack size, quantity/unit, intent, negation, correction, and expected basket after each turn. Native speakers should verify labels. Include quiet/noisy recordings, slow speech, pauses, regional names, code-switching and multi-turn changes.

### Paired comparisons

1. **Baseline A:** fixed speech model without domain hints → fixed generic extraction prompt → synthetic catalogue.
2. **System B:** same audio/model → vocabulary hints, regional-unit normalization, dialogue state, clarification and basket validation → same catalogue.
3. **Optional C:** alternative speech provider with the same downstream system, reported separately as a provider comparison.

Do not change the base model and all downstream logic simultaneously then attribute the gain solely to the harness. Save exact model identifiers, prompts, settings and input hashes. Evaluate voice understanding against a synthetic catalogue, separate from live Swiggy/Blinkit data and their access conditions.

### Metrics

| Metric | Definition |
| --- | --- |
| Item/brand/variant accuracy | Correct labeled fields divided by applicable fields; report per field |
| Quantity/unit accuracy | Correct normalized quantity-and-unit pairs divided by labeled pairs |
| Basket exact match | Turns whose entire resulting basket equals the labeled basket |
| Correction success | Correction turns producing the intended replacement/removal/quantity update |
| Clarification burden | Number of clarification turns per completed task |
| Task completion | Journeys completed correctly, with failures and exclusions included |
| Confirmation safety | Attempts to purchase without valid approval of the latest basket; target zero |
| Latency | End of speech to acknowledgement and to grounded result, median/p95 separately |
| WER/CER | Secondary transcription metric with a documented script/tokenization convention |

Ambiguous examples must permit an appropriate clarification as success; guessing is not success. Multi-turn evaluations must give both systems equivalent follow-up responses. Report first-pass accuracy separately from accuracy after clarification. Report counts and per-language results; do not hide a weak language behind an average.

Provisional targets to discuss after a pilot: at least 20% relative reduction in basket errors versus baseline, at least 90% quantity/unit accuracy in the bounded test set, and zero unauthorized checkout attempts. These are goals, **not results**. If errors fall from 20/60 to 12/60, report 40% relative error reduction and 13.3 percentage-point accuracy gain; do not confuse the two.

## Demo sequence

1. Briefly show a real user's original frustration and onboarding needs.
2. Play an identical held-out recording through baseline and EasyDial.
3. Show the extracted basket and the specific corrected error.
4. Run an attributed live provider search where access permits.
5. User says a replacement or quantity correction; system updates and reads it back.
6. Show the final summary and user confirmation; distinguish checkout-ready, payment-pending and placed states.
7. Show aggregate evaluation, including failures.

Do not call a checkout preview a completed order. If live purchase testing is unavailable, label the boundary honestly. A prerecorded successful journey is supporting evidence, not proof that a broken live integration works.

## User validation still needed

Recruit at least one actual target user. Observe ordering the same small basket with their usual method and EasyDial. Measure completion, help required, mistakes and perceived trust. Test whether one-time account/address setup can be caregiver-assisted without forcing every subsequent order through a caregiver. Do not assume age alone determines technical ability.
