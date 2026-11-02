"""Remove local identity from published reports, without changing measurements."""
import platform
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def public_text(text):
    text = text.replace(str(ROOT), '${REPO}').replace(str(Path.home()), '${HOME}')
    hostname = platform.node()
    if hostname:
        text = text.replace(hostname, '${HOST}')
    # Reports imported from another workstation may have a different user name.
    text = re.sub(r'/(?:Users|home)/[^/\s"\'<>]+', '${HOME}', text)
    text = re.sub(r'/var/folders/[^\s"\'<>]+', '${TMP}', text)
    return text

def public_data(value):
    if isinstance(value, str):
        return public_text(value)
    if isinstance(value, list):
        return [public_data(v) for v in value]
    if isinstance(value, dict):
        return {k: ('${HOST}' if k in ('host', 'hostname') else public_data(v)) for k,v in value.items()}
    return value
