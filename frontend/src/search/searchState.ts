import type { MonetizationType, MovieSort, SearchMoviesParams } from "../api/catalog";

const SORTS: MovieSort[] = ["RELEVANCE", "POPULARITY", "RELEASE_DATE_DESC", "RATING_COUNT_DESC"];
const TYPES: MonetizationType[] = ["FLATRATE", "RENT", "BUY", "FREE", "ADS"];

export interface SearchState {
  query: string;
  genreIds: string[];
  countryCodes: string[];
  releaseYearFrom?: number;
  releaseYearTo?: number;
  ottProviderIds: string[];
  ottMonetizationTypes: MonetizationType[];
  sort?: MovieSort;
}

function validYear(value: string | null) {
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1870 && parsed <= 2100 ? parsed : undefined;
}

function unique(values: string[]) {
  return [...new Set(values.filter(Boolean))];
}

export function parseSearchState(params: URLSearchParams): SearchState {
  const query = (params.get("q") ?? "").trim().slice(0, 100);
  const sortCandidate = params.get("sort") as MovieSort | null;
  return {
    query,
    genreIds: unique(params.getAll("genre")),
    countryCodes: unique(params.getAll("country").filter((value) => /^[A-Z]{2}$/.test(value))),
    releaseYearFrom: validYear(params.get("yearFrom")),
    releaseYearTo: validYear(params.get("yearTo")),
    ottProviderIds: unique(params.getAll("ott")),
    ottMonetizationTypes: unique(params.getAll("type")).filter((value): value is MonetizationType =>
      TYPES.includes(value as MonetizationType),
    ),
    sort: sortCandidate && SORTS.includes(sortCandidate) ? sortCandidate : undefined,
  };
}

export function serializeSearchState(state: SearchState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  state.genreIds.forEach((value) => params.append("genre", value));
  state.countryCodes.forEach((value) => params.append("country", value));
  if (state.releaseYearFrom) params.set("yearFrom", String(state.releaseYearFrom));
  if (state.releaseYearTo) params.set("yearTo", String(state.releaseYearTo));
  state.ottProviderIds.forEach((value) => params.append("ott", value));
  state.ottMonetizationTypes.forEach((value) => params.append("type", value));
  if (state.sort) params.set("sort", state.sort);
  return params;
}

export function toApiParams(state: SearchState, cursor?: string): SearchMoviesParams {
  return {
    query: state.query || undefined,
    genreIds: state.genreIds.length ? state.genreIds : undefined,
    countryCodes: state.countryCodes.length ? state.countryCodes : undefined,
    releaseYearFrom: state.releaseYearFrom,
    releaseYearTo: state.releaseYearTo,
    ottProviderIds: state.ottProviderIds.length ? state.ottProviderIds : undefined,
    ottMonetizationTypes: state.ottMonetizationTypes.length ? state.ottMonetizationTypes : undefined,
    sort: state.sort ?? (state.query ? "RELEVANCE" : "POPULARITY"),
    cursor,
    limit: 20,
  };
}

export function activeFilterCount(state: SearchState) {
  return (
    state.genreIds.length +
    state.countryCodes.length +
    state.ottProviderIds.length +
    state.ottMonetizationTypes.length +
    Number(Boolean(state.releaseYearFrom)) +
    Number(Boolean(state.releaseYearTo))
  );
}

export function emptyFilters(query: string): SearchState {
  return {
    query,
    genreIds: [],
    countryCodes: [],
    ottProviderIds: [],
    ottMonetizationTypes: [],
  };
}
