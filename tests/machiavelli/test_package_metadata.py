from importlib.metadata import version

import machiavelli


def test_runtime_version_matches_distribution_metadata() -> None:
    assert machiavelli.__version__ == version("machiavelli")
    assert machiavelli.VERSION == machiavelli.__version__


def test_current_development_version() -> None:
    assert machiavelli.__version__ == "0.8.1"
