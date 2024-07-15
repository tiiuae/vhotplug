from setuptools import setup, find_packages

setup(
    name="vhotplug",
    version="1.0",
    packages=find_packages(),
    py_modules=['main', 'qemulink', 'device'],
    scripts=["main.py"],
)
