import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { CatalogApiProvider } from "../api/CatalogApiContext";
import { HttpCatalogApi } from "../api/catalog";
import { C1ApiProvider } from "../api/C1ApiContext";
import { HttpC1Api } from "../api/c1";
import { C2BApiProvider } from "../api/C2BApiContext";
import { HttpC2BApi } from "../api/c2b";
import { C3ApiProvider } from "../api/C3ApiContext";
import { C4ApiProvider } from "../api/C4ApiContext";
import { C5ApiProvider } from "../api/C5ApiContext";
import { C6ApiProvider } from "../api/C6ApiContext";
import { HttpC6Api } from "../api/c6";

export function renderCatalog(ui: ReactElement, initialEntries: string[] = ["/search"], options: { c4Token?: string } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
    },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <CatalogApiProvider api={new HttpCatalogApi("http://localhost")}>
          <C1ApiProvider api={new HttpC1Api("http://localhost", "test-c1-owner-token")}>
            <C2BApiProvider api={new HttpC2BApi("http://localhost", "test-c1-owner-token")}>
              <C3ApiProvider baseUrl="http://localhost">
                <C4ApiProvider baseUrl="http://localhost" initialAccessToken={options.c4Token ?? null}>
                  <C5ApiProvider baseUrl="http://localhost">
                    <C6ApiProvider api={new HttpC6Api("http://localhost", "test-c1-owner-token")}>
                      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
                    </C6ApiProvider>
                  </C5ApiProvider>
                </C4ApiProvider>
              </C3ApiProvider>
            </C2BApiProvider>
          </C1ApiProvider>
        </CatalogApiProvider>
      </QueryClientProvider>,
    ),
  };
}
