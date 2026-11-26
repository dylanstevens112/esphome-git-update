# ESPHome Git Update

A Home Assistant integration for ESPHome devices whose firmware is built from a
Git repository on **Gitea** or **Forgejo**.

Home Assistant's own ESPHome update only offers an update when the ESPHome
version changes. This integration offers one whenever the repository's branch
has a commit the device is not running, and installs it with one tap from
**Settings → Updates** or the device page.

- **Update available** when the branch's newest commit differs from the commit
  the device reports (checked every 5 minutes). Optionally, commits that only
  change files such as `*.md` or `docs/` are not offered.
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
  changes). This repository ships an ESPHome component for that: see
  [Reporting the commit](#reporting-the-commit).

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
   The sensor must belong to the device built from the chosen configuration:
   the update shows on that device, and Install flashes it.
   A token is only needed for a private repository: create one in Gitea with
   **repository: read** and nothing else. A token needs the repository's
   `https` address.
3. Optionally list the files whose changes need no new build, one pattern per
   line, for example:

   ```text
   *.md
   docs/
   ```

   A commit that only changes matching files is then not offered as an update.
   `*` also matches `/` (so `*.md` matches Markdown files in every folder), and
   a pattern ending in `/` matches a whole folder. Paths are relative to the
   repository root and case-sensitive.

If Gitea later refuses the token (expired or removed), Home Assistant asks for
a new one under **Settings → Devices & services** instead of retrying.

Settings can be changed later with **Reconfigure**. A saved token is never shown
again; leave the field empty to keep it (only while the server stays the
same), or tick **Remove the saved access token**.

The update appears on the device's page as **Code**.

## Reporting the commit

The device must report the commit it was built from. The `git_revision`
component in this repository does that at build time:

```yaml
packages:
  device:
    url: https://git.example.com/owner/repo
    ref: main
    files: [device.yaml]
    refresh: 0s

external_components:
  - source: github://dylanstevens112/esphome-git-update@v1.1.0
    components: [git_revision]

git_revision:
  url: https://git.example.com/owner/repo  # the package's url and ref, exactly
  ref: main

text_sensor:
  - platform: template
    name: Firmware revision
    entity_category: diagnostic
    update_interval: 1h  # publishes within seconds of boot, then hourly; "never" would never publish
    lambda: return std::string(GIT_REVISION);
```

- With `url` (and `ref`, if the package sets one), the commit is read from
  ESPHome's own checkout of that package: the code the build really used, not
  a second copy fetched separately. Nothing is fetched again. If no package has
  that url and ref, validation fails and lists the packages that were fetched.
- Without `url`, the commit is read from the Git checkout that holds the
  device's YAML file, with `+local` added when tracked files have uncommitted
  changes. Use this when building from a local clone.
- If the commit cannot be read, the sensor reports `unknown` and no update is
  offered until it can.

`GIT_REVISION` is a C string, so the sensor above works unchanged if you used
an older copy of this component kept in your own repository: replace that
copy's `external_components` entry and add `url` and `ref`.

## Security

- **Token.** Only sent over `https`: an `http` address with a token is
  refused before anything is sent (a public repository needs no token and
  works over `http`). Stored in the config entry like other Home Assistant
  credentials, sent only as a request header to the server it was entered
  for, never in an address, log line or error message, and never shown in
  the form again. Reconfigure keeps a saved token only while the server
  (scheme, host and port) stays the same; moving to another server drops it.
  Redirects are not followed, so it cannot be passed on. Use a read-only
  token.
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

- Any new commit counts, including ones that do not change the firmware,
  unless its files are listed under the files that need no new build.
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
