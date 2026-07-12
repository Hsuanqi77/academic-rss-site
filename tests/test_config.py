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

CHARACTERIZATION_CONTEXT_GROUPS = (
    "acoustic-rf",
    "piezo-ferroelectric",
    "ultrasound-sensing",
    "mems-nems",
    "electronic-semiconductor",
    "ai-computational",
    "emerging-cross-disciplinary",
)

EXPECTED_PRODUCTION_TOPICS = (
    (
        "baw",
        "BAW",
        "acoustic-rf",
        ("bulk acoustic wave", "bulk acoustic resonator", "BAW resonator", "BAW filter"),
        (),
    ),
    (
        "saw",
        "SAW",
        "acoustic-rf",
        ("surface acoustic wave", "surface acoustic resonator", "SAW resonator", "SAW filter"),
        (),
    ),
    (
        "fbar",
        "FBAR",
        "acoustic-rf",
        ("film bulk acoustic resonator", "thin-film bulk acoustic resonator", "FBAR", "FBAR filter"),
        (),
    ),
    (
        "lamb-wave",
        "Lamb wave",
        "acoustic-rf",
        ("Lamb wave", "Lamb wave resonator", "LWR", "contour-mode resonator"),
        (),
    ),
    (
        "acoustic-resonator",
        "Acoustic resonator",
        "acoustic-rf",
        ("acoustic resonator", "piezoelectric resonator", "acoustic filter", "resonator filter"),
        (),
    ),
    (
        "rf-microwave",
        "RF & Microwave",
        "acoustic-rf",
        (
            "radio frequency",
            "RF front-end",
            "RF filter",
            "microwave",
            "millimeter wave",
            "millimetre wave",
            "mmWave",
        ),
        (),
    ),
    (
        "multiplexer",
        "Multiplexer",
        "acoustic-rf",
        ("duplexer", "multiplexer", "diplexer", "filter bank", "frequency multiplexer"),
        (),
    ),
    (
        "piezoelectric",
        "Piezoelectric",
        "piezo-ferroelectric",
        ("piezoelectric", "piezoelectricity", "piezoelectric coefficient", "piezoelectric thin film"),
        (),
    ),
    (
        "ferroelectric",
        "Ferroelectric",
        "piezo-ferroelectric",
        ("ferroelectric", "ferroelectricity", "ferroelectric thin film", "ferroelectric polarization"),
        (),
    ),
    (
        "aln",
        "AlN",
        "piezo-ferroelectric",
        ("aluminum nitride", "aluminium nitride", "AlN thin film", "AlN piezoelectric"),
        (),
    ),
    (
        "alscn",
        "AlScN",
        "piezo-ferroelectric",
        ("aluminum scandium nitride", "aluminium scandium nitride", "scandium-doped AlN", "ScAlN", "AlScN"),
        (),
    ),
    (
        "pzt",
        "PZT",
        "piezo-ferroelectric",
        ("lead zirconate titanate", "PZT thin film", "PZT piezoelectric"),
        (),
    ),
    (
        "linbo3",
        "LiNbO3",
        "piezo-ferroelectric",
        ("lithium niobate", "LiNbO3", "thin-film lithium niobate", "LNOI"),
        (),
    ),
    (
        "hfo2-hzo",
        "HfO2/HZO",
        "piezo-ferroelectric",
        ("ferroelectric hafnium oxide", "hafnium zirconium oxide", "HfO2 ferroelectric", "HZO ferroelectric"),
        (),
    ),
    (
        "lead-free-piezoelectrics",
        "Lead-free piezoelectrics",
        "piezo-ferroelectric",
        ("lead-free piezoelectric", "potassium sodium niobate", "KNN", "barium titanate", "BaTiO3"),
        (),
    ),
    (
        "film-growth",
        "Film growth",
        "piezo-ferroelectric",
        (
            "reactive sputtering",
            "magnetron sputtering",
            "MOCVD",
            "atomic layer deposition",
            "pulsed laser deposition",
            "sol-gel deposition",
            "epitaxial growth",
        ),
        (),
    ),
    (
        "pmut",
        "PMUT",
        "ultrasound-sensing",
        (
            "piezoelectric micromachined ultrasonic transducer",
            "piezoelectric micromachined ultrasound transducer",
            "PMUT",
            "PMUT array",
        ),
        (),
    ),
    (
        "cmut",
        "CMUT",
        "ultrasound-sensing",
        (
            "capacitive micromachined ultrasonic transducer",
            "capacitive micromachined ultrasound transducer",
            "CMUT",
            "CMUT array",
        ),
        (),
    ),
    (
        "ultrasonic-transducer",
        "Ultrasonic transducer",
        "ultrasound-sensing",
        ("ultrasonic transducer", "ultrasound transducer", "piezoelectric transducer", "high-frequency transducer"),
        (),
    ),
    (
        "ultrasound-imaging",
        "Ultrasound imaging",
        "ultrasound-sensing",
        ("ultrasound imaging", "ultrasonic imaging", "medical ultrasound", "high-frequency ultrasound", "photoacoustic imaging"),
        (),
    ),
    (
        "therapeutic-ultrasound",
        "Therapeutic ultrasound",
        "ultrasound-sensing",
        ("therapeutic ultrasound", "focused ultrasound", "high-intensity focused ultrasound", "HIFU"),
        (),
    ),
    (
        "acoustic-sensing",
        "Acoustic sensing",
        "ultrasound-sensing",
        ("acoustic sensor", "ultrasonic sensor", "acoustic sensing", "ultrasonic sensing", "non-destructive testing", "nondestructive evaluation"),
        (),
    ),
    (
        "mems",
        "MEMS",
        "mems-nems",
        ("microelectromechanical system", "MEMS device", "MEMS resonator", "MEMS sensor"),
        (),
    ),
    (
        "nems",
        "NEMS",
        "mems-nems",
        ("nanoelectromechanical system", "NEMS device", "NEMS resonator"),
        (),
    ),
    (
        "microfabrication",
        "Microfabrication",
        "mems-nems",
        ("microfabrication", "micromachining", "surface micromachining", "bulk micromachining", "deep reactive ion etching"),
        (),
    ),
    (
        "wafer-integration",
        "Wafer integration",
        "mems-nems",
        ("wafer bonding", "wafer-level packaging", "through-silicon via", "TSV integration", "heterogeneous integration"),
        (),
    ),
    (
        "cmos-integration",
        "CMOS integration",
        "mems-nems",
        ("CMOS-compatible", "CMOS integration", "monolithic integration", "back-end-of-line", "BEOL integration"),
        (),
    ),
    (
        "packaging",
        "Packaging",
        "mems-nems",
        ("MEMS packaging", "hermetic packaging", "vacuum packaging", "chip-scale packaging"),
        (),
    ),
    (
        "transistor",
        "Transistor",
        "electronic-semiconductor",
        ("field-effect transistor", "thin-film transistor", "MOSFET", "FinFET", "GAAFET", "TFET", "HEMT"),
        (),
    ),
    (
        "ferroelectric-transistor",
        "Ferroelectric transistor",
        "electronic-semiconductor",
        ("ferroelectric field-effect transistor", "FeFET", "negative capacitance transistor", "NCFET"),
        (),
    ),
    (
        "memory-memristor",
        "Memory & Memristor",
        "electronic-semiconductor",
        ("nonvolatile memory", "resistive random-access memory", "RRAM", "memristor", "ferroelectric memory", "neuromorphic device"),
        (),
    ),
    (
        "power-electronics",
        "Power electronics",
        "electronic-semiconductor",
        ("power semiconductor", "power transistor", "power diode", "high-voltage device", "power electronic device"),
        (),
    ),
    (
        "wide-bandgap-devices",
        "Wide-bandgap devices",
        "electronic-semiconductor",
        ("gallium nitride device", "GaN transistor", "GaN HEMT", "silicon carbide device", "SiC MOSFET", "ultra-wide-bandgap semiconductor"),
        (),
    ),
    (
        "2d-electronics",
        "2D electronics",
        "electronic-semiconductor",
        ("two-dimensional transistor", "2D semiconductor", "MoS2 transistor", "transition metal dichalcogenide", "van der Waals device"),
        (),
    ),
    (
        "sensors",
        "Sensors",
        "electronic-semiconductor",
        ("electronic sensor", "chemical sensor", "gas sensor", "pressure sensor", "biosensor", "strain sensor"),
        (),
    ),
    (
        "machine-learning",
        "Machine learning",
        "ai-computational",
        ("machine learning", "deep learning", "neural network", "convolutional neural network", "recurrent neural network"),
        (),
    ),
    (
        "transformer-llm",
        "Transformer & LLM",
        "ai-computational",
        ("transformer model", "large language model", "foundation model", "generative artificial intelligence", "generative AI"),
        (),
    ),
    (
        "inverse-design",
        "Inverse design",
        "ai-computational",
        ("inverse design", "topology optimization", "generative design", "computational design optimization"),
        (),
    ),
    (
        "surrogate-modelling",
        "Surrogate modelling",
        "ai-computational",
        ("surrogate model", "reduced-order model", "Bayesian optimization", "Gaussian process regression"),
        (),
    ),
    (
        "physics-informed-ai",
        "Physics-informed AI",
        "ai-computational",
        ("physics-informed neural network", "physics-informed machine learning", "PINN", "neural operator"),
        (),
    ),
    (
        "materials-informatics",
        "Materials informatics",
        "ai-computational",
        ("materials informatics", "materials discovery", "machine-learning interatomic potential", "property prediction"),
        (),
    ),
    (
        "autonomous-research",
        "Autonomous research",
        "ai-computational",
        ("autonomous experiment", "self-driving laboratory", "automated experimentation", "active learning", "robotic laboratory"),
        (),
    ),
    (
        "digital-twin",
        "Digital twin",
        "ai-computational",
        ("digital twin", "virtual sensor", "data-driven modelling", "predictive maintenance"),
        (),
    ),
    (
        "xray-characterization",
        "X-ray characterization",
        "characterization-reliability",
        ("x-ray diffraction", "XRD", "reciprocal space mapping", "RSM", "rocking curve", "omega scan"),
        CHARACTERIZATION_CONTEXT_GROUPS,
    ),
    (
        "electron-microscopy",
        "Electron microscopy",
        "characterization-reliability",
        ("scanning electron microscopy", "SEM", "transmission electron microscopy", "TEM", "STEM"),
        CHARACTERIZATION_CONTEXT_GROUPS,
    ),
    (
        "probe-microscopy",
        "Probe microscopy",
        "characterization-reliability",
        ("atomic force microscopy", "AFM", "piezoresponse force microscopy", "PFM", "Kelvin probe force microscopy"),
        CHARACTERIZATION_CONTEXT_GROUPS,
    ),
    (
        "spectroscopy",
        "Spectroscopy",
        "characterization-reliability",
        ("x-ray photoelectron spectroscopy", "XPS", "Raman spectroscopy", "secondary ion mass spectrometry", "SIMS"),
        CHARACTERIZATION_CONTEXT_GROUPS,
    ),
    (
        "crystal-quality",
        "Crystal quality",
        "characterization-reliability",
        ("crystal orientation", "c-axis texture", "mosaicity", "residual stress", "dislocation density", "full width at half maximum"),
        CHARACTERIZATION_CONTEXT_GROUPS,
    ),
    (
        "reliability",
        "Reliability",
        "characterization-reliability",
        ("device reliability", "fatigue endurance", "breakdown field", "time-dependent dielectric breakdown", "aging", "thermal stability", "frequency drift"),
        CHARACTERIZATION_CONTEXT_GROUPS,
    ),
    (
        "phononics",
        "Phononics",
        "emerging-cross-disciplinary",
        ("phononics", "phononic crystal", "phononic bandgap", "acoustic metamaterial", "topological acoustics"),
        (),
    ),
    (
        "quantum-acoustics",
        "Quantum acoustics",
        "emerging-cross-disciplinary",
        ("quantum acoustics", "quantum acoustic", "phonon qubit", "single phonon", "microwave-to-acoustic conversion"),
        (),
    ),
    (
        "optomechanics",
        "Optomechanics",
        "emerging-cross-disciplinary",
        ("cavity optomechanics", "optomechanical resonator", "acousto-optic interaction", "microwave-to-optical conversion"),
        (),
    ),
    (
        "acoustofluidics",
        "Acoustofluidics",
        "emerging-cross-disciplinary",
        ("acoustofluidics", "acoustic microfluidics", "surface acoustic wave microfluidics", "acoustic particle manipulation"),
        (),
    ),
    (
        "energy-harvesting",
        "Energy harvesting",
        "emerging-cross-disciplinary",
        ("piezoelectric energy harvesting", "piezoelectric energy harvester", "vibration energy harvesting", "triboelectric generator"),
        (),
    ),
    (
        "flexible-devices",
        "Flexible devices",
        "emerging-cross-disciplinary",
        ("flexible electronics", "stretchable electronics", "wearable sensor", "flexible piezoelectric", "bio-integrated electronics"),
        (),
    ),
    (
        "nonreciprocal-acoustics",
        "Nonreciprocal acoustics",
        "emerging-cross-disciplinary",
        ("nonreciprocal acoustics", "non-reciprocal acoustic", "acoustic isolator", "acoustic circulator"),
        (),
    ),
)


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


