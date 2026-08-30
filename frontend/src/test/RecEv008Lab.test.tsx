import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { RecEv008Lab } from "../evidence/RecEv008Lab";

describe("REC-EV-008 internal evidence lab", () => {
  it("예상 별점 표시와 NOT_COMPUTED를 출처·척도·한계와 함께 비교한다", () => {
    render(<RecEv008Lab initialComparison="stars" />);

    expect(screen.getByRole("main")).toHaveAttribute("data-evidence-id", "REC-EV-008");
    expect(screen.getByRole("heading", { level: 1, name: "REC-EV-008 UI 비교" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("제품 승격 금지");
    expect(screen.getByLabelText("실험 예상 별점 4.2점, 5점 만점")).toBeInTheDocument();
    expect(screen.getByText("숨김 / NOT_COMPUTED")).toBeInTheDocument();
    expect(screen.getByText("REC-EV-003B K10_DATA_ONLY")).toBeInTheDocument();
    expect(screen.getByText("REC-EV-003C fail-closed")).toBeInTheDocument();
    expect(screen.getByLabelText("근거 한계")).toHaveTextContent("추천 순위 개선 근거는 아닙니다");
  });

  it("K5·K10·skip은 실제 이탈률 대신 최소 조작 수만 비교한다", async () => {
    const user = userEvent.setup();
    render(<RecEv008Lab initialComparison="stars" />);

    const onboardingTab = screen.getByRole("button", { name: "온보딩 부담" });
    await user.click(onboardingTab);
    expect(onboardingTab).toHaveAttribute("aria-current", "page");

    const section = screen.getByRole("region", { name: "K5·K10·skip의 입력 부담은 어떻게 다른가?" });
    expect(within(section).getByText("K5 빠른 입력안")).toBeInTheDocument();
    expect(within(section).getByText("K10 데이터 후보")).toBeInTheDocument();
    expect(within(section).getByText("건너뛰기")).toBeInTheDocument();
    expect(within(section).getAllByText("최소 조작")).toHaveLength(3);
    expect(within(section).getByLabelText("영화 판단 5개")).toBeInTheDocument();
    expect(within(section).getByLabelText("영화 판단 10개")).toBeInTheDocument();
    expect(within(section).getByLabelText("영화 판단 0개")).toBeInTheDocument();
    expect(within(section).getByLabelText("근거 한계")).toHaveTextContent("실제 인지 부담이나 완료율이 아닙니다");
  });

  it("Balanced를 개선 미입증 후보로 표시하고 세 CI와 4인 coverage 한계를 노출한다", () => {
    render(<RecEv008Lab initialComparison="party" />);

    expect(screen.getByText("AVERAGE")).toBeInTheDocument();
    expect(screen.getByText("BALANCED 후보")).toBeInTheDocument();
    expect(screen.getByText("개선 미입증")).toBeInTheDocument();
    const ci = screen.getByLabelText("Balanced와 Average의 paired bootstrap 차이");
    expect(within(ci).getByText("[-0.0037, +0.0007]")).toBeInTheDocument();
    expect(within(ci).getByText("[-0.0035, +0.0045]")).toBeInTheDocument();
    expect(within(ci).getByText("[-0.0116, +0.0024]")).toBeInTheDocument();
    expect(screen.getByLabelText("근거 한계")).toHaveTextContent("0.69%~1.02%");
    expect(screen.getByLabelText("근거 한계")).toHaveTextContent("실제 파티 만족도를 관측하지 않았습니다");
  });

  it("REC-EV-006 typed coverage로 이유 1개와 최대 3개를 비교하되 문구·개수를 승인하지 않는다", async () => {
    const user = userEvent.setup();
    render(<RecEv008Lab initialComparison="reasons" />);

    expect(screen.getByText("REC_REASON_FAITHFULNESS_V1")).toBeInTheDocument();
    expect(screen.getAllByText("POPULARITY_BASELINE")).toHaveLength(2);
    expect(screen.getByText("LIST_DIVERSITY")).toBeInTheDocument();
    expect(screen.getByText("LESS_POPULAR_DISCOVERY")).toBeInTheDocument();
    expect(screen.queryByText("GENRE_AFFINITY")).not.toBeInTheDocument();
    expect(screen.getByText("emittable coverage 59.98%")).toBeInTheDocument();
    expect(screen.getByText("emittable coverage 24.31%")).toBeInTheDocument();
    expect(screen.getAllByText("실험 문구 · 실제 UI copy로 승인되지 않음")).toHaveLength(2);

    const navigation = screen.getByRole("navigation", { name: "REC-EV-008 비교 선택" });
    const firstTab = within(navigation).getByRole("button", { name: "예상 별점" });
    firstTab.focus();
    expect(firstTab).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(firstTab).toHaveAttribute("aria-current", "page");
  });
});

