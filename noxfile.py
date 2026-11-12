"""Developer tasks: ``uvx nox`` (or ``uv run nox``) to run lint + tests."""

from __future__ import annotations

import nox

nox.options.default_venv_backend = "uv"
nox.options.sessions = ["lint", "tests"]
nox.needs_version = ">=2024.4.15"

PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]


def _sync(session: nox.Session, *groups: str) -> None:
    """Install the project and dependency groups into the session venv from uv.lock."""
    args = [f"--group={g}" for g in groups]
    session.run_install(
        "uv",
        "sync",
        "--locked",
        *args,
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )


@nox.session
def lint(session: nox.Session) -> None:
    """Check formatting and lint rules with ruff."""
    session.install("ruff")
    session.run("ruff", "format", "--check", ".")
    session.run("ruff", "check", ".")


@nox.session
def fmt(session: nox.Session) -> None:
    """Auto-format and auto-fix with ruff."""
    session.install("ruff")
    session.run("ruff", "format", ".")
    session.run("ruff", "check", "--fix", ".")


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """Run the test suite."""
    _sync(session, "dev")
    session.run("pytest", "--cov=vera_in_viewer", "--cov-report=term-missing", *session.posargs)


@nox.session
def serve(session: nox.Session) -> None:
    """Launch the viewer (samples are built in): ``nox -s serve -- --port 1234``."""
    _sync(session)
    session.run("vera-in-viewer", *session.posargs)


@nox.session
def build(session: nox.Session) -> None:
    """Build sdist and wheel into ./dist."""
    session.run("uv", "build", external=True)
