# ESPHome Git Update

A Home Assistant integration for ESPHome devices whose firmware is built from a
Git repository on **Gitea** or **Forgejo**.

Home Assistant's own ESPHome update only offers an update when the ESPHome
version changes. This integration offers one whenever the repository's branch
has a commit the device is not running, and installs it with one tap from
**Settings → Updates** or the device page.

- **Update available** when the branch's newest commit differs from the commit
  the device reports (checked every 5 minutes).
- **Release notes** list the commits since the installed one, and the link opens
  the repository's comparison.
- **Install** asks ESPHome Device Builder to build the device and upload it over
  the air, the same two steps as Home Assistant's own ESPHome update. Progress
  shows while it runs; a failed build or upload is reported as an error.
- One entry per device, each with its own repository, branch and token.

## Requirements

- Home Assistant 2026.10 or later, with the **ESPHome Device Builder** app
  connected to Home Assistant's ESPHome integration (the connection that makes
  Home Assistant's own ESPHome updates work).
- A Device Builder configuration that builds from the repository, for example
  with a remote package, so that every build uses the newest code:

  ```yaml
  packages:
    device:
      url: https://git.example.com/owner/repo
      ref: main
      files: [device.yaml]
      refresh: 0s
  ```

- A text sensor on the device that reports the Git commit it was built from
  (7 or more hex characters, `+local` allowed for builds with uncommitted
  changes). See [Reporting the commit](#reporting-the-commit).

## Install

With HACS: **HACS → ⋮ → Custom repositories**, add this repository as an
*Integration*, then install **ESPHome Git Update** and restart Home Assistant.

By hand: copy `custom_components/esphome_git_update` to
`/config/custom_components/esphome_git_update` and restart Home Assistant.

## Set up

**Settings → Devices & services → Add integration → ESPHome Git Update**:

1. Pick the Device Builder configuration. Devices already set up are not
   offered again; add the integration once per device.
2. Check the suggested commit sensor (one whose name contains "revision",
   "commit" or "git"), and enter the repository's web address and branch.
   A token is only needed for a private repository: create one in Gitea with
   **repository: read** and nothing else.

Settings can be changed later with **Reconfigure**. A saved token is never shown
again; leave the field empty to keep it, or tick **Remove the saved access
token**.

The update appears on the device's page as **Code**.

## Reporting the commit

The device must report its commit. One way is a small external component kept
in the device's own repository, which asks Git at build time:

```python
# components/git_revision/__init__.py
from pathlib import Path
import subprocess

import esphome.codegen as cg
import esphome.config_validation as cv

CONFIG_SCHEMA = cv.Schema({})


def _git(*args):
    root = Path(__file__).resolve().parents[2]  # the repository checkout
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


async def to_code(config):
    commit = _git("rev-parse", "--short=7", "HEAD") or "unknown"
    if commit != "unknown" and _git("status", "--porcelain", "--untracked-files=no"):
        commit += "+local"
    cg.add_define("GIT_REVISION", commit)  # ESPHome makes it a C string
```

```yaml
external_components:
  - source:
      type: git
      url: https://git.example.com/owner/repo
      ref: main
      path: components
    refresh: 0s
    components: [git_revision]

git_revision:

text_sensor:
  - platform: template
    name: Firmware revision
    entity_category: diagnostic
    update_interval: never
    lambda: return std::string(GIT_REVISION);
```

## Security

- **Token.** Stored in the config entry like other Home Assistant credentials,
  sent only as a request header to the repository address you entered, never
  in an address, log line or error message. Redirects are not followed, so it
  cannot be passed on to another server. Use a read-only token, and an `https`
  address if the repository is reached over an untrusted network.
- **What is read.** Only the branch and the comparison between two commits.
  Answers over 2 MB, or that are not the expected shape, are refused; commit
  hashes are checked before they are used in an address; commit messages are
  shortened and shown as plain text.
- **What the device reports.** Only a commit hash (optionally `+local`) is
  accepted; anything else leaves the installed version unknown.
- **Install** builds only the configuration chosen at setup, through Home
  Assistant's existing Device Builder connection. Who may install is governed
  by Home Assistant's own permissions for update entities.

## Limits

- Any new commit counts, including ones that do not change the firmware.
- Install builds the newest commit at the time of the build, which may be newer
  than the one shown if a commit lands in between.
- Install uses Home Assistant's connection to Device Builder, which is not a
  public interface. If a future Home Assistant or Device Builder release
  changes it, Install reports an error and Device Builder's own Install button
  still works.

## Development

```sh
pip install -r requirements_test.txt
pytest
```
