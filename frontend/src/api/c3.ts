import type { components } from "./schema";
import { localFeaturesEnabled } from "../config/localFeatures";

export type C3ProviderIdSet = components["schemas"]["C3ProviderIdSet"];
export type C3OttCatalogComparison = components["schemas"]["C3OttCatalogComparison"];
export type C3CatalogMoviePage = components["schemas"]["C3CatalogMoviePage"];
export type C3Party = components["schemas"]["C3Party"];
export type C3PartyPage = components["schemas"]["C3PartyPage"];
export type C3PartyInvitation = components["schemas"]["C3PartyInvitation"];
export type C3PartyInvitationPage = components["schemas"]["C3PartyInvitationPage"];
export type C3AcceptPartyInvitationResponse = components["schemas"]["C3AcceptPartyInvitationResponse"];
export type C3PartyBaselinePage = components["schemas"]["C3PartyBaselinePage"];
export type C3ErrorResponse = components["schemas"]["ErrorResponse"];

export const C3_LOCAL_ACTORS = [
  { actorId: "018f6826-4da1-7c38-a846-8f794cd8b0cf", nickname: "local_owner" },
  { actorId: "4d85e2ae-87ce-4f48-8ac1-fabf89bb1371", nickname: "film_a" },
  { actorId: "bb5799ab-7654-4e01-8e0f-c1fe583d340d", nickname: "film_b" },
  { actorId: "85b0fa76-5b3e-4fcb-8846-807b466e757d", nickname: "film_c" },
] as const;

export type C3LocalActorId = (typeof C3_LOCAL_ACTORS)[number]["actorId"];

export class C3ApiError extends Error {
  constructor(readonly status: number, readonly payload?: C3ErrorResponse) {
    super(payload?.message ?? "로컬 기능을 처리하지 못했어요.");
    this.name = "C3ApiError";
  }
}

async function json<T>(pending: Response | Promise<Response>): Promise<T> {
  const response = await pending;
  if (response.ok) return response.json() as Promise<T>;
  let payload: C3ErrorResponse | undefined;
  try { payload = await response.json() as C3ErrorResponse; } catch { payload = undefined; }
  throw new C3ApiError(response.status, payload);
}

