import { request } from "../lib/api";

export type MovementKind = "receipt" | "sale" | "transfer_out" | "transfer_in";
export type TransferStatus = "received" | "in_transit";
export interface Branch { id: string; name: string; type: "warehouse" | "shop"; }
export interface Product { id: string; sku: string; name: string; brand: string; fitment: string; reorderLevel: number; sellingPrice: number; }
export interface Supplier { id: string; name: string; phone: string; }
export interface StockMovement { id: string; productId: string; branchId: string; quantity: number; kind: MovementKind; reference: string; createdAt: string; createdBy: string; }
export interface Transfer { id: string; productId: string; fromBranchId: string; toBranchId: string; quantity: number; status: TransferStatus; requestedAt: string; receivedAt?: string; }
export interface Sale { id: string; productId: string; branchId: string; quantity: number; total: number; receiptNumber: string; createdAt: string; }
export interface PurchaseOrder { id: string; reference: string; supplierId: string; branchId: string; status: string; notes: string; createdBy: string; requestedAt: string; receivedAt?: string; }
export interface PurchaseOrderLine { id: string; purchaseOrderId: string; productId: string; quantity: number; unitCost: number; createdAt: string; }
export interface Notification { id: string; title: string; body: string; kind: string; read: boolean; createdAt: string; }
interface OutboxEntry { id?: number; path: string; body: string; queuedAt: string; }

/** Small local cache and outbox using browser storage. The authoritative
 * records remain in the API database; this cache exists only for offline work. */
class LocalTable<T extends { id?: string | number }> {
  private readonly key: string;
  constructor(key: string) { this.key = key; }
  private read(): T[] { try { return JSON.parse(localStorage.getItem(this.key) ?? "[]") as T[]; } catch { return []; } }
  private write(rows: T[]) { localStorage.setItem(this.key, JSON.stringify(rows)); }
  async toArray() { return this.read(); }
  async clear() { this.write([]); }
  async count() { return this.read().length; }
  async add(row: T) { const copy = { ...row }; if (copy.id === undefined) copy.id = Date.now() + Math.floor(Math.random() * 1000); const rows = this.read(); rows.push(copy); this.write(rows); return copy.id; }
  async bulkPut(rows: T[]) { this.write(rows); }
  async bulkAdd(rows: T[]) { const next = this.read(); next.push(...rows); this.write(next); }
  async delete(id: string | number) { this.write(this.read().filter((row) => row.id !== id)); }
  orderBy<K extends keyof T>(key: K) { const sorted = this.read().sort((a, b) => String(a[key]).localeCompare(String(b[key]))); return { reverse: () => ({ toArray: async () => [...sorted].reverse() }) }; }
}
class SpopDatabase {
  branches = new LocalTable<Branch>("spop.branches"); products = new LocalTable<Product>("spop.products"); suppliers = new LocalTable<Supplier>("spop.suppliers"); movements = new LocalTable<StockMovement>("spop.movements"); transfers = new LocalTable<Transfer>("spop.transfers"); sales = new LocalTable<Sale>("spop.sales"); purchaseOrders = new LocalTable<PurchaseOrder>("spop.purchaseOrders"); purchaseOrderLines = new LocalTable<PurchaseOrderLine>("spop.purchaseOrderLines"); notifications = new LocalTable<Notification>("spop.notifications"); approvals = new LocalTable<{ id: string; type: string; subject: string; requester: string; approver: string; priority: string; status: string; createdAt: string }>("spop.approvals"); outbox = new LocalTable<OutboxEntry>("spop.outbox");
  async transaction(_mode: string, ...args: unknown[]) { const callback = args.at(-1); if (typeof callback === "function") await callback(); }
}
export const db = new SpopDatabase();

const localId = () => crypto.randomUUID();
const timestamp = () => new Date().toISOString();
async function queue(path: string, body: object) { await db.outbox.add({ path, body: JSON.stringify(body), queuedAt: timestamp() }); }
export async function pendingSyncCount() { return db.outbox.count(); }
async function replayOutbox() {
  const entries = await db.outbox.toArray();
  for (const entry of entries) {
    await request(entry.path, { method: "POST", body: entry.body });
    if (entry.id !== undefined) await db.outbox.delete(entry.id);
  }
}

