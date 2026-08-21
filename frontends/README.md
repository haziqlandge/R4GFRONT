# frontends/ — the site

This directory **is** the Shruti website. `index.html` at its root is the demo,
`docs.html` is the documentation page, and both are served statically on port
3000.

It replaced `apps/web`, the Next.js surface built in Phase 8, which has been
removed from the repo. See the 20 August entry in `Memory.md` for what that
change cost and what it bought.

---

## Run it

```bash
frontends\serve.bat
```

That brings up everything and opens the site:

| Port | Process | Notes |
|---|---|---|
| 8000 | `rag_core` | Loads a 655 MB index, allow about 12 seconds |
| 8001 | `stt_gateway` | Holds the Sarvam key, never the browser |
| 3000 | `python -m http.server` | Serves this directory |

Already-running services are detected and left alone, so re-running the script
never costs you a warm index. `run-dev.bat` in the repo root starts the same
three processes without opening a browser.

There is **no build step and no `node_modules`**. The page is HTML, one
stylesheet and ES modules; a change is visible on reload.

---

## Port 3000 is not a preference

`services/stt_gateway/config.py` allows CORS from `localhost:3000` and
`127.0.0.1:3000` and nothing else, because that service holds the Sarvam API key
and a wildcard origin on a credential-holding service is not acceptable
(`Rules.md` section 4 is HARD about this). `rag_core` holds no key and is
permissive, which is why the symptom is confusing:

**Serve this page on any other port and typing works while speaking fails**,
with a CORS rejection that reads like a broken microphone.

Two related traps:

- **The microphone needs a secure origin.** `localhost` counts as one. A LAN
  address like `192.168.1.20:3000` does not, and the mic will silently never
  prompt. Always open `localhost`.
- **Opening the HTML files directly does not work.** `file://` cannot load ES
  modules from `/_shared/`, and the audio worklet path resolves from the server
  root. Use the script.

---

## Layout

```
frontends/
├── serve.bat            brings up all three processes and opens the site
├── index.html           the demo
├── docs.html            the documentation page, a thin shell over renderDocs()
├── theme.css            every visual decision: palette, type, layout, motion
├── console.js           the on-page console panel
├── _shared/             behaviour and data
│   ├── data.js          every published number, with its source file named
│   ├── core.js          service clients, microphone, session analytics
│   ├── ui.js            answer, waterfall and analytics renderers
│   ├── docs.js          the documentation page renderer
│   ├── app.js           the controller that binds the markup
│   ├── base.css         structure only, driven entirely by CSS custom properties
│   └── pcm-worklet.js   48 kHz to 16 kHz PCM16, low pass then resample
└── _backup/
    └── 03-terminal-v1/  the first version of this design, self contained
```

### Why `_shared/` is still a separate layer

It was shared across eight interface treatments, seven of which have been
removed. Keeping the split is still worth it for a smaller reason: `base.css`
sets no colour, no font and no border of its own, it reads a token contract that
`theme.css` defines. That line between what the interface *does* and what it
*looks like* is what made the visual pass on this design cheap, and collapsing
the two files now would only make the next one expensive.

`_shared/data.js` holds every published figure once, and each block names the
dated file under `bench/results/` it came from. A judge who finds two different
P50s in the same submission stops trusting every other number on the page.

`_backup/03-terminal-v1/` is the first version of this design. It is self
contained and still runs, so rolling back is a copy.

---

## The design

A session log. Amber on black, monospace throughout, lowercase, regions framed
like a text user interface with the title sitting on the top rule. Read top to
bottom: input, output, then timing and analytics side by side with a console
panel filling the space under the timing. Dark only, one accent.

---

## Controls

### Keyboard

| Key | Does |
|---|---|
| `Space` | Start and stop recording. Ignored while you are typing in a text field. |
| `Ctrl` + `.` | Show or hide the text input. |

### The console panel

Under the timing panel, collapsed until you click it. It is styling, not a
shell: nothing runs on your machine, and every figure it prints comes from the
same data module the documentation page uses.

`help` lists everything. `status` polls both services live, `session` prints
the percentiles for the queries you have run, `budget`, `corpus`, `chunking`,
`rerank`, `guard`, `stack` and `speech` print the measured results. Arrow keys
walk your history. There are a few things in there worth finding on your own.

Opening and closing it:

| Action | What happens |
|---|---|
| Click the strip | Opens. The strip itself is removed, so there is exactly one `ok4t\ragfront>` on screen in either state. |
| Click anywhere else on the page | Nothing closes. The caret stops blinking, and the session stays exactly where it was. |
| `exit`, or `Esc` | Clears the pane, the input and the command history, then closes. |

Once it is open it stays open. A pane that folded itself away because you
clicked the answer above it took the session with it, and losing what you had
just read is a worse outcome than a pane left open, so closing is something you
say rather than something that happens to you. Both ways of saying it clear the
session on the way out.

