import type {
  CountryListResponse,
  GenreListResponse,
  MovieCardData,
  MovieDetailData,
  MovieSearchPage,
  OttAvailability,
  OttProviderListResponse,
  SimilarMovieResponse,
} from "../api/catalog";
import type {
  FilmPage,
  FrameDetail,
  PendingWatchConfirmationPage,
  PopcornBucket,
  RatingDeletionResult,
  RatingMutationResult,
  RatingPage,
  TasteProfile,
  UnratedViewingRecordPage,
  WatchConfirmationResult,
  WatchIntentClickResult,
} from "../api/c1";

export const ids = {
  movieOne: "6b226903-0ca4-4f5a-9bf0-50d6cedd224c",
  movieTwo: "e67778c9-7b2e-42d4-9d3e-a3026b2efea3",
  genre: "2d07d5d3-486f-4638-9d58-49331e798c76",
  provider: "d392a4d5-0428-4e06-aa41-aef899c06842",
  offer: "599b7559-5e80-48f5-8322-f6d4d567c1e8",
  watchIntent: "2dfa8b82-9f40-452d-a63f-18347483f7b7",
  viewingRecord: "531a4e1d-2da8-48f1-a702-79fd875793d3",
  rating: "0527c943-fb46-4aa5-aea2-130bdc752e75",
  frame: "2b480314-590c-4d9a-b5df-1ef745c15e76",
  popcorn: "6de3b230-3c32-4917-a9d7-f18c9c0ab79b",
};

export function movieCard(overrides: Partial<MovieCardData> = {}): MovieCardData {
  return {
    movieId: ids.movieOne,
    displayTitle: "나우 유 씨 미",
    displayTitleLocale: "ko-KR",
    releaseYear: 2013,
    posterUrl: "https://image.tmdb.org/t/p/w500/poster.jpg",
    genres: [{ genreId: ids.genre, name: "범죄" }],
    externalRating: { source: "TMDB", value: 7.3, scale: 10, ratingCount: 12_800 },
    availability: {
      region: "KR",
      availabilityStatus: "LISTED",
      freshness: "FRESH",
      snapshotAt: "2026-08-29T06:00:00Z",
      flatrateProviders: [{ providerId: ids.provider, name: "Netflix", logoUrl: null, isSubscribed: null }],
    },
    ...overrides,
  };
}

export function searchPage(items: MovieCardData[], nextCursor: string | null = null): MovieSearchPage {
  return {
    catalogVersion: "catalog-20260829-01",
    totalCount: items.length + (nextCursor ? 1 : 0),
    hasNext: Boolean(nextCursor),
    nextCursor,
    items,
    appliedFilters: {
      query: null,
      genreIds: [],
      countryCodes: [],
      releaseYearFrom: null,
      releaseYearTo: null,
      ottProviderIds: [],
      ottMonetizationTypes: ["FLATRATE"],
      sort: "POPULARITY",
    },
  };
}

export const detail: MovieDetailData = {
  catalogVersion: "catalog-20260829-01",
  movieId: ids.movieOne,
  displayTitle: "나우 유 씨 미",
  displayTitleLocale: "ko-KR",
  originalTitle: "Now You See Me",
  overview: "네 명의 마술사가 펼치는 범죄와 반전의 이야기.",
  overviewLocale: "ko-KR",
  releaseDate: "2013-05-29",
  runtimeMinutes: 115,
  posterUrl: null,
  backdropUrl: null,
  genres: [{ genreId: ids.genre, name: "범죄" }],
  productionCountries: [{ code: "US", name: "미국" }],
  directors: [{ personId: "66a7f6cc-0bc7-48e6-b244-36d96b5f13c7", name: "Louis Leterrier", role: "DIRECTOR", character: null, order: 0 }],
  cast: [{ personId: "20e3ae27-e701-4a43-bf4e-6d04e3d2f88d", name: "Jesse Eisenberg", role: "CAST", character: "J. Daniel Atlas", order: 0 }],
  externalRating: { source: "TMDB", value: 7.3, scale: 10, ratingCount: 12_800 },
  availability: movieCard().availability,
  metadataAsOf: "2026-08-29T06:00:00Z",
};

