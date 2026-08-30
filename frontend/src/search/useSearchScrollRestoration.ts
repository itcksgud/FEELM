import { useEffect, useRef } from "react";

const PREFIX = "feelm.catalog.scroll.";

export function useSearchScrollRestoration(key: string, ready: boolean) {
  const restored = useRef(false);

  useEffect(() => {
    if (!ready || restored.current) return;
    restored.current = true;
    const saved = Number(sessionStorage.getItem(PREFIX + key) ?? 0);
    if (Number.isFinite(saved) && saved > 0) requestAnimationFrame(() => window.scrollTo(0, saved));
  }, [key, ready]);

  useEffect(
    () => () => {
      sessionStorage.setItem(PREFIX + key, String(window.scrollY));
    },
    [key],
  );
}
