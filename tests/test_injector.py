"""Tests for the bug injector module."""

import pytest

from bugs.injector import (
    Injector,
    MutationType,
    apply_mutation,
    apply_mutation_by_id,
    list_mutation_sites,
)


class TestInjector:
    """Test the Injector class."""

    @pytest.fixture
    def injector(self):
        return Injector()

    def test_parse_simple_code(self, injector):
        """Test that parser works on simple code."""
        code = "x = 1"
        root = injector.parse(code)
        assert root is not None
        assert root.type == "module"

    def test_list_comparison_lt_gt(self, injector):
        """Test finding < and > operators."""
        code = "if x < y: pass"
        sites = injector.list_mutation_sites(code)
        lt_sites = [
            s for s in sites if s.mutation_type == MutationType.COMPARISON_LT_GT
        ]
        assert len(lt_sites) == 1
        assert lt_sites[0].original_text == "<"
        assert lt_sites[0].mutated_text == ">"

    def test_list_comparison_le_ge(self, injector):
        """Test finding <= and >= operators."""
        code = "if x <= y: pass"
        sites = injector.list_mutation_sites(code)
        le_sites = [
            s for s in sites if s.mutation_type == MutationType.COMPARISON_LE_GE
        ]
        assert len(le_sites) == 1
        assert le_sites[0].original_text == "<="
        assert le_sites[0].mutated_text == ">="

    def test_list_comparison_eq_ne(self, injector):
        """Test finding == and != operators."""
        code = "if x == y: pass"
        sites = injector.list_mutation_sites(code)
        eq_sites = [
            s for s in sites if s.mutation_type == MutationType.COMPARISON_EQ_NE
        ]
        assert len(eq_sites) == 1
        assert eq_sites[0].original_text == "=="
        assert eq_sites[0].mutated_text == "!="

    def test_list_boolean_and_or(self, injector):
        """Test finding and/or operators."""
        code = "if x and y: pass"
        sites = injector.list_mutation_sites(code)
        bool_sites = [
            s for s in sites if s.mutation_type == MutationType.BOOLEAN_AND_OR
        ]
        assert len(bool_sites) == 1
        assert bool_sites[0].original_text == "and"
        assert bool_sites[0].mutated_text == "or"

    def test_list_boundary_mutations(self, injector):
        """Test finding integer literals for boundary mutations."""
        code = "x = 5"
        sites = injector.list_mutation_sites(code)
        plus_sites = [
            s for s in sites if s.mutation_type == MutationType.BOUNDARY_PLUS_ONE
        ]
        minus_sites = [
            s for s in sites if s.mutation_type == MutationType.BOUNDARY_MINUS_ONE
        ]
        assert len(plus_sites) == 1
        assert plus_sites[0].mutated_text == "6"
        assert len(minus_sites) == 1
        assert minus_sites[0].mutated_text == "4"

    def test_apply_mutation_comparison(self, injector):
        """Test applying a comparison mutation."""
        code = "if x < y: pass"
        sites = injector.list_mutation_sites(code)
        lt_site = next(
            s for s in sites if s.mutation_type == MutationType.COMPARISON_LT_GT
        )
        mutated = injector.apply_mutation(code, lt_site)
        assert mutated == "if x > y: pass"

    def test_apply_mutation_boolean(self, injector):
        """Test applying a boolean mutation."""
        code = "if a and b: pass"
        sites = injector.list_mutation_sites(code)
        bool_site = next(
            s for s in sites if s.mutation_type == MutationType.BOOLEAN_AND_OR
        )
        mutated = injector.apply_mutation(code, bool_site)
        assert mutated == "if a or b: pass"

    def test_apply_mutation_boundary(self, injector):
        """Test applying a boundary mutation."""
        code = "for i in range(10): pass"
        sites = injector.list_mutation_sites(code)
        plus_site = next(
            s for s in sites if s.mutation_type == MutationType.BOUNDARY_PLUS_ONE
        )
        mutated = injector.apply_mutation(code, plus_site)
        assert mutated == "for i in range(11): pass"

    def test_mutation_site_id_unique(self, injector):
        """Test that mutation site IDs are unique."""
        code = "if x < y and y > z: pass"
        sites = injector.list_mutation_sites(code)
        ids = [s.site_id for s in sites]
        assert len(ids) == len(set(ids))

    def test_apply_mutation_by_id(self, injector):
        """Test applying mutation by ID."""
        code = "if x < y: pass"
        result = injector.apply_mutation_by_id(code, 0)
        assert result is not None
        mutated_code, site = result
        assert site.site_id == 0

    def test_apply_mutation_by_id_not_found(self, injector):
        """Test applying mutation with invalid ID returns None."""
        code = "x = 1"
        result = injector.apply_mutation_by_id(code, 9999)
        assert result is None

    def test_get_mutations_by_type(self, injector):
        """Test filtering mutations by type."""
        code = "if x < y and z == 0: pass"
        bool_sites = injector.get_mutations_by_type(code, MutationType.BOOLEAN_AND_OR)
        assert len(bool_sites) == 1
        assert all(s.mutation_type == MutationType.BOOLEAN_AND_OR for s in bool_sites)

    def test_multiline_code(self, injector):
        """Test mutation detection in multiline code."""
        code = """
def foo(x):
    if x < 10:
        return True
    return False
"""
        sites = injector.list_mutation_sites(code)
        assert len(sites) > 0
        # Should find < operator and integer 10
        types = {s.mutation_type for s in sites}
        assert MutationType.COMPARISON_LT_GT in types
        assert MutationType.BOUNDARY_PLUS_ONE in types

    def test_context_captured(self, injector):
        """Test that context is captured for mutation sites."""
        code = "if x < y: pass"
        sites = injector.list_mutation_sites(code)
        assert len(sites) > 0
        assert sites[0].context != ""

    def test_if_else_swap(self, injector):
        """Test if/else swap mutation."""
        code = '''def check(x):
    if x > 0:
        return "positive"
    else:
        return "negative"
'''
        sites = injector.list_mutation_sites(code)
        if_else_sites = [s for s in sites if s.mutation_type == MutationType.IF_ELSE_SWAP]
        assert len(if_else_sites) == 1

        # Apply mutation and verify swap
        mutated = injector.apply_mutation(code, if_else_sites[0])
        assert '"negative"' in mutated.split("if x > 0:")[1].split("else:")[0]
        assert '"positive"' in mutated.split("else:")[1]

    def test_if_else_swap_skips_elif(self, injector):
        """Test that if/elif/else chains don't create swap mutations."""
        code = '''def check(x):
    if x > 0:
        return "positive"
    elif x < 0:
        return "negative"
    else:
        return "zero"
'''
        sites = injector.list_mutation_sites(code)
        if_else_sites = [s for s in sites if s.mutation_type == MutationType.IF_ELSE_SWAP]
        assert len(if_else_sites) == 0

    def test_if_else_swap_skips_identical_bodies(self, injector):
        """Test that identical if/else bodies don't create swap mutations."""
        code = '''def check(x):
    if x > 0:
        return "same"
    else:
        return "same"
'''
        sites = injector.list_mutation_sites(code)
        if_else_sites = [s for s in sites if s.mutation_type == MutationType.IF_ELSE_SWAP]
        assert len(if_else_sites) == 0


class TestModuleFunctions:
    """Test module-level convenience functions."""

    def test_list_mutation_sites_function(self):
        """Test the module-level list_mutation_sites function."""
        code = "if x < y: pass"
        sites = list_mutation_sites(code)
        assert len(sites) > 0

    def test_apply_mutation_function(self):
        """Test the module-level apply_mutation function."""
        code = "if x < y: pass"
        sites = list_mutation_sites(code)
        mutated = apply_mutation(code, sites[0])
        assert mutated != code

    def test_apply_mutation_by_id_function(self):
        """Test the module-level apply_mutation_by_id function."""
        code = "if x < y: pass"
        result = apply_mutation_by_id(code, 0)
        assert result is not None
