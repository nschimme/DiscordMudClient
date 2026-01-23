import urllib.parse

def parse_mud_url(url_str):
    """
    Parses a MUD URL and returns (protocol, host, port, path).
    Defaults to 'telnets://' if no protocol is specified.
    """
    if not url_str:
        return None

    if '://' not in url_str:
        url_str = 'telnets://' + url_str

    parsed = urllib.parse.urlparse(url_str)
    protocol = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port
    path = parsed.path or '/'

    if not port:
        if protocol == 'telnet': port = 23
        elif protocol == 'telnets': port = 992
        elif protocol == 'ws': port = 80
        elif protocol == 'wss': port = 443

    return protocol, host, port, path
