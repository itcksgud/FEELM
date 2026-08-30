import { useState } from "react";
import {
  comparisonLabels,
  comparisonOrder,
  onboardingComparison,
  partyComparison,
  reasonComparison,
  starComparison,
  type EvidenceComparisonId,
} from "./recEv008Fixture";
import styles from "./recEv008Lab.module.css";

const isComparison = (value: string | null): value is EvidenceComparisonId =>
  comparisonOrder.includes(value as EvidenceComparisonId);

export function RecEv008Lab({ initialComparison }: { initialComparison?: EvidenceComparisonId }) {
  const fromQuery = new URLSearchParams(window.location.search).get("comparison");
  const [comparison, setComparison] = useState<EvidenceComparisonId>(
    initialComparison ?? (isComparison(fromQuery) ? fromQuery : "stars"),
  );

  return (
    <main className={styles.lab} data-evidence-id="REC-EV-008">
      <header className={styles.header}>
        <p className={styles.eyebrow}>INTERNAL EVIDENCE LAB · 공개 기능 아님</p>
        <h1>REC-EV-008 UI 비교</h1>
        <p className={styles.lead}>
          같은 근거를 다른 밀도로 표현했을 때의 정보량과 조작 부담을 비교합니다. 어떤 안도 제품 정책으로
          승인되지 않았습니다.
        </p>
        <div className={styles.warning} role="status">
          <strong>제품 승격 금지</strong>
          <span>공개 navigation·API·실제 사용자 만족도 근거가 없습니다.</span>
        </div>
      </header>

      <nav className={styles.tabs} aria-label="REC-EV-008 비교 선택">
        {comparisonOrder.map((id) => (
          <button
            aria-current={comparison === id ? "page" : undefined}
            className={comparison === id ? styles.activeTab : styles.tab}
            key={id}
            onClick={() => setComparison(id)}
            type="button"
          >
            {comparisonLabels[id]}
          </button>
        ))}
      </nav>

      <section className={styles.canvas} aria-labelledby={`${comparison}-title`}>
        {comparison === "stars" && <StarComparison />}
        {comparison === "onboarding" && <OnboardingComparison />}
        {comparison === "party" && <PartyComparison />}
        {comparison === "reasons" && <ReasonComparison />}
      </section>

      <footer className={styles.footer}>
        <span>Fixture only · 네트워크 요청 없음</span>
        <span>Viewport 기준 1440 × 1200</span>
      </footer>
    </main>
  );
}

function ComparisonHeading({ id, title, summary }: { id: string; title: string; summary: string }) {
  return (
    <div className={styles.sectionHeading}>
      <div>
        <p className={styles.sequence}>비교 질문</p>
        <h2 id={`${id}-title`}>{title}</h2>
      </div>
      <p>{summary}</p>
    </div>
  );
}

function StarComparison() {
  return (
    <>
      <ComparisonHeading
        id="stars"
        title="예상 별점을 숫자로 보여줄 것인가?"
        summary="숫자의 즉시성과 calibration 부재 시 fail-closed 표현을 같은 카드에서 비교합니다."
      />
      <div className={styles.twoColumn}>
        <article className={styles.variant} aria-labelledby="star-visible-title">
          <VariantMeta label="A" status="K10 데이터 후보" />
          <h3 id="star-visible-title">숫자 표시</h3>
          <MovieStub />
          <div className={styles.starValue} aria-label="실험 예상 별점 4.2점, 5점 만점">
            <span aria-hidden="true">★</span>
            <strong>{starComparison.computed.value.toFixed(1)}</strong>
            <span>/ {starComparison.computed.scale}</span>
          </div>
          <p className={styles.caption}>실험 예상 별점 · 외부 평점/FEELM 평균과 다름</p>
          <EvidenceChip>{starComparison.computed.evidence}</EvidenceChip>
        </article>

        <article className={styles.variant} aria-labelledby="star-hidden-title">
          <VariantMeta label="B" status="FAIL-CLOSED" />
          <h3 id="star-hidden-title">숨김 / NOT_COMPUTED</h3>
          <MovieStub />
          <div className={styles.notComputed} role="note">
            <strong>예상 별점을 표시하지 않아요</strong>
            <span>{starComparison.hidden.reason}</span>
          </div>
          <EvidenceChip>{starComparison.hidden.evidence}</EvidenceChip>
        </article>
      </div>
      <Limitation>{starComparison.limitation}</Limitation>
    </>
  );
}

