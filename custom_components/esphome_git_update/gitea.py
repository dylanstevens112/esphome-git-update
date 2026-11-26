"""Read a branch and its changes from a Gitea (or Forgejo) repository."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_BRANCH, CONF_REPO_URL, CONF_TOKEN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=20)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MESSAGE_CHARS = 4000
SHA = re.compile(r"[0-9a-f]{7,64}")
# Git branch names: no spaces, control characters or "..", not starting with "-".
BRANCH = re.compile(r"(?!-)(?!.*\.\.)[A-Za-z0-9._/\-]{1,200}")


def is_commit(text: str | None) -> bool:
    """A (short or full) commit hash, as Gitea and the device report them."""
    return bool(text) and SHA.fullmatch(text) is not None


class GiteaError(Exception):
    """Gitea could not be read. The message never contains the token."""


class InsecureTokenError(GiteaError):
    """A token would be sent without encryption (an http address)."""


def origin(repo_url: str) -> tuple[str, str, int | None] | None:
    """Scheme, host and port of an address: the server a token belongs to."""
    try:
        parts = urlsplit(repo_url.strip())
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    return (parts.scheme.lower(), parts.hostname.lower(), port or default)


@dataclass(frozen=True)
class Commit:
    """One commit on the branch."""

    sha: str
    message: str

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def title(self) -> str:
        lines = self.message.strip().splitlines()
        return lines[0].strip() if lines else ""


def _commit(sha: Any, message: Any) -> Commit:
    if not isinstance(sha, str) or not is_commit(sha.lower()):
        raise GiteaError("unexpected answer")
    text = message if isinstance(message, str) else ""
    return Commit(sha=sha.lower(), message=text[:MAX_MESSAGE_CHARS])


class GiteaRepo:
    """One repository, e.g. https://git.example.com/owner/repo.

    Gitea may live under a sub-path (https://example.com/git/owner/repo): the
    last two path parts are the owner and repository.
    """

    def __init__(self, hass: HomeAssistant, repo_url: str, branch: str, token: str | None) -> None:
        parts = urlsplit(repo_url.strip().rstrip("/"))
        segments = [s for s in parts.path.split("/") if s]
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or len(segments) < 2
        ):
            raise GiteaError("not a repository address")
        if segments[-1].endswith(".git"):
            segments[-1] = segments[-1][:-4]
        if not BRANCH.fullmatch(branch):
            raise GiteaError("not a branch name")
        if token and parts.scheme != "https":
            raise InsecureTokenError("a token needs an https address")
        base = "/".join(quote(s, safe="") for s in segments[:-2])
        prefix = f"{parts.scheme}://{parts.netloc}" + (f"/{base}" if base else "")
        owner_repo = "/".join(quote(s, safe="") for s in segments[-2:])
        self.web_url = f"{prefix}/{owner_repo}"
        self._api = f"{prefix}/api/v1/repos/{owner_repo}"
        self.branch = branch
        self._headers = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"token {token}"
        self._session = async_get_clientsession(hass)

    async def _get(self, path: str) -> Any:
        try:
            # No redirects: the token must only ever go to the address given.
            async with self._session.get(
                f"{self._api}/{path}",
                headers=self._headers,
                timeout=TIMEOUT,
                allow_redirects=False,
            ) as resp:
                if resp.status != 200:
                    raise GiteaError(f"Gitea answered {resp.status}")
                length = resp.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                    raise GiteaError("answer too large")
                body = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise GiteaError("answer too large")
                return json.loads(body)
        except GiteaError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            # Exception text can carry the request address; keep only the type.
            raise GiteaError(type(err).__name__) from err

    async def latest(self) -> Commit:
        # Gitea matches the rest of the path as the branch, so "/" stays as is
        # (the name is checked in __init__: no "..", spaces or odd characters).
        data = await self._get(f"branches/{quote(self.branch, safe='/')}")
        commit = data.get("commit") if isinstance(data, dict) else None
        if not isinstance(commit, dict):
            raise GiteaError("unexpected answer")
        return _commit(commit.get("id"), commit.get("message"))

    async def changes(self, base: str, head: str) -> list[Commit]:
        """Commits after base up to head, newest first."""
        if not (is_commit(base) and is_commit(head)):
            raise GiteaError("not a commit")
        data = await self._get(f"compare/{base}...{head}")
        commits = data.get("commits") if isinstance(data, dict) else None
        if not isinstance(commits, list):
            raise GiteaError("unexpected answer")
        result = []
        for item in commits:
            if isinstance(item, dict) and isinstance(item.get("commit"), dict):
                result.append(_commit(item.get("sha"), item["commit"].get("message")))
        return result

    def compare_url(self, base: str, head: str) -> str | None:
        if not (is_commit(base) and is_commit(head)):
            return None
        return f"{self.web_url}/compare/{base}...{head}"


class GiteaCoordinator(DataUpdateCoordinator[Commit]):
    """Checks the branch every few minutes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} code",
            update_interval=POLL_INTERVAL,
        )
        self.repo = GiteaRepo(
            hass,
            entry.data[CONF_REPO_URL],
            entry.data[CONF_BRANCH],
            entry.data.get(CONF_TOKEN) or None,
        )

    async def _async_update_data(self) -> Commit:
        try:
            return await self.repo.latest()
        except GiteaError as err:
            raise UpdateFailed(f"Could not read {self.repo.web_url} ({err})") from err
