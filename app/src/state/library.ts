/**
 * The library: reviews already saved on this machine.
 *
 * There is no "list my sessions" endpoint, and adding one would have been the
 * wrong trade: every saved review records the SHA-256 of every engine file that
 * produced it, so a new route in the sidecar retroactively invalidates the
 * integrity check of every review already on disk. Rather than break that for
 * the sake of a list, the list is assembled here out of two routes the sidecar
 * already has -- a directory listing and a file read.
 *
 * A session file is a megabyte or two of page and attestation bookkeeping, and
 * a card needs about twenty fields of it, so each one is read once, reduced to
 * a card, and remembered against the file's modification time. Opening the
 * library a second time costs nothing until a review actually changes.
 */
import { create } from "zustand";
import { api, fetchText } from "../api/client";
import type { Session, SessionCard } from "../api/types";

const CACHE_KEY = "openrad.library.v1";
const NAME_KEY = "openrad.library.names.v1";
const RELATIVE_DIR = ".cache/create-report";
const SESSION_GLOB = "*/session.json";
const MOST_RECENT = 16;
const CONCURRENCY = 4;

interface LibraryState {
  cards: SessionCard[];
  loading: boolean;
  loaded: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

// ------------------------------------------------------------------- cache
type Cache = Record<string, SessionCard>;

function readCache(): Cache {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? (JSON.parse(raw) as Cache) : {};
  } catch {
    return {};
  }
}

function writeCache(cache: Cache): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
  } catch {
    /* a full or disabled store costs a re-read, nothing more */
  }
}

// ---------------------------------------------------------------- summarise
/** One saved review, reduced to what a card shows. Counts, never judgements. */
function summarise(path: string, modified: number, session: Session): SessionCard {
  const studies = session.studies
    .map((study) => ({
      uid: study.uid,
      path: study.path,
      folder: study.folder,
      date: study.date,
      time: study.time,
      modalities: study.modalities ?? [],
      series: study.series?.length ?? 0,
    }))
    .sort((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));

  const verdicts: Record<string, number> = {};
  for (const claim of session.claims ?? []) {
    const status = claim.comparison?.status || "present";
    verdicts[status] = (verdicts[status] ?? 0) + 1;
  }

  return {
    path,
    modified,
    mode: session.mode,
    language: session.language ?? "tr",
    reader: session.reader ?? "",
    reading_complete: Boolean(session.reading_complete),
    finalized: Boolean(session.finalized_utc),
    report_file: session.report_file ?? "",
    guide_file: session.guide_file ?? "",
    studies,
    claims: session.claims?.length ?? 0,
    verdicts,
    comparison_status: session.comparison?.status ?? "",
    patient_context: session.patient_context ?? "",
  };
}

/** Run promises a few at a time; twenty megabytes at once helps nobody. */
async function pooled<T, R>(items: T[], limit: number, run: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = [];
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) {
      const index = cursor++;
      const item = items[index];
      if (item === undefined) return;
      out[index] = await run(item);
    }
  });
  await Promise.all(workers);
  return out;
}

async function discover(): Promise<SessionCard[]> {
  // Every folder the sidecar has opened this run is a place a review can live:
  // the directory it was started in, the archive the reader opened, and the
  // application workspace once a session has been prepared there.
  const status = await api.status();
  const directories = [...new Set(status.roots.map((root) => `${root.replace(/\/$/, "")}/${RELATIVE_DIR}`))];

  const listings = await Promise.all(
    directories.map((directory) =>
      api
        .filesList(directory, SESSION_GLOB)
        // A root with no reviews in it -- or one outside the path jail -- is
        // not an error, it is simply a place with nothing to list.
        .then((result) => result.entries)
        .catch(() => []),
    ),
  );

  const found = new Map<string, number>();
  for (const entries of listings) {
    for (const entry of entries) {
      if (!entry.dir) found.set(entry.path, entry.modified);
    }
  }

  const recent = [...found.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, MOST_RECENT);

  const cache = readCache();
  const next: Cache = {};
  const cards = await pooled(recent, CONCURRENCY, async ([path, modified]) => {
    const key = `${path}@${Math.round(modified)}`;
    const hit = cache[key];
    if (hit) {
      next[key] = hit;
      return hit;
    }
    try {
      const session = JSON.parse(await fetchText(path)) as Session;
      const card = summarise(path, modified, session);
      next[key] = card;
      return card;
    } catch {
      return null;
    }
  });

  writeCache(next);
  return cards.filter((card): card is SessionCard => card !== null);
}

/**
 * The name a reader gives a review.
 *
 * "Babam, 3 Eylül" is what makes a list of dates usable, and it is exactly the
 * kind of thing that must never be written into the session file: the ledger is
 * a sealed, hash-checked record of what the engine did, and a nickname is not
 * evidence. So names live in this browser, keyed by the session's path, and the
 * document on disk stays untouched.
 */
function readNames(): Record<string, string> {
  try {
    const raw = localStorage.getItem(NAME_KEY);
    return raw ? (JSON.parse(raw) as Record<string, string>) : {};
  } catch {
    return {};
  }
}

export function reviewName(path: string): string {
  return readNames()[path] ?? "";
}

export function setReviewName(path: string, name: string): void {
  try {
    const names = readNames();
    if (name.trim()) names[path] = name.trim();
    else delete names[path];
    localStorage.setItem(NAME_KEY, JSON.stringify(names));
  } catch {
    /* a browser that refuses storage simply shows dates, which still work */
  }
}

export const useLibrary = create<LibraryState>((set) => ({
  cards: [],
  loading: false,
  loaded: false,
  error: null,

  refresh: async () => {
    set({ loading: true, error: null });
    try {
      set({ cards: await discover(), loading: false, loaded: true });
    } catch (error) {
      set({
        loading: false,
        loaded: true,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  },
}));

/** A review worth putting on a card: it was read, and it says something. */
export function isReadable(card: SessionCard): boolean {
  return card.claims > 0;
}
