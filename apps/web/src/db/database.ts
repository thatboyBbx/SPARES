import Dexie, { type Table } from "dexie";
import { ApiError, getDeviceId, request } from "../lib/api";

/** Only present when the deployment has real Web Push configured — kept
 * unset in this environment, so the "Enable push notifications" button
 * stays hidden rather than prompting for a permission nothing backs. */
export function pushNotificationsAvailable(): boolean {
  return Boolean(import.meta.env.VITE_FIREBASE_VAPID_KEY);
}

export async function enablePushNotifications(): Promise<void> {
  const vapidKey = import.meta.env.VITE_FIREBASE_VAPID_KEY as string | undefined;
  if (!vapidKey || !("serviceWorker" in navigator) || !("PushManager" in window)) {
    throw new Error("Push notifications are not available on this device.");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notification permission was not granted.");
  }
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey });
  await request("/notifications/push-token", { method: "POST", body: JSON.stringify({ device_id: getDeviceId(), push_token: subscription.endpoint }) });
}

export type MovementKind = "receipt" | "sale" | "transfer_out" | "transfer_in";
export type TransferStatus = "requested" | "in_transit" | "received" | "cancelled";
export interface Branch { id: string; name: string; type: "warehouse" | "shop"; }
export interface Product { id: string; sku: string; name: string; brand: string; fitment: string; reorderLevel: number; sellingPrice: number; categoryId?: string | null; }
export interface Category { id: string; name: string; parentId: string | null; }
export interface Supplier { id: string; name: string; phone: string; }
export interface Customer { id: string; name: string; phone: string; }
export interface StockMovement { id: string; productId: string; branchId: string; quantity: number; kind: MovementKind; reference: string; createdAt: string; createdBy: string; }
export interface Transfer { id: string; reference: string; productId: string; fromBranchId: string; toBranchId: string; quantity: number; status: TransferStatus; version: number; requestedAt: string; fulfilledAt?: string | null; receivedAt?: string | null; }
export interface Sale { id: string; productId: string; branchId: string; customerId?: string | null; quantity: number; total: number; receiptNumber: string; createdAt: string; }
export interface PurchaseOrder { id: string; reference: string; supplierId: string; branchId: string; status: string; version: number; notes: string; createdBy: string; requestedAt: string; receivedAt?: string; }
export interface PurchaseOrderLine { id: string; purchaseOrderId: string; productId: string; quantity: number; unitCost: number; createdAt: string; }
export interface Notification { id: string; title: string; body: string; kind: string; read: boolean; createdAt: string; }
export interface Approval { id: string; type: string; subject: string; requester: string; approver: string; priority: string; status: string; deadline?: string | null; isOverdue?: boolean; relatedEntityType?: string | null; relatedEntityId?: string | null; createdAt: string; }
export interface Expense { id: string; branchId: string; category: string; amount: number; description: string; status: string; createdAt: string; }
interface OutboxEntry { id?: number; path: string; body: string; queuedAt: string; attempts: number; }

/** A conflict (409) surfaced by a version-checked action, kept client-side
 * until the operator acknowledges it. Not a full merge UI: the mutation
 * that produced the 409 never applied server-side, so "resolving" an entry
 * here only means "I've seen this, refresh and try again." */
export interface SyncIssue { id: string; kind: "transfer" | "purchase_order"; reference: string; message: string; }
let syncIssues: SyncIssue[] = [];
export function getSyncIssues(): SyncIssue[] { return syncIssues; }
export function dismissSyncIssue(id: string) { syncIssues = syncIssues.filter((issue) => issue.id !== id); }
function recordSyncIssue(issue: SyncIssue) { syncIssues = [issue, ...syncIssues.filter((existing) => existing.id !== issue.id)]; }

class SpopDatabase extends Dexie {
  branches!: Table<Branch, string>;
  products!: Table<Product, string>;
  categories!: Table<Category, string>;
  suppliers!: Table<Supplier, string>;
  customers!: Table<Customer, string>;
  movements!: Table<StockMovement, string>;
  transfers!: Table<Transfer, string>;
  sales!: Table<Sale, string>;
  purchaseOrders!: Table<PurchaseOrder, string>;
  purchaseOrderLines!: Table<PurchaseOrderLine, string>;
  notifications!: Table<Notification, string>;
  approvals!: Table<Approval, string>;
  expenses!: Table<Expense, string>;
  outbox!: Table<OutboxEntry, number>;

