import { createContext, useContext, useMemo, type ReactNode } from "react";
import { HttpC6Api, type C6Api } from "./c6";

const C6ApiContext = createContext<C6Api | null>(null);

export function C6ApiProvider({ api, children }: { api?: C6Api; children: ReactNode }) {
  const resolvedApi = useMemo(() => api ?? new HttpC6Api(), [api]);
  return <C6ApiContext.Provider value={resolvedApi}>{children}</C6ApiContext.Provider>;
}

export function useC6Api(): C6Api {
  const api = useContext(C6ApiContext);
  if (!api) throw new Error("C6ApiProvider가 필요합니다.");
  return api;
}
