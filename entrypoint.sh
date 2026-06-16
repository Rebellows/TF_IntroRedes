#!/bin/sh
cat > config.ini << CONF
[machine]
nickname = ${NICKNAME}

[timing]
token_delay        = ${TOKEN_DELAY}
data_delay         = ${DATA_DELAY}
token_timeout      = ${TOKEN_TIMEOUT}
min_token_interval = ${MIN_TOKEN_INTERVAL}

[faults]
error_probability = ${ERROR_PROBABILITY}
CONF

exec python3 -u main.py --log DEBUG
