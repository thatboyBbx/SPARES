/// <reference lib="webworker" />
import { cleanupOutdatedCaches, precacheAndRoute } from "workbox-precaching";

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<string | { url: string; revision: string | null }>;
};

precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Placeholder push handler: Phase 7 wires real FCM payloads through here and
// surfaces them as a Notification; until then this is a documented no-op
// rather than an unhandled event.
self.addEventListener("push", (event) => {
  if (!event.data) return;
  const payload = event.data.json() as { title?: string; body?: string };
  event.waitUntil(
    self.registration.showNotification(payload.title ?? "SparePilot", {
      body: payload.body ?? "",
    }),
  );
});
