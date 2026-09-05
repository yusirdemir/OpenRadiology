/**
 * Client for the local sidecar.
 *
 * Discovery, in order:
 *   1. `window.__OPENRAD__`, injected by the Tauri shell after it reads the
 *      sidecar's handshake line. This is the packaged application's path.
 *   2. the `/api` prefix, which the Vite dev server proxies to a sidecar it
 *      finds through the same handshake file. This is the browser dev path.
 *
 * Nothing is ever hard-coded to a port: the sidecar binds port 0 and the token
 * is fresh per launch, so a stale tab cannot talk to a new session.
 */
import type {
  AttestationCoverage,
  DisplayRequest,
  IdentityCandidate,
  LocaleBundle,
  CheckResult,
  JobSnapshot,
  MeasureResult,
  PlaneName,
  SeriesCard,
  SeriesMeta,
  Session,
  SliceImage,
  StudyCard,
  TranscriptEntry,
  ViewEventPayload,
} from "./types";

declare global {
  interface Window {
    __OPENRAD__?: { url: string; token: string; version?: string };
  }
}

export interface Handshake {
  url: string;
  token: string;
}

export function handshake(): Handshake {
  const injected = typeof window !== "undefined" ? window.__OPENRAD__ : undefined;
  if (injected?.url) return { url: injected.url.replace(/\/$/, ""), token: injected.token };
  return { url: "/api", token: "" };
}

export class SidecarError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly kind: string,
  ) {
    super(message);
    this.name = "SidecarError";
  }
}

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const { token } = handshake();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

async function raise(response: Response): Promise<never> {
  let message = `${response.status} ${response.statusText}`;
  let kind = "HttpError";
  try {
    const body = await response.json();
    if (body?.error) message = String(body.error);
    if (body?.kind) kind = String(body.kind);
  } catch {
    /* a non-JSON error body is not worth a second failure */
  }
  throw new SidecarError(message, response.status, kind);
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const { url } = handshake();
  const response = await fetch(url + path, {
    ...init,
    headers: authHeaders({ "Content-Type": "application/json", ...(init?.headers ?? {}) }),
  });
  if (!response.ok) await raise(response);
  return (await response.json()) as T;
}

const get = <T>(path: string, signal?: AbortSignal) => json<T>(path, { method: "GET", signal });
const post = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  json<T>(path, { method: "POST", body: JSON.stringify(body), signal });
const put = <T>(path: string, body: unknown) => json<T>(path, { method: "PUT", body: JSON.stringify(body) });

// ------------------------------------------------------------------ pixels
function parseNumberPair(raw: string | null): [number, number] {
  const [a = "1", b = "1"] = (raw ?? "").split(",");
  return [Number(a), Number(b)];
}

export interface SliceQuery {
  study: string;
  series: string;
  plane: PlaneName;
  index: number;
  mipMm?: number;
  thickPx?: number;
  allowTilt?: boolean;
}

/**
 * One image as raw calibrated samples.
 *
 * The bytes are the engine's values -- Hounsfield units, SUV, MR signal -- not
 * display grey. Windowing happens on the GPU from these, so changing window
 * width or level costs nothing and never needs another request.
 */
export async function fetchSlice(query: SliceQuery, signal?: AbortSignal): Promise<SliceImage> {
  const { url } = handshake();
  const params = new URLSearchParams({
    study: query.study,
    series: query.series,
    plane: query.plane,
    index: String(query.index),
  });
  if (query.mipMm) params.set("mip", String(query.mipMm));
  if (query.thickPx && query.thickPx > 1) params.set("thick", String(query.thickPx));
  if (query.allowTilt) params.set("allow_tilt", "1");

  const response = await fetch(`${url}/series/slice?${params}`, { headers: authHeaders(), signal });
  if (!response.ok) await raise(response);
  const buffer = await response.arrayBuffer();
  const headers = response.headers;
  const [rows = 0, cols = 0] = parseNumberPair(headers.get("X-Shape"));
  const dtype = headers.get("X-Dtype") ?? "float32";
  const zRaw = headers.get("X-Z-Mm");
  return {
    pixels: dtype === "int16" ? new Int16Array(buffer) : new Float32Array(buffer),
    rows,
    cols,
    mmPerPx: parseNumberPair(headers.get("X-Mm-Per-Px")),
    plane: (headers.get("X-Plane") ?? "ax") as PlaneName,
    index: Number(headers.get("X-Index") ?? query.index),
    count: Number(headers.get("X-Count") ?? 0),
    sopUid: headers.get("X-Sop-Uid") ?? "",
    instance: headers.get("X-Instance") ?? "",
    seriesUid: headers.get("X-Series-Uid") ?? "",
    studyUid: headers.get("X-Study-Uid") ?? "",
    zMm: zRaw === null ? null : Number(zRaw),
    mipMm: Number(headers.get("X-Mip-Mm") ?? 0),
  };
}

