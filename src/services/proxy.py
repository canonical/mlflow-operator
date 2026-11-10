"""The model's proxy settings, which Juju passes to the charm.

Juju sets JUJU_CHARM_HTTP_PROXY, JUJU_CHARM_HTTPS_PROXY and JUJU_CHARM_NO_PROXY in
the charm's environment from the model's juju-http-proxy, juju-https-proxy and
juju-no-proxy settings. It does not set them for the workload.
"""

import ipaddress
import os
from typing import Mapping, Optional
from urllib.parse import urlparse

_JUJU_PROXY_VARIABLES = {
    "HTTP_PROXY": "JUJU_CHARM_HTTP_PROXY",
    "HTTPS_PROXY": "JUJU_CHARM_HTTPS_PROXY",
    "NO_PROXY": "JUJU_CHARM_NO_PROXY",
}


def proxy_environment(environ: Mapping[str, str] = os.environ) -> dict:
    """Return the model's proxy settings as the variables a workload reads.

    Each variable is given in upper and lower case, because programs differ in which one
    they read. Settings the model leaves empty are left out.
    """
    variables = {}
    for name, juju_name in _JUJU_PROXY_VARIABLES.items():
        if value := environ.get(juju_name, ""):
            variables[name] = value
            variables[name.lower()] = value
    return variables


def _bypasses_proxy(host: str, no_proxy: str) -> bool:
    """Return whether no_proxy exempts host, matching names, domains and CIDRs."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    for entry in (e.strip() for e in no_proxy.split(",")):
        if not entry:
            continue
        if entry == "*":
            return True
        if address is not None:
            try:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                pass
            continue
        domain = entry.lstrip("*").lstrip(".").lower()
        if host.lower() == domain or host.lower().endswith("." + domain):
            return True
    return False


def proxies_for(url: str, environ: Mapping[str, str] = os.environ) -> Optional[dict]:
    """Return the proxies a client in the charm should use for url, or None to go direct."""
    settings = proxy_environment(environ)
    host = urlparse(url).hostname or ""
    if _bypasses_proxy(host, settings.get("NO_PROXY", "")):
        return None
    proxies = {
        scheme: settings[name]
        for scheme, name in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY"))
        if name in settings
    }
    return proxies or None
