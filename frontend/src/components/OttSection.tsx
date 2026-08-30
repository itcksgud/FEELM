import { useMutation, useQuery } from "@tanstack/react-query";
import type { MonetizationType, OttAvailability } from "../api/catalog";
import { useCatalogApi } from "../api/CatalogApiContext";
import { createIdempotencyKey, c1ErrorMessage, type CreateWatchIntentInput } from "../api/c1";
import { useC1Api } from "../api/C1ApiContext";
import { InlineLoader } from "./CatalogUi";
import styles from "../styles/catalog.module.css";

const GROUP_LABELS: Record<MonetizationType, string> = {
  FLATRATE: "구독으로 보기",
  RENT: "대여",
  BUY: "구매",
  FREE: "무료",
  ADS: "광고형 무료",
};

function safeExternalUrl(raw: string) {
  try {
    const url = new URL(raw);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

function formatSnapshot(instant: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(new Date(instant));
}

export function OttSection({ movieId }: { movieId: string }) {
  const api = useCatalogApi();
  const query = useQuery({
    queryKey: ["movie", movieId, "ott"],
    queryFn: ({ signal }) => api.getMovieOttOffers(movieId, signal),
  });

  return (
    <section className={styles.detailSection} aria-labelledby="ott-title">
      <div className={styles.sectionHeading}>
        <div>
          <p className={styles.eyebrow}>WATCH IN KOREA</p>
          <h2 id="ott-title">시청 가능한 OTT</h2>
        </div>
      </div>
      {query.isPending && <InlineLoader label="OTT 정보 불러오는 중" />}
      {query.isError && (
        <div className={styles.inlineState} role="alert">
          <p>OTT 정보를 확인할 수 없어요.</p>
          <button className={styles.secondaryButton} type="button" onClick={() => query.refetch()}>다시 시도</button>
        </div>
      )}
      {query.data && <OttAvailabilityContent availability={query.data} onRetry={() => query.refetch()} />}
    </section>
  );
}

export function OttAvailabilityContent({
  availability,
  onRetry,
  navigateExternal = (url) => window.location.assign(url),
}: {
  availability: OttAvailability;
  onRetry?: () => void;
  navigateExternal?: (url: string) => void;
}) {
  if (availability.availabilityStatus === "UNKNOWN") {
    return (
      <div className={styles.inlineState} role="status">
        <p>OTT 정보를 확인할 수 없어요.</p>
        {onRetry && <button className={styles.secondaryButton} type="button" onClick={onRetry}>다시 시도</button>}
      </div>
    );
  }

  if (availability.availabilityStatus === "NONE_LISTED") {
    return <div className={styles.inlineState} role="status"><p>현재 등록된 한국 시청 옵션이 없어요.</p></div>;
  }

  return <ListedOttAvailabilityContent availability={availability} navigateExternal={navigateExternal} />;
}

function ListedOttAvailabilityContent({ availability, navigateExternal }: {
  availability: OttAvailability;
  navigateExternal: (url: string) => void;
}) {
  const c1Api = useC1Api();
  const clickMutation = useMutation({ mutationFn: (input: CreateWatchIntentInput) => c1Api.createWatchIntent(input) });

  function openAfterRecorded(offerId: string) {
    const retry = clickMutation.isError && clickMutation.variables?.offerId === offerId;
    clickMutation.mutate({
      movieId: availability.movieId,
      offerId,
      idempotencyKey: retry ? clickMutation.variables.idempotencyKey : createIdempotencyKey("ott-click"),
    }, {
      onSuccess: (result) => {
        const destination = safeExternalUrl(result.destination.url);
        if (!destination) return;
        navigateExternal(destination);
      },
    });
  }

  const groups = [...availability.groups].sort((a, b) => {
    const order: MonetizationType[] = ["FLATRATE", "RENT", "BUY", "FREE", "ADS"];
    return order.indexOf(a.monetizationType) - order.indexOf(b.monetizationType);
  });

  return (
    <div className={styles.ottContent}>
      {availability.freshness === "STALE" && availability.snapshotAt && (
        <p className={styles.staleNotice}>정보 기준 {formatSnapshot(availability.snapshotAt)}</p>
      )}
      {groups.map((group) => {
        const offers = [...group.offers].sort((a, b) => Number(b.isSubscribed === true) - Number(a.isSubscribed === true));
        const content = (
          <div className={styles.offerList}>
            {offers.map((offer) => {
              const externalUrl = offer.link ? safeExternalUrl(offer.link.url) : undefined;
              const buttonText = offer.link?.type === "DIRECT" ? `${offer.providerName}에서 보기` : "시청 옵션 확인";
              const isCurrent = clickMutation.variables?.offerId === offer.offerId;
              return (
                <article className={styles.offerCard} key={offer.offerId}>
                  {offer.logoUrl ? <img src={offer.logoUrl} alt="" /> : <span className={styles.providerLogoFallback}>{offer.providerName.slice(0, 1)}</span>}
                  <div>
                    <h3>{offer.providerName}</h3>
                    <p>{GROUP_LABELS[offer.monetizationType]}{offer.isSubscribed === true ? " · 구독 중" : ""}</p>
                  </div>
                  {externalUrl ? (
                    <button
                      className={styles.offerLink}
                      type="button"
                      disabled={clickMutation.isPending}
                      onClick={() => openAfterRecorded(offer.offerId)}
                      aria-label={`${buttonText}, 외부 페이지로 이동`}
                    >
                      {clickMutation.isPending && isCurrent ? "기록 중" : buttonText}<span aria-hidden="true"> ↗</span>
                    </button>
                  ) : <span className={styles.offerUnavailable}>링크 준비 중</span>}
                  {clickMutation.isError && isCurrent && (
                    <p className={styles.inlineError} role="alert">{c1ErrorMessage(clickMutation.error)} 외부 페이지로 이동하지 않았어요.</p>
                  )}
                </article>
              );
            })}
          </div>
        );

        if (group.monetizationType === "FLATRATE") {
          return <section className={styles.offerGroup} key={group.monetizationType}><h3>{GROUP_LABELS[group.monetizationType]}</h3>{content}</section>;
        }
        return <details className={styles.offerGroup} key={group.monetizationType}><summary>{GROUP_LABELS[group.monetizationType]} <span>{offers.length}</span></summary>{content}</details>;
      })}
    </div>
  );
}
