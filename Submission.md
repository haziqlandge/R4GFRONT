# Submission.md

Everything that is scored but is not code. Team OK4T.

**This file exists because teams lose on this, not on engineering.** The promotion requirement is mandatory, per-member, and across three platforms. It is the easiest thing to forget and the easiest thing to fail on.

**There are no resubmissions.** The form is submitted once, and only after every box below is ticked by a second person.

---

## 1. Deliverables

| # | Deliverable | Where | Status |
|---|---|---|---|
| S1 | Submission form completed | https://forms.gle/MNvCjcv23Hn2Eeu58 | ☐ |
| S2 | GitHub repo link, public | | ☐ |
| S3 | Live working link | | ☐ |
| S4 | Video 1, team/process, 90 seconds | | ☐ |
| S5 | Video 2, end-to-end demo | | ☐ |
| S6 | Both videos on Instagram, every member | | ☐ |
| S7 | Both videos on X, every member | | ☐ |
| S8 | Both videos on LinkedIn, every member | | ☐ |
| S9 | `#RAGInGoa` on every post, every platform, every member | | ☐ |
| S10 | At least one Instagram account public | | ☐ |

---

## 2. Video 1: team and process, 90 seconds

**The brief is explicit: this shows how the team is working, process not product.** A product demo here wastes the slot and duplicates Video 2.

Ninety seconds is short. Every shot must earn its place.

| Time | Shot | Purpose |
|---|---|---|
| 0:00 to 0:08 | Team on camera, one line each: name and what they owned | Establishes there is a real team with real division of labour |
| 0:08 to 0:22 | The phase board from `Phases.md`, physical or digital, with phases being ticked | Shows the work was planned, not improvised |
| 0:22 to 0:38 | Screen recording of `scripts/04_bench_latency.py` running and printing percentiles | Shows measurement discipline, which is the thing this task actually rewards |
| 0:38 to 0:52 | The chunking comparison table being generated, eight strategies side by side | Shows requirement 2 was taken seriously |
| 0:52 to 1:06 | A genuine failure moment: a bench run over budget, then the fix, then the run under budget | The most credible 14 seconds in the video. Do not stage this; capture a real one. |
| 1:06 to 1:20 | Git log scrolling, commits tagged by phase, branches merging | Process evidence |
| 1:20 to 1:30 | Team, closing line, `#RAGInGoa` on screen | Required hashtag visible |

**Rules**
- Capture footage *during* Phases 2 through 7, not reconstructed on 22 August. Reconstructed process footage is obvious.
- Record every benchmark run from Phase 2 onward. You will not know which one is the good clip until later.
- Real audio. Subtitles burned in, because most social viewing is muted.

---

## 3. Video 2: end-to-end demo

No stated time limit. Target 2:30 to 3:00. Longer and nobody finishes it.

**Script**

| # | Beat | What is on screen | Why it is here |
|---|---|---|---|
| 1 | Open on the live URL, cold load | The interface, mic orb idle, waterfall empty | Proves it is deployed and real |
| 2 | Speak a question in English | Partials appearing live, orb ring reacting to voice | Requirement 1, visibly |
| 3 | Answer appears | Answer card, citation chips, **waterfall filling stage by stage**, total under 200ms | Requirements 3 and 4, in one shot, with no narration needed |
| 4 | Click a citation chip | Source passage expands, matched span highlighted | Groundedness made concrete |
| 5 | Speak a question in Hindi | Auto language detection, answer returned | Justifies the Sarvam choice and the multilingual corpus |
| 6 | Toggle chunking strategy, re-run the same query | Different passages retrieved, different waterfall | Requirement 2, visibly, which is otherwise invisible |
| 7 | Ask something the corpus cannot answer | **Abstention panel** with `LOW_CONFIDENCE`, score bars showing 0.19 against a 0.35 floor | Requirement 6, the single most convincing shot in the video |
| 8 | Ask something unsafe or off-topic | Abstention with a different typed reason | Shows the guardrail layers are distinct, not one blunt filter |
| 9 | Trigger the injected LLM 429 | Circuit breaker opens, waterfall shows the skipped stage hatched, extractive fallback answers anyway | Requirement 5, the harness earning its keep under failure |
| 10 | Cut to the benchmark output | P50 / P70 / P100 table, all three bands | Requirement 4, with the honest boundary stated |
| 11 | Narrate the honest paragraph from `Latency.md` section 9 | Terminal or slide | Turns the one weakness into a credibility moment |
| 12 | Close on the live URL and `#RAGInGoa` | | |

