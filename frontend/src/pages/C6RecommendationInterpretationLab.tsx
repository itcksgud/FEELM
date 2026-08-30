import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { C6ApiError, c6ErrorMessage, type C6Confidence, type C6Limitation } from "../api/c6";
import { useC6Api } from "../api/C6ApiContext";
import { Poster } from "../components/CatalogUi";
import styles from "../styles/c6RecommendationInterpretationLab.module.css";

const queryKey = ["c6", "recommendation-interpretation-experiment"] as const;

function confidenceLabel(confidence: C6Confidence): string {
  const labels: Record<C6Confidence, string> = {
    HIGH: "근거 충분",
    MEDIUM: "근거 보통",
    LOW: "근거 적음",
    INSUFFICIENT_DATA: "자료 부족",
  };
  return labels[confidence];
}

function formatRating(value: number | null): string {
  return value === null ? "계산 전" : `${value.toFixed(2)} / 5`;
}

function formatUtility(value: number | null): string {
  if (value === null) return "자료 부족";
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function limitationLabel(limitation: C6Limitation): string {
  if (limitation === "LOCAL_EXPERIMENT_ONLY") return "로컬 개발 환경에서 판단 근거를 수집하기 위한 실험입니다.";
  if (limitation === "NOT_SELF_REPORTED_SATISFACTION") return "사용자가 직접 응답한 만족도가 아니라 평가 이력으로 계산한 추정값입니다.";
  if (limitation === "NOT_PRODUCT_DISPLAY_APPROVED") return "일반 사용자에게 표시하도록 승인된 기능이 아닙니다.";
  return "가장 최근의 유효한 평가를 정해진 K 구간만큼 사용합니다.";
}

export function C6RecommendationInterpretationLab() {
  const api = useC6Api();
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.getRecommendationInterpretation(signal),
    retry: false,
  });
  const experiment = query.data;

  return (
    <main className={styles.page}>
      <div className={styles.inner}>
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>LOCAL EXPERIMENT · C6</p>
            <h1>추천 해석 실험</h1>
            <p className={styles.intro}>예상 별점과 개인 평가 분포의 상대 위치를 함께 살펴보는 개발 전용 화면입니다.</p>
          </div>
          <Link className={styles.backLink} to="/me/recommendations">추천 목록으로</Link>
        </header>

        <aside className={styles.boundaryNotice} aria-label="실험 해석 경계">
          <strong>제품 노출 미승인 · 감정 직접 측정 아님</strong>
          <p>이 값은 과거 평가로 계산한 로컬 예측이며 직접 측정한 만족도가 아니에요. 사용자의 감정을 직접 측정하지 않으며, 일반 서비스 화면에 노출하도록 승인되지 않았습니다.</p>
        </aside>

        {query.isPending && (
          <section className={styles.statePanel} role="status">
            <span className={styles.spinner} aria-hidden="true" />
            <h2>실험 결과를 계산하고 있어요</h2>
            <p>평가 분포와 추천 후보의 모델 출력을 확인하는 중입니다.</p>
          </section>
        )}

        {query.isError && (
          <section className={styles.statePanel} role="alert">
            <p className={styles.stateCode}>{query.error instanceof C6ApiError ? query.error.status : "ERROR"}</p>
            <h2>{query.error instanceof C6ApiError && query.error.status === 401 ? "실험 인증이 필요해요" : query.error instanceof C6ApiError && query.error.status === 503 ? "모델을 준비하지 못했어요" : "실험 결과를 불러오지 못했어요"}</h2>
            <p>{c6ErrorMessage(query.error)}</p>
            <button type="button" onClick={() => void query.refetch()}>다시 불러오기</button>
          </section>
        )}

        {experiment && (
          <>
            <section className={styles.profileSection} aria-labelledby="rating-profile-title">
              <div className={styles.sectionTitle}>
                <div><p className={styles.eyebrow}>RATING PROFILE</p><h2 id="rating-profile-title">내 평가 기준</h2></div>
                <span className={styles.confidence}>{confidenceLabel(experiment.ratingProfile.confidence)}</span>
              </div>
              <div className={styles.profileGrid}>
                <article><span>활성 평가</span><strong>{experiment.ratingProfile.activeRatingCount}개</strong></article>
                <article><span>내 평균</span><strong>{formatRating(experiment.ratingProfile.mean)}</strong></article>
                <article><span>내 중앙값</span><strong>{formatRating(experiment.ratingProfile.median)}</strong></article>
                <article><span>모델 입력</span><strong>{experiment.modelContext.usedRatingCount} / {experiment.modelContext.availableRatingCount}</strong></article>
              </div>
              {experiment.ratingProfile.activeRatingCount === 0 && <p className={styles.insufficient}>평가 기록이 없어 개인 기준을 계산할 수 없습니다. 이 상태의 숫자는 제품 판단에 사용하지 않습니다.</p>}
            </section>

            <section aria-labelledby="prediction-title">
              <div className={styles.sectionTitle}>
                <div><p className={styles.eyebrow}>PREDICTION PREVIEW</p><h2 id="prediction-title">예상 별점 (실험)</h2></div>
                <span>{experiment.predictions.length}편 · 제품 표시 불가</span>
              </div>
              {experiment.predictions.length === 0 ? (
                <div className={styles.emptyState}><h3>계산 가능한 영화가 없어요</h3><p>평가 수와 모델 아티팩트 상태를 확인해 주세요.</p></div>
              ) : (
                <div className={styles.predictionGrid}>
                  {experiment.predictions.map((prediction) => (
                    <article className={styles.predictionCard} key={prediction.movie.movieId} aria-label={`${prediction.movie.title} 예상 별점 실험 결과`}>
                      <div className={styles.poster}><Poster src={prediction.movie.posterUrl} title={prediction.movie.title} /></div>
                      <div className={styles.cardBody}>
                        <div className={styles.cardTopline}><span>{confidenceLabel(prediction.confidence)}</span><span>{prediction.directFoldIn ? "내 평가 직접 반영" : "모델 기준"}</span></div>
                        <h3>{prediction.movie.title}</h3>
                        <p className={styles.movieMeta}>{[prediction.movie.releaseYear, prediction.movie.genres.slice(0, 2).join(" · ")].filter(Boolean).join(" · ") || "메타데이터 없음"}</p>
                        <div className={styles.predictionValues}>
                          <div><span>예상 별점 (실험)</span><strong>{formatRating(prediction.predictedRating)}</strong></div>
                          <div><span>개인 기준 기대 효용</span><strong>{formatUtility(prediction.expectedRelativeUtility)}</strong></div>
                        </div>
                        <p className={styles.notEligible}>실험 전용 · displayEligible=false</p>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <section className={styles.evidenceSection} aria-labelledby="taste-evidence-title">
              <div className={styles.sectionTitle}>
                <div><p className={styles.eyebrow}>OBSERVED EVIDENCE</p><h2 id="taste-evidence-title">취향 관측 근거</h2></div>
                <span>성격 진단이 아닌 평가 집계</span>
              </div>
              {experiment.tasteEvidence.length === 0 ? (
                <div className={styles.emptyState}><h3>표시할 관측 근거가 없어요</h3><p>자료가 더 쌓이기 전에는 취향을 단정하지 않습니다.</p></div>
              ) : (
                <ul className={styles.evidenceList}>
                  {experiment.tasteEvidence.map((evidence) => (
                    <li key={`${evidence.dimensionType}:${evidence.dimensionKey}`}>
                      <div><span className={styles.dimension}>{evidence.dimensionType}</span><strong>{evidence.displayName}</strong></div>
                      <dl>
                        <div><dt>평가 수</dt><dd>{evidence.ratingCount}개</dd></div>
                        <div><dt>평균</dt><dd>{formatRating(evidence.averageRating)}</dd></div>
                        <div><dt>내 평균 대비</dt><dd>{evidence.liftFromUserMean === null ? "자료 부족" : `${evidence.liftFromUserMean >= 0 ? "+" : ""}${evidence.liftFromUserMean.toFixed(2)}`}</dd></div>
                        <div><dt>근거 수준</dt><dd>{confidenceLabel(evidence.confidence)}</dd></div>
                      </dl>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className={styles.limitations} aria-labelledby="limitations-title">
              <div><p className={styles.eyebrow}>READ BEFORE INTERPRETING</p><h2 id="limitations-title">해석 제한</h2></div>
              {experiment.limitations.length === 0 ? <p>서버에서 전달된 추가 제한 사항이 없습니다. 실험 경계는 계속 적용됩니다.</p> : <ul>{experiment.limitations.map((limitation) => <li key={limitation}>{limitationLabel(limitation)}</li>)}</ul>}
            </section>

            <footer className={styles.modelFooter}>
              <span>실험 {experiment.experimentVersion}</span>
              <span>입력 {experiment.inputVersion}</span>
              <span>아티팩트 {experiment.modelContext.artifactSetVersion}</span>
              <span>효용 정책 {experiment.modelContext.utilityPolicyVersion}</span>
              <span>정책 {experiment.modelContext.policyVersion}</span>
              <span>K 선택 {experiment.modelContext.kSelectionPolicyVersion}</span>
            </footer>
          </>
        )}
      </div>
    </main>
  );
}
