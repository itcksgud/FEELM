import { useQuery } from "@tanstack/react-query";
import { useCatalogApi } from "../api/CatalogApiContext";
import { MovieCard, MovieGridSkeleton } from "./CatalogUi";
import styles from "../styles/catalog.module.css";

export function SimilarMovies({ movieId }: { movieId: string }) {
  const api = useCatalogApi();
  const query = useQuery({
    queryKey: ["movie", movieId, "similar"],
    queryFn: ({ signal }) => api.getSimilarMovies(movieId, 10, signal),
  });

  if (query.isPending) {
    return <section className={styles.detailSection} aria-labelledby="similar-title"><h2 id="similar-title">비슷한 영화</h2><MovieGridSkeleton count={4} /></section>;
  }
  if (query.isError) {
    return (
      <section className={styles.detailSection} aria-labelledby="similar-title">
        <div className={styles.sectionHeading}><h2 id="similar-title">비슷한 영화</h2><button className={styles.textButton} onClick={() => query.refetch()}>다시 시도</button></div>
      </section>
    );
  }
  if (!query.data.items.length) return null;

  return (
    <section className={styles.detailSection} aria-labelledby="similar-title">
      <div className={styles.sectionHeading}>
        <div><p className={styles.eyebrow}>MORE LIKE THIS</p><h2 id="similar-title">비슷한 영화</h2></div>
      </div>
      <div className={styles.similarScroller}>
        {query.data.items.map((item) => (
          <div className={styles.similarItem} key={item.movie.movieId}>
            <MovieCard movie={item.movie} />
            <p className={styles.similarityReasons}>{item.reasons.map((reason) => reason.label).join(" · ")}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
