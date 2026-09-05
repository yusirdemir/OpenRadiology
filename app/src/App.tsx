/** Application shell: connect to the sidecar, then shelf → workspace → report. */
import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import type { LocaleBundle } from "./api/types";
import { useConnection } from "./state/connection";
import { useSession } from "./state/session";
import { useShelf } from "./state/shelf";
import { useViewer } from "./state/viewer";
import { ReportScreen } from "./view/ReportScreen";
import { IdentityGate } from "./view/IdentityGate";
import { Shelf } from "./view/Shelf";
import { Workspace } from "./view/Workspace";

type Screen = "shelf" | "workspace" | "report";

export default function App() {
  const { status, error, connect } = useConnection();
  const [screen, setScreen] = useState<Screen>("shelf");
  const [locale, setLocale] = useState<LocaleBundle | null>(null);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const [identityGate, setIdentityGate] = useState<{ folders: string[]; message: string } | null>(null);
  const prepare = useSession((s) => s.prepare);
  const closeSession = useSession((s) => s.close);
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
  }, [status]);

  const open = useCallback(
    async (paths: string[], identitySource?: string) => {
      setOpening(true);
      setOpenError(null);
      try {
        // The session is prepared against the scanned root, so the engine
        // resolves study folders exactly as `openrad prepare` would. `repo` is
        // left to the sidecar, which puts the session in the application's
        // workspace rather than inside the patient's folder.
        await prepare({
          studies_root: root,
          folders: paths,
          lang: "tr",
          ...(identitySource ? { identity_source: identitySource } : {}),
        });
        useViewer.setState({ panes: [], activePane: 0, pending: [] });
        setIdentityGate(null);
        setScreen("workspace");
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        // The engine refuses to compare exports whose patient identifiers
        // differ. That is a question for the reader, not an error to report.
        if (/patient identifier/i.test(message) || /identity evidence/i.test(message)) {
          setIdentityGate({ folders: paths, message });
        } else {
          setOpenError(message);
        }
      } finally {
        setOpening(false);
      }
    },
    [prepare, root],
  );

  if (status === "connecting") {
    return <Splash message="Motor bağlantısı kuruluyor…" />;
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
    <div className="relative h-full w-full">
      {openError && (
        <div className="absolute inset-x-0 top-0 z-50 bg-alarm-900 px-4 py-2 text-center text-xs text-alarm-400">
          {openError}
          <button type="button" className="ml-3 underline" onClick={() => setOpenError(null)}>
            kapat
          </button>
        </div>
      )}
      {screen === "shelf" && <Shelf onOpen={(paths) => void open(paths)} busy={opening} />}
      {identityGate && root && (
        <IdentityGate
          studiesRoot={root}
          folders={identityGate.folders}
          message={identityGate.message}
          onCancel={() => setIdentityGate(null)}
          onConfirm={(identitySource) => void open(identityGate.folders, identitySource)}
        />
      )}
      {screen === "workspace" && (
        <Workspace
          locale={locale}
          onBack={() => {
            closeSession();
            setScreen("shelf");
          }}
          onReport={() => setScreen("report")}
        />
      )}
      {screen === "report" && <ReportScreen locale={locale} onBack={() => setScreen("workspace")} />}
    </div>
  );
}

function Splash({ message, detail, action }: { message: string; detail?: string; action?: React.ReactNode }) {
  return (
    <div className="grid h-full place-items-center">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-4 h-6 w-6 rounded-full border border-amber-400/50 pulse-ring" />
        <p className="text-[13px] text-chalk-300">{message}</p>
        {detail && <p className="readout mt-2 text-[11px] leading-relaxed text-chalk-600">{detail}</p>}
        {action && <div className="mt-4">{action}</div>}
      </div>
    </div>
  );
}
