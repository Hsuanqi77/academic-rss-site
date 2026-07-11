from pathlib import Path

import pytest

from paper_radar.config import (
    ConfigError,
    FeedConfig,
    TopicCatalog,
    TopicConfig,
    TopicGroupConfig,
    load_feeds,
    load_topic_catalog,
    load_topics,
)
from paper_radar.matching import normalize_match_separators


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_configuration_models_are_immutable_and_slotted() -> None:
    feed = FeedConfig("feed", "Feed", "nature", "https://example.com/feed.xml")
    group = TopicGroupConfig("acoustic-rf", "声学与射频器件", 1)
    topic = TopicConfig("saw", "SAW", ("SAW",), "acoustic-rf")
    catalog = TopicCatalog((group,), (topic,))

    assert feed.enabled is True
    assert feed.aliases == ()
    assert not hasattr(feed, "__dict__")
    assert not hasattr(group, "__dict__")
    assert not hasattr(topic, "__dict__")
    assert not hasattr(catalog, "__dict__")
    with pytest.raises(AttributeError):
        feed.name = "Changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        topic.group = "changed"  # type: ignore[misc]


def test_valid_topic_catalog_loads_once_and_load_topics_is_compatible(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topic_groups:
  - id: acoustic-rf
    label: 声学与射频器件
    order: 1
topics:
  - id: saw
    label: SAW
    group: acoustic-rf
    keywords: [surface acoustic wave, SAW]
""",
    )

    catalog = load_topic_catalog(path)

    assert catalog.groups == (TopicGroupConfig("acoustic-rf", "声学与射频器件", 1),)
    assert catalog.topics == (
        TopicConfig("saw", "SAW", ("surface acoustic wave", "SAW"), "acoustic-rf"),
    )
    assert load_topics(path) == list(catalog.topics)


def test_match_separator_normalization_handles_unicode_and_collapses_spacing() -> None:
    assert normalize_match_separators("  Surface\u2011ACOUSTIC\tWave  ") == "surface acoustic wave"
    assert normalize_match_separators("A\u030A-RF") == "å rf"


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
topic_groups:
  - {id: acoustic-rf, label: 声学与射频器件, order: 1}
topics:
  - id: saw
    label: SAW
    group: acoustic-rf
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


@pytest.mark.parametrize("loader, root_key", [(load_feeds, "feeds")])
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


@pytest.mark.parametrize(
    ("feed_id", "feed_url"),
    [
        ("malformed-port", "https://example.com:not-a-port/feed.xml"),
        ("missing-host", "https://:443/feed.xml"),
        ("invalid-host", "https://-/feed.xml"),
    ],
)
def test_malformed_https_feed_urls_are_rejected(
    feed_id: str, feed_url: str, tmp_path: Path
) -> None:
    path = write_yaml(
        tmp_path,
        f"""
feeds:
  - id: {feed_id}
    name: Broken Feed
    publisher: nature
    feed_url: {feed_url}
""",
    )

    with pytest.raises(ConfigError, match=rf"^feed {feed_id} has invalid feed_url"):
        load_feeds(path)


def test_canonical_feed_urls_must_be_unique(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
feeds:
  - id: first
    name: First Feed
    publisher: nature
    feed_url: https://EXAMPLE.com:443/feed.xml
  - id: second
    name: Second Feed
    publisher: nature
    feed_url: https://example.com/feed.xml
""",
    )

    with pytest.raises(ConfigError, match="duplicate feed URL: https://example.com/feed.xml"):
        load_feeds(path)


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


@pytest.mark.parametrize(
    ("loader", "content", "message"),
    [
        (
            load_feeds,
            """
feeds:
  - id: apl
    name: Applied Physics Letters
    publisher: aip
    feed_url: https://example.com/apl.xml
    enable: false
""",
            "feed apl has unknown fields: enable",
        ),
        (
            load_topics,
            """
topic_groups:
  - {id: acoustic-rf, label: 声学与射频器件, order: 1}
topics:
  - id: saw
    label: SAW
    group: acoustic-rf
    keywords: [SAW]
    keyword: surface acoustic wave
""",
            "topic saw has unknown fields: keyword",
        ),
    ],
)
def test_unknown_row_fields_are_rejected(
    loader, content: str, message: str, tmp_path: Path
) -> None:
    path = write_yaml(tmp_path, content)

    with pytest.raises(ConfigError, match=f"^{message}$"):
        loader(path)


