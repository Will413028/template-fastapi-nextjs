import pytest

from app.platform.config import ConfigError, Settings


def test_defaults_are_production() -> None:
    settings = Settings.from_env({})
    assert settings.mode == "prod"
    assert (settings.host, settings.port) == ("", 8000)
    assert settings.probe_host == "127.0.0.1"


def test_dev_mode_and_address() -> None:
    settings = Settings.from_env({"MODE": "dev", "HTTP_ADDR": "127.0.0.1:9000"})
    assert settings.mode == "dev"
    assert (settings.host, settings.port) == ("127.0.0.1", 9000)
    assert settings.probe_host == "127.0.0.1"


def test_unspecified_ipv6_host_is_probed_on_loopback() -> None:
    assert Settings.from_env({"HTTP_ADDR": "[::]:8000"}).probe_host == "127.0.0.1"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MODE": "staging"}, "MODE must be dev or prod"),
        ({"HTTP_ADDR": "8000"}, "HTTP_ADDR must be host:port"),
        ({"HTTP_ADDR": ":http"}, "HTTP_ADDR must be host:port"),
        ({"HTTP_ADDR": ":70000"}, "HTTP_ADDR port must be 1-65535"),
    ],
)
def test_invalid_values_stop_startup(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)
