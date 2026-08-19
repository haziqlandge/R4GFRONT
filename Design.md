# Design.md

Visual and interaction system for the voice RAG interface. Team OK4T.

> **Status, 20 August 2026: partly superseded.** This document was written for
> `apps/web`, the Next.js surface that has since been removed. The *thesis*
> below still governs the shipped site — instrumentation rather than a chat app,
> every number mono and tabular, the abstention panel weighted equally to an
> answer, the measurement boundary on screen — and those rules are restated in
> `HANDOFF.md` 5A. The specific type stack, colour tokens, component names and
> `tokens.css` file references describe a surface that no longer exists. The
> shipped design is documented in `frontends/README.md` and its decisions live
> in `frontends/theme.css`.

---

## 1. Design thesis

The product is a machine that listens, thinks visibly, and sometimes refuses. The interface should feel like **instrumentation**, not like a chat app.

Three commitments follow from that:

1. **Latency is the hero, not the answer.** Most RAG demos hide their timing. Ours puts a live per-stage waterfall on screen. The visual language should therefore borrow from oscilloscopes, audio meters and flight instruments rather than from chat bubbles.
2. **Refusal is a first-class state, not an error.** Abstention gets its own designed treatment with equal visual weight to an answer. It is a feature the brief asks for; it should not look like something broke.
3. **One screen, no navigation.** There is nowhere to go. Every pixel is the product.

**What this is explicitly not:** a centered card on a gradient background with a shadcn button and an Inter heading. That is the default look of every hackathon submission and it reads as templated. The brief asks that nothing look stale or repetitive, and avoiding the current default stack aesthetic is the single highest-leverage way to satisfy that.

---

## 2. Colour

Dark-first. Not because dark mode is fashionable but because the interface is dense with live numeric readouts and a dark field makes signal colours legible.

### 2.1 Base

```css
--ink-900: #0A0C0F;   /* page field                     */
--ink-800: #101318;   /* raised surface, cards          */
--ink-700: #171B22;   /* input wells, inset areas       */
--ink-600: #22272F;   /* borders, dividers              */
--ink-500: #333A44;   /* disabled, inactive strokes     */
```

The base is a cool near-black with a slight blue cast, not pure `#000`. Pure black on OLED causes visible smearing during the mic orb animation and makes the whole thing feel cheap.

### 2.2 Foreground

```css
--paper-100: #F2F4F7;  /* primary text          */
--paper-300: #B6BDC7;  /* secondary text        */
--paper-500: #7C838E;  /* tertiary, timestamps  */
--paper-700: #4C525B;  /* placeholder           */
```

### 2.3 Signal

Exactly four signal colours. Each one means one thing and never anything else. This is the discipline that keeps a dense interface readable.

```css
--signal-live:   #4EE1A0;  /* listening, healthy, under budget */
--signal-think:  #7C8CFF;  /* processing, retrieval active     */
--signal-warn:   #F2B441;  /* degraded, fallback path taken    */
--signal-halt:   #FF6B6B;  /* abstained, failed, over budget   */
```

**Semantics, enforced.**

| Colour | Means | Appears on |
|---|---|---|
| `--signal-live` | mic is open, or a stage completed inside its budget | mic orb active ring, waterfall bars under budget, health dot |
| `--signal-think` | work in progress, retrieval or generation running | waterfall bars in flight, transcript partials, citation chips |
| `--signal-warn` | degraded but functional: fallback path, skipped stage, circuit breaker open | waterfall bars for skipped stages, LLM-path badge |
| `--signal-halt` | abstained, or a stage exceeded budget | abstention panel accent, over-budget bars |

Never use a signal colour decoratively. If a colour appears where it does not carry meaning, the whole system stops being readable and becomes noise.

### 2.4 Accent

One warm accent, used sparingly, for the single primary affordance and nothing else.

```css
--ember-500: #FF7A45;  /* the mic orb core, idle state */
--ember-300: #FFA47A;
```

