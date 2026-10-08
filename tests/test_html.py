from fixagent.html import html_to_text


def test_html_to_text_strips_script_and_style_blocks():
    html = "<p>Keep</p><script>drop()</script><STYLE>.x{}</STYLE><p>this</p>"
    assert html_to_text(html) == "Keep this"


def test_html_to_text_reads_only_first_300kb():
    assert html_to_text("a" * 300_000 + " tail") == "a" * 300_000


def test_html_to_text_many_unclosed_scripts_finish_fast():
    import time
    start = time.perf_counter()
    html_to_text('<script type="application/ld+json">{"@type": "Thing"}' * 4000)
    html_to_text("<" * 300_000)
    assert time.perf_counter() - start < 0.5

