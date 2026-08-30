import type { components } from "./schema";

export type C2BErrorResponse = components["schemas"]["ErrorResponse"];
export type RecommendationMovie = components["schemas"]["PersonalDiscoveryMovieCard"];
export type RecommendationItem = components["schemas"]["PersonalDiscoveryDeliveryItem"];
export type RecommendationPageInfo = components["schemas"]["RecommendationPageInfo"];
export type RecommendationDelivery = components["schemas"]["RecommendationDelivery"];
export type RecommendationAppend = components["schemas"]["RecommendationAppend"];
export type RecommendationDismissal = components["schemas"]["RecommendationDismissal"];

export type AppendRecommendationsInput = {
  deliveryId: string;
  expectedRevision: number;
  cursor: string;
  appendEventId: string;
  idempotencyKey: string;
};

export type DismissRecommendationInput = {
  deliveryItemId: string;
  expectedRevision: number;
  dismissalEventId: string;
  idempotencyKey: string;
};

export class C2BApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly traceId?: string;

  constructor(status: number, payload?: C2BErrorResponse) {
    super(payload?.message ?? "추천 요청을 처리하지 못했어요.");
    this.name = "C2BApiError";
    this.status = status;
    this.code = payload?.code;
    this.traceId = payload?.traceId;
  }
}

export function createC2BId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let payload: C2BErrorResponse | undefined;
  try {
    payload = await response.json() as C2BErrorResponse;
  } catch {
    payload = undefined;
  }
  throw new C2BApiError(response.status, payload);
}

function assertAppendLimit(result: RecommendationAppend): RecommendationAppend {
  if (result.appendedItems.length > 3) {
    throw new C2BApiError(502);
  }
  return result;
}

export interface C2BApi {
  getRecommendations(signal?: AbortSignal): Promise<RecommendationDelivery>;
  appendRecommendations(input: AppendRecommendationsInput): Promise<RecommendationAppend>;
  dismissRecommendation(input: DismissRecommendationInput): Promise<RecommendationDismissal>;
}

export class HttpC2BApi implements C2BApi {
  constructor(
    private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? "",
    private readonly bearerToken = import.meta.env.VITE_C1_FAKE_BEARER_TOKEN ?? "test-c1-owner-token",
  ) {}

  private headers(extra: Record<string, string> = {}): HeadersInit {
    return {
      Authorization: `Bearer ${this.bearerToken}`,
      "Content-Type": "application/json",
      ...extra,
    };
  }

  async getRecommendations(signal?: AbortSignal) {
    const response = await fetch(`${this.baseUrl}/api/v1/me/recommendations/personal-discovery`, {
      headers: this.headers(),
      cache: "no-store",
      signal,
    });
    return readJson<RecommendationDelivery>(response);
  }

  async appendRecommendations(input: AppendRecommendationsInput) {
    const response = await fetch(`${this.baseUrl}/api/v1/me/recommendation-deliveries/${input.deliveryId}/append`, {
      method: "POST",
      headers: this.headers({ "Idempotency-Key": input.idempotencyKey }),
      body: JSON.stringify({
        appendEventId: input.appendEventId,
        expectedRevision: input.expectedRevision,
        cursor: input.cursor,
      }),
    });
    return assertAppendLimit(await readJson<RecommendationAppend>(response));
  }

  async dismissRecommendation(input: DismissRecommendationInput) {
    const response = await fetch(`${this.baseUrl}/api/v1/me/recommendation-delivery-items/${input.deliveryItemId}/dismissals`, {
      method: "POST",
      headers: this.headers({ "Idempotency-Key": input.idempotencyKey }),
      body: JSON.stringify({
        dismissalEventId: input.dismissalEventId,
        expectedRevision: input.expectedRevision,
        reason: "NOT_INTERESTED",
      }),
    });
    return readJson<RecommendationDismissal>(response);
  }
}

export function c2bErrorMessage(error: unknown): string {
  if (!(error instanceof C2BApiError)) return "추천을 불러오지 못했어요. 다시 시도해 주세요.";
  if (error.status === 401) return "로그인이 만료됐어요. 다시 로그인해 주세요.";
  if (error.status === 409) return "추천 목록이 변경됐어요. 최신 목록을 다시 불러와 주세요.";
  if (error.status === 503) return "추천 서비스를 잠시 이용할 수 없어요.";
  return error.message;
}