The warm ember against the cool field is the one deliberate temperature contrast in the whole design. It makes the mic unmistakably the thing you interact with, in a screen full of readouts you only observe. Use it in at most two places.

### 2.5 What is banned

- Gradients as backgrounds. A gradient on the mic orb is fine; a gradient behind the page is 2021.
- Purple-to-blue anything. It is the AI-product default and it is exhausted.
- Glassmorphism, frosted panels, backdrop blur on cards.
- More than five hues on screen at once.
- Colour used for hierarchy where weight or spacing would do the job.

---

## 3. Typography

Two families. Not three. Two.

```css
--font-ui:   "Geist", ui-sans-serif, system-ui, sans-serif;
--font-mono: "Geist Mono", ui-monospace, "SF Mono", monospace;
```

**Why these.** Geist has a slightly technical, drawn-for-screens character without the ubiquity of Inter. Geist Mono has excellent tabular figures, which matters enormously for a live-updating latency readout: proportional digits make numbers jitter as they change and it looks broken.

If Geist is unavailable, fall back to `Söhne` / `IBM Plex Sans` with `IBM Plex Mono`. Do not fall back to Inter. Inter is the stale default the brief warns about.

### 3.1 Scale

A 1.25 ratio, capped at six steps. More steps than this and hierarchy stops meaning anything.

| Token | Size / line-height | Use |
|---|---|---|
| `--t-display` | 44 / 48, weight 600, tracking -0.02em | The answer text only |
| `--t-title` | 28 / 34, weight 600 | Section labels, abstention headline |
| `--t-body` | 17 / 26, weight 400 | Passage text, citations |
| `--t-label` | 13 / 18, weight 500, tracking 0.04em, uppercase | Stage names, metric labels |
| `--t-mono-lg` | 20 / 24, mono, `font-variant-numeric: tabular-nums` | Total latency readout |
| `--t-mono-sm` | 12 / 16, mono, tabular | Per-stage ms, scores, trace id |

### 3.2 Rules

- **Every number on screen is mono and tabular.** No exceptions. This is the single typographic rule that makes the interface feel like an instrument.
- Uppercase with letterspacing is reserved for `--t-label` only. Uppercase body text is unreadable and uppercase headings are a cliché.
- Maximum measure for passage text: 68 characters. Retrieved passages can be long and full-width body text at 17px is punishing.
- The answer uses `--t-display` at 44px. It should feel disproportionately large relative to everything around it. The answer is the payload; the instrumentation is context.

---

## 4. Layout

### 4.1 Grid

12 columns, 24px gutters, max content width 1180px, centered. Two-zone split on desktop:

```
┌──────────────────────────────────────────────────────────┐
│  HEADER  logo · health dot · strategy toggle             │  64px
├────────────────────────────────┬─────────────────────────┤
│                                │                         │
│  STAGE                         │  INSTRUMENT             │
│  (cols 1-7)                    │  (cols 8-12)            │
│                                │                         │
│   mic orb                      │   latency waterfall     │
│   live transcript              │   confidence readout    │
│   answer OR abstention         │   stage log             │
│   citation chips               │   trace id              │
│                                │                         │
└────────────────────────────────┴─────────────────────────┘
```

Below 900px the instrument column collapses beneath the stage column, waterfall first. Do not hide it on mobile; it is the differentiator.

### 4.2 Spacing

8px base. Steps: 4, 8, 12, 16, 24, 32, 48, 72. Nothing else.

Generous vertical rhythm in the stage column, tight in the instrument column. The asymmetry is intentional: the stage is where you look, the instrument is where you scan.

### 4.3 Radii and borders

```css
--r-sm: 6px;    /* chips, badges          */
--r-md: 10px;   /* cards, panels          */
--r-lg: 18px;   /* the answer surface     */
--r-full: 999px;
```

Borders are `1px solid var(--ink-600)`. No shadows anywhere except a single soft glow on the mic orb when active. Shadows on a dark field read as smudges.

---

