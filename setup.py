import os
from setuptools import setup, find_packages

setup(
    name="rate_limiting_guard",
    author="Sinergise ltd.",
    author_email="info@sentinel-hub.com",
    license="MIT",
    version=os.environ.get("RATE_LIMITING_GUARD_VERSION", "0.0.0"),
    packages=["rate_limiting_guard", "rate_limiting_guard.lib", "rate_limiting_guard.syncer"],
    install_requires =["redis", "kazoo", "requests", "pyjwt"]
)
