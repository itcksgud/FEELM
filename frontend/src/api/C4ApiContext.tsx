import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { HttpC4Api, type AuthenticationResult, type C4Api, type MyMembership } from "./c4";

type C4ContextValue = {
  api: C4Api;
  accessToken: string | null;
  membership: MyMembership | null;
  acceptAuthentication: (result: AuthenticationResult) => void;
  clearSession: () => void;
  restoreSession: () => Promise<boolean>;
};

const C4Context = createContext<C4ContextValue | null>(null);

export function C4ApiProvider({ children, api, baseUrl = import.meta.env.VITE_API_BASE_URL ?? "", initialAccessToken = null }: { children: ReactNode; api?: C4Api; baseUrl?: string; initialAccessToken?: string | null }) {
  const [accessToken, setAccessToken] = useState<string | null>(initialAccessToken);
  const [membership, setMembership] = useState<MyMembership | null>(null);
  const tokenRef = useRef(accessToken);
  tokenRef.current = accessToken;
  const resolvedApi = useMemo(() => api ?? new HttpC4Api(baseUrl, () => tokenRef.current), [api, baseUrl]);
  const acceptAuthentication = useCallback((authentication: AuthenticationResult) => {
    tokenRef.current = authentication.accessToken;
    setAccessToken(authentication.accessToken);
    setMembership(authentication.membership);
  }, []);
  const clearSession = useCallback(() => {
    tokenRef.current = null;
    setAccessToken(null);
    setMembership(null);
  }, []);
  const restoreSession = useCallback(async () => {
    try { acceptAuthentication(await resolvedApi.refresh()); return true; }
    catch { clearSession(); return false; }
  }, [resolvedApi, acceptAuthentication, clearSession]);
  const value = useMemo(() => ({ api: resolvedApi, accessToken, membership, acceptAuthentication, clearSession, restoreSession }), [resolvedApi, accessToken, membership, acceptAuthentication, clearSession, restoreSession]);
  return <C4Context.Provider value={value}>{children}</C4Context.Provider>;
}

export function useC4() {
  const value = useContext(C4Context);
  if (!value) throw new Error("useC4 must be used within C4ApiProvider");
  return value;
}
