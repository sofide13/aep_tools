# -*- coding: utf-8 -*-
"""Acces reseau HTTPS uniquement (http.client), sans urllib.request.urlopen.

Seul le schema https est autorise ; les redirections sont suivies (max 5)
et ne peuvent mener qu'a une URL https.

Auteur : Kouidri Sofiane - Copyright (c) 2026 Kouidri Sofiane
"""
import http.client
import urllib.error
import urllib.parse

_REDIRECTS = (301, 302, 303, 307, 308)


class _Response:
    """Reponse minimale compatible avec l'usage : with ... as r, r.read(n), r.headers."""

    def __init__(self, conn, resp):
        self._conn = conn
        self._resp = resp
        self.headers = resp.headers
        self.status = resp.status

    def read(self, amt=None):
        return self._resp.read() if amt is None else self._resp.read(amt)

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def https_urlopen(req, timeout=30):
    """Ouvre une URL https. Accepte une urllib.request.Request ou une chaine."""
    if isinstance(req, str):
        url, data, headers, method = req, None, {}, "GET"
    else:
        url = req.full_url
        data = req.data
        headers = dict(req.header_items())
        method = req.get_method()

    for _ in range(6):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname:
            raise ValueError("URL non autorisee (https requis) : " + url[:80])
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443,
                                           timeout=timeout)
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        if resp.status in _REDIRECTS and resp.getheader("Location"):
            url = urllib.parse.urljoin(url, resp.getheader("Location"))
            resp.read()
            conn.close()
            if resp.status == 303:
                method, data = "GET", None
            continue
        if resp.status >= 400:
            err = urllib.error.HTTPError(url, resp.status, resp.reason,
                                         resp.headers, None)
            conn.close()
            raise err
        return _Response(conn, resp)
    raise ValueError("Trop de redirections : " + url[:80])
