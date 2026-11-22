"""Numero de version unique : lu dans metadata.txt (evite les chaines figees)."""
import os


def get_version():
    path = os.path.join(os.path.dirname(__file__), "metadata.txt")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip().lower().startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "?"
