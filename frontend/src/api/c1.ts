import createClient, { type Client } from "openapi-fetch";
import type { components, paths } from "./schema";

export type C1ErrorResponse = components["schemas"]["ErrorResponse"];
export type WatchIntentClickResult = components["schemas"]["WatchIntentClickResult"];
export type PendingWatchConfirmationPage = components["schemas"]["PendingWatchConfirmationPage"];
export type PendingWatchConfirmation = components["schemas"]["PendingWatchConfirmation"];
export type MovieSummary = components["schemas"]["MovieSummary"];
export type Rating = components["schemas"]["Rating"];
export type UnratedViewingRecord = components["schemas"]["UnratedViewingRecord"];
export type WatchConfirmationResult = components["schemas"]["WatchConfirmationResult"];
export type UnratedViewingRecordPage = components["schemas"]["UnratedViewingRecordPage"];
export type RatingPage = components["schemas"]["RatingPage"];
export type RatingItem = components["schemas"]["RatingItem"];
export type RatingMutationResult = components["schemas"]["RatingMutationResult"];
export type RatingDeletionResult = components["schemas"]["RatingDeletionResult"];
export type FilmPage = components["schemas"]["FilmPage"];
export type FrameDetail = components["schemas"]["FrameDetail"];
export type PopcornBucket = components["schemas"]["PopcornBucket"];
export type TasteProfile = components["schemas"]["TasteProfile"];
export type CreateWatchIntentInput = { movieId: string; offerId: string; idempotencyKey: string };
export type ConfirmWatchIntentInput = { watchIntentId: string; watched: boolean; expectedRevision: number; idempotencyKey: string };
export type PutRatingInput = { movieId: string; value: number; expectedRevision?: number; idempotencyKey: string };
export type DeleteRatingInput = { movieId: string; expectedRevision: number; idempotencyKey: string };
export type RatingEditorLookup =
  | { kind: "RATED"; item: RatingItem }
  | { kind: "UNRATED"; item: UnratedViewingRecord };

type CursorPage<T> = {
  items: T[];
  hasNext: boolean;
  nextCursor?: string | null;
};

async function findCursorItem<T>(
  loadPage: (cursor?: string) => Promise<CursorPage<T>>,
  matches: (item: T) => boolean,
): Promise<T | undefined> {
  let cursor: string | undefined;
  const visitedCursors = new Set<string>();

  while (true) {
    const page = await loadPage(cursor);
    const item = page.items.find(matches);
    if (item) return item;
    if (!page.hasNext) return undefined;

    const nextCursor = page.nextCursor ?? undefined;
    if (!nextCursor || visitedCursors.has(nextCursor)) {
      throw new Error("C1 cursor pagination did not advance");
    }
    visitedCursors.add(nextCursor);
    cursor = nextCursor;
  }
}

export class C1ApiError extends Error {
  readonly status: number;
  readonly code?: C1ErrorResponse["code"];
  readonly fieldErrors: C1ErrorResponse["fieldErrors"];
  readonly traceId?: string;

  constructor(status: number, payload?: C1ErrorResponse) {
    super(payload?.message ?? "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.");
    this.name = "C1ApiError";
    this.status = status;
    this.code = payload?.code;
    this.fieldErrors = payload?.fieldErrors ?? [];
    this.traceId = payload?.traceId;
  }
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response };

function unwrap<T>({ data, error, response }: FetchResult<T>): T {
  if (data !== undefined) return data;
  throw new C1ApiError(response.status, error as C1ErrorResponse | undefined);
}

export function createIdempotencyKey(operation: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `c1-${operation}-${random}`;
}

export interface C1Api {
  createWatchIntent(input: CreateWatchIntentInput): Promise<WatchIntentClickResult>;
  listPendingWatchConfirmations(cursor?: string, signal?: AbortSignal): Promise<PendingWatchConfirmationPage>;
  findPendingWatchConfirmation(watchIntentId: string, signal?: AbortSignal): Promise<PendingWatchConfirmation | undefined>;
  confirmWatchIntent(input: ConfirmWatchIntentInput): Promise<WatchConfirmationResult>;
  listUnratedViewingRecords(cursor?: string, signal?: AbortSignal): Promise<UnratedViewingRecordPage>;
  listMyRatings(cursor?: string, signal?: AbortSignal): Promise<RatingPage>;
  findMyRatingEditor(movieId: string, signal?: AbortSignal): Promise<RatingEditorLookup | undefined>;
  putMyRating(input: PutRatingInput): Promise<RatingMutationResult>;
  deleteMyRating(input: DeleteRatingInput): Promise<RatingDeletionResult>;
  getMyFilm(cursor?: string, signal?: AbortSignal): Promise<FilmPage>;
  getMyFrame(frameId: string, signal?: AbortSignal): Promise<FrameDetail>;
  getMyPopcornBucket(signal?: AbortSignal): Promise<PopcornBucket>;
  getMyTasteProfile(signal?: AbortSignal): Promise<TasteProfile>;
}

