/**
 * The agent bridge, from the reader's side.
 *
 * When an MCP client is driving this session, two things have to be visible.
 * The first is what it did: every tool call arrives on a live stream and lands
 * in the transcript, so the person can check the agent's work instead of
 * trusting it. The second is the display gate. When the agent asks to read a
 * rendered sheet, that request surfaces here as a page the person has to
 * actually look at -- at full resolution, for long enough to count -- before
 * the agent is allowed to receive it and mark it reviewed.
 *
 * The gate is the reason this panel is not a log viewer. Declining is a real
 * option, and a page nobody looks at never becomes evidence.
 */
import { useEffect, useRef, useState } from "react";
import { api, followAgent } from "../api/client";
import type { DisplayRequest, TranscriptEntry } from "../api/types";
import { useSession } from "../state/session";
import { PageViewer } from "./PageViewer";

interface StreamState {
  connected: boolean;
  transcript: TranscriptEntry[];
  pending: DisplayRequest[];
}

function useAgentStream(): StreamState {
  const [state, setState] = useState<StreamState>({ connected: false, transcript: [], pending: [] });

  useEffect(() => {
    let alive = true;
    void api
      .agentState()
      .then((snapshot) => {
        if (alive) {
          setState({ connected: true, transcript: snapshot.transcript, pending: snapshot.pending_displays });
        }
      })
      .catch(() => undefined);

    const stop = followAgent((event, data) => {
      if (!alive) return;
      if (event === "hello") {
        const payload = data as { transcript: TranscriptEntry[]; pending_displays: DisplayRequest[] };
        setState({ connected: true, transcript: payload.transcript, pending: payload.pending_displays });
      } else if (event === "entry") {
        setState((prev) => ({ ...prev, transcript: [...prev.transcript, data as TranscriptEntry].slice(-400) }));
      } else if (event === "display") {
        setState((prev) => ({ ...prev, pending: [...prev.pending, data as DisplayRequest] }));
      }
    });
    return () => {
      alive = false;
      stop();
    };
  }, []);

  return state;
}

export function AgentPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { transcript, pending } = useAgentStream();
  const [dismissed, setDismissed] = useState<string[]>([]);
  const request = pending.find((r) => !dismissed.includes(r.id));
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [transcript.length]);

  return (
    <>
      {request && (
        <DisplayGateDialog
          request={request}
          onSettled={() => setDismissed((prev) => [...prev, request.id])}
        />
      )}
      {open && (
        <aside className="chrome-grain absolute inset-y-0 right-0 z-40 flex w-[380px] flex-col border-l border-[var(--hairline-strong)] bg-ink-900 shadow-[-16px_0_40px_rgba(0,0,0,0.45)]">
          <header className="flex h-9 shrink-0 items-center gap-2 border-b border-[var(--hairline)] px-2.5">
            <span className="rail-label">Ajan</span>
            <span className="chip text-attest-400">MCP köprüsü</span>
            <div className="flex-1" />
            <button type="button" className="btn" onClick={onClose}>
              Kapat
            </button>
          </header>

          <div ref={listRef} className="scroll-y min-h-0 flex-1 p-2">
            {transcript.length === 0 ? (
              <p className="p-2 text-[11px] leading-relaxed text-chalk-600">
                Henüz bir ajan bağlanmadı. Claude Code veya Claude Desktop’ı köprüye bağladığınızda
                yaptığı her adım burada canlı görünür; bir sayfayı okumak istediğinde önce size
                gösterilir.
              </p>
            ) : (
              <ol className="space-y-1">
                {transcript.map((entry) => (
                  <li key={entry.id} className="flex gap-2 rounded-[2px] px-1.5 py-1 hover:bg-ink-850">
                    <span className="readout mt-0.5 shrink-0 text-[10px] text-chalk-600">
                      {new Date(entry.ts * 1000).toLocaleTimeString("tr-TR", { hour12: false })}
                    </span>
                    <span className="min-w-0">
                      <span
                        className={`block text-[11px] leading-snug ${
                          entry.kind === "display_shown"
                            ? "text-attest-400"
                            : entry.kind === "display_request"
                              ? "text-amber-400"
                              : "text-chalk-300"
                        }`}
                      >
                        {entry.summary ?? entry.name ?? entry.kind}
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <footer className="shrink-0 border-t border-[var(--hairline)] p-2.5">
            <p className="text-[10px] leading-relaxed text-chalk-600">
              Ajan bu pencerenin oturumunu ve çözülmüş hacimlerini paylaşır. Bir sayfayı “okundu”
              işaretleyebilmesi için o sayfanın burada gerçekten gösterilmiş olması gerekir.
            </p>
          </footer>
        </aside>
      )}
    </>
  );
}

/**
 * The gate itself, wrapped around the shared page viewer.
 *
 * The only difference from a reader opening a page is what happens afterwards:
 * the window tells the sidecar it displayed it, which releases the agent's
 * blocked `page_view`.
 */
function DisplayGateDialog({ request, onSettled }: { request: DisplayRequest; onSettled: () => void }) {
  const workDir = useSession((s) => s.session?.work_dir ?? "");
  const sessionPath = useSession((s) => s.path ?? undefined);
  return (
    <PageViewer
      pagePath={request.page_path}
      purpose={request.purpose}
      workDir={workDir}
      sessionPath={sessionPath}
      requestedByAgent
      onAttested={() => {
        void api.displayShown(request.id).finally(() => {
          void useSession.getState().refreshCoverage();
          onSettled();
        });
      }}
      onDismiss={() => {
        void api.displayDeclined(request.id, "Okuyucu bu sayfayı görüntülemedi").finally(onSettled);
      }}
    />
  );
}
