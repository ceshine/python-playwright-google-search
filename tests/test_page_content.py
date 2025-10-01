"""Tests for HTML to Markdown conversion helpers."""

from playwright_google_search.page_content import convert_html_to_markdown


def test_convert_html_to_markdown_converts_basic_structure() -> None:
    html = """
    <html>
        <head>
            <title>Sample Page</title>
        </head>
        <body>
            <h1>Hello</h1>
            <p>World</p>
            <p><a href=\"https://example.com\">Example Link</a></p>
        </body>
    </html>
    """

    markdown = convert_html_to_markdown(html=html, url="https://example.com")

    assert "Hello" in markdown
    assert "World" in markdown
    assert "[Example Link](https://example.com)" in markdown
