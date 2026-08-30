import { createContext, useContext, useMemo, type ReactNode } from "react";
import { HttpCatalogApi, type CatalogApi } from "./catalog";

const CatalogApiContext = createContext<CatalogApi | null>(null);

export function CatalogApiProvider({ api, children }: { api?: CatalogApi; children: ReactNode }) {
  const resolvedApi = useMemo(() => api ?? new HttpCatalogApi(), [api]);
  return <CatalogApiContext.Provider value={resolvedApi}>{children}</CatalogApiContext.Provider>;
}

export function useCatalogApi(): CatalogApi {
  const api = useContext(CatalogApiContext);
  if (!api) throw new Error("CatalogApiProvider가 필요합니다.");
  return api;
}
