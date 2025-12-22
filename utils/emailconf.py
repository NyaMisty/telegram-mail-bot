import dataclasses
import json
from typing import Callable
import pysondb

def getDB():
    return pysondb.getDb("conf/email_accounts.json")
emailDB = getDB()

def doEmailDBMigration():
    global emailDB
    def doMigration(handler: Callable[[dict], bool]):
        with open('conf/email_accounts.json', 'r') as f:
            rawDB = json.load(f)
        if not ('data' in rawDB and len(rawDB['data']) > 0):
            return
        # add fields
        changed = False
        if handler(rawDB):
            changed = True
        
        if not changed:
            return
        data = json.dumps(rawDB, indent=4)
        with open('conf/email_accounts.json', 'w') as f:
            f.write(data)
    
    def migrateHandler_chatId(rawDB: dict[str]):
        changed = False
        for confDict in rawDB['data']:
            # migrate001: int chat_id into string
            if 'chat_id' in confDict:
                if isinstance(confDict['chat_id'], int):
                    confDict['chat_id'] = str(confDict['chat_id'])
                    changed = True
        return changed

    def migrateHandler_disabled(rawDB: dict[str]):
        changed = False
        for confDict in rawDB['data']:
            # migrate002: initialize disabled field
            if 'disabled' not in confDict:
                confDict['disabled'] = False
                changed = True
        return changed

    def migrateHandler_mailbox(rawDB: dict[str]):
        changed = False
        for confDict in rawDB['data']:
            # migrate003: convert inbox_num to mailbox_offsets
            if 'inbox_num' in confDict:
                if 'mailbox_offsets' not in confDict:
                    confDict['mailbox_offsets'] = {'inbox': confDict['inbox_num']}
                confDict.pop('inbox_num')
                changed = True
        return changed

    doMigration(migrateHandler_chatId)
    doMigration(migrateHandler_disabled)
    doMigration(migrateHandler_mailbox)
    emailDB = getDB()  # reinitialize the DB after migration


doEmailDBMigration()

@dataclasses.dataclass
class EmailConfBase():
    email_addr: str
    email_passwd: str
    server_uri: str
    smtp_server_uri: str | None

    def as_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, emailConfDict, ignore_extra_fields=False):
        if 'id' in emailConfDict:
            emailConfDict.pop('id')
        if ignore_extra_fields:
            # print(dataclasses.fields(cls))
            clsFields = [f.name for f in dataclasses.fields(cls)]
            emailConfDict = {k: v for k, v in emailConfDict.items() if k in clsFields}
        emailConf = cls(**emailConfDict)
        return emailConf


@dataclasses.dataclass
class EmailConf(EmailConfBase):
    chat_id: str
    mailbox_offsets: dict[str, int] = dataclasses.field(default_factory=dict)
    disabled: bool | None = False

def getEmailConf(email_addr):
    emailConfs = emailDB.getByQuery({'email_addr': email_addr})
    if not emailConfs:
        raise Exception(f'cannot find config for email {email_addr}')
    assert len(emailConfs) == 1
    
    emailConfDict = emailConfs[0]
    emailConf = EmailConf.from_dict(emailConfDict)
    return emailConf
