/**
 * Typed clients for the two services. Architecture.md 9 is the contract.
 *
 * The browser never talks to Sarvam or Groq directly - Rules.md 4 is HARD about
 * that, and it is why stt_gateway exists as a separate process at all. No key
 * appears anywhere in this file or anywhere else under apps/web.
 */

export type AnswerStatus = "ANSWERED" | "ABSTAINED";
export type AnswerPath = "EXTRACTIVE" | "GENERATIVE" | "NONE";
export type AnswerMode = "fast" | "accurate";
export type AbstainReason =
  | "OFF_TOPIC"
  | "LOW_CONFIDENCE"
  | "UNSAFE_INPUT"
  | "UNGROUNDED_OUTPUT"
  | "AMBIGUOUS_RETRIEVAL";

export interface Citation {
  passage_id: string;
  score: number;
  text: string;
  language: string;
}

export interface StageSpan {
  name: string;
  ms: number;
  status: "ok" | "skipped" | "fallback" | "failed";
  detail: string | null;
}

export interface TraceView {
  total_ms: number;
  budget_ms: number;
  stages: StageSpan[];
}

export interface Confidence {
  rerank_top1: number | null;
  score_gap: number | null;
  groundedness: number | null;
}

export interface AnswerResponse {
  trace_id: string;
  status: AnswerStatus;
  path: AnswerPath;
  answer: string | null;
  abstain_reason: AbstainReason | null;
  citations: Citation[];
  confidence: Confidence;
  trace: TraceView | null;
}

export interface TranscriptResponse {
  type: string;
  text: string;
  language: string;
  language_confidence: number | null;
  stt_ms?: number;
  audio_seconds?: number;
}

const RAG_CORE =
  process.env.NEXT_PUBLIC_RAG_CORE_URL ?? "http://127.0.0.1:8000";
const STT_GATEWAY =
  process.env.NEXT_PUBLIC_STT_GATEWAY_URL ?? "http://127.0.0.1:8001";

export class ApiError extends Error {}

export async function ask(
  query: string,
  mode: AnswerMode = "fast",
  strategy = "c1"
): Promise<AnswerResponse> {
  const res = await fetch(`${RAG_CORE}/v1/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, mode, strategy, trace: true }),
  });
  if (!res.ok) {
    throw new ApiError(`rag_core returned ${res.status}`);
  }
  return (await res.json()) as AnswerResponse;
}

/**
 * Send one recorded utterance for transcription.
 *
 * Raw PCM16, not a container format. The recorder already produces exactly what
 * Sarvam wants and the gateway tags the codec, so re-wrapping it as WAV here
 * would add bytes and a second format to be wrong about.
 */
export async function transcribe(pcm: Int16Array): Promise<TranscriptResponse> {
  const form = new FormData();
  form.append(
    "file",
    new Blob([pcm.buffer as ArrayBuffer], { type: "application/octet-stream" }),
    "utterance.pcm"
  );
  form.append("language", "unknown");

  const res = await fetch(`${STT_GATEWAY}/v1/stt/file`, {
    method: "POST",
    body: form,
  });
  const body = await res.json();
  if (!res.ok) {
    throw new ApiError(body?.detail ?? `stt_gateway returned ${res.status}`);
  }
  return body as TranscriptResponse;
}

export interface Health {
  status: string;
  reranker?: string | null;
  generative?: boolean;
  passage_store?: string;
  chunks?: number;
}

export async function health(): Promise<Health | null> {
  try {
    const res = await fetch(`${RAG_CORE}/health`);
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}
