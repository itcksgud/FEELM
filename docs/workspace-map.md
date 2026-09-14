# FEELM 폴더·브랜치 지도

확인: 2026-09-12. 같은 Git 저장소를 여러 worktree로 연 상태다. 브랜치와 폴더가 항상 일대일로 생기는 것은 아니다.

| 폴더 (`C:/higher/projects/` 아래) | 브랜치 | 용도 |
| --- | --- | --- |
| `FEELM-standalone` | `project/standalone-feelm` | 개인 연구 원본. 최신 미커밋 실험도 여기에 있음 |
| `FEELM-docs-efficiency` | `docs/ai-efficiency-20260912` | 현재 문서 정리·사용량 측정 작업. 원본의 로컬 문서·스크립트를 보존 복사한 작업공간 |
| `FEELM` | `main` | 기본 checkout |
| `FEELM-recommendation-lab` | `experiment/recommendation-lab` | 이전 실험 작업공간 |
| `FEELM-presentation-script` | `docs/presentation-script-v2` | 이전 발표 작업공간 |
| `FEELM-standalone/.codex-tmp/discovery-classifier-20260911` | `research/discovery-classifier-20260911` | 임시 연구 worktree |
| `FEELM-standalone/.codex-tmp/rec038-korean-movie-guide-20260911` | `docs/rec038-korean-movie-guide-20260911` | 임시 설명 worktree |

GitHub 원격에서 확인한 브랜치는 `main`, `project/standalone-feelm` 두 개였다. 로컬 작업 브랜치나 미커밋 파일은 Push 전까지 GitHub에 나타나지 않는다.
팀 GitLab 작업·공식 문서는 `S15P21E106`에서 관리한다. 개인 연구 선정안을 팀 계약으로 취급하지 않는다.

최신 상태는 저장소 안에서 `git worktree list`, `git status --short --branch`, `git branch -vv`로 확인한다.
이 표는 정리 시점의 지도이며 자동으로 갱신되지 않는다. 원본 worktree를 강제 전환·정리하지 않는다.