/**
 * Fetch a work-directory asset as a blob.
 *
 * Images are loaded this way rather than by pointing `<img src>` at the
 * sidecar, because an `<img>` cannot carry an Authorization header and putting
 * the session token in a URL would spread it through history and logs for no
 * benefit. The server therefore accepts the token in a header only.
 */
export async function fetchFile(path: string): Promise<Blob> {
  const { url } = handshake();
  const response = await fetch(`${url}/files?path=${encodeURIComponent(path)}`, { headers: authHeaders() });
  if (!response.ok) await raise(response);
  return response.blob();
}

export async function fetchText(path: string): Promise<string> {
  return (await fetchFile(path)).text();
}

// -------------------------------------------------------------------- SSE
export type StreamHandler = (event: string, data: unknown) => void;

/**
 * Follow a server-sent event stream.
 *
 * `EventSource` cannot carry an Authorization header, and putting the session
 * token in a URL would leak it into logs and history, so the stream is read
 * from `fetch` and parsed here. Returns a function that stops following.
 */
export function followStream(path: string, onEvent: StreamHandler): () => void {
  const controller = new AbortController();
  const { url } = handshake();
  void (async () => {
    try {
      const response = await fetch(url + path, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) return;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let split = buffer.indexOf("\n\n");
        while (split !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          let name = "message";
          const payload: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) payload.push(line.slice(5).trim());
          }
          if (payload.length) {
            try {
              onEvent(name, JSON.parse(payload.join("\n")));
            } catch {
              onEvent(name, payload.join("\n"));
            }
          }
          split = buffer.indexOf("\n\n");
        }
      }
    } catch {
      /* aborted or the sidecar went away; the caller sees the job status */
    }
  })();
  return () => controller.abort();
}

export const followJob = (jobId: string, onEvent: StreamHandler): (() => void) =>
  followStream(`/jobs/${jobId}/events`, onEvent);

export const followAgent = (onEvent: StreamHandler): (() => void) => followStream("/agent/stream", onEvent);

