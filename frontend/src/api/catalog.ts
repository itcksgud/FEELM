import createClient, { type Client } from "openapi-fetch";
import type { components, operations, paths } from "./schema";

export type MovieCardData = components["schemas"]["MovieCard"];
export type MovieDetailData = components["schemas"]["MovieDetail"];
export type MovieSearchPage = components["schemas"]["MovieSearchPage"];
export type SimilarMovieResponse = components["schemas"]["SimilarMovieResponse"];
export type OttAvailability = components["schemas"]["OttAvailability"];
export type GenreListResponse = components["schemas"]["GenreListResponse"];
export type CountryListResponse = components["schemas"]["CountryListResponse"];
export type OttProviderListResponse = components["schemas"]["OttProviderListResponse"];
export type ErrorResponse = components["schemas"]["ErrorResponse"];
export type MonetizationType = components["schemas"]["MonetizationType"];
export type MovieSort = components["schemas"]["MovieSort"];
export type SearchMoviesParams = operations["searchMovies"]["parameters"]["query"];

export class CatalogApiError extends Error {
  readonly status: number;
  readonly code?: ErrorResponse["code"];
  readonly fieldErrors: ErrorResponse["fieldErrors"];

  constructor(status: number, payload?: ErrorResponse) {
    super(payload?.message ?? "영화 정보를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.");
    this.name = "CatalogApiError";
    this.status = status;
    this.code = payload?.code;
    this.fieldErrors = payload?.fieldErrors ?? [];
  }
}

export interface CatalogApi {
  searchMovies(params: SearchMoviesParams, signal?: AbortSignal): Promise<MovieSearchPage>;
  getMovie(movieId: string, signal?: AbortSignal): Promise<MovieDetailData>;
  getSimilarMovies(movieId: string, limit?: number, signal?: AbortSignal): Promise<SimilarMovieResponse>;
  getMovieOttOffers(movieId: string, signal?: AbortSignal): Promise<OttAvailability>;
  listGenres(signal?: AbortSignal): Promise<GenreListResponse>;
  listCountries(signal?: AbortSignal): Promise<CountryListResponse>;
  listOttProviders(signal?: AbortSignal): Promise<OttProviderListResponse>;
}

type FetchResult<T> = {
  data?: T;
  error?: unknown;
  response: Response;
};

function unwrap<T>({ data, error, response }: FetchResult<T>): T {
  if (data !== undefined) return data;
  throw new CatalogApiError(response.status, error as ErrorResponse | undefined);
}

export class HttpCatalogApi implements CatalogApi {
  private readonly client: Client<paths>;

  constructor(baseUrl = import.meta.env.VITE_API_BASE_URL ?? "") {
    this.client = createClient<paths>({ baseUrl });
  }

  async searchMovies(params: SearchMoviesParams, signal?: AbortSignal) {
    return unwrap(
      await this.client.GET("/api/v1/movies", {
        params: { query: params },
        signal,
      }),
    );
  }

  async getMovie(movieId: string, signal?: AbortSignal) {
    return unwrap(
      await this.client.GET("/api/v1/movies/{movieId}", {
        params: { path: { movieId } },
        signal,
      }),
    );
  }

  async getSimilarMovies(movieId: string, limit = 10, signal?: AbortSignal) {
    return unwrap(
      await this.client.GET("/api/v1/movies/{movieId}/similar", {
        params: { path: { movieId }, query: { limit } },
        signal,
      }),
    );
  }

  async getMovieOttOffers(movieId: string, signal?: AbortSignal) {
    return unwrap(
      await this.client.GET("/api/v1/movies/{movieId}/ott-offers", {
        params: { path: { movieId } },
        signal,
      }),
    );
  }

  async listGenres(signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/catalog/genres", { signal }));
  }

  async listCountries(signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/catalog/countries", { signal }));
  }

  async listOttProviders(signal?: AbortSignal) {
    return unwrap(await this.client.GET("/api/v1/ott-providers", { signal }));
  }
}
