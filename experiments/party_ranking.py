"""팀 계약과 분리해 파티 추천 점수 정책을 실험하는 작은 실행 파일."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from statistics import fmean
from typing import Iterable, Sequence


POLICY_VERSION = "PARTY_BALANCED_V1_EXPERIMENT"


@dataclass(frozen=True)
class PartyScore:
    """한 후보 영화에 대한 파티 점수와 설명 가능한 구성 요소."""

    policy_version: str
    member_count: int
    mean_predicted_star: float
    minimum_predicted_star: float
    preference_gap: float
    raw_score: float
    fit_score: int
    eligible: bool
    fallback_reason: str | None


def calculate_party_score(
    predicted_stars: Iterable[float],
    *,
    minimum_threshold: float = 3.0,
) -> PartyScore:
    """개인별 예상 별점을 평균·최저점·편차로 집계한다.

    실험식은 다음과 같다.

    ``20 * clamp(0.60 * mean + 0.40 * minimum - 0.10 * gap, 0, 5)``

    최저 예상 별점이 ``minimum_threshold`` 미만이면 기본 추천 후보에서 제외하고,
    후보 부족 시에만 fallback으로 사용할 수 있도록 표시한다.
    """

    stars = tuple(float(star) for star in predicted_stars)
    if not stars:
        raise ValueError("파티 점수에는 한 명 이상의 예상 별점이 필요합니다.")
    if not 1.0 <= minimum_threshold <= 5.0:
        raise ValueError("최저 별점 기준은 1.0~5.0 범위여야 합니다.")
    if any(not 1.0 <= star <= 5.0 for star in stars):
        raise ValueError("예상 별점은 모두 1.0~5.0 범위여야 합니다.")

    mean_star = fmean(stars)
    minimum_star = min(stars)
    preference_gap = max(stars) - minimum_star
    raw_score = 0.60 * mean_star + 0.40 * minimum_star - 0.10 * preference_gap
    clamped_score = min(max(raw_score, 0.0), 5.0)
    eligible = minimum_star >= minimum_threshold

    return PartyScore(
        policy_version=POLICY_VERSION,
        member_count=len(stars),
        mean_predicted_star=round(mean_star, 3),
        minimum_predicted_star=round(minimum_star, 3),
        preference_gap=round(preference_gap, 3),
        raw_score=round(raw_score, 3),
        fit_score=round(20 * clamped_score),
        eligible=eligible,
        fallback_reason=None if eligible else "MEMBER_BELOW_MINIMUM_THRESHOLD",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="개인별 예상 별점으로 설명 가능한 파티 적합도 점수를 계산합니다."
    )
    parser.add_argument(
        "predicted_stars",
        metavar="STAR",
        type=float,
        nargs="+",
        help="각 파티원의 1.0~5.0 예상 별점",
    )
    parser.add_argument(
        "--minimum-threshold",
        type=float,
        default=3.0,
        help="기본 후보가 되기 위한 모든 파티원의 최소 예상 별점 (기본값: 3.0)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = calculate_party_score(
        args.predicted_stars,
        minimum_threshold=args.minimum_threshold,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

