import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { MovieCardData, SearchMoviesParams } from "../api/catalog";
import { useCatalogApi } from "../api/CatalogApiContext";
import styles from "../styles/catalog.module.css";
import { localFeaturesEnabled } from "../config/localFeatures";

export function AppHeader({ compact = false }: { compact?: boolean }) {
  return (
    <header className={styles.appHeader}>
      <Link className={styles.brand} to="/search" aria-label="FEELM 검색 홈">
        <span className={styles.brandMark} aria-hidden="true">F</span>
        <span>FEELM</span>
      </Link>
      <div className={styles.headerActions}>
        {!compact && <p className={styles.tagline}>영화를 발견하는 새로운 감각</p>}
        <nav className={styles.headerNav} aria-label="주요 메뉴">
          <Link className={styles.headerNavLink} to="/me/recommendations">영화 추천</Link>
          <Link className={styles.headerNavLink} to="/me/profile">내 계정</Link>
          <Link className={styles.headerNavLink} to="/me/reports">리포트</Link>
          <Link className={styles.headerNavLink} to="/me/notifications">알림</Link>
          {localFeaturesEnabled && <Link className={styles.headerNavLink} to="/me/parties">Party</Link>}
        </nav>
      </div>
    </header>
  );
}

export function SearchBox({
  value,
  onChange,
  onSubmit,
  autoFocus = false,
  label = "영화 검색",
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  autoFocus?: boolean;
  label?: string;
}) {
  function submit(event: FormEvent) {
    event.preventDefault();
    const normalized = value.trim();
    if (normalized) onSubmit(normalized);
  }

  return (
    <form className={styles.searchBox} role="search" onSubmit={submit}>
      <span className={styles.searchIcon} aria-hidden="true">⌕</span>
      <label className={styles.srOnly} htmlFor="catalog-search">{label}</label>
      <input
        id="catalog-search"
        value={value}
        onChange={(event) => onChange(event.target.value.slice(0, 100))}
        placeholder="제목, 감독, 배우를 검색해 보세요"
        autoComplete="off"
        autoFocus={autoFocus}
      />
      {value && (
        <button className={styles.clearButton} type="button" onClick={() => onChange("")} aria-label="검색어 지우기">
          ×
        </button>
      )}
    </form>
  );
}

export function Poster({ src, title, priority = false }: { src: string | null; title: string; priority?: boolean }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return (
    <div className={styles.posterFrame}>
      <img
        src={!src || failed ? "/poster-placeholder.svg" : src}
        alt={src && !failed ? `${title} 포스터` : `${title} 포스터 없음`}
        loading={priority ? "eager" : "lazy"}
        onError={() => setFailed(true)}
      />
    </div>
  );
}

export function MovieCard({ movie, priority = false }: { movie: MovieCardData; priority?: boolean }) {
  const location = useLocation();
  const badges = movie.availability.flatrateProviders.slice(0, 3);
  return (
    <article className={styles.movieCard}>
      <Link
        className={styles.movieCardLink}
        to={`/movies/${movie.movieId}`}
        state={{ fromCatalog: true, fromPath: `${location.pathname}${location.search}` }}
        aria-label={`${movie.displayTitle} 상세 보기`}
      >
        <Poster src={movie.posterUrl} title={movie.displayTitle} priority={priority} />
        <div className={styles.movieCardBody}>
          <h3>{movie.displayTitle}</h3>
          <p className={styles.movieMeta}>
            {[movie.releaseYear, movie.genres[0]?.name].filter(Boolean).join(" · ") || "영화 정보 확인 중"}
          </p>
          {movie.externalRating && (
            <p className={styles.externalRating} aria-label={`TMDB 평점 ${movie.externalRating.value} / ${movie.externalRating.scale}`}>
              <span aria-hidden="true">★</span> TMDB {movie.externalRating.value}/{movie.externalRating.scale}
            </p>
          )}
          <div className={styles.providerBadges} aria-label="한국 정액제 OTT">
            {movie.availability.availabilityStatus === "LISTED" && badges.map((provider) => (
              <span key={provider.providerId} className={provider.isSubscribed ? styles.subscribedBadge : styles.providerBadge}>
                {provider.name}
              </span>
            ))}
            {movie.availability.availabilityStatus === "UNKNOWN" && <span className={styles.availabilityNote}>OTT 정보 확인 중</span>}
            {movie.availability.availabilityStatus === "NONE_LISTED" && <span className={styles.availabilityNote}>등록된 한국 OTT 없음</span>}
          </div>
        </div>
      </Link>
    </article>
  );
}

export function MovieGrid({ movies }: { movies: MovieCardData[] }) {
  return (
    <div className={styles.movieGrid}>
      {movies.map((movie, index) => <MovieCard key={movie.movieId} movie={movie} priority={index < 4} />)}
    </div>
  );
}

export function MovieGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className={styles.movieGrid} aria-label="영화 목록 불러오는 중" aria-busy="true">
      {Array.from({ length: count }, (_, index) => (
        <div className={styles.movieSkeleton} key={index}>
          <div className={styles.skeletonPoster} />
          <div className={styles.skeletonLine} />
          <div className={styles.skeletonLineShort} />
        </div>
      ))}
    </div>
  );
}

export function InlineLoader({ label = "불러오는 중" }: { label?: string }) {
  return <div className={styles.inlineLoader} role="status" aria-label={label}><span className={styles.spinner} />{label}</div>;
}

export function ErrorPanel({
  title = "영화 정보를 불러오지 못했어요",
  message = "잠시 후 다시 시도해 주세요.",
  onRetry,
  action,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
  action?: ReactNode;
}) {
  return (
    <section className={styles.statePanel} role="alert">
      <span className={styles.stateIcon} aria-hidden="true">!</span>
      <h2>{title}</h2>
      <p>{message}</p>
      {onRetry && <button className={styles.primaryButton} type="button" onClick={onRetry}>다시 시도</button>}
      {action}
    </section>
  );
}

export function PopularMovies() {
  const api = useCatalogApi();
  const query = useQuery({
    queryKey: ["popular-movies"],
    queryFn: ({ signal }) => api.searchMovies({ sort: "POPULARITY", limit: 6 } satisfies SearchMoviesParams, signal),
  });

  if (query.isPending) return (
    <section className={styles.homeSection} aria-labelledby="popular-title">
      <div className={styles.sectionHeading}><div><p className={styles.eyebrow}>POPULAR NOW</p><h2 id="popular-title">인기 영화</h2></div></div>
      <MovieGridSkeleton />
    </section>
  );
  if (query.isError) {
    return (
      <section className={styles.homeSection} aria-labelledby="popular-title">
        <div className={styles.sectionHeading}><div><p className={styles.eyebrow}>POPULAR NOW</p><h2 id="popular-title">인기 영화</h2></div></div>
        <ErrorPanel title="인기 영화를 불러오지 못했어요" message="검색은 계속 이용할 수 있어요." onRetry={() => query.refetch()} />
      </section>
    );
  }
  if (!query.data.items.length) return null;
  return (
    <section className={styles.homeSection} aria-labelledby="popular-title">
      <div className={styles.sectionHeading}><div><p className={styles.eyebrow}>POPULAR NOW</p><h2 id="popular-title">인기 영화</h2></div></div>
      <MovieGrid movies={query.data.items.slice(0, 6)} />
    </section>
  );
}
