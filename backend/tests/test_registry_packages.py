"""Keep declared analysis methods aligned with the runtime image."""

from pathlib import Path

import yaml

from app.services.registry import validate_contested_ensemble


def test_registry_r_packages_are_installed():
    repository_root = Path(__file__).resolve().parents[2]
    registry = yaml.safe_load((repository_root / "registry" / "decision_points.yaml").read_text())
    recipes = yaml.safe_load((repository_root / "registry" / "recipes.yaml").read_text())
    package_manifest = (repository_root / "backend" / "r-package-list.R").read_text()

    required_packages = {
        method["r_package"]
        for decision in registry["decision_points"].values()
        for method in decision.get("default_ensemble", [])
        if method.get("r_package")
    }
    required_packages.update(
        package
        for recipe in recipes["recipes"].values()
        for package in recipe.get("r_packages", [])
    )

    missing = sorted(package for package in required_packages if f'"{package}"' not in package_manifest)
    assert not missing, f"Registry methods require packages absent from r-package-list.R: {missing}"


def test_contested_method_contract_rejects_single_method():
    errors = validate_contested_ensemble(
        "differential_abundance",
        [{"id": "ancombc", "name": "ANCOM-BC2"}],
    )

    assert errors
    assert "at least 2 methods" in errors[0]


def test_contested_method_contract_accepts_registered_ensemble():
    errors = validate_contested_ensemble(
        "differential_abundance",
        [
            {"id": "ancombc", "name": "ANCOM-BC2"},
            {"id": "aldex2", "name": "ALDEx2"},
        ],
    )

    assert errors == []
