# OPERATIONS.md

**Two jobs, written out in full: changing the external-source limits, and
deploying to https://shrutirag.duckdns.org.**

Written for a session starting cold. It assumes nothing about the codebase and
names every file and command. If a step here disagrees with your instinct,
follow the step — most of them exist because the obvious approach failed once and
cost a debugging cycle.

Read [`DONT-FORGET.md`](DONT-FORGET.md) as well; it holds the facts that are
easy to get wrong. This file is only the two procedures.

---

## 0. Orientation, in sixty seconds

| | |
|---|---|
| Local repo | `C:\rag` on the Windows box |
| Repo on the server | `/home/haziqlandge/app` — **not a git checkout**, see §2.2 |
| What Caddy actually serves | `/var/www/shruti` — **not** the repo, see §2.1 |
| Server | `haziqlandge@34.100.222.236`, GCP `n2-standard-8`, Mumbai |
| Services | `shruti-core` (:8000) and `shruti-gateway` (:8001), systemd, loopback only |
| Public URL | https://shrutirag.duckdns.org, Caddy on 443 |

The site is **static files plus two Python services**. There is no build step, no
bundler, no Docker, no CI. "Deploying" means copying files and restarting a
systemd unit.

**Start the stack locally** with `run-dev.bat` (repo root). It opens three
windows: `rag_core` on :8000, `stt_gateway` on :8001, and the site on :3000.
`rag_core` takes ~13 s to load a 655 MB index and `/health` returns 503 until it
is ready, so wait rather than assuming it failed.

**Port 3000 is not a preference.** `stt_gateway` allows CORS from `:3000` only,
so on any other port typing works and the microphone fails with an error that
reads exactly like a broken mic.

---

## 1. Changing the limits

Both numbers live in **`services/rag_core/config.py`**. Both are backend-only —
the browser reads neither — so changing them needs `rag_core` restarted and
nothing else. Neither affects the 200 ms claim; both apply only to the external
source, which is outside it by construction.

### 1.1 The per-client rate limit

**Constant:** `ASIDE_RATE_LIMIT`
**Currently:** `15`
**Meaning:** how many external-source calls one client may make per
`ASIDE_RATE_WINDOW_SECONDS` (60).

```python
# services/rag_core/config.py
ASIDE_RATE_LIMIT: Final[int] = 15         # 0 = DISABLED
ASIDE_RATE_WINDOW_SECONDS: Final[float] = 60.0
```

- **`0` means DISABLED**, not "refuse everything". That is deliberate and pinned
  by `tests/test_ratelimit.py::test_zero_disables_the_limiter`. Zero is the off
  switch because it is the one value that could never be a sensible cap.
- It has been **5**, then **0**, and is now **15**. Loose enough that a judge
  working through the sample questions and a few of their own never meets it;
  tight enough that a script cannot drain the shared token window in seconds.
- **Exceeding it is VISIBLE to the visitor.** The endpoint returns
  `rate_limited: true` and the external panel says *"too many frequent
  requests ⚠ / You are being Rate Limited"*. Every OTHER empty response — no
  key, dead upstream, open breaker — stays silent and the panel simply does not
  appear, because none of those is the visitor's doing. Being throttled is, and
  it clears on its own, so saying so means they wait instead of concluding the
  feature is broken. If you change this behaviour, change `renderAside()` in
  `frontends/_shared/ui.js` and the `rate_limited` flag in `main.py` together.

**To change it:** edit the number, restart `rag_core`, confirm on `/health`.

```bash
curl -s http://127.0.0.1:8000/health
```

`aside.per_client_per_minute` reports the value, or `null` when it is `0`.

**To verify it actually bites**, send one more than the limit from a single
client. Every call must carry the SAME `X-Forwarded-For`, since that is the
bucket key:

```bash
python -c "import httpx; [print(i+1, httpx.post('http://127.0.0.1:8000/v1/aside', json={'query':'who is elon musk'}, headers={'X-Forwarded-For':'203.0.113.99'}, timeout=40).json().get('rate_limited')) for i in range(17)]"
```

Expect `False` for the first 15 and `True` after. A refused call returns in ~1 ms
with no network touched — that is the limiter running in FRONT of the circuit
breaker, so a capped client never records a failure against a breaker that is
protecting other visitors.

**Why it exists, before you leave it off permanently.** The external source and
the Band B generative fallback share ONE free-tier Groq key and one 12,000-token
window. Without a cap, one visitor clicking repeatedly exhausts that window and
the panel stops appearing **for everyone**, taking Band B with it. Turn it back
on before the site is public for any length of time.

