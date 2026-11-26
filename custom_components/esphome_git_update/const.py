"""Constants for ESPHome Git Update."""
from datetime import timedelta

DOMAIN = "esphome_git_update"

CONF_REPO_URL = "repo_url"
CONF_BRANCH = "branch"
CONF_TOKEN = "token"
CONF_REVISION_ENTITY = "revision_entity"
CONF_CONFIGURATION = "configuration"
# The Device Builder device's node name, which ties the entry to its ESPHome device.
CONF_NODE_NAME = "node_name"
# Path patterns of files whose changes need no new build (e.g. *.md, docs/).
CONF_IGNORED_FILES = "ignored_files"
MAX_IGNORED_PATTERNS = 50
MAX_PATTERN_CHARS = 200

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
# A build with uncommitted changes reports its commit with this suffix.
LOCAL_SUFFIX = "+local"
