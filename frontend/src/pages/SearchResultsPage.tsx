import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CatalogApiError, type MovieSort } from "../api/catalog";
import { useCatalogApi } from "../api/CatalogApiContext";
import { AppHeader, ErrorPanel, InlineLoader, MovieGrid, MovieGridSkeleton, SearchBox } from "../components/CatalogUi";
import { FilterSheet } from "../components/FilterSheet";
import { rememberQuery } from "../search/recentQueries";
import { activeFilterCount, emptyFilters, parseSearchState, serializeSearchState, toApiParams } from "../search/searchState";
import { useSearchScrollRestoration } from "../search/useSearchScrollRestoration";
import styles from "../styles/catalog.module.css";

const SORT_LABELS: Record<MovieSort, string> = {
  RELEVANCE: "관련도순",
  POPULARITY: "인기순",
  RELEASE_DATE_DESC: "최신순",
  RATING_COUNT_DESC: "평가 많은순",
};

export function SearchResultsPage() {
  const api = useCatalogApi();
  const [params, setParams] = useSearchParams();
  const paramsKey = params.toString();
  const state = useMemo(() => parseSearchState(new URLSearchParams(paramsKey)), [paramsKey]);
  const [input, setInput] = useState(state.query);
  const [filterOpen, setFilterOpen] = useState(false);
  const sentinel = useRef<HTMLButtonElement>(null);

  useEffect(() => setInput(state.query), [state.query]);

  useEffect(() => {
    const normalized = input.trim().slice(0, 100);
    if (normalized === state.query) return;
    const timer = window.setTimeout(() => {
      const next = { ...state, query: normalized, sort: undefined };
      setParams(serializeSearchState(next), { replace: true });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [input, setParams, state]);

  const query = useInfiniteQuery({
    queryKey: ["movie-search", paramsKey],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.searchMovies(toApiParams(state, pageParam ?? undefined), signal),
    getNextPageParam: (lastPage) => lastPage.hasNext ? lastPage.nextCursor : undefined,
  });

  const movies = query.data?.pages.flatMap((page) => page.items) ?? [];
  const totalCount = query.data?.pages[0]?.totalCount ?? 0;
  useSearchScrollRestoration(paramsKey || "popular", query.isSuccess);

  useEffect(() => {
    const element = sentinel.current;
    if (!element || !query.hasNextPage || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !query.isFetchingNextPage) void query.fetchNextPage();
    }, { rootMargin: "240px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, [query.fetchNextPage, query.hasNextPage, query.isFetchingNextPage]);

  function submit(value: string) {
    rememberQuery(value);
    setParams(serializeSearchState({ ...state, query: value, sort: undefined }));
  }

  const error = query.error instanceof CatalogApiError ? query.error : undefined;
  const validationError = error?.status === 400;

  return (
    <main className={styles.page}>
      <div className={styles.pageInner}>
        <AppHeader compact />
        <div className={styles.resultsToolbar}>
          <SearchBox value={input} onChange={setInput} onSubmit={submit} />
          <div className={styles.resultControls}>
            <button className={styles.filterButton} type="button" onClick={() => setFilterOpen(true)}>
              필터{activeFilterCount(state) > 0 && <span>{activeFilterCount(state)}</span>}
            </button>
            <label className={styles.sortControl}>
              <span className={styles.srOnly}>정렬</span>
              <select
                value={state.sort ?? (state.query ? "RELEVANCE" : "POPULARITY")}
                onChange={(event) => setParams(serializeSearchState({ ...state, sort: event.target.value as MovieSort }))}
              >
                {(Object.keys(SORT_LABELS) as MovieSort[]).map((sort) => <option value={sort} key={sort}>{SORT_LABELS[sort]}</option>)}
              </select>
            </label>
          </div>
        </div>

        <section className={styles.resultsSection} aria-labelledby="results-title">
          <div className={styles.sectionHeading}>
            <div>
              <p className={styles.eyebrow}>CATALOG</p>
              <h1 id="results-title">{state.query ? `‘${state.query}’ 검색 결과` : "영화 둘러보기"}</h1>
            </div>
            {query.isSuccess && <p className={styles.resultCount}>{totalCount.toLocaleString("ko-KR")}편</p>}
          </div>

          {query.isPending && <MovieGridSkeleton />}
          {query.isError && (
            <ErrorPanel
              title={validationError ? "필터 값을 확인해 주세요" : "검색 결과를 불러오지 못했어요"}
              message={validationError ? "잘못된 필터를 초기화하거나 수정한 뒤 다시 시도해 주세요." : "검색어와 필터는 그대로 보관했어요."}
              onRetry={validationError ? undefined : () => query.refetch()}
              action={validationError ? <button className={styles.secondaryButton} type="button" onClick={() => setParams(serializeSearchState(emptyFilters(state.query)))}>필터 초기화</button> : undefined}
            />
          )}
          {query.isSuccess && movies.length === 0 && (
            <section className={styles.statePanel} role="status">
              <span className={styles.stateIcon} aria-hidden="true">0</span>
              <h2>검색 결과가 없어요</h2>
              <p>다른 검색어나 더 넓은 조건으로 찾아보세요.</p>
              <button className={styles.primaryButton} type="button" onClick={() => setParams(serializeSearchState(emptyFilters(state.query)))}>필터 초기화</button>
            </section>
          )}
          {movies.length > 0 && <MovieGrid movies={movies} />}
          {query.hasNextPage && (
            <button
              ref={sentinel}
              className={styles.loadMoreButton}
              type="button"
              disabled={query.isFetchingNextPage}
              onClick={() => query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? <InlineLoader label="다음 영화 불러오는 중" /> : "더 보기"}
            </button>
          )}
        </section>
      </div>

      <FilterSheet
        open={filterOpen}
        value={state}
        onClose={() => setFilterOpen(false)}
        onApply={(next) => { setParams(serializeSearchState(next)); setFilterOpen(false); }}
      />
    </main>
  );
}
