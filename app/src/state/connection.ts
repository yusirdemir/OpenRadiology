/** Sidecar connection: readiness, engine diagnostics and the attestation policy. */
import { create } from "zustand";
import { api } from "../api/client";
import { DEFAULT_POLICY, type DwellPolicy } from "../lib/attestation";
import type { WindowPreset } from "../api/types";

interface ConnectionState {
  status: "connecting" | "ready" | "failed";
  version: string;
  error: string | null;
  policy: DwellPolicy;
  regions: Record<string, string[]>;
  windows: WindowPreset[];
  cacheUsedBytes: number;
  cacheBudgetBytes: number;
  connect: () => Promise<void>;
  refreshStatus: () => Promise<void>;
}

export const useConnection = create<ConnectionState>((set) => ({
  status: "connecting",
  version: "",
  error: null,
  policy: DEFAULT_POLICY,
  regions: {},
  windows: [],
  cacheUsedBytes: 0,
  cacheBudgetBytes: 0,

  connect: async () => {
    set({ status: "connecting", error: null });
    try {
      const health = await api.health();
      const status = await api.status();
      set({
        status: "ready",
        version: health.version,
        error: null,
        policy: {
          minDwellMs: status.policy.min_dwell_ms,
          minScale: status.policy.min_scale,
          requireFocus: status.policy.require_focus,
        },
        regions: status.regions,
        windows: status.windows,
        cacheUsedBytes: status.cache.used_bytes,
        cacheBudgetBytes: status.cache.budget_bytes,
      });
    } catch (error) {
      set({ status: "failed", error: error instanceof Error ? error.message : String(error) });
    }
  },

  refreshStatus: async () => {
    try {
      const status = await api.status();
      set({ cacheUsedBytes: status.cache.used_bytes, cacheBudgetBytes: status.cache.budget_bytes });
    } catch {
      /* a status poll failing is not worth surfacing */
    }
  },
}));
