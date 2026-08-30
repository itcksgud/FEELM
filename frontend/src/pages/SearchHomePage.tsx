import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppHeader, PopularMovies, SearchBox } from "../components/CatalogUi";
import { clearRecentQueries, readRecentQueries, rememberQuery, removeRecentQuery } from "../search/recentQueries";
import styles from "../styles/catalog.module.css";

export function SearchHomePage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [recent, setRecent] = useState(() => readRecentQueries());

  function openResults(value: string) {
    const normalized = value.trim();
    if (!normalized) return;
    setRecent(rememberQuery(normalized));
    navigate(`/search/results?q=${encodeURIComponent(normalized)}`);
  }

  useEffect(() => {
    const normalized = query.trim();
    if (!normalized) return;
    const timer = window.setTimeout(() => openResults(normalized), 250);
    return () => window.clearTimeout(timer);
    // openResults intentionally uses the latest route callback for a single debounce cycle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  return (
    <main className={styles.page}>
      <div className={styles.pageInner}>
        <AppHeader />
        <section className={styles.searchHero} aria-labelledby="search-title">
          <p className={styles.eyebrow}>FIND YOUR NEXT FILM</p>
          <h1 id="search-title">오늘은 어떤 영화를<br />만나고 싶나요?</h1>
          <SearchBox value={query} onChange={setQuery} onSubmit={openResults} autoFocus />
        </section>

        {recent.length > 0 && (
          <section className={styles.homeSection} aria-labelledby="recent-title">
            <div className={styles.sectionHeading}>
              <h2 id="recent-title">최근 검색어</h2>
              <button className={styles.textButton} type="button" onClick={() => { clearRecentQueries(); setRecent([]); }}>전체 삭제</button>
            </div>
            <ul className={styles.recentList}>
              {recent.map((item) => (
                <li key={item}>
                  <button className={styles.recentQuery} type="button" onClick={() => openResults(item)}>{item}</button>
                  <button className={styles.removeRecent} type="button" onClick={() => setRecent(removeRecentQuery(item))} aria-label={`${item} 최근 검색어 삭제`}>×</button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <PopularMovies />
      </div>
    </main>
  );
}