export async function loadFromServer() {
  try { await replayOutbox(); } catch { /* Keep entries queued until the next successful connection. */ }
  const data = (await request("/bootstrap")) as {
    branches: Array<{ id: string; name: string; kind: "warehouse" | "shop" }>;
    products: Array<{ id: string; sku: string; name: string; brand: string; fitment: string; reorder_level: number; selling_price: number }>;
    suppliers: Supplier[];
    movements: Array<{ id: string; product_id: string; branch_id: string; quantity: number; kind: MovementKind; reference: string; created_at: string; created_by: string }>;
    transfers: Array<{ id: string; product_id: string; from_branch_id: string; to_branch_id: string; quantity: number; status: TransferStatus; requested_at: string; received_at?: string }>;
    sales: Array<{ id: string; product_id: string; branch_id: string; quantity: number; total: number; receipt_number: string; created_at: string }>;
    purchase_orders: Array<{ id: string; reference: string; supplier_id: string; branch_id: string; status: string; notes: string; created_by: string; requested_at: string; received_at?: string }>;
    purchase_order_lines: Array<{ id: string; purchase_order_id: string; product_id: string; quantity: number; unit_cost: number; created_at: string }>;
    approvals: Array<{ id: string; type: string; subject: string; requester: string; approver: string; priority: string; status: string; created_at: string }>;
    notifications: Array<{ id: string; title: string; body: string; kind: string; read: boolean; created_at: string }>;
  };
  await db.transaction("rw", db.branches, db.products, db.suppliers, db.movements, db.transfers, db.sales, db.purchaseOrders, db.purchaseOrderLines, db.notifications, db.approvals, async () => {
    await Promise.all([db.branches.clear(), db.products.clear(), db.suppliers.clear(), db.movements.clear(), db.transfers.clear(), db.sales.clear(), db.purchaseOrders.clear(), db.purchaseOrderLines.clear(), db.notifications.clear(), db.approvals.clear()]);
    await db.branches.bulkPut(data.branches.map((b) => ({ id: b.id, name: b.name, type: b.kind })));
    await db.products.bulkPut(data.products.map((p) => ({ id: p.id, sku: p.sku, name: p.name, brand: p.brand, fitment: p.fitment, reorderLevel: p.reorder_level, sellingPrice: p.selling_price })));
    await db.suppliers.bulkPut(data.suppliers.map((s) => s));
    await db.movements.bulkPut(data.movements.map((m) => ({ id: m.id, productId: m.product_id, branchId: m.branch_id, quantity: m.quantity, kind: m.kind, reference: m.reference, createdAt: m.created_at, createdBy: m.created_by })));
    await db.transfers.bulkPut(data.transfers.map((t) => ({ id: t.id, productId: t.product_id, fromBranchId: t.from_branch_id, toBranchId: t.to_branch_id, quantity: t.quantity, status: t.status, requestedAt: t.requested_at, receivedAt: t.received_at })));
    await db.sales.bulkPut(data.sales.map((s) => ({ id: s.id, productId: s.product_id, branchId: s.branch_id, quantity: s.quantity, total: s.total, receiptNumber: s.receipt_number, createdAt: s.created_at })));
    await db.purchaseOrders.bulkPut(data.purchase_orders.map((order) => ({ id: order.id, reference: order.reference, supplierId: order.supplier_id, branchId: order.branch_id, status: order.status, notes: order.notes, createdBy: order.created_by, requestedAt: order.requested_at, receivedAt: order.received_at })));
    await db.purchaseOrderLines.bulkPut(data.purchase_order_lines.map((line) => ({ id: line.id, purchaseOrderId: line.purchase_order_id, productId: line.product_id, quantity: line.quantity, unitCost: line.unit_cost, createdAt: line.created_at })));
    await db.approvals.bulkPut(data.approvals.map((approval) => ({ id: approval.id, type: approval.type, subject: approval.subject, requester: approval.requester, approver: approval.approver, priority: approval.priority, status: approval.status, createdAt: approval.created_at })));
    await db.notifications.bulkPut(data.notifications.map((notification) => ({ id: notification.id, title: notification.title, body: notification.body, kind: notification.kind, read: notification.read, createdAt: notification.created_at })));
  });
}
export async function recordReceipt(productId: string, branchId: string, quantity: number, supplierId: string) {
  const body = { product_id: productId, branch_id: branchId, supplier_id: supplierId, quantity, client_request_id: localId() };
  try { await request("/receipts", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { const reference = `OFFLINE-GRN-${Date.now()}`; await db.transaction("rw", db.movements, db.outbox, async () => { await db.movements.add({ id: localId(), productId, branchId, quantity, kind: "receipt", reference, createdAt: timestamp(), createdBy: "Offline operator" }); await queue("/receipts", body); }); }
}
export async function recordSale(product: Product, branchId: string, quantity: number) {
  const body = { product_id: product.id, branch_id: branchId, quantity, client_request_id: localId() };
  try { await request("/sales", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { const reference = `OFFLINE-S-${Date.now()}`; await db.transaction("rw", db.movements, db.sales, db.outbox, async () => { await db.movements.add({ id: localId(), productId: product.id, branchId, quantity: -quantity, kind: "sale", reference, createdAt: timestamp(), createdBy: "Offline operator" }); await db.sales.add({ id: localId(), productId: product.id, branchId, quantity, total: product.sellingPrice * quantity, receiptNumber: reference, createdAt: timestamp() }); await queue("/sales", body); }); }
}
export async function recordTransfer(productId: string, fromBranchId: string, toBranchId: string, quantity: number) {
  const body = { product_id: productId, from_branch_id: fromBranchId, to_branch_id: toBranchId, quantity, client_request_id: localId() };
  try { await request("/transfers", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { const reference = `OFFLINE-TRF-${Date.now()}`; await db.transaction("rw", db.movements, db.transfers, db.outbox, async () => { await db.transfers.add({ id: reference, productId, fromBranchId, toBranchId, quantity, status: "in_transit", requestedAt: timestamp() }); await db.movements.bulkAdd([{ id: localId(), productId, branchId: fromBranchId, quantity: -quantity, kind: "transfer_out", reference, createdAt: timestamp(), createdBy: "Offline operator" }, { id: localId(), productId, branchId: toBranchId, quantity, kind: "transfer_in", reference, createdAt: timestamp(), createdBy: "Offline operator" }]); await queue("/transfers", body); }); }
}
export async function createApproval(type: string, subject: string, requester: string, approver: string, priority: string) { await request("/approvals", { method: "POST", body: JSON.stringify({ type, subject, requester, approver, priority }) }); await loadFromServer(); }
export async function createExpense(branchId: string, category: string, amount: number, description: string) { await request("/expenses", { method: "POST", body: JSON.stringify({ branch_id: branchId, category, amount, description }) }); await loadFromServer(); }
export async function createPurchaseOrder(supplierId: string, branchId: string, notes: string, items: Array<{ productId: string; quantity: number; unitCost: number }>) {
  const body = { supplier_id: supplierId, branch_id: branchId, notes, items: items.map((item) => ({ product_id: item.productId, quantity: item.quantity, unit_cost: item.unitCost })) };
  try { await request("/purchase-orders", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { await queue("/purchase-orders", body); }
}
export async function receivePurchaseOrder(orderId: string) {
  try { await request(`/purchase-orders/${orderId}/receive`, { method: "POST" }); await loadFromServer(); }
  catch { await queue(`/purchase-orders/${orderId}/receive`, {}); }
}
export async function markNotificationRead(notificationId: string) {
  try { await request(`/notifications/${notificationId}/read`, { method: "POST" }); await loadFromServer(); }
  catch { /* keep cached state */ }
}