**Rules**
- Record at 1920x1080, 60fps. The waterfall animation and the amplitude ring do not survive 30fps.
- **Record a second pass in a square viewport** for the Instagram crop. The two-column layout does not crop to 1:1.
- One take per beat, cut together. Do not attempt a single unbroken take; one network hiccup wastes twenty minutes.
- Do not speed up the latency demo. The whole point is that it is already fast. Speeding it up destroys the claim.
- Have a backup recording of every beat. Live demos fail on the day you need them.

---

## 4. Promotion tracker

**Mandatory. Every member. Every platform. Both videos. `#RAGInGoa` on all of them.**

| Member | IG posted | IG public? | X posted | LinkedIn posted | Links collected |
|---|---|---|---|---|---|
| _name_ | ☐ | ☐ | ☐ | ☐ | ☐ |
| _name_ | ☐ | ☐ | ☐ | ☐ | ☐ |
| _name_ | ☐ | ☐ | ☐ | ☐ | ☐ |
| _name_ | ☐ | ☐ | ☐ | ☐ | ☐ |

**At least one Instagram account must be public.** Decide who, before posting day, and have them switch it before uploading. A public account switched after posting sometimes does not backfill visibility.

**Suggested caption** (adapt per person, do not post four identical captions, it reads as spam and platforms may suppress it):

> We built a voice-first RAG system for HH Goa 2026. Speak a question, get a grounded answer from AI4Bharat's MSMARCO-XI corpus, cited, in under 200ms on the core pipeline.
>
> Eight chunking strategies. Hybrid retrieval with cross-encoder reranking. Zero network calls on the fast path. And it refuses to answer when it cannot ground the answer, which turned out to be the hardest part to get right.
>
> Full latency breakdown and code in the repo.
>
> #RAGInGoa

**Per-platform format**
- Instagram: 1080x1080 square crop, both videos, either as a carousel or two posts. Hashtag in the caption, not the first comment.
- X: 16:9, both videos. Thread of two posts works better than one post with two videos.
- LinkedIn: 16:9, both videos. Longer caption performs better here; expand on the technical decisions.

**Do all posting on 22 August, together, in one sitting.** Chasing a teammate's LinkedIn post at 11:40 PM is how this requirement gets failed.

---

## 5. Pre-submission verification

Run this on 22 August, before touching the form. **A second person ticks each box, not the person who did the work.**

**Live link**
- ☐ Opens in a fresh browser with no cache
- ☐ Opens on mobile data, not just office wifi
- ☐ Mic permission prompt appears and works
- ☐ A full voice query succeeds
- ☐ A text query succeeds (fallback for judges with no mic)
- ☐ Abstention case triggers correctly
- ☐ No console errors on load
- ☐ Backend health check green, indexes warm

**Repo**
- ☐ Public, not private
- ☐ README has architecture diagram, latency table, reproduction steps, honest boundary statement
- ☐ `.env` is not committed
- ☐ **Secret scan run over full git history**, not just the working tree
- ☐ All planning docs committed
- ☐ `bench/results/` contains the final dated results
- ☐ Repo clones and runs from the README instructions on a clean machine

**Videos**
- ☐ Video 1 is 90 seconds or under
- ☐ Video 1 is about process, not product
- ☐ Video 2 shows the full pipeline working
- ☐ Subtitles burned in on both
- ☐ Both uploaded to all three platforms by all members
- ☐ `#RAGInGoa` present on every single post

**Form**
- ☐ Every field filled
- ☐ Links tested by pasting them into an incognito window
- ☐ Team name `OK4T` spelled correctly
- ☐ Submitted once, by the designated person, after every box above is ticked

---

## 6. Timeline for the final 48 hours

| When | What |
|---|---|
| 21 Aug, 11:59 PM | **Code freeze.** No merges after this. |
| 22 Aug, morning | Video 1 assembly and export |
| 22 Aug, midday | Video 2 recording, all beats, backups of each |
| 22 Aug, afternoon | Video 2 edit and export, both aspect ratios |
| 22 Aug, early evening | Full verification pass, section 5, second-person checked |
| 22 Aug, ~8 PM | **Everyone posts, together, in one sitting.** Links collected. |
| 22 Aug, ~10 PM | Form submitted |
| 22 Aug, 11:59 PM | Deadline. Do not be here. |

Submitting two hours early costs nothing. Submitting two minutes late costs everything, and there are no resubmissions.
