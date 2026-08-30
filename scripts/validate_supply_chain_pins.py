from __future__ import annotations

import hashlib
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHA256 = r"[0-9a-f]{64}"

EXPECTED_IMAGES = {
    "backend/Dockerfile": {
        "eclipse-temurin:17-jdk-alpine@sha256:0bd5d65efad5c8d9f8d8e6573aa5c8851237550605ff18ff78fee5810c2ebe25",
        "eclipse-temurin:17-jre-alpine@sha256:27cc0849148c0fd32ee8e95988917becf9bc96a3182a24f99d9763aa8e90f8cb",
    },
    "frontend/Dockerfile": {
        "node:22.14-alpine@sha256:9bef0ef1e268f60627da9ba7d7605e8831d5b56ad07487d24d1aa386336d1944",
        "nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10",
    },
    "recommender/Dockerfile": {
        "python:3.12.5-slim-bookworm@sha256:c24c34b502635f1f7c4e99dc09a2cbd85d480b7dcfd077198c6b5af138906390",
    },
    "docker-compose.yml": {
        "postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94",
        "axllent/mailpit:v1.30.4@sha256:5a49a77c5bdbe7c5474450b4f46348d09949df3695257729c93a30369382d4f6",
    },
}

EXPECTED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/setup-java": "cf277c60eb25467037889841efdb72551f06f6c3",
}

EXPECTED_GRADLE_DISTRIBUTION_SHA256 = (
    "bd71102213493060956ec229d946beee57158dbd89d0e62b91bca0fa2c5f3531"
)
EXPECTED_GRADLE_WRAPPER_JAR_SHA256 = (
    "7d3a4ac4de1c32b59bc6a4eb8ecb8e612ccd0cf1ae1e99f66902da64df296172"
)
EXPECTED_GITLEAKS_VERSION = "8.29.1"
EXPECTED_GITLEAKS_WINDOWS_SHA256 = "e4b7d556f0cddbe23d10d8fac2ab0f29f68f019091c6599ffbeaa8a4fb71ac78"
EXPECTED_GITLEAKS_LINUX_SHA256 = "e4eb209d04e20339d77122a3bdf9cd41351255cfb27ebcb75e85325e04f88924"
EXPECTED_ACTIONLINT_VERSION = "1.7.12"
EXPECTED_ACTIONLINT_WINDOWS_SHA256 = "6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9"
EXPECTED_ACTIONLINT_LINUX_SHA256 = "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_packages(relative_path: str, errors: list[str]) -> set[str]:
    path = ROOT / relative_path
    if not path.is_file():
        errors.append(f"missing hash lock: {relative_path}")
        return set()

    text = path.read_text(encoding="utf-8")
    if "--hash=sha256:" not in text:
        errors.append(f"hash lock has no SHA-256 entries: {relative_path}")
    if re.search(r"(?m)^\s*(--index-url|--extra-index-url|--trusted-host)\b", text):
        errors.append(f"hash lock embeds an alternate package index: {relative_path}")

    packages: set[str] = set()
    current_name: str | None = None
    current_has_hash = False
    for line in [*text.splitlines(), ""]:
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
        if match:
            if current_name is not None and not current_has_hash:
                errors.append(f"unhashed requirement in {relative_path}: {current_name}")
            current_name = normalize_package(match.group(1))
            current_has_hash = False
            packages.add(current_name)
        elif current_name is not None and "--hash=sha256:" in line:
            hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", line)
            if not hashes:
                errors.append(f"malformed SHA-256 in {relative_path}: {current_name}")
            current_has_hash = current_has_hash or bool(hashes)
    if current_name is not None and not current_has_hash:
        errors.append(f"unhashed requirement in {relative_path}: {current_name}")
    return packages


def direct_pyproject_dependencies() -> tuple[set[str], set[str]]:
    data = tomllib.loads(read("recommender/pyproject.toml"))
    runtime = {
        normalize_package(re.match(r"^([A-Za-z0-9_.-]+)", item).group(1))
        for item in data["project"]["dependencies"]
    }
    test = {
        normalize_package(re.match(r"^([A-Za-z0-9_.-]+)", item).group(1))
        for item in data["project"]["optional-dependencies"]["test"]
    }
    return runtime, test


