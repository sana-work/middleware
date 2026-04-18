from setuptools import setup, find_packages

__Version__ = "1.0.0"

setup(
    name="opsui-agent-recon-api",
    version=__Version__,
    packages=find_packages(exclude=["tests", "docs"]),
    include_package_data=True,
    python_requires=">=3.13",
    install_requires=[
        "fastapi",
        "uvicorn",
        "pydantic",
        "pydantic-settings",
        "motor",
        "confluent-kafka",
        "httpx",
        "python-multipart",
        "reportlab",
        "slowapi"
    ],
    entry_points={
        "console_scripts": [
            "recon-middleware=app.main:app",
        ],
    },
)
