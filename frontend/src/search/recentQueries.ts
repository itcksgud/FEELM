const STORAGE_KEY = "feelm.catalog.recentQueries.v1";
const MAX_RECENT = 10;

export function readRecentQueries(storage: Storage = localStorage): string[] {
  try {
    const parsed = JSON.parse(storage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string").slice(0, MAX_RECENT)
      : [];
  } catch {
    return [];
  }
}

export function rememberQuery(query: string, storage: Storage = localStorage): string[] {
  const normalized = query.trim().slice(0, 100);
  if (!normalized) return readRecentQueries(storage);
  const next = [normalized, ...readRecentQueries(storage).filter((item) => item !== normalized)].slice(0, MAX_RECENT);
  storage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function removeRecentQuery(query: string, storage: Storage = localStorage): string[] {
  const next = readRecentQueries(storage).filter((item) => item !== query);
  storage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function clearRecentQueries(storage: Storage = localStorage) {
  storage.removeItem(STORAGE_KEY);
}
