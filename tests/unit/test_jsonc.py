import pytest

from cursor_portability.planning.jsonc import (
    JsoncError,
    insert_array_entries,
    insert_object_entries,
    parse_jsonc,
    replace_object_values,
)


def test_parse_with_comments_and_trailing_comma() -> None:
    text = """{
  // font
  "editor.fontSize": 14,
  "x": 1,
}"""
    doc = parse_jsonc(text)
    assert doc.value == {"editor.fontSize": 14, "x": 1}


def test_string_with_slashes() -> None:
    text = '{"url": "https://example.com"}'
    doc = parse_jsonc(text)
    assert doc.value["url"].startswith("https")


def test_trailing_comma_inside_string_is_kept() -> None:
    assert parse_jsonc('{"a": ",}", "b": [",]"]}').value == {"a": ",}", "b": [",]"]}


def test_insert_preserves_comment_outside_insert() -> None:
    text = '{\n  // keep\n  "a": 1\n}'
    doc = parse_jsonc(text)
    merged = insert_object_entries(doc, {"b": 2})
    assert "// keep" in merged
    assert '"b": 2' in merged
    again = parse_jsonc(merged)
    assert again.value == {"a": 1, "b": 2}


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        "{\n}",
        "{ // empty\n}",
        '{"a": 1}',
        '{\n  "a": 1,\n}',
        '{\n  "a": 1 // trailing\n}',
        '{\n  "a": 1 /* block */\n}',
        '{\n  // }\n  "a": {"nested": [1, 2]}\n}',
    ],
)
def test_insert_object_entries_produces_valid_jsonc(text: str) -> None:
    doc = parse_jsonc(text)
    merged = insert_object_entries(doc, {'quote"key': "v"})
    assert parse_jsonc(merged).value == {**doc.value, 'quote"key': "v"}
    for comment in ("// empty", "// trailing", "/* block */", "// }"):
        if comment in text:
            assert comment in merged


@pytest.mark.parametrize("text", ["[]", "[\n]", '[\n  {"key": "a"},\n]', '[{"key": "a"}]'])
def test_insert_array_entries_produces_valid_jsonc(text: str) -> None:
    doc = parse_jsonc(text)
    merged = insert_array_entries(doc, [{"key": "b"}])
    assert parse_jsonc(merged).value == [*doc.value, {"key": "b"}]


def test_replace_value_in_place_keeps_comments_and_key_count() -> None:
    text = '{\n  // size\n  "editor.fontSize": 12, // inline\n  "x": {"a": [1, 2]},\n  "y": null\n}'
    doc = parse_jsonc(text)
    merged = replace_object_values(doc, {"editor.fontSize": 16, "x": None, "y": [1]})
    assert parse_jsonc(merged).value == {"editor.fontSize": 16, "x": None, "y": [1]}
    assert merged.count('"editor.fontSize"') == 1
    assert "// size" in merged
    assert "// inline" in merged


def test_replace_missing_key_fails() -> None:
    with pytest.raises(JsoncError):
        replace_object_values(parse_jsonc('{"a": 1}'), {"b": 2})


def test_duplicate_keys_fail() -> None:
    with pytest.raises(JsoncError):
        parse_jsonc('{"a":1,"a":2}')


@pytest.mark.parametrize("text", ["1", '"s"', '{"a": 1} 2', '{"a": 1 /* open', '{"a": / 1}'])
def test_invalid_documents_fail(text: str) -> None:
    with pytest.raises(JsoncError):
        parse_jsonc(text)