export function availability(overrides: Partial<OttAvailability> = {}): OttAvailability {
  return {
    catalogVersion: "catalog-20260829-01",
    movieId: ids.movieOne,
    region: "KR",
    availabilityStatus: "LISTED",
    freshness: "FRESH",
    snapshotAt: "2026-08-29T06:00:00Z",
    source: "TMDB_JUSTWATCH",
    groups: [{
      monetizationType: "FLATRATE",
      offers: [{
        offerId: ids.offer,
        providerId: ids.provider,
        providerName: "Netflix",
        logoUrl: null,
        monetizationType: "FLATRATE",
        isSubscribed: true,
        link: { type: "AGGREGATOR", url: "https://www.themoviedb.org/movie/75656/watch" },
      }],
    }],
    ...overrides,
  };
}

export const genres: GenreListResponse = {
  catalogVersion: "catalog-20260829-01",
  items: [{ genreId: ids.genre, name: "범죄" }],
};

export const countries: CountryListResponse = {
  catalogVersion: "catalog-20260829-01",
  items: [{ code: "US", name: "미국" }, { code: "KR", name: "대한민국" }],
};

export const providers: OttProviderListResponse = {
  catalogVersion: "catalog-20260829-01",
  region: "KR",
  items: [{ providerId: ids.provider, name: "Netflix", logoUrl: null, displayPriority: 10, isSubscribed: null }],
};

export const similar: SimilarMovieResponse = {
  catalogVersion: "catalog-20260829-01",
  similarityVersion: "sim-fixture-v1",
  items: [{
    movie: movieCard({ movieId: ids.movieTwo, displayTitle: "프레스티지" }),
    reasons: [{ code: "SHARED_GENRE", label: "같은 범죄 장르" }, { code: "SHARED_DIRECTOR", label: "같은 감독" }],
  }],
};

export const c1Movie = {
  movieId: ids.movieOne,
  displayTitle: "나우 유 씨 미",
  posterUrl: null,
  releaseYear: 2013,
};

export const pendingConfirmations: PendingWatchConfirmationPage = {
  totalCount: 1,
  hasNext: false,
  nextCursor: null,
  items: [{
    watchIntentId: ids.watchIntent,
    movie: c1Movie,
    provider: { providerId: ids.provider, name: "Netflix" },
    clickedAt: "2026-08-27T11:00:00Z",
    confirmationDueAt: "2026-08-29T11:00:00Z",
    expiresAt: "2026-09-03T11:00:00Z",
    revision: 1,
  }],
};

export const unratedViewingRecords: UnratedViewingRecordPage = {
  totalCount: 1,
  hasNext: false,
  nextCursor: null,
  items: [{
    viewingRecordId: ids.viewingRecord,
    movie: c1Movie,
    watchedConfirmedAt: "2026-08-29T12:00:00Z",
    provider: { providerId: ids.provider, name: "Netflix" },
    revision: 1,
  }],
};

export const ratingPage: RatingPage = {
  totalCount: 1,
  hasNext: false,
  nextCursor: null,
  items: [{
    rating: {
      ratingId: ids.rating,
      movieId: ids.movieOne,
      value: 4,
      revision: 2,
      createdAt: "2026-08-29T12:00:00Z",
      updatedAt: "2026-08-29T13:00:00Z",
    },
    movie: c1Movie,
    watchedConfirmedAt: "2026-08-29T12:00:00Z",
    frameId: ids.frame,
  }],
};

export const filmPage: FilmPage = {
  totalCount: 1,
  hasNext: false,
  nextCursor: null,
  filmRevision: 4,
  items: [{
    frameId: ids.frame,
    movie: c1Movie,
    myRating: 4,
    watchedConfirmedAt: "2026-08-29T12:00:00Z",
    createdAt: "2026-08-29T13:00:00Z",
  }],
};

