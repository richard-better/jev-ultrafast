# Faster on the real web

The current video completes the Google Flights task in **9.195 seconds at 1×**. It starts with one natural-language goal and uses dynamic controls throughout. Jev selects operation + target in one request; GLM 5.3 Flash generates the city strings when TYPE_TEXT is selected.

[Video](demo.mp4) · [Recording measurements](flights-measurement.json) · [Matched run measurements](full-speed-measurement.json)

## Current recording

Three takes were recorded back to back with the same settings: a 1470×1470 viewport, TypeSafe `jev-1.13.0`, and GLM 5.3 Flash through OpenCode Go with reasoning effort `none`. All three passed independent verification, in **8.539, 9.195 and 11.148 s**. The video uses the median take; every take is listed in the measurement JSON.

The recording contains **18 Jev requests**, **10 interactions plus one explicit WAIT**, and **two text calls**. Median Jev latency was **258 ms**, 4.982 s in total. GLM generated **Zurich in 1,462 ms** and **London in 797 ms**. Search executed at **7.406 s**; final verified completion was **9.195 s**. That final interval includes Google's results loading, state changes, and the completion decision. It stays in the video.

Timing begins at the first prediction after initial homepage observation and ends at the accepted DONE choice. It includes text generation, model requests, browser work, stale decisions, and loading. Browser setup, initial navigation, and fresh independent post-run verification are outside the clock. The video contains 109 continuous screencast frames plus the initial screenshot, uses original timestamps, has no opening hold, and adds a 0.5-second final hold. Only the top account/navigation strip is cropped.

The recording reports 104,157 TypeSafe input tokens and 6,474 output tokens across all requests. OpenCode Go is a subscription and reports no per-request charge for the two text calls.

## Direct URL baseline

When the route and date are known up front, the task needs no agent. Google Flights accepts a deep link, `https://www.google.com/travel/flights?hl=en&q=Flights from Zurich to London on 2026-10-29 one way economy 1 adult`, which opens the same search. Three takes on the same browser profile showed verified results **0.667, 0.722 and 0.840 s** after navigation started, with no model calls. The [URL video](url-demo.mp4) uses the median take; [measurements](url-measurement.json) list every take.

The clocks differ: this one starts at navigation and includes loading the page, while the agent's starts after its initial page has loaded. The same content checks pass (one way, Zürich, London, the date and year, and flight results on that date); the path check does not apply, because the deep link keeps its `/travel/flights?q=` address. The agent's value is in goals that need reading and choosing on the page, or sites whose state cannot be put in a URL; this task is a benchmark of driving the page, not of that.

## Cheapest flight to booking options

A task that a URL cannot answer up front: *find the cheapest one-way flight from Zurich to London on October 29, 2026, for one adult in economy, select it, and stop on the page that lists where it can be booked; do not continue to any booking site.* The goal names the outcome, never the controls. Both arms use the same loop, 1470×1470 viewport, TypeSafe `jev-1.13.0` and GLM 5.3 Flash; the only difference is whether the Google Flights [URL map](../examples/maps/google-flights.json) is loaded.

| | Takes (all verified) | Video (median) | Jev requests | Actions | Text calls |
| --- | --- | ---: | ---: | ---: | ---: |
| Without a URL map | 11.104, 11.396, 13.640 s | [11.396 s](cheapest-demo.mp4) | 21 | 14 | 2 |
| With the URL map | 5.523, 6.674, 6.789 s | [6.674 s](cheapest-map-demo.mp4) | 10 | 5 | 1 |

Without the map, Jev fills origin, destination, one way and the date, searches, sorts by cheapest, opens the first flight's details and selects it. With it, Jev chooses the map's one-way entry, the text model fills its parameters (Zurich, London, 2026-10-29, economy, 1 adult), code builds the results URL, and Jev sorts, opens and selects the same way. In the map video Jev first opens one flight's details before sorting, then sorts and opens the cheapest; the detour stays in the video. The first 2.8 s of the map run are the decision to take the shortcut and the text model filling it.

Independent verification checks that the final page is Google's booking page for Zurich to a London airport on the date, one way, with booking options shown; that no "Continue to book" action ran; and that the selected flight is a flight listed at the lowest price, read from a fresh results page in a separate tab after the run. Flights are compared by airports and times, not by price: Google listed the cheapest easyJet fare at HUF 23,279 on the results page and HUF 23,299 on its booking page. Measurements: [without the map](cheapest-measurement.json), [with the map](cheapest-map-measurement.json). The [side-by-side video](cheapest-side-by-side.mp4) starts both median takes together on one clock at 1×; each side stops on its final frame once its run is done.

Earlier benchmark runs of the same task, five per arm interleaved, all passed with medians of 12.3 s without the map and 6.7 s with it; the slowest run in each arm was one 12-15 s text-model request.

