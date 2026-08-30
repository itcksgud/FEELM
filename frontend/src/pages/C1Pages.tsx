import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  C1ApiError,
  c1ErrorMessage,
  type ConfirmWatchIntentInput,
  createIdempotencyKey,
  type DeleteRatingInput,
  type PutRatingInput,
  shouldRetryC1,
  type MovieSummary,
  type PendingWatchConfirmation,
  type Rating,
  type RatingItem,
  type RatingMutationResult,
  type TasteProfile,
  type UnratedViewingRecord,
} from "../api/c1";
import { useC1Api } from "../api/C1ApiContext";
import {
  C1ErrorState,
  C1Loader,
  C1PageShell,
  EmptyState,
  formatC1Date,
  MovieRecordCard,
  Notice,
  RatingPicker,
} from "../components/C1Ui";
import { Poster } from "../components/CatalogUi";
import styles from "../styles/c1.module.css";

const qk = {
  pending: ["c1", "pending"] as const,
  unrated: ["c1", "unrated"] as const,
  ratings: ["c1", "ratings"] as const,
  film: ["c1", "film"] as const,
  bucket: ["c1", "bucket"] as const,
  taste: ["c1", "taste"] as const,
};

function LoadMore({ hasNextPage, loading, onClick }: { hasNextPage: boolean; loading: boolean; onClick: () => void }) {
  if (!hasNextPage) return null;
  return <button className={styles.loadMore} type="button" disabled={loading} onClick={onClick}>{loading ? "불러오는 중" : "더 보기"}</button>;
}

function useInvalidateC1() {
  const queryClient = useQueryClient();
  return () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["c1"] }),
  ]);
}

