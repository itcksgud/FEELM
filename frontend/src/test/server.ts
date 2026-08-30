import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  availability,
  confirmationWatched,
  countries,
  detail,
  filmPage,
  frameDetail,
  genres,
  movieCard,
  pendingConfirmations,
  popcornBucket,
  providers,
  ratingDeletionResult,
  ratingMutationResult,
  ratingPage,
  searchPage,
  similar,
  tasteProfile,
  unratedViewingRecords,
  watchIntentClickResult,
} from "./fixtures";

export const handlers = [
  http.get("http://localhost/api/v1/movies", () => HttpResponse.json(searchPage([movieCard()]))),
  http.get("http://localhost/api/v1/movies/:movieId/similar", () => HttpResponse.json(similar)),
  http.get("http://localhost/api/v1/movies/:movieId/ott-offers", () => HttpResponse.json(availability())),
  http.get("http://localhost/api/v1/movies/:movieId", ({ params }) => HttpResponse.json({ ...detail, movieId: String(params.movieId) })),
  http.get("http://localhost/api/v1/catalog/genres", () => HttpResponse.json(genres)),
  http.get("http://localhost/api/v1/catalog/countries", () => HttpResponse.json(countries)),
  http.get("http://localhost/api/v1/ott-providers", () => HttpResponse.json(providers)),
  http.post("http://localhost/api/v1/watch-intents", () => HttpResponse.json(watchIntentClickResult, { status: 201 })),
  http.get("http://localhost/api/v1/me/watch-intents/pending-confirmation", () => HttpResponse.json(pendingConfirmations)),
  http.post("http://localhost/api/v1/watch-intents/:watchIntentId/confirmation", async ({ request }) => {
    const body = await request.json() as { watched: boolean };
    return HttpResponse.json(body.watched ? confirmationWatched : { ...confirmationWatched, status: "CONFIRMED_NOT_WATCHED", viewingRecord: null });
  }),
  http.get("http://localhost/api/v1/me/viewing-records/unrated", () => HttpResponse.json(unratedViewingRecords)),
  http.get("http://localhost/api/v1/me/ratings", () => HttpResponse.json(ratingPage)),
  http.put("http://localhost/api/v1/me/ratings/:movieId", () => HttpResponse.json(ratingMutationResult)),
  http.delete("http://localhost/api/v1/me/ratings/:movieId", () => HttpResponse.json(ratingDeletionResult)),
  http.get("http://localhost/api/v1/me/film", () => HttpResponse.json(filmPage)),
  http.get("http://localhost/api/v1/me/film/frames/:frameId", () => HttpResponse.json(frameDetail)),
  http.get("http://localhost/api/v1/me/popcorn-bucket", () => HttpResponse.json(popcornBucket)),
  http.get("http://localhost/api/v1/me/taste-profile", () => HttpResponse.json(tasteProfile)),
];

export const server = setupServer(...handlers);
