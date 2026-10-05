import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { db, fulfilTransfer, getSyncIssues, loadFromServer, pendingSyncCount, prepareOfflineWorkspace, recordSale, type Product } from "./database";

const product: Product = { id: "p1", sku: "SKU-1", name: "Brake pad", brand: "TRW", fitment: "Hilux", reorderLevel: 4, sellingPrice: 20 };
const bootstrap = { branches: [], products: [], categories: [], suppliers: [], customers: [], movements: [], transfers: [], sales: [], purchase_orders: [], purchase_order_lines: [], approvals: [], expenses: [], notifications: [], supplier_payments: [], journal_entries: [] };

describe("offline queue and conflict recovery", () => {
  beforeEach(async () => {
    vi.restoreAllMocks();
    await db.delete();
    await db.open();
    window.localStorage.clear();
    await prepareOfflineWorkspace("u1");
  });

  afterAll(async () => db.close());

  it("queues an offline sale exactly once and drains it after reconnection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await recordSale(product, "shop", 2, null);
    expect(await pendingSyncCount()).toBe(1);
    expect(await db.sales.count()).toBe(1);
    expect((await db.movements.toArray()).filter((movement) => movement.kind === "sale")).toHaveLength(1);

    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      const payload = url.endsWith("/bootstrap") ? bootstrap : {};
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    await loadFromServer();
    expect(await pendingSyncCount()).toBe(0);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/sales"))).toHaveLength(1);
  });

  it("surfaces stale-version conflicts as recoverable sync issues", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Transfer changed on another device" }), { status: 409, headers: { "Content-Type": "application/json" } })));
    await expect(fulfilTransfer("t1", "TRF-1", 1)).rejects.toThrow("Transfer changed on another device");
    expect(getSyncIssues()).toContainEqual(expect.objectContaining({ id: "t1", reference: "TRF-1", kind: "transfer" }));
  });

  it("rejects a server validation error without recording a false offline sale", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Insufficient stock" }), { status: 422, headers: { "Content-Type": "application/json" } })));
    await expect(recordSale(product, "shop", 2)).rejects.toThrow("Insufficient stock");
    expect(await pendingSyncCount()).toBe(0);
    expect(await db.sales.count()).toBe(0);
  });

  it("keeps a queued sale after repeated server failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await recordSale(product, "shop", 2);
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/sales")) return new Response(JSON.stringify({ detail: "Temporarily unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify(url.endsWith("/bootstrap") ? bootstrap : {}), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    for (let attempt = 0; attempt < 6; attempt++) await loadFromServer();
    expect(await pendingSyncCount()).toBe(1);
    expect((await db.outbox.toArray())[0].attempts).toBe(6);
  });
});
