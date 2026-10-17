#!/bin/bash
# Run python with the Ditto live environment: /workspace/ditto_live/run_py.sh script.py [args]
. /workspace/ditto_live/env.sh
exec /workspace/venv_ditto/bin/python "$@"
