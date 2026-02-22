#!/usr/bin/env python3
"""Extend existing bug catalogs to new style variants.

Usage:
    python scripts/extend_catalogs.py --styles nodocstrings nodocs_full
    python scripts/extend_catalogs.py --styles nodocstrings nodocs_full --repos humanize

This script takes the existing validated bugs (from original catalogs) and maps
them to new style variants, validates that the mutations are detected, then saves
new catalog files.  It does NOT regenerate existing catalogs.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bugs.catalog import BugCatalog, BugEntry, HiddenMetadata, run_with_cleanup
from bugs.injector import MutationSite, MutationType
from bugs.mapper import MappedMutation, map_mutation
from bugs.repo_config import get_config

DATA_DIR = Path("/Users/yuan/stylebench-data")
BUGS_DIR = DATA_DIR / "bugs_canonical"

ALL_REPOS = ["humanize", "validators", "python-markdown", "more-itertools"]
TEST_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Reconstruct MutationSite from hidden catalog metadata
# ---------------------------------------------------------------------------


def _reconstruct_site(
    source: str,
    hidden: dict,
    bug_num: int,
) -> MutationSite | None:
    """Reconstruct a MutationSite from hidden catalog metadata.

    Uses line_number + original_text to find the byte offset in the source.
    """
    line_number = hidden["line_number"]  # 1-indexed
    mutation_type_str = hidden["mutation_type"]
    original_text = hidden["original_text"]
    mutated_text = hidden["mutated_text"]
    context = hidden["context"]

    try:
        mtype = MutationType(mutation_type_str)
    except ValueError:
        print(f"  Warning: unknown mutation type {mutation_type_str}")
        return None

    # For RETURN_NONE, the stored original_text has "return " prepended.
    # The actual start_byte points to the return value (after "return ").
    actual_search_text = original_text
    if mtype == MutationType.RETURN_NONE and original_text.startswith("return "):
        actual_search_text = original_text[len("return ") :]

    src_bytes = source.encode("utf-8")
    lines = source.splitlines(keepends=True)

    # Find byte offset of the target line (1-indexed → 0-indexed)
    line_idx = line_number - 1
    if line_idx < 0 or line_idx >= len(lines):
        print(f"  Warning: bug #{bug_num} line {line_number} out of range")
        return None

    search_bytes = actual_search_text.encode("utf-8")

    # Strategy 1: Use context to find the right occurrence of original_text.
    # The context is "~40 chars before" + original_text + "~40 chars after".
    # So the mutation is near the CENTER of the context string (position ~40).
    # Find the occurrence of actual_search_text in the context that is closest
    # to the center, then build a unique anchor from surrounding chars.
    import re as _re

    start_byte = None
    ctx_clean = context.replace("...", "").replace("\n", " ")
    ctx_center = len(ctx_clean) // 2

    # Find all occurrences of actual_search_text in ctx_clean
    search_pat = _re.escape(actual_search_text)
    all_ctx_matches = [m.start() for m in _re.finditer(search_pat, ctx_clean)]
    if all_ctx_matches:
        # Pick occurrence closest to center
        ctx_search_idx = min(all_ctx_matches, key=lambda p: abs(p - ctx_center))
        # Build anchor: up to 15 chars before + actual_search_text + up to 10 chars after
        anchor_before = ctx_clean[max(0, ctx_search_idx - 15) : ctx_search_idx]
        anchor_after = ctx_clean[
            ctx_search_idx + len(actual_search_text) : ctx_search_idx + len(actual_search_text) + 10
        ]
        anchor = (anchor_before + actual_search_text + anchor_after).strip()
        if len(anchor) >= len(actual_search_text) + 3:
            # Search for anchor in source window (with whitespace normalization)
            window_start = max(0, line_idx - 10)
            window_start_byte = sum(len(ln.encode("utf-8")) for ln in lines[:window_start])
            window_end = min(len(lines), line_idx + 15)
            window_end_byte = sum(len(ln.encode("utf-8")) for ln in lines[:window_end])
            orig_window = source[window_start_byte:window_end_byte]
            window_norm = _re.sub(r"[ \n\t]+", " ", orig_window)
            anchor_norm = _re.sub(r"[ \n\t]+", " ", anchor)
            pos = window_norm.find(anchor_norm)
            if pos != -1:
                # Map whitespace-normalized position back to byte offset
                i = 0
                norm_chars = 0
                while i < len(orig_window) and norm_chars < pos:
                    if orig_window[i] in " \n\t":
                        while i < len(orig_window) and orig_window[i] in " \n\t":
                            i += 1
                        norm_chars += 1
                    else:
                        i += 1
                        norm_chars += 1
                # i is start of anchor in orig_window; find actual_search_text within it
                # Use word-boundary match for identifier tokens to avoid hitting
                # substrings (e.g., 'p' in 'map' or 'x' in 'xx_hi').
                sub = orig_window[i:]
                is_identifier = bool(_re.match(r"^[A-Za-z_]\w*$", actual_search_text))
                if is_identifier:
                    wb_match = _re.search(r"\b" + _re.escape(actual_search_text) + r"\b", sub)
                    p = wb_match.start() if wb_match else -1
                else:
                    p = sub.encode("utf-8").find(search_bytes)
                if p != -1:
                    start_byte = window_start_byte + len(orig_window[:i].encode("utf-8")) + p

    # Strategy 2: Line-based search (fallback)
    if start_byte is None:
        # Search in source bytes starting from a window around the target line.
        # Extend window by number of newlines in actual_search_text so multi-line
        # mutations (e.g. if_else_swap) are fully contained in the window.
        num_extra = actual_search_text.count("\n")
        window_start = max(0, line_idx - 5)
        window_start_byte = sum(len(ln.encode("utf-8")) for ln in lines[:window_start])
        window_end = min(len(lines), line_idx + max(10, num_extra + 5))
        window_end_byte = sum(len(ln.encode("utf-8")) for ln in lines[:window_end])

        window_text = src_bytes[window_start_byte:window_end_byte].decode("utf-8", errors="replace")
        is_identifier = bool(_re.match(r"^[A-Za-z_]\w*$", actual_search_text))
        if is_identifier:
            wb_match = _re.search(r"\b" + _re.escape(actual_search_text) + r"\b", window_text)
            pos_in_window = wb_match.start() if wb_match else -1
        else:
            pos_in_window = window_text.find(actual_search_text)

        if pos_in_window == -1:
            trunc = repr(actual_search_text[:40])
            print(f"  Warning: bug #{bug_num} {trunc} not found near line {line_number}")
            return None

        start_byte = window_start_byte + pos_in_window

    end_byte = start_byte + len(search_bytes)

    # Compute start_point and end_point from byte offsets
    text_before = src_bytes[:start_byte].decode("utf-8", errors="replace")
    text_before_end = src_bytes[:end_byte].decode("utf-8", errors="replace")
    start_row = text_before.count("\n")
    start_col = len(text_before) - text_before.rfind("\n") - 1
    end_row = text_before_end.count("\n")
    end_col = len(text_before_end) - text_before_end.rfind("\n") - 1

    return MutationSite(
        site_id=bug_num,
        mutation_type=mtype,
        start_byte=start_byte,
        end_byte=end_byte,
        start_point=(start_row, start_col),
        end_point=(end_row, end_col),
        original_text=actual_search_text,
        mutated_text=mutated_text if mtype != MutationType.RETURN_NONE else actual_search_text,
        context=context,
    )


# ---------------------------------------------------------------------------
# Apply mutations and run tests
# ---------------------------------------------------------------------------


def _apply_mapped_mutation(file_path: Path, mapping: MappedMutation) -> str | None:
    try:
        original = file_path.read_text()
        before = original[: mapping.start_byte]
        after = original[mapping.end_byte :]
        mutated = before + mapping.mutated_text + after
        file_path.write_text(mutated)
        return original
    except Exception:
        return None


def _run_tests_in(repo_path: Path, repo: str) -> tuple[int, str, list[str]]:
    config = get_config(repo)
    test_command = config.get_test_command(repo_path, external=False)
    try:
        result = run_with_cleanup(test_command, cwd=repo_path, timeout=TEST_TIMEOUT)
        output = result.stdout + result.stderr
        failing = []
        for line in output.split("\n"):
            if line.startswith("FAILED "):
                test_name = line.split(" ")[1].split(" -")[0]
                failing.append(test_name)
        return result.returncode, output, failing
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT", []
    except Exception as e:
        return -1, f"ERROR: {e}", []


# ---------------------------------------------------------------------------
# Main extension logic
# ---------------------------------------------------------------------------


def extend_repo(repo: str, new_styles: list[str], verbose: bool = False) -> None:
    """Extend catalog for one repo to new styles."""
    print(f"\n{'=' * 60}")
    print(f"Extending catalog for: {repo}")
    print(f"New styles: {new_styles}")
    print(f"{'=' * 60}")

    # Load existing original catalog
    orig_catalog_path = BUGS_DIR / f"{repo}-original.json"
    if not orig_catalog_path.exists():
        print(f"  ERROR: {orig_catalog_path} not found")
        return

    orig_catalog = BugCatalog.load(orig_catalog_path)
    bugs = orig_catalog.bugs
    hidden = orig_catalog._hidden

    print(f"  Loaded {len(bugs)} existing bugs from original catalog")

    # Create temp copies for validation
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Copy original + new style repos
        all_styles = ["original"] + new_styles
        copies: dict[str, Path] = {}
        for style in all_styles:
            src = DATA_DIR / style / repo
            if not src.exists():
                print(f"  ERROR: {src} not found")
                return
            dst = temp_path / style / repo
            shutil.copytree(
                src,
                dst,
                symlinks=True,
                ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
            )
            copies[style] = dst

        # Run baseline tests
        baselines: dict[str, set[str]] = {}
        for style in all_styles:
            print(f"  Baseline test: {style}...", end=" ", flush=True)
            _, _, bf = _run_tests_in(copies[style], repo)
            baselines[style] = set(bf)
            print(f"{len(bf)} pre-existing failures")

        # Build new catalogs
        config = get_config(repo)
        new_catalogs: dict[str, BugCatalog] = {}
        for style in new_styles:
            new_catalogs[style] = BugCatalog(
                repo=repo,
                style=style,
                generated_at=datetime.now().isoformat(),
                test_command=config.get_test_command(DATA_DIR / style / repo),
            )

        validated_count = 0
        skipped_map = 0
        skipped_validate = 0

        for i, (bug_entry, hidden_meta) in enumerate(zip(bugs, hidden), start=1):
            bug_id_base = f"{repo}-{{style}}-{i:03d}"
            rel_path = hidden_meta.file_path

            # Read original source
            orig_source_path = DATA_DIR / "original" / repo / rel_path
            if not orig_source_path.exists():
                skipped_map += 1
                continue

            orig_source = orig_source_path.read_text()

            # Reconstruct MutationSite
            hidden_dict = {
                "line_number": hidden_meta.line_number,
                "mutation_type": hidden_meta.mutation_type,
                "original_text": hidden_meta.original_text,
                "mutated_text": hidden_meta.mutated_text,
                "context": hidden_meta.context,
            }
            site = _reconstruct_site(orig_source, hidden_dict, i)
            if site is None:
                skipped_map += 1
                if verbose:
                    print(f"  Bug #{i}: Could not reconstruct site")
                continue

            # Map to new styles
            style_mappings: dict[str, MappedMutation] = {}
            all_mapped = True
            for style in new_styles:
                target_path = DATA_DIR / style / repo / rel_path
                if not target_path.exists():
                    all_mapped = False
                    break
                target_source = target_path.read_text()
                result = map_mutation(orig_source, target_source, site, style)
                if result is None:
                    all_mapped = False
                    break
                result.file_path = rel_path
                style_mappings[style] = result

            if not all_mapped:
                skipped_map += 1
                if verbose:
                    print(f"  Bug #{i}: Could not map to all styles")
                continue

            # Validate: apply mutation in each new style, run tests
            all_killed = True
            style_test_results: dict[str, dict] = {}

            for style in new_styles:
                mapping = style_mappings[style]
                file_in_copy = copies[style] / rel_path
                saved = _apply_mapped_mutation(file_in_copy, mapping)
                if saved is None:
                    all_killed = False
                    break

                exit_code, output, failing = _run_tests_in(copies[style], repo)
                file_in_copy.write_text(saved)

                new_failures = [t for t in failing if t not in baselines[style]]
                if exit_code == 0 or not new_failures:
                    all_killed = False
                    if verbose:
                        nf = len(new_failures)
                        print(
                            f"  Bug #{i}: Not killed in {style}"
                            f" (exit={exit_code}, new_failures={nf})"
                        )
                    break

                style_test_results[style] = {
                    "test_output": output,
                    "failing_tests": new_failures,
                    "exit_code": exit_code,
                }

            if not all_killed:
                skipped_validate += 1
                continue

            # Add to new catalogs
            validated_count += 1
            for style in new_styles:
                tr = style_test_results[style]
                mapping = style_mappings[style]
                bug_id = bug_id_base.replace("{style}", style)

                new_catalogs[style].bugs.append(
                    BugEntry(
                        bug_id=bug_id,
                        test_output=tr["test_output"],
                        failing_tests=tr["failing_tests"],
                        exit_code=tr["exit_code"],
                    )
                )
                new_catalogs[style]._hidden.append(
                    HiddenMetadata(
                        bug_id=bug_id,
                        file_path=mapping.file_path,
                        line_number=mapping.line_number,
                        mutation_type=mapping.mutation_type.value,
                        original_text=mapping.original_text,
                        mutated_text=mapping.mutated_text,
                        context=mapping.context,
                    )
                )

            if verbose:
                print(
                    f"  Bug #{i}: Validated"
                    f" ({hidden_meta.mutation_type} in {rel_path}:{hidden_meta.line_number})"
                )

        nm = skipped_map
        nk = skipped_validate
        print(f"\n  Results: {validated_count} validated, {nm} not mapped, {nk} not killed")

    # Save new catalogs
    BUGS_DIR.mkdir(parents=True, exist_ok=True)
    for style, catalog in new_catalogs.items():
        path = BUGS_DIR / f"{repo}-{style}.json"
        catalog.save(path)
        print(f"  Saved {path.name} ({len(catalog.bugs)} bugs)")


def main():
    parser = argparse.ArgumentParser(description="Extend bug catalogs to new style variants")
    parser.add_argument("--styles", nargs="+", required=True, help="New styles to generate")
    parser.add_argument(
        "--repos", nargs="+", default=ALL_REPOS, choices=ALL_REPOS, help="Repos to process"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    for repo in args.repos:
        extend_repo(repo, args.styles, verbose=args.verbose)

    print("\nDone.")


if __name__ == "__main__":
    main()
