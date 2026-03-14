from setuptools import setup
from setuptools import find_packages

try:
    requires = open('requirements.txt').read().splitlines()
except FileNotFoundError:
    print("Warning: requirements.txt not found, proceeding without install_requires")
    requires = []

setup(
    name='Argus',
    version='0.2.5',
    packages=find_packages(),
    url='',
    license='',
    author='Salman',
    author_email='',
    description='',
    install_requires=requires
)
