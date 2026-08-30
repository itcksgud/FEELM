import { createContext, useContext, useMemo, type ReactNode } from "react";
import { HttpC2BApi, type C2BApi } from "./c2b";

const C2BApiContext = createContext<C2BApi | null>(null);

export function C2BApiProvider({ api, children }: { api?: C2BApi; children: ReactNode }) {
  const resolvedApi = useMemo(() => api ?? new HttpC2BApi(), [api]);
  return <C2BApiContext.Provider value={resolvedApi}>{children}</C2BApiContext.Provider>;
}

export function useC2BApi(): C2BApi {
  const api = useContext(C2BApiContext);
  if (!api) throw new Error("C2BApiProvider가 필요합니다.");
  return api;
}
