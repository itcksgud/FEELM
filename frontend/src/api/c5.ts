import type { components } from "./schema";
import { isLoopbackOrigin, localFeaturesEnabled } from "../config/localFeatures";

export type TasteReportSummaryPage = components["schemas"]["TasteReportSummaryPage"];
export type TasteReport = components["schemas"]["TasteReport"];
export type ReportExport = components["schemas"]["ReportExport"];
export type PrivacySettings = components["schemas"]["PrivacySettings"];
export type ResourcePrivacy = components["schemas"]["ResourcePrivacy"];
export type PublicProfile = components["schemas"]["PublicProfile"];
export type PublicFilmPage = components["schemas"]["PublicFilmPage"];
export type PublicPopcornPage = components["schemas"]["PublicPopcornPage"];
export type CreatedReportShare = components["schemas"]["CreatedReportShare"];
export type ReportViewerSession = components["schemas"]["ReportViewerSession"];
export type SharedTasteReport = components["schemas"]["SharedTasteReport"];
export type NotificationSettings = components["schemas"]["NotificationSettings"];
export type NotificationPage = components["schemas"]["NotificationPage"];
export type InAppNotification = components["schemas"]["InAppNotification"];

type C5Error = components["schemas"]["C5Error"];
export class C5ApiError extends Error {
  constructor(readonly status: number, readonly payload?: C5Error) { super(payload?.message ?? "요청을 처리하지 못했어요."); this.name = "C5ApiError"; }
}
async function json<T>(pending: Promise<Response>): Promise<T> { const response = await pending; if (response.ok) return (response.status === 204 ? undefined : await response.json()) as T; let payload: C5Error | undefined; try { payload = await response.json() as C5Error; } catch { payload = undefined; } throw new C5ApiError(response.status, payload); }
function key() { return globalThis.crypto?.randomUUID?.() ?? `request-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function query(cursor?: string) { const value = new URLSearchParams({ limit: "20" }); if (cursor) value.set("cursor", cursor); return value; }

export interface C5Api {
  listReports(cursor?: string): Promise<TasteReportSummaryPage>; createReport(periodStart: string): Promise<TasteReport>; getReport(reportId: string, cursor?: string): Promise<TasteReport>;
  createExport(reportId: string): Promise<ReportExport>; getExport(exportId: string): Promise<ReportExport>; downloadExport(exportId: string): Promise<Blob>;
  getPrivacy(): Promise<PrivacySettings>; replacePrivacy(resources: ResourcePrivacy[], expectedRevision: number): Promise<PrivacySettings>;
  getPublicProfile(publicProfileId: string): Promise<PublicProfile>; listPublicFilm(publicProfileId: string, cursor?: string): Promise<PublicFilmPage>; listPublicPopcorns(publicProfileId: string, cursor?: string): Promise<PublicPopcornPage>;
  createShare(reportId: string): Promise<CreatedReportShare>; revokeShare(shareId: string): Promise<void>; exchangeShare(rawToken: string): Promise<ReportViewerSession>; getSharedReport(viewerToken: string, cursor?: string): Promise<SharedTasteReport>;
  getNotificationSettings(): Promise<NotificationSettings>; replaceNotificationSettings(enabled: boolean, expectedRevision: number): Promise<NotificationSettings>; listNotifications(cursor?: string): Promise<NotificationPage>; updateNotification(notificationId: string, state: "READ" | "DISMISSED"): Promise<InAppNotification>;
}

export class HttpC5Api implements C5Api {
  constructor(private readonly baseUrl: string, private readonly token: () => string | null) {}
  private owner(extra: Record<string, string> = {}) { const token = this.token(); if (!token) throw new C5ApiError(401); return { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...extra }; }
  private mutation() { return { "Idempotency-Key": key() }; }
  private localCapability() { if (!localFeaturesEnabled || !isLoopbackOrigin()) throw new C5ApiError(404); }
  listReports(cursor?: string) { return json<TasteReportSummaryPage>(fetch(`${this.baseUrl}/api/v1/me/taste-reports?${query(cursor)}`, { headers: this.owner() })); }
  createReport(periodStart: string) { return json<TasteReport>(fetch(`${this.baseUrl}/api/v1/me/taste-reports`, { method: "POST", headers: this.owner(this.mutation()), body: JSON.stringify({ periodStart }) })); }
  getReport(id: string, cursor?: string) { return json<TasteReport>(fetch(`${this.baseUrl}/api/v1/me/taste-reports/${id}?${query(cursor)}`, { headers: this.owner() })); }
  createExport(id: string) { this.localCapability(); return json<ReportExport>(fetch(`${this.baseUrl}/api/v1/me/taste-reports/${id}/exports`, { method: "POST", headers: this.owner(this.mutation()) })); }
  getExport(id: string) { this.localCapability(); return json<ReportExport>(fetch(`${this.baseUrl}/api/v1/me/report-exports/${id}`, { headers: this.owner() })); }
  async downloadExport(id: string) { this.localCapability(); const response = await fetch(`${this.baseUrl}/api/v1/me/report-exports/${id}/content`, { headers: this.owner() }); if (!response.ok) throw new C5ApiError(response.status); return response.blob(); }
  getPrivacy() { return json<PrivacySettings>(fetch(`${this.baseUrl}/api/v1/me/privacy-settings`, { headers: this.owner() })); }
  replacePrivacy(resources: ResourcePrivacy[], expectedRevision: number) { return json<PrivacySettings>(fetch(`${this.baseUrl}/api/v1/me/privacy-settings`, { method: "PUT", headers: this.owner(this.mutation()), body: JSON.stringify({ resources, expectedRevision }) })); }
  getPublicProfile(id: string) { return json<PublicProfile>(fetch(`${this.baseUrl}/api/v1/public/profiles/${id}`)); }
  listPublicFilm(id: string, cursor?: string) { return json<PublicFilmPage>(fetch(`${this.baseUrl}/api/v1/public/profiles/${id}/film?${query(cursor)}`)); }
  listPublicPopcorns(id: string, cursor?: string) { return json<PublicPopcornPage>(fetch(`${this.baseUrl}/api/v1/public/profiles/${id}/popcorns?${query(cursor)}`)); }
  createShare(id: string) { this.localCapability(); return json<CreatedReportShare>(fetch(`${this.baseUrl}/api/v1/me/taste-reports/${id}/shares`, { method: "POST", headers: this.owner(this.mutation()) })); }
  revokeShare(id: string) { this.localCapability(); return json<void>(fetch(`${this.baseUrl}/api/v1/me/report-shares/${id}/revoke`, { method: "POST", headers: this.owner(this.mutation()) })); }
  exchangeShare(rawToken: string) { this.localCapability(); return json<ReportViewerSession>(fetch(`${this.baseUrl}/api/v1/public/report-shares/exchange`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rawToken }) })); }
  getSharedReport(viewerToken: string, cursor?: string) { this.localCapability(); return json<SharedTasteReport>(fetch(`${this.baseUrl}/api/v1/public/shared-report?${query(cursor)}`, { headers: { "X-Report-Viewer-Session": viewerToken } })); }
  getNotificationSettings() { return json<NotificationSettings>(fetch(`${this.baseUrl}/api/v1/me/notification-settings`, { headers: this.owner() })); }
  replaceNotificationSettings(enabled: boolean, expectedRevision: number) { return json<NotificationSettings>(fetch(`${this.baseUrl}/api/v1/me/notification-settings`, { method: "PUT", headers: this.owner(this.mutation()), body: JSON.stringify({ watchConfirmationDueEnabled: enabled, expectedRevision }) })); }
  listNotifications(cursor?: string) { return json<NotificationPage>(fetch(`${this.baseUrl}/api/v1/me/notifications?${query(cursor)}`, { headers: this.owner() })); }
  updateNotification(id: string, state: "READ" | "DISMISSED") { return json<InAppNotification>(fetch(`${this.baseUrl}/api/v1/me/notifications/${id}/state`, { method: "PUT", headers: this.owner(this.mutation()), body: JSON.stringify({ state }) })); }
}
