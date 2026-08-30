import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { C3_LOCAL_ACTORS, HttpC3Api, type C3Api } from "./c3";
import { localFeaturesEnabled } from "../config/localFeatures";

type C3ContextValue = {
  api: C3Api;
  actorId: string;
  setActorId: (actorId: string) => void;
};

const C3Context = createContext<C3ContextValue | null>(null);

export function C3ApiProvider({ children, api, baseUrl = import.meta.env.VITE_API_BASE_URL ?? "" }: { children: ReactNode; api?: C3Api; baseUrl?: string }) {
  const [actorId, setActorId] = useState<string>(localFeaturesEnabled ? C3_LOCAL_ACTORS[0].actorId : "");
  const resolvedApi = useMemo(() => api ?? new HttpC3Api(baseUrl, actorId), [api, baseUrl, actorId]);
  const value = useMemo(() => ({ api: resolvedApi, actorId, setActorId }), [resolvedApi, actorId]);
  return <C3Context.Provider value={value}>{children}</C3Context.Provider>;
}

export function useC3() {
  const value = useContext(C3Context);
  if (!value) throw new Error("useC3 must be used within C3ApiProvider");
  return value;
}
