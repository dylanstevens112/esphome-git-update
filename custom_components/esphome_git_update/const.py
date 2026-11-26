"""Constants for ESPHome Git Update."""
from datetime import timedelta

DOMAIN = "esphome_git_update"

CONF_REPO_URL = "repo_url"
CONF_BRANCH = "branch"
CONF_TOKEN = "token"
CONF_REVISION_ENTITY = "revision_entity"
CONF_CONFIGURATION = "configuration"

DEFAULT_BRANCH = "main"

POLL_INTERVAL = timedelta(minutes=5)
UPLOAD_ATTEMPTS = 2
UPLOAD_RETRY_DELAY_S = 20
# Generous limits so a stuck build or upload cannot leave the update
# "in progress" for ever.
COMPILE_TIMEOUT_S = 60 * 60
UPLOAD_TIMEOUT_S = 15 * 60

# Words that mark a device's "which commit am I running" sensor, used only to
# suggest it in the setup form.
REVISION_SENSOR_WORDS = ("revision", "commit", "git")
