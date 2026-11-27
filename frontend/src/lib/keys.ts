import { useEffect, useRef } from "react";

/**
 * Keyboard-first shortcuts (J/K next and previous, A approve, E edit, / search, ? help).
 * Ignored while typing in a field, and while a modal dialog is open.
 */
export function useShortcuts(map: Record<string, () => void>, enabled = true) {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (document.querySelector("[role=dialog]")) return;
      const fn = ref.current[e.key];
      if (fn) {
        e.preventDefault();
        fn();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

export const SHORTCUTS: [string, string][] = [
  ["J / K", "Next / previous item"],
  ["Enter", "Open the selected item"],
  ["A", "Approve the open item"],
  ["E", "Edit the suggested fix"],
  ["/", "Search Rx, patient ID or prescriber"],
  ["?", "Show all shortcuts"],
];