export class HttpC1Api implements C1Api {
  private readonly client: Client<paths>;

  constructor(
    baseUrl = import.meta.env.VITE_API_BASE_URL ?? "",
    bearerToken = import.meta.env.VITE_C1_FAKE_BEARER_TOKEN ?? "test-c1-owner-token",
  ) {
    this.client = createClient<paths>({
      baseUrl,
      headers: { Authorization: `Bearer ${bearerToken}` },
    });
  }

  async createWatchIntent(input: { movieId: string; offerId: string; idempotencyKey: string }) {
    return unwrap(
      await this.client.POST("/api/v1/watch-intents", {
        params: { header: { "Idempotency-Key": input.idempotencyKey } },
        body: { movieId: input.movieId, offerId: input.offerId },
      }),
    );
  }

  async listPendingWatchConfirmations(cursor?: string, signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/me/watch-intents/pending-confirmation", {
      params: { query: { cursor, limit: 20 } },
      signal,
    }));
  }

  async findPendingWatchConfirmation(watchIntentId: string, signal?: AbortSignal) {
    return findCursorItem(
      (cursor) => this.listPendingWatchConfirmations(cursor, signal),
      (item) => item.watchIntentId === watchIntentId,
    );
  }

  async confirmWatchIntent(input: { watchIntentId: string; watched: boolean; expectedRevision: number; idempotencyKey: string }) {
    return unwrap(await this.client.POST("/api/v1/watch-intents/{watchIntentId}/confirmation", {
      params: {
        path: { watchIntentId: input.watchIntentId },
        header: { "Idempotency-Key": input.idempotencyKey },
      },
      body: { watched: input.watched, expectedRevision: input.expectedRevision },
    }));
  }

  async listUnratedViewingRecords(cursor?: string, signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/me/viewing-records/unrated", {
      params: { query: { cursor, limit: 20 } },
      signal,
    }));
  }

  async listMyRatings(cursor?: string, signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/me/ratings", {
      params: { query: { cursor, limit: 20 } },
      signal,
    }));
  }

  async findMyRatingEditor(movieId: string, signal?: AbortSignal): Promise<RatingEditorLookup | undefined> {
    const rating = await findCursorItem(
      (cursor) => this.listMyRatings(cursor, signal),
      (item) => item.rating.movieId === movieId,
    );
    if (rating) return { kind: "RATED", item: rating };

    const unrated = await findCursorItem(
      (cursor) => this.listUnratedViewingRecords(cursor, signal),
      (item) => item.movie.movieId === movieId,
    );
    return unrated ? { kind: "UNRATED", item: unrated } : undefined;
  }

  async putMyRating(input: { movieId: string; value: number; expectedRevision?: number; idempotencyKey: string }) {
    return unwrap(await this.client.PUT("/api/v1/me/ratings/{movieId}", {
      params: {
        path: { movieId: input.movieId },
        header: { "Idempotency-Key": input.idempotencyKey },
      },
      body: { value: input.value, expectedRevision: input.expectedRevision },
    }));
  }

  async deleteMyRating(input: { movieId: string; expectedRevision: number; idempotencyKey: string }) {
    return unwrap(await this.client.DELETE("/api/v1/me/ratings/{movieId}", {
      params: {
        path: { movieId: input.movieId },
        header: {
          "Idempotency-Key": input.idempotencyKey,
          "X-Expected-Revision": input.expectedRevision,
        },
      },
    }));
  }

  async getMyFilm(cursor?: string, signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/me/film", {
      params: { query: { cursor, limit: 20 } },
      signal,
    }));
  }

  async getMyFrame(frameId: string, signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/me/film/frames/{frameId}", {
      params: { path: { frameId } },
      signal,
    }));
  }

  async getMyPopcornBucket(signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/me/popcorn-bucket", { signal }));
  }

  async getMyTasteProfile(signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/me/taste-profile", { signal }));
  }
}

export function shouldRetryC1(failureCount: number, error: unknown) {
  return error instanceof C1ApiError && error.status === 503 && failureCount < 1;
}

export function c1ErrorMessage(error: unknown): string {
  if (!(error instanceof C1ApiError)) return "요청을 처리하지 못했어요. 다시 시도해 주세요.";
  if (error.status === 400) return error.fieldErrors[0]?.reason ?? "입력값을 확인해 주세요.";
  if (error.status === 401) return "로그인이 만료됐어요. 다시 로그인해 주세요.";
  if (error.status === 404) return "항목을 찾을 수 없거나 접근할 수 없어요.";
  if (error.code === "REVISION_CONFLICT") return "다른 곳에서 내용이 변경됐어요. 최신 정보를 불러와 주세요.";
  if (error.code === "WATCH_INTENT_NOT_CONFIRMABLE") return "이미 응답했거나 확인 가능한 시간이 지났어요.";
  if (error.status === 503) return "평가 서비스를 잠시 이용할 수 없어요. 입력값은 그대로 두었습니다.";
  return error.message;
}
