import unittest

from tails_cloner.remote_index import (
    RemoteIndexError,
    RemoteVersionIndex,
    apply_latest_release_metadata,
    build_version_assets,
    parse_directory_listing,
    parse_gitlab_tags_document,
    parse_latest_release_document,
)


class FakeTextFetcher:
    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, timeout_seconds: int) -> str:
        self.calls.append((url, timeout_seconds))
        if url not in self.responses:
            raise RuntimeError(f"unexpected text url: {url}")
        return self.responses[url]


class FakeJsonFetcher:
    def __init__(self, responses: dict[str, object] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, timeout_seconds: int) -> object:
        self.calls.append((url, timeout_seconds))
        if url not in self.responses:
            raise RuntimeError(f"unexpected json url: {url}")
        return self.responses[url]


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

    def test_parse_latest_release_document_extracts_version_and_optional_urls(self) -> None:
        metadata = parse_latest_release_document(
            {
                "version": "7.6.1",
                "iso": {"url": "https://example.invalid/tails-amd64-7.6.1.iso"},
                "img": {"url": "https://example.invalid/tails-amd64-7.6.1.img"},
            }
        )

        self.assertEqual(metadata.version, "7.6.1")
        self.assertEqual(metadata.iso_url, "https://example.invalid/tails-amd64-7.6.1.iso")
        self.assertEqual(metadata.img_url, "https://example.invalid/tails-amd64-7.6.1.img")

    def test_parse_gitlab_tags_document_filters_out_prereleases(self) -> None:
        versions = parse_gitlab_tags_document(
            [
                {"name": "7.6.1"},
                {"name": "7.6"},
                {"name": "7.7-rc1"},
                {"name": "stable"},
            ]
        )

        self.assertEqual(versions, ["7.6.1", "7.6"])

    def test_apply_latest_release_metadata_prefers_metadata_urls(self) -> None:
        assets = apply_latest_release_metadata(
            "https://download.example/stable/",
            parse_latest_release_document(
                {
                    "version": "7.6.1",
                    "iso": {"url": "https://meta.invalid/7.6.1.iso"},
                    "img": {"url": "https://meta.invalid/7.6.1.img"},
                }
            ),
        )

        self.assertEqual(assets.iso_url, "https://meta.invalid/7.6.1.iso")
        self.assertEqual(assets.img_url, "https://meta.invalid/7.6.1.img")
        self.assertEqual(
            assets.sig_url,
            "https://download.example/stable/7.6.1/tails-amd64-7.6.1.iso.sig",
        )

    def test_fetch_versions_merges_latest_tags_and_directory_listing(self) -> None:
        latest_release_url = "https://tails.example/latest.json"
        tags_api_url = "https://tails.example/tags"
        base_url = "https://download.example/stable/"
        json_fetcher = FakeJsonFetcher(
            {
                latest_release_url: {
                    "version": "7.6.1",
                    "iso": {"url": "https://cdn.example/7.6.1.iso"},
                    "img": {"url": "https://cdn.example/7.6.1.img"},
                },
                tags_api_url: [{"name": "7.6"}, {"name": "7.5.1"}, {"name": "7.7-rc1"}],
            }
        )
        text_fetcher = FakeTextFetcher({base_url: '<a href="7.5/">7.5/</a><a href="7.6/">7.6/</a>'})
        index = RemoteVersionIndex(
            base_url=base_url,
            latest_release_url=latest_release_url,
            tags_api_url=tags_api_url,
            fetch_json=json_fetcher,
            fetch_text=text_fetcher,
        )

        entries = index.fetch_versions()

        self.assertEqual([entry.version for entry in entries], ["7.6.1", "7.6", "7.5.1", "7.5"])
        self.assertEqual(entries[0].iso_url, "https://cdn.example/7.6.1.iso")
        self.assertEqual(json_fetcher.calls, [(latest_release_url, 15), (tags_api_url, 15)])
        self.assertEqual(text_fetcher.calls, [(base_url, 15)])

    def test_fetch_versions_falls_back_to_directory_listing_when_json_sources_fail(self) -> None:
        base_url = "https://download.example/stable/"

        def failing_json_fetcher(url: str, timeout_seconds: int) -> object:
            raise RuntimeError(f"boom from {url}")

        text_fetcher = FakeTextFetcher({base_url: '<a href="6.12/">6.12/</a>'})
        index = RemoteVersionIndex(
            base_url=base_url,
            latest_release_url="https://tails.example/latest.json",
            tags_api_url="https://tails.example/tags",
            fetch_json=failing_json_fetcher,
            fetch_text=text_fetcher,
        )

        entries = index.fetch_versions()

        self.assertEqual([entry.version for entry in entries], ["6.12"])
        self.assertEqual(
            entries[0].iso_url,
            "https://download.example/stable/6.12/tails-amd64-6.12.iso",
        )

    def test_fetch_versions_raises_when_all_sources_fail(self) -> None:
        base_url = "https://download.example/stable/"

        def failing_json_fetcher(url: str, timeout_seconds: int) -> object:
            raise RuntimeError("json unavailable")

        def failing_text_fetcher(url: str, timeout_seconds: int) -> str:
            raise RuntimeError("html unavailable")

        index = RemoteVersionIndex(
            base_url=base_url,
            latest_release_url="https://tails.example/latest.json",
            tags_api_url="https://tails.example/tags",
            fetch_json=failing_json_fetcher,
            fetch_text=failing_text_fetcher,
        )

        with self.assertRaises(RemoteIndexError):
            index.fetch_versions()

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