## 5. The mic orb

The one piece of the interface that should feel alive. Everything else is deliberately static and instrumental; this is where the personality lives.

**States**

| State | Appearance | Motion |
|---|---|---|
| Idle | 96px circle, `--ember-500` core at 40% opacity, thin `--ink-600` ring | Slow breathe, scale 1.0 to 1.03, 3.2s, ease-in-out |
| Requesting permission | Ring pulses `--paper-500` | 800ms pulse |
| Listening | Core at full `--ember-500`, outer ring `--signal-live`, ring radius driven by live RMS amplitude | Ring responds to voice in real time, 60fps, no smoothing beyond a 3-frame moving average |
| Processing | Core dims to 60%, ring switches to `--signal-think`, rotates | 1.1s linear rotation, continuous |
| Answered | Brief `--signal-live` ring flash, then return to idle | 260ms flash, 400ms settle |
| Abstained | Ring flashes `--signal-halt`, orb contracts slightly | 300ms |

**The amplitude-reactive ring is the most important detail in the whole design.** It is the thing that makes the product feel responsive before any latency number appears, and it costs almost nothing: read the analyser node RMS you already have from the Web Audio graph and map it to ring radius.

Respect `prefers-reduced-motion`: replace breathe and rotation with opacity steps, keep the amplitude ring (it is information, not decoration).

---

## 6. The latency waterfall

The signature component. It must be legible in a compressed Instagram video at arm's length, which is a genuine constraint on its design.

**Form.** Horizontal stacked bars, one row per stage, on a shared time axis scaled to the 200ms budget. A vertical rule marks 200ms.

```
input_guard   ▓▓                                    6ms
embed         ▓▓▓▓▓                                 11ms
dense         ▓▓                                     5ms
lexical       ▓                                      3ms
fusion        ▏                                    0.4ms
rerank        ▓▓▓▓▓▓▓▓▓▓▓▓▓▓                        31ms
route         ▏                                    0.2ms
extractive    ▓▓                                     4ms
output_guard  ▓▓▓▓                                   9ms
              ├────────────────────────┼─────────┤
              0                       200ms     TOTAL 69.6ms
```

**Rules**
- Bars fill left to right with a 180ms ease-out as each stage completes. Do not animate them all in at the end; the point is watching the pipeline execute.
- Bar colour: `--signal-live` under budget, `--signal-warn` if the stage was skipped for budget or took a fallback, `--signal-halt` if the total exceeds 200ms.
- Skipped stages render as a hollow outlined bar with a diagonal hatch. Visible absence, not invisible absence.
- The total readout is `--t-mono-lg`, tabular, and it counts up during execution rather than appearing fully formed.
- The 200ms rule is a 1px dashed `--paper-700` vertical line, always present, even when the total is well under it. Its presence is what makes the number meaningful.

---

## 7. Answer and abstention surfaces

### 7.1 Answer

Surface: `--ink-800`, `--r-lg`, 32px padding, 1px `--ink-600` border, left edge accented with a 3px `--signal-live` bar.

Text at `--t-display`. Below it, citation chips in a horizontal wrap. Below those, a single mono line: `EXTRACTIVE · 69.6ms · confidence 0.81`.

### 7.2 Citation chips

Pill, `--r-full`, `--ink-700` fill, `--signal-think` 1px border, `--t-mono-sm` text reading `[1] passage_id · 0.81`. On hover or tap, expand inline to show the source passage at `--t-body`, max measure 68ch, with the matched span highlighted in a `--signal-think` 18% background wash.

### 7.3 Abstention

**Equal visual weight to an answer. This is deliberate.**

Same surface geometry, but the left accent bar is `--signal-halt` and the headline is `--t-title` rather than `--t-display`:

```
DID NOT ANSWER
Reason: LOW_CONFIDENCE

The best matching passage scored 0.19 against a
required floor of 0.35. Nothing in the indexed
corpus reliably answers this question.

  top match      0.19  ▓▓▏
  required       0.35  ├─────
  score gap      0.02
```

