/**
 * The few places the application needs to know whether it is inside Tauri.
 *
 * Kept behind a narrow interface so the whole interface runs in a plain browser
 * for development, with a typed path instead of a native dialog.
 */
export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

type DialogModule = { open: (options: Record<string, unknown>) => Promise<string | string[] | null> };

/** Native folder picker under Tauri; a prompt in the browser. */
export async function pickFolder(title: string): Promise<string | null> {
  if (inTauri()) {
    try {
      const dialog = (await import(/* @vite-ignore */ "@tauri-apps/plugin-dialog")) as unknown as DialogModule;
      const selected = await dialog.open({ directory: true, multiple: false, title });
      return typeof selected === "string" ? selected : null;
    } catch {
      /* fall through to the prompt */
    }
  }
  const typed = window.prompt(title);
  return typed && typed.trim() ? typed.trim() : null;
}

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0";
  const units = ["B", "KB", "MB", "GB"];
  const power = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** power).toFixed(power >= 2 ? 1 : 0)} ${units[power]}`;
}

/** DICOM StudyDate (YYYYMMDD) as a Turkish date. */
export function formatStudyDate(raw: string): string {
  if (!/^\d{8}$/.test(raw)) return raw || "—";
  return `${raw.slice(6, 8)}.${raw.slice(4, 6)}.${raw.slice(0, 4)}`;
}

export function formatStudyTime(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (digits.length < 4) return "";
  return `${digits.slice(0, 2)}:${digits.slice(2, 4)}`;
}