**"Per client" means the client IP, read from the RIGHTMOST `X-Forwarded-For`
hop.** Caddy appends the real peer, so the last entry is the one it wrote.
Reading the leftmost — which is the usual advice — would let anyone mint a fresh
identity per request by varying one header.

### 1.2 The output token cap

**Constant:** `ASIDE_MAX_TOKENS`
**Currently:** `200`
**Meaning:** the longest answer the external source may write.

```python
# services/rag_core/config.py
ASIDE_MAX_TOKENS: Final[int] = 200
```

**THERE IS A FLOOR UNDER THIS AND IT IS CLOSE. Do not lower it without
re-measuring.** `openai/gpt-oss-20b` is a *reasoning* model: it spends tokens
thinking before it writes, and the thinking comes out of the same cap. At **160**
it returned `"Eric Adams is the"` — a real, shipped truncation, recorded in
`ISSUES.md` I34. 200 leaves only 40 tokens of headroom above that.

There is a separate constant `GROQ_MAX_TOKENS = 160` for the *grounded* Band B
path. **That is a different number for a different job** — there the model
paraphrases passages it was handed and needs less room. Do not "tidy" them into
one.

**If you change `ASIDE_MAX_TOKENS`, re-run this and read every answer:**

```bash
python -c "import httpx; [print('---', q, '\n', (httpx.post('http://127.0.0.1:8000/v1/aside', json={'query': q}, timeout=40).json().get('text') or '')) for q in ['Who is the mayor of New York City?','Who is Donald Trump?','Explain photosynthesis in detail','What is the population of India?','प्रकाश संश्लेषण क्या है']]"
```

Every answer must end in `.`, `!`, `?` or `।`. A sentence that stops mid-word is
the truncation trap firing again. Test **both scripts** — Devanagari uses more
tokens per visible character.

`tests/test_aside.py` pins the value, so change the assertion in the same commit
and say in the message that you re-measured.

### 1.3 Restarting after a config change

**Locally:** close the `rag_core` window and re-run `run-dev.bat`, or:

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8000,8001,3000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
```

then `run-dev.bat`. Wait for `/health` to return `"status":"ok"` — it is 503
while the index loads.

**On the server:** see §2. Config changes are backend, so you need the
`services/` half of the deploy and a `systemctl restart`.

---

## 2. Deploying to shrutirag.duckdns.org

### 2.1 The two traps, before any commands

**Trap one: the web root is not the repo.** Caddy serves `/var/www/shruti`. The
repo is at `/home/haziqlandge/app`. A home directory is `0750`, the `caddy` user
cannot traverse into it, and serving from `~` returns 403 with nothing useful in
the log. **Editing `~/app/frontends/` changes nothing until an `rsync` runs**, and
the failure is silent: `scp` succeeds, `grep` on the file you copied finds your
change, and the site keeps serving the old one.

**Trap two: the browser cache.** Caddy sends no `cache-control` on these files,
so Chrome reuses a heuristically cached copy and the page looks unchanged after a
completely correct deploy. **If the verifier says the new bytes are live and the
page still looks old, do not re-deploy.** Ctrl-F5, or DevTools with "Disable
cache" ticked.

### 2.2 `git pull` on the server does not work

`~/app` is **not a git repository**. The tree got there by `scp` during Phase 7
and was never cloned. `cd ~/app && git pull` fails with *"not a git repository"*.

So the deploy **copies files from the Windows box**. Do not spend time trying to
make `git pull` work unless you first `git clone` the repo there — and note the
repo is private, so that needs credentials on the server.

### 2.3 The one command

From `C:\rag` on the Windows box:

```bash
deploy-live.bat copy
```

`copy` is the mode that works today: it tars the working tree over SSH instead of
pulling. The other modes exist for when the server becomes a real checkout:

| | |
|---|---|
| `deploy-live.bat copy` | **use this.** Sends this machine's files, needs no git on the server |
| `deploy-live.bat` | `git pull` on the server, then deploy — fails today, see §2.2 |
| `deploy-live.bat site` | static files only, skips the ~13 s core restart |
| `deploy-live.bat check` | touches nothing, just reports what is live |

It refuses to run past uncommitted or unpushed work in the non-`copy` modes, and
it uses plain OpenSSH — **never `gcloud compute ssh`**, which on this Windows box
shells out to PuTTY's plink and gets the key refused, a failure that reads like a
permissions problem and is not.

### 2.4 Doing it by hand

If the script is unavailable or you need to see each step:

```bash
# 1. copy the tree up  (run from C:\rag)
tar -cf - frontends services | ssh -i ~/.ssh/google_compute_engine haziqlandge@34.100.222.236 "tar -xf - -C ~/app"

