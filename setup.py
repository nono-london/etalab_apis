import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="etalab_apis",
    version="0.0.0.4",
    author="Nono London",
    author_email="",
    description="Wrapper around etalab apis",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/nono-london/etalab_apis",
    packages=["etalab_apis"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=["aiohttp", "tqdm", "numpy"],
    tests_require=["pytest", "pytest-asyncio"],
    python_requires='>=3.10',
)