There is no row of command buttons. `help` is the discovery mechanism a terminal
already has, and eight chips offering a subset of the commands are a second,
worse copy of it.

The caret is drawn rather than inherited. The browser's own is painted out and
a `_` is positioned at the exact pixel the next character will occupy, measured
against a hidden mirror of the input, so text starts where the caret is instead
of after it. It blinks only while the console has focus, and there is no caret
at all when it does not, which is what an unfocused terminal window does. Past
the width of the field the measurement stops being true, so the browser's caret
comes back for the rest of that line.

### Turning the text input on

**It is OFF by default**, so the page opens voice first and the microphone is
the obvious thing to reach for. That is a presentation choice, not a feature
flag: the box stays in the DOM and stays wired, so switching it back on costs
nothing and loses no state. It has to stay one action away, because a judge
without a microphone still has to be able to try the system.

When it is on, submitting empties it. The question is echoed into the transcript
line above the box, so nothing is lost and the next question can be typed
without clearing the previous one by hand.

| Where | How |
|---|---|
| Keyboard | `Ctrl` + `.` |
| Browser devtools | `shruti.chat.off()` and `shruti.chat.on()` |
| The console panel | `off chat` and `on chat` |

The devtools form needs the dot, because `off chat` on its own is not valid
JavaScript. The console panel accepts the spaced form, and either word order.
The confirmation line is printed by the `shruti:chat` listener alone, so every
route into that switch reports itself exactly once.

---

## What is on the demo page

- Microphone capture with an amplitude reactive ring, and a text box on the same
  endpoint for anyone without a mic, off by default and one keystroke away
- The transcript appears whole when you stop speaking. A word-by-word live
  version is **built and switched off** (`LIVE_TRANSCRIPT` in `_shared/app.js`),
  because Sarvam streams romanised partials unless the language is pinned and
  pinning corrupts the other language's final. The measurement is in that file
  and in `scripts/08c_probe_hindi_partials.py`
- Four sample questions, two English and two Hindi, every one of them run
  through the real pipeline and the answer read before it went on the page.
  `SAMPLE_QUERIES` in `_shared/core.js` records the ones that were rejected, and
  they were rejected for answering WRONGLY rather than for abstaining - which is
  `ISSUES.md` I26 showing up in the demo rather than in the eval.
  These used to be five, two of which the corpus deliberately cannot answer, so
  that the refusal was one click away. That was changed on request; the refusal
  is still one typed question away, since any gibberish triggers it
- The answer, its path badge, and citations that expand in place
- The abstention panel: the typed reason, the score, and the calibrated floor
  drawn on the same axis
- A per stage latency waterfall scaled to the 200 ms budget rather than to the
  total, so headroom stays visible
- Session analytics: rolling P50, P70, P90 and P100, path distribution, per
  stage medians, a sparkline against the budget line, and a JSON export
- The published 250 query figures alongside the live ones
- The measurement boundary stated on screen, with speech to text on its own line

### On the analytics panel

Requirement 4 asks for percentiles across a reasonable number of queries rather
than one best case run. The submitted figures come from the offline 250 query
benchmark. The panel is the live counterpart, so a judge can watch a
distribution build up instead of taking a table on trust.

Two honesty rules are enforced in code:

1. `n` is always shown next to the percentiles, so a P100 over four runs is
   never presented as a tail measurement. The panel used to spell that out in a
   sentence below the grid as well; the sentence was dropped on 21 Aug and the
   `n` label is now carrying it alone.
2. Band A and Band B samples are kept in separate series, and the panel shows
   one at a time — the title is a switch, `analytics · model` or
   `analytics · external`. Averaging an in process extractive answer with a Groq
   round trip produces a number that describes neither.

   **Membership is decided by whether the request left the process, not by
   `path`.** Three outcomes call the model and then report a path that is not
   `GENERATIVE`: the model reporting insufficient context, the call failing, and
   the output guard rejecting the answer. `rag_core` stamps `called the model`
   onto the `answer_generative` span and `Analytics.usedNetwork()` reads it. This
   was a live defect until 21 Aug — one Hindi question routed to the model pinned
   "Band A P100" above 500 ms for the rest of the session.

The timing panel switches the same way, `timing · model` or `timing · external`,
and for the same reason: a routed query used to draw a 551 ms `answer_generative`
bar inside a panel captioned "pipeline is the 200 ms claim".

**The aside is not in either series.** `accurate` mode draws a second panel below
the answer, headed "external source · not from corpus", holding the same question
answered by a model with no retrieval behind it. It comes from `/v1/aside`, a
separate endpoint requested only after our answer has painted, and `record()`
never sees it. Folding an external model's round trip into percentiles that
describe this pipeline would make them describe neither.

