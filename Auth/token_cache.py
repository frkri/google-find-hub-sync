#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import json
import os

SECRETS_ENV_VAR = 'SECRETS_JSON'

def get_cached_value_or_set(name: str, generator: callable):

    existing_value = get_cached_value(name)

    if existing_value is not None:
        return existing_value

    value = generator()
    set_cached_value(name, value)
    return value


def get_cached_value(name: str):
    secrets_data = _get_secrets_data()
    
    if secrets_data:
        return secrets_data.get(name)
    
    return None


def set_cached_value(name: str, value: str):
    secrets_data = _get_secrets_data()
    
    if secrets_data is None:
        secrets_data = {}
    
    secrets_data[name] = value
    os.environ[SECRETS_ENV_VAR] = json.dumps(secrets_data)


def _get_secrets_data():
    secrets_json = os.environ.get(SECRETS_ENV_VAR)
    
    if not secrets_json:
        return None
    
    try:
        return json.loads(secrets_json)
    except json.JSONDecodeError:
        return None
