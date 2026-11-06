import pytest

from services.proxy import proxies_for, proxy_environment

JUJU_PROXY = {
    "JUJU_CHARM_HTTP_PROXY": "http://egress.internal:3128",
    "JUJU_CHARM_HTTPS_PROXY": "http://egress.internal:3128",
    "JUJU_CHARM_NO_PROXY": "127.0.0.1,localhost,10.0.0.0/8,.svc,.cluster.local",
}


def test_proxy_environment_in_both_cases():
    assert proxy_environment(JUJU_PROXY) == {
        "HTTP_PROXY": "http://egress.internal:3128",
        "http_proxy": "http://egress.internal:3128",
        "HTTPS_PROXY": "http://egress.internal:3128",
        "https_proxy": "http://egress.internal:3128",
        "NO_PROXY": "127.0.0.1,localhost,10.0.0.0/8,.svc,.cluster.local",
        "no_proxy": "127.0.0.1,localhost,10.0.0.0/8,.svc,.cluster.local",
    }


def test_no_proxy_settings_give_an_empty_environment():
    assert proxy_environment({}) == {}
    assert proxy_environment({"JUJU_CHARM_HTTPS_PROXY": ""}) == {}


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://radosgw.example.com:443",
            {"http": "http://egress.internal:3128", "https": "http://egress.internal:3128"},
        ),
        ("http://minio.kubeflow.svc:9000", None),
        ("http://minio.kubeflow.svc.cluster.local:9000", None),
        ("http://10.1.2.3:9000", None),
        ("http://localhost:9000", None),
        (
            "http://192.168.1.1:9000",
            {"http": "http://egress.internal:3128", "https": "http://egress.internal:3128"},
        ),
    ],
    ids=["external-host", "svc", "cluster-local", "cidr", "localhost", "ip-outside-cidr"],
)
def test_proxies_for(url, expected):
    assert proxies_for(url, JUJU_PROXY) == expected


def test_no_proxies_without_settings():
    assert proxies_for("https://radosgw.example.com:443", {}) is None


def test_a_wildcard_bypasses_every_host():
    assert (
        proxies_for("https://radosgw.example.com", {**JUJU_PROXY, "JUJU_CHARM_NO_PROXY": "*"})
        is None
    )