@pytest.mark.parametrize(
    ("loader", "content"),
    [
        (
            load_feeds,
            """
feeds:
  - id: apl
    id: duplicate
    name: Applied Physics Letters
    publisher: aip
    feed_url: https://example.com/apl.xml
""",
        ),
        (
            load_topics,
            """
topic_groups:
  - {id: acoustic-rf, label: 声学与射频器件, order: 1}
topics:
  - id: saw
    id: duplicate
    label: SAW
    group: acoustic-rf
    keywords: [SAW]
""",
        ),
    ],
)
def test_duplicate_yaml_mapping_keys_are_rejected(loader, content: str, tmp_path: Path) -> None:
    path = write_yaml(tmp_path, content)

    with pytest.raises(ConfigError, match="duplicate YAML key: id"):
        loader(path)


def test_topic_requires_at_least_one_nonblank_keyword(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: 声学与射频器件, order: 1}
topics:
  - id: saw
    label: SAW
    group: acoustic-rf
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
topic_groups:
  - {id: acoustic-rf, label: 声学与射频器件, order: 1}
topics:
  - id: saw
    label: SAW
    group: acoustic-rf
    keywords: [SAW]
  - id: saw
    label: Surface acoustic wave
    group: acoustic-rf
    keywords: [surface acoustic wave]
""",
    )

    with pytest.raises(ConfigError, match="^duplicate topic id: saw$"):
        load_topics(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("topic_groups: []\ntopics: [{id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}]", "topic_groups must be a non-empty list"),
        ("topic_groups: [{id: acoustic-rf, label: Acoustic, order: 1}]\ntopics: []", "topics must be a non-empty list"),
    ],
)
def test_topic_catalog_lists_must_be_nonempty(
    content: str, message: str, tmp_path: Path
) -> None:
    path = write_yaml(tmp_path, content)

    with pytest.raises(ConfigError, match=f"^{message}$"):
        load_topic_catalog(path)


@pytest.mark.parametrize(
    ("groups", "message"),
    [
        (
            """
  - {id: acoustic-rf, label: Acoustic, order: 1}
  - {id: acoustic-rf, label: Other, order: 2}
""",
            "duplicate topic group id: acoustic-rf",
        ),
        (
            """
  - {id: acoustic-rf, label: Acoustic, order: 1}
  - {id: piezo-ferroelectric, label: Acoustic, order: 2}
""",
            "duplicate topic group label: Acoustic",
        ),
        (
            """
  - {id: acoustic-rf, label: Acoustic, order: 1}
  - {id: piezo-ferroelectric, label: Piezo, order: 1}
""",
            "duplicate topic group order: 1",
        ),
        (
            """
  - {id: acoustic-rf, label: Acoustic, order: 1}
  - {id: piezo-ferroelectric, label: Piezo, order: 3}
""",
            "topic group order must be continuous from 1 to 2",
        ),
        (
            """
  - {id: acoustic-rf, label: Acoustic, order: 0}
""",
            "topic group acoustic-rf order must be a positive integer",
        ),
    ],
)
def test_topic_group_identity_and_order_rules_are_enforced(
    groups: str, message: str, tmp_path: Path
) -> None:
    path = write_yaml(
        tmp_path,
        f"""topic_groups:{groups}
topics:
  - {{id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}}
  - {{id: piezoelectric, label: Piezoelectric, group: piezo-ferroelectric, keywords: [piezoelectric]}}
""",
    )

    with pytest.raises(ConfigError, match=f"^{message}$"):
        load_topic_catalog(path)


def test_topic_rejects_unknown_group_duplicate_label_and_empty_group(tmp_path: Path) -> None:
    unknown_group = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1}
topics:
  - {id: saw, label: SAW, group: missing, keywords: [SAW]}
""",
    )
    with pytest.raises(ConfigError, match="^topic saw references unknown group: missing$"):
        load_topic_catalog(unknown_group)

    duplicate_label = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1}
topics:
  - {id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}
  - {id: fbar, label: SAW, group: acoustic-rf, keywords: [FBAR]}
""",
    )
    with pytest.raises(ConfigError, match="^duplicate topic label: SAW$"):
        load_topic_catalog(duplicate_label)

    empty_group = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1}
  - {id: piezo-ferroelectric, label: Piezo, order: 2}
topics:
  - {id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}
""",
    )
    with pytest.raises(ConfigError, match="^topic group has no topics: piezo-ferroelectric$"):
        load_topic_catalog(empty_group)


@pytest.mark.parametrize(
    ("requires", "message"),
    [
        ("[]", "topic xrd requires_any_group must be a non-empty list when provided"),
        ("[acoustic-rf, acoustic-rf]", "topic xrd requires_any_group contains duplicates"),
        ("[missing]", "topic xrd requires_any_group references unknown group: missing"),
        (
            "[characterization-reliability]",
            "topic xrd requires_any_group cannot contain its own group",
        ),
    ],
)
def test_requires_any_group_rules_are_enforced(
    requires: str, message: str, tmp_path: Path
) -> None:
    path = write_yaml(
        tmp_path,
        f"""
topic_groups:
  - {{id: acoustic-rf, label: Acoustic, order: 1}}
  - {{id: characterization-reliability, label: Characterization, order: 2}}
topics:
  - {{id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}}
  - id: xrd
    label: XRD
    group: characterization-reliability
    requires_any_group: {requires}
    keywords: [XRD]
""",
    )

    with pytest.raises(ConfigError, match=f"^{message}$"):
        load_topic_catalog(path)


