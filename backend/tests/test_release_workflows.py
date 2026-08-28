import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
RELEASE_POLICY = ROOT / "packaging" / "windows" / "release-policy.json"
RELEASE_RUNBOOK = ROOT / "docs" / "installer" / "RELEASE_RUNBOOK.md"
PHASE_AUDIT = ROOT / "docs" / "installer" / "PHASE_3_6_AUDIT.md"


def test_ci_runs_for_the_repository_default_branch_and_security_gate() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "branches: [main, master, develop]" in workflow
    assert "branches: [main, master]" in workflow
    assert "node --test tests/security-dependencies.test.mjs" in workflow
    assert "pip install pip-audit==2.10.1" in workflow
    assert "python -m pip_audit -r requirements.txt --progress-spinner off" in workflow


def test_tag_deploy_cannot_build_images_before_release_verification() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "\n  verify-release:\n" in workflow
    assert "python -m pytest" in workflow
    assert "node --test tests/security-dependencies.test.mjs" in workflow
    assert "pip install pip-audit==2.10.1" in workflow
    assert "python -m pip_audit -r requirements.txt --progress-spinner off" in workflow
    assert "npm run build" in workflow
    assert "\n    needs: verify-release\n" in workflow


def test_tag_deploy_has_minimal_registry_permissions_and_traceable_images() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "\npermissions:\n  contents: read\n" in workflow
    assert "\n    permissions:\n      contents: read\n      packages: write\n" in workflow
    assert "id: backend_image" in workflow
    assert "id: frontend_image" in workflow
    assert "ghcr.io/${{ github.repository }}/backend:sha-${{ github.sha }}" in workflow
    assert "ghcr.io/${{ github.repository }}/frontend:sha-${{ github.sha }}" in workflow
    assert "${{ steps.backend_image.outputs.digest }}" in workflow
    assert "${{ steps.frontend_image.outputs.digest }}" in workflow


def test_tag_release_candidate_strictly_validates_version_and_policy() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    policy = json.loads(RELEASE_POLICY.read_text(encoding="utf-8"))

    assert "^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$" in workflow
    assert "id: release_metadata" in workflow
    assert "published_at" in workflow
    assert policy == {
        "schemaVersion": 1,
        "keyId": "windows-release-v1",
        "migrationStrategy": "alembic_upgrade_head",
        "supportedFrom": [],
    }


def test_release_candidate_builds_and_gates_exactly_six_linux_amd64_images() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "\n  build-images:\n" in workflow
    assert "file: ./deploy/nginx/Dockerfile.release" in workflow
    assert workflow.count("platforms: linux/amd64") == 3
    for name in ("backend", "frontend", "nginx", "postgres", "redis", "browserless"):
        assert f'{{name:"{name}"' in workflow
        assert f"{name}_digest" in workflow
    for reference in (
        "pgvector/pgvector@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
        "redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99",
        "browserless/chrome@sha256:57d19e414d9fe4ae9d2ab12ba768c97f38d51246c5b31af55a009205c136012f",
    ):
        assert reference in workflow
    assert "anchore/sbom-action@v0.24.0" in workflow
    assert "anchore/scan-action@v7" in workflow
    assert "sigstore/cosign-installer@v4.1.2" in workflow
    assert "cosign sign --yes" in workflow
    assert "cosign attest --yes" in workflow
    assert "uses: actions/attest@v4" in workflow