export const frameDetail: FrameDetail = {
  frameId: ids.frame,
  movie: c1Movie,
  rating: ratingPage.items[0].rating,
  watchedConfirmedAt: "2026-08-29T12:00:00Z",
  provider: { providerId: ids.provider, name: "Netflix" },
  createdAt: "2026-08-29T13:00:00Z",
  derivationVersion: "c1-v1",
};

const flavorData: Array<[PopcornBucket["flavors"][number]["code"], string, string]> = [
  ["ADRENALINE", "짜릿함", "#d45c45"],
  ["WONDER", "상상", "#7665bb"],
  ["JOY", "유쾌함", "#e2a934"],
  ["HEART", "여운", "#c9798e"],
  ["SHADOW", "긴장", "#41424a"],
  ["REAL", "현실", "#579a8b"],
  ["LEGACY", "시대", "#9b7655"],
  ["RHYTHM", "리듬", "#4f7faf"],
];

export const popcornBucket: PopcornBucket = {
  totalCount: 1,
  mappingVersion: "v1",
  aggregateRevision: 3,
  flavors: flavorData.map(([code, displayName, colorToken], index) => ({
    flavorId: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    code,
    displayName,
    colorToken,
    count: code === "SHADOW" ? 1 : 0,
    ratingCount: code === "SHADOW" ? 1 : 0,
    averageRating: code === "SHADOW" ? 4 : null,
  })),
};

export const tasteProfile: TasteProfile = {
  derivationVersion: "taste-v1",
  aggregateRevision: 3,
  items: [
    { dimensionType: "GENRE", dimensionKey: ids.genre, displayName: "범죄", ratingCount: 1, averageRating: 4 },
    { dimensionType: "COUNTRY", dimensionKey: "US", displayName: "미국", ratingCount: 1, averageRating: 4 },
    { dimensionType: "DIRECTOR", dimensionKey: "director-local", displayName: "Louis Leterrier", ratingCount: 1, averageRating: 4 },
  ],
};

export const watchIntentClickResult: WatchIntentClickResult = {
  outcome: "CREATED",
  movieId: ids.movieOne,
  providerId: ids.provider,
  watchIntent: {
    watchIntentId: ids.watchIntent,
    status: "LINK_CLICKED",
    clickedAt: "2026-08-29T12:00:00Z",
    confirmationDueAt: "2026-08-31T12:00:00Z",
    expiresAt: "2026-09-05T12:00:00Z",
    revision: 1,
  },
  destination: { linkType: "AGGREGATOR", url: "https://www.themoviedb.org/movie/75656/watch", externalNavigation: true },
};

export const confirmationWatched: WatchConfirmationResult = {
  watchIntentId: ids.watchIntent,
  status: "CONFIRMED_WATCHED",
  respondedAt: "2026-08-29T12:00:00Z",
  revision: 2,
  viewingRecord: {
    viewingRecordId: ids.viewingRecord,
    movieId: ids.movieOne,
    status: "WATCHED_CONFIRMED",
    watchedConfirmedAt: "2026-08-29T12:00:00Z",
    provider: { providerId: ids.provider, name: "Netflix" },
    revision: 1,
  },
};

export const ratingMutationResult: RatingMutationResult = {
  mutation: "CREATED",
  rating: ratingPage.items[0].rating,
  derivedState: {
    viewingStatus: "RATED_COMPLETED",
    frameId: ids.frame,
    popcornId: ids.popcorn,
    filmTotalCount: 1,
    aggregateRevision: 3,
    recommendationRefresh: "QUEUED",
  },
};

export const ratingDeletionResult: RatingDeletionResult = {
  movieId: ids.movieOne,
  ratingRemoved: true,
  viewingStatus: "WATCHED_CONFIRMED",
  frameActive: false,
  popcornActive: false,
  filmTotalCount: 0,
  aggregateRevision: 4,
  recommendationRefresh: "QUEUED",
};
