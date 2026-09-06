/**
 * The study shelf: what was scanned, and which studies the reader picked.
 *
 * The selection is capped at two on purpose. A review here is either one
 * examination read on its own, or one examination read against exactly one
 * earlier examination. Three columns of images side by side is not a third
 * mode, it is a worse version of both -- so a third pick does not fail, it
 * simply replaces the older of the two already chosen.
 */
import { create } from "zustand";
import { api } from "../api/client";
import type { StudyCard } from "../api/types";

export const MAX_SELECTION = 2;

interface ShelfState {
  root: string | null;
  studies: StudyCard[];
  scanning: boolean;
  error: string | null;
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
    // Chronology comes from DICOM StudyDate and StudyTime, never from the order
    // the reader clicked or from folder names.
    const order = new Map(studies.map((s) => [s.path, `${s.date}${s.time}`]));
    const chronological = (paths: string[]) =>
      [...paths].sort((a, b) => (order.get(a) ?? "").localeCompare(order.get(b) ?? ""));

    if (selection.includes(path)) {
      set({ selection: chronological(selection.filter((p) => p !== path)) });
      return;
    }
    if (selection.length < MAX_SELECTION) {
      set({ selection: chronological([...selection, path]) });
      return;
    }
    // Full: keep the more recent of the two and let the new pick join it.
    const kept = chronological(selection).slice(1);
    set({ selection: chronological([...kept, path]) });
  },

  clearSelection: () => set({ selection: [] }),
}));
