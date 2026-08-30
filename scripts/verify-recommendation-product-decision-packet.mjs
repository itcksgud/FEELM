import { access, readFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const packet = await text("docs/recommendation/product-decision-packet.md");
const registry = await text("docs/recommendation/product-decisions-required.md");
const tasks = await text("docs/tasks/recommendation-evidence-backlog.yaml");
const evidenceReadme = await text("docs/recommendation/evidence/README.md");
const errors = [];

check(packet.includes("상태: `APPROVED_LOCAL_PRODUCT_BOUNDARIES`"), "packet local approval status drift");
check(packet.includes("승인 결과: `RECORDED_LOCAL_PRODUCT_APPROVAL`"), "packet approval record missing");
check(packet.includes("공개 UI/API/추천 champion 변경: `NO`"), "packet product boundary missing");

const decisions = {
  "REC-PD-001": "stars-1440x1200.png",
  "REC-PD-003": "onboarding-1440x1200.png",
  "REC-PD-005": "party-1440x1200.png",
  "REC-PD-007": "reasons-1440x1200.png",
};
const headings = [...packet.matchAll(/^## \d+\. (REC-PD-\d{3})/gm)];
for (const [decision, screenshot] of Object.entries(decisions)) {
  const heading = headings.find((match) => match[1] === decision);
  if (!heading) {
    errors.push(`${decision} section missing`);
    continue;
  }
  const next = headings.find((match) => match.index > heading.index);
  const section = packet.slice(heading.index, next?.index ?? packet.length);
  for (const phrase of [
    "제품 영향 한 문장",
    screenshot,
    "동일 조건 수치",
    "권장안",
    "반대안과 손실",
    "불확실성·MovieLens 한계",
    "되돌림 비용",
    "선택됨",
  ]) {
    check(section.includes(phrase), `${decision} missing ${phrase}`);
  }
  const registryRow = registry.split("\n").find((line) => line.includes(`\`${decision}\``)) ?? "";
  check(registryRow.includes("`APPROVED_LOCAL_PRODUCT_BOUNDARY`"), `${decision} registry status drift`);
  await access(
    path.resolve(root, `docs/recommendation/evidence/assets/rec-ev-008/${screenshot}`),
  ).catch(() => errors.push(`${decision} screenshot missing: ${screenshot}`));
}

const onboardingHeading = headings.find((match) => match[1] === "REC-PD-003");
const nextOnboarding = headings.find((match) => match.index > onboardingHeading.index);
const onboarding = packet.slice(onboardingHeading.index, nextOnboarding?.index ?? packet.length);
for (const phrase of [
  "DN-C4A-004",
  "maximum=10",
  "submittedMinimum=1",
  "skipAtZero=true",
  "rerun=VERSIONED_REPLACE",
]) {
  check(onboarding.includes(phrase), `REC-PD-003/C4A shared decision missing ${phrase}`);
}

check(/^status: LOCAL_PRODUCT_BOUNDARIES_APPROVED$/m.test(tasks), "recommendation task registry approval drift");
const task009 = tasks.slice(tasks.indexOf("- id: TASK-REC-EV-009"));
check(/status: DONE/.test(task009), "TASK-REC-EV-009 is not DONE");
check(
  evidenceReadme.includes("제품 결정 패킷: `APPROVED_LOCAL_PRODUCT_BOUNDARIES`"),
  "evidence README packet status drift",
);

if (errors.length) {
  console.error("Recommendation product decision packet verification failed:");
  for (const error of errors) console.error(`- ${error}`);
  process.exit(1);
}

console.log(
  "Recommendation product decision packet verification passed: 4 local product boundaries recorded, 4 canonical screenshots, C4A shared gate linked; production remains blocked.",
);

function check(condition, message) {
  if (!condition) errors.push(message);
}

async function text(relativePath) {
  return readFile(path.resolve(root, relativePath), "utf8");
}