def direct_requirement_dependencies(relative_path: str) -> set[str]:
    result = set()
    for line in read(relative_path).splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==", line.strip())
        if match:
            result.add(normalize_package(match.group(1)))
    return result


def main() -> int:
    errors: list[str] = []

    for relative_path, expected_refs in EXPECTED_IMAGES.items():
        text = read(relative_path)
        for image_ref in expected_refs:
            if image_ref not in text:
                errors.append(f"missing expected image digest in {relative_path}: {image_ref}")
        for image_ref in re.findall(r"(?m)^(?:FROM|\s*image:)\s+([^\s]+)", text):
            if not re.search(rf"@sha256:{SHA256}$", image_ref):
                errors.append(f"container image is not digest-pinned in {relative_path}: {image_ref}")

    wrapper = read("backend/gradle/wrapper/gradle-wrapper.properties")
    checksum_match = re.search(r"(?m)^distributionSha256Sum=([0-9a-f]+)$", wrapper)
    if not checksum_match or checksum_match.group(1) != EXPECTED_GRADLE_DISTRIBUTION_SHA256:
        errors.append("Gradle distributionSha256Sum is missing or differs from the audited Gradle 8.14.3 ZIP")
    wrapper_jar = ROOT / "backend/gradle/wrapper/gradle-wrapper.jar"
    if not wrapper_jar.is_file():
        errors.append("Gradle wrapper JAR is missing")
    else:
        actual_wrapper_hash = hashlib.sha256(wrapper_jar.read_bytes()).hexdigest()
        if actual_wrapper_hash != EXPECTED_GRADLE_WRAPPER_JAR_SHA256:
            errors.append("Gradle wrapper JAR differs from the official Gradle 8.14.3 wrapper JAR")

    runtime_packages = locked_packages("recommender/requirements.lock", errors)
    test_packages = locked_packages("recommender/requirements-test.lock", errors)
    data_packages = locked_packages("requirements-data.lock", errors)
    locked_packages("scripts/requirements-lock-tools.lock", errors)
    locked_packages("scripts/requirements-audit-tools.lock", errors)
    locked_packages("scripts/requirements-build-tools.lock", errors)
    runtime_direct, test_direct = direct_pyproject_dependencies()
    data_direct = direct_requirement_dependencies("requirements-data.txt")
    if missing := runtime_direct - runtime_packages:
        errors.append(f"runtime lock misses direct dependencies: {sorted(missing)}")
    if missing := (runtime_direct | test_direct) - test_packages:
        errors.append(f"test lock misses direct dependencies: {sorted(missing)}")
    if missing := data_direct - data_packages:
        errors.append(f"data lock misses direct dependencies: {sorted(missing)}")

    dockerfile = read("recommender/Dockerfile")
    for required_fragment in (
        "COPY requirements.lock ./",
        "pip install --no-cache-dir --require-hashes -r requirements.lock",
        "pip install --no-cache-dir --no-deps --no-build-isolation .",
    ):
        if required_fragment not in dockerfile:
            errors.append(f"recommender Dockerfile misses locked install boundary: {required_fragment}")

    frontend_dockerfile = read("frontend/Dockerfile")
    if "npm install --global" in frontend_dockerfile or "npm install -g" in frontend_dockerfile:
        errors.append("frontend Dockerfile must use npm bundled in the digest-pinned Node image")

    workflow = read(".github/workflows/ci.yml")
    if workflow.count("runs-on: ubuntu-24.04") != 8 or "ubuntu-latest" in workflow:
        errors.append("CI runner images must be fixed to ubuntu-24.04 for all eight jobs")
    if workflow.count("python-version: '3.12.5'") != 4:
        errors.append("CI Python must be fixed to 3.12.5 for all four Python jobs")
    if workflow.count("java-version: '17.0.20+8'") != 2:
        errors.append("CI Temurin Java must be fixed to 17.0.20+8 for both Java jobs")
    action_refs = re.findall(r"uses:\s+(actions/[A-Za-z0-9_.-]+)@([^\s#]+)", workflow)
    for action, revision in action_refs:
        expected = EXPECTED_ACTIONS.get(action)
        if expected is None:
            errors.append(f"unreviewed first-party GitHub Action: {action}")
        elif revision != expected:
            errors.append(f"GitHub Action is not pinned to audited revision: {action}@{revision}")
    for action, revision in EXPECTED_ACTIONS.items():
        if (action, revision) not in action_refs:
            errors.append(f"expected pinned GitHub Action is absent: {action}@{revision}")

    for required_fragment in (
        "pip install --require-hashes -r recommender/requirements-test.lock",
        "pip install --no-build-isolation --require-hashes -r requirements-data.lock",
        "pip install --require-hashes -r scripts/requirements-audit-tools.lock",
        "pip install --require-hashes -r scripts/requirements-build-tools.lock",
        "pip_audit -r recommender/requirements.lock",
        "pip_audit -r recommender/requirements-test.lock",
        "pip_audit -r requirements-data.lock",
        "pip_audit -r scripts/requirements-audit-tools.lock",
        "pip_audit -r scripts/requirements-build-tools.lock",
        "pip_audit -r scripts/requirements-lock-tools.lock",
    ):
        if required_fragment not in workflow:
            errors.append(f"CI misses locked dependency boundary: {required_fragment}")
    if workflow.count("pip install --require-hashes -r scripts/requirements-build-tools.lock") != 2:
        errors.append("CI must install hash-locked build tools for evidence and data-pipeline jobs")

    compose = read("docker-compose.yml")
    if compose.count("platform: linux/amd64") != 6:
        errors.append("all six Compose services must select the audited linux/amd64 image manifests")

    history_script = read("scripts/verify-git-history-secrets.ps1")
    for required_fragment in (
        f"$version = '{EXPECTED_GITLEAKS_VERSION}'",
        EXPECTED_GITLEAKS_WINDOWS_SHA256,
        'GITLEAKS_POSITIVE_CONTROL_NOT_DETECTED',
        "gitleaks/gitleaks/releases/download",
    ):
        if required_fragment not in history_script:
            errors.append(f"local history scanner misses pinned boundary: {required_fragment}")
    for required_fragment in (
        f"releases/download/v{EXPECTED_GITLEAKS_VERSION}/gitleaks_{EXPECTED_GITLEAKS_VERSION}_linux_x64.tar.gz",
        EXPECTED_GITLEAKS_LINUX_SHA256,
        "fetch-depth: 0",
        "control_exit",
        "gitleaks git --no-banner --redact .",
    ):
        if required_fragment not in workflow:
            errors.append(f"CI history scanner misses pinned boundary: {required_fragment}")

    actionlint_script = read("scripts/verify-github-workflow.ps1")
    for required_fragment in (
        f"$version = '{EXPECTED_ACTIONLINT_VERSION}'",
        EXPECTED_ACTIONLINT_WINDOWS_SHA256,
        "rhysd/actionlint/releases/download",
        "'-no-color'",
    ):
        if required_fragment not in actionlint_script:
            errors.append(f"local workflow scanner misses pinned boundary: {required_fragment}")
    for required_fragment in (
        f"releases/download/v{EXPECTED_ACTIONLINT_VERSION}/actionlint_{EXPECTED_ACTIONLINT_VERSION}_linux_amd64.tar.gz",
        EXPECTED_ACTIONLINT_LINUX_SHA256,
        'tool_root="$RUNNER_TEMP/feelm-actionlint-1.7.12"',
        '"$tool_root/actionlint" -no-color .github/workflows/ci.yml',
    ):
        if required_fragment not in workflow:
            errors.append(f"CI workflow scanner misses pinned boundary: {required_fragment}")

    if errors:
        print("Supply-chain pin validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        "Supply-chain pin validation: PASS "
        f"({sum(len(value) for value in EXPECTED_IMAGES.values())} images, "
        f"{len(EXPECTED_ACTIONS)} actions, 6 Python locks, Gradle ZIP+wrapper checksums, "
        "Gitleaks pinned+controlled, actionlint pinned)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
