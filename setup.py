from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

# get version from __version__ variable in tradehub_core/__init__.py
from tradehub_core import __version__ as version

setup(
    name="tradehub_core",
    version=version,
    description="Base platform infrastructure layer for TradeHub Marketplace",
    author="TradeHub Team",
    author_email="dev@tradehub.local",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
