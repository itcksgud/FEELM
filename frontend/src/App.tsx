import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { SearchHomePage } from "./pages/SearchHomePage";
import { SearchResultsPage } from "./pages/SearchResultsPage";
import { MovieDetailPage } from "./pages/MovieDetailPage";
import {
  FilmPage,
  FrameDetailPage,
  PopcornBucketPage,
  RatingCompletePage,
  RatingEditorPage,
  RatingsPage,
  WatchConfirmationPage,
  WatchConfirmationsPage,
} from "./pages/C1Pages";
import { RecommendationsPage } from "./pages/RecommendationsPage";
import {
  OttComparisonCreatePage,
  OttComparisonMoviesPage,
  OttComparisonPage,
  PartiesPage,
  PartyBaselinePage,
  PartyCreatePage,
  PartyDetailPage,
  PartyInvitationsPage,
} from "./pages/C3Pages";
import {
  LoginPage,
  MembershipPage,
  OnboardingCompletePage,
  OnboardingMoviesPage,
  OnboardingOttPage,
  ProtectedC4Route,
  SignUpPage,
  VerifyEmailPage,
} from "./pages/C4Pages";
import {
  NotificationsPage,
  PrivacyPage,
  PublicProfilePage,
  ReportDetailPage,
  ReportExportPage,
  ReportsPage,
  ReportSharePage,
  SharedReportPage,
} from "./pages/C5Pages";
import { localFeaturesEnabled } from "./config/localFeatures";

const compiledLocalFeatures = import.meta.env.DEV || import.meta.env.VITE_LOCAL_FEATURES_ENABLED === "true";
const RecEv008Lab = compiledLocalFeatures
  ? lazy(async () => ({ default: (await import("./evidence/RecEv008Lab")).RecEv008Lab }))
  : null;
const C6RecommendationInterpretationLab = compiledLocalFeatures
  ? lazy(async () => ({
      default: (await import("./pages/C6RecommendationInterpretationLab")).C6RecommendationInterpretationLab,
    }))
  : null;

export function App({ enableLocalFeatures = localFeaturesEnabled }: { enableLocalFeatures?: boolean }) {
  return (
    <Routes>
      <Route path="/search" element={<SearchHomePage />} />
      <Route path="/search/results" element={<SearchResultsPage />} />
      <Route path="/movies/:movieId" element={<MovieDetailPage />} />
      <Route path="/me/watch-confirmations" element={<WatchConfirmationsPage />} />
      <Route path="/me/watch-confirmations/:watchIntentId" element={<WatchConfirmationPage />} />
      <Route path="/me/movies/:movieId/rating" element={<RatingEditorPage />} />
      <Route path="/me/rating-complete/:movieId" element={<RatingCompletePage />} />
      <Route path="/me/film" element={<FilmPage />} />
      <Route path="/me/film/frames/:frameId" element={<FrameDetailPage />} />
      <Route path="/me/ratings" element={<RatingsPage />} />
      <Route path="/me/popcorn-bucket" element={<PopcornBucketPage />} />
      <Route path="/me/recommendations" element={<RecommendationsPage />} />
      <Route path="/sign-up" element={<SignUpPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/me/profile" element={<ProtectedC4Route><MembershipPage /></ProtectedC4Route>} />
      <Route path="/onboarding/movies" element={<ProtectedC4Route><OnboardingMoviesPage /></ProtectedC4Route>} />
      <Route path="/onboarding/ott" element={<ProtectedC4Route><OnboardingOttPage /></ProtectedC4Route>} />
      <Route path="/onboarding/complete" element={<ProtectedC4Route><OnboardingCompletePage /></ProtectedC4Route>} />
      <Route path="/me/reports" element={<ProtectedC4Route><ReportsPage /></ProtectedC4Route>} />
      <Route path="/me/reports/:reportId" element={<ProtectedC4Route><ReportDetailPage /></ProtectedC4Route>} />
      <Route path="/me/privacy" element={<ProtectedC4Route><PrivacyPage /></ProtectedC4Route>} />
      <Route path="/people/:publicProfileId" element={<PublicProfilePage />} />
      <Route path="/me/notifications" element={<ProtectedC4Route><NotificationsPage /></ProtectedC4Route>} />
      {enableLocalFeatures && <>
        <Route path="/me/reports/:reportId/export" element={<ProtectedC4Route><ReportExportPage /></ProtectedC4Route>} />
        <Route path="/me/reports/:reportId/share" element={<ProtectedC4Route><ReportSharePage /></ProtectedC4Route>} />
        <Route path="/shared-report" element={<SharedReportPage />} />
        <Route path="/me/ott-comparisons/new" element={<OttComparisonCreatePage />} />
        <Route path="/me/ott-comparisons/:comparisonId" element={<OttComparisonPage />} />
        <Route path="/me/ott-comparisons/:comparisonId/providers/:providerId/movies" element={<OttComparisonMoviesPage />} />
        <Route path="/me/parties" element={<PartiesPage />} />
        <Route path="/me/parties/new" element={<PartyCreatePage />} />
        <Route path="/me/party-invitations" element={<PartyInvitationsPage />} />
        <Route path="/parties/:partyId" element={<PartyDetailPage />} />
        <Route path="/parties/:partyId/baseline-recommendations" element={<PartyBaselinePage />} />
      </>}
      {enableLocalFeatures && RecEv008Lab && (
        <Route path="/__evidence/rec-ev-008" element={<Suspense fallback={null}><RecEv008Lab /></Suspense>} />
      )}
      {enableLocalFeatures && C6RecommendationInterpretationLab && (
        <Route path="/__experiments/recommendation-interpretation" element={<Suspense fallback={null}><C6RecommendationInterpretationLab /></Suspense>} />
      )}
      <Route path="*" element={<Navigate to="/search" replace />} />
    </Routes>
  );
}
