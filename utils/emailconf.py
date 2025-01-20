import dataclasses
import pysondb

emailDB = pysondb.getDb("conf/email_accounts.json")
def doEmailDBMigration():
    for confDict in emailDB.getAll():
        curID = confDict.pop('id')

        # migrate001: int chat_id into string
        if 'chat_id' in confDict:
            if isinstance(confDict['chat_id'], int):
                confDict['chat_id'] = str(confDict['chat_id'])
        emailDB.updateById(curID, confDict)

doEmailDBMigration()

@dataclasses.dataclass
class EmailConf():
    email_addr: str
    email_passwd: str
    server_uri: str
    smtp_server_uri: str | None
    chat_id: str
    inbox_num: int
    
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
