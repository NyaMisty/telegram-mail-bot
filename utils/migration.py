import json
import os

EMAIL_CONF = "conf/email_accounts.json"


def load_conf():
    with open(EMAIL_CONF, 'r') as f:
        return json.load(f)

def save_conf(conf):
    with open(EMAIL_CONF, 'w') as f:
        json.dump(conf, f, indent=4)

def chat_id_to_string(account):
    if isinstance(account['chat_id'], int):
        print(f"Converting chat_id {account['chat_id']} to string")
        account['chat_id'] = str(account['chat_id'])

def conf_migrate():
    if not os.path.exists(EMAIL_CONF):
        return

    conf = load_conf()
    for account in conf['data']:
        chat_id_to_string(account)
        ... # other migrations

    save_conf(conf)