/** The study shelf: what was scanned, and which studies the reader picked. */
import { create } from "zustand";
import { api } from "../api/client";
import type { StudyCard } from "../api/types";

interface ShelfState {
  root: string | null;
  studies: StudyCard[];
  scanning: boolean;
  error: string | null;
  /** Study paths chosen for a single read or a chronological comparison. */
  selection: string[];
  scan: (root: string) => Promise<void>;
  toggle: (path: string) => void;
  clearSelection: () => void;
}

export const useShelf = create<ShelfState>((set, get) => ({
  root: null,
  studies: [],
  scanning: false,
  error: null,
  selection: [],

  scan: async (root) => {
    set({ scanning: true, error: null });
    try {
      const result = await api.scanStudies(root);
      set({ root: result.root, studies: result.studies, scanning: false, selection: [] });
    } catch (error) {
      set({ scanning: false, error: error instanceof Error ? error.message : String(error) });
    }
  },

  toggle: (path) => {
    const { selection, studies } = get();
    const next = selection.includes(path) ? selection.filter((p) => p !== path) : [...selection, path];
    // Chronology comes from DICOM StudyDate and StudyTime, never from the order
    // the reader clicked or from folder names.
    const order = new Map(studies.map((s) => [s.path, `${s.date}${s.time}`]));
    next.sort((a, b) => (order.get(a) ?? "").localeCompare(order.get(b) ?? ""));
    set({ selection: next });
  },

  clearSelection: () => set({ selection: [] }),
}));
