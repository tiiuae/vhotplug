from setuptools import setup, find_packages

setup(
    name="vhotplug",
    version="1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "vhotplug=vhotplug.vhotplug:main",
        ],
    },
)
