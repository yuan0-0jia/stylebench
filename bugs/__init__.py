"""Bug injection and catalog generation for StyleBench."""

from .catalog import BugCatalog, BugEntry, CatalogGenerator, generate_catalog
from .injector import (
    Injector,
    MutationSite,
    MutationType,
    apply_mutation,
    apply_mutation_by_id,
    list_mutation_sites,
)
from .repo_config import REPO_CONFIGS, RepoConfig, get_config
from .validator import ValidationReport, Validator, validate_mutations

__all__ = [
    # Injector
    "Injector",
    "MutationSite",
    "MutationType",
    "list_mutation_sites",
    "apply_mutation",
    "apply_mutation_by_id",
    # Validator
    "Validator",
    "ValidationReport",
    "validate_mutations",
    # Catalog
    "CatalogGenerator",
    "BugCatalog",
    "BugEntry",
    "generate_catalog",
    # Config
    "RepoConfig",
    "REPO_CONFIGS",
    "get_config",
]
