import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CatalogApiProvider } from "./api/CatalogApiContext";
import { C1ApiProvider } from "./api/C1ApiContext";
import { C2BApiProvider } from "./api/C2BApiContext";
import { C3ApiProvider } from "./api/C3ApiContext";
import { C4ApiProvider } from "./api/C4ApiContext";
import { C5ApiProvider } from "./api/C5ApiContext";
import { C6ApiProvider } from "./api/C6ApiContext";
import { App } from "./App";
import "./styles/global.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 10 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <CatalogApiProvider>
        <C1ApiProvider>
          <C2BApiProvider>
            <C3ApiProvider>
              <C4ApiProvider>
                <C5ApiProvider>
                  <C6ApiProvider>
                    <BrowserRouter>
                      <App />
                    </BrowserRouter>
                  </C6ApiProvider>
                </C5ApiProvider>
              </C4ApiProvider>
            </C3ApiProvider>
          </C2BApiProvider>
        </C1ApiProvider>
      </CatalogApiProvider>
    </QueryClientProvider>
  </StrictMode>,
);
