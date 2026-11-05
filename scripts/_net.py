# -*- coding: utf-8 -*-
"""Acces reseau securise : seules les URL https:// sont autorisees.

Auteur : Kouidri Sofiane - Copyright (c) 2026 Kouidri Sofiane
"""
import urllib.parse
import urllib.request


def https_urlopen(req, timeout=30):
    """urlopen limite au schema https (refuse file://, ftp://, etc.)."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if urllib.parse.urlparse(url).scheme.lower() != "https":
        raise ValueError("URL non autorisee (https requis) : " + url[:80])
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - schema https verifie ci-dessus