export function WatchConfirmationsPage() {
  const api = useC1Api();
  const location = useLocation();
  const query = useInfiniteQuery({
    queryKey: qk.pending,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.listPendingWatchConfirmations(pageParam, signal),
    getNextPageParam: (page) => page.hasNext ? page.nextCursor ?? undefined : undefined,
    retry: shouldRetryC1,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const notice = (location.state as { notice?: string } | null)?.notice;

  return (
    <C1PageShell eyebrow="WATCH CHECK" title="영화, 잘 보셨나요?" description="OTT로 이동한 뒤 48시간이 지난 영화만 확인해요.">
      {notice && <Notice kind="success">{notice}</Notice>}
      {query.isPending && <C1Loader label="감상 확인 목록 불러오는 중" />}
      {query.isError && <C1ErrorState error={query.error} onRetry={() => query.refetch()} />}
      {query.isSuccess && items.length === 0 && (
        <EmptyState title="확인할 영화가 없어요" description="새로운 영화를 찾으면 시청 후 여기에 알려드릴게요." action={<Link className={styles.primaryButton} to="/search">영화 찾기</Link>} />
      )}
      {items.length > 0 && (
        <div className={styles.recordList}>
          {items.map((item) => (
            <MovieRecordCard
              key={item.watchIntentId}
              movie={item.movie}
              metadata={`${item.provider.name} · ${formatC1Date(item.clickedAt)} 이동`}
              action={<Link className={styles.primaryButton} to={`/me/watch-confirmations/${item.watchIntentId}`} state={{ pending: item }}>답하기</Link>}
            />
          ))}
        </div>
      )}
      <LoadMore hasNextPage={Boolean(query.hasNextPage)} loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()} />
    </C1PageShell>
  );
}

export function WatchConfirmationPage() {
  const { watchIntentId = "" } = useParams();
  const api = useC1Api();
  const navigate = useNavigate();
  const location = useLocation();
  const invalidate = useInvalidateC1();
  const stateItem = (location.state as { pending?: PendingWatchConfirmation } | null)?.pending;
  const query = useQuery({
    queryKey: [...qk.pending, "lookup", watchIntentId],
    queryFn: ({ signal }) => api.findPendingWatchConfirmation(watchIntentId, signal),
    enabled: !stateItem,
    retry: shouldRetryC1,
  });
  const item = stateItem ?? query.data;
  const mutation = useMutation({
    mutationFn: (input: ConfirmWatchIntentInput) => api.confirmWatchIntent(input),
    onSuccess: async (result, variables) => {
      await invalidate();
      if (variables.watched && result.viewingRecord && item) {
        navigate(`/me/movies/${item.movie.movieId}/rating`, {
          replace: true,
          state: {
            editor: {
              movie: item.movie,
              providerName: item.provider.name,
              watchedConfirmedAt: result.viewingRecord.watchedConfirmedAt,
            } satisfies RatingEditorState,
          },
        });
      } else {
        navigate("/me/watch-confirmations", { replace: true, state: { notice: "감상 여부를 기록했어요." } });
      }
    },
  });

  function respond(watched: boolean) {
    const retry = mutation.isError && mutation.variables?.watched === watched;
    mutation.mutate({
      watchIntentId,
      watched,
      expectedRevision: item?.revision ?? 1,
      idempotencyKey: retry ? mutation.variables.idempotencyKey : createIdempotencyKey("confirm"),
    });
  }

  if (!stateItem && query.isPending) return <C1PageShell eyebrow="WATCH CHECK" title="감상 확인"><C1Loader label="확인 항목 불러오는 중" /></C1PageShell>;
  if (!stateItem && query.isError) return <C1PageShell eyebrow="WATCH CHECK" title="감상 확인"><C1ErrorState error={query.error} onRetry={() => query.refetch()} /></C1PageShell>;
  if (!item) return <C1PageShell eyebrow="WATCH CHECK" title="감상 확인"><C1ErrorState error={new C1ApiError(404)} /></C1PageShell>;

  return (
    <C1PageShell eyebrow="WATCH CHECK" title="영화, 잘 보셨나요?" description={`${item.provider.name}에서 이동한 영화예요.`}>
      <section className={styles.confirmCard}>
        <div className={styles.confirmPoster}><Poster src={item.movie.posterUrl} title={item.movie.displayTitle} priority /></div>
        <div>
          <p>{item.movie.releaseYear ?? "개봉 연도 미상"}</p>
          <h2>{item.movie.displayTitle}</h2>
          <p>확인 기한 {formatC1Date(item.expiresAt)}</p>
        </div>
      </section>
      {mutation.isError && <Notice kind="error">{c1ErrorMessage(mutation.error)} 같은 선택으로 다시 시도할 수 있어요.</Notice>}
      <div className={styles.confirmActions} aria-live="polite">
        <button type="button" className={styles.primaryButton} disabled={mutation.isPending} onClick={() => respond(true)}>봤어요</button>
        <button type="button" className={styles.secondaryButton} disabled={mutation.isPending} onClick={() => respond(false)}>안 봤어요</button>
        <Link className={styles.textButton} to="/me/watch-confirmations">나중에</Link>
      </div>
      {mutation.isPending && <p className={styles.saving} role="status">감상 여부 저장 중</p>}
    </C1PageShell>
  );
}

type RatingEditorState = {
  movie: MovieSummary;
  providerName: string;
  watchedConfirmedAt: string;
  rating?: Rating;
  frameId?: string;
};

function editorStateFromRating(item: RatingItem): RatingEditorState {
  return { movie: item.movie, providerName: "감상 확인 OTT", watchedConfirmedAt: item.watchedConfirmedAt, rating: item.rating, frameId: item.frameId };
}

function editorStateFromUnrated(item: UnratedViewingRecord): RatingEditorState {
  return { movie: item.movie, providerName: item.provider.name, watchedConfirmedAt: item.watchedConfirmedAt };
}

function DeleteRatingDialog({ open, pending, onCancel, onConfirm }: { open: boolean; pending: boolean; onCancel: () => void; onConfirm: () => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (open) cancelRef.current?.focus(); }, [open]);
  if (!open) return null;

  function keyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !pending) onCancel();
    if (event.key !== "Tab") return;
    const first = cancelRef.current;
    const last = confirmRef.current;
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  return (
    <div className={styles.dialogBackdrop} role="presentation">
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="delete-rating-title" onKeyDown={keyDown}>
        <h2 id="delete-rating-title">평가를 삭제할까요?</h2>
        <p>평가는 삭제되지만 감상 기록은 ‘평가 안 남긴 영화’에 유지됩니다. 필름과 팝콘에서는 제거돼요.</p>
        <div>
          <button ref={cancelRef} className={styles.secondaryButton} type="button" disabled={pending} onClick={onCancel}>취소</button>
          <button ref={confirmRef} className={styles.dangerButton} type="button" disabled={pending} onClick={onConfirm}>삭제</button>
        </div>
      </div>
    </div>
  );
}