function OnboardingComparison() {
  return (
    <>
      <ComparisonHeading
        id="onboarding"
        title="K5·K10·skip의 입력 부담은 어떻게 다른가?"
        summary="시간·이탈률 대신 화면에서 확정적으로 셀 수 있는 최소 조작 수만 비교합니다."
      />
      <div className={styles.threeColumn}>
        {onboardingComparison.map((variant) => (
          <article className={styles.variant} key={variant.id} aria-labelledby={`onboarding-${variant.id}`}>
            <VariantMeta label={variant.id} status="미승인 입력안" />
            <h3 id={`onboarding-${variant.id}`}>{variant.title}</h3>
            <div className={styles.actionMeter}>
              <strong>{variant.minimumActions}</strong>
              <span>최소 조작</span>
            </div>
            <div className={styles.dotGrid} aria-label={`영화 판단 ${variant.movieDecisions}개`}>
              {Array.from({ length: 10 }, (_, index) => (
                <span
                  className={index < variant.movieDecisions ? styles.dotActive : styles.dot}
                  key={index}
                  aria-hidden="true"
                />
              ))}
            </div>
            <p>{variant.movieDecisions}개 영화 판단 + 완료/skip 1회</p>
            <p className={styles.caption}>{variant.note}</p>
          </article>
        ))}
      </div>
      <Limitation>
        MovieLens에는 가입 이탈·소요시간이 없습니다. 위 숫자는 UI 최소 조작 수이며 실제 인지 부담이나
        완료율이 아닙니다.
      </Limitation>
    </>
  );
}

function PartyComparison() {
  return (
    <>
      <ComparisonHeading
        id="party"
        title="Average와 Balanced 후보는 무엇을 다르게 고르는가?"
        summary="실제 순위 반전 사례와 전체 paired bootstrap을 분리해서 보여줍니다."
      />
      <div className={styles.twoColumn}>
        {[partyComparison.average, partyComparison.balanced].map((variant, index) => (
          <article className={styles.variant} key={variant.policy} aria-labelledby={`party-${index}`}>
            <VariantMeta label={index === 0 ? "A" : "B"} status={index === 0 ? "기준선" : "개선 미입증"} />
            <h3 id={`party-${index}`}>{variant.policy}</h3>
            <p className={styles.movieTitle}>{variant.selectedTitle}</p>
            <dl className={styles.metricList}>
              <div>
                <dt>관측 relative utility 평균</dt>
                <dd>{variant.observedRelativeUtility.toFixed(3)}</dd>
              </div>
              <div>
                <dt>구성원 MovieLens 평가</dt>
                <dd>{variant.ratings.join(" · ")}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
      <div className={styles.ciPanel} aria-label="Balanced와 Average의 paired bootstrap 차이">
        <h3>Balanced − Average · 95% CI</h3>
        <div className={styles.ciGrid}>
          {partyComparison.pairedBootstrap.map((metric) => (
            <div key={metric.label}>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <code>{metric.ci}</code>
            </div>
          ))}
        </div>
      </div>
      <Limitation>{partyComparison.limitation} 실제 파티 만족도를 관측하지 않았습니다.</Limitation>
    </>
  );
}

function ReasonComparison() {
  return (
    <>
      <ComparisonHeading
        id="reasons"
        title="추천 이유를 1개만, 또는 최대 3개까지 보여줄 것인가?"
        summary="REC-EV-006의 typed reason coverage만 사용하며 문구·개수 승인은 분리합니다."
      />
      <div className={styles.twoColumn}>
        {reasonComparison.variants.map((variant, index) => (
          <article className={styles.variant} key={variant.id} aria-labelledby={`reason-${variant.id}`}>
            <VariantMeta label={index === 0 ? "A" : "B"} status="제품 결정 대기" />
            <h3 id={`reason-${variant.id}`}>{variant.title}</h3>
            <ul className={styles.reasonList}>
              {variant.reasons.map((reason) => (
                <li key={reason.code}>
                  <span className={styles.reasonCode}>{reason.code}</span>
                  <strong>{reason.candidateCopy}</strong>
                  <span>emittable coverage {(reason.coverage * 100).toFixed(2)}%</span>
                </li>
              ))}
            </ul>
            <p className={styles.caption}>실험 문구 · 실제 UI copy로 승인되지 않음</p>
          </article>
        ))}
      </div>
      <div className={styles.provenance}>
        <strong>{reasonComparison.contract}</strong>
        <span>{reasonComparison.evidenceStatus}</span>
        <span>positive contribution + rank effect + provenance + non-sensitive Gate</span>
      </div>
      <Limitation>{reasonComparison.limitation}</Limitation>
    </>
  );
}

function MovieStub() {
  return (
    <div className={styles.movieStub} aria-label={starComparison.movieTitle}>
      <div aria-hidden="true" />
      <span>
        <strong>{starComparison.movieTitle}</strong>
        <small>드라마 · 2025</small>
      </span>
    </div>
  );
}

function VariantMeta({ label, status }: { label: string; status: string }) {
  return (
    <div className={styles.variantMeta}>
      <span>{label}</span>
      <strong>{status}</strong>
    </div>
  );
}

function EvidenceChip({ children }: { children: string }) {
  return <span className={styles.evidenceChip}>{children}</span>;
}

function Limitation({ children }: { children: React.ReactNode }) {
  return (
    <aside className={styles.limitation} aria-label="근거 한계">
      <strong>해석 한계</strong>
      <p>{children}</p>
    </aside>
  );
}
