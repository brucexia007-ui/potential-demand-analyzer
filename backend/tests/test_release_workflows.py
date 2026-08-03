from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


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
