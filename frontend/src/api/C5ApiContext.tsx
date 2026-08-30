import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useC4 } from "./C4ApiContext";
import { HttpC5Api, type C5Api, type ReportViewerSession } from "./c5";

type C5ContextValue = { api: C5Api; viewerSession: ReportViewerSession | null; setViewerSession: (session: ReportViewerSession | null) => void };
const C5Context = createContext<C5ContextValue | null>(null);
export function C5ApiProvider({ children, api, baseUrl = import.meta.env.VITE_API_BASE_URL ?? "" }: { children: ReactNode; api?: C5Api; baseUrl?: string }) {
  const { accessToken } = useC4();
  const [viewerSession, setViewerSession] = useState<ReportViewerSession | null>(null);
  const resolvedApi = useMemo(() => api ?? new HttpC5Api(baseUrl, () => accessToken), [api, baseUrl, accessToken]);
  const value = useMemo(() => ({ api: resolvedApi, viewerSession, setViewerSession }), [resolvedApi, viewerSession]);
  return <C5Context.Provider value={value}>{children}</C5Context.Provider>;
}
export function useC5() { const value = useContext(C5Context); if (!value) throw new Error("useC5 must be used within C5ApiProvider"); return value; }
