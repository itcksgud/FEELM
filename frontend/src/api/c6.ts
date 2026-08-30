import type { components } from "./schema";

export type C6Confidence = components["schemas"]["C6Confidence"];
export type RecommendationInterpretationExperiment = components["schemas"]["C6InterpretationExperiment"];
export type C6Limitation = RecommendationInterpretationExperiment["limitations"][number];

type C6ErrorPayload = { message?: string; code?: string; traceId?: string };

export class C6ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly traceId?: string;

  constructor(status: number, payload?: C6ErrorPayload) {
    super(payload?.message ?? "실험 결과를 불러오지 못했어요.");
    this.name = "C6ApiError";
    this.status = status;
    this.code = payload?.code;
    this.traceId = payload?.traceId;
  }
}

export interface C6Api {
  getRecommendationInterpretation(signal?: AbortSignal): Promise<RecommendationInterpretationExperiment>;
}

async function parseError(response: Response): Promise<C6ErrorPayload | undefined> {
  try {
    return await response.json() as C6ErrorPayload;
  } catch {
    return undefined;
  }
}

export class HttpC6Api implements C6Api {
  constructor(
    private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? "",
    private readonly bearerToken = import.meta.env.VITE_C1_FAKE_BEARER_TOKEN ?? "test-c1-owner-token",
  ) {}

  async getRecommendationInterpretation(signal?: AbortSignal): Promise<RecommendationInterpretationExperiment> {
    const response = await fetch(`${this.baseUrl}/api/v1/me/recommendation-interpretation-experiment`, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${this.bearerToken}`,
      },
      cache: "no-store",
      signal,
    });
    if (!response.ok) throw new C6ApiError(response.status, await parseError(response));
    return await response.json() as RecommendationInterpretationExperiment;
  }
}

export function c6ErrorMessage(error: unknown): string {
  if (!(error instanceof C6ApiError)) return "실험 결과를 불러오지 못했어요. 다시 시도해 주세요.";
  if (error.status === 401) return "로컬 실험 인증이 만료됐어요. 테스트 토큰을 확인해 주세요.";
  if (error.status === 503) return "추천 실험 모델이 아직 준비되지 않았어요. 모델 상태를 확인한 뒤 다시 시도해 주세요.";
  return error.message;
}
