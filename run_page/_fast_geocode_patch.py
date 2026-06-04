"""
Temporary patch: replace Nominatim reverse-geocode with a fast in-process lookup.
- For Chinese users, most activities are in China; the bounding-box check decides.
- Falls back to "Unknown" if outside the rough China bounding box.
This avoids the 1 req/s Nominatim rate limit (which makes 600+ activities take hours).
"""
import os
import sys


CN_BBOX = (18.0, 73.0, 54.0, 135.5)


def fast_reverse(lat, lon):
    if CN_BBOX[0] <= lat <= CN_BBOX[2] and CN_BBOX[1] <= lon <= CN_BBOX[3]:
        return "中国"
    return "Unknown"


def _install_patch():
    """Monkey-patch the module-level `g` (Nominatim) in generator.db."""
    from generator import db as gen_db

    class _FastReverse:
        def __call__(self, query, *args, **kwargs):
            try:
                lat_str, lon_str = query.split(",", 1)
                return fast_reverse(float(lat_str.strip()), float(lon_str.strip()))
            except Exception:
                return "Unknown"

    gen_db.g.reverse = _FastReverse()


def main():
    _install_patch()

    import asyncio
    import hashlib
    import coros_sync

    ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COROS_ACCOUNT")
    PASSWORD = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("COROS_PASSWORD")
    ONLY_RUN = "--only-run" in sys.argv

    # Coros API expects the password as a lowercase MD5 hex digest.
    ENCRYPTED_PWD = hashlib.md5(PASSWORD.encode()).hexdigest()

    asyncio.run(
        coros_sync.download_and_generate(
            account=ACCOUNT,
            password=ENCRYPTED_PWD,
            is_only_running=ONLY_RUN,
            file_type="fit",
        )
    )


if __name__ == "__main__":
    main()
