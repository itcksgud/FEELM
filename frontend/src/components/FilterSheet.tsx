import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useCatalogApi } from "../api/CatalogApiContext";
import type { MonetizationType } from "../api/catalog";
import { emptyFilters, type SearchState } from "../search/searchState";
import { InlineLoader } from "./CatalogUi";
import styles from "../styles/catalog.module.css";

const TYPE_LABELS: Record<MonetizationType, string> = {
  FLATRATE: "구독",
  RENT: "대여",
  BUY: "구매",
  FREE: "무료",
  ADS: "광고형",
};

function toggle(values: string[], value: string) {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

export function FilterSheet({
  open,
  value,
  onClose,
  onApply,
}: {
  open: boolean;
  value: SearchState;
  onClose: () => void;
  onApply: (value: SearchState) => void;
}) {
  const api = useCatalogApi();
  const [draft, setDraft] = useState(value);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) setDraft(value);
  }, [open, value]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", closeOnEscape);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = previous;
      previouslyFocused?.focus();
    };
  }, [onClose, open]);

  const genres = useQuery({ queryKey: ["facets", "genres"], queryFn: ({ signal }) => api.listGenres(signal), enabled: open });
  const countries = useQuery({ queryKey: ["facets", "countries"], queryFn: ({ signal }) => api.listCountries(signal), enabled: open });
  const providers = useQuery({ queryKey: ["facets", "providers"], queryFn: ({ signal }) => api.listOttProviders(signal), enabled: open });
  const invalidYears = Boolean(draft.releaseYearFrom && draft.releaseYearTo && draft.releaseYearFrom > draft.releaseYearTo);
  const effectiveTypes = useMemo(
    () => draft.ottMonetizationTypes.length ? draft.ottMonetizationTypes : ["FLATRATE" as MonetizationType],
    [draft.ottMonetizationTypes],
  );

  if (!open) return null;

  const facetLoading = genres.isPending || countries.isPending || providers.isPending;
  const facetError = genres.isError || countries.isError || providers.isError;

  return (
    <div className={styles.sheetBackdrop} onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className={styles.filterSheet} role="dialog" aria-modal="true" aria-labelledby="filter-title">
        <header className={styles.sheetHeader}>
          <button ref={closeButton} className={styles.iconButton} type="button" onClick={onClose} aria-label="필터 닫기">×</button>
          <h2 id="filter-title">검색 필터</h2>
          <button className={styles.textButton} type="button" onClick={() => setDraft(emptyFilters(value.query))}>초기화</button>
        </header>

        {facetLoading && <InlineLoader label="필터 목록 불러오는 중" />}
        {facetError && (
          <div className={styles.inlineError} role="alert">
            필터 목록을 불러오지 못했어요.
            <button type="button" onClick={() => void Promise.all([genres.refetch(), countries.refetch(), providers.refetch()])}>다시 시도</button>
          </div>
        )}

        <div className={styles.sheetContent}>
          <fieldset>
            <legend>장르</legend>
            <div className={styles.choiceGrid}>
              {genres.data?.items.map((genre) => (
                <label className={styles.choicePill} key={genre.genreId}>
                  <input type="checkbox" checked={draft.genreIds.includes(genre.genreId)} onChange={() => setDraft({ ...draft, genreIds: toggle(draft.genreIds, genre.genreId) })} />
                  <span>{genre.name}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>제작 국가</legend>
            <div className={styles.choiceGrid}>
              {countries.data?.items.map((country) => (
                <label className={styles.choicePill} key={country.code}>
                  <input type="checkbox" checked={draft.countryCodes.includes(country.code)} onChange={() => setDraft({ ...draft, countryCodes: toggle(draft.countryCodes, country.code) })} />
                  <span>{country.name}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>개봉 연도</legend>
            <div className={styles.yearFields}>
              <label>시작<input aria-label="개봉 연도 시작" type="number" min="1870" max="2100" value={draft.releaseYearFrom ?? ""} onChange={(event) => setDraft({ ...draft, releaseYearFrom: event.target.value ? Number(event.target.value) : undefined })} /></label>
              <span aria-hidden="true">—</span>
              <label>끝<input aria-label="개봉 연도 끝" type="number" min="1870" max="2100" value={draft.releaseYearTo ?? ""} onChange={(event) => setDraft({ ...draft, releaseYearTo: event.target.value ? Number(event.target.value) : undefined })} /></label>
            </div>
            {invalidYears && <p className={styles.fieldError}>시작 연도는 끝 연도보다 늦을 수 없어요.</p>}
          </fieldset>

          <fieldset>
            <legend>한국 OTT</legend>
            <div className={styles.choiceGrid}>
              {providers.data?.items.map((provider) => (
                <label className={styles.choicePill} key={provider.providerId}>
                  <input type="checkbox" checked={draft.ottProviderIds.includes(provider.providerId)} onChange={() => setDraft({ ...draft, ottProviderIds: toggle(draft.ottProviderIds, provider.providerId) })} />
                  <span>{provider.name}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>시청 유형</legend>
            <div className={styles.choiceGrid}>
              {(Object.keys(TYPE_LABELS) as MonetizationType[]).map((type) => (
                <label className={styles.choicePill} key={type}>
                  <input
                    type="checkbox"
                    checked={effectiveTypes.includes(type)}
                    onChange={() => setDraft({ ...draft, ottMonetizationTypes: toggle(effectiveTypes, type) as MonetizationType[] })}
                  />
                  <span>{TYPE_LABELS[type]}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>

        <footer className={styles.sheetFooter}>
          <button
            className={styles.primaryButton}
            type="button"
            disabled={invalidYears || facetLoading}
            onClick={() => onApply({
              ...draft,
              ottMonetizationTypes: draft.ottMonetizationTypes.length ? effectiveTypes : [],
            })}
          >
            적용하기
          </button>
        </footer>
      </section>
    </div>
  );
}
