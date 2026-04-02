import unittest

from tails_cloner.remote_index import (
    build_version_assets,
    parse_directory_listing,
    RemoteVersionIndex,
)


class FakeFetcher:
    def __init__(self, html: str):
        self.html = html
        self.calls = []

    def __call__(self, url: str, timeout_seconds: int) -> str:
        self.calls.append((url, timeout_seconds))
        return self.html


class RemoteIndexTests(unittest.TestCase):
    def test_parse_directory_listing_extracts_and_sorts_versions(self) -> None:
        html = """
        <html>
          <a href="6.12/">6.12/</a>
          <a href="6.11/">6.11/</a>
          <a href="latest/">latest/</a>
          <a href="6.9/">6.9/</a>
        </html>
        """

        self.assertEqual(parse_directory_listing(html), ["6.12", "6.11", "6.9"])

    def test_fetch_versions_uses_fetcher_and_returns_entries(self) -> None:
        fetcher = FakeFetcher('<a href="6.12/">6.12/</a>')
        index = RemoteVersionIndex(base_url="https://download.example/stable/", fetch_text=fetcher)

        entries = index.fetch_versions()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].version, "6.12")
        self.assertEqual(
            entries[0].iso_url,
            "https://download.example/stable/6.12/tails-amd64-6.12.iso",
        )
        self.assertEqual(fetcher.calls, [("https://download.example/stable/", 15)])

    def test_build_version_assets_constructs_expected_urls(self) -> None:
        assets = build_version_assets("https://download.example/stable/", "6.12")

        self.assertEqual(assets.iso_url, "https://download.example/stable/6.12/tails-amd64-6.12.iso")
        self.assertEqual(assets.sig_url, "https://download.example/stable/6.12/tails-amd64-6.12.iso.sig")
        self.assertEqual(
            assets.sha256_url,
            "https://download.example/stable/6.12/tails-amd64-6.12.img.sha256",
        )


if __name__ == "__main__":
    unittest.main()
