# Deploying to Google Cloud

Target for `rag_core` and `stt_gateway`. Decided 15 Aug 2026, replacing Oracle Cloud. See `Memory.md` reversal R3 for why.

**Region: `asia-south1` (Mumbai).** `Architecture.md` §10 is unambiguous — a non-India region adds enough round trip to make the task unwinnable.

---

## 1. Why a VM and not Cloud Run

Cloud Run is the obvious GCP answer for a FastAPI service and it is the wrong one here.

`rag_core` holds ~1.2 GB of warm index in RAM and takes seconds to load it at startup. Cloud Run's default model pays that per cold start, which `Rules.md` §3.2 bans outright. You *can* pin `--min-instances=1` with CPU always allocated, which does keep it warm — but you are then paying VM prices for a VM you cannot SSH into, `mmap` predictably, or profile properly. For a project whose entire thesis is per-millisecond control, the VM is the honest choice.

**Compute Engine VM, always on.**

## 2. Machine type: avoid `e2`

| Type | vCPU / RAM | ~$/mo, `asia-south1` | Verdict |
|---|---|---|---|
| `e2-medium` | 2 shared / 4 GB | ~$27 | **No.** Burstable. |
| `e2-standard-2` | 2 / 8 GB | ~$49 | **No.** Still the `e2` burst model. |
| **`n2-standard-2`** | **2 / 8 GB** | **~$70** | Was the choice. Too small - see the note below. |
| `c2-standard-4` | 4 / 16 GB | ~$150 | Upgrade path if rerank misses budget |

**The `e2` family is burstable** — sustained CPU is throttled toward a baseline once burst credits run out. That is invisible at P50 and brutal at P100, and P100 is the number that fails. `Latency.md` §4 reserves 25 ms for jitter; a throttling vCPU eats that and more. `n2` gives consistent dedicated cores.

At ~$70/mo the $300 credit covers roughly four months. The submission needs it live through the HH Goa selection rounds in mid-September, so this is comfortable.

## 2A. What was actually deployed, and why it is not what section 2 says

**This guide describes the box as chosen on 17 August. It grew twice on 20
August** and the commands below still name the original size, which is correct
as a starting point and wrong as a description of production.

`n2-standard-2` missed the 200 ms budget on the deployed service - en P50
190.47 ms, hi P50 200.87 - so it went to `n2-standard-4` and then
`n2-standard-8`. **Read `ISSUES.md` I28 before concluding the box was the
problem**: most of that cost was two ONNX sessions with four intra-op threads
each fighting over four vCPUs, and fixing it took en P50 from 132.59 to 64.48
without touching hardware.

What the 8 vCPUs actually buy is **concurrency, not speed** (`ISSUES.md` I29):
every Band A stage is synchronous and never yields, so one uvicorn process
serves one request at a time. Production runs **4 workers x 2 ONNX threads**, and
if the vCPU count changes both numbers move with it - the rule is
workers = vCPUs / 2, paired with `ONNX_THREADS_SERVING = 2`.

**Cost.** Roughly 4x the ~$70/mo this guide budgeted, taken deliberately for a
judging window measured in weeks. Resizing down after judging is a task somebody
has to do; `HANDOFF.md` 1A has the command.

---

## 3. Provision

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable compute.googleapis.com

# Static IP first, so the URL never changes under you
gcloud compute addresses create rag-core-ip --region=asia-south1

gcloud compute instances create rag-core \
  --project=YOUR_PROJECT_ID \
  --zone=asia-south1-a \
  --machine-type=n2-standard-2 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-balanced \
  --address=$(gcloud compute addresses describe rag-core-ip --region=asia-south1 --format='value(address)') \
  --tags=rag-core

gcloud compute firewall-rules create allow-rag-core \
  --allow=tcp:80,tcp:443 \
  --target-tags=rag-core \
  --description="HTTP/HTTPS to rag_core"
```

GCP's firewall is the only one in the path — unlike Oracle, the Ubuntu image does not ship blocking `iptables` rules.

```bash
gcloud compute ssh rag-core --zone=asia-south1-a
```

## 4. On the box

```bash
sudo apt update && sudo apt install -y docker.io
sudo usermod -aG docker $USER && newgrp docker
```

Then run the `rag_core` image with the indexes baked in or mounted. Keys come from the environment, never from a committed file:

```bash
docker run -d --restart=always -p 80:8000 \
  -e GROQ_API_KEY="$GROQ_API_KEY" \
  --name rag-core ghcr.io/haziqlandge/rag-core:latest
```

That one key serves both the generative fallback and the `accurate`-mode aside,
out of the same 12,000-token window, which is why the aside is capped per client
(`ISSUES.md` I35). No Google key is needed or wanted — see `Memory.md` R5, and
note that if one is ever added it must be an **AI Studio** key, never Vertex AI,
which would bill the same $300 trial credit the next section tells you to set an
alarm on.

`--restart=always` plus the `/health` keepalive in `Architecture.md` §10 covers process death and host reboots.

## 5. Set a budget alert before you walk away

**Billing → Budgets & alerts → Create budget**, thresholds at $50 / $150 / $250.

The $300 credit is the whole runway. If it drains — a forgotten GPU, a runaway job — **every resource stops** and Compute Engine data is marked for deletion, with a 30-day grace period to recover. Losing the live URL during the selection rounds would be an unforced submission failure.

## 6. Trial mechanics worth knowing

- The trial ends at **$300 spent or 90 days, whichever comes first**. From 15 Aug, that is roughly 13 November.
- **You are not charged unless you manually upgrade** to a paid account. The trial account auto-closes instead.
- When it ends, **workloads stop** and data is marked for deletion. 30-day grace period to reinstate by upgrading.
- Free-trial accounts carry quota caps (commonly 8 vCPUs per region, no GPU). `n2-standard-2` is well inside them.

## 7. Frontend

The Next.js app goes to Vercel, region `bom1` (Mumbai), so the browser, the edge and `rag_core` are all in the same city. Vercel is fine for the frontend — `Rules.md` §3.2 only bans Vercel serverless functions for `rag_core`.
