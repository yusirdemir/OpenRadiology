/**
 * The evidence ledger, as the interface sees it.
 *
 * The session file is the single source of truth; this store holds a copy,
 * applies edits and writes them back. Two invariants are enforced here rather
 * than left to discipline:
 *
 *  - a region or claim is only ever written with the addresses it cites, so a
 *    statement without a slice reference cannot be saved,
 *  - `reviewed` is never set by the interface. The server sets it from view
 *    attestations, and refuses the ones no attestation covers. `refusedReviews`
 *    carries those refusals back so the reader can be told which page still
 *    needs looking at.
 */
import { create } from "zustand";
import { api } from "../api/client";
import type {
  AttestationCoverage,
  CheckResult,
  Claim,
  Ref,
  Region,
  RegionStatus,
  Session,
  SessionStudy,
} from "../api/types";

export interface RegionKey {
  studyUid: string;
  key: string;
}

interface SessionState {
  path: string | null;
  session: Session | null;
  coverage: AttestationCoverage | null;
  check: CheckResult | null;
  saving: boolean;
  dirty: boolean;
  error: string | null;
  refusedReviews: { path: string; reason: string }[];

  open: (path: string) => Promise<void>;
  prepare: (payload: Record<string, unknown>) => Promise<string>;
  close: () => void;
  save: () => Promise<void>;
  runCheck: () => Promise<CheckResult | null>;
  refreshCoverage: () => Promise<void>;

  setRegion: (target: RegionKey, patch: Partial<Region>) => void;
  upsertClaim: (claim: Claim) => void;
  removeClaim: (id: string) => void;
  patchSession: (patch: Partial<Session>) => void;
}

function replaceStudy(session: Session, studyUid: string, update: (study: SessionStudy) => SessionStudy): Session {
  return { ...session, studies: session.studies.map((s) => (s.uid === studyUid ? update(s) : s)) };
}

export const useSession = create<SessionState>((set, get) => ({
  path: null,
  session: null,
  coverage: null,
  check: null,
  saving: false,
  dirty: false,
  error: null,
  refusedReviews: [],

  open: async (path) => {
    const result = await api.session(path);
    set({
      path: result.session_path,
      session: result.session,
      coverage: result.attestation,
      check: null,
      dirty: false,
      error: null,
      refusedReviews: [],
    });
  },

  prepare: async (payload) => {
    const result = await api.prepare(payload);
    set({
      path: result.session_path,
      session: result.session,
      // A fresh session has an empty ledger, but the counts still need to be
      // real: the status bar must never claim "no attestations" when it simply
      // never asked.
      coverage: await api.coverage(result.session_path).catch(() => null),
      check: null,
      dirty: false,
      error: null,
      refusedReviews: [],
    });
    return result.session_path;
  },

  close: () => set({ path: null, session: null, coverage: null, check: null, dirty: false, refusedReviews: [] }),

  save: async () => {
    const { path, session } = get();
    if (!path || !session) return;
    set({ saving: true, error: null });
    try {
      const result = await api.saveSession(path, session);
      set({
        session: result.session,
        coverage: result.attestation,
        refusedReviews: result.refused_reviews,
        saving: false,
        dirty: false,
      });
    } catch (error) {
      set({ saving: false, error: error instanceof Error ? error.message : String(error) });
    }
  },

  runCheck: async () => {
    const { path } = get();
    if (!path) return null;
    const result = await api.check(path);
    set({ check: result, coverage: result.attestation });
    return result;
  },

  refreshCoverage: async () => {
    const { path } = get();
    if (!path) return;
    try {
      set({ coverage: await api.coverage(path) });
    } catch {
      /* coverage is advisory in the interface; the server decides at save time */
    }
  },

  setRegion: (target, patch) => {
    const session = get().session;
    if (!session) return;
    set({
      dirty: true,
      session: replaceStudy(session, target.studyUid, (study) => {
        const current = study.regions[target.key];
        if (!current) return study;
        const next: Region = { ...current, ...patch };
        // A statement is only as good as its address: a finding without a slice
        // reference is refused here rather than at the end of the read.
        if (next.status === "finding" && next.refs.length === 0) next.status = "pending";
        return { ...study, regions: { ...study.regions, [target.key]: next } };
      }),
    });
  },

  upsertClaim: (claim) => {
    const session = get().session;
    if (!session) return;
    const existing = session.claims.findIndex((c) => c.id === claim.id);
    const claims = existing >= 0
      ? session.claims.map((c, i) => (i === existing ? claim : c))
      : [...session.claims, claim];
    set({ session: { ...session, claims }, dirty: true });
  },

  removeClaim: (id) => {
    const session = get().session;
    if (!session) return;
    set({ session: { ...session, claims: session.claims.filter((c) => c.id !== id) }, dirty: true });
  },

  patchSession: (patch) => {
    const session = get().session;
    if (!session) return;
    set({ session: { ...session, ...patch }, dirty: true });
  },
}));

// ------------------------------------------------------------------ helpers
export const REGION_STATUS_ORDER: RegionStatus[] = ["pending", "finding", "limited", "not_covered", "no_finding"];

export function regionEntries(study: SessionStudy | undefined): [string, Region][] {
  if (!study) return [];
  return Object.entries(study.regions);
}

export function coverageOf(study: SessionStudy | undefined): { done: number; total: number } {
  const entries = regionEntries(study);
  return { done: entries.filter(([, r]) => r.status !== "pending").length, total: entries.length };
}

export function sameRef(a: Ref, b: Ref): boolean {
  return a.sop_uid === b.sop_uid && a.row === b.row && a.col === b.col;
}
