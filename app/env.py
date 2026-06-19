"""Tiny .env loader (stdlib, no dependency).

Reads KEY=VALUE lines from a .env file into os.environ so keys/config can live in one file
instead of being exported every shell. Real environment variables always win (we never override
a value already set). Call load_dotenv() early — before the LLM layer reads EPISTEMIC_MODEL.
"""
import os


def load_dotenv(path=".env"):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return  # no .env — fine
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:        # don't override a real env var
            os.environ[k] = v
