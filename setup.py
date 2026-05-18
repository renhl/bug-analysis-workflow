from setuptools import setup, find_packages

setup(
    name="bug-analysis-workflow",
    version="1.0.0",
    description="Evidence-first bug root-cause analysis workflow",
    packages=find_packages(
        exclude=["tests*", ".venv*", "scripts*", "skills*", "docs*"],
        include=["core", "core.*", "connectors", "connectors.*",
                 "adapters", "adapters.*", "domains", "domains.*"],
    ),
    python_requires=">=3.10",
    install_requires=[
        "httpx>=0.24.0",
        "pyyaml>=6.0",
        "tree-sitter>=0.20.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "black>=23.0.0",
            "mypy>=1.0.0",
        ]
    },
)
