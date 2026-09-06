/**
 * Application shell.
 *
 * Three screens and one rule about moving between them: the library, the
 * reading, the documents. A review is opened from a session file the sidecar
 * already knows about, or prepared fresh from one or two study folders; either
 * way the reading screen is only ever entered with a session in hand, so it
 * never has to render a "no data" state.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import type { LocaleBundle } from "./api/types";
import { useConnection } from "./state/connection";
import { useLibrary } from "./state/library";
import { useSession } from "./state/session";
import { useShelf } from "./state/shelf";
import { useViewer } from "./state/viewer";
import { ErrorBoundary } from "./view/ErrorBoundary";
import { Home } from "./view/Home";
import { IdentityGate } from "./view/IdentityGate";
import { Review } from "./view/Review";

type Screen = "home" | "review";

export default function App() {
  const { status, error, connect } = useConnection();
  const [screen, setScreen] = useState<Screen>("home");
  const [locale, setLocale] = useState<LocaleBundle | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [gate, setGate] = useState<{ folders: string[]; message: string } | null>(null);
  const root = useShelf((s) => s.root);

  useEffect(() => {
    void connect();
  }, [connect]);

  useEffect(() => {
    if (status !== "ready") return;
    void api
      .locale("tr")
      .then((result) => setLocale(result.locale))
      .catch(() => undefined);

    // A session handed in on the URL is how the agent bridge and a saved
    // shortcut both arrive; it goes straight to the reading.
    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("session");
    if (wanted) {
      void useSession
        .getState()
        .open(wanted)
        .then(() => setScreen("review"))
        .catch((e: unknown) => setFailure(e instanceof Error ? e.message : String(e)));
    }
  }, [status]);

  const openSession = useCallback(async (path: string) => {
    setOpening(path);
    setFailure(null);
    useViewer.getState().reset();
    try {
      await useSession.getState().open(path);
      setScreen("review");
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setOpening(null);
    }
  }, []);

  const prepare = useCallback(
    async (folders: string[], identitySource?: string) => {
      setPreparing(true);
      setFailure(null);
      try {
        // Prepared against the scanned root, so the engine resolves study
        // folders exactly as `openrad prepare` would. `repo` is left to the
        // sidecar, which puts the session in the application's own workspace
        // rather than inside the patient's folder.
        await useSession.getState().prepare({
          ...(root ? { studies_root: root } : {}),
          folders,
          lang: "tr",
          ...(identitySource ? { identity_source: identitySource } : {}),
        });
        useViewer.getState().reset();
        setGate(null);
        setScreen("review");
        void useLibrary.getState().refresh();
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        // The engine refuses to compare exports whose patient identifiers
        // differ. That is a question for the reader, not an error to report.
        if (/patient identifier/i.test(message) || /identity evidence/i.test(message)) {
          setGate({ folders, message });
        } else {
          setFailure(message);
        }
      } finally {
        setPreparing(false);
      }
    },
    [root],
  );

  if (status === "connecting") {
    return <Splash message="Motora bağlanılıyor…" />;
  }
  if (status === "failed") {
    return (
      <Splash
        message="Motora bağlanılamadı"
        detail={error ?? ""}
        action={
          <button type="button" className="btn btn-primary" onClick={() => void connect()}>
            Yeniden dene
          </button>
        }
      />
    );
  }

  return (
    <ErrorBoundary fallbackTitle="Uygulamada bir sorun oluştu">
      <div className="relative h-full w-full bg-ink-950">
        {failure && (
          <div className="fade-in absolute inset-x-0 top-0 z-50 flex items-center justify-center gap-3 border-b border-alert-400/30 bg-alert-900 px-4 py-2 text-[12px] text-alert-400">
            <span className="truncate">{failure}</span>
            <button type="button" className="shrink-0 underline" onClick={() => setFailure(null)}>
              kapat
            </button>
          </div>
        )}

        {screen === "home" && (
          <Home
            opening={opening}
            preparing={preparing}
            onOpenSession={(path) => void openSession(path)}
            onPrepare={(folders) => void prepare(folders)}
          />
        )}

        {screen === "review" && <Review locale={locale} onHome={() => setScreen("home")} />}

        {gate && root && (
          <IdentityGate
            studiesRoot={root}
            folders={gate.folders}
            message={gate.message}
            onCancel={() => setGate(null)}
            onConfirm={(identitySource) => void prepare(gate.folders, identitySource)}
          />
        )}
      </div>
    </ErrorBoundary>
  );
}

function Splash({
  message,
  detail,
  action,
}: {
  message: string;
  detail?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="grid h-full place-items-center bg-ink-950">
      <div className="max-w-md px-8 text-center">
        <div className="mx-auto mb-5 h-6 w-6 rounded-full border-2 border-amber-400/25 border-t-amber-400 spin" />
        <p className="display text-[17px]">{message}</p>
        {detail && (
          <p className="readout mt-3 text-[11px] leading-relaxed text-chalk-600">{detail}</p>
        )}
        {action && <div className="mt-6">{action}</div>}
      </div>
    </div>
  );
}
