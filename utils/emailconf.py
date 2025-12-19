import dataclasses
import json
import pysondb

def getDB():
    return pysondb.getDb("conf/email_accounts.json")
emailDB = getDB()

def doEmailDBMigration():
    global emailDB
    def doMigrationAddField():
        with open('conf/email_accounts.json', 'r') as f:
            rawDB = json.load(f)
        if not ('data' in rawDB and len(rawDB['data']) > 0):
            return
        # add fields
        changed = False
        if 'disabled' not in rawDB['data'][0]:
            rawDB['data'][0]['disabled'] = False
            changed = True
        
        if not changed:
            return
        data = json.dumps(rawDB, indent=4)
        with open('conf/email_accounts.json', 'w') as f:
            f.write(data)
    
    def doMigrationData():
        for confDict in emailDB.getAll():
            curID = confDict.pop('id')

            # migrate001: int chat_id into string
            if 'chat_id' in confDict:
                if isinstance(confDict['chat_id'], int):
                    confDict['chat_id'] = str(confDict['chat_id'])
            
            # migrate002: initialize disabled field
            if 'disabled' not in confDict:
                confDict['disabled'] = False

            emailDB.updateById(curID, confDict)

    doMigrationAddField()
    emailDB = getDB()  # reinitialize the DB after migration
    doMigrationData()


doEmailDBMigration()

@dataclasses.dataclass
class EmailConf():
    email_addr: str
    email_passwd: str
    server_uri: str
    smtp_server_uri: str | None
    chat_id: str
    inbox_num: int
    disabled: bool | None = False
    
    def as_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, emailConfDict):
        if 'id' in emailConfDict:
            emailConfDict.pop('id')
        emailConf = EmailConf(**emailConfDict)
        return emailConf

def getEmailConf(email_addr):
    emailConfs = emailDB.getByQuery({'email_addr': email_addr})
    if not emailConfs:
        raise Exception(f'cannot find config for email {email_addr}')
    assert len(emailConfs) == 1
    
    emailConfDict = emailConfs[0]
    emailConf = EmailConf.from_dict(emailConfDict)
    return emailConf
