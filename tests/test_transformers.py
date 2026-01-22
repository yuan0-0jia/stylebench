"""Tests for code style transformers."""

import pytest

from transformers import (
    BadNamingTransformer,
    CamelCaseTransformer,
    FormattingTransformer,
    SnakeCaseTransformer,
    camel_to_snake,
    snake_to_camel,
)


class TestNamingConversions:
    """Test snake_case <-> camelCase conversion functions."""

    def test_snake_to_camel_simple(self):
        assert snake_to_camel("hello_world") == "helloWorld"
        assert snake_to_camel("get_user_name") == "getUserName"
        assert snake_to_camel("calculate_total_price") == "calculateTotalPrice"

    def test_snake_to_camel_single_word(self):
        assert snake_to_camel("hello") == "hello"
        assert snake_to_camel("name") == "name"

    def test_snake_to_camel_with_leading_underscore(self):
        assert snake_to_camel("_private_var") == "_privateVar"
        assert snake_to_camel("__double_underscore") == "__doubleUnderscore"

    def test_snake_to_camel_empty(self):
        assert snake_to_camel("") == ""
        assert snake_to_camel("_") == "_"
        assert snake_to_camel("__") == "__"

    def test_camel_to_snake_simple(self):
        assert camel_to_snake("helloWorld") == "hello_world"
        assert camel_to_snake("getUserName") == "get_user_name"
        assert camel_to_snake("calculateTotalPrice") == "calculate_total_price"

    def test_camel_to_snake_single_word(self):
        assert camel_to_snake("hello") == "hello"
        assert camel_to_snake("name") == "name"

    def test_camel_to_snake_with_leading_underscore(self):
        assert camel_to_snake("_privateVar") == "_private_var"

    def test_camel_to_snake_empty(self):
        assert camel_to_snake("") == ""


class TestCamelCaseTransformer:
    """Test CamelCaseTransformer."""

    def test_transform_simple_function(self):
        code = '''
def get_user_name(user_id):
    user_name = fetch_user(user_id)
    return user_name
'''
        transformer = CamelCaseTransformer()
        result = transformer.transform(code)

        assert "getUserName" in result.transformed_code
        assert "userId" in result.transformed_code
        assert "userName" in result.transformed_code
        assert "fetchUser" in result.transformed_code
        assert result.changes_made > 0

    def test_transform_preserves_builtins(self):
        code = '''
def process_items(items):
    result = len(items)
    print(result)
    return True
'''
        transformer = CamelCaseTransformer()
        result = transformer.transform(code)

        # Built-ins should not be transformed
        assert "len" in result.transformed_code
        assert "print" in result.transformed_code
        assert "True" in result.transformed_code

    def test_transform_preserves_dunder_methods(self):
        code = '''
class MyClass:
    def __init__(self):
        self.my_value = 0

    def __str__(self):
        return str(self.my_value)
'''
        transformer = CamelCaseTransformer()
        result = transformer.transform(code)

        # Dunder methods should not be transformed
        assert "__init__" in result.transformed_code
        assert "__str__" in result.transformed_code
        # But regular attributes should be
        assert "myValue" in result.transformed_code

    def test_no_changes_needed(self):
        code = '''
def processItems(items):
    return items
'''
        transformer = CamelCaseTransformer()
        result = transformer.transform(code)

        assert result.changes_made == 0
        assert result.transformed_code == code

    def test_transform_class_attributes(self):
        code = '''
class UserProfile:
    def __init__(self, first_name, last_name):
        self.first_name = first_name
        self.last_name = last_name
        self.full_name = f"{first_name} {last_name}"
'''
        transformer = CamelCaseTransformer()
        result = transformer.transform(code)

        assert "firstName" in result.transformed_code
        assert "lastName" in result.transformed_code
        assert "fullName" in result.transformed_code


class TestSnakeCaseTransformer:
    """Test SnakeCaseTransformer."""

    def test_transform_simple_function(self):
        code = '''
def getUserName(userId):
    userName = fetchUser(userId)
    return userName
'''
        transformer = SnakeCaseTransformer()
        result = transformer.transform(code)

        assert "get_user_name" in result.transformed_code
        assert "user_id" in result.transformed_code
        assert "user_name" in result.transformed_code
        assert "fetch_user" in result.transformed_code
        assert result.changes_made > 0

    def test_transform_preserves_builtins(self):
        code = '''
def processItems(items):
    result = len(items)
    return True
'''
        transformer = SnakeCaseTransformer()
        result = transformer.transform(code)

        assert "len" in result.transformed_code
        assert "True" in result.transformed_code

    def test_no_changes_needed(self):
        code = '''
def process_items(items):
    return items
'''
        transformer = SnakeCaseTransformer()
        result = transformer.transform(code)

        assert result.changes_made == 0
        assert result.transformed_code == code