  constructor() {
    super("spop");
    this.version(1).stores({
      branches: "id",
      products: "id, categoryId",
      categories: "id, parentId",
      suppliers: "id",
      customers: "id, name",
      movements: "id, createdAt, productId, branchId",
      transfers: "id, requestedAt, status",
      sales: "id, createdAt",
      purchaseOrders: "id, requestedAt, status",
      purchaseOrderLines: "id, purchaseOrderId",
      notifications: "id, createdAt",
      approvals: "id, status",
      expenses: "id, status",
      outbox: "++id",
    });
  }
}
export const db = new SpopDatabase();

const localId = () => crypto.randomUUID();
const timestamp = () => new Date().toISOString();
const MAX_OUTBOX_ATTEMPTS = 5;
async function queue(path: string, body: object) { await db.outbox.add({ path, body: JSON.stringify(body), queuedAt: timestamp(), attempts: 0 }); }
export async function pendingSyncCount() { return db.outbox.count(); }
async function replayOutbox() {
  const entries = await db.outbox.toArray();
  for (const entry of entries) {
    try {
      await request(entry.path, { method: "POST", body: entry.body });
      if (entry.id !== undefined) await db.outbox.delete(entry.id);
    } catch (error) {
      const attempts = (entry.attempts ?? 0) + 1;
      if (entry.id === undefined) continue;
      if (attempts >= MAX_OUTBOX_ATTEMPTS) {
        await db.outbox.delete(entry.id);
        await request("/sync/report-failure", { method: "POST", body: JSON.stringify({ path: entry.path, error: error instanceof Error ? error.message : "Unknown error", attempts }) }).catch(() => undefined);
      } else {
        await db.outbox.update(entry.id, { attempts });
      }
    }
  }
}

