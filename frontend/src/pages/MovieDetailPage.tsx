import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { CatalogApiError } from "../api/catalog";
import { useCatalogApi } from "../api/CatalogApiContext";
import { AppHeader, ErrorPanel, InlineLoader, Poster } from "../components/CatalogUi";
import { OttSection } from "../components/OttSection";
import { SimilarMovies } from "../components/SimilarMovies";
import styles from "../styles/catalog.module.css";

function formatRuntime(minutes: number | null) {
  if (!minutes) return null;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours}시간 ${rest ? `${rest}분` : ""}`.trim() : `${rest}분`;
}

export function MovieDetailPage() {
  const { movieId = "" } = useParams();
  const api = useCatalogApi();
  const location = useLocation();
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["movie", movieId, "detail"],
    queryFn: ({ signal }) => api.getMovie(movieId, signal),
    retry: (count, error) => !(error instanceof CatalogApiError && error.status === 404) && count < 1,
  });

  function goBack() {
    const state = location.state as { fromCatalog?: boolean } | null;
    if (state?.fromCatalog) navigate(-1);
    else navigate("/search");
  }

  if (query.isPending) {
    return <main className={styles.page}><div className={styles.pageInner}><AppHeader compact /><div className={styles.detailLoading}><InlineLoader label="영화 상세 불러오는 중" /></div></div></main>;
  }

  if (query.isError) {
    const notFound = query.error instanceof CatalogApiError && query.error.status === 404;
    return (
      <main className={styles.page}><div className={styles.pageInner}><AppHeader compact />
        <ErrorPanel
          title={notFound ? "영화 정보를 찾을 수 없어요" : "영화 정보를 불러오지 못했어요"}
          message={notFound ? "검색에서 다른 영화를 찾아보세요." : "잠시 후 다시 시도해 주세요."}
          onRetry={notFound ? undefined : () => query.refetch()}
          action={notFound ? <Link className={styles.primaryButton} to="/search">검색으로 돌아가기</Link> : undefined}
        />
      </div></main>
    );
  }

  const movie = query.data;
  const year = movie.releaseDate?.slice(0, 4);
  const metadata = [year, movie.genres[0]?.name, formatRuntime(movie.runtimeMinutes)].filter(Boolean).join(" · ");

  return (
    <main className={styles.detailPage}>
      <div className={styles.detailBackdrop} style={movie.backdropUrl ? { backgroundImage: `linear-gradient(to bottom, rgba(20,19,18,.2), #fbfaf8), url(${movie.backdropUrl})` } : undefined} />
      <div className={styles.pageInner}>
        <AppHeader compact />
        <button className={styles.backButton} type="button" onClick={goBack} aria-label="이전 화면으로 돌아가기">← <span>뒤로</span></button>
        <article className={styles.detailArticle}>
          <div className={styles.detailHero}>
            <div className={styles.detailPoster}><Poster src={movie.posterUrl} title={movie.displayTitle} priority /></div>
            <div className={styles.detailIntro}>
              <p className={styles.eyebrow}>{movie.originalTitle}</p>
              <h1>{movie.displayTitle}</h1>
              <p className={styles.detailMeta}>{metadata || "상세 정보 확인 중"}</p>
              {movie.externalRating && (
                <p className={styles.detailRating}><span aria-hidden="true">★</span> TMDB {movie.externalRating.value}/{movie.externalRating.scale}<small>{movie.externalRating.ratingCount.toLocaleString("ko-KR")}명 평가</small></p>
              )}
              <div className={styles.genreList}>{movie.genres.map((genre) => <span key={genre.genreId}>{genre.name}</span>)}</div>
            </div>
          </div>

          <section className={styles.detailSection} aria-labelledby="overview-title">
            <p className={styles.eyebrow}>STORY</p>
            <h2 id="overview-title">줄거리</h2>
            <p className={styles.overview}>{movie.overview}</p>
            {movie.overviewLocale !== "ko-KR" && <p className={styles.localeNotice}>{movie.overviewLocale} 원문으로 제공되는 줄거리예요.</p>}
          </section>

          <section className={styles.detailSection} aria-labelledby="credits-title">
            <p className={styles.eyebrow}>CAST &amp; CREW</p>
            <h2 id="credits-title">감독과 출연진</h2>
            <dl className={styles.credits}>
              <div><dt>감독</dt><dd>{movie.directors.map((person) => person.name).join(", ") || "정보 없음"}</dd></div>
              <div><dt>출연</dt><dd>{movie.cast.map((person) => person.character ? `${person.name} (${person.character})` : person.name).join(", ") || "정보 없음"}</dd></div>
              {movie.productionCountries.length > 0 && <div><dt>제작 국가</dt><dd>{movie.productionCountries.map((country) => country.name).join(", ")}</dd></div>}
            </dl>
          </section>

          <OttSection movieId={movieId} />
          <SimilarMovies movieId={movieId} />
        </article>
      </div>
    </main>
  );
}
