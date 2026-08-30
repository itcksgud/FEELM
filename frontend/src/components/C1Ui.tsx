import type { KeyboardEvent, ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import type { C1ApiError } from "../api/c1";
import { c1ErrorMessage } from "../api/c1";
import { AppHeader, InlineLoader, Poster } from "./CatalogUi";
import styles from "../styles/c1.module.css";

export function C1PageShell({ eyebrow, title, description, children }: {
  eyebrow: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <main className={styles.page}>
      <div className={styles.pageInner}>
        <AppHeader compact />
        <nav className={styles.myNav} aria-label="내 기록 메뉴">
          <NavLink to="/me/watch-confirmations">감상 확인</NavLink>
          <NavLink to="/me/ratings">평가</NavLink>
          <NavLink to="/me/film">필름</NavLink>
          <NavLink to="/me/popcorn-bucket">팝콘</NavLink>
        </nav>
        <header className={styles.pageHeader}>
          <p className={styles.eyebrow}>{eyebrow}</p>
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </header>
        {children}
      </div>
    </main>
  );
}

export function C1Loader({ label }: { label: string }) {
  return <div className={styles.centerState}><InlineLoader label={label} /></div>;
}

export function C1ErrorState({ error, onRetry, compact = false }: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const status = (error as C1ApiError | undefined)?.status;
  return (
    <section className={compact ? styles.inlineErrorState : styles.errorState} role="alert">
      <h2>{status === 401 ? "로그인이 필요해요" : status === 404 ? "항목을 찾을 수 없어요" : "불러오지 못했어요"}</h2>
      <p>{c1ErrorMessage(error)}</p>
      {onRetry && status !== 401 && status !== 404 && (
        <button type="button" className={styles.secondaryButton} onClick={onRetry}>다시 시도</button>
      )}
      {status === 404 && <Link className={styles.secondaryButton} to="/me/ratings">평가 목록으로</Link>}
    </section>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <section className={styles.emptyState} role="status">
      <span aria-hidden="true">○</span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </section>
  );
}

export function Notice({ children, kind = "info" }: { children: ReactNode; kind?: "info" | "success" | "error" }) {
  return <p className={`${styles.notice} ${styles[kind]}`} role={kind === "error" ? "alert" : "status"} aria-live="polite">{children}</p>;
}

export function MovieRecordCard({ movie, metadata, action, rating }: {
  movie: { movieId: string; displayTitle: string; posterUrl: string | null; releaseYear: number | null };
  metadata: string;
  action: ReactNode;
  rating?: number;
}) {
  return (
    <article className={styles.recordCard}>
      <div className={styles.recordPoster}><Poster src={movie.posterUrl} title={movie.displayTitle} /></div>
      <div className={styles.recordBody}>
        <p>{movie.releaseYear ?? "개봉 연도 미상"}</p>
        <h2>{movie.displayTitle}</h2>
        <p>{metadata}</p>
        {rating !== undefined && <strong aria-label={`내 별점 ${rating} / 5`}>★ {rating}/5</strong>}
      </div>
      <div className={styles.recordAction}>{action}</div>
    </article>
  );
}

export function RatingPicker({ value, onChange, disabled = false }: {
  value: number | null;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  function handleKey(event: KeyboardEvent<HTMLDivElement>) {
    if (disabled || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 : -1;
    const focusedScore = Number((document.activeElement as HTMLElement | null)?.dataset.score);
    const current = Number.isInteger(focusedScore) && focusedScore >= 1 && focusedScore <= 5
      ? focusedScore
      : (value ?? (direction > 0 ? 0 : 6));
    const next = Math.min(5, Math.max(1, current + direction));
    onChange(next);
    document.getElementById(`rating-${next}`)?.focus();
  }

  return (
    <div className={styles.ratingPicker} role="radiogroup" aria-label="내 별점, 5점 만점" onKeyDown={handleKey}>
      {[1, 2, 3, 4, 5].map((score) => (
        <button
          id={`rating-${score}`}
          key={score}
          type="button"
          role="radio"
          aria-checked={value === score}
          data-score={score}
          data-filled={value !== null && score <= value}
          aria-label={`${score}점`}
          tabIndex={value === score || (value === null && score === 1) ? 0 : -1}
          disabled={disabled}
          onClick={() => onChange(score)}
        >
          <span aria-hidden="true">★</span>
        </button>
      ))}
    </div>
  );
}

export function formatC1Date(instant: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(new Date(instant));
}
