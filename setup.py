import re
from setuptools import setup
from setuptools import find_packages

try:
    requires = open('requirements.txt').read().splitlines()
except FileNotFoundError:
    print("Warning: requirements.txt not found, proceeding without install_requires")
    requires = []

with open('argus/__init__.py') as f:
    maybe_version = re.search(r'__version__ = ["\'](.+?)["\']', f.read())
    if maybe_version:
        __version__ = maybe_version.group(1)
    else:
        raise RuntimeError('Unable to find version string.')


setup(
    name='Argus',
    version=__version__,
    packages=find_packages(),
    url='',
    license='',
    author='Salman',
    author_email='',
    description='',
    install_requires=requires
)