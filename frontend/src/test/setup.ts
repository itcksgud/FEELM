import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  cleanup();
  server.resetHandlers();
  localStorage.clear();
  sessionStorage.clear();
});

afterAll(() => server.close());

Object.defineProperty(window, "scrollY", { configurable: true, writable: true, value: 0 });
window.scrollTo = vi.fn((optionsOrX?: ScrollToOptions | number, y?: number) => {
  const nextY = typeof optionsOrX === "number" ? (y ?? 0) : (optionsOrX?.top ?? 0);
  Object.defineProperty(window, "scrollY", { configurable: true, writable: true, value: nextY });
});