def test_valid_topic_catalog_loads(tmp_path: Path) -> None:
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


def test_load_topics_returns_catalog_topics(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1}
topics:
  - {id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}
""",
    )

    catalog = load_topic_catalog(path)

    assert load_topics(path) == list(catalog.topics)


def test_load_topic_catalog_reads_path_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: acoustic-rf, label: Acoustic, order: 1}
topics:
  - {id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}
""",
    )
    original_read_text = Path.read_text
    read_count = 0

    def counting_read_text(self: Path, *args, **kwargs) -> str:
        nonlocal read_count
        read_count += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    load_topic_catalog(path)

    assert read_count == 1


def test_topic_groups_are_returned_in_order_field_order(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        """
topic_groups:
  - {id: piezo-ferroelectric, label: Piezo, order: 2}
  - {id: acoustic-rf, label: Acoustic, order: 1}
topics:
  - {id: piezoelectric, label: Piezoelectric, group: piezo-ferroelectric, keywords: [piezoelectric]}
  - {id: saw, label: SAW, group: acoustic-rf, keywords: [SAW]}
""",
    )

    catalog = load_topic_catalog(path)

    assert [(group.id, group.order) for group in catalog.groups] == [
        ("acoustic-rf", 1),
        ("piezo-ferroelectric", 2),
    ]
    assert [topic.id for topic in catalog.topics] == ["piezoelectric", "saw"]


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