export function RatingEditorPage() {
  const { movieId = "" } = useParams();
  const api = useC1Api();
  const location = useLocation();
  const navigate = useNavigate();
  const invalidate = useInvalidateC1();
  const locationState = (location.state as { editor?: RatingEditorState } | null)?.editor;
  const lookupQuery = useQuery({
    queryKey: ["c1", "rating-editor", movieId],
    queryFn: ({ signal }) => api.findMyRatingEditor(movieId, signal),
    enabled: !locationState,
    retry: shouldRetryC1,
  });
  const editor = locationState ?? (
    lookupQuery.data?.kind === "RATED"
      ? editorStateFromRating(lookupQuery.data.item)
      : lookupQuery.data?.kind === "UNRATED"
        ? editorStateFromUnrated(lookupQuery.data.item)
        : undefined
  );
  const [value, setValue] = useState<number | null>(locationState?.rating?.value ?? null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  useEffect(() => {
    if (editor?.rating) setValue(editor.rating.value);
  }, [editor?.rating]);

  const saveMutation = useMutation({
    mutationFn: (input: PutRatingInput) => api.putMyRating(input),
    onSuccess: async (result) => {
      await invalidate();
      navigate(`/me/rating-complete/${movieId}`, { replace: true, state: { result, title: editor?.movie.displayTitle } });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (input: DeleteRatingInput) => api.deleteMyRating(input),
    onSuccess: async () => {
      await invalidate();
      navigate("/me/ratings?tab=unrated", { replace: true, state: { notice: "평가를 삭제했어요. 감상 기록은 유지됩니다." } });
    },
  });

  function save() {
    if (!value) return;
    const expectedRevision = editor?.rating?.revision;
    const retry = saveMutation.isError && saveMutation.variables?.value === value && saveMutation.variables.expectedRevision === expectedRevision;
    saveMutation.mutate({
      movieId,
      value,
      expectedRevision,
      idempotencyKey: retry ? saveMutation.variables.idempotencyKey : createIdempotencyKey("rating"),
    });
  }

  function remove() {
    if (!editor?.rating) return;
    const retry = deleteMutation.isError && deleteMutation.variables?.expectedRevision === editor.rating.revision;
    deleteMutation.mutate({
      movieId,
      expectedRevision: editor.rating.revision,
      idempotencyKey: retry ? deleteMutation.variables.idempotencyKey : createIdempotencyKey("rating-delete"),
    });
  }

  const directLoading = !locationState && lookupQuery.isPending;
  const directError = !locationState && lookupQuery.error;
  if (directLoading) return <C1PageShell eyebrow="RATE" title="내 별점"><C1Loader label="평가 정보 불러오는 중" /></C1PageShell>;
  if (directError) return <C1PageShell eyebrow="RATE" title="내 별점"><C1ErrorState error={directError} onRetry={() => void lookupQuery.refetch()} /></C1PageShell>;
  if (!editor) return <C1PageShell eyebrow="RATE" title="내 별점"><C1ErrorState error={new C1ApiError(404)} /></C1PageShell>;

  const error = saveMutation.error ?? deleteMutation.error;
  const conflict = error instanceof C1ApiError && error.code === "REVISION_CONFLICT";
  return (
    <C1PageShell eyebrow="RATE" title={editor.rating ? "평가 수정" : "이 영화는 어땠나요?"} description="별점은 정수 1~5로 기록해요.">
      <section className={styles.ratingEditor}>
        <div className={styles.editorPoster}><Poster src={editor.movie.posterUrl} title={editor.movie.displayTitle} priority /></div>
        <div className={styles.editorBody}>
          <p>{editor.movie.releaseYear ?? "개봉 연도 미상"} · {editor.providerName}</p>
          <h2>{editor.movie.displayTitle}</h2>
          <p>감상 확인 {formatC1Date(editor.watchedConfirmedAt)}</p>
          <RatingPicker value={value} onChange={setValue} disabled={saveMutation.isPending || deleteMutation.isPending} />
          <p className={styles.ratingLabel} aria-live="polite">{value ? `내 별점 ${value}/5` : "별점을 선택해 주세요"}</p>
        </div>
      </section>
      {error && <Notice kind="error">{c1ErrorMessage(error)}</Notice>}
      {conflict && <button className={styles.secondaryButton} type="button" onClick={() => window.location.reload()}>최신 값 다시 불러오기</button>}
      <div className={styles.editorActions}>
        <button className={styles.primaryButton} type="button" disabled={!value || saveMutation.isPending || deleteMutation.isPending} onClick={save}>{saveMutation.isPending ? "저장 중" : "저장"}</button>
        {!editor.rating && <Link className={styles.textButton} to="/me/ratings?tab=unrated">나중에 평가하기</Link>}
        {editor.rating && <button className={styles.textDangerButton} type="button" disabled={saveMutation.isPending || deleteMutation.isPending} onClick={() => setDeleteOpen(true)}>평가 삭제</button>}
      </div>
      <DeleteRatingDialog open={deleteOpen} pending={deleteMutation.isPending} onCancel={() => setDeleteOpen(false)} onConfirm={remove} />
    </C1PageShell>
  );
}

export function RatingCompletePage() {
  const location = useLocation();
  const state = location.state as { result?: RatingMutationResult; title?: string } | null;
  if (!state?.result) return <Navigate to="/me/film" replace />;
  return (
    <C1PageShell eyebrow="FILM ADDED" title="필름에 추가됐어요" description={`${state.title ?? "영화"}의 평가가 안전하게 반영됐어요.`}>
      <section className={styles.completeCard} aria-live="polite">
        <span aria-hidden="true">✓</span>
        <h2>내 별점 {state.result.rating.value}/5</h2>
        <p>필름 {state.result.derivedState.filmTotalCount}편 · 추천 반영 대기 중</p>
        <div>
          <Link className={styles.primaryButton} to="/me/film">필름 보기</Link>
          <Link className={styles.secondaryButton} to="/search">다음 영화 찾기</Link>
        </div>
      </section>
    </C1PageShell>
  );
}

export function RatingsPage() {
  const api = useC1Api();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "unrated" ? "unrated" : "rated";
  const ratings = useInfiniteQuery({
    queryKey: qk.ratings,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.listMyRatings(pageParam, signal),
    getNextPageParam: (page) => page.hasNext ? page.nextCursor ?? undefined : undefined,
    enabled: tab === "rated",
    retry: shouldRetryC1,
  });
  const unrated = useInfiniteQuery({
    queryKey: qk.unrated,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.listUnratedViewingRecords(pageParam, signal),
    getNextPageParam: (page) => page.hasNext ? page.nextCursor ?? undefined : undefined,
    enabled: tab === "unrated",
    retry: shouldRetryC1,
  });
  const current = tab === "rated" ? ratings : unrated;
  const ratedItems = ratings.data?.pages.flatMap((page) => page.items) ?? [];
  const unratedItems = unrated.data?.pages.flatMap((page) => page.items) ?? [];
  const notice = (location.state as { notice?: string } | null)?.notice;

  return (
    <C1PageShell eyebrow="MY RATINGS" title="평가 기록" description="평가한 영화와 감상 후 아직 평가하지 않은 영화를 나눠 봐요.">
      {notice && <Notice kind="success">{notice}</Notice>}
      <div className={styles.tabs} role="tablist" aria-label="평가 기록 구분">
        <button role="tab" aria-selected={tab === "rated"} type="button" onClick={() => setParams({ tab: "rated" })}>평가</button>
        <button role="tab" aria-selected={tab === "unrated"} type="button" onClick={() => setParams({ tab: "unrated" })}>평가 안 남긴 영화</button>
      </div>
      {current.isPending && <C1Loader label="평가 기록 불러오는 중" />}
      {current.isError && <C1ErrorState error={current.error} onRetry={() => current.refetch()} />}
      {tab === "rated" && ratings.isSuccess && ratedItems.length === 0 && <EmptyState title="아직 남긴 평가가 없어요" description="감상한 영화에 별점을 남겨보세요." />}
      {tab === "unrated" && unrated.isSuccess && unratedItems.length === 0 && <EmptyState title="평가를 기다리는 영화가 없어요" description="감상 확인을 마치면 여기에 표시돼요." />}
      <div className={styles.recordList} role="tabpanel">
        {tab === "rated" && ratedItems.map((item) => (
          <MovieRecordCard key={item.rating.ratingId} movie={item.movie} metadata={`수정 ${formatC1Date(item.rating.updatedAt)}`} rating={item.rating.value} action={<Link className={styles.secondaryButton} to={`/me/movies/${item.movie.movieId}/rating`} state={{ editor: editorStateFromRating(item) }}>수정</Link>} />
        ))}
        {tab === "unrated" && unratedItems.map((item) => (
          <MovieRecordCard key={item.viewingRecordId} movie={item.movie} metadata={`${item.provider.name} · 감상 ${formatC1Date(item.watchedConfirmedAt)}`} action={<Link className={styles.primaryButton} to={`/me/movies/${item.movie.movieId}/rating`} state={{ editor: editorStateFromUnrated(item) }}>평가하기</Link>} />
        ))}
      </div>
      <LoadMore hasNextPage={Boolean(current.hasNextPage)} loading={current.isFetchingNextPage} onClick={() => void current.fetchNextPage()} />
    </C1PageShell>
  );
}

export function FilmPage() {
  const api = useC1Api();
  const query = useInfiniteQuery({
    queryKey: qk.film,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.getMyFilm(pageParam, signal),
    getNextPageParam: (page) => page.hasNext ? page.nextCursor ?? undefined : undefined,
    retry: shouldRetryC1,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const total = query.data?.pages[0]?.totalCount ?? 0;

  return (
    <C1PageShell eyebrow="MY FILM" title="내 취향 필름" description={`평가를 완료한 영화 ${total}편을 모았어요.`}>
      {query.isPending && <C1Loader label="필름 불러오는 중" />}
      {query.isError && <C1ErrorState error={query.error} onRetry={() => query.refetch()} />}
      {query.isSuccess && items.length === 0 && <EmptyState title="아직 필름에 추가된 영화가 없어요" description="영화를 보고 평가하면 필름이 만들어져요." action={<Link className={styles.primaryButton} to="/search">영화 찾기</Link>} />}
      {items.length > 0 && (
        <div className={styles.filmGrid}>
          {items.map((frame) => (
            <Link className={styles.frameCard} key={frame.frameId} to={`/me/film/frames/${frame.frameId}`}>
              <Poster src={frame.movie.posterUrl} title={frame.movie.displayTitle} />
              <h2>{frame.movie.displayTitle}</h2>
              <p aria-label={`내 별점 ${frame.myRating} / 5`}>★ {frame.myRating}/5</p>
              <small>{formatC1Date(frame.watchedConfirmedAt)}</small>
            </Link>
          ))}
        </div>
      )}
      <LoadMore hasNextPage={Boolean(query.hasNextPage)} loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()} />
    </C1PageShell>
  );
}

export function FrameDetailPage() {
  const { frameId = "" } = useParams();
  const api = useC1Api();
  const query = useQuery({
    queryKey: ["c1", "frame", frameId],
    queryFn: ({ signal }) => api.getMyFrame(frameId, signal),
    retry: shouldRetryC1,
  });

  if (query.isPending) return <C1PageShell eyebrow="FRAME" title="필름 한 장"><C1Loader label="프레임 불러오는 중" /></C1PageShell>;
  if (query.isError) return <C1PageShell eyebrow="FRAME" title="필름 한 장"><C1ErrorState error={query.error} onRetry={() => query.refetch()} /></C1PageShell>;
  const frame = query.data;
  const editor: RatingEditorState = {
    movie: frame.movie,
    providerName: frame.provider.name,
    watchedConfirmedAt: frame.watchedConfirmedAt,
    rating: frame.rating,
    frameId: frame.frameId,
  };
  return (
    <C1PageShell eyebrow="FRAME" title={frame.movie.displayTitle} description="내 감상 사실과 별점만 표시해요.">
      <section className={styles.frameDetail}>
        <div className={styles.framePoster}><Poster src={frame.movie.posterUrl} title={frame.movie.displayTitle} priority /></div>
        <dl>
          <div><dt>내 별점</dt><dd>{frame.rating.value}/5</dd></div>
          <div><dt>감상 확인</dt><dd>{formatC1Date(frame.watchedConfirmedAt)}</dd></div>
          <div><dt>확인 OTT</dt><dd>{frame.provider.name}</dd></div>
          <div><dt>최종 수정</dt><dd>{formatC1Date(frame.rating.updatedAt)}</dd></div>
        </dl>
      </section>
      <div className={styles.editorActions}>
        <Link className={styles.primaryButton} to={`/me/movies/${frame.movie.movieId}/rating`} state={{ editor }}>평가 수정·삭제</Link>
        <Link className={styles.secondaryButton} to={`/movies/${frame.movie.movieId}`}>영화 상세</Link>
      </div>
    </C1PageShell>
  );
}

const dimensionLabels = { GENRE: "장르", COUNTRY: "국가", DIRECTOR: "감독" } as const;

export function PopcornBucketPage() {
  const api = useC1Api();
  const bucket = useQuery({ queryKey: qk.bucket, queryFn: ({ signal }) => api.getMyPopcornBucket(signal), retry: shouldRetryC1 });
  const taste = useQuery({ queryKey: qk.taste, queryFn: ({ signal }) => api.getMyTasteProfile(signal), retry: shouldRetryC1 });
  const groupedTaste = useMemo(() => {
    const groups = new Map<string, TasteProfile["items"]>();
    for (const item of taste.data?.items ?? []) groups.set(item.dimensionType, [...(groups.get(item.dimensionType) ?? []), item]);
    return groups;
  }, [taste.data]);

  return (
    <C1PageShell eyebrow="POPCORN BUCKET" title="한눈에 보는 내 취향" description="본 영화 수와 별점 평균은 서로 다른 지표로 보여드려요.">
      {bucket.isPending && <C1Loader label="팝콘 버킷 불러오는 중" />}
      {bucket.isError && <C1ErrorState error={bucket.error} onRetry={() => bucket.refetch()} />}
      {bucket.data && (
        <>
          <div className={styles.bucketSummary}><strong>{bucket.data.totalCount}</strong><span>알의 취향 · mapping {bucket.data.mappingVersion}</span></div>
          <div className={styles.flavorGrid} aria-label="팝콘 맛별 기록">
            {bucket.data.flavors.map((flavor) => (
              <article className={styles.flavorCard} key={flavor.code} style={{ borderTopColor: flavor.colorToken }}>
                <span>{flavor.code}</span>
                <h2>{flavor.displayName}</h2>
                <p><strong>{flavor.count}</strong>편</p>
                <p>내 평균 {flavor.averageRating === null ? "평가 없음" : `${flavor.averageRating.toFixed(1)}/5`}</p>
              </article>
            ))}
          </div>
          {bucket.data.totalCount === 0 && <Notice>아직 팝콘이 없어요. 평가를 완료하면 맛별로 쌓여요.</Notice>}
        </>
      )}
      <section className={styles.tasteSection} aria-labelledby="raw-taste-title">
        <p className={styles.eyebrow}>RAW TASTE</p>
        <h2 id="raw-taste-title">취향 원천 집계</h2>
        <p>해석 점수 없이 장르·국가·감독별 평가 수와 평균만 제공합니다.</p>
        {taste.isPending && <C1Loader label="취향 집계 불러오는 중" />}
        {taste.isError && <C1ErrorState compact error={taste.error} onRetry={() => taste.refetch()} />}
        {taste.data && taste.data.items.length === 0 && <EmptyState title="집계할 평가가 없어요" description="첫 평가를 남기면 원천 집계가 생겨요." />}
        {taste.data && taste.data.items.length > 0 && (
          <div className={styles.tasteGroups}>
            {Array.from(groupedTaste.entries()).map(([dimension, items]) => (
              <section key={dimension}>
                <h3>{dimensionLabels[dimension as keyof typeof dimensionLabels]}</h3>
                <ul>{items.map((item) => <li key={item.dimensionKey}><span>{item.displayName}</span><span>{item.ratingCount}편 · 평균 {item.averageRating === null ? "없음" : `${item.averageRating.toFixed(1)}/5`}</span></li>)}</ul>
              </section>
            ))}
          </div>
        )}
      </section>
    </C1PageShell>
  );
}
