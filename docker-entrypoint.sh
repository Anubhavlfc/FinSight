#!/bin/sh
# Start as root only long enough to make the data folder writable (a freshly mounted disk
# belongs to root), then run the server as the unprivileged "finsight" user.
set -e
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${FINSIGHT_HOME:-/data}"
    chown -R finsight:finsight "${FINSIGHT_HOME:-/data}"
    exec setpriv --reuid=finsight --regid=finsight --init-groups "$@"
fi
exec "$@"