# 2. everything else runs on the server
ssh -i ~/.ssh/google_compute_engine haziqlandge@34.100.222.236
```

then, on the server:

```bash
cd ~/app && ./deploy/deploy.sh          # site + services
# or: ./deploy/deploy.sh --site         # static only
# or: ./deploy/deploy.sh --services     # restart only
```

`deploy/deploy.sh` does the rsync, the `chown`, the restart, waits for `/health`
to come back green, and then verifies over HTTPS. Its raw form:

```bash
sudo rsync -a --delete /home/haziqlandge/app/frontends/ /var/www/shruti/
sudo chown -R caddy:caddy /var/www/shruti
sudo systemctl restart shruti-core shruti-gateway     # only if services/ changed
```

`--delete` matters: without it a file you removed from the repo keeps being
served forever.

### 2.5 SHELL SCRIPTS MUST BE LF

`core.autocrlf` is `true` on the Windows box. A `.sh` file that reaches the
server with CRLF fails as:

```
/usr/bin/env: 'bash\r': No such file or directory
```

`.gitattributes` pins `*.sh` to LF and this is handled — but if you create a new
shell script, check it before shipping:

```bash
file deploy/deploy.sh     # must NOT say "with CRLF line terminators"
```

### 2.6 Verifying — the only check that means anything

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\verify-live.ps1
```

It fetches every asset over HTTPS and **compares the whole file by hash**, with
line endings normalised so the CRLF checkout here and the LF checkout there are
not a permanent false mismatch.

**Do not replace this with a grep.** The first version grepped for a phrase from
the newest change and reported `ok` for `ui.js` while the server was two days
stale — the phrase it picked was already in the file. A probe that can pass
against the wrong build is worse than no probe, because it sends you hunting for
a cache problem you do not have.

Expected output:

```
   ok      index.html
   ...
   ok      rag_core carries the aside rate limit
   10 of 10 assets match. The live site is this build.
```

Any `STALE` line means the sync did not land — SSH in and run
`./deploy/deploy.sh` directly to see the error.

### 2.7 Which half do you actually need?

| you changed | frontend sync | core restart |
|---|---|---|
| `frontends/**` | yes | no — use `--site` |
| `services/**` (incl. `config.py`) | no | **yes** |
| both | yes | yes |

Config changes like `ASIDE_MAX_TOKENS` and `ASIDE_RATE_LIMIT` are **services**,
so a frontend-only sync will appear to succeed and change nothing.

### 2.8 Smoke test the live site

```bash
curl -s https://shrutirag.duckdns.org/api/core/health
```

Expect `"status":"ok"`, `"reranker":"multi"`, `"generative":true`, and an
`aside` block. Then a real question:

```bash
curl -s -X POST https://shrutirag.duckdns.org/api/core/v1/answer -H "Content-Type: application/json" -d '{"query":"what is the boiling point of water","mode":"fast"}'
```

**Do not test Hindi through a Windows shell** — non-ASCII through `curl` there
mangles silently (`ISSUES.md` I12). Use the browser or a Python client.

---

## 3. If something is wrong

| symptom | cause | fix |
|---|---|---|
| Page looks old, verifier says `ok` | browser cache | Ctrl-F5. Do **not** re-deploy |
| Verifier says `STALE` | rsync did not run | `ssh` in, run `./deploy/deploy.sh` |
| `/usr/bin/env: 'bash\r'` | CRLF in a `.sh` | §2.5 |
| `not a git repository` | `~/app` is not a clone | use `deploy-live.bat copy`, §2.2 |
| `gcloud compute ssh` key refused | plink on Windows | use plain `ssh -i ~/.ssh/google_compute_engine` |
| 403 on every page | serving from `~` not `/var/www` | §2.1 |
| Core 503 after restart | index still loading | wait ~13 s |
| External panel missing | rate limit, no key, or dead upstream | all three look identical by design. `curl` `/v1/aside` and read `upstream_ms` |
| External answer cut off mid-sentence | `ASIDE_MAX_TOKENS` too low | §1.2 |
| Local mic dead | not on port 3000 | §0 |

---

## 4. What must not drift

- **No API key ever reaches the browser.** Keys live in `~/app/.env` on the
  server and `C:\rag\.env` locally, both gitignored.
- **Never quote 59.99 ms as the product's latency.** That is the development
  machine. The deployed figures are 95.89 en / 115.88 hi.
- **The interface says "external source", never "AI" and never "aside".**
  `/v1/aside` is the internal endpoint name and stays internal.
- **`MODEL` and `EXTERNAL` must not agree in accurate mode.** If they do, the
  external view has stopped counting the external-source call — this has broken
  twice. `ISSUES.md` I36 and I37.