export async function loadFromServer() {
  try { await replayOutbox(); } catch { /* Keep entries queued until the next successful connection. */ }
  const data = (await request("/bootstrap")) as {
    branches: Array<{ id: string; name: string; kind: "warehouse" | "shop" }>;
    products: Array<{ id: string; sku: string; name: string; brand: string; fitment: string; reorder_level: number; selling_price: number; category_id: string | null }>;
    categories: Array<{ id: string; name: string; parent_id: string | null }>;
    suppliers: Supplier[];
    customers: Customer[];
    movements: Array<{ id: string; product_id: string; branch_id: string; quantity: number; kind: MovementKind; reference: string; created_at: string; created_by: string }>;
    transfers: Array<{ id: string; reference: string; product_id: string; from_branch_id: string; to_branch_id: string; quantity: number; status: TransferStatus; version: number; created_at: string; fulfilled_at?: string | null; received_at?: string | null }>;
    sales: Array<{ id: string; product_id: string; branch_id: string; customer_id: string | null; quantity: number; total: number; receipt_number: string; created_at: string }>;
    purchase_orders: Array<{ id: string; reference: string; supplier_id: string; branch_id: string; status: string; version: number; notes: string; created_by: string; requested_at: string; received_at?: string }>;
    purchase_order_lines: Array<{ id: string; purchase_order_id: string; product_id: string; quantity: number; unit_cost: number; created_at: string }>;
    approvals: Array<{ id: string; type: string; subject: string; requester: string; approver: string; priority: string; status: string; deadline?: string | null; is_overdue?: boolean; related_entity_type?: string | null; related_entity_id?: string | null; created_at: string }>;
    expenses: Array<{ id: string; branch_id: string; category: string; amount: number; description: string; status: string; created_at: string }>;
    notifications: Array<{ id: string; title: string; body: string; kind: string; read: boolean; created_at: string }>;
  };
  await db.transaction("rw", [db.branches, db.products, db.categories, db.suppliers, db.customers, db.movements, db.transfers, db.sales, db.purchaseOrders, db.purchaseOrderLines, db.notifications, db.approvals, db.expenses], async () => {
    await Promise.all([db.branches.clear(), db.products.clear(), db.categories.clear(), db.suppliers.clear(), db.customers.clear(), db.movements.clear(), db.transfers.clear(), db.sales.clear(), db.purchaseOrders.clear(), db.purchaseOrderLines.clear(), db.notifications.clear(), db.approvals.clear(), db.expenses.clear()]);
    await db.branches.bulkPut(data.branches.map((b) => ({ id: b.id, name: b.name, type: b.kind })));
    await db.products.bulkPut(data.products.map((p) => ({ id: p.id, sku: p.sku, name: p.name, brand: p.brand, fitment: p.fitment, reorderLevel: p.reorder_level, sellingPrice: p.selling_price, categoryId: p.category_id })));
    await db.categories.bulkPut(data.categories.map((c) => ({ id: c.id, name: c.name, parentId: c.parent_id })));
    await db.suppliers.bulkPut(data.suppliers.map((s) => s));
    await db.customers.bulkPut(data.customers.map((c) => ({ id: c.id, name: c.name, phone: c.phone })));
    await db.movements.bulkPut(data.movements.map((m) => ({ id: m.id, productId: m.product_id, branchId: m.branch_id, quantity: m.quantity, kind: m.kind, reference: m.reference, createdAt: m.created_at, createdBy: m.created_by })));
    await db.transfers.bulkPut(data.transfers.map((t) => ({ id: t.id, reference: t.reference, productId: t.product_id, fromBranchId: t.from_branch_id, toBranchId: t.to_branch_id, quantity: t.quantity, status: t.status, version: t.version, requestedAt: t.created_at, fulfilledAt: t.fulfilled_at, receivedAt: t.received_at })));
    await db.sales.bulkPut(data.sales.map((s) => ({ id: s.id, productId: s.product_id, branchId: s.branch_id, customerId: s.customer_id, quantity: s.quantity, total: s.total, receiptNumber: s.receipt_number, createdAt: s.created_at })));
    await db.purchaseOrders.bulkPut(data.purchase_orders.map((order) => ({ id: order.id, reference: order.reference, supplierId: order.supplier_id, branchId: order.branch_id, status: order.status, version: order.version, notes: order.notes, createdBy: order.created_by, requestedAt: order.requested_at, receivedAt: order.received_at })));
    await db.purchaseOrderLines.bulkPut(data.purchase_order_lines.map((line) => ({ id: line.id, purchaseOrderId: line.purchase_order_id, productId: line.product_id, quantity: line.quantity, unitCost: line.unit_cost, createdAt: line.created_at })));
    await db.approvals.bulkPut(data.approvals.map((approval) => ({ id: approval.id, type: approval.type, subject: approval.subject, requester: approval.requester, approver: approval.approver, priority: approval.priority, status: approval.status, deadline: approval.deadline, isOverdue: approval.is_overdue, relatedEntityType: approval.related_entity_type, relatedEntityId: approval.related_entity_id, createdAt: approval.created_at })));
    await db.expenses.bulkPut(data.expenses.map((expense) => ({ id: expense.id, branchId: expense.branch_id, category: expense.category, amount: expense.amount, description: expense.description, status: expense.status, createdAt: expense.created_at })));
    await db.notifications.bulkPut(data.notifications.map((notification) => ({ id: notification.id, title: notification.title, body: notification.body, kind: notification.kind, read: notification.read, createdAt: notification.created_at })));
  });
}
export async function recordReceipt(productId: string, branchId: string, quantity: number, supplierId: string) {
  const body = { product_id: productId, branch_id: branchId, supplier_id: supplierId, quantity, client_request_id: localId() };
  try { await request("/receipts", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { const reference = `OFFLINE-GRN-${Date.now()}`; await db.transaction("rw", db.movements, db.outbox, async () => { await db.movements.add({ id: localId(), productId, branchId, quantity, kind: "receipt", reference, createdAt: timestamp(), createdBy: "Offline operator" }); await queue("/receipts", body); }); }
}
export async function recordSale(product: Product, branchId: string, quantity: number, customerId?: string | null) {
  const body = { product_id: product.id, branch_id: branchId, quantity, customer_id: customerId || null, client_request_id: localId() };
  try { await request("/sales", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { const reference = `OFFLINE-S-${Date.now()}`; await db.transaction("rw", db.movements, db.sales, db.outbox, async () => { await db.movements.add({ id: localId(), productId: product.id, branchId, quantity: -quantity, kind: "sale", reference, createdAt: timestamp(), createdBy: "Offline operator" }); await db.sales.add({ id: localId(), productId: product.id, branchId, customerId: customerId || null, quantity, total: product.sellingPrice * quantity, receiptNumber: reference, createdAt: timestamp() }); await queue("/sales", body); }); }
}
export async function createCustomer(name: string, phone: string): Promise<Customer> {
  const customer = (await request("/customers", { method: "POST", body: JSON.stringify({ name, phone }) })) as { id: string; name: string; phone: string };
  await db.customers.put({ id: customer.id, name: customer.name, phone: customer.phone });
  return customer;
}
export async function fetchDailySales(): Promise<Array<{ day: string; sales_count: number; sales_total: number }>> {
  return (await request("/reports/daily-sales")) as Array<{ day: string; sales_count: number; sales_total: number }>;
}
export async function recordTransfer(productId: string, fromBranchId: string, toBranchId: string, quantity: number) {
  // Only the request step can be queued offline: fulfilling and receiving
  // move real stock and must be verified against the live server.
  const body = { product_id: productId, from_branch_id: fromBranchId, to_branch_id: toBranchId, quantity, client_request_id: localId() };
  try { await request("/transfers", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { const reference = `OFFLINE-TRF-${Date.now()}`; await db.transaction("rw", db.transfers, db.outbox, async () => { await db.transfers.add({ id: reference, reference, productId, fromBranchId, toBranchId, quantity, status: "requested", version: 1, requestedAt: timestamp() }); await queue("/transfers", body); }); }
}
export async function fulfilTransfer(transferId: string, reference: string, expectedVersion: number) {
  try {
    await request(`/transfers/${transferId}/fulfil`, { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) });
    await loadFromServer();
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) recordSyncIssue({ id: transferId, kind: "transfer", reference, message: error.message });
    throw error;
  }
}
export async function receiveTransfer(transferId: string, reference: string, expectedVersion: number) {
  try {
    await request(`/transfers/${transferId}/receive`, { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) });
    await loadFromServer();
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) recordSyncIssue({ id: transferId, kind: "transfer", reference, message: error.message });
    throw error;
  }
}
export async function cancelTransfer(transferId: string) {
  await request(`/transfers/${transferId}/cancel`, { method: "POST" });
  await loadFromServer();
}
export async function createApproval(type: string, subject: string, requester: string, approver: string, priority: string) { await request("/approvals", { method: "POST", body: JSON.stringify({ type, subject, requester, approver, priority, client_request_id: localId() }) }); await loadFromServer(); }
export async function approveApproval(approvalId: string) { await request(`/approvals/${approvalId}/approve`, { method: "POST" }); await loadFromServer(); }
export async function rejectApproval(approvalId: string) { await request(`/approvals/${approvalId}/reject`, { method: "POST" }); await loadFromServer(); }
export async function createExpense(branchId: string, category: string, amount: number, description: string) { await request("/expenses", { method: "POST", body: JSON.stringify({ branch_id: branchId, category, amount, description, client_request_id: localId() }) }); await loadFromServer(); }
export async function approveExpense(expenseId: string) { await request(`/expenses/${expenseId}/approve`, { method: "POST" }); await loadFromServer(); }
export async function rejectExpense(expenseId: string) { await request(`/expenses/${expenseId}/reject`, { method: "POST" }); await loadFromServer(); }
export async function createPurchaseOrder(supplierId: string, branchId: string, notes: string, items: Array<{ productId: string; quantity: number; unitCost: number }>) {
  const body = { supplier_id: supplierId, branch_id: branchId, notes, items: items.map((item) => ({ product_id: item.productId, quantity: item.quantity, unit_cost: item.unitCost })), client_request_id: localId() };
  try { await request("/purchase-orders", { method: "POST", body: JSON.stringify(body) }); await loadFromServer(); }
  catch { await queue("/purchase-orders", body); }
}
export async function approvePurchaseOrder(orderId: string) {
  await request(`/purchase-orders/${orderId}/approve`, { method: "POST" });
  await loadFromServer();
}
export async function receivePurchaseOrder(orderId: string, reference: string, expectedVersion: number) {
  try {
    await request(`/purchase-orders/${orderId}/receive`, { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) });
    await loadFromServer();
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) recordSyncIssue({ id: orderId, kind: "purchase_order", reference, message: error.message });
    throw error;
  }
}
export async function markNotificationRead(notificationId: string) {
  try { await request(`/notifications/${notificationId}/read`, { method: "POST" }); await loadFromServer(); }
  catch { /* keep cached state */ }
}
