from setuptools import setup, find_packages

setup(
    name="opsui-agent-recon-api",
    version="1.0.0",
    packages=find_packages(include=["app", "app.*"]),
    include_package_data=True,
    python_requires=">=3.11",
    install_requires=[
        "fastapi==0.110.1",
        "uvicorn==0.29.0",
        "pydantic==2.7.0",
        "pydantic-settings==2.2.1",
        "motor==3.4.0",
        "confluent-kafka==2.3.0",
        "httpx==0.27.0",
        "python-multipart==0.0.9",
        "reportlab==4.1.0",
        "slowapi==0.1.9"
    ],
    entry_points={
        "console_scripts": [
            "recon-middleware=app.main:app",
        ],
    },
)