@pytest.mark.parametrize(
    "publisher",
    ("nature", "aps", "aip", "ieee", "wiley", "elsevier", "aaas", "springer"),
)
def test_supported_publishers_load(publisher: str, tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        f"""
feeds:
  - id: {publisher}-test
    name: Supported Publisher Feed
    publisher: {publisher}
    feed_url: https://example.com/{publisher}.xml
""",
    )

    feeds = load_feeds(path)

    assert feeds[0].publisher == publisher


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

    with pytest.raises(
        ConfigError,
        match="^topic saw has duplicate normalized keyword: surface acoustic wave$",
    ):
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
        "nature-electronics",
        "npj-computational-materials",
        "physical-review-applied",
        "applied-physics-letters",
        "journal-applied-physics",
        "apl-materials",
        "ieee-transactions-ultrasonics",
        "ieee-transactions-mtt",
        "ieee-microwave-wireless-technology-letters",
        "ieee-transactions-electron-devices",
        "ieee-electron-device-letters",
        "journal-microelectromechanical-systems",
        "advanced-materials",
        "advanced-functional-materials",
        "advanced-electronic-materials",
        "acta-materialia",
        "science-advances",
        "nano-micro-letters",
    ]

    feeds = load_feeds(PROJECT_ROOT / "feeds.yml")

    assert [feed.id for feed in feeds] == expected_ids
    ultrasonics_feed = next(feed for feed in feeds if feed.id == "ieee-transactions-ultrasonics")
    assert (
        ultrasonics_feed.name
        == "IEEE Transactions on Ultrasonics, Ferroelectrics, and Frequency Control"
    )


