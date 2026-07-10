from pathlib import Path

import pytest

from paper_radar.config import ConfigError, FeedConfig, TopicConfig, load_feeds, load_topics


def write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_configuration_models_are_immutable_and_slotted() -> None:
    feed = FeedConfig("feed", "Feed", "nature", "https://example.com/feed.xml")
    topic = TopicConfig("saw", "SAW", ("SAW",))

    assert feed.enabled is True
    assert feed.aliases == ()
    assert not hasattr(feed, "__dict__")
    assert not hasattr(topic, "__dict__")
    with pytest.raises(AttributeError):
        feed.name = "Changed"  # type: ignore[misc]


def test_duplicate_feed_ids_are_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
feeds:
  - id: apl
    name: Applied Physics Letters
    publisher: aip
    feed_url: https://example.com/apl.xml
  - id: apl
    name: Another Feed
    publisher: ieee
    feed_url: https://example.com/another.xml
""",
    )

    with pytest.raises(ConfigError, match="^duplicate feed id: apl$"):
        load_feeds(path)


def test_valid_https_ieee_feed_loads_with_enabled_defaulting_to_true(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
feeds:
  - id: ieee-test
    name: IEEE Test Feed
    publisher: ieee
    feed_url: https://ieeexplore.ieee.org/rss/TOC1.XML
    aliases:
      - Former Name
      - Legacy Name
""",
    )

    feeds = load_feeds(path)

    assert len(feeds) == 1
    assert feeds[0].id == "ieee-test"
    assert feeds[0].enabled is True
    assert feeds[0].aliases == ("Former Name", "Legacy Name")


def test_topic_without_keywords_is_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topics:
  - id: saw
    label: SAW
    keywords: []
""",
    )

    with pytest.raises(ConfigError, match="^topic saw must define keywords$"):
        load_topics(path)


@pytest.mark.parametrize("loader, root_key", [(load_feeds, "feeds"), (load_topics, "topics")])
def test_configuration_root_must_be_a_mapping(loader, root_key: str, tmp_path: Path) -> None:
    path = write_yaml(tmp_path, f"- {root_key}\n")

    with pytest.raises(ConfigError, match="configuration root must be a mapping"):
        loader(path)


@pytest.mark.parametrize("loader, root_key", [(load_feeds, "feeds"), (load_topics, "topics")])
def test_configuration_lists_must_be_nonempty(loader, root_key: str, tmp_path: Path) -> None:
    path = write_yaml(tmp_path, f"{root_key}: []\n")

    with pytest.raises(ConfigError, match=f"{root_key} must be a non-empty list"):
        loader(path)


def test_feed_rows_must_be_mappings(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "feeds:\n  - not-a-mapping\n")

    with pytest.raises(ConfigError, match="feed row 1 must be a mapping"):
        load_feeds(path)


def test_feed_url_must_be_unique_and_https(tmp_path: Path) -> None:
    insecure = write_yaml(
        tmp_path,
        """
feeds:
  - id: insecure
    name: Insecure Feed
    publisher: nature
    feed_url: http://example.com/feed.xml
""",
    )
    with pytest.raises(ConfigError, match="feed insecure must use an HTTPS URL"):
        load_feeds(insecure)

    duplicate = write_yaml(
        tmp_path,
        """
feeds:
  - id: first
    name: First Feed
    publisher: nature
    feed_url: https://example.com/feed.xml
  - id: second
    name: Second Feed
    publisher: wiley
    feed_url: https://example.com/feed.xml
""",
    )
    with pytest.raises(ConfigError, match="duplicate feed URL: https://example.com/feed.xml"):
        load_feeds(duplicate)


def test_unknown_publisher_is_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
feeds:
  - id: unknown
    name: Unknown Publisher
    publisher: example
    feed_url: https://example.com/feed.xml
""",
    )

    with pytest.raises(ConfigError, match="unknown publisher for feed unknown: example"):
        load_feeds(path)


def test_topic_requires_at_least_one_nonblank_keyword(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topics:
  - id: saw
    label: SAW
    keywords:
      - "  "
""",
    )

    with pytest.raises(ConfigError, match="^topic saw must define keywords$"):
        load_topics(path)


def test_duplicate_topic_ids_are_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topics:
  - id: saw
    label: SAW
    keywords: [SAW]
  - id: saw
    label: Surface acoustic wave
    keywords: [surface acoustic wave]
""",
    )

    with pytest.raises(ConfigError, match="^duplicate topic id: saw$"):
        load_topics(path)


def test_yaml_parse_errors_are_wrapped(tmp_path: Path) -> None:
    path = write_yaml(tmp_path, "feeds: [unterminated\n")

    with pytest.raises(ConfigError, match="could not read YAML configuration"):
        load_feeds(path)


def test_seed_configuration_contains_only_approved_feeds() -> None:
    expected_ids = [
        "nature-communications",
        "nature-biotechnology",
        "nature-methods",
        "nature",
        "nature-cancer",
        "nature-machine-intelligence",
        "nature-computational-science",
        "nature-reviews-molecular-cell-biology",
        "nature-reviews-genetics",
        "nature-reviews-cancer",
        "microsystems-nanoengineering",
        "applied-physics-letters",
        "ieee-transactions-ultrasonics",
        "ieee-transactions-mtt",
        "ieee-microwave-wireless-technology-letters",
        "ieee-transactions-electron-devices",
        "ieee-electron-device-letters",
        "journal-microelectromechanical-systems",
        "advanced-materials",
        "advanced-functional-materials",
    ]

    feeds = load_feeds(Path("feeds.yml"))

    assert [feed.id for feed in feeds] == expected_ids


def test_seed_configuration_contains_only_approved_topics() -> None:
    expected_ids = [
        "baw",
        "saw",
        "fbar",
        "mems",
        "aln",
        "alscn",
        "piezoelectric",
        "ultrasound",
        "acoustic-resonator",
        "microwave",
        "rf",
        "ferroelectric",
        "semiconductor",
        "electron-device",
    ]

    topics = load_topics(Path("topics.yml"))

    assert [topic.id for topic in topics] == expected_ids
