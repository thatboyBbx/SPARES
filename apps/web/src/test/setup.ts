import "fake-indexeddb/auto";
import "@testing-library/jest-dom/vitest";

if (!globalThis.crypto?.randomUUID) {
  Object.defineProperty(globalThis, "crypto", {
    value: { randomUUID: () => `test-${Math.random().toString(16).slice(2)}` },
  });
}
