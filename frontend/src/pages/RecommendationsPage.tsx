import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  C2BApiError,
  c2bErrorMessage,
  createC2BId,
  type AppendRecommendationsInput,
  type DismissRecommendationInput,
  type RecommendationAppend,
  type RecommendationDelivery,
  type RecommendationItem,
} from "../api/c2b";
import { useC2BApi } from "../api/C2BApiContext";
import { AppHeader, ErrorPanel, InlineLoader, Poster } from "../components/CatalogUi";
import styles from "../styles/recommendations.module.css";

const recommendationsKey = ["c2b", "personal-discovery"] as const;

function mergeItems(existing: RecommendationItem[], appended: RecommendationItem[]): RecommendationItem[] {
  const byId = new Map(existing.map((item) => [item.deliveryItemId, item]));
  for (const item of appended.slice(0, 3)) byId.set(item.deliveryItemId, item);
  return [...byId.values()].sort((left, right) => left.position - right.position);
}

function mergeAppend(current: RecommendationDelivery, result: RecommendationAppend): RecommendationDelivery {
  return {
    ...current,
    deliveryRevision: result.deliveryRevision,
    items: mergeItems(current.items, result.appendedItems),
    pageInfo: result.pageInfo,
  };
}

function RecommendationCard({
  item,
  disabled,
  onDismiss,
}: {
  item: RecommendationItem;
  disabled: boolean;
  onDismiss: () => void;
}) {
  return (
    <article className={styles.card}>
      <Link className={styles.movieLink} to={`/movies/${item.movie.movieId}`} aria-label={`${item.movie.title} 상세 보기`}>
        <div className={styles.poster}><Poster src={item.movie.posterUrl} title={item.movie.title} /></div>
        <div className={styles.cardBody}>
          <p className={styles.cardEyebrow}>인기 기준 추천</p>
          <h2>{item.movie.title}</h2>
          <p className={styles.meta}>
            {[item.movie.releaseYear, item.movie.genres.slice(0, 2).join(" · ")].filter(Boolean).join(" · ") || "영화 정보 확인 중"}
          </p>
        </div>
      </Link>
      <button className={styles.dismissButton} type="button" disabled={disabled} onClick={onDismiss}>
        관심 없음
      </button>
    </article>
  );
}

export function RecommendationsPage() {
  const api = useC2BApi();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: recommendationsKey,
    queryFn: ({ signal }) => api.getRecommendations(signal),
    retry: (count, error) => error instanceof C2BApiError && error.status === 503 && count < 1,
  });

  const appendMutation = useMutation({
    mutationFn: (input: AppendRecommendationsInput) => api.appendRecommendations(input),
    onSuccess: async (result) => {
      queryClient.setQueryData<RecommendationDelivery>(recommendationsKey, (current) => current ? mergeAppend(current, result) : current);
      await queryClient.refetchQueries({ queryKey: recommendationsKey, exact: true });
    },
  });

  const dismissMutation = useMutation({
    mutationFn: (input: DismissRecommendationInput) => api.dismissRecommendation(input),
    onSuccess: async (result) => {
      queryClient.setQueryData<RecommendationDelivery>(recommendationsKey, (current) => current ? {
        ...current,
        deliveryRevision: result.deliveryRevision,
        items: current.items.filter((item) => item.deliveryItemId !== result.deliveryItemId),
        pageInfo: {
          ...current.pageInfo,
          activeItemCount: Math.max(0, current.pageInfo.activeItemCount - 1),
        },
      } : current);
      await queryClient.refetchQueries({ queryKey: recommendationsKey, exact: true });
    },
  });

  const delivery = query.data;
  const mutationBusy = appendMutation.isPending || dismissMutation.isPending;

  function append() {
    if (!delivery?.pageInfo.hasMore || !delivery.pageInfo.nextCursor || mutationBusy) return;
    const previous = appendMutation.variables;
    const retry = appendMutation.isError
      && previous?.deliveryId === delivery.deliveryId
      && previous.expectedRevision === delivery.deliveryRevision
      && previous.cursor === delivery.pageInfo.nextCursor;
    appendMutation.mutate(retry ? previous : {
      deliveryId: delivery.deliveryId,
      expectedRevision: delivery.deliveryRevision,
      cursor: delivery.pageInfo.nextCursor,
      appendEventId: createC2BId(),
      idempotencyKey: createC2BId(),
    });
  }

  function dismiss(item: RecommendationItem) {
    if (!delivery || mutationBusy) return;
    const previous = dismissMutation.variables;
    const retry = dismissMutation.isError
      && previous?.deliveryItemId === item.deliveryItemId
      && previous.expectedRevision === delivery.deliveryRevision;
    dismissMutation.mutate(retry ? previous : {
      deliveryItemId: item.deliveryItemId,
      expectedRevision: delivery.deliveryRevision,
      dismissalEventId: createC2BId(),
      idempotencyKey: createC2BId(),
    });
  }

  return (
    <main className={styles.page}>
      <div className={styles.inner}>
        <AppHeader />
        <header className={styles.hero}>
          <p className={styles.eyebrow}>PERSONAL DISCOVERY</p>
          <h1>오늘의 영화 추천</h1>
          <p>지금은 검증된 인기 기준으로 실제 영화만 보여드려요.</p>
        </header>

        {query.isPending && <InlineLoader label="추천 영화 불러오는 중" />}
        {query.isError && !delivery && (
          <ErrorPanel
            title="추천을 불러오지 못했어요"
            message={c2bErrorMessage(query.error)}
            onRetry={() => void query.refetch()}
          />
        )}

        {delivery && delivery.items.length === 0 && (
          <section className={styles.emptyState}>
            <h2>지금 보여드릴 추천이 없어요</h2>
            <p>평가 기록과 영화 목록이 갱신되면 다시 확인해 주세요.</p>
            <Link className={styles.secondaryLink} to="/search">영화 찾아보기</Link>
          </section>
        )}

        {delivery && delivery.items.length > 0 && (
          <section aria-labelledby="recommendation-list-title">
            <div className={styles.sectionHeading}>
              <div>
                <p className={styles.eyebrow}>POPULARITY BASELINE</p>
                <h2 id="recommendation-list-title">인기 기준 추천</h2>
              </div>
              <span>{delivery.items.length}편</span>
            </div>
            <div className={styles.grid} aria-live="polite">
              {delivery.items.map((item) => (
                <RecommendationCard
                  key={item.deliveryItemId}
                  item={item}
                  disabled={mutationBusy}
                  onDismiss={() => dismiss(item)}
                />
              ))}
            </div>
          </section>
        )}

        {(appendMutation.isError || dismissMutation.isError) && (
          <p className={styles.mutationError} role="alert">
            {c2bErrorMessage(appendMutation.error ?? dismissMutation.error)} 기존 추천은 그대로 유지했어요.
          </p>
        )}

        {delivery?.pageInfo.hasMore && delivery.pageInfo.nextCursor && (
          <div className={styles.moreArea}>
            <button className={styles.moreButton} type="button" disabled={mutationBusy} onClick={append}>
              {appendMutation.isPending ? "추천 추가 중" : "추천 더 보기"}
            </button>
            <p>한 번에 최대 3편을 기존 목록 뒤에 추가해요.</p>
          </div>
        )}

        {delivery && !delivery.pageInfo.hasMore && delivery.items.length > 0 && (
          <p className={styles.exhausted} role="status">준비된 추천을 모두 확인했어요.</p>
        )}
      </div>
    </main>
  );
}