def test_normalized_duplicate_topic_keywords_are_rejected(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1}
topics:
  - id: saw
    label: SAW
    group: acoustic-rf
    keywords: [surface-acoustic-wave, " Surface\u2013Acoustic  Wave "]
""",
    )

    with pytest.raises(ConfigError, match="^topic saw has duplicate normalized keyword"):
        load_topic_catalog(path)


def test_topic_group_and_catalog_unknown_fields_are_rejected(tmp_path: Path) -> None:
    group_path = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1, description: nope}
topics:
  - {id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}
""",
    )
    with pytest.raises(ConfigError, match="^topic group acoustic-rf has unknown fields"):
        load_topic_catalog(group_path)

    root_path = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1}
topics:
  - {id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}
extra: nope
""",
    )
    with pytest.raises(ConfigError, match="^topic catalog has unknown fields: extra$"):
        load_topic_catalog(root_path)


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

    feeds = load_feeds(PROJECT_ROOT / "feeds.yml")

    assert [feed.id for feed in feeds] == expected_ids
    ultrasonics_feed = next(feed for feed in feeds if feed.id == "ieee-transactions-ultrasonics")
    assert (
        ultrasonics_feed.name
        == "IEEE Transactions on Ultrasonics, Ferroelectrics, and Frequency Control"
    )


def test_seed_configuration_contains_only_approved_topics() -> None:
    expected_groups = [
        ("acoustic-rf", "声学与射频器件", 1),
        ("piezo-ferroelectric", "压电与铁电薄膜", 2),
        ("ultrasound-sensing", "超声换能器与声学传感", 3),
        ("mems-nems", "MEMS/NEMS 与微纳制造", 4),
        ("electronic-semiconductor", "电子与半导体器件", 5),
        ("ai-computational", "人工智能与计算设计", 6),
        ("characterization-reliability", "材料表征与器件可靠性", 7),
        ("emerging-cross-disciplinary", "新兴交叉方向", 8),
    ]
    expected_ids = [
        "baw",
        "saw",
        "fbar",
        "lamb-wave",
        "acoustic-resonator",
        "rf-microwave",
        "multiplexer",
        "piezoelectric",
        "ferroelectric",
        "aln",
        "alscn",
        "pzt",
        "linbo3",
        "hfo2-hzo",
        "lead-free-piezoelectrics",
        "film-growth",
        "pmut",
        "cmut",
        "ultrasonic-transducer",
        "ultrasound-imaging",
        "therapeutic-ultrasound",
        "acoustic-sensing",
        "mems",
        "nems",
        "microfabrication",
        "wafer-integration",
        "cmos-integration",
        "packaging",
        "transistor",
        "ferroelectric-transistor",
        "memory-memristor",
        "power-electronics",
        "wide-bandgap-devices",
        "2d-electronics",
        "sensors",
        "machine-learning",
        "transformer-llm",
        "inverse-design",
        "surrogate-modelling",
        "physics-informed-ai",
        "materials-informatics",
        "autonomous-research",
        "digital-twin",
        "xray-characterization",
        "electron-microscopy",
        "probe-microscopy",
        "spectroscopy",
        "crystal-quality",
        "reliability",
        "phononics",
        "quantum-acoustics",
        "optomechanics",
        "acoustofluidics",
        "energy-harvesting",
        "flexible-devices",
        "nonreciprocal-acoustics",
    ]

    catalog = load_topic_catalog(PROJECT_ROOT / "topics.yml")

    assert [(group.id, group.label, group.order) for group in catalog.groups] == expected_groups
    assert [topic.id for topic in catalog.topics] == expected_ids
    assert len(catalog.groups) == 8
    assert len(catalog.topics) == 56

    topics = {topic.id: topic for topic in catalog.topics}
    assert topics["baw"].keywords == (
        "bulk acoustic wave",
        "bulk acoustic resonator",
        "BAW resonator",
        "BAW filter",
    )
    assert "MEMS packaging" in topics["packaging"].keywords
    assert "generative AI" in topics["transformer-llm"].keywords
    assert "full width at half maximum" in topics["crystal-quality"].keywords

    characterization_requirements = tuple(
        group_id
        for group_id, _, _ in expected_groups
        if group_id != "characterization-reliability"
    )
    for topic in catalog.topics:
        if topic.group == "characterization-reliability":
            assert topic.requires_any_group == characterization_requirements