Its footer names the model — `openai/gpt-oss-20b` — and that is load bearing
rather than decorative: this is the one panel on the page with no citation and no
grounding check behind it, so an unattributed one would be the only unlabelled
claim on a site whose pitch is that every figure names its source. `aside()` in
`core.js` returns `{ text, model }` for exactly that reason.

Two things the page is quiet about and should not start claiming. The panel
answers from training-time memory, so it is **not** a source of current facts — a
search-grounded upstream was built and removed on 21 Aug (`Memory.md` R5). And it
is rate limited to five calls per client per minute, because it spends the same
Groq window as the generative fallback; a throttled visitor simply sees no panel,
which is the same thing a dead upstream produces and needs no separate message.
`ISSUES.md` I34 and I35.

---

## What is on the documentation page

The six technical requirements in the order the brief states them, each with
what was asked, what was built and the measurement behind it. Then the three
latency bands with the boundary for each, a pipeline diagram showing where the
200 ms clock starts and stops, the chunking comparison, the reranker measurement
that changed the architecture, the guardrail calibration, the harness, speech to
text, a nine phase timeline, and the stack with what was rejected and why.

It includes the corrections. The reranker our own rules named as the default
turned out to be actively harmful in Hindi. The assumption that 20 candidates
could be reranked in 45 ms was wrong by a factor of five. The abstention floor
detects off topic input well and ungrounded answers poorly, and the page says so
with the number attached.

The section bar is sticky. Which section is active is computed from position —
the last section whose top has passed the reading line — rather than from an
IntersectionObserver: at a boundary two sections intersect at once, the earlier
one wins, and that left the *previous* entry highlighted every time you clicked
a link. Below 760px the link list is replaced by the brand, a dropdown naming
the section you are in, and a `>run` button, all three appearing only once the
bar is actually stuck.

---

## A note on the answer text

MSMARCO-XI passages are stitched together from several source sentences and the
join often lost its whitespace, so the raw text really does read
`corporate.A group of people` and `owns itsA CORPORATION`. That is in the
corpus, not a rendering bug.

The interface puts the spaces back before displaying it. It inserts whitespace
and changes nothing else: no word is added, removed, reordered or altered, so
the quoted answer is still the passage verbatim in every sense that matters.
The rule that splits a lowercase run from a following capital needs three
lowercase letters first, which is what leaves iPhone, eBay and McDonald intact.

The answer is then shown in two weights. Whole sentences from the start, until
there is enough to be worth reading, are set large; the rest of the passage is
set small underneath. Nothing is hidden or truncated, it is only weighted,
because MS MARCO passages often open with a fragment like "Also called body
corporate." that is a true first sentence and a useless headline.

---

## Not shown yet: groundedness

Phase 6 added an output guard, and `Confidence.groundedness` is now populated on
every response. An extractive answer reports **1.0**, which is the extractive
path's structural guarantee expressed as a number rather than asserted in prose.

**The interface does not render it.** The field is in the API and in
`_shared/data.js`'s vocabulary but no panel shows it. It would be one row in the
answer side panel, next to confidence and margin.

Whether to build that or spend the remaining time on deployment is written up as
decision **D-B** in `DONT-FORGET.md` 12, along with the argument for each.

---

## Verified

Against both live services on 20 August 2026, served from this directory root:

- English query: `EXTRACTIVE`, 3 citations, 54.6 ms Band A
- Hindi query: `EXTRACTIVE`, Devanagari answer and citations, 141.0 ms Band A
- Gibberish: `ABSTAINED` with `LOW_CONFIDENCE`, top score -4.908 against the
  -1.103 floor, which matches the calibrated value exactly
- Zero console errors
- No horizontal page overflow at 375, 768 and 1280; the wide tables scroll
  inside their own wrapper instead of stretching the page
- Session analytics, path distribution and per stage medians all populate
- Pipeline diagram reflows from rows to a single column under 720px
- All nine documentation sections highlight their own entry in the section bar,
  at the top and the bottom of the page as well as in the middle
- `Space`, `Ctrl` + `.`, `off chat` and `shruti.chat.off()` all verified, and
  `Space` correctly does nothing while a text field has focus

**The microphone path is now verified against a real microphone**, 20 Aug 2026,
which is the gap Phase 4 and Phase 8 both left open. Two English queries spoken
into a browser and answered end to end: speech 1016 ms / pipeline 65.2 ms, and
speech 705 ms / pipeline 68.2 ms, both `EXTRACTIVE` with three citations. The
second returned a Hindi passage at rank 2 beside its English twin at rank 1, so
cross-lingual retrieval fired on live spoken input rather than on a constructed
example. Two samples is a sighting rather than a distribution, and the
documentation page says so where it prints them.
