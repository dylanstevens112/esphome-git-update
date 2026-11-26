"""git_revision: the Git commit a device is built from, as the C string GIT_REVISION.

With ``url`` (and ``ref``, if the package has one) it reads the commit from the
checkout of the remote package with that url and ref, the code the build really
uses. ESPHome has already fetched it, so nothing is fetched again.

Without ``url`` it reads the Git checkout that holds the device's YAML file, and
adds "+local" when tracked files have uncommitted changes.

The commit is "unknown" when it cannot be read.
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import git
from esphome.const import CONF_REF, CONF_URL
from esphome.core import CORE

CODEOWNERS = ["@dylanstevens112"]

_LOGGER = logging.getLogger(__name__)

PACKAGES_DOMAIN = "packages"
LOCAL_SUFFIX = "+local"
UNKNOWN = "unknown"


def _ref_needs_url(config):
    if CONF_REF in config and CONF_URL not in config:
        raise cv.Invalid("ref needs url (the package's url)")
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.Optional(CONF_URL): cv.url,
            cv.Optional(CONF_REF): cv.git_ref,
        }
    ),
    _ref_needs_url,
)


def _package_sources():
    """(url, ref) of every remote package ESPHome fetched, or None if unknown."""
    try:
        from esphome.components.packages import get_remote_package_sources
    except ImportError:  # older ESPHome: cannot check
        return None
    return [(s.url, s.ref) for s in get_remote_package_sources()]


def _check_package(config):
    url = config.get(CONF_URL)
    if url is None:
        return config
    sources = _package_sources()
    if sources is None or (url, config.get(CONF_REF)) in sources:
        return config
    fetched = ", ".join(f"{u} (ref {r or 'not set'})" for u, r in sources) or "none"
    raise cv.Invalid(
        f"No remote package is fetched from {url} (ref {config.get(CONF_REF) or 'not set'}). "
        f"Use the same url and ref as the package. Packages fetched: {fetched}",
        path=[CONF_URL],
    )


# Packages are resolved before any component is validated, so their sources
# are known by now.
FINAL_VALIDATE_SCHEMA = _check_package


def _git(repo: Path, *args: str) -> str:
    try:
        # Fixed arguments and git from PATH, as ESPHome itself runs it.
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def package_checkout(url: str, ref: str | None) -> Path:
    """Where ESPHome keeps the package with this url and ref."""
    never = getattr(git, "NEVER_REFRESH", None)
    if never is not None:
        # ESPHome's own lookup. The package is already there (checked in
        # final validation), so with refresh disabled this only finds it.
        repo_dir, _ = git.clone_or_update(
            url=url, ref=ref, refresh=never, domain=PACKAGES_DOMAIN
        )
        return Path(repo_dir)
    # Older ESPHome: the same place, worked out as ESPHome does.
    digest = hashlib.sha256(f"{url}@{ref}".encode()).hexdigest()[:8]
    return Path(CORE.data_dir) / PACKAGES_DOMAIN / digest


def revision(repo: Path) -> str:
    """Short commit of a checkout, "+local" if tracked files changed."""
    commit = _git(repo, "rev-parse", "--short=7", "HEAD")
    if not commit:
        return UNKNOWN
    if _git(repo, "status", "--porcelain", "--untracked-files=no"):
        commit += LOCAL_SUFFIX
    return commit


async def to_code(config):
    if CONF_URL in config:
        repo = package_checkout(config[CONF_URL], config.get(CONF_REF))
        where = config[CONF_URL]
    else:
        repo = Path(CORE.config_path).resolve().parent
        where = str(repo)
    commit = revision(repo)
    if commit == UNKNOWN:
        _LOGGER.warning("git_revision: could not read the commit of %s", where)
    else:
        _LOGGER.info("git_revision: %s", commit)
    cg.add_define("GIT_REVISION", commit)  # ESPHome makes it a C string
