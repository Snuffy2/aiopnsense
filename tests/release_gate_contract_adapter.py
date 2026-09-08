"""Repository-specific release gate identities for shared contract tests."""

GATES = (
    {
        "workflow": "pytest_check.yml",
        "job": "tests",
        "product": "pytest",
        "required_check": "pytest_check.yml::pytest check and post coverage",
    },
    {
        "workflow": "docs.yml",
        "job": "build-docs",
        "product": "docs",
        "required_check": "docs.yml::build-docs",
    },
    {
        "workflow": "uv-lock-check.yml",
        "job": "uv-lock-check",
        "product": "lock",
        "required_check": "uv-lock-check.yml::Validate uv lock consistency",
    },
    {
        "workflow": "prek-autofix-review.yml",
        "job": "review",
        "product": "prek",
        "required_check": "prek-autofix-review.yml::review",
    },
)