def test_release_candidate_assembles_verifies_and_signs_online_and_offline_assets() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "python packaging/release_tools/evaluate_vulnerabilities.py" in workflow
    assert "python packaging/release_tools/generate_third_party_licenses.py" in workflow
    assert "python packaging/release_tools/build_windows_release.py" in workflow
    assert "python packaging/release_tools/verify_windows_release.py" in workflow
    assert "python packaging/release_tools/build_windows_online_release.py" in workflow
    assert "python packaging/release_tools/verify_windows_online_release.py" in workflow
    assert '--repository-url "https://github.com/$GITHUB_REPOSITORY"' in workflow
    assert "--schema packaging/release-manifest.schema.json" in workflow
    assert "--schema packaging/windows/release-manifest.schema.json" not in workflow
    assert "python packaging/release_tools/finalize_release_assets.py sbom-zip" in workflow
    assert "python packaging/release_tools/finalize_release_assets.py sign-checksums" in workflow
    assert "python packaging/release_tools/finalize_release_assets.py verify-checksums" in workflow
    assert '$(basename "$online_zip")' in workflow
    assert "skopeo copy --preserve-digests" in workflow
    assert "-C work/images-oci blobs index.json oci-layout" in workflow
    assert "-C work/images-oci ." not in workflow
    assert "release-candidate" in workflow
    assert "merge-multiple: false" in workflow
    assert "supply/supply-backend/backend.grype.json" in workflow
    assert 'public_key_sha256="$(jq -r \'.publicKeySha256\' work/build-result.json)"' in workflow
    assert 'sha256sum work/keys/public.pem' not in workflow


def test_formal_release_waits_for_three_clean_windows_offline_rounds() -> None:
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "[self-hosted, Windows, X64, kanyikan-clean-e2e]" in workflow
    assert "round: [1, 2, 3]" in workflow
    assert "Invoke-CleanOfflineInstallE2E.ps1" in workflow
    assert "Invoke-NegativeInstallE2E.ps1" in workflow
    assert "KANYIKAN_ENTER_OFFLINE_SCRIPT" in workflow
    assert "KANYIKAN_INFRASTRUCTURE_HOOKS_ROOT" in workflow
    assert "if: always()" in workflow
    assert "formalReleaseBlockedBy" not in workflow
    assert "needs: [assemble-offline-candidate, windows-clean-offline-e2e]" in workflow
    assert "gh release create" in workflow
    assert "--verify-tag" in workflow
    assert "--draft" in workflow
    assert 'gh release edit "$GITHUB_REF_NAME" --draft=false' in workflow


def test_release_runbook_declares_secrets_runner_hooks_evidence_and_no_publish_boundary() -> None:
    runbook = RELEASE_RUNBOOK.read_text(encoding="utf-8")

    for value in (
        "WINDOWS_RELEASE_PRIVATE_KEY_PEM",
        "WINDOWS_RELEASE_PUBLIC_KEY_PEM",
        "KANYIKAN_CLEAN_E2E=1",
        "self-hosted, Windows, X64, kanyikan-clean-e2e",
        "KANYIKAN_ENTER_OFFLINE_SCRIPT",
        "KANYIKAN_EXIT_OFFLINE_SCRIPT",
        "KANYIKAN_INFRASTRUCTURE_HOOKS_ROOT",
        "Enter-DockerStopped.ps1",
        "Exit-WindowsContainers.ps1",
        "Enter-DiskInsufficient.ps1",
        "release-candidate",
        "windows-e2e-round-1",
        "ONLINE_PACKAGE_CONTRACT",
    ):
        assert value in runbook
    assert "不得创建 GitHub Release" in runbook
    assert "不得输出私钥" in runbook


def test_phase_audit_separates_automated_evidence_from_unverified_release_gates() -> None:
    audit = PHASE_AUDIT.read_text(encoding="utf-8")

    assert re.search(r"审计基线：`[0-9a-f]{40}`", audit)
    for value in (
        "108/108",
        "39/39",
        "IMPLEMENTED_AUTOMATED_VERIFIED",
        "IMPLEMENTED_NOT_RUNTIME_VERIFIED",
        "MISSING_DECISION",
        "EXTERNAL_NOT_RUN",
        "ONLINE_PACKAGE_CONTRACT",
        "NO-GO",
    ):
        assert value in audit
    assert "真实 Windows E2E：PASS" not in audit
    assert "Phase 3～6：完成" not in audit
