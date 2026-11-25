"""Fixture feed documents for parser and fetcher tests."""

RSS_TWO_ITEMS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <link>https://example.com</link>
    <item>
      <title>First Post</title>
      <link>https://example.com/first</link>
      <guid>guid-1</guid>
      <pubDate>Sat, 22 Nov 2025 10:00:00 GMT</pubDate>
      <description>First summary</description>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/second</link>
      <pubDate>Sun, 23 Nov 2025 11:30:00 GMT</pubDate>
      <description>Second summary</description>
    </item>
  </channel>
</rss>
"""

RSS_ENTRY_WITHOUT_ID_OR_LINK = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>T</title>
    <item><title>Orphan</title><description>no id, no link</description></item>
  </channel>
</rss>
"""

RSS_NO_DATE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>T</title>
    <item><title>Undated</title><link>https://e.com/x</link><guid>g</guid></item>
  </channel>
</rss>
"""

MALFORMED = b"<rss><channel><title>broken"

NOT_A_FEED = b"<html><body><p>hello</p></body></html>"

ATOM_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <entry>
    <title>Atom Post</title>
    <id>atom-1</id>
    <link href="https://example.com/atom-1"/>
    <updated>2025-11-22T10:00:00Z</updated>
    <summary>Atom summary</summary>
  </entry>
</feed>
"""

RSS_ONE_NEW_ITEM = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <link>https://example.com</link>
    <item>
      <title>First Post</title>
      <link>https://example.com/first</link>
      <guid>guid-1</guid>
      <pubDate>Sat, 22 Nov 2025 10:00:00 GMT</pubDate>
      <description>First summary</description>
    </item>
    <item>
      <title>Third Post</title>
      <link>https://example.com/third</link>
      <guid>guid-3</guid>
      <pubDate>Mon, 24 Nov 2025 09:00:00 GMT</pubDate>
      <description>Third summary</description>
    </item>
  </channel>
</rss>
"""

ARTICLE_HTML = b"""<!DOCTYPE html>
<html>
<head><title>LLM breakthrough</title><script>var x = 1;</script>
<style>.nav { color: red; }</style></head>
<body>
<nav><ul><li>Home</li><li>About</li><li>Contact</li></ul></nav>
<header><div class="ad">Buy now!</div></header>
<article>
<h1>New LLM breakthrough</h1>
<p>A new model for code generation was released today. It scores highly on
benchmarks for Python, TypeScript, and Rust, with improvements across
every major evaluation suite used by the research community.</p>
<h2>What changed</h2>
<ul><li>Faster inference</li><li>Longer context</li></ul>
<p>Researchers say the gains come from better data curation and a new
training objective that rewards careful reasoning over memorization.</p>
</article>
<footer><p>Copyright 2025. All rights reserved.</p></footer>
</body>
</html>
"""

NEAR_EMPTY_HTML = b"""<!DOCTYPE html>
<html><head><title>Paywall</title></head>
<body><nav>Home</nav><p>Subscribe to continue reading.</p></body></html>
"""

PDF_BYTES = b"%PDF-1.4 fake pdf content, not html at all"