// ------------------------------------------------------------------ routes
export const api = {
  health: () => get<{ ok: boolean; version: string; pid: number }>("/health"),
  status: () =>
    get<{
      version: string;
      roots: string[];
      cache: { budget_bytes: number; used_bytes: number; entries: unknown[] };
      jobs: JobSnapshot[];
      policy: { min_dwell_ms: number; min_scale: number; require_focus: boolean };
      regions: Record<string, string[]>;
      windows: { name: string; center: number; width: number }[];
    }>("/status"),
  doctor: () => get<{ exit_code: number; report: unknown }>("/doctor"),
  locale: (lang: string) =>
    get<{ language: string; available: string[]; locale: LocaleBundle }>(`/locale?lang=${encodeURIComponent(lang)}`),

  scanStudies: (root: string) => post<{ root: string; studies: StudyCard[] }>("/studies/scan", { root }),
  identityCandidates: (studiesRoot: string, folders: string[]) =>
    post<{ studies_root: string; default_name: string; candidates: IdentityCandidate[] }>(
      "/studies/identity-candidates",
      { studies_root: studiesRoot, folders },
    ),
  inventory: (study: string) => post<Record<string, unknown>>("/studies/inventory", { study }),
  listSeries: (study: string) => post<{ study: string; series: SeriesCard[] }>("/series/list", { study }),
  seriesMeta: (study: string, series: string, allowTilt = false) =>
    post<SeriesMeta>("/series/meta", { study, series, allow_tilt: allowTilt }),
  point: (study: string, series: string, index: number, row: number, col: number) =>
    post<{ patient_mm: number[]; value: number | null; modality: string; z_mm: number; sop_uid: string; instance: string }>(
      "/series/point",
      { study, series, index, row, col },
    ),
  locate: (study: string, series: string, patientMm: number[]) =>
    post<{ index: number; row: number; col: number; z_mm: number; sop_uid: string; instance: string; in_plane: boolean }>(
      "/series/locate",
      { study, series, patient_mm: patientMm },
    ),

  measure: (payload: Record<string, unknown>) => post<MeasureResult>("/measure", payload),

  prepare: (payload: Record<string, unknown>) =>
    post<{ session_path: string; session: Session }>("/session/prepare", payload),
  session: (path: string) =>
    get<{ session_path: string; session: Session; attestation: AttestationCoverage }>(
      `/session?path=${encodeURIComponent(path)}`,
    ),
  saveSession: (path: string, session: Session) =>
    put<{ session_path: string; session: Session; refused_reviews: { path: string; reason: string }[]; attestation: AttestationCoverage }>(
      "/session",
      { path, session },
    ),
  register: (path: string, directory: string) =>
    post<{ registered: number; session: Session; attestation: AttestationCoverage }>("/session/register", {
      path,
      directory,
    }),
  check: (path: string) => post<CheckResult>("/session/check", { path }),
  finish: (path: string, lang?: string) =>
    post<{ report: string; guide: string; report_sha256: string; guide_sha256: string; session: Session }>(
      "/session/finish",
      { path, lang },
    ),

  attest: (workDir: string, events: ViewEventPayload[], sessionPath?: string) =>
    post<{
      accepted: number;
      rejected: { reason: string }[];
      promoted?: string[];
      refused_reviews?: { path: string; reason: string }[];
      coverage?: AttestationCoverage;
    }>("/attest", { work_dir: workDir, events, ...(sessionPath ? { session: sessionPath } : {}) }),
  coverage: (sessionPath: string) =>
    get<AttestationCoverage>(`/attest/coverage?session=${encodeURIComponent(sessionPath)}`),

  render: (kind: string, payload: Record<string, unknown>) => post<JobSnapshot>(`/render/${kind}`, payload),
  jobs: () => get<{ jobs: JobSnapshot[] }>("/jobs"),
  job: (id: string) => get<JobSnapshot & { stdout: string[] }>(`/jobs/${id}`),
  cancelJob: (id: string) => post<JobSnapshot>(`/jobs/${id}/cancel`, {}),
  anonymize: (source: string, target: string, salt: string) =>
    post<JobSnapshot>("/anonymize", { source, target, salt }),

  agentState: () =>
    get<{ window_connected: boolean; pending_displays: DisplayRequest[]; transcript: TranscriptEntry[] }>("/agent/state"),
  displayShown: (id: string) => post<{ acknowledged: boolean }>(`/agent/display/${id}/shown`, {}),
  displayDeclined: (id: string, reason: string) =>
    post<{ acknowledged: boolean }>(`/agent/display/${id}/declined`, { reason }),
};

/** Filmstrip thumbnails as one PNG, with the indices it actually sampled. */
export async function fetchAtlas(
  study: string,
  series: string,
  plane: PlaneName,
  count: number,
  tile: number,
  win?: { center: number; width: number },
  signal?: AbortSignal,
): Promise<{ blob: Blob; tile: number; indices: number[] }> {
  const { url } = handshake();
  const params = new URLSearchParams({ study, series, plane, count: String(count), tile: String(tile) });
  if (win) {
    params.set("center", String(win.center));
    params.set("width", String(win.width));
  }
  const response = await fetch(`${url}/series/atlas?${params}`, { headers: authHeaders(), signal });
  if (!response.ok) await raise(response);
  const indices = (response.headers.get("X-Atlas-Indices") ?? "").split(",").filter(Boolean).map(Number);
  return { blob: await response.blob(), tile: Number(response.headers.get("X-Atlas-Tile") ?? tile), indices };
}