def test_seed_configuration_contains_nine_new_production_feeds() -> None:
    expected = {
        "physical-review-applied": (
            "Physical Review Applied",
            "aps",
            "https://feeds.aps.org/rss/recent/prapplied.xml",
        ),
        "nature-electronics": (
            "Nature Electronics",
            "nature",
            "https://www.nature.com/natelectron.rss",
        ),
        "advanced-electronic-materials": (
            "Advanced Electronic Materials",
            "wiley",
            "https://advanced.onlinelibrary.wiley.com/action/showFeed?type=etoc&feed=rss&jc=2199160X",
        ),
        "journal-applied-physics": (
            "Journal of Applied Physics",
            "aip",
            "https://pubs.aip.org/rss/site_1000029/1000017.xml",
        ),
        "apl-materials": (
            "APL Materials",
            "aip",
            "https://pubs.aip.org/rss/site_1000013/1000009.xml",
        ),
        "npj-computational-materials": (
            "npj Computational Materials",
            "nature",
            "https://www.nature.com/npjcompumats.rss",
        ),
        "acta-materialia": (
            "Acta Materialia",
            "elsevier",
            "https://rss.sciencedirect.com/publication/science/13596454",
        ),
        "science-advances": (
            "Science Advances",
            "aaas",
            "https://feeds.science.org/rss/science-advances.xml",
        ),
        "nano-micro-letters": (
            "Nano-Micro Letters",
            "springer",
            "https://link.springer.com/search.rss?facet-journal-id=40820",
        ),
    }

    feeds = load_feeds(PROJECT_ROOT / "feeds.yml")
    actual = {
        feed.id: (feed.name, feed.publisher, feed.feed_url, feed.enabled)
        for feed in feeds
    }

    assert len(feeds) == 29
    for feed_id, production_contract in expected.items():
        assert actual[feed_id] == (*production_contract, True)
    assert all(
        feed.name.casefold() != "acs applied materials & interfaces" for feed in feeds
    )


def test_seed_configuration_contains_only_approved_topics() -> None:
    expected_groups = (
        ("acoustic-rf", "声学与射频器件", 1),
        ("piezo-ferroelectric", "压电与铁电薄膜", 2),
        ("ultrasound-sensing", "超声换能器与声学传感", 3),
        ("mems-nems", "MEMS/NEMS 与微纳制造", 4),
        ("electronic-semiconductor", "电子与半导体器件", 5),
        ("ai-computational", "人工智能与计算设计", 6),
        ("characterization-reliability", "材料表征与器件可靠性", 7),
        ("emerging-cross-disciplinary", "新兴交叉方向", 8),
    )

    catalog = load_topic_catalog(PROJECT_ROOT / "topics.yml")

    actual_groups = tuple(
        (group.id, group.label, group.order) for group in catalog.groups
    )
    actual_topics = tuple(
        (
            topic.id,
            topic.label,
            topic.group,
            topic.keywords,
            topic.requires_any_group,
        )
        for topic in catalog.topics
    )

    assert len(EXPECTED_PRODUCTION_TOPICS) == 56
    assert actual_groups == expected_groups
    assert actual_topics == EXPECTED_PRODUCTION_TOPICS