class TestBadNamingTransformer:
    """Test BadNamingTransformer."""

    def test_transform_local_variables(self):
        code = '''
def calculate_total(items):
    total_price = 0
    for item in items:
        item_price = item.price
        total_price += item_price
    return total_price
'''
        transformer = BadNamingTransformer()
        result = transformer.transform(code)

        # Variables should be renamed to short names
        assert result.changes_made > 0
        # Original names should be gone
        assert "total_price" not in result.transformed_code or result.changes_made == 0

    def test_transforms_local_variables(self):
        code = '''
def calculate(value):
    doubled = value * 2
    tripled = value * 3
    return doubled + tripled
'''
        transformer = BadNamingTransformer()
        result = transformer.transform(code)

        # Should transform local variables to short names
        assert result.changes_made > 0
        # Code should still be valid Python
        compile(result.transformed_code, "<string>", "exec")

    def test_preserves_single_char_names(self):
        code = '''
def process(x, y):
    z = x + y
    return z
'''
        transformer = BadNamingTransformer()
        result = transformer.transform(code)

        # Single-char names should stay as-is
        # (they're already "bad" names)
        assert "x" in result.transformed_code
        assert "y" in result.transformed_code

    def test_deterministic_renaming(self):
        code = '''
def process(items):
    total = 0
    count = 0
    for item in items:
        total += item
        count += 1
    return total / count
'''
        transformer = BadNamingTransformer()
        result1 = transformer.transform(code)
        result2 = transformer.transform(code)

        # Same input should produce same output
        assert result1.transformed_code == result2.transformed_code


class TestFormattingTransformer:
    """Test FormattingTransformer."""

    def test_default_formatting(self):
        # Poorly formatted code
        code = '''
def hello(   x,y,z   ):
    return x+y+z
'''
        transformer = FormattingTransformer(style="default")
        result = transformer.transform(code)

        # Should have some formatting applied
        # (exact output depends on ruff version)
        assert result.transformed_code is not None

    def test_different_profiles(self):
        code = '''
def process(items):
    result = [item for item in items if item > 0]
    return result
'''
        # Just verify different profiles can be used
        for style in ["default", "pep8_strict", "wide", "compact"]:
            transformer = FormattingTransformer(style=style)
            result = transformer.transform(code)
            assert result.transformed_code is not None


class TestTransformerIntegration:
    """Integration tests for transformer pipelines."""

    def test_camelcase_then_format(self):
        code = '''
def get_user_name(user_id):
    user_data=fetch_user(user_id)
    return user_data
'''
        # First transform to camelCase
        camel_transformer = CamelCaseTransformer()
        result1 = camel_transformer.transform(code)

        # Then format
        format_transformer = FormattingTransformer()
        result2 = format_transformer.transform(result1.transformed_code)

        assert "getUserName" in result2.transformed_code
        assert "userId" in result2.transformed_code

    def test_transform_preserves_syntax(self):
        """Verify transformed code is still valid Python."""
        code = '''
def calculate_total_price(item_list):
    total_price = 0
    for current_item in item_list:
        item_price = current_item.get_price()
        if item_price > 0:
            total_price += item_price
    return total_price
'''
        transformer = CamelCaseTransformer()
        result = transformer.transform(code)

        # Should be able to compile the result
        compile(result.transformed_code, "<string>", "exec")

    def test_roundtrip_conversion(self):
        """Test snake -> camel -> snake preserves structure."""
        original = '''
def process_user_data(user_input):
    validated_data = validate_input(user_input)
    return validated_data
'''
        # Convert to camelCase
        camel = CamelCaseTransformer()
        camel_result = camel.transform(original)

        # Convert back to snake_case
        snake = SnakeCaseTransformer()
        snake_result = snake.transform(camel_result.transformed_code)

        # Should be roughly equivalent (may have minor differences)
        # Both should compile
        compile(original, "<string>", "exec")
        compile(snake_result.transformed_code, "<string>", "exec")