The previous video, a 7.073-second run at 1120×780 with Mercury through OpenRouter and a 178 ms median Jev latency, is described in the [earlier performance report](https://github.com/browser-use/jev-ultrafast/blob/1231850a0bf1a0c0341fe408ef1668dbbfdfac46/docs/performance.md). The matched comparison below predates the current recording and keeps its original settings.

## Matched runtime comparison

Six alternating runs, one task, one existing Chrome profile. Both arms used the same natural-language goal, independent result checker, 1120×780 viewport, TypeSafe `jev-1.13.0`, `inception/mercury-2.5`, disabled text reasoning, and action/request budgets. Initial navigation is excluded in both arms. Each run creates and closes its own tab. All six attempts are included; no provider or verification failures occurred.

| Pair | Original runtime | Optimized runtime | Verified |
| --- | ---: | ---: | --- |
| 1 | 11.214 s | 6.964 s | Both |
| 2 | 8.984 s | 7.913 s | Both |
| 3 | 9.450 s | 7.092 s | Both |
| **Median** | **9.450 s** | **7.092 s** | **3/3 each** |

The optimized runtime was faster in all three pairs. Median task time was **25.0% lower**, median TypeSafe requests fell **22 → 17**, and median browser protocol calls fell **1,092 → 101**. Three pairs are too few for a strong statistical claim (two-sided sign-test p = 0.25). This is a small controlled-input comparison, not a broad agent benchmark; Google, network responses, routing, and browser caches remain live.

The original arm is the frozen source from `68c077bf79caca4e817b8e8a5854b2efa0c81ff6`. Both arms use Mercury so the runtime comparison does not conflate a helper-model change with code changes. Per-run source hashes, model settings, token counts, helper costs, browser version, protocol counts, and verification results are in the measurement JSON.

## Where the time went

The original loop invalidated decisions on every DOM mutation, including animations. It also read the accessibility tree repeatedly and resolved hundreds of DOM nodes. The new snapshot reads common HTML/ARIA controls in one browser call. Click guards compare the selected target and nearby context, plus document/form state. Current geometry and hit-testing still run before input.

A brief event-based combobox wait lets suggestions arrive before asking Jev to choose from an incomplete popup. Text comes from an actual LLM. Native text replacement was also fixed to issue the browser's select-all command explicitly.

## Other checks

| Task | Time | Independent result |
| --- | ---: | --- |
| Wikipedia: open Gödel’s incompleteness theorems | 2.798 s | Exact article URL |
| Local hotel fixture: search Lisbon, Design, Free cancellation, open Casa Flora | 1.896 s | Property plus all three applied filters |

These are separate smoke checks, not matched speed comparisons. Local browser checks cover moved/replaced/hidden/disabled controls, field and checkbox properties, changed nearby context, overlay blocking, native-select execution, real text replacement, autocomplete arrival, and navigation. Offline tests cover the model contract, stale retries, interrupted mutations, helper validation, and independent trip verification.

After the timed runs, native-select interruption handling was tightened: uncertain mutation results stop instead of being treated as retryable stale reads. Flights does not exercise native SELECT. The comparison's timing and recording hashes are retained unchanged; the final failure path is covered by offline fault injection and local browser checks.

## Development attempts retained

Before freezing the candidate, the original runtime passed once in 9.302 s. Two accessibility-tree/semantic-guard candidates took 9.395 s and 10.157 s. The first direct-DOM candidate took 8.697 s but failed independent verification because name/value extraction was incomplete. Recursive labels and combobox values fixed that failure; subsequent verified diagnostics took 8.051, 8.631, 8.395, 8.385, and 7.741 s. A Mercury diagnostic passed in 7.559 s. These are changed-code development attempts, not the matched comparison above.

A six-call helper probe used the two real flight-field contexts with Gemini 2.5 Flash Lite, Gemini 3.1 Flash Lite, and Mercury 2.5. All returned the correct values in this tiny probe. Mercury then passed the live Flights, Wikipedia, and local filter checks. This does not establish general semantic accuracy. Earlier probes had rejected a model that swapped origin/destination and another that emitted commentary instead of valid JSON.

The previous 11.387-second recording and post-recording 12.898-second policy regression are described in the [original performance report](https://github.com/browser-use/jev-ultrafast/blob/68c077bf79caca4e817b8e8a5854b2efa0c81ff6/docs/performance.md). The older prepared-step prototype remains in [performance-prepared.md](performance-prepared.md). Raw attempts and original-timestamp frames remain in ignored local artifacts.

## Limits

This DOM reader supports common HTML and ARIA controls; it does not implement the full accessible-name algorithm or traverse shadow roots/frames. Scoped click guards deliberately allow unrelated visible updates. Sampled visible nested scroll regions are supported; canvas, uploads, new tabs, obscured or unsampled scroll containers, and arbitrary keyboard widgets remain unsupported. A valid operation can still be wrong, and DONE is never independent evidence of success.