The little inline bar comparison is the whole guardrail requirement made visible in one glance. A judge watching a video understands instantly that the system measured its own uncertainty and declined. That comprehension is worth more than any amount of guardrail code they cannot see.

Never render abstention in red-alert styling with a warning triangle. It is a correct outcome, not an error. Restrained, factual, informative.

---

## 8. Motion

**Durations.** 120ms micro (hover, chip expand), 180ms component (bar fill), 260ms surface (answer appear), 400ms settle.

**Easing.** `cubic-bezier(0.2, 0.8, 0.2, 1)` for entrances, `cubic-bezier(0.4, 0, 1, 1)` for exits, linear only for the continuous processing rotation.

**Rules**
- Nothing animates for longer than 400ms except the idle breathe and the processing rotation.
- No entrance animation on page load. The interface is present immediately. Staggered fade-ins on first paint are a hackathon tell.
- No spring physics. This is an instrument, not a toy.
- Transcript partials append character-groups without layout shift. Reserve the line height before text arrives.
- Everything respects `prefers-reduced-motion` except the amplitude ring, which is information.

---

## 9. Anti-repetition rules

The brief explicitly asks that nothing look stale or repetitive. Concretely:

**Constant, never varied (this is what prevents chaos)**
- Two font families, six type steps, no more
- Four signal colours with fixed meanings
- One accent colour
- One radius scale, one spacing scale
- One easing curve for entrances

**Varied, deliberately (this is what prevents monotony)**
- Density: stage column is airy, instrument column is dense. The contrast is the visual interest.
- Typographic scale jump: `--t-display` at 44px next to `--t-mono-sm` at 12px is a 3.7x jump on the same screen. Extreme contrast in one place beats moderate contrast everywhere.
- Texture: the waterfall's hatched skipped-stage bars are the only textured element. One texture, used once, is memorable. Texture everywhere is noise.
- Motion budget: exactly one continuously animated element (the orb). Everything else is still until it changes.

**The test:** screenshot the interface next to five other hackathon submissions. If ours is identifiable at thumbnail size, the design worked. If it is not, the most likely culprit is that the instrument column got cut, because that is the only genuinely unusual thing on the screen.

---

## 10. Token file

`apps/web/styles/tokens.css`, the single source of truth. Tailwind config extends from these; no hex value ever appears in a component.

```css
:root {
  --ink-900:#0A0C0F; --ink-800:#101318; --ink-700:#171B22;
  --ink-600:#22272F; --ink-500:#333A44;
  --paper-100:#F2F4F7; --paper-300:#B6BDC7;
  --paper-500:#7C838E; --paper-700:#4C525B;
  --signal-live:#4EE1A0; --signal-think:#7C8CFF;
  --signal-warn:#F2B441; --signal-halt:#FF6B6B;
  --ember-500:#FF7A45; --ember-300:#FFA47A;

  --font-ui:"Geist",ui-sans-serif,system-ui,sans-serif;
  --font-mono:"Geist Mono",ui-monospace,monospace;

  --r-sm:6px; --r-md:10px; --r-lg:18px; --r-full:999px;
  --ease-in:cubic-bezier(0.2,0.8,0.2,1);
  --ease-out:cubic-bezier(0.4,0,1,1);
}
```

---

## 11. Video and social assets

Video 2 is shot against this interface, so the design must survive compression.

- Record at 1920x1080, minimum 60fps, so the waterfall fill and amplitude ring survive the encode.
- Instagram crop: 1080x1080. The two-column desktop layout does not crop well. **Record a dedicated square-viewport pass** with the instrument column stacked below.
- The `--signal-live` green on `--ink-900` survives aggressive compression. The `--paper-500` grey on `--ink-800` does not. Any text that must be readable in the video uses `--paper-100`.
- Thumbnail: mic orb at listening state, waterfall visible, the total latency number large. No text overlay beyond the number and `#RAGInGoa`.
