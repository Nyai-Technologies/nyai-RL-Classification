"""The text of a file as the parser service sends it: cleaning and chunks."""
import json
import re

from app.config import settings


def clean_text(t):
    """Un-escape table chunks stored as (double-)encoded JSON lines -> 'key: value' lines; drop control characters."""
    if not t:
        return ""
    s = t
    for _ in range(3):  # peel off string-encoding layers
        try:
            v = json.loads(s)
        except Exception:
            break
        if isinstance(v, str):
            s = v
        else:
            s = json.dumps(v)
            break
    lines = []
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                lines.append(" | ".join(f"{k}: {v}" for k, v in obj.items()))
                continue
            if isinstance(obj, list):
                lines.append(" | ".join(map(str, obj)))
                continue
        except Exception:
            pass
        lines.append(line)
    out = "\n".join(lines)
    out = out.replace('\\"', '"').replace("\\n", "\n")
    out = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out)       # NULs and other control characters
    return re.sub(r"[ \t]+", " ", out).strip()


class TextSource:
    """One file whose parsed text (or chunks) was handed over by the parser service."""

    def __init__(self, file_id, text=None, file_name=None, chunk_chars=None, chunks=None):
        chunk_chars = chunk_chars or settings.CHUNK_CHARS
        self.file_id, self.file_name = file_id, file_name or file_id
        if chunks:                       # caller already chunked the file: use its chunks, in order
            self.pieces = [c for c in (clean_text(x) for x in chunks[:settings.RETRY_CHUNKS]) if c]
        else:
            t = clean_text(text or "")
            self.pieces = [t[i:i + chunk_chars].strip() for i in range(0, len(t), chunk_chars)]
            self.pieces = [p for p in self.pieces if p]

    def file_ids(self):
        return [self.file_id]

    def chunks(self, file_id, n):
        return self.file_name, self.pieces[:n]


class MultiTextSource:
    """Several files: [{file_id, text|chunks, file_name?}, ...]. Same interface as TextSource."""

    def __init__(self, files):
        self.files = {f["file_id"]: TextSource(f["file_id"], f.get("text"), f.get("file_name"),
                                               chunks=f.get("chunks")) for f in files}

    def file_ids(self):
        return list(self.files)

    def chunks(self, file_id, n):
        return self.files[file_id].chunks(file_id, n)
