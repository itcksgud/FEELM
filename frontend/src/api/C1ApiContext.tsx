import { createContext, useContext, useMemo, type ReactNode } from "react";
import { HttpC1Api, type C1Api } from "./c1";

const C1ApiContext = createContext<C1Api | null>(null);

export function C1ApiProvider({ api, children }: { api?: C1Api; children: ReactNode }) {
  const resolvedApi = useMemo(() => api ?? new HttpC1Api(), [api]);
  return <C1ApiContext.Provider value={resolvedApi}>{children}</C1ApiContext.Provider>;
}

export function useC1Api(): C1Api {
  const api = useContext(C1ApiContext);
  if (!api) throw new Error("C1ApiProvider가 필요합니다.");
  return api;
}