export function createC3Id(): string {
  return globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export interface C3Api {
  createOttComparison(providerIds: string[], idempotencyKey: string): Promise<C3OttCatalogComparison>;
  getOttComparison(comparisonId: string, signal?: AbortSignal): Promise<C3OttCatalogComparison>;
  listOttMovies(comparisonId: string, providerId: string, cursor?: string, signal?: AbortSignal): Promise<C3CatalogMoviePage>;
  listParties(signal?: AbortSignal): Promise<C3PartyPage>;
  createParty(name: string, providerIds: string[], idempotencyKey: string): Promise<C3Party>;
  getParty(partyId: string, signal?: AbortSignal): Promise<C3Party>;
  listPartyInvitations(partyId: string, signal?: AbortSignal): Promise<C3PartyInvitationPage>;
  createInvitation(partyId: string, recipientActorId: string, expectedPartyRevision: number, idempotencyKey: string): Promise<C3PartyInvitation>;
  listMyInvitations(signal?: AbortSignal): Promise<C3PartyInvitationPage>;
  acceptInvitation(invitationId: string, expectedPartyRevision: number, expectedInvitationRevision: number, idempotencyKey: string): Promise<C3AcceptPartyInvitationResponse>;
  listBaseline(partyId: string, cursor?: string, signal?: AbortSignal): Promise<C3PartyBaselinePage>;
}

export class HttpC3Api implements C3Api {
  constructor(private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? "", private readonly actorId: string = localFeaturesEnabled ? C3_LOCAL_ACTORS[0].actorId : "") {}

  private headers(idempotencyKey?: string): HeadersInit {
    if (!localFeaturesEnabled || !this.actorId) throw new C3ApiError(403);
    return {
      "Content-Type": "application/json",
      "X-Local-Actor-Id": this.actorId,
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
    };
  }

  createOttComparison(providerIds: string[], key: string) {
    return json<C3OttCatalogComparison>(fetch(`${this.baseUrl}/api/v1/me/ott-catalog-comparisons`, { method: "POST", headers: this.headers(key), body: JSON.stringify({ providerIds }) }));
  }
  getOttComparison(id: string, signal?: AbortSignal) {
    return json<C3OttCatalogComparison>(fetch(`${this.baseUrl}/api/v1/me/ott-catalog-comparisons/${id}`, { headers: this.headers(), signal }));
  }
  listOttMovies(id: string, providerId: string, cursor?: string, signal?: AbortSignal) {
    const query = new URLSearchParams({ providerId, limit: "20" });
    if (cursor) query.set("cursor", cursor);
    return json<C3CatalogMoviePage>(fetch(`${this.baseUrl}/api/v1/me/ott-catalog-comparisons/${id}/movies?${query}`, { headers: this.headers(), signal }));
  }
  listParties(signal?: AbortSignal) {
    return json<C3PartyPage>(fetch(`${this.baseUrl}/api/v1/me/parties`, { headers: this.headers(), signal }));
  }
  createParty(name: string, providerIds: string[], key: string) {
    return json<C3Party>(fetch(`${this.baseUrl}/api/v1/me/parties`, { method: "POST", headers: this.headers(key), body: JSON.stringify({ name, providerIds }) }));
  }
  getParty(id: string, signal?: AbortSignal) {
    return json<C3Party>(fetch(`${this.baseUrl}/api/v1/parties/${id}`, { headers: this.headers(), signal }));
  }
  listPartyInvitations(id: string, signal?: AbortSignal) {
    return json<C3PartyInvitationPage>(fetch(`${this.baseUrl}/api/v1/parties/${id}/invitations`, { headers: this.headers(), signal }));
  }
  createInvitation(id: string, recipientActorId: string, expectedPartyRevision: number, key: string) {
    return json<C3PartyInvitation>(fetch(`${this.baseUrl}/api/v1/parties/${id}/invitations`, { method: "POST", headers: this.headers(key), body: JSON.stringify({ recipientActorId, expectedPartyRevision }) }));
  }
  listMyInvitations(signal?: AbortSignal) {
    return json<C3PartyInvitationPage>(fetch(`${this.baseUrl}/api/v1/me/party-invitations`, { headers: this.headers(), signal }));
  }
  acceptInvitation(id: string, expectedPartyRevision: number, expectedInvitationRevision: number, key: string) {
    return json<C3AcceptPartyInvitationResponse>(fetch(`${this.baseUrl}/api/v1/me/party-invitations/${id}/accept`, { method: "POST", headers: this.headers(key), body: JSON.stringify({ expectedPartyRevision, expectedInvitationRevision }) }));
  }
  listBaseline(id: string, cursor?: string, signal?: AbortSignal) {
    const query = new URLSearchParams({ limit: "20" });
    if (cursor) query.set("cursor", cursor);
    return json<C3PartyBaselinePage>(fetch(`${this.baseUrl}/api/v1/parties/${id}/baseline-recommendations?${query}`, { headers: this.headers(), signal }));
  }
}

export function c3ErrorMessage(error: unknown): string {
  if (!(error instanceof C3ApiError)) return "로컬 기능을 불러오지 못했어요.";
  if (error.status === 401) return "로컬 테스트 사용자를 다시 선택해 주세요.";
  if (error.status === 404) return "이 항목을 찾을 수 없어요.";
  if (error.status === 409) return "정보가 변경됐어요. 새로고침 후 다시 시도해 주세요.";
  if (error.status === 503) return "로컬 영화 목록을 준비하지 못했어요. 다시 시도해 주세요.";
  return error.message;
}
